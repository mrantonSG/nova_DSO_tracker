"""Tests for the telemetry endpoint swap, the single-worker election,
the channel/ui_language payload fields, and the skyglow channel header."""
import os
import types
from datetime import date, datetime

import pytest

import nova
import nova.blueprints.core as core_module
from nova.helpers import release_file_lock, try_acquire_file_lock
from nova.models import DbUser, UiPref

LEGACY = nova.LEGACY_TELEMETRY_ENDPOINT
DEFAULT = nova.DEFAULT_TELEMETRY_ENDPOINT
CUSTOM = "https://example.test/custom-telemetry"


class _SyncThread:
    """Runs the target inline so send_telemetry_async's worker is deterministic
    (and never fires requests.post after the test's patches are undone)."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=False):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


@pytest.fixture
def telemetry_send(monkeypatch):
    """Make send_telemetry_async run end-to-end without network or threads."""
    posted = []
    monkeypatch.setattr(nova, "threading", types.SimpleNamespace(Thread=_SyncThread))
    monkeypatch.setattr(nova, "BACKGROUND_TASKS_DISABLED", False)
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    monkeypatch.setattr(nova, "build_telemetry_payload", lambda *a, **k: {})
    monkeypatch.setattr(
        nova.requests, "post",
        lambda url, **k: (posted.append(url) or types.SimpleNamespace(status_code=200)),
    )
    return posted


# --- 1. Endpoint swap ------------------------------------------------------

def test_legacy_endpoint_swapped_for_new(telemetry_send, monkeypatch):
    monkeypatch.setenv("NOVA_TELEMETRY_ENDPOINT", LEGACY)
    nova.send_telemetry_async({}, force=True)
    assert nova.TELEMETRY_DEBUG_STATE["endpoint"] == DEFAULT
    assert telemetry_send == [DEFAULT]


def test_custom_endpoint_left_untouched(telemetry_send, monkeypatch):
    monkeypatch.setenv("NOVA_TELEMETRY_ENDPOINT", CUSTOM)
    nova.send_telemetry_async({}, force=True)
    assert nova.TELEMETRY_DEBUG_STATE["endpoint"] == CUSTOM
    assert telemetry_send == [CUSTOM]


def test_swap_never_writes_the_env_file(telemetry_send, monkeypatch):
    env_path = os.path.join(nova.INSTANCE_PATH, ".env")
    with open(env_path, encoding="utf-8") as f:
        before = f.read()
    monkeypatch.setenv("NOVA_TELEMETRY_ENDPOINT", LEGACY)
    nova.send_telemetry_async({}, force=True)
    with open(env_path, encoding="utf-8") as f:
        after = f.read()
    assert before == after


# --- 2. Cross-process election (dedicated file lock) -----------------------

@pytest.fixture
def telemetry_lock(tmp_path, monkeypatch):
    """Point the telemetry lock at a temp dir with clean module state."""
    monkeypatch.setattr(nova, "INSTANCE_PATH", str(tmp_path))
    monkeypatch.setattr(nova, "BACKGROUND_TASKS_DISABLED", False)
    monkeypatch.setattr(nova, "_telemetry_lock_fh", None)
    monkeypatch.setattr(nova, "_telemetry_lock_next_retry", 0.0)
    return tmp_path


def test_two_processes_exactly_one_owner(telemetry_lock):
    # "process A" holds the lock.
    a = try_acquire_file_lock(nova._telemetry_lock_path())
    assert a is not None
    try:
        assert nova._telemetry_process_owns_send() is False            # B loses
        assert try_acquire_file_lock(nova._telemetry_lock_path()) is None  # and can't force it
    finally:
        release_file_lock(a)
    # A releases; B takes over on its next attempt.
    nova._telemetry_lock_next_retry = 0.0
    assert nova._telemetry_process_owns_send() is True                 # B is the one owner
    assert try_acquire_file_lock(nova._telemetry_lock_path()) is None  # C loses


def test_owner_release_takeover_respects_retry_window(telemetry_lock, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(nova.time, "monotonic", lambda: clock["t"])

    a = try_acquire_file_lock(nova._telemetry_lock_path())
    assert a is not None
    assert nova._telemetry_process_owns_send() is False   # first attempt fails

    release_file_lock(a)                                  # owner dies
    clock["t"] += 60                                      # 1 minute later
    assert nova._telemetry_process_owns_send() is False   # inside window -> no attempt

    clock["t"] += nova._TELEMETRY_LOCK_RETRY_SECONDS      # window elapsed
    assert nova._telemetry_process_owns_send() is True    # takes over


def test_reloader_child_without_startup_block_becomes_owner(telemetry_lock, monkeypatch):
    # A reloader child skips the module-level startup block entirely, so
    # should_start_threads is never defined there -- ownership must not need it.
    monkeypatch.delattr(nova, "should_start_threads", raising=False)
    assert nova._telemetry_process_owns_send() is True


def test_non_owner_starts_no_scheduler(telemetry_lock, monkeypatch):
    calls = []
    monkeypatch.setattr(nova, "send_telemetry_async", lambda *a, **k: calls.append(1))
    nova._telemetry_startup_once.clear()

    held = try_acquire_file_lock(nova._telemetry_lock_path())
    assert held is not None
    try:
        nova._start_telemetry_scheduler_once()   # another process owns -> nothing
        assert calls == []
    finally:
        release_file_lock(held)

    nova._telemetry_lock_next_retry = 0.0
    nova._start_telemetry_scheduler_once()       # now the owner -> sends once
    assert calls == [1]


def test_once_flag_only_claimed_by_the_owner(telemetry_lock, monkeypatch):
    """A non-owner must not burn the per-process once-flag, or a later takeover
    would find it set and never start its daily loop."""
    nova._telemetry_startup_once.clear()
    held = try_acquire_file_lock(nova._telemetry_lock_path())
    assert held is not None
    try:
        nova._start_telemetry_scheduler_once()   # non-owner -> early return
        nova.telemetry_startup_ping_once()       # legacy hook, same request
        assert nova._telemetry_startup_once.is_set() is False
    finally:
        release_file_lock(held)

    # Takeover: the flag is still free, so the new owner starts its daily loop.
    nova._telemetry_lock_next_retry = 0.0
    calls = []
    monkeypatch.setattr(nova, "send_telemetry_async", lambda *a, **k: calls.append(1))
    nova._start_telemetry_scheduler_once()
    assert nova._telemetry_startup_once.is_set() is True
    assert calls == [1]


def test_disabled_background_tasks_takes_no_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(nova, "INSTANCE_PATH", str(tmp_path))
    monkeypatch.setattr(nova, "BACKGROUND_TASKS_DISABLED", True)
    monkeypatch.setattr(nova, "_telemetry_lock_fh", None)
    assert nova._telemetry_process_owns_send() is False
    assert not os.path.exists(os.path.join(str(tmp_path), "telemetry.lock"))


# --- 3. channel / ui_language ---------------------------------------------

def test_channel_default_and_env_override(monkeypatch):
    monkeypatch.delenv("NOVA_TELEMETRY_CHANNEL", raising=False)
    assert nova.telemetry_channel() == "prod"
    monkeypatch.setenv("NOVA_TELEMETRY_CHANNEL", "dev")
    assert nova.telemetry_channel() == "dev"
    monkeypatch.setenv("NOVA_TELEMETRY_CHANNEL", "   ")
    assert nova.telemetry_channel() == "prod"


def test_payload_carries_channel_and_ui_language(db_session, monkeypatch):
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", True)
    monkeypatch.delenv("NOVA_TELEMETRY_CHANNEL", raising=False)
    payload = nova.build_telemetry_payload({}, "")
    assert payload["channel"] == "prod"
    assert payload["ui_language"] is None


def test_ui_language_single_user_value(db_session, monkeypatch):
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", True)
    user = DbUser(username="default")
    db_session.add(user)
    db_session.flush()
    db_session.add(UiPref(user_id=user.id, json_blob='{"language": "de"}'))
    db_session.commit()
    assert nova._telemetry_ui_language() == "de"


def test_ui_language_none_in_multi_user(monkeypatch):
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", False)
    assert nova._telemetry_ui_language() is None


def test_ui_language_none_on_error(db_session, monkeypatch):
    monkeypatch.setattr(nova, "SINGLE_USER_MODE", True)
    user = DbUser(username="default")
    db_session.add(user)
    db_session.flush()
    db_session.add(UiPref(user_id=user.id, json_blob="not-json"))
    db_session.commit()
    assert nova._telemetry_ui_language() is None


# --- 4. Skyglow channel header --------------------------------------------

def test_skyglow_request_carries_channel_and_drops_record_event(tmp_path, monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"cache_hit": False}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Resp()

    events = []
    monkeypatch.setattr(core_module.requests, "post", fake_post)
    monkeypatch.setattr(core_module, "record_event", lambda name: events.append(name))
    monkeypatch.setenv("NOVA_TELEMETRY_CHANNEL", "dev")

    core_module._run_skyglow_task(nova.app, "loc-1", 10.0, 20.0, 100.0, 21.0, 4, str(tmp_path))

    assert captured["headers"]["X-Nova-Channel"] == "dev"
    assert captured["headers"]["X-API-Key"]
    assert events == []
