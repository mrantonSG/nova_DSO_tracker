"""The Solar System page's Jupiter card and its Galilean Calendar rows, in node.

Runs static/js/solar_system.js with the real engines and the stand-in DOM of
tests/test_solar_system_js.py (its driver, unchanged), with the Moon's and both new
cards' modules loaded too, as the template loads them. What the page puts in the DOM is
checked against the modules' own output for the same inputs (the modules themselves are
pinned to the engines in tests/test_solar_jupiter.py); the template checks at the end
render the page with the Flask client.

The harness -- the three cards' view buttons and the two new cards' snapshots -- is here,
and test_solar_mars_page.py imports _run and the i18n from this module.
"""

import json
import math
import re
import subprocess

import pytest

from test_solar_system_js import _DRIVER, LONDON, NODE, PAGE_ORDER, STATIC_JS

# The page's own order, the Moon's and both new cards' scripts as the template loads them
CARDS_ORDER = PAGE_ORDER + ("nova_moon.js", "nova_moon_features.js", "nova_jupiter.js",
                            "nova_mars.js", "nova_mars_features.js", "nova_solar_moon.js",
                            "nova_solar_jupiter.js", "nova_solar_mars.js")

# 2 Nov 2026 20:00 UTC
NOW_MS = 1793649600000

# The location the cards are computed for, as JavaScript (LONDON, from the driver)
SITE = json.dumps(LONDON)

