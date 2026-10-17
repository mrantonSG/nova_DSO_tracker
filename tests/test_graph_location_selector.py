"""The graph page's location selector (markup only).

The header carries the dashboard's #location-select: the user's own active
locations, the page's location selected. An inactive location is listed only when
it is the page's location (an old ?location= link), then disabled and selected.
"""

from html.parser import HTMLParser

from nova.models import AstroObject, DbUser, Location

LOC_NAME = "Default Test Loc"  # created by the conftest client fixture (is_default=True)


class _SelectParser(HTMLParser):
    """Collects the <option>s of <select id="location-select">."""

    def __init__(self):
        super().__init__()
        self.found = False
        self.options = []   # dicts: value, label, selected, disabled
        self._in_select = False
        self._option = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select" and attrs.get("id") == "location-select":
            self.found = self._in_select = True
        elif tag == "option" and self._in_select:
            self._option = {"value": attrs.get("value"), "label": "",
                            "selected": "selected" in attrs, "disabled": "disabled" in attrs}

    def handle_data(self, data):
        if self._option is not None:
            self._option["label"] += data

    def handle_endtag(self, tag):
        if tag == "option" and self._option is not None:
            self._option["label"] = self._option["label"].strip()
            self.options.append(self._option)
            self._option = None
        elif tag == "select" and self._in_select:
            self._in_select = False


def _selector(client, url):
    response = client.get(url)
    assert response.status_code == 200
    parser = _SelectParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.found, "no #location-select on the page"
    return parser.options


def _selected(options):
    return [o["value"] for o in options if o["selected"]]


def _add_locations(db_session, username="default"):
    user = db_session.query(DbUser).filter_by(username=username).one()
    db_session.add(Location(user_id=user.id, name="Second Site", lat=10, lon=20, timezone="UTC"))
    db_session.add(Location(user_id=user.id, name="Retired Site", lat=30, lon=40,
                            timezone="UTC", active=False))
    db_session.commit()


def test_selector_lists_own_active_locations_with_the_current_one_selected(client, db_session):
    _add_locations(db_session)

    options = _selector(client, '/graph_dashboard/M42')

    assert [o["value"] for o in options] == [LOC_NAME, "Second Site"]
    assert _selected(options) == [LOC_NAME]
    assert not any(o["disabled"] for o in options)

    # ?location= picks another active one: that one is selected instead.
    assert _selected(_selector(client, '/graph_dashboard/M42?location=Second Site')) == ["Second Site"]


def test_inactive_location_listed_only_as_the_pages_location(client, db_session):
    _add_locations(db_session)

    options = _selector(client, '/graph_dashboard/M42?location=Retired Site')

    assert [o["value"] for o in options] == [LOC_NAME, "Retired Site", "Second Site"]
    retired = next(o for o in options if o["value"] == "Retired Site")
    assert retired == {"value": "Retired Site", "label": "Retired Site (Inactive)",
                       "selected": True, "disabled": True}
    assert _selected(options) == ["Retired Site"]


def test_selector_never_shows_another_users_locations(multi_user_client, db_session):
    client, ids = multi_user_client
    db_session.add(AstroObject(user_id=ids["user_a_id"], object_name="M42",
                               ra_hours=5.58, dec_deg=-5.4))
    db_session.add(Location(user_id=ids["user_b_id"], name="B Secret Site",
                            lat=1, lon=2, timezone="UTC", is_default=True))
    db_session.commit()

    options = _selector(client, '/graph_dashboard/M42?location=B Secret Site')

    assert [o["value"] for o in options] == ["UserA_Home"]
    assert _selected(options) == ["UserA_Home"]


def test_guest_sees_the_selector(mu_client_logged_out, db_session):
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    db_session.add(Location(user_id=guest.id, name="Guest Site", lat=50, lon=10,
                            timezone="UTC", is_default=True))
    db_session.add(AstroObject(user_id=guest.id, object_name="Guest M31",
                               ra_hours=0.71, dec_deg=41.3))
    db_session.commit()
    with mu_client_logged_out.session_transaction() as sess:
        sess['guest_mode'] = True

    options = _selector(mu_client_logged_out, '/graph_dashboard/Guest M31')

    assert [o["value"] for o in options] == ["Guest Site"]
    assert _selected(options) == ["Guest Site"]


def test_header_has_no_location_display_for_the_journal_ajax_write(client):
    """journal_section.js sets #location-display's textContent on an AJAX session
    switch; on the select that would wipe the options, so the id must not exist."""
    page = client.get('/graph_dashboard/M42').get_data(as_text=True)
    assert 'id="location-display"' not in page
