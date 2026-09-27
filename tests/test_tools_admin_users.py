"""
Tests that the tools blueprint admin guards respect ADMIN_USERS instead of a
hard-coded "admin" username.  Multi-user mode only.
"""
from unittest.mock import MagicMock


def _get_flashes(client):
    with client.session_transaction() as sess:
        return list(sess.get('_flashes', []))


def test_repair_db_denied_for_non_admin(multi_user_client, monkeypatch):
    """A user NOT in ADMIN_USERS still gets the 403-style redirect + error flash."""
    client, _ = multi_user_client
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)
    monkeypatch.setattr('nova.blueprints.tools.ADMIN_USERS', {"admin"})
    repair = MagicMock()
    monkeypatch.setattr('nova.blueprints.tools.repair_journals', repair)

    resp = client.post('/tools/repair_db')

    assert resp.status_code == 302
    assert ('error', "Not authorized.") in _get_flashes(client)
    assert repair.called is False


def test_repair_db_allowed_for_admin_users_member(multi_user_client, monkeypatch):
    """A user whose name is in ADMIN_USERS (but not "admin") can run the repair."""
    client, _ = multi_user_client
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)
    monkeypatch.setattr('nova.blueprints.tools.ADMIN_USERS', {"UserA"})
    repair = MagicMock()
    monkeypatch.setattr('nova.blueprints.tools.repair_journals', repair)

    resp = client.post('/tools/repair_db')

    assert resp.status_code == 302
    assert ('success', "Database repair completed.") in _get_flashes(client)
    repair.assert_called_once_with(dry_run=False)
