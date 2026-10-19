"""
/api/journal_sessions - step A of the dashboard session split.

The endpoint returns exactly the row shape templates/index.html inlines today
(window.NOVA_INDEX.journalSessions). Step A keeps that inline in place, so
test_endpoint_rows_match_index_inline_rows() pins the two together; step B
deletes that test with the inline.
"""
import json
import re
from datetime import date

from nova.models import AstroObject, DbUser, JournalSession, Project
from nova.journal_rows import JOURNAL_SESSION_FIELDS


_REAL_FIELDS = set(JOURNAL_SESSION_FIELDS) - {'is_stub'}   # is_stub only on stubs
_STUB_FIELDS = {
    'id', 'is_stub', 'project_id', 'project_name', 'project_status', 'object_name',
    'date_utc', 'location_name', 'telescope_setup_notes',
    'calculated_integration_time_minutes', 'session_rating_subjective',
    'other_projects', 'other_projects_count',
}


def _default_user(db):
    return db.query(DbUser).filter_by(username="default").one()


def _session(db, user_id, name="M42", **cols):
    s = JournalSession(user_id=user_id, object_name=name, date_utc=date(2026, 1, 10))
    for key, value in cols.items():
        setattr(s, key, value)
    db.add(s)
    db.flush()
    return s


def _index_inline_rows(client):
    html = client.get("/").get_data(as_text=True)
    match = re.search(r"journalSessions:\s*(\[.*?\]),\n", html, re.S)
    assert match, "journalSessions payload not found in index.html"
    return json.loads(match.group(1))


def _endpoint_rows(client):
    resp = client.get("/api/journal_sessions")
    assert resp.status_code == 200
    return resp.get_json()["sessions"]


def test_real_row_field_set_is_exactly_the_contract(su_client_logged_in, db_session):
    _session(db_session, _default_user(db_session).id)
    db_session.commit()

    (row,) = _endpoint_rows(su_client_logged_in)
    assert set(row) == _REAL_FIELDS


def test_payload_carries_no_log_analysis_cache_and_no_notes(su_client_logged_in, db_session):
    _session(db_session, _default_user(db_session).id,
             log_analysis_cache='{"phd2": {"frames": [[1.0, 2.0]]}}',
             notes="x" * 5000)
    db_session.commit()

    (row,) = _endpoint_rows(su_client_logged_in)
    assert "log_analysis_cache" not in row
    assert "notes" not in row


def test_date_utc_is_an_iso_string(su_client_logged_in, db_session):
    _session(db_session, _default_user(db_session).id)
    db_session.commit()

    (row,) = _endpoint_rows(su_client_logged_in)
    assert row["date_utc"] == "2026-01-10"


def test_project_without_sessions_becomes_one_stub_row(su_client_logged_in, db_session):
    user = _default_user(db_session)
    db_session.add(Project(id="p-empty", user_id=user.id, name="Empty",
                           status="In Progress", target_object_name="M31"))
    db_session.commit()

    stubs = [r for r in _endpoint_rows(su_client_logged_in) if r.get("is_stub")]
    assert len(stubs) == 1
    assert set(stubs[0]) == _STUB_FIELDS
    assert stubs[0]["id"] == -1
    assert stubs[0]["project_id"] == "p-empty"
    assert stubs[0]["project_name"] == "Empty"
    assert stubs[0]["object_name"] == "M31"


def test_another_users_sessions_are_never_returned(multi_user_client, db_session):
    client, ids = multi_user_client
    _session(db_session, ids["user_a_id"], name="M42")
    _session(db_session, ids["user_b_id"], name="M31")
    db_session.commit()

    assert [r["object_name"] for r in _endpoint_rows(client)] == ["M42"]


def test_guest_is_allowed_and_gets_the_same_rows_as_the_index(mu_client_logged_out):
    """Guests reach the endpoint (200, not 401) and see exactly what index()
    still hands them: the shared guest_user's own sessions."""
    with mu_client_logged_out.session_transaction() as sess:
        sess["guest_mode"] = True

    resp = mu_client_logged_out.get("/api/journal_sessions")
    assert resp.status_code == 200
    rows = resp.get_json()["sessions"]
    assert isinstance(rows, list)

    inline = {r["id"]: r for r in _index_inline_rows(mu_client_logged_out)}
    assert {r["id"]: r for r in rows} == inline


def test_endpoint_rows_match_index_inline_rows(su_client_logged_in, db_session):
    """Every field the endpoint keeps must equal today's inlined value."""
    user = _default_user(db_session)
    obj = db_session.query(AstroObject).filter_by(user_id=user.id, object_name="M42").one()
    s = _session(db_session, user.id, name="M42", location_name="Default Test Loc",
                 calculated_integration_time_minutes=300.0, session_rating_subjective=4)
    s.object_record_uid = obj.record_uid
    db_session.add(Project(id="p-1", user_id=user.id, name="Proj", status="Completed"))
    db_session.commit()

    inline = {r["id"]: r for r in _index_inline_rows(su_client_logged_in)}
    endpoint = {r["id"]: r for r in _endpoint_rows(su_client_logged_in)}

    assert inline.keys() == endpoint.keys()
    for sid, row in endpoint.items():
        for key, value in row.items():
            assert inline[sid].get(key) == value, f"field {key!r} differs for session {sid}"
