"""Backfill of JournalSession.calculated_integration_time_minutes.

Covers the shared helper (stored-keys semantics), the standalone backfill
(dry-run / apply / idempotent, never touching sync_rev), and the startup hook
that self-heals every start without breaking startup on failure.
"""
import json
import os
from datetime import date

import pytest

from nova import _backfill_integration_on_startup, _run_backfill_once_per_process, app
from nova.config import INSTANCE_PATH
from nova.helpers import (
    _HAS_FCNTL, backfill_integration_minutes, compute_integration_minutes,
    release_file_lock, try_acquire_file_lock,
)
from nova.models import DbUser, JournalSession


def _user(db, name="bf_user"):
    db.add(DbUser(username=name))
    db.commit()
    return db.query(DbUser).filter_by(username=name).one()


def _add_session(db, user_id, external_id, **fields):
    sess = JournalSession(user_id=user_id, external_id=external_id,
                          date_utc=date(2026, 1, 2), **fields)
    db.add(sess)
    db.commit()
    return sess


# --- the shared helper (stored-keys semantics) -------------------------------

def test_helper_counts_stored_custom_keys():
    values = {
        "number_of_subs_light": 10,
        "exposure_time_per_sub_sec": 120,
        "filter_L_subs": 5,
        "filter_L_exposure_sec": 60,
        "custom_filter_data": json.dumps(
            {"filter_NB_subs": 3, "filter_NB_exposure_sec": 30}),
    }
    # 10*120 + 5*60 + 3*30 = 1590 s -> 26.5 min, with no filter definition
    # consulted at all (the helper only reads the session's own stored keys).
    assert compute_integration_minutes(values) == 26.5


def test_helper_returns_none_without_subs():
    assert compute_integration_minutes({}) is None
    assert compute_integration_minutes({"number_of_subs_light": None}) is None


# --- the standalone backfill ------------------------------------------------

def test_backfill_dry_run_then_apply_is_idempotent(db_session):
    user = _user(db_session)
    with_subs = _add_session(db_session, user.id, "bf-1",
                             number_of_subs_light=10,
                             exposure_time_per_sub_sec=120)
    _add_session(db_session, user.id, "bf-2")  # no subs data

    rev_before = with_subs.sync_rev
    stamp_before = with_subs.sync_changed_at

    dry = backfill_integration_minutes(db_session, dry_run=True)
    assert dry == {"scanned": 2, "fillable": 1, "no_subs": 1, "filled": 0}
    db_session.expire_all()
    assert db_session.query(JournalSession).filter_by(
        external_id="bf-1").one().calculated_integration_time_minutes is None

    applied = backfill_integration_minutes(db_session, dry_run=False)
    assert applied["scanned"] == 2
    assert applied["fillable"] == 1
    assert applied["filled"] == 1

    db_session.expire_all()
    filled = db_session.query(JournalSession).filter_by(external_id="bf-1").one()
    assert filled.calculated_integration_time_minutes == 20.0
    # Recompute wrote only the one column: the sync rev and stamp are untouched.
    assert filled.sync_rev == rev_before
    assert filled.sync_changed_at == stamp_before
    # The no-subs session is still NULL (correctly: nothing to fill).
    assert db_session.query(JournalSession).filter_by(
        external_id="bf-2").one().calculated_integration_time_minutes is None

    # Idempotent: a second apply has nothing left to fill (only bf-2, still
    # NULL with no subs data, is scanned).
    again = backfill_integration_minutes(db_session, dry_run=False)
    assert again == {"scanned": 1, "fillable": 0, "no_subs": 1, "filled": 0}


def test_backfill_scoped_to_user_id(db_session):
    user_a = _user(db_session, "a")
    user_b = _user(db_session, "b")
    _add_session(db_session, user_a.id, "a-1",
                 number_of_subs_light=10, exposure_time_per_sub_sec=60)
    _add_session(db_session, user_b.id, "b-1",
                 number_of_subs_light=10, exposure_time_per_sub_sec=60)

    result = backfill_integration_minutes(db_session, dry_run=False,
                                          user_id=user_a.id)
    assert result["filled"] == 1
    db_session.expire_all()
    assert db_session.query(JournalSession).filter_by(
        external_id="a-1").one().calculated_integration_time_minutes == 10.0
    assert db_session.query(JournalSession).filter_by(
        external_id="b-1").one().calculated_integration_time_minutes is None


# --- the startup hook --------------------------------------------------------

def test_startup_hook_fills_nulls_and_is_idempotent(db_session):
    user = _user(db_session)
    sess = _add_session(db_session, user.id, "su-1",
                        number_of_subs_light=10,
                        exposure_time_per_sub_sec=120)
    rev_before = sess.sync_rev
    stamp_before = sess.sync_changed_at

    _backfill_integration_on_startup()

    db_session.expire_all()
    filled = db_session.query(JournalSession).filter_by(external_id="su-1").one()
    assert filled.calculated_integration_time_minutes == 20.0
    assert filled.sync_rev == rev_before
    assert filled.sync_changed_at == stamp_before

    # Second run: nothing left to fill.
    _backfill_integration_on_startup()
    db_session.expire_all()
    assert db_session.query(JournalSession).filter_by(
        external_id="su-1").one().calculated_integration_time_minutes == 20.0


def test_startup_hook_swallows_an_exception(db_session, monkeypatch):
    def boom(db, *, dry_run=True, user_id=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("nova.helpers.backfill_integration_minutes", boom)
    # Must not raise: a failure inside the backfill must never break startup.
    _backfill_integration_on_startup()


# --- the once-per-process startup wrapper ------------------------------------

def test_run_backfill_once_per_process_skips_when_env_var_set(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr("nova._backfill_integration_on_startup",
                        lambda: calls.append(1))
    monkeypatch.setenv("NOVA_SKIP_STARTUP_BACKFILL", "1")
    monkeypatch.setitem(app.config, "TESTING", False)

    _run_backfill_once_per_process()
    assert calls == []


def test_run_backfill_once_per_process_skips_when_testing(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr("nova._backfill_integration_on_startup",
                        lambda: calls.append(1))
    monkeypatch.delenv("NOVA_SKIP_STARTUP_BACKFILL", raising=False)

    monkeypatch.setitem(app.config, "TESTING", True)
    _run_backfill_once_per_process()
    assert calls == []

    monkeypatch.setitem(app.config, "TESTING", False)
    _run_backfill_once_per_process()
    assert calls == [1]


@pytest.mark.skipif(not _HAS_FCNTL, reason="cross-process lock needs fcntl")
def test_run_backfill_once_per_process_skips_when_lock_held(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr("nova._backfill_integration_on_startup",
                        lambda: calls.append(1))
    monkeypatch.delenv("NOVA_SKIP_STARTUP_BACKFILL", raising=False)

    fh = try_acquire_file_lock(os.path.join(INSTANCE_PATH, "backfill"))
    assert fh is not None
    try:
        _run_backfill_once_per_process()
        assert calls == []          # another process holds the lock
    finally:
        release_file_lock(fh)
