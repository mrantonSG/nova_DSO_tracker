"""The Solar System page: who may open it, what it loads, and the dashboard's way in.

/solar_system follows the dashboard index's access rules exactly (core.py index()):
single-user, a logged-in user and a guest get the page; a logged-out visitor without
guest mode is sent to the login page by the auth gate. The page computes everything in
the browser, so it must load the engines in dependency order before its own scripts.
"""
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from nova.models import Component, DbUser, Rig, UiPref
from nova.record_links import sync_rig_links

# Dependency order: nova_dashboard_rows.js needs astro and transit; nova_planets.js
# needs astro; nova_plot_data.js astro and transit; nova_solar_rows.js astro, planets
# and dashboard_rows; nova_solar_calendar.js those and solar_rows; nova_solar_rigfit.js
# nothing; the chart plot_data and planets; the page all of them.
PAGE_SCRIPTS = [
    "nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js", "nova_planets.js",
    "nova_plot_data.js", "nova_solar_rows.js", "nova_solar_calendar.js",
    "nova_solar_rigfit.js", "solar_system_chart.js", "solar_system.js",
]


def _scripts(html):
    return re.findall(r'<script[^>]*\ssrc="[^"]*/js/([^"/?]+\.js)\?v=[^"]*"[^>]*>', html)


def test_single_user_gets_the_page(client):
    response = client.get("/solar_system")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="solar-table"' in html
    assert 'id="solarChartCanvas"' in html


def test_logged_in_multi_user_gets_the_page(multi_user_client):
    client, _ = multi_user_client
    assert client.get("/solar_system").status_code == 200


def test_guest_gets_the_page(mu_client_logged_out):
    mu_client_logged_out.get("/guest")
    assert mu_client_logged_out.get("/solar_system").status_code == 200


def test_logged_out_is_sent_to_login(mu_client_logged_out):
    response = mu_client_logged_out.get("/solar_system")
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == "/login"
    assert parse_qs(location.query)["next"] == ["/solar_system"]


def test_page_loads_the_engines_before_its_own_scripts(client):
    """All deferred, so they run in document order: each finds its globals."""
    scripts = _scripts(client.get("/solar_system").get_data(as_text=True))
    assert [name for name in scripts if name in PAGE_SCRIPTS] == PAGE_SCRIPTS
    assert "dashboard.js" not in scripts
    assert "graph_view_chart.js" not in scripts


def test_dashboard_header_links_to_the_page_before_about(client):
    """A real link (base.js only follows data-nav-url on the click target itself),
    after Configuration and immediately before the About button."""
    html = client.get("/").get_data(as_text=True)
    link = re.search(r'<a id="solar-system-link"[^>]*>', html)
    assert link, "the dashboard has no Solar System link"
    assert 'href="/solar_system"' in link.group(0)
    assert 'class="about-trigger-button"' in link.group(0)
    assert 'aria-label="Solar System"' in link.group(0)
    config_at = html.index("Configuration</button>")
    link_at = link.start()
    about_at = html.rindex("<button", 0, html.index('id="about-trigger"'))
    assert config_at < link_at < about_at
    between = html[link_at:about_at]
    assert between.count("<button") == 0 and between.count("<a ") == 1


def test_solar_table_does_not_use_the_dashboard_column_keys(client):
    """dashboard.css and nova-theme.css size, align and set fonts on table cells by
    data-column-key, so a solar column named like a dashboard one picked up the wrong
    width and font. The solar table keys its columns with data-solar-col, styled by
    solar_system.css, which loads after the two stylesheets it overrides."""
    html = client.get("/solar_system").get_data(as_text=True)
    table = html[html.index('<table id="solar-table"'):html.index("</table>", html.index('<table id="solar-table"'))]
    assert "data-column-key" not in table
    assert table.count("data-solar-col=") == 11
    sheets = re.findall(r'<link rel="stylesheet" href="[^"]*/css/([^"/?]+\.css)', html)
    assert sheets.index("solar_system.css") > sheets.index("dashboard.css")
    assert sheets.index("solar_system.css") > sheets.index("nova-theme.css")
    page_js = (Path(__file__).resolve().parent.parent / "static" / "js" / "solar_system.js").read_text()
    assert "columnKey" not in page_js and "solarCol" in page_js


def test_year_input_is_bounded_like_the_month_series(client):
    """The month/year chart's year input stays inside nightlyMax()'s 1900-2100."""
    html = client.get("/solar_system").get_data(as_text=True)
    year = re.search(r'<input[^>]*id="solar-year-select"[^>]*>', html).group(0)
    assert 'min="1900"' in year and 'max="2100"' in year