I18N = {
    "error": "CALC ERROR", "inputsError": "INPUTS ERROR", "chartError": "CHART ERROR",
    "belowThreshold": "BELOW", "exactTime": "exact {time}",
    "bodies": {"moon": "Moon", "jupiter": "Jupiter", "mars": "Mars"},
    "eventHelp": {"galilean": "GAL HELP"},
    "galilean": "DOUBLE ({moons}, {minutes} min)",
    "jupiter": {
        "title": "JUPITER", "error": "JUP ERROR", "highestAt": "at {time}", "north": "N",
        "northHelp": "NORTH TIP", "diameter": "DIA", "diameterHelp": "DIA TIP",
        "magnitude": "MAG", "illuminated": "ILLUM", "cm": "CM", "cmHelp": "CM TIP",
        "de": "DE", "deHelp": "DE TIP", "east": "east", "west": "west",
        "onDisc": "on the disc", "inFront": "in front", "behind": "behind",
        "eclipse": "in eclipse", "rj": "{rj} Rj", "rjFromCentre": "{rj} radii",
        "shadowOnDisc": "SHADOW", "minutes": "{n} min", "altitude": "alt {alt}",
        "double": "double", "doubleHelp": "DOUBLE TIP", "shadowAt": "shadow at {time}",
        "until": "until {time}", "noEvents": "NO EVENTS", "noShadows": "NO SHADOWS",
        "empty": {"notUp": "NOTUP"}, "diagramAria": "JUP DIAGRAM",
        "moonNames": {"io": "Io", "europa": "Europa", "ganymede": "Ganymede",
                      "callisto": "Callisto"},
        "eventTypes": {"transit": "Satellite transit", "occultation": "Occultation",
                       "eclipse": "Eclipse", "shadow": "Shadow transit"},
    },
    "mars": {
        "title": "MARS", "error": "MARS ERROR", "highestAt": "at {time}", "north": "N",
        "diameter": "DIA", "magnitude": "MAG", "illuminated": "ILLUM", "distance": "DIST",
        "cm": "CM", "de": "DE", "credit": "SRC {source}", "diagramAria": "MARS DIAGRAM",
        "poleTilt": "tilt {deg}", "position": "{lat}/{lon}", "cmDist": "cm {deg}",
        "nearLimb": "LIMB", "nextPassage": "next {time} {when}", "today": "TONIGHT",
        "tomorrow": "TOMORROW", "inDays": "IN {n}",
        "empty": {"none": "NOTHING", "notUp": "NOTUP"},
        "types": {"mons": "Mountain", "crater": "Crater", "albedo": "Albedo"},
    },
}

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# The three cards' view buttons -- the Moon's group and the two the new cards share --
# which the driver's querySelectorAll does not know, and both new cards' snapshots. The
# buttons are built before the page runs, as the template has them.
_VIEWS = r"""
const view = ['normal', 'diagonal', 'inverted'];
const moonButtons = view.map(v => { const b = h.$('moon-view-' + v); b.dataset.moonView = v; return b; });
const jupButtons = view.map(v => { const b = h.$('jupiter-view-' + v); b.dataset.cardView = v; return b; });
const marsButtons = view.map(v => { const b = h.$('mars-view-' + v); b.dataset.cardView = v; return b; });
const qsa = h.context.document.querySelectorAll;
h.context.document.querySelectorAll = sel =>
    (sel.indexOf('solar-moon-view') >= 0 || sel.indexOf('solar-card-view') >= 0)
        ? moonButtons.concat(jupButtons, marsButtons) : qsa(sel);
const pressed = btns => btns.filter(b => b.attrs['aria-pressed'] === 'true')
    .map(b => b.dataset.cardView || b.dataset.moonView);
// An element's children as their class and text
const parts = el => el.children.map(c => ({ cls: c.className, text: c.textContent }));
// The children carrying a class (an SVG element carries its class as an attribute)
const withClass = (parent, cls) =>
    parent.children.filter(c => String(c.attrs.class || '').trim().split(/\s+/).indexOf(cls) >= 0);
const svgOf = id => h.$(id).children[0];
const jupCard = () => {
    const box = h.$('solar-jupiter'), body = h.$('solar-jupiter-body'), svg = svgOf('solar-jupiter-diagram');
    return {
        hidden: box.hidden, title: h.$('solar-jupiter-title').textContent,
        error: h.$('solar-jupiter-error').hidden ? null : h.$('solar-jupiter-error').textContent,
        bodyHidden: body.hidden, muted: body.classList.contains('solar-card-muted'),
        tiles: h.$('solar-jupiter-tiles').children.map(t => ({
            label: t.children[0].textContent, value: t.children[1].children[0].textContent,
            cls: t.children[1].children[0].className, title: t.title })),
        moons: h.$('solar-jupiter-moons').children.map(li => ({
            moon: li.dataset.moon, cls: li.className, parts: parts(li), title: li.title })),
        events: h.$('solar-jupiter-events').children.map(li => ({
            type: li.dataset.type, below: li.classList.contains('solar-card-below'),
            parts: parts(li), title: li.title })),
        shadows: h.$('solar-jupiter-shadows').children.map(li => ({
            type: li.dataset.type, parts: parts(li), title: li.title })),
        empty: h.$('solar-jupiter-empty').hidden ? null : h.$('solar-jupiter-empty').textContent,
        noShadows: h.$('solar-jupiter-no-shadows').hidden ? null : h.$('solar-jupiter-no-shadows').textContent,
        pressed: pressed(jupButtons),
        svg: !svg ? null : {
            viewBox: svg.attrs.viewBox, label: svg.attrs['aria-label'],
            discs: withClass(svg, 'solar-card-disc').length,
            moons: withClass(svg, 'solar-card-moon').map(g => ({ moon: g.attrs['data-moon'],
                cls: g.attrs.class, cx: +g.children[0].attrs.cx, cy: +g.children[0].attrs.cy,
                r: +g.children[0].attrs.r, title: g.children[1].textContent })),
            labels: withClass(svg, 'solar-card-moon-label').map(t => ({
                text: t.textContent, x: +t.attrs.x, y: +t.attrs.y })),
            shadows: withClass(svg, 'solar-card-shadow').map(g => ({ moon: g.attrs['data-moon'],
                cx: +g.children[0].attrs.cx, cy: +g.children[0].attrs.cy, title: g.children[1].textContent })),
            north: withClass(svg, 'solar-card-north').map(t => ({
                text: t.textContent, x: +t.attrs.x, y: +t.attrs.y, title: t.children[0].textContent })),
        },
    };
};
const marsCard = () => {
    const box = h.$('solar-mars'), body = h.$('solar-mars-body'), svg = svgOf('solar-mars-diagram');
    const marker = g => ({ n: g.attrs['data-n'], cx: +g.children[0].attrs.cx,
        cy: +g.children[0].attrs.cy, r: +g.children[0].attrs.r, title: g.children[1].textContent });
    // A feature chip: the number when it has one, the name, the type, and the bar's width
    const chip = li => {
        const c = li.children, off = c[0].className === 'solar-card-num' ? 1 : 0;
        return { n: li.dataset.n || null, side: li.dataset.side, cls: li.className,
            num: off ? c[0].textContent : null, name: c[off].textContent,
            type: c[off + 1].textContent, bar: c[off + 2].children[0].style.width,
            title: li.title, label: li.attrs['aria-label'] || null, focusable: li.attrs.tabindex || null };
    };
    const pole = g => { const d = g.children[0].attrs.d.split(' ');
        return { d: g.children[0].attrs.d, x: +d[4], y: +d[5], title: g.children[1].textContent }; };
    return {
        hidden: box.hidden, title: h.$('solar-mars-title').textContent,
        error: h.$('solar-mars-error').hidden ? null : h.$('solar-mars-error').textContent,
        bodyHidden: body.hidden, muted: body.classList.contains('solar-card-muted'),
        tiles: h.$('solar-mars-tiles').children.map(t => ({
            label: t.children[0].textContent, value: t.children[1].children[0].textContent,
            title: t.title })),
        features: h.$('solar-mars-features').children.map(chip),
        moreList: h.$('solar-mars-more-list').children.map(chip),
        more: h.$('solar-mars-more-list').children.length, moreHidden: h.$('solar-mars-more').hidden,
        empty: h.$('solar-mars-empty').hidden ? null : h.$('solar-mars-empty').textContent,
        credit: h.$('solar-mars-credit').hidden ? null : h.$('solar-mars-credit').textContent,
        pressed: pressed(marsButtons),
        hot: !svg ? null : withClass(svg, 'solar-card-hot')[0].children.map(c => ({
            tag: c.tagName, text: c.textContent, r: c.attrs.r || null,
            cx: c.attrs.cx || null, cy: c.attrs.cy || null,
            x: c.attrs.x || null, y: c.attrs.y || null })),
        svg: !svg ? null : {
            label: svg.attrs['aria-label'],
            surface: withClass(svg, 'solar-card-surface').length,
            shaded: withClass(svg, 'solar-card-shaded').length,
            terminator: withClass(svg, 'solar-card-terminator').length,
            markers: withClass(svg, 'solar-card-marker').map(marker),
            pole: withClass(svg, 'solar-card-pole').map(pole),
            north: withClass(svg, 'solar-card-north').map(t => ({
                text: t.textContent, x: +t.attrs.x, y: +t.attrs.y })),
        },
    };
};
"""


