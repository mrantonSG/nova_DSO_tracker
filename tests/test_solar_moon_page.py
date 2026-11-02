"""The Solar System page's Moon card and lunar Calendar rows, in node.

Runs static/js/solar_system.js with the real engines and the stand-in DOM of
tests/test_solar_system_js.py (its driver, unchanged), with nova_moon.js,
nova_moon_features.js and nova_solar_moon.js loaded too, as the page loads them. The
template checks at the end render the page with the Flask client.
"""

import json
import re
import subprocess

import pytest

from test_solar_system_js import _DRIVER, LONDON, NODE, PAGE_ORDER, STATIC_JS

MOON_ORDER = PAGE_ORDER + ("nova_moon.js", "nova_moon_features.js", "nova_solar_moon.js")

# 2 Nov 2026 20:00 UTC
NOW_MS = 1793649600000

I18N = {
    "error": "CALC ERROR", "inputsError": "INPUTS ERROR", "chartError": "CHART ERROR",
    "belowThreshold": "BELOW", "lunarX": "LUNAR X", "lunarNotVisible": "LUNAR HIDDEN",
    "bodies": {"moon": "Moon"},
    "eventHelp": {"lunarX": "X HELP", "libration": "LIB HELP"},
    "moon": {
        "title": "MOON", "error": "MOON ERROR", "highestAt": "at {time}",
        "colongitude": "COLONG", "libration": "LIB", "librationHelp": "LIB TIP", "illuminated": "ILLUM",
        "morning": "AM", "evening": "PM", "sunAlt": "sun {alt}", "position": "{lat}/{lon}",
        "nearLimb": "LIMB", "nextNights": "next {dates}", "noNextNights": "NO NEXT",
        "level": {"excellent": "EXC {side} {deg}", "favourable": "FAV {side} {deg}",
                  "moderate": "MOD {side} {deg}", "none": "NONE {deg}", "dark": "DARK {side} {deg}"},
        "empty": {"new": "NEW", "full": "FULL", "none": "NOTHING", "notUp": "NOTUP"},
        "north": "N", "types": {"crater": "Crater"}, "credit": "SRC {source}",
    },
}

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# The Moon card's view buttons, which the driver's querySelectorAll does not know
_VIEWS = r"""
const viewButtons = ['normal', 'diagonal', 'inverted'].map(v => {
    const b = h.$('moon-view-' + v); b.dataset.moonView = v; return b; });
const qsa = h.context.document.querySelectorAll;
h.context.document.querySelectorAll = sel => (sel.indexOf('solar-moon-view') >= 0 ? viewButtons : qsa(sel));
const pressed = () => viewButtons.filter(b => b.attrs['aria-pressed'] === 'true').map(b => b.dataset.moonView);
const markers = () => {
    const svg = h.$('solar-moon-diagram').children[0];
    return svg ? svg.children.filter(c => c.attrs.class === 'solar-moon-marker')
        .map(g => ({ n: g.attrs['data-n'], x: +g.children[0].attrs.cx, y: +g.children[0].attrs.cy })) : [];
};
const card = () => ({
    hidden: h.$('solar-moon').hidden, title: h.$('solar-moon-title').textContent,
    error: h.$('solar-moon-error').hidden ? null : h.$('solar-moon-error').textContent,
    bodyHidden: h.$('solar-moon-body').hidden,
    tiles: h.$('solar-moon-tiles').children.length,
    features: h.$('solar-moon-features').children.map(li => ({ n: li.dataset.n, title: li.title,
        name: li.children.filter(c => c.className === 'solar-moon-name').map(c => c.textContent)[0] })),
    more: h.$('solar-moon-more-list').children.length, moreHidden: h.$('solar-moon-more').hidden,
    empty: h.$('solar-moon-empty').hidden ? null : h.$('solar-moon-empty').textContent,
    libration: h.$('solar-moon-libration').textContent, markers: markers(), pressed: pressed(),
});
"""