def test_transit_header_has_no_degree_subtext(client):
    """The transit cell's main line is a time, so its header carries no unit."""
    html = client.get("/solar_system").get_data(as_text=True)
    th = re.search(r'<th data-solar-col="transit"[^>]*>.*?</th>', html).group(0)
    assert "(°)" not in th


def test_calendar_tab_has_its_own_table(client):
    """A second tab, Calendar, with its own table: eight data-solar-col columns, no
    dashboard column keys, and its own body and error."""
    html = client.get("/solar_system").get_data(as_text=True)
    tabs = re.findall(r'<button class="tab-button[^"]*" data-tab="([a-z]+)"', html)
    assert tabs == ["bodies", "calendar"]
    assert 'id="calendar-tab" class="tab-content"' in html
    start = html.index('<table id="solar-cal-table"')
    table = html[start:html.index("</table>", start)]
    assert "data-column-key" not in table
    assert re.findall(r'<th data-solar-col="([a-z]+)"[^>]*>', table) == [
        "date", "body", "event", "mag", "diam", "elong", "maxalt", "days"]
    assert 'id="solar-cal-body"' in table and 'id="solar-cal-error"' in html


def test_tabs_are_an_aria_tablist(client):
    """role=tablist / tab / tabpanel, each tab naming its panel and each panel its tab,
    so the aria-selected solar_system.js sets is valid."""
    html = client.get("/solar_system").get_data(as_text=True)
    assert '<div class="tab-container" role="tablist">' in html
    for tab, selected in (("bodies", "true"), ("calendar", "false")):
        button = re.search(rf'<button [^>]*data-tab="{tab}"[^>]*>', html).group(0)
        assert 'role="tab"' in button and f'id="{tab}-tab-button"' in button
        assert f'aria-controls="{tab}-tab"' in button and f'aria-selected="{selected}"' in button
        panel = re.search(rf'<div id="{tab}-tab" [^>]*>', html).group(0)
        assert 'role="tabpanel"' in panel and f'aria-labelledby="{tab}-tab-button"' in panel


def test_each_tab_has_its_own_error(client):
    """The page error sits in the Bodies tab and the calendar's in the Calendar tab, so
    whichever tab is shown can say the inputs did not load (solar_system.js shows both);
    the text is the translated inputsError."""
    html = client.get("/solar_system").get_data(as_text=True)
    for tab, error, other in (("bodies", "solar-error", "calendar"), ("calendar", "solar-cal-error", "bodies")):
        start = html.index(f'<div id="{tab}-tab"')
        end = html.index(f'<div id="{other}-tab"') if tab == "bodies" else html.index("<script>", start)
        assert f'<p id="{error}" class="status-secondary" role="alert" hidden></p>' in html[start:end]
    assert re.search(r'inputsError:\s*"[^"]+"', html)


def test_solar_tables_are_styled_by_id(client):
    """solar_system.css names each table by id, never by a shared class, so the Bodies
    table's rules keep the weight that beats dashboard.css and nova-theme.css; every
    rule for the Bodies table that the calendar shares lists both ids."""
    css = (Path(__file__).resolve().parent.parent / "static" / "css" / "solar_system.css").read_text()
    selectors = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert ".solar-table" not in selectors
    for sel in re.findall(r"([^{}]+)\{", selectors):
        if "#solar-cal-table" in sel and "#solar-table" in sel:
            parts = [p.strip() for p in sel.split(",")]
            bodies = [p for p in parts if p.startswith("#solar-table")]
            calendar = [p for p in parts if p.startswith("#solar-cal-table")]
            assert [p.replace("#solar-table", "#solar-cal-table", 1) for p in bodies] == calendar


def _header_titles(html, table_id):
    start = html.index(f'<table id="{table_id}"')
    table = html[start:html.index("</table>", start)]
    return {col: unescape(title)
            for col, title in re.findall(r'<th data-solar-col="([a-z]+)" title="([^"]*)">', table)}


def _header_labels(html, table_id):
    start = html.index(f'<table id="{table_id}"')
    table = html[start:html.index("</table>", start)]
    return {col: unescape(label)
            for col, label in re.findall(r'<th data-solar-col="([a-z]+)"[^>]*><span>([^<]*)</span>', table)}