def _run(tmp_path, steps, search="?body=jupiter", sim=None, storage=None, order=CARDS_ORDER):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "steps.js"
    # The steps run in node, whose clock is the real one; the page's is the vm's, pinned by
    # the driver to NOW_MS. The cases are handed the same number so they can ask the modules
    # for what the page sees.
    case.write_text(f"const NOW_MS = {NOW_MS};\n" + _VIEWS + steps)
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


# The module's own answer for exactly the inputs the page uses: the same location, the
# same simulated date, and the page's own pinned clock (the steps run in node, whose Date
# is the real one; the page's is the vm's, pinned by the driver).
_ORACLE = """
const J = h.context.NovaSolarJupiter;
const opts = {inputs: {location: %(site)s}, nowMs: NOW_MS, simDate: '%(sim)s'};
const t = J.tonight(opts);
const d = J.diagram(t.geometry, 'normal', t.moons);
const next = J.nextNights(opts);
"""


def _oracle(sim):
    return _ORACLE % {"site": SITE, "sim": sim}


def _num(text):
    """The number in a formatted value, whatever unit or separator surrounds it."""
    return float(re.search(r"[-−+]?\d+(?:\.\d+)?", text).group(0).replace("−", "-"))


def _signed(x, digits=1):
    """'+4.6' or '−6.6': the page's own signed(), U+2212 and all."""
    return ("−" if x < 0 else "+") + f"{abs(x):.{digits}f}"


def _time(hm, hm_day):
    """NovaSolarRows.timeLabel(): the hour, and the day suffix when there is one."""
    return hm + (f" {'−' if hm_day < 0 else '+'}{abs(hm_day)}" if hm_day else "")