def _run(tmp_path, steps, search="?body=moon", sim=None, storage=None, order=MOON_ORDER):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "steps.js"
    case.write_text(_VIEWS + steps)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"inputs": {"location": LONDON}, "i18n": I18N,
                               "equipment": {"telescopes": [], "cameras": []},
                               "search": search, "sim": sim, "storage": storage, "nowMs": NOW_MS}))
    out = tmp_path / "out.json"
    run = subprocess.run(
        [NODE, str(driver), str(case), str(inp), str(out), str(STATIC_JS / "solar_system.js")]
        + [str(STATIC_JS / name) for name in order],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def test_moon_card_only_while_the_moon_is_selected(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { jupiter: card() };
        const rows = h.$('solar-body').children;
        await h.clickRow('solar-body', rows.findIndex(tr => tr.dataset.body === 'moon'));
        out.moon = card();
        return out;
    """, search="?body=jupiter", sim="2026-11-17")
    assert got["jupiter"]["hidden"]
    assert not got["moon"]["hidden"] and got["moon"]["error"] is None


def test_moon_card_content_at_first_quarter(tmp_path):
    """17 Nov 2026: the title names tonight's highest moment, three tiles, up to eight
    numbered features with a marker each on the drawing, the libration line."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        return card();
    """, sim="2026-11-17")
    assert re.fullmatch(r"MOON · at \d\d:\d\d", got["title"])
    assert got["tiles"] == 3 and got["empty"] is None
    feats = got["features"]
    assert 1 <= len(feats) <= 8 and [f["n"] for f in feats] == [str(i + 1) for i in range(len(feats))]
    assert sorted(m["n"] for m in got["markers"]) == sorted(f["n"] for f in feats)
    assert all(f["title"].split("\n")[1] == "AM" for f in feats), "morning terminator"
    assert "Archimedes" in [f["name"] for f in feats]
    assert got["moreHidden"] == (got["more"] == 0)
    assert re.match(r"(EXC|FAV|MOD|DARK) \S|NONE ", got["libration"])


# The libration line, its tile and the arrow on the drawing: text and level
_LIBRATION = """
    h.online();
    await h.load('bodies');
    const svg = h.$('solar-moon-diagram').children[0];
    const arrow = svg.children.find(c => c.attrs.class === 'solar-moon-arrow');
    const tile = h.$('solar-moon-tiles').children[1];
    return { text: h.$('solar-moon-libration').textContent, line: h.$('solar-moon-libration').dataset.level,
             tile: tile.dataset.level, arrow: arrow.attrs['data-level'] };
"""


def test_libration_towards_a_dark_limb_is_never_favourable(tmp_path):
    """17 Nov 2026: tilted 6.0 deg to the Mare Orientale side, which is in darkness: the
    dark wording, and the line, tile and arrow in the neutral style, not favourable."""
    got = _run(tmp_path, _LIBRATION, sim="2026-11-17")
    assert got["text"] == "DARK Mare Orientale 6.0"
    assert got["line"] == got["tile"] == got["arrow"] == "dark"


def test_libration_towards_a_lit_limb_is_favourable(tmp_path):
    """12 Nov 2026: 5.6 deg to the Mare Humboldtianum side, lit: favourable, with the lit
    features named."""
    got = _run(tmp_path, _LIBRATION, sim="2026-11-12")
    assert re.fullmatch(r"FAV Mare Humboldtianum 5\.6 — [A-Z].*", got["text"])
    assert got["line"] == got["tile"] == got["arrow"] == "favourable"


def test_marker_number_only_for_the_hovered_or_focused_chip(tmp_path):
    """The dots on the disc carry no number; the chip under the pointer or focus puts
    its number, on a larger dot, on the drawing's top layer, one at a time."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const svg = h.$('solar-moon-diagram').children[0];
        const hot = svg.children[svg.children.length - 1];
        const chips = h.$('solar-moon-features').children;
        const shown = () => hot.children.map(c => ({ tag: c.tagName, text: c.textContent,
            x: +(c.attrs.cx || c.attrs.x), y: +(c.attrs.cy || c.attrs.y) }));
        const dots = svg.children.filter(c => c.attrs.class === 'solar-moon-marker');
        const out = { hotClass: hot.attrs.class, dotTags: dots.map(g => g.children.map(c => c.tagName)),
                      dotAt: Object.fromEntries(dots.map(g => [g.attrs['data-n'], [+g.children[0].attrs.cx, +g.children[0].attrs.cy]])),
                      before: shown() };
        await chips[1].fire('mouseenter');
        out.hover2 = shown();
        await chips[0].fire('mouseenter');
        out.hover1 = shown();
        await chips[0].fire('mouseleave');
        out.left = shown();
        await chips[2].fire('focus');
        out.focus3 = shown();
        await chips[2].fire('blur');
        out.blurred = shown();
        return out;
    """, sim="2026-11-17")
    assert got["hotClass"] == "solar-moon-hot", "the top layer is drawn last"
    assert got["dotTags"] and all(tags == ["circle", "title"] for tags in got["dotTags"]), "no number on a dot"
    assert got["before"] == [] and got["left"] == [] and got["blurred"] == []
    for key, n in (("hover2", "2"), ("hover1", "1"), ("focus3", "3")):
        shown = got[key]
        assert [c["tag"] for c in shown] == ["circle", "text"] and shown[1]["text"] == n
        assert [shown[0]["x"], shown[0]["y"]] == pytest.approx(got["dotAt"][n])


def test_moon_card_says_why_when_the_moon_is_not_up(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        return card();
    """, sim="2026-12-09")
    assert got["empty"] == "NOTUP" and got["features"] == []


def test_view_switch_mirrors_the_drawing_and_is_remembered(tmp_path):
    """The three cards' one shared view, nova_solar_view (the Moon's own key while the
    Moon was the only card: test_view_switch_restores_and_survives_blocked_storage)."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { first: card() };
        await h.$('moon-view-diagonal').fire('click');
        out.diagonal = card();
        out.stored = h.stored('nova_solar_view');
        await h.$('moon-view-inverted').fire('click');
        out.inverted = card();
        return out;
    """, sim="2026-11-17")
    assert got["first"]["pressed"] == ["normal"]
    assert got["diagonal"]["pressed"] == ["diagonal"] and got["stored"] == "diagonal"
    normal = {m["n"]: m for m in got["first"]["markers"]}
    for m in got["diagonal"]["markers"]:
        assert m["x"] == pytest.approx(-normal[m["n"]]["x"]) and m["y"] == pytest.approx(normal[m["n"]]["y"])
    for m in got["inverted"]["markers"]:
        assert m["x"] == pytest.approx(-normal[m["n"]]["x"]) and m["y"] == pytest.approx(-normal[m["n"]]["y"])


