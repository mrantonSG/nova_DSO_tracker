"""Timeouts on the catalog manifest fetch and the Ollama AI provider."""

import pytest
import requests
from urllib3.exceptions import ReadTimeoutError

import nova.helpers as helpers
from nova import app
from nova.ai import service
from nova.ai.service import AIServiceError


class _FakeResponse:
    def __init__(self, json_data=None, lines=None, line_error=None):
        self._json = json_data
        self._lines = lines or []
        self._line_error = line_error

    def raise_for_status(self):
        pass

    def json(self):
        return self._json

    def iter_lines(self):
        for line in self._lines:
            yield line
        if self._line_error is not None:
            raise self._line_error


# --- Catalog manifest -------------------------------------------------------

def test_catalog_manifest_fetch_uses_2s_timeout(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(json_data=[{"id": "p1", "filename": "p1.yaml"}])

    monkeypatch.setattr(helpers, "CATALOG_MANIFEST_CACHE", {"data": None, "expires": 0})
    monkeypatch.setattr(helpers.requests, "get", fake_get)

    packs = helpers.discover_catalog_packs()

    assert packs == [{"id": "p1", "filename": "p1.yaml"}]
    assert len(calls) == 1
    assert calls[0][0].endswith("/manifest.json")
    assert calls[0][1]["timeout"] == 2


# --- Ollama -----------------------------------------------------------------

@pytest.fixture
def ollama_config(monkeypatch):
    monkeypatch.setitem(app.config, "AI_PROVIDER", "ollama")
    monkeypatch.setitem(app.config, "AI_BASE_URL", "http://ollama.invalid:11434")
    monkeypatch.setitem(app.config, "AI_MODEL", "test-model")
    monkeypatch.setitem(app.config, "AI_API_KEY", None)


def _capture_post(monkeypatch, response=None, error=None):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(service.requests, "post", fake_post)
    return calls


def test_ollama_blocking_call_uses_at_least_300s(ollama_config, monkeypatch):
    calls = _capture_post(monkeypatch, response=_FakeResponse(json_data={"response": "ok"}))
    with app.app_context():
        assert service.get_ai_response("hi") == "ok"            # default 120
        assert service.get_ai_response("hi", timeout=60) == "ok"
        assert service.get_ai_response("hi", timeout=600) == "ok"
    assert [c["timeout"] for c in calls] == [300, 300, 600]


def test_ollama_stream_uses_caller_timeout(ollama_config, monkeypatch):
    calls = _capture_post(monkeypatch, response=_FakeResponse(lines=[b'{"response": "a"}', b'{"response": "b"}']))
    with app.app_context():
        assert "".join(service.get_ai_response("hi", stream=True)) == "ab"
        assert "".join(service.get_ai_response("hi", stream=True, timeout=45)) == "ab"
    assert [c["timeout"] for c in calls] == [120, 45]
    assert all(c["stream"] is True for c in calls)


def test_ollama_blocking_timeout_message(ollama_config, monkeypatch):
    _capture_post(monkeypatch, error=requests.exceptions.ReadTimeout("Read timed out. (read timeout=300)"))
    with app.app_context(), pytest.raises(AIServiceError) as exc:
        service.get_ai_response("hi")
    assert str(exc.value) == "Request timed out."


def test_ollama_stream_connect_timeout_message(ollama_config, monkeypatch):
    _capture_post(monkeypatch, error=requests.exceptions.ConnectTimeout("connect timed out"))
    with app.app_context(), pytest.raises(AIServiceError) as exc:
        list(service.get_ai_response("hi", stream=True))
    assert str(exc.value) == "Request timed out."


def test_ollama_stream_midstream_timeout_message(ollama_config, monkeypatch):
    # requests re-raises a read timeout inside iter_content as ConnectionError(ReadTimeoutError).
    mid = requests.exceptions.ConnectionError(ReadTimeoutError(None, None, "Read timed out."))
    _capture_post(monkeypatch, response=_FakeResponse(lines=[b'{"response": "a"}'], line_error=mid))
    chunks = []
    with app.app_context(), pytest.raises(AIServiceError) as exc:
        for chunk in service.get_ai_response("hi", stream=True):
            chunks.append(chunk)
    assert chunks == ["a"]
    assert str(exc.value) == "Request timed out."


def test_ollama_other_errors_keep_their_message(ollama_config, monkeypatch):
    _capture_post(monkeypatch, error=requests.exceptions.ConnectionError("Connection refused"))
    with app.app_context(), pytest.raises(AIServiceError) as exc:
        service.get_ai_response("hi")
    assert str(exc.value) == "Connection refused"