def test_jupiter_card_only_while_jupiter_is_selected(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { jupiter: jupCard() };
        const rows = h.$('solar-body').children;
        await h.clickRow('solar-body', rows.findIndex(tr => tr.dataset.body === 'moon'));
        out.moon = jupCard();
        return out;
    """, sim="2026-12-09")
    assert not got["jupiter"]["hidden"] and got["jupiter"]["error"] is None
    assert got["jupiter"]["tiles"] and got["jupiter"]["svg"]["discs"] == 1
    assert got["moon"]["hidden"]


def test_jupiter_card_shows_the_moons_the_phenomena_and_the_shadows(tmp_path):
    """9 Dec 2026, London: five tiles, the four moons with where each one is, tonight's six
    phenomena with their times and lengths, the next nights' shadow transits, and the
    drawing. Every value is the module's own for the same inputs."""
    got = _run(tmp_path, _oracle("2026-12-09") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), strip: {R: J.STRIP_RJ, H: J.STRIP_HALF_HEIGHT},
                 t: { hm: t.hm, mag: t.mag, illuminated: t.illuminated, ge: t.geometry,
                      events: t.events, moons: t.moons.map(m => ({name: m.name, x: m.x, y: m.y,
                          rj: m.rj, front: m.front, onDisc: m.onDisc, eclipsed: m.eclipsed,
                          shadow: m.shadow, dist: Math.hypot(m.x, m.y).toFixed(1)})) },
                 d: d, next: next.shadows,
                 dates: next.shadows.map(s => { const [y, m, dd] = s.date.split('-').map(Number);
                     return new Intl.DateTimeFormat('en', {weekday: 'short', day: 'numeric',
                         month: 'short', timeZone: 'UTC'}).format(new Date(Date.UTC(y, m - 1, dd))); }) };
    """, sim="2026-12-09")
    card, t, d = got["card"], got["t"], got["d"]
    assert re.fullmatch(r"JUPITER · at \d\d:\d\d", card["title"])
    assert card["title"] == "JUPITER · at " + t["hm"]
    assert not card["hidden"] and card["error"] is None and not card["muted"]

    # The tiles: diameter, magnitude, illumination, the two central meridians, De
    tiles = card["tiles"]
    assert [tile["label"] for tile in tiles] == ["DIA", "MAG", "ILLUM", "CM", "DE"]
    assert [tile["cls"] for tile in tiles] == ["solar-card-tile-value"] * 5
    assert tiles[0]["value"].endswith("″") and _num(tiles[0]["value"]) == pytest.approx(
        t["ge"]["diameterArcsec"], abs=0.05)
    assert _num(tiles[1]["value"]) == pytest.approx(t["mag"], abs=0.05)
    assert tiles[2]["value"].endswith("%") and _num(tiles[2]["value"]) == pytest.approx(
        t["illuminated"] * 100, abs=0.5)
    assert tiles[3]["value"].endswith("°") and " · " in tiles[3]["value"]
    assert [_num(part) for part in tiles[3]["value"].split(" · ")] == [
        pytest.approx(t["ge"]["cm"]["I"], abs=0.5), pytest.approx(t["ge"]["cm"]["II"], abs=0.5)]
    assert tiles[4]["value"][0] == ("−" if t["ge"]["de"] < 0 else "+")
    assert _num(tiles[4]["value"]) == pytest.approx(t["ge"]["de"], abs=0.05)
    assert tiles[4]["value"].endswith("°")
    assert tiles[0]["title"] == "DIA TIP" and tiles[3]["title"] == "CM TIP"
    assert tiles[1]["title"] == "" and tiles[2]["title"] == ""

    # The moons: all four, in the engine's order, each with which side of Jupiter it is on,
    # in front of the disc or behind the planet, in eclipse or not, and how far out
    assert [chip["moon"] for chip in card["moons"]] == [m["name"] for m in t["moons"]]
    for chip, m in zip(card["moons"], t["moons"]):
        place = ["east" if m["x"] >= 0 else "west",
                 "on the disc" if m["onDisc"] else "in front" if m["front"] else "behind"]
        if m["eclipsed"]:
            place.append("in eclipse")
        assert chip["cls"] == "solar-card-item solar-card-moon"
        assert chip["parts"] == [{"cls": "solar-card-name", "text": m["name"].capitalize()},
                                 {"cls": "solar-card-note", "text": " · ".join(place)},
                                 {"cls": "solar-card-distance", "text": m["dist"] + " Rj"}]
        assert chip["title"].split("\n") == [" · ".join(place), f"{m['rj']:.2f} radii"] \
            + (["SHADOW"] if m["shadow"] else [])
    assert any(m["onDisc"] for m in t["moons"]), "no moon on the disc to check"

    # Tonight's phenomena: the engine's own events in order, each with its time, what it is,
    # the moon, how long it lasts, the altitude in the tooltip and the double badge
    assert [chip["type"] for chip in card["events"]] == [e["type"] for e in t["events"]]
    assert card["events"], "no phenomena to check"
    types = {"transit": "Satellite transit", "occultation": "Occultation", "eclipse": "Eclipse",
             "shadow": "Shadow transit"}
    for chip, e in zip(card["events"], t["events"]):
        assert chip["parts"][:4] == [
            {"cls": "solar-card-time", "text": _time(e["hm"], e["hmDay"])},
            {"cls": "solar-card-name", "text": types[e["type"]]},
            {"cls": "solar-card-note", "text": e["moon"].capitalize()},
            {"cls": "solar-card-note", "text": f"{e['minutes']} min"}]
        assert chip["parts"][4:] == ([{"cls": "solar-card-badge", "text": "double"}] if e["double"] else [])
        assert chip["below"] == (not e["visible"])
        lines = chip["title"].split("\n")
        assert lines[0] == types[e["type"]] and lines[1] == f"{e['minutes']} min"
        assert lines[2].startswith("alt ")
        assert ("BELOW" in lines) == (not e["visible"])
        assert ("DOUBLE TIP" in lines) == bool(e["double"])

    # The next nights' shadow transits, doubles merged: one chip per row, the moons that
    # pair up, the length, and the tooltip's own time
    assert got["next"] and len(card["shadows"]) == len(got["next"])
    for chip, s, date in zip(card["shadows"], got["next"], got["dates"]):
        time = _time(s["hm"], s["hmDay"])
        assert chip["type"] == "shadow"
        assert chip["parts"][0] == {"cls": "solar-card-date", "text": date}
        assert re.fullmatch(r"\w{3}, \w{3} \d{1,2}", chip["parts"][0]["text"]), chip["parts"][0]
        assert chip["parts"][1:] == (
            [{"cls": "solar-card-time", "text": time},
             {"cls": "solar-card-name", "text": " + ".join(m.capitalize() for m in s["moons"])},
             {"cls": "solar-card-note", "text": f"{s['minutes']} min"}]
            + ([{"cls": "solar-card-badge", "text": "double"}] if s["double"] else []))
        assert chip["title"].startswith("shadow at " + time)
    assert card["noShadows"] is None and card["empty"] is None

    # The drawing: the strip's own frame, the disc on the moons' line, a dot and a label
    # per moon (hollow behind the planet), a dark spot per shadow, and celestial north
    svg, R, H = card["svg"], got["strip"]["R"], got["strip"]["H"]
    assert svg["viewBox"] == f"{-(R + 1)} {-H} {2 * (R + 1)} {2 * H}"
    assert svg["label"] == "JUP DIAGRAM" and svg["discs"] == 1
    assert [dot["moon"] for dot in svg["moons"]] == [m["name"] for m in t["moons"]]
    for dot, m in zip(svg["moons"], d["moons"]):
        assert dot["cx"] == pytest.approx(m["x"], abs=1e-6)
        assert dot["cy"] == pytest.approx(m["y"], abs=1e-6)
        assert dot["r"] == 0.26 and abs(dot["cy"]) <= H
        assert dot["cls"] == ("solar-card-moon" if m["front"]
                              else "solar-card-moon solar-card-moon-behind")
        assert dot["title"].startswith(m["name"].capitalize() + " — ") and " Rj" not in dot["title"]
    assert [label["text"] for label in svg["labels"]] == ["Io", "Europa", "Ganymede", "Callisto"]
    assert [label["x"] for label in svg["labels"]] == [dot["cx"] for dot in svg["moons"]]
    assert all(abs(label["y"]) <= H for label in svg["labels"])

    spots = [(m, s) for m, s in zip(t["moons"], d["moons"]) if s["shadow"]]
    assert spots, "no shadow on the disc to check"
    assert [spot["moon"] for spot in svg["shadows"]] == [m["name"] for m, _ in spots]
    for spot, (m, s) in zip(svg["shadows"], spots):
        assert spot["cx"] == pytest.approx(s["shadow"]["x"], abs=1e-6)
        assert spot["cy"] == pytest.approx(s["shadow"]["y"], abs=1e-6)
        assert spot["title"] == m["name"].capitalize() + " — SHADOW"

    north = svg["north"]
    assert len(north) == 1 and north[0]["text"] == "N" and north[0]["title"] == "NORTH TIP"
    # The label sits 1.85 Rj out along celestial north, written to two decimals
    assert north[0]["x"] == pytest.approx(float(f"{d['north']['x'] * 1.85:.2f}"), abs=1e-9)
    assert north[0]["y"] == pytest.approx(float(f"{d['north']['y'] * 1.85:.2f}"), abs=1e-9)
    assert math.hypot(north[0]["x"], north[0]["y"]) == pytest.approx(1.85, abs=0.01)


def test_jupiter_card_is_quiet_when_it_is_below_the_threshold(tmp_path):
    """Two nights Jupiter is not worth pointing at: 1 Aug 2027 it climbs to 6 degrees, and
    1 Sep 2027 it does not rise at all. The card says which, muted, and still places the
    moons; a night with nothing happening at all (14 Feb 2027) says that instead."""
    low = _run(tmp_path, _oracle("2027-08-01") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), maxAlt: t.maxAlt, threshold: t.threshold, reason: t.mutedReason };
    """, sim="2027-08-01")
    assert low["reason"] == "altitude" and low["maxAlt"] < low["threshold"] == 20
    assert low["card"]["muted"] and low["card"]["empty"] == "BELOW"
    assert len(low["card"]["moons"]) == 4 and low["card"]["events"] == []
    assert low["card"]["noShadows"] == "NO SHADOWS", "not one shadow transit in the fortnight"

    up = _run(tmp_path, _oracle("2027-09-01") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), maxAlt: t.maxAlt, reason: t.mutedReason };
    """, sim="2027-09-01")
    assert up["reason"] == "notUp" and up["maxAlt"] < 0
    assert up["card"]["muted"] and up["card"]["empty"] == "NOTUP"
    assert len(up["card"]["moons"]) == 4 and up["card"]["events"] == []

    quiet = _run(tmp_path, _oracle("2027-02-14") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), events: t.events.length, doubles: t.doubles };
    """, sim="2027-02-14")
    assert quiet["events"] == 0 and quiet["doubles"] == 0
    assert quiet["card"]["empty"] == "NO EVENTS" and quiet["card"]["events"] == []
    assert not quiet["card"]["muted"] and quiet["card"]["shadows"], "the next nights still have some"


