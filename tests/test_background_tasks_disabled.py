"""NOVA_DISABLE_BACKGROUND_TASKS (set in conftest.py) must keep telemetry off the network."""
import threading

import nova
import nova.config


def test_flag_is_active_under_pytest():
    assert nova.config.BACKGROUND_TASKS_DISABLED is True


def test_send_telemetry_async_makes_no_http_call(monkeypatch):
    monkeypatch.setenv('TELEMETRY_ENABLED', 'true')
    monkeypatch.setenv('NOVA_TELEMETRY_ENDPOINT', 'http://telemetry.invalid/ping')
    posted = threading.Event()
    monkeypatch.setattr(nova.requests, 'post', lambda *a, **k: posted.set())

    nova.send_telemetry_async({'telemetry': {'enabled': True}}, force=True)

    # The POST runs in a daemon thread, so give it a moment to fire.
    assert not posted.wait(timeout=1.0), "send_telemetry_async made an HTTP call"