def test_view_switch_restores_and_survives_blocked_storage(tmp_path):
    """A stored view is restored (and, from the old Moon-only key, adopted under the new
    one); a storage that refuses every read and write leaves the switch working."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: card(), stored: h.stored('nova_solar_view') };
    """, sim="2026-11-17", storage={"nova_solar_moon_view": "inverted"})
    assert got["card"]["pressed"] == ["inverted"] and got["stored"] == "inverted"
    blocked = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        await h.$('moon-view-diagonal').fire('click');
        return card();
    """, storage="throws")
    assert blocked["pressed"] == ["diagonal"] and not blocked["hidden"] and blocked["error"] is None


def test_moon_card_error_leaves_the_rest_of_the_page(tmp_path):
    got = _run(tmp_path, """
        h.online();
        h.context.NovaSolarMoon.tonight = () => { throw new Error('boom'); };
        await h.load('bodies');
        return { card: card(), snap: h.snap() };
    """, sim="2026-11-17")
    assert got["card"]["error"] == "MOON ERROR" and got["card"]["bodyHidden"]
    assert got["snap"]["solarError"] is None and got["snap"]["bodyRows"] > 0


def test_simulated_date_change_recomputes_the_card(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { quarter: card() };
        h.$('sim-date-input').value = '2026-11-02';
        await h.$('sim-date-input').fire('change');
        await h.flush();
        out.waning = card();
        return out;
    """, sim="2026-11-17")
    assert got["quarter"]["features"][0]["title"].split("\n")[1] == "AM"
    assert got["waning"]["features"] and all(f["title"].split("\n")[1] == "PM" for f in got["waning"]["features"])