def test_jupiter_card_error_leaves_the_rest_of_the_page(tmp_path):
    """A failure while computing the card: the card's own line, and the rest of the page --
    the bodies table, the Moon's card, the chart -- untouched."""
    got = _run(tmp_path, """
        h.online();
        h.context.NovaSolarJupiter.tonight = () => { throw new Error('boom'); };
        await h.load('bodies');
        const rows = h.$('solar-body').children;
        const row = body => rows.findIndex(tr => tr.dataset.body === body);
        const out = { card: jupCard() };
        await h.clickRow('solar-body', row('moon'));
        out.moon = jupCard();
        await h.clickRow('solar-body', row('jupiter'));
        out.back = jupCard();
        // The Moon's own card is unharmed, and shows when it is selected
        await h.clickRow('solar-body', row('moon'));
        out.moonShown = !h.$('solar-moon').hidden && h.$('solar-moon-error').hidden
            && h.$('solar-moon-diagram').children.length > 0;
        out.snap = h.snap();
        return out;
    """, sim="2026-12-09")
    assert got["card"]["error"] == "JUP ERROR" and got["card"]["bodyHidden"]
    assert got["card"]["title"] == "JUPITER" and not got["card"]["hidden"]
    assert got["back"]["error"] == "JUP ERROR" and got["back"]["bodyHidden"]
    assert got["moon"]["hidden"], "hidden while the Moon is selected"
    assert got["moonShown"] and got["snap"]["bodiesShown"]
    assert got["snap"]["solarError"] is None and got["snap"]["bodyRows"] > 0


