"""
The printable report pages render, and load the self-hosted fonts.

Both templates link css/fonts.css (DM Sans / DM Mono from static/fonts/) and name
no third-party font CDN, so a report looks the same offline.
"""
from datetime import date

from nova.models import DbUser, JournalSession, Project


def _default_user(db_session):
    return db_session.query(DbUser).filter_by(username="default").one()


def _assert_self_hosted_fonts(html):
    assert "fonts.css" in html
    assert "fonts.googleapis" not in html


def test_project_report_renders(su_client_logged_in, db_session):
    user = _default_user(db_session)
    project = Project(id="p-report", user_id=user.id, name="Orion Mosaic",
                      status="In Progress", target_object_name="M42")
    db_session.add(project)
    db_session.flush()
    session = JournalSession(user_id=user.id, object_name="M42",
                             date_utc=date(2026, 1, 10))
    session.projects.append(project)
    db_session.add(session)
    db_session.commit()

    resp = su_client_logged_in.get("/project/report_page/p-report")

    assert resp.status_code == 200
    _assert_self_hosted_fonts(resp.get_data(as_text=True))


def test_journal_report_renders(su_client_logged_in, db_session):
    user = _default_user(db_session)
    session = JournalSession(user_id=user.id, object_name="M42",
                             date_utc=date(2026, 1, 10))
    db_session.add(session)
    db_session.commit()

    resp = su_client_logged_in.get(f"/journal/report_page/{session.id}")

    assert resp.status_code == 200
    _assert_self_hosted_fonts(resp.get_data(as_text=True))