def test_calendar_lunar_rows_and_the_event_type_filter(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('calendar');
        await h.flush();
        const events = () => h.table('solar-cal-body').map(r => ({
            text: r.cells.find(c => c.col === 'event').text, title: r.title, body: r.body,
            help: r.cells.find(c => c.col === 'event').title }));
        const out = { all: events() };
        await h.choose('solar-cal-type-filter', 'lunar');
        out.lunar = events();
        out.stored = h.stored('nova_solar_cal_type');
        await h.choose('solar-cal-type-filter', 'planets');
        out.planets = events();
        await h.choose('solar-cal-type-filter', 'conjunctions');
        out.conjunctions = events();
        return out;
    """, search="")
    lunar_text = lambda e: e["text"] == "LUNAR X" or e["text"].startswith("EXC ")
    assert any(e["text"] == "LUNAR X" for e in got["all"]) and any(e["text"].startswith("EXC ") for e in got["all"])
    assert got["lunar"] and all(lunar_text(e) and e["body"] == "moon" for e in got["lunar"])
    assert got["stored"] == "lunar"
    assert all(e["help"] in ("X HELP", "LIB HELP") for e in got["lunar"])
    assert any(e["title"] == "LUNAR HIDDEN" for e in got["lunar"] if e["text"] == "LUNAR X")
    assert got["planets"] and not any(lunar_text(e) for e in got["planets"])
    assert got["conjunctions"] and not any(lunar_text(e) for e in got["conjunctions"])
    assert len(got["lunar"]) + len(got["planets"]) + len(got["conjunctions"]) == len(got["all"])


def test_calendar_type_filter_is_restored(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('calendar');
        await h.flush();
        return { value: h.$('solar-cal-type-filter').value, rows: h.table('solar-cal-body').map(r => r.body) };
    """, search="", storage={"nova_solar_cal_type": "lunar"})
    assert got["value"] == "lunar" and got["rows"] and set(got["rows"]) == {"moon"}


def test_without_the_moon_scripts_the_page_works_as_before(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('calendar');
        await h.flush();
        return { hidden: h.$('solar-moon').hidden,
                 kinds: h.table('solar-cal-body').map(r => r.cells.find(c => c.col === 'event').text) };
    """, order=PAGE_ORDER)
    assert got["hidden"] is True
    assert got["kinds"] and "LUNAR X" not in got["kinds"]


def test_moon_card_credits_the_feature_names_source(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        return { text: h.$('solar-moon-credit').textContent, hidden: h.$('solar-moon-credit').hidden };
    """, sim="2026-11-17")
    assert got == {"text": "SRC Gazetteer of Planetary Nomenclature, IAU WGPSN / USGS Astrogeology",
                   "hidden": False}


def test_chip_aria_label_starts_with_the_name(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        return h.$('solar-moon-features').children.map(li => ({ label: li.attrs['aria-label'],
            name: li.children.filter(c => c.className === 'solar-moon-name')[0].textContent, side: li.dataset.side }));
    """, sim="2026-11-17")
    assert got
    for chip in got:
        parts = chip["label"].split(", ")
        assert len(parts) == 4 and parts[0] == chip["name"], chip
        assert parts[2] == ("AM" if chip["side"] == "morning" else "PM") and parts[3].startswith("sun "), chip


def test_tick_with_unchanged_inputs_keeps_the_card_nodes_and_focus(tmp_path):
    """The card is drawn once per night, location, simulated date, view and language: a
    tick in between replaces nothing, so a focused chip keeps its focus and its marker."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const svg = h.$('solar-moon-diagram').children[0];
        const chip = h.$('solar-moon-features').children[1];
        chip.marked = true;
        await chip.fire('focus');
        const hot = () => svg.children[svg.children.length - 1].children.map(c => c.textContent);
        const out = { before: hot() };
        await h.tick();
        await h.tick();
        out.sameSvg = h.$('solar-moon-diagram').children[0] === svg;
        out.sameChip = h.$('solar-moon-features').children[1] === chip && chip.marked === true;
        out.after = hot();
        // A view change still redraws
        await h.$('moon-view-diagonal').fire('click');
        out.redrawn = h.$('solar-moon-diagram').children[0] !== svg;
        return out;
    """, sim="2026-11-17")
    assert got["before"] == ["", "2"]
    assert got["sameSvg"] and got["sameChip"]
    assert got["after"] == ["", "2"]
    assert got["redrawn"]


def test_moon_card_error_resets_the_title(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { ok: card().title };
        h.context.NovaSolarMoon.tonight = () => { throw new Error('boom'); };
        h.$('sim-date-input').value = '2026-11-02';
        await h.$('sim-date-input').fire('change');
        await h.flush();
        out.failed = card();
        return out;
    """, sim="2026-11-17")
    assert got["ok"].startswith("MOON · at ")
    assert got["failed"]["title"] == "MOON" and got["failed"]["error"] == "MOON ERROR"


def test_calendar_shows_the_planets_when_the_lunar_rows_fail(tmp_path):
    got = _run(tmp_path, """
        h.online();
        const logged = [];
        h.context.console.error = (msg) => logged.push(String(msg));
        h.context.NovaSolarMoon.calendarRows = () => { throw new Error('boom'); };
        await h.load('calendar');
        await h.flush();
        return { rows: h.table('solar-cal-body').map(r => ({ body: r.body,
                     text: r.cells.find(c => c.col === 'event').text })),
                 error: h.$('solar-cal-error').hidden ? null : h.$('solar-cal-error').textContent,
                 logged: logged };
    """, search="")
    assert got["error"] is None
    assert got["rows"] and not any(r["text"] == "LUNAR X" or r["text"].startswith("EXC ") for r in got["rows"])
    assert any("lunar Calendar events" in m for m in got["logged"])


