"""/api/desktop_library: the desktop dashboard's library columns, with no astronomy.

The browser computes the astronomy columns itself (static/js/nova_astro.js) and
takes everything else from here: per enabled object, the non-astronomy fields of
the /api/get_desktop_data_batch row (obj.to_dict() plus framing_rig, has_notes,
session_count), in the batch's order. These tests pin the two together by walking
every batch page, and cover the three added fields' edge cases, user isolation,
auth, and the settings.skyglow_flags gate /api/desktop_inputs now carries.
"""

from datetime import date

import pytest

from nova.config import astro_context_cache
from nova.models import AstroObject, DbUser, JournalSession, Location, Rig, SavedFraming

LOC_NAME = "Default Test Loc"  # created by the conftest client fixture (lat 50, lon 10, UTC)
LIBRARY_KEYS = set(AstroObject().to_dict()) | {"framing_rig", "has_notes", "session_count"}


def _user(db_session, username="default"):
    return db_session.query(DbUser).filter_by(username=username).one()


def _library(client):
    response = client.get('/api/desktop_library')
    assert response.status_code == 200
    data = response.get_json()
    assert set(data.keys()) == {"objects"}
    return data["objects"]


def _batch(client, location):
    """Every batch row, walking the pages the way dashboard.js does."""
    rows, offset = [], 0
    while True:
        response = client.get('/api/get_desktop_data_batch',
                              query_string={"offset": offset, "limit": 50, "location": location})
        assert response.status_code == 200
        page = response.get_json()
        rows += page["results"]
        if not page["has_more"]:
            return rows
        offset += 50


def _by_name(rows):
    return {r["Object"]: r for r in rows}


def _assert_library_matches_batch(client, location):
    library, batch = _library(client), _batch(client, location)
    assert [r["Object"] for r in library] == [r["Object"] for r in batch]
    for lib_row, batch_row in zip(library, batch):
        assert set(lib_row.keys()) == LIBRARY_KEYS
        assert lib_row == {k: batch_row[k] for k in LIBRARY_KEYS}
    return library


# --- the batch, restricted to the library keys -------------------------------------------

def test_library_rows_are_the_batch_rows_in_batch_order(client, db_session):
    """130 objects (three batch pages), mixed-case names so SQLite's BINARY order
    is visible, both the computed and the never-rises (pre-filter) batch paths,
    and every library field populated somewhere."""
    user = _user(db_session)
    rig = Rig(user_id=user.id, rig_name="Main Rig")
    db_session.add(rig)
    notes = [None, "", "None", "<div><br></div>", "<p>Ha</p>", "plain"]
    objs = []
    for i in range(130):
        obj = AstroObject(
            user_id=user.id,
            object_name=f"obj {i:03d}" if i % 3 else f"OBJ {i:03d}",
            common_name=f"Common {i}", ra_hours=(i * 0.37) % 24, dec_deg=-85 + (i * 1.31) % 170,
            type="G", constellation="Ori", magnitude=i / 10, size=i / 7, sb=i / 9,
            project_name=notes[i % len(notes)], active_project=bool(i % 2),
            image_url=f"http://img/{i}" if i % 4 == 0 else None, description_text="d" * (i % 5),
            enabled=(i % 11 != 0))
        db_session.add(obj)
        objs.append(obj)
    db_session.flush()
    for i, obj in enumerate(objs[:40]):
        if i % 3 == 0:
            db_session.add(SavedFraming(user_id=user.id, object_name=obj.object_name,
                                        object_record_uid=obj.record_uid,
                                        rig_record_uid=rig.record_uid))
        for _ in range(i % 3):
            db_session.add(JournalSession(user_id=user.id, date_utc=date(2025, 1, 1),
                                          object_name=obj.object_name,
                                          object_record_uid=obj.record_uid))
    db_session.commit()
    astro_context_cache.clear()

    library = _assert_library_matches_batch(client, LOC_NAME)

    # Code-point order (what SQLite BINARY gives): upper case before lower case,
    # so the conftest's 'M42' and every 'OBJ …' come before any 'obj …'.
    names = [r["Object"] for r in library]
    assert names == sorted(names)
    assert names.index("M42") < names.index("OBJ 129") < names.index("obj 001")
    # Both batch paths were exercised.
    batch = _by_name(_batch(client, LOC_NAME))
    assert any(r.get("is_geometrically_impossible") for r in batch.values())
    assert any("below_skyglow_floor" in r for r in batch.values())


