"""The Sync settings page: connected-state buttons and the two empty states.

Rendered through the real /sync route. The connected states are set up by
writing a SyncState row directly — no server is contacted.
"""
import re
from datetime import datetime

import pytest

from nova.models import DbUser, SyncState

BUTTON = re.compile(r'<button\b([^>]*)>(.*?)</button>', re.S)
CLASS = re.compile(r'class="([^"]*)"')
PRIMARY = "action-button"


def _status_card_buttons(body):
    """(label, classes) for each button in the paired status card, in order."""
    card = body.split('class="sync-status-card"', 1)[1].split("</li>", 1)[0]
    out = []
    for attrs, label in BUTTON.findall(card):
        classes = CLASS.search(attrs).group(1).split()
        out.append((label.strip(), classes))
    return out


@pytest.fixture
def page(su_client_logged_in, db_session, monkeypatch):
    monkeypatch.setattr("nova.blueprints.sync_tokens.NOVA_SYNC_CLIENT", True)
    user = db_session.query(DbUser).filter_by(username="default").one()

    def render(state):
        db_session.query(SyncState).filter_by(user_id=user.id).delete()
        if state is not None:
            db_session.add(SyncState(
                user_id=user.id, server_url="https://hub.example", token="t",
                server_account="tester", paired_at=datetime(2026, 1, 1),
                last_sync_at=datetime(2026, 1, 2) if state == "normal" else None,
                local_pushed_rev=0))
        db_session.commit()
        resp = su_client_logged_in.get("/sync")
        assert resp.status_code == 200
        return resp.data.decode()

    return render


def test_sync_page_buttons_and_empty_states(page):
    # Normal: Sync now is the one primary, Download is not.
    normal = _status_card_buttons(page("normal"))
    assert [label for label, _ in normal] == ["Sync now", "Download from server", "Disconnect"]
    assert PRIMARY in normal[0][1]
    assert PRIMARY not in normal[1][1]
    assert sum(PRIMARY in classes for _, classes in normal) == 1

    # needs_download: Download is the one primary; Sync now is not.
    needs = _status_card_buttons(page("needs_download"))
    assert [label for label, _ in needs] == ["Sync now", "Download from server", "Disconnect"]
    assert PRIMARY in needs[1][1]
    assert PRIMARY not in needs[0][1]
    assert sum(PRIMARY in classes for _, classes in needs) == 1

    # Disconnected + no tokens: both empty states render, without the emoji.
    empty = page(None)
    boxes = re.findall(r'<div class="sync-empty-state">(.*?)</div>\s*</div>', empty, re.S)
    assert len(boxes) == 2
    for box in boxes:
        assert "\U0001F511" not in box and "&#128273;" not in box  # key
        assert "\U0001F517" not in box and "&#128279;" not in box  # link
        assert "gallery-empty-icon" not in box
