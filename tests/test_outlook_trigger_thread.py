"""No Outlook update thread outlives a test: conftest's no_outlook_thread stands in for
nova's threading module so trigger_outlook_update_for_user never starts its thread, while
tests that patch threading themselves still see their own Thread."""

import inspect
import threading
from types import SimpleNamespace

from flask import g

import nova
from conftest import OUTLOOK_THREAD_TARGET


def _fire(monkeypatch):
    # Everything the trigger needs to reach its thread
    monkeypatch.setattr(nova, "build_user_config_from_db",
                        lambda username: {"locations": {"Home": {"active": True}}})
    with nova.app.test_request_context("/"):
        g.db_user = SimpleNamespace(id=1, username="default")
        nova.trigger_outlook_update_for_user("default")


def test_the_stand_in_matches_the_trigger():
    """The stand-in recognises the thread by its target's name: it must still be the one."""
    assert f"def {OUTLOOK_THREAD_TARGET}(" in inspect.getsource(nova.trigger_outlook_update_for_user)


def test_the_trigger_starts_no_thread_in_tests(monkeypatch):
    before = set(threading.enumerate())
    started = []
    real_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self) or real_start(self))
    _fire(monkeypatch)
    assert started == [] and set(threading.enumerate()) <= before


def test_a_test_that_patches_thread_still_gets_it(monkeypatch):
    calls = []
    monkeypatch.setattr("threading.Thread",
                        lambda *a, **k: calls.append(k) or SimpleNamespace(start=lambda: None))
    _fire(monkeypatch)
    assert len(calls) == 1 and calls[0]["target"].__name__ == OUTLOOK_THREAD_TARGET
    # and anything else in nova still has the real module
    assert nova.threading.Lock is threading.Lock
