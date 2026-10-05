"""
Mobile quick journal entry: choosing "new project" with a name creates the
project for the current user and links the new session to it.
"""
from nova.models import JournalSession, Project


def test_mobile_journal_new_creates_and_links_new_project(multi_user_client, db_session):
    client, ids = multi_user_client

    resp = client.post('/m/journal/new', data={
        'session_date': "2026-01-10",
        'target_object_id': "M42",
        'location_name': "UserA_Home",
        'project_selection': "new_project",
        'new_project_name': "Orion Mosaic",
    })

    assert resp.status_code == 302
    db_session.expire_all()
    project = db_session.query(Project).filter_by(user_id=ids["user_a_id"], name="Orion Mosaic").one()
    assert project.target_object_name == "M42"
    session_a = db_session.query(JournalSession).filter_by(user_id=ids["user_a_id"]).one()
    assert session_a.project_id == project.id
