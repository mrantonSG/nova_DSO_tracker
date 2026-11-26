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


def test_ordered_hosts_keeps_priority_order_and_puts_fresh_down_last():
    _set("simbad.cds.unistra.fr", False, age=0)
    _set("simbad.cfa.harvard.edu", True, age=0)
    _set("simbad.idia.ac.za", True, age=61)
    order = simbad_service._ordered_hosts()
    assert order == [
        "simbad.cfa.harvard.edu",
        "simbad.idia.ac.za",
        "simbad.cds.unistra.fr",
    ]


def test_ordered_hosts_retries_strasbourg_before_a_working_mirror_once_down_ttl_expires():
    """A mirror kept fresh by its own successes must not hide Strasbourg's recovery."""
    _set("simbad.cds.unistra.fr", False, age=901)   # down verdict expired
    _set("simbad.cfa.harvard.edu", True, age=0)     # fresh up
    assert simbad_service._ordered_hosts() == simbad_service.HOSTS


def test_tap_csv_switches_back_to_strasbourg_when_it_recovers(monkeypatch):
    _set("simbad.cds.unistra.fr", False, age=901)
    _set("simbad.cfa.harvard.edu", True, age=0)
    monkeypatch.setattr(simbad_service, "_probe", lambda host: True)
    posted = []

    def fake_post(url, data=None, timeout=None):
        posted.append(url)
        r = MagicMock()
        r.ok, r.status_code = True, 200
        r.headers = {"content-type": "text/csv"}
        r.text = "main_id,ra\n\"M  42\",83.8\n"
        return r

    monkeypatch.setattr(simbad_service.requests, "post", fake_post)
    simbad_service.tap_csv("SELECT 1")
    assert posted == ["https://simbad.cds.unistra.fr/simbad/sim-tap/sync"]
    assert simbad_service.current_host() == "simbad.cds.unistra.fr"


def test_ttl_up_is_sixty_seconds():
    _set("simbad.cfa.harvard.edu", True, age=59)
    assert simbad_service._is_fresh("simbad.cfa.harvard.edu", time.monotonic())
    _set("simbad.cfa.harvard.edu", True, age=61)
    assert not simbad_service._is_fresh("simbad.cfa.harvard.edu", time.monotonic())


def test_ttl_down_is_nine_hundred_seconds():
    _set("simbad.cfa.harvard.edu", False, age=899)
    assert simbad_service._is_fresh("simbad.cfa.harvard.edu", time.monotonic())
    _set("simbad.cfa.harvard.edu", False, age=901)
    assert not simbad_service._is_fresh("simbad.cfa.harvard.edu", time.monotonic())


def test_probe_is_serialized_by_host_lock(monkeypatch):
    calls = []

    def slow_probe(host):
        calls.append(host)
        time.sleep(0.05)
        return True

    monkeypatch.setattr(simbad_service, "_probe", slow_probe)
    results = []

    def worker():
        results.append(simbad_service._ensure_host_usable("simbad.cfa.harvard.edu"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls == ["simbad.cfa.harvard.edu"]
    assert results == [True, True]


def test_mark_down_and_mark_up_update_cache():
    simbad_service.mark_down("simbad.cfa.harvard.edu")
    is_up, _ = simbad_service._health["simbad.cfa.harvard.edu"]
    assert is_up is False
    assert simbad_service._is_fresh("simbad.cfa.harvard.edu", time.monotonic())
    simbad_service.mark_up("simbad.cfa.harvard.edu")
    is_up, _ = simbad_service._health["simbad.cfa.harvard.edu"]
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
    assert simbad_service._health["simbad.cfa.harvard.edu"][0] is True


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
    simbad_service.mark_up("simbad.cfa.harvard.edu")
    assert simbad_service.current_host() == "simbad.cfa.harvard.edu"
    assert simbad_service.is_available_cached() is True

    # 61 s later the up entry is stale but the down entry is still fresh.
    clock.now += 61.0
    assert simbad_service.current_host() == "simbad.cfa.harvard.edu"
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
    simbad_service.mark_up("simbad.cfa.harvard.edu")
    clock.now += 61.0
    assert simbad_service.current_host() == "simbad.cfa.harvard.edu"

    clock.now += 1.0
    simbad_service.mark_up("simbad.cds.unistra.fr")
    assert simbad_service.current_host() == "simbad.cds.unistra.fr"
    assert simbad_service.is_available_cached() is True


def test_is_available_cached_false_when_every_known_host_is_down(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)

    simbad_service.mark_down("simbad.cds.unistra.fr")
    simbad_service.mark_down("simbad.cfa.harvard.edu")
    clock.now += 61.0
    assert simbad_service.is_available_cached() is False


# --- tap_csv(budget=...): a firm limit on the whole call, failover included ---

class _FakeTapResponse:
    ok = True
    status_code = 200
    headers = {"content-type": "text/csv"}
    text = "main_id,ra\nM 1,83.6\n"


def test_tap_csv_cuts_the_request_timeout_to_the_budget(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)
    simbad_service.mark_up("simbad.cds.unistra.fr")
    seen = []
    monkeypatch.setattr(simbad_service.requests, "post",
                        lambda url, data, timeout: seen.append(timeout) or _FakeTapResponse())

    assert simbad_service.tap_csv("SELECT 1", timeout=10, budget=4.0).startswith("main_id")
    assert seen == [4.0]


def test_tap_csv_does_not_start_a_probe_that_cannot_finish(monkeypatch):
    """Stale hosts are skipped when probe + one attempt do not fit; a fresh one is used."""
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)
    simbad_service.mark_up("simbad.idia.ac.za")       # fresh; the other two are unknown
    probed, posted = [], []
    monkeypatch.setattr(simbad_service, "_probe", lambda host: probed.append(host) or True)
    monkeypatch.setattr(simbad_service.requests, "post",
                        lambda url, data, timeout: posted.append(url) or _FakeTapResponse())

    simbad_service.tap_csv("SELECT 1", budget=simbad_service.PROBE_MAX)
    assert probed == []
    assert len(posted) == 1 and "simbad.idia.ac.za" in posted[0]


def test_tap_csv_stops_failover_when_the_budget_is_spent(monkeypatch):
    """A host that hangs for the whole budget is not followed by another attempt."""
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)
    for host in simbad_service.HOSTS:
        simbad_service.mark_up(host)
    posted = []

    def hanging_post(url, data, timeout):
        posted.append((url, timeout))
        clock.now += timeout
        raise requests.exceptions.ReadTimeout("hung")

    monkeypatch.setattr(simbad_service.requests, "post", hanging_post)
    with pytest.raises(simbad_service.SimbadUnavailableError):
        simbad_service.tap_csv("SELECT 1", timeout=10, budget=13.0)
    # 10 s on the first host, then 3 s left: the second gets 3 s, then nothing is left.
    assert [t for _, t in posted] == [10, 3.0]
    assert clock.now - 1000.0 <= 13.0


def test_tap_csv_reports_an_exhausted_budget_without_trying_any_host(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(simbad_service, "time", clock)
    monkeypatch.setattr(simbad_service, "_probe",
                        lambda host: pytest.fail("no probe expected"))
    with pytest.raises(simbad_service.SimbadUnavailableError, match="budget exhausted"):
        simbad_service.tap_csv("SELECT 1", budget=0.5)
