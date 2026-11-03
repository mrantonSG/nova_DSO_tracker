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

from nova.models import Component, DbUser

# Dependency order: nova_dashboard_rows.js needs astro and transit; nova_planets.js
# needs astro; nova_plot_data.js astro and transit; nova_solar_rows.js astro, planets
# and dashboard_rows; nova_solar_calendar.js those and solar_rows; nova_solar_rigfit.js
# nothing; the chart plot_data and planets; the page all of them.
PAGE_SCRIPTS = [
    "nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js", "nova_planets.js",
    "nova_plot_data.js", "nova_solar_rows.js", "nova_solar_calendar.js",
    "nova_moon.js", "nova_moon_features.js", "nova_jupiter.js", "nova_mars.js",
    "nova_mars_features.js", "nova_solar_moon.js", "nova_solar_jupiter.js",
    "nova_solar_mars.js", "nova_solar_inner.js", "nova_solar_rigfit.js",
    "solar_system_chart.js", "solar_system.js",
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



def test_html_lang_is_the_locale_the_page_is_rendered_in(multi_user_client):
    """A signed-in user with no saved language and a German browser: Flask-Babel renders the
    page in German, and <html lang> -- which every date on the cards is written in -- says
    so too (it once fell back to 'en' and the dates came out in English)."""
    client, _ = multi_user_client
    html = client.get("/solar_system", headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}).get_data(as_text=True)
    assert '<html lang="de"' in html
    assert _header_labels(html, "solar-cal-table")["date"] == "Datum"
    english = client.get("/solar_system", headers={"Accept-Language": "en-GB"}).get_data(as_text=True)
    assert '<html lang="en"' in english


# ------------------------------------------------------------------ Planetary calculator

def _add_component(db_session, username, kind, name, **fields):
    """A component of `username`, with the record_uid the model gives it."""
    user = db_session.query(DbUser).filter_by(username=username).one()
    comp = Component(user_id=user.id, kind=kind, name=name, **fields)
    db_session.add(comp)
    db_session.commit()
    return comp


def _solar_equipment(html):
    """The page's embedded equipment (tojson escapes <, > and & as \\u escapes, which JSON reads)."""
    return json.loads(re.search(r"const solarEquipment = (.*);\n", html).group(1))


def test_page_embeds_telescopes_and_cameras_with_raw_fields(client, db_session):
    """The user's telescopes and cameras, each list by name (case-insensitive), each with
    its record_uid and raw fields; no reducers/extenders; no component without a
    record_uid (the page could not remember it). A name reaches the page as JSON text,
    never as markup."""
    c11 = _add_component(db_session, "default", "telescope", "c11 <b>x</b>", aperture_mm=280.0,
                         focal_length_mm=2800.0)
    apo = _add_component(db_session, "default", "telescope", "Apo", aperture_mm=None,
                         focal_length_mm=550.0)
    asi = _add_component(db_session, "default", "camera", "ASI462", pixel_size_um=2.9,
                         sensor_width_mm=5.6, sensor_height_mm=3.2)
    _add_component(db_session, "default", "reducer_extender", "Barlow 2x", factor=2.0)
    no_uid = _add_component(db_session, "default", "telescope", "No uid", aperture_mm=100.0,
                            focal_length_mm=900.0)
    empty_uid = _add_component(db_session, "default", "camera", "Empty uid", pixel_size_um=3.0)
    # Cleared after the insert, which gives every row a record_uid
    db_session.query(Component).filter_by(id=no_uid.id).update({"record_uid": None})
    db_session.query(Component).filter_by(id=empty_uid.id).update({"record_uid": ""})
    db_session.commit()
    html = client.get("/solar_system").get_data(as_text=True)
    assert "<b>x</b>" not in html
    assert _solar_equipment(html) == {
        "telescopes": [
            {"uid": apo.record_uid, "name": "Apo", "aperture_mm": None, "focal_length_mm": 550.0},
            {"uid": c11.record_uid, "name": "c11 <b>x</b>", "aperture_mm": 280.0, "focal_length_mm": 2800.0},
        ],
        "cameras": [
            {"uid": asi.record_uid, "name": "ASI462", "pixel_size_um": 2.9,
             "sensor_width_mm": 5.6, "sensor_height_mm": 3.2},
        ],
    }


def test_page_embeds_only_the_page_users_components(multi_user_client, db_session):
    client, _ = multi_user_client
    _add_component(db_session, "UserA", "telescope", "A scope", aperture_mm=200.0, focal_length_mm=2000.0)
    _add_component(db_session, "UserB", "telescope", "B scope", aperture_mm=200.0, focal_length_mm=2000.0)
    _add_component(db_session, "UserB", "camera", "B camera", pixel_size_um=2.9)
    eq = _solar_equipment(client.get("/solar_system").get_data(as_text=True))
    assert [t["name"] for t in eq["telescopes"]] == ["A scope"]
    assert eq["cameras"] == []


