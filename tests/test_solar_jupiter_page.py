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
from datetime import datetime
from zoneinfo import ZoneInfo

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
        "northHelp": "NORTH TIP", "cmII": "CM II", "cmIIHelp": "CM TIP",
        "systemI": "I {deg}", "systemIII": "III {deg}", "east": "east", "west": "west",
        "onDisc": "on the disc", "inFront": "in front", "behind": "behind",
        "eclipse": "in eclipse", "rjFromCentre": "{rj} radii",
        "grs": {"none": "GRS NONE", "nextOne": "NEXT {time}", "meta": "II {lon} @ {date}", "help": "GRS HELP",
                "tonight": "TONIGHT {times}", "next": "NEXT:", "noNext": "NO NEXT",
                "source": "DEFAULT {lon} {date}", "user": "YOURS {lon} {date}",
                "drift": "DRIFT {rate}", "stale": "STALE"},
        "grsMarker": "GRS MARK", "legendVisible": "L-VISIBLE", "legendBehind": "L-BEHIND",
        "legendEclipsed": "L-ECLIPSED", "legendShadow": "L-SHADOW", "legendGrs": "L-GRS",
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
        "cm": "CM", "cmHelp": "CM TIP", "deNorth": "N-US {deg}", "deSouth": "S-US {deg}",
        "dsNorth": "N-SUN {deg}", "dsSouth": "S-SUN {deg}", "best": "BEST",
        "bestHelp": "BEST {name}", "bar": "BAR {deg}", "nextOnCm": "NEXT {time}",
        "capNorth": "CAP N", "capSouth": "CAP S",
        "credit": "SRC {source}", "diagramAria": "MARS DIAGRAM",
        "position": "{lat}/{lon}", "cmDist": "cm {deg}", "nearLimb": "LIMB",
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
        grs: { times: h.$('solar-jupiter-grs-times').textContent,
               none: h.$('solar-jupiter-grs-times').classList.contains('solar-card-grs-none'),
               upcoming: h.$('solar-jupiter-grs-times').classList.contains('solar-card-grs-upcoming'),
               meta: h.$('solar-jupiter-grs-meta').textContent,
               stale: h.$('solar-jupiter-grs').dataset.stale, title: h.$('solar-jupiter-grs').title,
               reset: !h.$('solar-jupiter-grs-reset').hidden, edit: !h.$('solar-jupiter-grs-edit').hidden,
               form: !h.$('solar-jupiter-grs-form').hidden,
               invalid: !h.$('solar-jupiter-grs-invalid').hidden },
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
            width: +svg.attrs.width, height: +svg.attrs.height, scale: +svg.attrs['data-scale'],
            legend: svg.children[0].tagName === 'title' ? svg.children[0].textContent : null,
            discs: withClass(svg, 'solar-card-disc').length,
            disc: withClass(svg, 'solar-card-disc').map(c => ({ cx: +c.attrs.cx, cy: +c.attrs.cy, r: +c.attrs.r }))[0],
            moons: withClass(svg, 'solar-card-moon').map(g => ({ moon: g.attrs['data-moon'],
                cls: g.attrs.class, cx: +g.children[0].attrs.cx, cy: +g.children[0].attrs.cy,
                r: +g.children[0].attrs.r, title: g.children[1].textContent })),
            labels: withClass(svg, 'solar-card-moon-label').map(t => ({
                text: t.textContent, moon: t.attrs['data-moon'], x: +t.attrs.x, y: +t.attrs.y })),
            grs: withClass(svg, 'solar-card-grs').map(g => ({ cx: +g.children[0].attrs.cx,
                cy: +g.children[0].attrs.cy, rx: +g.children[0].attrs.rx, title: g.children[1].textContent })),
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
    // A feature chip: its parts' classes, the name and the bar's width
    const chip = li => {
        const c = li.children;
        // The stand-in keeps classList apart from className: both, as a browser has them
        return { n: li.dataset.n || null, side: li.dataset.side,
            cls: [li.className].concat([...li.classList.set]).join(' '),
            parts: c.map(x => x.className), name: c[0].textContent, bar: c[1].children[0].style.width,
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
            note: t.children[2] ? t.children[2].textContent : null, title: t.title })),
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
            caps: withClass(svg, 'solar-card-cap').map(g => ({ pole: g.attrs['data-pole'],
                d: g.children[0].attrs.d, title: g.children[1].textContent })),
            svgNorth: withClass(svg, 'solar-card-north').length,
            // N is HTML over the drawing: its place in percent of the box
            north: h.$('solar-mars-diagram').children.filter(c => c.className === 'solar-card-north-label')
                .map(c => ({ text: c.textContent, left: parseFloat(c.style.left), top: parseFloat(c.style.top),
                             title: c.title })),
        },
    };
};
"""


# The driver's storage, with removeItem (the GRS tile's Reset), which a browser has
_STORE = "setItem: (k, v) => m.set(k, String(v)) };"
assert _DRIVER.count(_STORE) == 1
_CARDS_DRIVER = _DRIVER.replace(_STORE, "setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) };")


def _run(tmp_path, steps, search="?body=jupiter", sim=None, storage=None, order=CARDS_ORDER):
    driver = tmp_path / "driver.js"
    driver.write_text(_CARDS_DRIVER)
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


def _when(utc_ms, hm):
    """The page's local date and time of an instant in London: 'Wed 9 Dec 21:14', the
    date the event's own, the time the module's."""
    local = datetime.fromtimestamp(utc_ms / 1000, tz=ZoneInfo(LONDON["timezone"]))
    return f"{local:%a} {local.day} {local:%b} {hm}"


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
    """9 Dec 2026, London: the GRS tile and the System II tile, tonight's six phenomena and
    the next nights' shadow transits, each at its own local date and time, and the
    drawing. Every value is the module's own for the same inputs."""
    got = _run(tmp_path, _oracle("2026-12-09") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), strip: {R: J.STRIP_RJ},
                 t: { hm: t.hm, ge: t.geometry, events: t.events,
                      moons: t.moons.map(m => ({name: m.name, x: m.x, y: m.y, front: m.front,
                          onDisc: m.onDisc, eclipsed: m.eclipsed, shadow: m.shadow})) },
                 d: d, next: next.shadows };
    """, sim="2026-12-09")
    card, t, d = got["card"], got["t"], got["d"]
    assert re.fullmatch(r"JUPITER · at \d\d:\d\d", card["title"])
    assert card["title"] == "JUPITER · at " + t["hm"]
    assert not card["hidden"] and card["error"] is None and not card["muted"]

    # The tiles after the GRS tile: System II alone, System I and III in its tooltip
    tiles = card["tiles"]
    assert [tile["label"] for tile in tiles] == ["CM II"]
    assert tiles[0]["cls"] == "solar-card-tile-value" and tiles[0]["value"].endswith("°")
    assert _num(tiles[0]["value"]) == pytest.approx(t["ge"]["cm"]["II"], abs=0.5)
    assert tiles[0]["title"] == "\n".join(["CM TIP", f"I {t['ge']['cm']['I']:.0f}",
                                           f"III {t['ge']['cm']['III']:.0f}"])

    # Tonight's phenomena: the engine's own events in order, each with its own local date
    # and time (no day suffix), what it is, the moon, how long it lasts, the altitude in
    # the tooltip and the double badge
    assert [chip["type"] for chip in card["events"]] == [e["type"] for e in t["events"]]
    assert card["events"], "no phenomena to check"
    types = {"transit": "Satellite transit", "occultation": "Occultation", "eclipse": "Eclipse",
             "shadow": "Shadow transit"}
    for chip, e in zip(card["events"], t["events"]):
        assert chip["parts"][:4] == [
            {"cls": "solar-card-time", "text": _when(e["startUtcMs"], e["hm"])},
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
    assert any(e["hmDay"] for e in t["events"]), "an event after midnight, to check its date"
    for chip in card["events"] + card["shadows"]:
        assert re.fullmatch(r"\w{3} \d{1,2} \w{3} \d\d:\d\d", chip["parts"][0]["text"]), chip["parts"][0]
        assert "+" not in chip["parts"][0]["text"], "no day suffix"

    # The next nights' shadow transits, doubles merged: one chip per row, its own date and
    # time, the moons that pair up, the length, and the tooltip's own time
    assert got["next"] and len(card["shadows"]) == len(got["next"])
    for chip, s in zip(card["shadows"], got["next"]):
        when = _when(s["utcMs"], s["hm"])
        assert chip["type"] == "shadow"
        assert chip["parts"] == (
            [{"cls": "solar-card-time", "text": when},
             {"cls": "solar-card-name", "text": " + ".join(m.capitalize() for m in s["moons"])},
             {"cls": "solar-card-note", "text": f"{s['minutes']} min"}]
            + ([{"cls": "solar-card-badge", "text": "double"}] if s["double"] else []))
        assert chip["title"].startswith("shadow at " + when)
    assert card["noShadows"] is None and card["empty"] is None

    # The drawing: the moons' symbols by what each is doing, a black dot per shadow, the
    # legend in the drawing's own tooltip, each moon's place in its own
    svg = card["svg"]
    assert svg["label"] == "JUP DIAGRAM" and svg["discs"] == 1
    assert svg["legend"].split("\n") == ["JUP DIAGRAM", "L-VISIBLE", "L-BEHIND", "L-ECLIPSED",
                                         "L-SHADOW", "L-GRS"]
    k, cx, cy = svg["scale"], svg["width"] / 2, svg["height"] / 2
    assert [dot["moon"] for dot in svg["moons"]] == [m["name"] for m in t["moons"]]
    for dot, m, dm in zip(svg["moons"], t["moons"], d["moons"]):
        assert dot["cx"] == pytest.approx(cx + dm["x"] * k, abs=0.02)
        assert dot["cy"] == pytest.approx(cy + dm["y"] * k, abs=0.02)
        assert dot["cls"] == "solar-card-moon solar-card-moon-" + dm["state"]
        place = ["east" if m["x"] >= 0 else "west",
                 "on the disc" if m["onDisc"] else "in front" if m["front"] else "behind"]
        if m["eclipsed"]:
            place.append("in eclipse")
        assert dot["title"].split("\n") == [m["name"].capitalize(), " · ".join(place),
                                            f"{math.hypot(m['x'], m['y']):.1f} radii"]
    assert sorted(label["text"] for label in svg["labels"]) == ["Callisto", "Europa", "Ganymede", "Io"]

    spots = [(m, s) for m, s in zip(t["moons"], d["moons"]) if s["shadow"]]
    assert spots, "no shadow on the disc to check"
    assert [spot["moon"] for spot in svg["shadows"]] == [m["name"] for m, _ in spots]
    for spot, (m, s) in zip(svg["shadows"], spots):
        assert spot["cx"] == pytest.approx(cx + s["shadow"]["x"] * k, abs=0.02)
        assert spot["cy"] == pytest.approx(cy + s["shadow"]["y"] * k, abs=0.02)
        assert math.hypot(spot["cx"] - cx, spot["cy"] - cy) <= k + 0.02, "on the disc"
        assert spot["title"] == m["name"].capitalize() + " — SHADOW"

    north = svg["north"]
    assert len(north) == 1 and north[0]["text"] == "N" and north[0]["title"] == "NORTH TIP"
    # 14 px outside the disc, along celestial north
    assert north[0]["x"] == pytest.approx(cx + d["north"]["x"] * (k + 14), abs=0.02)
    assert north[0]["y"] == pytest.approx(cy + d["north"]["y"] * (k + 14), abs=0.02)


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
    assert len(low["card"]["svg"]["moons"]) == 4 and low["card"]["events"] == []
    assert low["card"]["noShadows"] == "NO SHADOWS", "not one shadow transit in the fortnight"

    up = _run(tmp_path, _oracle("2027-09-01") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), maxAlt: t.maxAlt, reason: t.mutedReason };
    """, sim="2027-09-01")
    assert up["reason"] == "notUp" and up["maxAlt"] < 0
    assert up["card"]["muted"] and up["card"]["empty"] == "NOTUP"
    assert len(up["card"]["svg"]["moons"]) == 4 and up["card"]["events"] == []

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
    # In pixels about the disc's centre, which stays in the middle of the strip
    cx, cy = got["normal"]["svg"]["width"] / 2, got["normal"]["svg"]["height"] / 2
    rel = lambda card: {dot["moon"]: (dot["cx"] - cx, dot["cy"] - cy) for dot in card["svg"]["moons"]}
    normal, diagonal, inverted = rel(got["normal"]), rel(got["diagonal"]), rel(got["inverted"]["card"])
    for moon, (x, y) in diagonal.items():
        assert x == pytest.approx(-normal[moon][0], abs=0.02)
        assert y == pytest.approx(normal[moon][1], abs=0.02)
    for moon, (x, y) in inverted.items():
        assert x == pytest.approx(-normal[moon][0], abs=0.02)
        assert y == pytest.approx(-normal[moon][1], abs=0.02)
    assert got["diagonal"]["svg"]["north"][0]["x"] - cx == pytest.approx(
        -(got["normal"]["svg"]["north"][0]["x"] - cx), abs=0.02)


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