def test_library_leaves_out_disabled_objects(client, db_session):
    user = _user(db_session)
    db_session.add(AstroObject(user_id=user.id, object_name="NGC 7000",
                               ra_hours=20.98, dec_deg=44.5, enabled=False))
    db_session.commit()

    names = [r["Object"] for r in _library(client)]

    assert "M42" in names
    assert "NGC 7000" not in names


def test_library_has_no_astronomy(client):
    for row in _library(client):
        assert set(row.keys()) == LIBRARY_KEYS
        assert "Altitude Current" not in row


def test_library_does_not_need_a_location(client, db_session):
    """The batch is 404 without a location; the library is location-free."""
    user = _user(db_session)
    db_session.query(Location).filter_by(user_id=user.id).delete()
    db_session.commit()
    astro_context_cache.clear()

    assert [r["Object"] for r in _library(client)] == ["M42"]


# --- framing_rig, has_notes, session_count -----------------------------------------------

def test_framing_rig_labels(client, db_session):
    user = _user(db_session)
    rig = Rig(user_id=user.id, rig_name="Main Rig")
    db_session.add(rig)
    framings = {
        # name: (rig_record_uid, stored rig_name) or None for no framing
        "F1 rig by uid": ("RIG", "stale text"),     # the rig, found by UID, wins
        "F2 rig gone": ("gone-uid", "Old Rig"),     # UID set but no such rig: '', not the text
        "F3 no rig uid": (None, "Typed Rig"),       # no UID: the stored text
        "F4 no uid no text": (None, None),          # framing without any rig: ''
        "F5 no framing": None,
    }
    db_session.flush()
    for name, framing in framings.items():
        obj = AstroObject(user_id=user.id, object_name=name, ra_hours=5.58, dec_deg=-5.4)
        db_session.add(obj)
        db_session.flush()
        if framing is not None:
            rig_uid, rig_name = framing
            db_session.add(SavedFraming(user_id=user.id, object_name=name,
                                        object_record_uid=obj.record_uid,
                                        rig_record_uid=rig.record_uid if rig_uid == "RIG" else rig_uid,
                                        rig_name=rig_name))
    db_session.commit()

    rows = _by_name(_assert_library_matches_batch(client, LOC_NAME))

    assert rows["F1 rig by uid"]["framing_rig"] == "Main Rig"
    assert rows["F2 rig gone"]["framing_rig"] == ""
    assert rows["F3 no rig uid"]["framing_rig"] == "Typed Rig"
    assert rows["F4 no uid no text"]["framing_rig"] == ""
    assert rows["F5 no framing"]["framing_rig"] == ""


@pytest.mark.parametrize("project_name, expected", [
    (None, False),
    ("", False),
    ("   ", False),
    ("None", False),
    ("none", False),
    ("<div><br></div>", False),
    ("<p> None </p>", False),
    ("<div>Shoot Ha first</div>", True),
    ("plain text", True),
])
def test_has_notes(client, db_session, project_name, expected):
    user = _user(db_session)
    db_session.add(AstroObject(user_id=user.id, object_name="N1", ra_hours=5.58, dec_deg=-5.4,
                               project_name=project_name))
    db_session.commit()

    rows = _by_name(_assert_library_matches_batch(client, LOC_NAME))

    assert rows["N1"]["has_notes"] is expected


def test_session_count(client, db_session):
    user = _user(db_session)
    obj = AstroObject(user_id=user.id, object_name="S1", ra_hours=5.58, dec_deg=-5.4)
    db_session.add(obj)
    db_session.flush()
    for day in (1, 2, 3):
        db_session.add(JournalSession(user_id=user.id, date_utc=date(2025, 1, day),
                                      object_name="S1", object_record_uid=obj.record_uid))
    db_session.commit()

    rows = _by_name(_assert_library_matches_batch(client, LOC_NAME))

    assert rows["S1"]["session_count"] == 3
    assert rows["M42"]["session_count"] == 0