def test_guest_gets_the_guest_components(mu_client_logged_out, db_session):
    """The guest's page carries guest_user's telescopes and cameras (the first visit also
    seeds the demo ones), never a component that has been deleted."""
    _add_component(db_session, "guest_user", "telescope", "Guest scope", aperture_mm=150.0,
                   focal_length_mm=1500.0)
    gone = _add_component(db_session, "guest_user", "camera", "Gone camera", pixel_size_um=2.0)
    db_session.delete(gone)
    db_session.commit()
    mu_client_logged_out.get("/guest")
    eq = _solar_equipment(mu_client_logged_out.get("/solar_system").get_data(as_text=True))
    guest = db_session.query(DbUser).filter_by(username="guest_user").one()
    rows = db_session.query(Component).filter_by(user_id=guest.id).all()
    assert "Guest scope" in [t["name"] for t in eq["telescopes"]]
    assert "Gone camera" not in [c["name"] for c in eq["cameras"]]
    assert sorted(t["uid"] for t in eq["telescopes"]) == sorted(
        c.record_uid for c in rows if c.kind == "telescope" and c.record_uid)
    assert sorted(c["uid"] for c in eq["cameras"]) == sorted(
        c.record_uid for c in rows if c.kind == "camera" and c.record_uid)


def test_calculator_has_three_styled_selects(client):
    """Telescope, camera and amplification, each a .styled-select named for screen
    readers; the browser fills the options."""
    html = client.get("/solar_system").get_data(as_text=True)
    controls = re.search(r'<div class="solar-calc-controls" id="solar-calc-controls">(.*?)</div>', html, re.S).group(1)
    selects = re.findall(r'<select id="([a-z-]+)" class="styled-select" aria-label="([^"]+)"></select>', controls)
    assert selects == [("solar-calc-telescope", "Telescope"), ("solar-calc-camera", "Camera"),
                       ("solar-calc-amp", "Amplification")]
    assert re.search(r'<h3 id="solar-calc-title">Planetary calculator</h3>', html)


def test_calculator_layout_puts_the_preview_beside_the_result_on_wide_cards(client):
    """Summary, preview and result in one wrapper, in that order (stacked); from 900 px of
    card width (a container query) the preview sits right beside the text column at its
    natural width, up to 400 px wide, a 72 px gap (3 x --space-6) between them."""
    html = client.get("/solar_system").get_data(as_text=True)
    body = re.search(r'<div class="solar-calc-body">(.*?)</dl>', html, re.S).group(1)
    assert re.findall(r'id="(solar-calc-[a-z]+)"', body) == ["solar-calc-summary", "solar-calc-preview", "solar-calc-result"]
    css = (Path(__file__).resolve().parent.parent / "static" / "css" / "solar_system.css").read_text()
    assert "container: solar-calc / inline-size;" in css
    wide = css[css.index("@container solar-calc (min-width: 900px)"):]
    assert 'grid-template-areas: "summary preview" "result preview";' in wide
    assert "grid-template-columns: minmax(0, max-content) 400px;" in wide
    figure = css[css.index("#bodies-tab .solar-preview-figure {"):]
    assert "aspect-ratio: 3 / 2;" in figure[:figure.index("}")]
    # Text that changes with a select keeps its height: the summary two lines when stacked
    # (under 900 px, natural on a wide card), the focal-ratio hint two (three under 450 px)
    rule = lambda sel: css[css.index(sel + " {"):][:css[css.index(sel + " {"):].index("}")]
    assert "min-height" not in rule("#bodies-tab .solar-calc-summary")
    stacked = css[css.index("@container solar-calc (max-width: 899px)"):]
    stacked = stacked[:stacked.index("}\n}")]
    assert "#bodies-tab .solar-calc-summary {" in stacked and "min-height: 2.9em;" in stacked
    assert "min-height: 2.9em;" not in wide[:wide.index("}\n}")]
    assert "min-height: 2.8em;" in rule("#bodies-tab .solar-calc-hint-fratio")
    narrow = css[css.index("@container solar-calc (max-width: 449px)"):]
    assert "min-height: 4.2em;" in narrow[:narrow.index("}")]
    assert "justify-content: start;" in wide and "column-gap: calc(var(--space-6) * 3);" in wide


def test_empty_calculator_points_to_the_configuration_page(client):
    html = client.get("/solar_system").get_data(as_text=True)
    assert _solar_equipment(html) == {"telescopes": [], "cameras": []}
    empty = re.search(r'<p id="solar-calc-empty"[^>]*>(.*?)</p>', html, re.S).group(1)
    assert "Add a telescope and a camera on the Configuration page." in empty
    assert 'href="/config_form?prefill_config_tab=rigs"' in empty


@pytest.mark.parametrize("lang, calc_title, hide_muted, conjunction, calculating", [
    ("de", "Planetenrechner", "Nicht sichtbare Ereignisse ausblenden", "Konjunktion {sep}°", "Wird berechnet..."),
    ("ja", "惑星計算ツール", "見えないイベントを隠す", "合 {sep}°", "計算中..."),
])
def test_new_strings_are_translated(mu_client_logged_out, lang, calc_title, hide_muted, conjunction, calculating):
    """The planetary calculator's title, the calendar filter, the conjunction label and the
    calendar's "Calculating...", through _(); the placeholder survives for the page to
    fill in."""
    mu_client_logged_out.get("/guest")
    mu_client_logged_out.get(f"/set_language/{lang}")
    html = mu_client_logged_out.get("/solar_system").get_data(as_text=True)
    assert re.search(r'<h3 id="solar-calc-title">' + re.escape(calc_title) + "</h3>", html)
    assert hide_muted in html
    assert json.dumps(conjunction) in html or conjunction in html
    assert re.search(r'<p id="solar-cal-loading"[^>]*>' + re.escape(calculating) + "</p>", html)