# --- the Great Red Spot -------------------------------------------------------------------

_GRS = """
const SJ = h.context.NovaSolarJupiter;
const gopts = {inputs: {location: %(site)s}, nowMs: NOW_MS, simDate: '%(sim)s'};
const grsOf = o => { const g = SJ.grs(gopts, o);
    return { tonight: g.tonight.map(x => ({hm: x.hm, utcMs: x.utcMs})),
             next: g.next.map(x => ({hm: x.hm, utcMs: x.utcMs})), stale: g.stale,
             refLon: g.refLon, refDate: g.refDate }; };
"""


def _grs(sim):
    return _GRS % {"site": SITE, "sim": sim}


def test_grs_tile_shows_tonights_transit_and_the_next_nights(tmp_path):
    """7 Nov 2026, London: the spot crosses the central meridian at 03:14 with Jupiter up.
    The tile shows that time large, the longitude and its date under it, the next nights'
    transits at their own date and time in its tooltip; the spot is on the drawing while it
    faces us. The built-in value is five months old: no warning."""
    got = _run(tmp_path, _oracle("2026-11-07") + _grs("2026-11-07") + """
        h.online();
        await h.load('bodies');
        const ref = SJ.grsReference(null);
        return { card: jupCard(), g: grsOf(null),
                 marker: SJ.diagram(t.geometry, 'normal', t.moons, SJ.grsLongitude(ref, t.instantUtc)).grs };
    """, sim="2026-11-07")
    card, g = got["card"], got["g"]
    assert [x["hm"] for x in g["tonight"]] == ["03:14"]
    tile = card["grs"]
    assert tile["times"] == "03:14" and not tile["none"]
    assert tile["meta"] == "II 91.0 @ 1 Jun 2026"
    assert tile["stale"] == "false" and not tile["reset"] and tile["edit"] and not tile["form"]
    lines = tile["title"].split("\n")
    assert lines[0] == "GRS HELP"
    assert lines[1] == "TONIGHT " + _when(g["tonight"][0]["utcMs"], "03:14")
    assert lines[2] == "NEXT:"
    assert lines[3:3 + len(g["next"])] == [_when(x["utcMs"], x["hm"]) for x in g["next"]]
    assert lines[3 + len(g["next"]):] == ["DEFAULT 91.0 1 Jun 2026", "DRIFT +1.75"]
    assert "STALE" not in lines
    # The spot on the drawing when it faces us at the card's instant, nowhere else
    marks = card["svg"]["grs"]
    if got["marker"]["visible"]:
        k, cx, cy = card["svg"]["scale"], card["svg"]["width"] / 2, card["svg"]["height"] / 2
        assert len(marks) == 1 and marks[0]["title"] == "GRS MARK"
        assert marks[0]["cx"] == pytest.approx(cx + got["marker"]["x"] * k, abs=0.02)
        assert marks[0]["cy"] == pytest.approx(cy + got["marker"]["y"] * k, abs=0.02)
    else:
        assert marks == []