def test_headers_explain_themselves(client):
    """The browser's own tooltip (a title attribute, as Nova does elsewhere) on the
    headers that need words; Diameter, Elongation and the nightly maximum read the same
    in both tables. The calendar's cell tooltips come from solarI18n."""
    html = client.get("/solar_system").get_data(as_text=True)
    bodies = _header_titles(html, "solar-table")
    calendar = _header_titles(html, "solar-cal-table")
    assert set(bodies) == {"rise", "transit", "set", "maxalt", "diam", "illum", "elong"}
    assert set(calendar) == {"date", "diam", "elong", "maxalt", "days"}
    assert bodies["rise"] == bodies["set"]
    for col in ("diam", "elong", "maxalt"):
        assert bodies[col] == calendar[col]
    assert calendar["date"] == "The evening on which the observing night starts."
    assert re.search(r'exactTime:\s+"Exact time: \{time\}"', html)
    for kind in ("opposition", "elongationEast", "elongationWest"):
        assert re.search(kind + r':\s+"[^"]+"', html)


@pytest.mark.parametrize("lang, date_label, date_help, exact", [
    ("de", "Datum", "Der Abend, an dem die Beobachtungsnacht beginnt.", "Genaue Zeit: {time}"),
    ("ja", "日付", "観測夜が始まる日の夕方。", "正確な時刻：{time}"),
    ("fr", "Date", "Le soir où commence la nuit d'observation.", "Heure exacte : {time}"),
])
def test_tooltips_are_translated(mu_client_logged_out, lang, date_label, date_help, exact):
    """Through _() and the compiled catalogs, like every other string on the page; the
    placeholder survives for the page to fill in. The Date header too (fr once read
    "Données")."""
    mu_client_logged_out.get("/guest")
    mu_client_logged_out.get(f"/set_language/{lang}")
    html = mu_client_logged_out.get("/solar_system").get_data(as_text=True)
    assert f'<html lang="{lang}"' in html
    assert _header_labels(html, "solar-cal-table")["date"] == date_label
    assert _header_titles(html, "solar-cal-table")["date"] == date_help
    assert json.dumps(exact) in html or exact in html


# ------------------------------------------------------------------ Rig fit card

def _add_rig(db_session, username, name, active=True, telescope=None, camera=None, reducer=None):
    """A rig of `username` with its own components (dicts of Component fields, or None
    for no such component), record_uid links synced."""
    user = db_session.query(DbUser).filter_by(username=username).one()
    ids = {}
    for kind, fields in (("telescope", telescope), ("camera", camera), ("reducer_extender", reducer)):
        if fields is None:
            continue
        comp = Component(user_id=user.id, kind=kind, name=f"{name} {kind}", **fields)
        db_session.add(comp)
        db_session.flush()
        ids[kind] = comp.id
    rig = Rig(user_id=user.id, rig_name=name, active=active, telescope_id=ids.get("telescope"),
              camera_id=ids.get("camera"), reducer_extender_id=ids.get("reducer_extender"))
    sync_rig_links(db_session, rig)
    db_session.add(rig)
    db_session.commit()
    return rig


def _solar_rigs(html):
    """The page's embedded rigs (tojson escapes <, > and & as \\u escapes, which JSON reads)."""
    return json.loads(re.search(r"const solarRigs = (.*);\n", html).group(1))


C11 = {"aperture_mm": 280.0, "focal_length_mm": 2800.0}
ASI462 = {"pixel_size_um": 2.9, "sensor_width_mm": 5.6, "sensor_height_mm": 3.2}


def test_page_embeds_the_active_rigs_with_raw_component_fields(client, db_session):
    """Only active rigs, in the user's rig order (by name), each with its components'
    raw fields: what nova_solar_rigfit.js needs to work out each value and name the
    field a value lacks. A rig name reaches the page as JSON text, never as markup."""
    _add_rig(db_session, "default", "C11 <b>x</b>", telescope=C11, camera=ASI462,
             reducer={"factor": 2.0})
    _add_rig(db_session, "default", "Apo", telescope={"aperture_mm": None, "focal_length_mm": 550.0},
             camera=None)
    _add_rig(db_session, "default", "Parked", active=False, telescope=C11, camera=ASI462)
    html = client.get("/solar_system").get_data(as_text=True)
    assert "<b>x</b>" not in html
    assert _solar_rigs(html) == [
        {"name": "Apo", "has_telescope": True, "has_camera": False,
         "focal_length_mm": 550.0, "aperture_mm": None, "factor": None,
         "pixel_size_um": None, "sensor_width_mm": None, "sensor_height_mm": None},
        {"name": "C11 <b>x</b>", "has_telescope": True, "has_camera": True,
         "focal_length_mm": 2800.0, "aperture_mm": 280.0, "factor": 2.0,
         "pixel_size_um": 2.9, "sensor_width_mm": 5.6, "sensor_height_mm": 3.2},
    ]