def test_jupiter_card_is_computed_once_a_night(tmp_path):
    """The 60 s tick and a view change redraw but do not recompute: the card is keyed on the
    night, the location, the threshold and the sampling interval. A new night recomputes."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const J = h.context.NovaSolarJupiter;
        const calls = { tonight: 0, next: 0 };
        const tonight = J.tonight, nextNights = J.nextNights;
        J.tonight = function () { calls.tonight++; return tonight.apply(this, arguments); };
        J.nextNights = function () { calls.next++; return nextNights.apply(this, arguments); };
        const svg = svgOf('solar-jupiter-diagram');
        await h.tick();
        await h.tick();
        const after = { tonight: calls.tonight, next: calls.next,
                        sameSvg: svgOf('solar-jupiter-diagram') === svg };
        await h.$('jupiter-view-diagonal').fire('click');
        after.redrawn = svgOf('solar-jupiter-diagram') !== svg;
        after.pressed = jupCard().pressed;
        // A new night recomputes
        h.$('sim-date-input').value = '2027-01-03';
        await h.tick();
        return { after: after, calls: calls };
    """, sim="2026-12-09")
    assert got["after"] == {"tonight": 0, "next": 0, "sameSvg": True, "redrawn": True,
                            "pressed": ["diagonal"]}
    assert got["calls"] == {"tonight": 1, "next": 1}, "a new night recomputes the card"