def test_grs_tile_warns_when_the_longitude_is_old(tmp_path):
    """16 Dec 2026: the built-in longitude is more than six months old. The tile keeps the
    prediction and takes the warning state (the warning colour, in the stylesheet), and the
    tooltip says why."""
    old = _run(tmp_path, _grs("2026-12-16") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), g: grsOf(null) };
    """, sim="2026-12-16")
    tile = old["card"]["grs"]
    assert old["g"]["stale"] and tile["stale"] == "true"
    assert tile["times"] == " · ".join(x["hm"] for x in old["g"]["tonight"]) == "00:30"
    assert tile["title"].split("\n")[-1] == "STALE"


    css = (STATIC_JS.parent / "css" / "solar_system.css").read_text()
    rule = css.index('#bodies-tab .solar-card-grs[data-stale="true"] .solar-card-grs-meta {')
    assert "color: #94600f;" in css[rule:css.index("}", rule)], "the seeing tiles' warning colour"


def test_grs_tile_shows_the_next_transit_when_none_is_tonight(tmp_path):
    """9 Dec 2026, London: no transit while Jupiter is up tonight. The tile shows the next
    one in tonight's place, as large, at its own local date and time and marked as next;
    only when the next nights have none either does it say so (late July 2026, Jupiter at
    conjunction with the Sun)."""
    got = _run(tmp_path, _grs("2026-12-09") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), g: grsOf(null) };
    """, sim="2026-12-09")
    g, tile = got["g"], got["card"]["grs"]
    assert g["tonight"] == [] and g["next"], "a night without a transit, with transits after it"
    first = g["next"][0]
    assert tile["times"] == "NEXT " + _when(first["utcMs"], first["hm"])
    assert tile["upcoming"] and not tile["none"], "styled as tonight's times, marked as next"
    assert tile["stale"] == "true"

    quiet = _run(tmp_path, _grs("2026-07-29") + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), g: grsOf(null) };
    """, sim="2026-07-29")
    assert quiet["g"]["tonight"] == [] and quiet["g"]["next"] == []
    assert quiet["card"]["grs"]["times"] == "GRS NONE"
    assert quiet["card"]["grs"]["none"] and not quiet["card"]["grs"]["upcoming"]


def test_grs_edit_and_reset_are_kept(tmp_path):
    """Edit opens the form with the current value; a bad value is refused and nothing is
    kept; Save keeps the user's longitude under nova_solar_grs and the transits move with
    it; the next visit starts from it; Reset goes back to the built-in value."""
    got = _run(tmp_path, _grs("2026-11-07") + """
        h.online();
        await h.load('bodies');
        const out = { before: jupCard().grs };
        await h.$('solar-jupiter-grs-edit').fire('click');
        out.open = { card: jupCard().grs, lon: h.$('solar-jupiter-grs-lon').value,
                     date: h.$('solar-jupiter-grs-date').value };
        h.$('solar-jupiter-grs-lon').value = '400';
        await h.$('solar-jupiter-grs-save').fire('click');
        out.refused = { card: jupCard().grs, stored: h.stored('nova_solar_grs') };
        h.$('solar-jupiter-grs-lon').value = '120,5';
        h.$('solar-jupiter-grs-date').value = '2026-11-01';
        await h.$('solar-jupiter-grs-save').fire('click');
        out.saved = { card: jupCard().grs, stored: h.stored('nova_solar_grs'),
                      g: grsOf({lon: 120.5, date: '2026-11-01'}) };
        await h.$('solar-jupiter-grs-reset').fire('click');
        out.reset = { card: jupCard().grs, stored: h.stored('nova_solar_grs') };
        return out;
    """, sim="2026-11-07")
    assert got["before"]["times"] == "03:14"
    assert got["open"]["card"]["form"] and not got["open"]["card"]["edit"]
    assert (got["open"]["lon"], got["open"]["date"]) == ("91.0", "2026-06-01")
    assert got["refused"]["card"]["invalid"] and got["refused"]["card"]["form"]
    assert got["refused"]["stored"] is None
    saved = got["saved"]
    assert json.loads(saved["stored"]) == {"lon": 120.5, "date": "2026-11-01"}
    assert not saved["card"]["form"] and saved["card"]["edit"] and saved["card"]["reset"]
    assert saved["card"]["meta"] == "II 120.5 @ 1 Nov 2026"
    assert saved["card"]["times"] == " · ".join(x["hm"] for x in saved["g"]["tonight"])
    assert saved["card"]["times"] != "03:14", "the transits move with the longitude"
    assert "YOURS 120.5 1 Nov 2026" in saved["card"]["title"].split("\n")
    assert got["reset"]["stored"] is None and not got["reset"]["card"]["reset"]
    assert got["reset"]["card"]["times"] == "03:14"
    assert got["reset"]["card"]["meta"] == "II 91.0 @ 1 Jun 2026"

    # The next visit: the kept value is the one the card starts from
    again = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        return jupCard().grs;
    """, sim="2026-11-07", storage={"nova_solar_grs": json.dumps({"lon": 120.5, "date": "2026-11-01"})})
    assert again["meta"] == "II 120.5 @ 1 Nov 2026" and again["reset"]
    assert again["times"] == saved["card"]["times"]

    # A kept value that is not usable is ignored
    broken = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        return jupCard().grs;
    """, sim="2026-11-07", storage={"nova_solar_grs": "{not json"})
    assert broken["meta"] == "II 91.0 @ 1 Jun 2026" and not broken["reset"]


def test_grs_tile_works_with_blocked_storage(tmp_path):
    """Site data blocked: every read and write throws. The card draws with the built-in
    value, and an edit still holds for the visit."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { before: jupCard() };
        await h.$('solar-jupiter-grs-edit').fire('click');
        h.$('solar-jupiter-grs-lon').value = '200';
        h.$('solar-jupiter-grs-date').value = '2026-10-20';
        await h.$('solar-jupiter-grs-save').fire('click');
        out.saved = jupCard();
        await h.$('solar-jupiter-grs-reset').fire('click');
        out.reset = jupCard();
        return out;
    """, search="?body=jupiter", storage="throws")
    before = got["before"]
    assert not before["hidden"] and before["error"] is None
    assert before["grs"]["meta"] == "II 91.0 @ 1 Jun 2026" and not before["grs"]["reset"]
    assert got["saved"]["error"] is None and got["saved"]["grs"]["meta"] == "II 200.0 @ 20 Oct 2026"
    assert got["saved"]["grs"]["reset"] and not got["saved"]["grs"]["form"]
    assert got["reset"]["grs"]["meta"] == "II 91.0 @ 1 Jun 2026"


# --- the drawing ---------------------------------------------------------------------------

def _boxes(labels):
    """The page's own estimate of each label's box (JUP_CHAR_PX, JUP_LABEL_PX)."""
    return [(lb["x"] - (len(lb["text"]) * 7 + 4) / 2, lb["x"] + (len(lb["text"]) * 7 + 4) / 2,
             lb["y"] - 7, lb["y"] + 7) for lb in labels]


def test_jupiter_is_centred_at_one_fixed_scale(tmp_path):
    """Every night the same: the disc in the middle of a 160 px strip, STRIP_RJ Jupiter
    radii each side, so Callisto at its greatest elongation still fits; the names never
    overlap one another or the disc, and stay inside the strip."""
    cards = {}
    for sim in ("2026-11-07", "2026-11-14", "2026-12-09", "2026-12-16"):
        cards[sim] = _run(tmp_path, """
            h.online();
            await h.load('bodies');
            return { card: jupCard(), R: h.context.NovaSolarJupiter.STRIP_RJ };
        """, sim=sim)
    for sim, got in cards.items():
        svg, R = got["card"]["svg"], got["R"]
        W, H = svg["width"], svg["height"]
        assert R >= 26.5, "wide enough for Callisto's greatest elongation"
        assert (W, H) == (720, 160), "the stand-in has no layout: the default width"
        assert svg["viewBox"] == f"0 0 {W} {H}"
        assert svg["scale"] == pytest.approx((W / 2 - 8) / R, abs=1e-6)
        assert svg["disc"] == {"cx": W / 2, "cy": H / 2, "r": round(svg["scale"], 2)}
        assert all(0 <= dot["cx"] <= W and 0 <= dot["cy"] <= H for dot in svg["moons"])
        labels = svg["labels"]
        assert sorted(lb["moon"] for lb in labels) == ["callisto", "europa", "ganymede", "io"]
        boxes = _boxes(labels)
        disc = (W / 2 - svg["scale"], W / 2 + svg["scale"], H / 2 - svg["scale"], H / 2 + svg["scale"])
        for i, a in enumerate(boxes):
            assert a[0] >= 0 and a[1] <= W and a[2] >= 0 and a[3] <= H, f"{sim}: outside the strip"
            assert not (a[0] < disc[1] and disc[0] < a[1] and a[2] < disc[3] and disc[2] < a[3]), \
                f"{sim}: a name over the disc"
            for b in boxes[i + 1:]:
                assert not (a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]), \
                    f"{sim}: two names overlap: {labels}"
    scales = {got["card"]["svg"]["scale"] for got in cards.values()}
    assert len(scales) == 1, "one scale, whatever the night"


def test_moon_symbols_follow_each_moons_state(tmp_path):
    """A filled light dot beside or in front of the disc, a dashed ring behind it, a faded
    ring in eclipse: the class is the module's state for that moon, and the stylesheet
    draws each one so. Shadows are the only black."""
    seen = set()
    for sim in ("2026-11-07", "2026-11-14"):
        got = _run(tmp_path, _oracle(sim) + """
            h.online();
            await h.load('bodies');
            return { card: jupCard(), states: d.moons.map(m => [m.name, m.state]) };
        """, sim=sim)
        classes = {dot["moon"]: dot["cls"] for dot in got["card"]["svg"]["moons"]}
        for name, state in got["states"]:
            assert classes[name] == "solar-card-moon solar-card-moon-" + state
            seen.add(state)
    assert seen == {"visible", "behind", "eclipsed"}
    css = (STATIC_JS.parent / "css" / "solar_system.css").read_text()
    rule = lambda sel: css[css.index(sel + " {"):css.index("}", css.index(sel + " {"))]
    visible = rule("#bodies-tab .solar-jupiter-svg .solar-card-moon-visible circle")
    behind = rule("#bodies-tab .solar-jupiter-svg .solar-card-moon-behind circle")
    eclipsed = rule("#bodies-tab .solar-jupiter-svg .solar-card-moon-eclipsed circle")
    assert "fill: var(--solar-body-moon);" in visible
    assert "fill: none;" in behind and "stroke-dasharray" in behind
    assert "fill: none;" in eclipsed and "var(--text-faint)" in eclipsed
    assert "fill: #000;" in rule("#bodies-tab .solar-card-shadow circle")
    assert css.count("#000;") == 1, "black is only ever a shadow"


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

def test_the_strips_names_are_a_fixed_size_at_every_width():
    """The strip is drawn in pixels at the width it is shown, so its names and N are set
    at a fixed 12 px and never hidden: no rule scales them with the drawing or drops them
    on a narrow card."""
    css = (STATIC_JS.parent / "css" / "solar_system.css").read_text()
    base = css.index("#bodies-tab .solar-jupiter-svg .solar-card-moon-label,\n"
                     "#bodies-tab .solar-jupiter-svg .solar-card-north {")
    assert "font-size: 12px;" in css[base:css.index("}", base)]
    assert "@container solar-card (max-width: 749px)" not in css
    assert not re.search(r"solar-card-moon-label[^{]*\{[^}]*display:\s*none", css)
    height = css.index("#bodies-tab #solar-jupiter-diagram {")
    assert "height: 160px;" in css[height:css.index("}", height)]


# --- dates in the page's language ----------------------------------------------------------

_DE_WHEN = re.compile(r"(Mo|Di|Mi|Do|Fr|Sa|So)\., \d{1,2}\. \S+ \d\d:\d\d")
_JA_WHEN = re.compile(r"\d{1,2}月\d{1,2}日\([月火水木金土日]\) \d\d:\d\d")


@pytest.mark.parametrize("lang, when, ref, tonight", [
    ("de", _DE_WHEN, "1. Juni 2026", "So., 8. Nov. 03:14"),
    ("ja", _JA_WHEN, "2026年6月1日", "11月8日(日) 03:14"),
])
def test_card_dates_follow_the_pages_language(tmp_path, lang, when, ref, tonight):
    """<html lang> is Flask-Babel's locale; every date on the Jupiter and Mars cards and
    the GRS tile is written in it: the phenomena and shadow chips, the GRS transits and the
    longitude's date, the Mars features' next passage."""
    set_lang = "h.context.document.documentElement.lang = '%s';\n" % lang
    jup = _run(tmp_path, _grs("2026-11-07") + set_lang + """
        h.online();
        await h.load('bodies');
        return { card: jupCard(), g: grsOf(null) };
    """, sim="2026-11-07")
    card = jup["card"]
    assert card["grs"]["meta"] == "II 91.0 @ " + ref
    lines = card["grs"]["title"].split("\n")
    assert lines[1] == "TONIGHT " + tonight
    nexts = lines[3:3 + len(jup["g"]["next"])]
    assert nexts and all(when.fullmatch(x) for x in nexts), nexts
    assert lines[3 + len(nexts)] == "DEFAULT 91.0 " + ref
    chips = [p["text"] for li in card["events"] + card["shadows"]
             for p in li["parts"] if p["cls"] == "solar-card-time"]
    assert chips and all(when.fullmatch(x) for x in chips), chips

    mars = _run(tmp_path, set_lang + """
        h.online();
        await h.load('bodies');
        return marsCard();
    """, search="?body=mars", sim="2026-11-07")
    passages = [line[len("NEXT "):] for f in mars["features"] for line in f["title"].split("\n")
                if line.startswith("NEXT ")]
    assert passages and all(when.fullmatch(x) for x in passages), passages