def test_page_embeds_only_the_page_users_rigs(multi_user_client, db_session):
    client, _ = multi_user_client
    _add_rig(db_session, "UserA", "A rig", telescope=C11, camera=ASI462)
    _add_rig(db_session, "UserB", "B rig", telescope=C11, camera=ASI462)
    names = [r["name"] for r in _solar_rigs(client.get("/solar_system").get_data(as_text=True))]
    assert names == ["A rig"]


def test_guest_gets_the_guest_rigs(mu_client_logged_out, db_session):
    """/get_rig_data is login_required, so the page carries the rigs itself: the active
    rigs of guest_user (which the first visit also seeds with the demo rigs)."""
    _add_rig(db_session, "guest_user", "Guest rig", telescope=C11, camera=ASI462)
    _add_rig(db_session, "guest_user", "Guest parked", active=False, telescope=C11, camera=ASI462)
    mu_client_logged_out.get("/guest")
    html = mu_client_logged_out.get("/solar_system").get_data(as_text=True)
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    active = db_session.query(Rig).filter_by(user_id=guest.id, active=True).all()
    names = [r["name"] for r in _solar_rigs(html)]
    assert "Guest rig" in names and "Guest parked" not in names
    assert sorted(names) == sorted(r.rig_name for r in active)


@pytest.mark.parametrize("rig_sort", [5, ["name-desc"], {"x": 1}, True])
def test_unreadable_rig_sort_falls_back_to_the_default_order(client, db_session, rig_sort):
    """A stored rig_sort sort_rigs cannot read (not a string) gives the default order,
    by name, instead of a 500."""
    _add_rig(db_session, "default", "Zeta", telescope=C11, camera=ASI462)
    _add_rig(db_session, "default", "Alpha", telescope=C11, camera=ASI462)
    user = db_session.query(DbUser).filter_by(username="default").one()
    db_session.query(UiPref).filter_by(user_id=user.id).delete()
    db_session.add(UiPref(user_id=user.id, json_blob=json.dumps({"rig_sort": rig_sort})))
    db_session.commit()
    resp = client.get("/solar_system")
    assert resp.status_code == 200
    assert [r["name"] for r in _solar_rigs(resp.get_data(as_text=True))] == ["Alpha", "Zeta"]


def test_readable_rig_sort_is_kept(client, db_session):
    _add_rig(db_session, "default", "Alpha", telescope=C11, camera=ASI462)
    _add_rig(db_session, "default", "Zeta", telescope=C11, camera=ASI462)
    user = db_session.query(DbUser).filter_by(username="default").one()
    db_session.query(UiPref).filter_by(user_id=user.id).delete()
    db_session.add(UiPref(user_id=user.id, json_blob=json.dumps({"rig_sort": "name-desc"})))
    db_session.commit()
    names = [r["name"] for r in _solar_rigs(client.get("/solar_system").get_data(as_text=True))]
    assert names == ["Zeta", "Alpha"]


def test_no_active_rig_points_to_the_configuration_page(client, db_session):
    _add_rig(db_session, "default", "Parked", active=False, telescope=C11, camera=ASI462)
    html = client.get("/solar_system").get_data(as_text=True)
    assert _solar_rigs(html) == []
    empty = re.search(r'<p id="solar-rigfit-empty"[^>]*>(.*?)</p>', html, re.S).group(1)
    assert "No active rig. Add or activate one on the Configuration page." in empty
    assert 'href="/config_form?prefill_config_tab=rigs"' in empty


@pytest.mark.parametrize("lang, rig_fit, hide_muted, conjunction, calculating", [
    ("de", "Rig-Eignung", "Nicht sichtbare Ereignisse ausblenden", "Konjunktion {sep}°", "Wird berechnet..."),
    ("ja", "機材との相性", "見えないイベントを隠す", "合 {sep}°", "計算中..."),
])
def test_new_strings_are_translated(mu_client_logged_out, lang, rig_fit, hide_muted, conjunction, calculating):
    """The Rig fit card, the calendar filter, the conjunction label and the calendar's
    "Calculating...", through _(); the placeholder survives for the page to fill in."""
    mu_client_logged_out.get("/guest")
    mu_client_logged_out.get(f"/set_language/{lang}")
    html = mu_client_logged_out.get("/solar_system").get_data(as_text=True)
    assert re.search(r'<h3 id="solar-rigfit-title">' + re.escape(rig_fit) + "</h3>", html)
    assert hide_muted in html
    assert json.dumps(conjunction) in html or conjunction in html
    assert re.search(r'<p id="solar-cal-loading"[^>]*>' + re.escape(calculating) + "</p>", html)