def test_the_cards_view_switch_is_shared_and_mirrors_the_drawing(tmp_path):
    """One choice for all three cards, kept under nova_solar_view: either new card's buttons
    switch it, the Moon's own follow, and the drawing mirrors -- diagonal left and right,
    inverted both ways."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { normal: jupCard(), moonBefore: pressed(moonButtons) };
        await h.$('jupiter-view-diagonal').fire('click');
        out.diagonal = jupCard();
        out.storedAfterOne = h.stored('nova_solar_view');
        await h.$('mars-view-inverted').fire('click');
        out.inverted = { card: jupCard(), pressed: pressed(jupButtons),
                         moonPressed: pressed(moonButtons), marsPressed: pressed(marsButtons),
                         stored: h.stored('nova_solar_view') };
        return out;
    """, sim="2026-12-09")
    assert got["normal"]["pressed"] == ["normal"] and got["moonBefore"] == ["normal"]
    assert got["diagonal"]["pressed"] == ["diagonal"] and got["storedAfterOne"] == "diagonal"
    assert got["inverted"]["stored"] == "inverted"
    assert got["inverted"]["pressed"] == ["inverted"] and got["inverted"]["marsPressed"] == ["inverted"]
    assert got["inverted"]["moonPressed"] == ["inverted"], "the Moon's own buttons follow"
    normal = {dot["moon"]: dot for dot in got["normal"]["svg"]["moons"]}
    diagonal = {dot["moon"]: dot for dot in got["diagonal"]["svg"]["moons"]}
    inverted = {dot["moon"]: dot for dot in got["inverted"]["card"]["svg"]["moons"]}
    for moon, dot in diagonal.items():
        assert dot["cx"] == pytest.approx(-normal[moon]["cx"], abs=1e-6)
        assert dot["cy"] == pytest.approx(normal[moon]["cy"], abs=1e-6)
    for moon, dot in inverted.items():
        assert dot["cx"] == pytest.approx(-normal[moon]["cx"], abs=1e-6)
        assert dot["cy"] == pytest.approx(-normal[moon]["cy"], abs=1e-6)
    assert got["diagonal"]["svg"]["north"][0]["x"] == pytest.approx(
        -got["normal"]["svg"]["north"][0]["x"], abs=1e-6)


def test_the_calendar_carries_the_galilean_rows_and_the_filter_narrows_them(tmp_path):
    """The Calendar's sixth kind: two moons' shadows on Jupiter at once while Jupiter is
    above the threshold, from the module's own window over the next six months. The type
    filter and the body filter pick them out; the date cell's tooltip carries the end."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        await h.click('calendar');
        const first = { snap: h.snap(), rows: h.table('solar-cal-body') };
        const gali = first.rows.filter(r => r.cells[2].text.startsWith('DOUBLE ('));
        await h.choose('solar-cal-type-filter', 'galilean');
        const filtered = h.table('solar-cal-body');
        await h.choose('solar-cal-type-filter', 'lunar');
        const lunar = h.table('solar-cal-body');
        await h.choose('solar-cal-type-filter', '');
        await h.setFilter('jupiter', false);
        // The module's own window, and the end of each one as the page prints it (the end
        // is the row's own, so the day suffix has to come from the same day arithmetic)
        const rows = h.context.NovaSolarJupiter.calendarRows(
            {inputs: {location: """ + SITE + """}, nowMs: NOW_MS, simDate: '2026-11-02'}).rows;
        const tz = h.context.NovaSolarRows._night.site({location: """ + SITE + """}).tz;
        const dayNo = ms => { const p = h.context.NovaAstro.localParts(ms, tz);
            return Date.UTC(p.year, p.month - 1, p.day) / 86400000; };
        const module = rows.map(r => ({night: r.night, hm: r.hm, hmDay: r.hmDay, moons: r.moons,
            minutes: r.minutes, utcMs: r.utcMs, endUtcMs: r.endUtcMs,
            until: h.context.NovaSolarRows.timeLabel(h.context.NovaAstro.formatHM(r.endUtcMs, tz),
                dayNo(r.endUtcMs) - (dayNo(r.utcMs) - r.hmDay))}));
        return { first: first, gali: gali, filtered: filtered, lunar: lunar,
                 jupiter: h.table('solar-cal-body'), module: module };
    """, search="?body=moon", sim="2026-11-02")
    # The measured 7 for London over this window (nova_solar_jupiter.js's header)
    assert len(got["module"]) == 7 and len(got["gali"]) == len(got["module"])
    assert got["first"]["snap"]["calError"] is None
    assert got["first"]["snap"]["calRows"] == len(got["first"]["rows"])
    for row, module in zip(got["gali"], got["module"]):
        assert row["body"] == "jupiter" and row["cells"][1]["text"] == "Jupiter"
        assert row["cells"][0]["text"] == module["night"]
        assert row["cells"][2]["text"] == "DOUBLE (%s, %d min)" % (
            " + ".join(m.capitalize() for m in module["moons"]), module["minutes"])
        assert row["cells"][2]["title"] == "GAL HELP"
        assert module["endUtcMs"] > module["utcMs"] and module["hmDay"] in (0, 1)
        assert row["cells"][0]["title"] == "exact %s\nuntil %s" % (
            _time(module["hm"], module["hmDay"]), module["until"]), row["cells"][0]["title"]
    # The type filter: the Galilean rows and nothing else
    assert len(got["filtered"]) == len(got["gali"])
    assert all(r["cells"][2]["text"].startswith("DOUBLE (") for r in got["filtered"])
    assert not [r for r in got["lunar"] if r["cells"][2]["text"].startswith("DOUBLE (")]
    # The body filter: Jupiter's own rows, its conjunctions with the Moon among them (they
    # carry both bodies, and the row's own body is the first)
    assert got["jupiter"] and all("Jupiter" in r["cells"][1]["text"] for r in got["jupiter"])
    assert len(got["jupiter"]) >= len(got["gali"])
    assert [r for r in got["jupiter"] if r["body"] == "moon"], "a conjunction rides along"


