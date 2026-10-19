"""The graph page's JSON branch: session rows, not session logs.

/graph_dashboard/<object>?format=json ships the same session dicts twice
(sessions.grouped and sessions.all_specific). They must not carry the four log
columns; every other key stays as it was. The branch's only reader,
tests/test_uid_reads_objects_locations.py, uses id and project_name, so this one
test is the whole contract.
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
