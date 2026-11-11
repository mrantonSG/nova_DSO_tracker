"""An empty or whitespace-only log upload is rejected on both journal routes.

The add route used to drop a 0-byte log silently (and kept a whitespace-only
one); the edit route wrote it, stored the path, stamped it for sync and, for
NINA, flashed "NINA log imported successfully." anyway. Both now refuse it via
_flash_rejected_upload with one shared message.
"""
from datetime import date
from io import BytesIO

import pytest

from nova.models import JournalSession

EMPTY_MSG = "The uploaded log file is empty."
NINA_OK_MSG = "NINA log imported successfully."

# (route field, uploaded name, the column it lands in)
LOGS = [
    pytest.param("asiair_log", "Autorun_Log.txt", "asiair_log_content", id="asiair"),
    pytest.param("phd2_log", "PHD2_GuideLog.txt", "phd2_log_content", id="phd2"),
    pytest.param("nina_log", "NINA_log.log", "nina_log_content", id="nina"),
]
EMPTY_BODIES = [
    pytest.param(b"", id="zero-bytes"),
    pytest.param(b"  \r\n\t\n ", id="whitespace"),
]

SESSION_FORM = {"session_date": "2026-01-10"}


@pytest.fixture
def world(multi_user_client, db_session, monkeypatch):
    """Logged-in client plus spies on the two side effects a saved log has."""
    client, ids = multi_user_client
    import nova.blueprints.journal as journal

    saved, marked = [], []
    real_save, real_mark = journal.save_log_to_filesystem, journal.mark_file_changed

    def spy_save(session_id, log_type, *args, **kwargs):
        saved.append(log_type)
        return real_save(session_id, log_type, *args, **kwargs)

    def spy_mark(row, column):
        marked.append(column)
        return real_mark(row, column)

    monkeypatch.setattr(journal, "save_log_to_filesystem", spy_save)
    monkeypatch.setattr(journal, "mark_file_changed", spy_mark)

    class W:
        pass

    w = W()
    w.client, w.db, w.uid = client, db_session, ids["user_a_id"]
    w.saved, w.marked = saved, marked
    return w


def _post(w, url, data, files):
    resp = w.client.post(url, data=dict(data, **files),
                         content_type="multipart/form-data")
    assert resp.status_code == 302, resp.status_code
    w.db.expire_all()
    return resp


def _flashes(w):
    with w.client.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def _session(db, uid):
    row = JournalSession(user_id=uid, date_utc=date(2026, 1, 10), external_id="ext-1")
    db.add(row)
    db.commit()
    db.expire_all()
    return row


@pytest.mark.parametrize("body", EMPTY_BODIES)
@pytest.mark.parametrize("field,filename,column", LOGS)
def test_an_empty_log_on_add_is_rejected(world, field, filename, column, body):
    _post(world, "/journal/add",
          {"session_date": "2026-01-10", "target_object_id": "M 42"},
          {field: (BytesIO(body), filename)})

    session = world.db.query(JournalSession).filter_by(user_id=world.uid).one()
    assert not getattr(session, column)
    assert world.saved == []
    assert column not in world.marked
    assert ("error", EMPTY_MSG) in _flashes(world)


@pytest.mark.parametrize("body", EMPTY_BODIES)
@pytest.mark.parametrize("field,filename,column", LOGS)
def test_an_empty_log_on_edit_is_rejected(world, field, filename, column, body):
    session = _session(world.db, world.uid)

    _post(world, f"/journal/edit/{session.id}", SESSION_FORM,
          {field: (BytesIO(body), filename)})

    session = world.db.get(JournalSession, session.id)
    assert not getattr(session, column)
    assert session.log_analysis_cache is None
    assert world.saved == []
    assert column not in world.marked
    flashes = _flashes(world)
    assert ("error", EMPTY_MSG) in flashes
    assert ("success", NINA_OK_MSG) not in flashes


def test_an_empty_log_on_edit_keeps_the_existing_log(world):
    """A rejected upload must not overwrite or clear a log already attached."""
    session = _session(world.db, world.uid)
    _post(world, f"/journal/edit/{session.id}", SESSION_FORM,
          {"nina_log": (BytesIO(b"first body"), "NINA_log.log")})
    stored = world.db.get(JournalSession, session.id).nina_log_content
    assert stored
    world.saved.clear()
    world.marked.clear()

    _post(world, f"/journal/edit/{session.id}", SESSION_FORM,
          {"nina_log": (BytesIO(b""), "NINA_log.log")})

    assert world.db.get(JournalSession, session.id).nina_log_content == stored
    assert world.saved == []
    assert "nina_log_content" not in world.marked


def test_a_non_empty_nina_log_on_edit_still_imports(world):
    session = _session(world.db, world.uid)

    _post(world, f"/journal/edit/{session.id}", SESSION_FORM,
          {"nina_log": (BytesIO(b"2026-01-10T20:00:00|INFO|start\n"), "NINA_log.log")})

    session = world.db.get(JournalSession, session.id)
    assert session.nina_log_content
    assert world.saved == ["nina"]
    assert "nina_log_content" in world.marked
    flashes = _flashes(world)
    assert ("success", NINA_OK_MSG) in flashes
    assert ("error", EMPTY_MSG) not in flashes