def test_a_galilean_row_opens_jupiter_on_the_bodies_tab(tmp_path):
    """Clicking one: the Bodies tab, Jupiter selected, the event's own month on the chart."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        await h.click('calendar');
        const rows = h.table('solar-cal-body');
        const i = rows.findIndex(r => r.cells[2].text.startsWith('DOUBLE ('));
        await h.clickRow('solar-cal-body', i);
        return { night: rows[i].cells[0].text, shown: h.snap().bodiesShown, card: jupCard(),
                 month: h.$('solar-month-select').value, year: h.$('solar-year-select').value,
                 views: h.views().map(v => v[1]) };
    """, search="?body=moon", sim="2026-11-02")
    assert got["shown"] and not got["card"]["hidden"] and got["card"]["error"] is None
    year, month, _ = got["night"].split("-")
    assert got["month"] == str(int(month)) and got["year"] == str(int(year))
    assert "chart-section" in got["views"], "scrolled to the chart"


# --- the template --------------------------------------------------------------------------

def _scripts(html):
    return re.findall(r'<script[^>]*\ssrc="[^"]*/js/([^"/?]+\.js)\?v=[^"]*"[^>]*>', html)


def test_page_has_the_two_cards_between_the_moon_and_the_calculator(client):
    html = client.get("/solar_system").get_data(as_text=True)
    moon = html.index('id="solar-moon"')
    jupiter = html.index('id="solar-jupiter"')
    mars = html.index('id="solar-mars"')
    assert html.index('id="chart-section"') < moon < jupiter < mars < html.index('id="solar-calc"')
    for card in ("jupiter", "mars"):
        assert f'<div id="solar-{card}" class="content-section" hidden>' in html
        for part in ("title", "error", "body", "tiles", "diagram"):
            assert f'id="solar-{card}-{part}"' in html
        assert f'<p id="solar-{card}-error" class="status-secondary" role="alert" hidden></p>' in html
        assert f'<div id="solar-{card}-diagram" class="solar-card-diagram solar-{card}-diagram">' in html
        assert f'<div class="solar-card-views" role="group" aria-label="View"' in html
    assert html.count('class="solar-card-views"') == 2
    assert html.count('class="solar-card-view"') == 6
    assert html.count('data-card-view="normal" aria-pressed="true"') == 2
    assert ">Jupiter detail</h3>" in html and ">Mars detail</h3>" in html
    assert 'id="solar-jupiter-shadows"' in html and 'id="solar-jupiter-no-shadows"' in html
    assert 'id="solar-mars-more-list"' in html and 'id="solar-mars-credit"' in html


def test_page_loads_the_card_scripts_after_their_engines(client):
    scripts = _scripts(client.get("/solar_system").get_data(as_text=True))
    at = scripts.index
    assert at("nova_astro.js") < at("nova_planets.js") < at("nova_jupiter.js")
    assert at("nova_planets.js") < at("nova_mars.js") < at("nova_mars_features.js")
    assert at("nova_jupiter.js") < at("nova_solar_jupiter.js") < at("solar_system.js")
    assert at("nova_mars.js") < at("nova_solar_mars.js") < at("solar_system.js")
    assert at("nova_moon_features.js") < at("nova_solar_moon.js")


# --- the stylesheet ------------------------------------------------------------------------

def test_the_strips_names_are_drawn_only_where_they_can_be_read():
    """The moon names are set in the strip's own units, so their size is a fixed share of
    the card: 16 px on a laptop, 4 px on a phone. Below 750 px they are dropped rather
    than drawn unreadably -- the strip keeps the whole figure in view instead of
    scrolling a 900 px one, and the chips below name every moon in the order they lie
    along the line. N stays: one glyph, and where it sits is the message."""
    css = (STATIC_JS.parent / "css" / "solar_system.css").read_text()
    base = css.index("#bodies-tab .solar-jupiter-svg .solar-card-moon-label {")
    assert "display: none" not in css[base:css.index("}", base)]
    start = css.index("@container solar-card (max-width: 749px)")
    block = css[start:css.index("\n}\n", start)]
    assert ".solar-card-moon-label {" in block and "display: none;" in block
    assert "solar-card-north" not in block

