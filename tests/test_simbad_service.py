import threading
import time
from unittest.mock import MagicMock

import pytest
import requests

import modules.simbad_service as simbad_service


@pytest.fixture(autouse=True)
def _clear_health():
    simbad_service._health.clear()
    yield
    simbad_service._health.clear()


def _set(host, is_up, age=0):
    simbad_service._health[host] = (is_up, time.monotonic() - age)


def test_ordered_hosts_prefers_up_then_unknown_then_down():
    _set("simbad.cds.unistra.fr", False, age=0)
    _set("simbad.harvard.edu", True, age=0)
    _set("simbad.idia.ac.za", True, age=61)
    order = simbad_service._ordered_hosts()
    assert order == [
        "simbad.harvard.edu",
        "simbad.idia.ac.za",
        "simbad.cds.unistra.fr",
    ]


def test_ttl_up_is_sixty_seconds():
    _set("simbad.harvard.edu", True, age=59)
    assert simbad_service._is_fresh("simbad.harvard.edu", time.monotonic())
    _set("simbad.harvard.edu", True, age=61)
    assert not simbad_service._is_fresh("simbad.harvard.edu", time.monotonic())


def test_ttl_down_is_nine_hundred_seconds():
    _set("simbad.harvard.edu", False, age=899)
    assert simbad_service._is_fresh("simbad.harvard.edu", time.monotonic())
    _set("simbad.harvard.edu", False, age=901)
    assert not simbad_service._is_fresh("simbad.harvard.edu", time.monotonic())


def test_probe_is_serialized_by_host_lock(monkeypatch):
    calls = []

    def slow_probe(host):
        calls.append(host)
        time.sleep(0.05)
        return True

    monkeypatch.setattr(simbad_service, "_probe", slow_probe)
    results = []

    def worker():
        results.append(simbad_service._ensure_host_usable("simbad.harvard.edu"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls == ["simbad.harvard.edu"]
    assert results == [True, True]


def test_mark_down_and_mark_up_update_cache():
    simbad_service.mark_down("simbad.harvard.edu")
    is_up, _ = simbad_service._health["simbad.harvard.edu"]
    assert is_up is False
    assert simbad_service._is_fresh("simbad.harvard.edu", time.monotonic())
    simbad_service.mark_up("simbad.harvard.edu")
    is_up, _ = simbad_service._health["simbad.harvard.edu"]
    assert is_up is True


def test_query_object_fails_over_to_next_host(monkeypatch):
    monkeypatch.setattr(simbad_service, "_ensure_host_usable", lambda host: True)
    table = MagicMock(name="table")

    def make_instance(host, row_limit=1):
        inst = MagicMock()
        if host == "simbad.cds.unistra.fr":
            inst.query_object.side_effect = requests.exceptions.ConnectionError("down")
        else:
            inst.query_object.return_value = table
        return inst

    monkeypatch.setattr(simbad_service, "make_simbad_instance", make_instance)
    assert simbad_service.query_object("M 31") is table
    assert simbad_service._health["simbad.cds.unistra.fr"][0] is False
    assert simbad_service._health["simbad.harvard.edu"][0] is True


class _FailingSimbad:
    def add_votable_fields(self, *fields):
        pass

    def query_object(self, name):
        raise requests.exceptions.ConnectionError("down")


def test_query_object_raises_when_all_hosts_fail(monkeypatch):
    monkeypatch.setattr(simbad_service, "_ensure_host_usable", lambda host: True)
    monkeypatch.setattr(
        simbad_service, "make_simbad_instance",
        lambda host, row_limit=1: _FailingSimbad())
    with pytest.raises(simbad_service.SimbadUnavailableError):
        simbad_service.query_object("M 31")


def test_timeout_session_injects_default_timeout(monkeypatch):
    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    session = simbad_service._TimeoutSession((3, 8))
    session.get("https://example.test")
    assert captured["timeout"] == (3, 8)


def test_timeout_session_keeps_explicit_timeout(monkeypatch):
    captured = {}

    def fake_request(self, method, url, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    session = simbad_service._TimeoutSession((3, 8))
    session.get("https://example.test", timeout=1)
    assert captured["timeout"] == 1


# --- current_host()/is_available_cached() must keep stale "up" knowledge ---

class _FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


def test_current_host_keeps_mirror_after_up_ttl(monkeypatch):
    """A mirror that worked keeps winning after its 60 s up-TTL expires."""
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)

    # One search with Strasbourg down: the probe marks Strasbourg down and a
    # mirror up. This is what a real query leaves in the cache.
    simbad_service.mark_down("simbad.cds.unistra.fr")
    simbad_service.mark_up("simbad.harvard.edu")
    assert simbad_service.current_host() == "simbad.harvard.edu"
    assert simbad_service.is_available_cached() is True

    # 61 s later the up entry is stale but the down entry is still fresh.
    clock.now += 61.0
    assert simbad_service.current_host() == "simbad.harvard.edu"
    assert simbad_service.is_available_cached() is True


def test_current_host_defaults_when_no_knowledge(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)

    assert simbad_service.current_host() == "simbad.cds.unistra.fr"
    assert simbad_service.is_available_cached() is True


def test_current_host_prefers_host_marked_up_again(monkeypatch):
    """Once Strasbourg is marked up again it wins over the stale mirror."""
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)

    simbad_service.mark_down("simbad.cds.unistra.fr")
    simbad_service.mark_up("simbad.harvard.edu")
    clock.now += 61.0
    assert simbad_service.current_host() == "simbad.harvard.edu"

    clock.now += 1.0
    simbad_service.mark_up("simbad.cds.unistra.fr")
    assert simbad_service.current_host() == "simbad.cds.unistra.fr"
    assert simbad_service.is_available_cached() is True


def test_is_available_cached_false_when_every_known_host_is_down(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)

    simbad_service.mark_down("simbad.cds.unistra.fr")
    simbad_service.mark_down("simbad.harvard.edu")
    clock.now += 61.0
    assert simbad_service.is_available_cached() is False
