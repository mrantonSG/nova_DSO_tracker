"""The desktop header's navigation icons.

base.html renders a nav icon group (Dashboard, Solar System, Configuration) and
an action group (Info, Theme) on every page. The current page's nav icon shows
the active state (`.is-active` + `aria-current="page"`) instead of disappearing;
the graph view and every other page have none active. The Configuration icon is
hidden from guests in multi-user mode (Dashboard and Solar System stay).
"""
import re


def _nav_group(html):
    """The first .nav-icon-group (the nav icons), up to its closing </div>."""
    start = html.index('<div class="nav-icon-group">')
    end = html.index('</div>', start)
    return html[start:end]


def _nav_states(html):
    """Map each nav anchor's aria-label to {is_active, aria_current}."""
    states = {}
    for anchor in re.findall(r'<a\b[^>]*>', _nav_group(html)):
        classes = re.search(r'class="([^"]*)"', anchor).group(1).split()
        label = re.search(r'aria-label="([^"]*)"', anchor).group(1)
        states[label] = {
            "is_active": "is-active" in classes,
            "aria_current": 'aria-current="page"' in anchor,
        }
    return states


def test_dashboard_nav_icons_active(su_client_logged_in):
    html = su_client_logged_in.get("/").get_data(as_text=True)
    states = _nav_states(html)
    assert set(states) == {"Dashboard", "Solar System", "Configuration"}
    assert states["Dashboard"] == {"is_active": True, "aria_current": True}
    assert states["Solar System"] == {"is_active": False, "aria_current": False}
    assert states["Configuration"] == {"is_active": False, "aria_current": False}


def test_solar_system_nav_icons_active(su_client_logged_in):
    html = su_client_logged_in.get("/solar_system").get_data(as_text=True)
    states = _nav_states(html)
    assert set(states) == {"Dashboard", "Solar System", "Configuration"}
    assert states["Solar System"] == {"is_active": True, "aria_current": True}
    assert states["Dashboard"] == {"is_active": False, "aria_current": False}
    assert states["Configuration"] == {"is_active": False, "aria_current": False}


def test_config_nav_icons_active(su_client_logged_in):
    html = su_client_logged_in.get("/config_form").get_data(as_text=True)
    states = _nav_states(html)
    assert set(states) == {"Dashboard", "Solar System", "Configuration"}
    assert states["Configuration"] == {"is_active": True, "aria_current": True}
    assert states["Dashboard"] == {"is_active": False, "aria_current": False}
    assert states["Solar System"] == {"is_active": False, "aria_current": False}


def test_graph_view_has_no_nav_icon_active(su_client_logged_in):
    html = su_client_logged_in.get("/graph_dashboard/M42").get_data(as_text=True)
    states = _nav_states(html)
    assert set(states) == {"Dashboard", "Solar System", "Configuration"}
    for state in states.values():
        assert state == {"is_active": False, "aria_current": False}


def test_configuration_text_button_is_gone(su_client_logged_in):
    """The header's text 'Configuration' button was replaced by the icon."""
    html = su_client_logged_in.get("/").get_data(as_text=True)
    assert "Configuration</button>" not in html


def test_guest_sees_no_configuration_icon(mu_client_logged_out):
    mu_client_logged_out.get("/guest")
    html = mu_client_logged_out.get("/").get_data(as_text=True)
    states = _nav_states(html)
    assert set(states) == {"Dashboard", "Solar System"}
    assert "Configuration" not in states
    assert states["Dashboard"] == {"is_active": True, "aria_current": True}
    assert states["Solar System"] == {"is_active": False, "aria_current": False}
