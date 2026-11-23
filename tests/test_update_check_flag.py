"""NOVA_DISABLE_UPDATE_CHECK=1 skips the GitHub release check and hides the notice."""
import pytest

import nova.workers.updates as updates
from nova.config import LATEST_VERSION_INFO


class _RecordingThread:
    started = []

    def __init__(self, *args, target=None, **kwargs):
        self.target = target
        self.daemon = False

    def start(self):
        _RecordingThread.started.append(self.target)


@pytest.fixture
def recorded(monkeypatch):
    _RecordingThread.started = []
    monkeypatch.setattr(updates.threading, "Thread", _RecordingThread)
    return _RecordingThread.started


@pytest.fixture
def pending_update():
    saved = dict(LATEST_VERSION_INFO)
    LATEST_VERSION_INFO.clear()
    LATEST_VERSION_INFO.update({"new_version": "99.0.0", "url": "https://example.invalid/r"})
    yield
    LATEST_VERSION_INFO.clear()
    LATEST_VERSION_INFO.update(saved)


@pytest.mark.parametrize("value", [None, "0", ""])
def test_check_starts_when_flag_unset(monkeypatch, recorded, value):
    if value is None:
        monkeypatch.delenv("NOVA_DISABLE_UPDATE_CHECK", raising=False)
    else:
        monkeypatch.setenv("NOVA_DISABLE_UPDATE_CHECK", value)
    assert updates.start_update_check(object()) is not None
    assert recorded == [updates.check_for_updates]


def test_check_skipped_when_flag_is_1(monkeypatch, recorded):
    monkeypatch.setenv("NOVA_DISABLE_UPDATE_CHECK", "1")
    assert updates.start_update_check(object()) is None
    assert recorded == []


def test_notice_data_served_when_flag_unset(client, monkeypatch, pending_update):
    monkeypatch.delenv("NOVA_DISABLE_UPDATE_CHECK", raising=False)
    resp = client.get("/api/latest_version")
    assert resp.status_code == 200
    assert resp.get_json() == {"new_version": "99.0.0", "url": "https://example.invalid/r"}


def test_no_notice_when_flag_is_1(client, monkeypatch, pending_update):
    monkeypatch.setenv("NOVA_DISABLE_UPDATE_CHECK", "1")
    resp = client.get("/api/latest_version")
    assert resp.status_code == 200
    assert resp.get_json() == {}
