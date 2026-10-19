"""
/api/framing_objects - the graph page's Framing Assistant object list.

Until diff 2 the graph page inlined this list as NOVA_GRAPH_DATA.allObjects;
the Framing Assistant now fetches it once, when it opens. The rows must be
exactly what core.graph_dashboard() inlined - same fields, same values, same
order - which test_row_is_exactly_the_inlined_row() pins by literal, while
test_graph_page_no_longer_inlines_the_catalog() and
test_graph_js_reads_the_endpoint_and_not_graph_data() pin the two halves of the
move (the page stops carrying it, the JS stops reading it from the page).

Not covered: the .filter(ra_hours != None, dec_deg != None) the query carries
over from graph_dashboard(). Both columns are NOT NULL in the model, so no row
can reach the endpoint without coordinates on a schema built from the models -
the guard only excludes rows from a pre-constraint instance/app.db, which a
test cannot fabricate.
"""
import os

import pytest

from nova.models import AstroObject, DbUser

# The graph page contract: exactly the keys graph_dashboard() inlined, read by
# static/js/graph_view_chart.js addNovaObjectsCatalog() - ra_deg/dec_deg/
# object_name/common_name/id place the marker and its label, the rest feed the
# Inspiration modal. A new field means a new entry here, next to the builder.
_FIELDS = {
    "id", "object_name", "common_name", "ra_deg", "dec_deg",
    "image_url", "image_credit", "image_source_link",
    "description_text", "description_credit", "description_source_link",
    "Size", "Type", "Constellation", "Object", "Common Name",
}


def _obj(db, user_id, name, ra_hours=1.0, dec_deg=20.0, **cols):
    obj = AstroObject(user_id=user_id, object_name=name,
                      ra_hours=ra_hours, dec_deg=dec_deg)
    for key, value in cols.items():
        setattr(obj, key, value)
    db.add(obj)
    db.flush()
    return obj


def _rows(client):
    resp = client.get("/api/framing_objects")
    assert resp.status_code == 200
    return resp.get_json()["objects"]


def _names(rows):
    return [row["object_name"] for row in rows]


def _default_user(db):
    return db.query(DbUser).filter_by(username="default").one()


def test_row_is_exactly_the_inlined_row(client, db_session):
    user = _default_user(db_session)
    obj = _obj(db_session, user.id, "NGC 7000", ra_hours=20.98, dec_deg=44.33,
               common_name="North America Nebula",
               image_url="/uploads/default/ngc7000.jpg",
               image_credit="Jane Doe",
               image_source_link="https://example.org/ngc7000",
               description_text="Emission nebula in Cygnus",
               description_credit="Credit Line",
               description_source_link="https://example.org/desc",
               size="120 x 100 arcmin", type="Emission Nebula",
               constellation="Cygnus")
    db_session.commit()

    (row,) = [r for r in _rows(client) if r["object_name"] == "NGC 7000"]
    assert set(row) == _FIELDS
    assert row == {
        "id": obj.id,
        "object_name": "NGC 7000",
        "common_name": "North America Nebula",
        "ra_deg": pytest.approx(20.98 * 15.0),
        "dec_deg": 44.33,
        "image_url": "/uploads/default/ngc7000.jpg",
        "image_credit": "Jane Doe",
        "image_source_link": "https://example.org/ngc7000",
        "description_text": "Emission nebula in Cygnus",
        "description_credit": "Credit Line",
        "description_source_link": "https://example.org/desc",
        "Size": "120 x 100 arcmin",
        "Type": "Emission Nebula",
        "Constellation": "Cygnus",
        "Object": "NGC 7000",
        "Common Name": "North America Nebula",
    }


def test_rows_keep_the_graph_page_order(client, db_session):
    """The endpoint is the same query graph_dashboard() ran inline."""
    user = _default_user(db_session)
    for name in ("M31", "M33", "M51"):
        _obj(db_session, user.id, name)
    db_session.commit()

    direct = [o.id for o in db_session.query(AstroObject)
              .filter_by(user_id=user.id)
              .filter(AstroObject.ra_hours.isnot(None), AstroObject.dec_deg.isnot(None))
              .all()]
    assert [row["id"] for row in _rows(client)] == direct


def test_another_users_objects_are_never_returned(multi_user_client, db_session):
    client, ids = multi_user_client
    _obj(db_session, ids["user_a_id"], "M42", ra_hours=5.58, dec_deg=-5.4)
    _obj(db_session, ids["user_b_id"], "M31", ra_hours=0.71, dec_deg=41.27)
    db_session.commit()

    names = _names(_rows(client))
    assert "M42" in names
    assert "M31" not in names


def test_guest_is_allowed_and_scoped_to_the_guest_user(mu_client_logged_out, db_session):
    """Guests reach the endpoint (200, not 401) and get the guest_user's own
    library - which the provisioner seeds - never another user's objects."""
    user_a = db_session.query(DbUser).filter_by(username="UserA").one()
    _obj(db_session, user_a.id, "ZZ_USER_A_ONLY", ra_hours=0.71, dec_deg=41.27)
    db_session.commit()

    with mu_client_logged_out.session_transaction() as sess:
        sess["guest_mode"] = True

    resp = mu_client_logged_out.get("/api/framing_objects")
    assert resp.status_code == 200
    rows = resp.get_json()["objects"]
    assert isinstance(rows, list)
    assert "ZZ_USER_A_ONLY" not in _names(rows)


def test_graph_page_no_longer_inlines_the_catalog(client, db_session):
    """The list moved to the endpoint - gone from NOVA_GRAPH_DATA, not dropped:
    the endpoint still serves the same object."""
    user = _default_user(db_session)
    _obj(db_session, user.id, "NGC 7000", ra_hours=20.98, dec_deg=44.33,
         description_text="ZZ_CATALOG_MARKER")
    db_session.commit()

    resp = client.get("/graph_dashboard/M42")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "ZZ_CATALOG_MARKER" not in html
    assert "allObjects:" not in html          # the NOVA_GRAPH_DATA key
    assert any(r["description_text"] == "ZZ_CATALOG_MARKER" for r in _rows(client))


def test_graph_js_reads_the_endpoint_and_not_graph_data():
    """The page-side half: the catalog is fetched, and the inline read the
    builder used to do (window.NOVA_GRAPH_DATA.allObjects) has no reader left."""
    path = os.path.join(os.path.dirname(__file__), '..', 'static', 'js',
                        'graph_view_chart.js')
    with open(path, encoding='utf-8') as fh:
        src = fh.read()

    assert "'/api/framing_objects'" in src
    assert 'graphData.allObjects' not in src