# --- multi-user isolation, auth ----------------------------------------------------------

def test_library_never_shows_another_users_rows(multi_user_client, db_session):
    client, ids = multi_user_client
    a_id, b_id = ids["user_a_id"], ids["user_b_id"]

    rig_b = Rig(user_id=b_id, rig_name="B Secret Rig")
    db_session.add(rig_b)
    obj_a = AstroObject(user_id=a_id, object_name="M42", ra_hours=5.58, dec_deg=-5.4)
    db_session.add_all([obj_a, AstroObject(user_id=b_id, object_name="B Only Object",
                                           ra_hours=1.0, dec_deg=40.0)])
    db_session.flush()
    # A's framing points at B's rig UID: it must not resolve to B's rig.
    db_session.add(SavedFraming(user_id=a_id, object_name="M42", object_record_uid=obj_a.record_uid,
                                rig_record_uid=rig_b.record_uid))
    # B's own framing and sessions carry A's object UID: none of them may count for A.
    db_session.add(SavedFraming(user_id=b_id, object_name="M42", object_record_uid=obj_a.record_uid,
                                rig_name="B Secret Typed Rig"))
    db_session.add(JournalSession(user_id=a_id, date_utc=date(2025, 1, 1),
                                  object_name="M42", object_record_uid=obj_a.record_uid))
    for day in (2, 3):
        db_session.add(JournalSession(user_id=b_id, date_utc=date(2025, 1, day),
                                      object_name="M42", object_record_uid=obj_a.record_uid))
    db_session.commit()

    response = client.get('/api/desktop_library')
    assert response.status_code == 200
    assert b"B Secret" not in response.data
    assert b"B Only Object" not in response.data

    rows = _by_name(_assert_library_matches_batch(client, "UserA_Home"))
    own = {o.object_name for o in db_session.query(AstroObject).filter_by(user_id=a_id, enabled=True)}
    assert set(rows) == own
    assert rows["M42"]["framing_rig"] == ""
    assert rows["M42"]["session_count"] == 1


def test_library_auth_matches_the_batch(mu_client_logged_out):
    """Logged out in multi-user mode: the same refusal as the batch."""
    library = mu_client_logged_out.get('/api/desktop_library')
    batch = mu_client_logged_out.get('/api/get_desktop_data_batch')

    assert library.status_code == 401
    assert (library.status_code, library.get_json()) == (batch.status_code, batch.get_json())


def test_library_serves_the_guest_user_like_the_batch(mu_client_logged_out, db_session):
    guest = _user(db_session, "guest_user")
    db_session.add(Location(user_id=guest.id, name="Guest Site", lat=50, lon=10,
                            timezone="UTC", is_default=True))
    db_session.add(AstroObject(user_id=guest.id, object_name="Guest M31",
                               ra_hours=0.71, dec_deg=41.3))
    db_session.commit()
    with mu_client_logged_out.session_transaction() as sess:
        sess['guest_mode'] = True

    rows = _assert_library_matches_batch(mu_client_logged_out, "Guest Site")

    assert "Guest M31" in [r["Object"] for r in rows]


# --- /api/desktop_inputs settings.skyglow_flags ------------------------------------------

@pytest.mark.parametrize("token, expected", [("a-token", True), ("", False), (None, False)])
def test_desktop_inputs_skyglow_flags_follows_the_token(client, monkeypatch, token, expected):
    """The batch's gate is `if os.environ.get("NASA_EARTHDATA_TOKEN")`, so an empty
    value counts as unset."""
    if token is None:
        monkeypatch.delenv("NASA_EARTHDATA_TOKEN", raising=False)
    else:
        monkeypatch.setenv("NASA_EARTHDATA_TOKEN", token)

    settings = client.get('/api/desktop_inputs').get_json()["settings"]

    assert settings["skyglow_flags"] is expected
