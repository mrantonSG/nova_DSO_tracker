"""The Solar System page: who may open it, what it loads, and the dashboard's way in.

/solar_system follows the dashboard index's access rules exactly (core.py index()):
single-user, a logged-in user and a guest get the page; a logged-out visitor without
guest mode is sent to the login page by the auth gate. The page computes everything in
the browser, so it must load the engines in dependency order before its own scripts.
"""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# Dependency order: nova_dashboard_rows.js needs astro and transit; nova_planets.js
# needs astro; nova_plot_data.js astro and transit; nova_solar_rows.js astro, planets
# and dashboard_rows; the chart plot_data and planets; the page all of them.
PAGE_SCRIPTS = [
    "nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js", "nova_planets.js",
    "nova_plot_data.js", "nova_solar_rows.js", "solar_system_chart.js", "solar_system.js",
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
    th = re.search(r'<th data-solar-col="transit">.*?</th>', html).group(0)
    assert "(°)" not in th
