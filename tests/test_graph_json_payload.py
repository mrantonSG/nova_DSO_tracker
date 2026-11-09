"""The graph page's JSON branch: session rows, not session logs.

/graph_dashboard/<object>?format=json ships the same session dicts twice
(sessions.grouped and sessions.all_specific). They must not carry the four log
columns; every other key stays as it was. The branch's only reader,
tests/test_uid_reads_objects_locations.py, uses id and project_name, so this one
test is the whole contract.

The file columns are the other half of the contract: an emptied slot holds ""
(session_image_file is instance sync's removal marker there, see
nova.sync_engine.FILE_REMOVED) and a never-used one holds NULL. Truthiness
readers cannot tell them apart, but a JSON client can, so the payload keeps
shipping null for both.
"""
import json
from datetime import date

from nova import AstroObject, DbUser, JournalSession
from nova.record_links import sync_session_links

_LOG_COLUMNS = ("log_analysis_cache", "asiair_log_content",
                "phd2_log_content", "nina_log_content")


def test_json_session_rows_carry_no_log_columns(client, db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    obj = db_session.query(AstroObject).filter_by(user_id=user.id,
                                                  object_name="M42").one()
    session = JournalSession(
        user_id=user.id, object_name="M42", date_utc=date(2026, 1, 10),
        object_record_uid=obj.record_uid,
        location_name="Default Test Loc", notes="<p>Clear night</p>",
        log_analysis_cache=json.dumps({"ZZ_CACHE_MARKER": 1}),
        asiair_log_content="asiair.log",
        phd2_log_content="phd2.log",
        nina_log_content="nina.log",
    )
    db_session.add(session)
    sync_session_links(db_session, session)   # the object page finds it by UID
    db_session.commit()

    data = client.get("/graph_dashboard/M42?format=json").get_json()

    rows = list(data["sessions"]["all_specific"])
    for group in data["sessions"]["grouped"]:
        rows.extend(group["sessions"])

    assert rows                                   # both branches are exercised
    for row in rows:
        for column in _LOG_COLUMNS:
            assert column not in row

    # Minimal shape change: everything else is still there.
    assert {"id", "object_name", "notes", "date_utc"} <= set(rows[0])
    assert "ZZ_CACHE_MARKER" not in json.dumps(data)


def test_json_ships_an_emptied_file_slot_as_null(client, db_session):
    """A removal is "" in the row, null in the payload, as it always was.

    The user deleting a session photo (or a project image) leaves "" in the
    column, the marker instance sync reads as an explicit removal. In JSON that
    would surface as a value where the client has always seen null -- and an
    untouched slot must keep looking exactly the same as a removed one.
    """
    user = db_session.query(DbUser).filter_by(username="default").one()
    obj = db_session.query(AstroObject).filter_by(user_id=user.id,
                                                  object_name="M42").one()
    removed = JournalSession(user_id=user.id, object_name="M42",
                             date_utc=date(2026, 1, 11),
                             object_record_uid=obj.record_uid,
                             location_name="Default Test Loc",
                             session_image_file="")      # removed by the user
    untouched = JournalSession(user_id=user.id, object_name="M42",
                               date_utc=date(2026, 1, 12),
                               object_record_uid=obj.record_uid,
                               location_name="Default Test Loc")   # never had one
    kept = JournalSession(user_id=user.id, object_name="M42",
                          date_utc=date(2026, 1, 13),
                          object_record_uid=obj.record_uid,
                          location_name="Default Test Loc",
                          session_image_file="42.png")
    db_session.add_all([removed, untouched, kept])
    for session in (removed, untouched, kept):
        sync_session_links(db_session, session)
    db_session.commit()

    data = client.get("/graph_dashboard/M42?format=json").get_json()

    rows = list(data["sessions"]["all_specific"])
    for group in data["sessions"]["grouped"]:
        rows.extend(group["sessions"])
    by_id = {row["id"]: row for row in rows}

    assert by_id[removed.id]["session_image_file"] is None
    assert by_id[untouched.id]["session_image_file"] is None
    # A slot that really holds a file still ships the name.
    assert by_id[kept.id]["session_image_file"] == "42.png"
