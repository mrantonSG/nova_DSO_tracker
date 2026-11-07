"""NOVA_DISABLE_BACKGROUND_TASKS (set in conftest.py) must keep telemetry off the network."""
import threading

import nova
import nova.config
from nova.models import Location


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


def test_config_form_new_location_makes_no_skyglow_call(multi_user_client, db_session, monkeypatch):
    """Saving a location must not reach skyglow.nova-tracker.com under pytest.

    A real POST here would hit the production skyglow service with the shipped
    shared key, so the suite has to stay off it. Both the thread and the HTTP
    call are stubbed: the guard returns before either can be reached, and each
    stub stays visible if that changes.
    """
    client, ids = multi_user_client
    user_a_id = ids["user_a_id"]

    threads_started = []
    posts_made = []

    class _RecordingThread:
        def __init__(self, *args, **kwargs):
            threads_started.append(kwargs.get('target'))

        def start(self):
            pass

    def _recording_post(*args, **kwargs):
        posts_made.append(args[0] if args else kwargs.get('url'))
        raise AssertionError("skyglow POST reached the network")

    monkeypatch.setattr(nova.blueprints.core.threading, 'Thread', _RecordingThread)
    monkeypatch.setattr(nova.blueprints.core.requests, 'post', _recording_post)

    resp = client.post('/config_form', data={
        'submit_new_location': '1', 'new_location': 'SkyglowProbe',
        'new_lat': '48.2', 'new_lon': '16.4',
        'new_timezone': 'Europe/Vienna', 'new_active': 'on',
    })

    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/config_form')

    # The location really was written, so the branch that fires skyglow ran to
    # completion rather than bailing out earlier and skipping the call anyway.
    db_session.expire_all()
    assert db_session.query(Location).filter_by(
        user_id=user_a_id, name='SkyglowProbe').one_or_none() is not None

    assert posts_made == [], f"skyglow HTTP call made: {posts_made}"
    assert threads_started == [], f"background threads started: {threads_started}"