def test_moon_libration_colours_are_the_documented_success_values():
    """Favourable and excellent take the seeing tiles' success colour, light and dark;
    moderate and none stay neutral (no amber, no legacy status variables)."""
    css = (STATIC_JS.parent / "css" / "solar_system.css").read_text()
    moon = css[css.index("THE MOON CARD"):]

    def rule(selector):
        # A rule's selector may be one name in a list: the credit is styled with the
        # Mars card's, in the same rule
        for block in moon.split("}"):
            head, _, decls = block.partition("{")
            # The slice starts inside the section's comment, whose opening /* is cut
            head = re.sub(r"/\*.*?\*/", "", head, flags=re.S).split("*/")[-1]
            if selector in [name.strip() for name in head.split(",")]:
                return head + "{" + decls
        raise ValueError(selector)

    light, dark = rule("#solar-moon"), rule(':root[data-theme="dark"] #solar-moon')
    assert "--solar-moon-good: #2a9060;" in light and "--solar-moon-good-tint: rgba(42, 144, 96, 0.10);" in light
    assert "--solar-moon-good: #6dcab0;" in dark and "--solar-moon-good-tint: rgba(109, 202, 176, 0.14);" in dark
    for sel in ('.solar-moon-arrow[data-level="excellent"]', '.solar-moon-tile[data-level="excellent"]',
                '.solar-moon-tile[data-level="excellent"] .solar-moon-dial-hand',
                '.solar-moon-libration[data-level="excellent"]'):
        assert "var(--solar-moon-good)" in rule("#bodies-tab " + sel), sel
    assert "background: var(--solar-moon-good-tint);" in rule('#bodies-tab .solar-moon-tile[data-level="excellent"]')
    assert 'data-level="moderate"' not in moon and 'data-level="none"' not in moon
    assert "--success-color" not in moon and "--warning-color" not in moon
    credit = rule("#bodies-tab .solar-moon-credit")
    assert "font-size: 12px;" in credit and "color: var(--text-muted);" in credit


# --- the template --------------------------------------------------------------------------

def _scripts(html):
    return re.findall(r'<script[^>]*\ssrc="[^"]*/js/([^"/?]+\.js)\?v=[^"]*"[^>]*>', html)


def test_page_loads_the_moon_scripts_after_their_engines(client):
    scripts = _scripts(client.get("/solar_system").get_data(as_text=True))
    at = scripts.index
    assert at("nova_astro.js") < at("nova_moon.js") < at("nova_solar_moon.js") < at("solar_system.js")
    assert at("nova_moon_features.js") < at("nova_solar_moon.js")
    assert at("nova_planets.js") < at("nova_solar_rows.js") < at("nova_solar_moon.js")


def test_view_control_has_short_labels_with_the_long_ones_as_tooltips(client):
    html = client.get("/solar_system").get_data(as_text=True)
    buttons = re.findall(r'<button type="button" class="solar-moon-view" data-moon-view="(\w+)"'
                         r' aria-pressed="\w+"(?: title="([^"]*)")?>([^<]*)</button>', html)
    assert [(v, t, label) for v, t, label in buttons] == [
        ("normal", "", "Normal"),
        ("diagonal", "Star diagonal (mirrored)", "Diagonal"),
        ("inverted", "Newtonian (inverted)", "Newtonian"),
    ]


def test_page_has_the_moon_card_and_the_event_type_filter(client):
    html = client.get("/solar_system").get_data(as_text=True)
    card = html.index('id="solar-moon"')
    assert html.index('id="chart-section"') < card < html.index('id="solar-calc"')
    assert re.search(r'<div id="solar-moon" class="content-section" hidden>', html)
    for view in ("normal", "diagonal", "inverted"):
        assert f'data-moon-view="{view}"' in html
    select = re.search(r'<select id="solar-cal-type-filter".*?</select>', html, re.S).group(0)
    assert re.findall(r'<option value="(\w*)"', select) == ["", "planets", "conjunctions", "lunar",
                                                            "galilean"]
    assert '"Lunar X and V"' in html
