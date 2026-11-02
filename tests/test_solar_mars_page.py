"""The Solar System page's Mars card, in node.

Runs static/js/solar_system.js with the real engines and the stand-in DOM of
tests/test_solar_system_js.py (its driver, unchanged), with the Moon's and both new
cards' modules loaded too, as the template loads them. The harness -- the cards' view
buttons and the snapshots -- lives in test_solar_jupiter_page.py, which this imports;
what the page puts in the DOM is checked against the module's own output for the same
inputs (the module itself is pinned to the engines in tests/test_solar_mars.py).
"""

import re

import pytest

from test_solar_jupiter_page import SITE, _run, _when  # noqa: F401 (SITE: the case templates)

# The module's own answer for the same inputs the page uses, with the disc's own view
_MARS = """
const M = h.context.NovaSolarMars;
const opts = {inputs: {location: %(site)s}, nowMs: NOW_MS, simDate: '%(sim)s'};
const t = M.tonight(opts);
const d = M.diagram(t.geometry, 'normal', t.top);
const source = h.context.NovaMarsFeatures.SOURCE;
"""


def _mars(sim):
    return _MARS % {"site": SITE, "sim": sim}


def test_mars_card_only_while_mars_is_selected(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { mars: marsCard() };
        const rows = h.$('solar-body').children;
        await h.clickRow('solar-body', rows.findIndex(tr => tr.dataset.body === 'mars'));
        out.after = marsCard();
        return out;
    """, search="?body=jupiter", sim="2026-11-17")
    assert got["mars"]["hidden"]
    assert not got["after"]["hidden"] and got["after"]["error"] is None
    assert len(got["after"]["tiles"]) == 2 and got["after"]["svg"]["surface"] == 1


def test_mars_card_shows_the_disc_the_features_and_the_source(tmp_path):
    """17 Nov 2026, London: the CM tile (De and Ds in words in its tooltip) and Best
    tonight, the eight features an imager identifies first -- each chip the name and a bar
    for how close it stands to the central meridian, the rest in its tooltip -- the rest
    behind "More features", and the disc drawn with Mars's north up, its polar cap
    included, and an arrow to celestial north.
    Every value is the module's own for the same inputs."""
    got = _run(tmp_path, _mars("2026-11-17") + """
        h.online();
        await h.load('bodies');
        return { card: marsCard(),
                 t: { hm: t.hm, ge: t.geometry, best: t.best,
                      features: t.features.length, top: t.top.map(f => ({name: f.name,
                          type: f.type, cmDist: f.cmDist, diameterKm: f.diameterKm,
                          nearLimb: f.nearLimb, nextUtcMs: f.nextUtcMs, nextHm: f.nextHm,
                          cm: Math.abs(Math.round(f.cmDist)),
                          bar: Math.round(Math.max(0, 1 - Math.abs(f.cmDist) /
                              M.NEAR_LIMB_DEG) * 100) + '%'})),
                      more: t.more.map(f => f.name), empty: t.empty },
                 d: d, source: source.credit, constants: {top: M.TOP_COUNT, limb: M.NEAR_LIMB_DEG} };
    """, search="?body=mars", sim="2026-11-17")
    card, t, d = got["card"], got["t"], got["d"]
    assert card["title"] == "MARS · at " + t["hm"]
    assert not card["hidden"] and card["error"] is None and not card["muted"]

    # The tiles: the central meridian, De and Ds in words in its tooltip; Best tonight
    tiles = card["tiles"]
    ge = t["ge"]
    assert [tile["label"] for tile in tiles] == ["CM", "BEST"]
    assert tiles[0]["value"] == f"{ge['cm']:.0f}°"
    words = lambda deg, north, south: f"{north if deg >= 0 else south} {abs(round(deg))}"
    assert tiles[0]["title"].split("\n") == ["CM TIP", words(ge["de"], "N-US", "S-US"),
                                             words(ge["ds"], "N-SUN", "S-SUN")]
    assert ge["de"] > 0 and "N-US" in tiles[0]["title"]
    best = t["best"]
    assert best and best["name"] == t["top"][0]["name"]
    assert tiles[1]["value"] == best["hm"] and tiles[1]["note"] == best["name"]
    assert tiles[1]["title"] == "BEST " + best["name"]

    # The features: the module's own top eight, in its order, each chip the name and the
    # bar only; the type, the place, what the bar means and the next passage in its
    # tooltip; the rest behind "More features"
    features = card["features"]
    assert len(features) == got["constants"]["top"] == 8
    assert [f["n"] for f in features] == [str(i + 1) for i in range(8)]
    for chip, f in zip(features, t["top"]):
        assert chip["parts"] == ["solar-card-name", "solar-card-bar"]
        assert chip["name"] == f["name"]
        assert chip["bar"] == f["bar"], "the bar's width is the distance from the meridian"
        assert chip["side"] == ("east" if f["cmDist"] >= 0 else "west")
        assert chip["cls"].split(" ")[0] == "solar-card-item"
        assert ("solar-card-feature-limb" in chip["cls"]) == f["nearLimb"]
        assert chip["focusable"] == "0", "reachable by keyboard, for its marker"
        assert chip["label"] == ", ".join([f["name"], "Albedo", f"cm {f['cm']}"])
        lines = chip["title"].split("\n")
        assert lines[0] == "Albedo" and "/" in lines[1] and lines[2] == f"BAR {f['cm']}"
        assert ("LIMB" in lines) == f["nearLimb"]
        assert lines[-1] == "NEXT " + _when(f["nextUtcMs"], f["nextHm"])
        assert "+" not in lines[-1].split(" ", 1)[1].split(" ")[-1], "no day suffix"
    assert [f["name"] for f in features] == [f["name"] for f in t["top"]]
    assert card["more"] == len(t["more"]) > 0 and not card["moreHidden"]
    assert [f["name"] for f in card["moreList"]] == t["more"]
    assert [f["n"] for f in card["moreList"]] == [None] * len(t["more"])
    assert card["moreList"][0]["bar"] and card["moreList"][0]["parts"] == ["solar-card-name", "solar-card-bar"]

    # The credit line: the gazetteer the names come from
    assert card["credit"] == "SRC " + got["source"]

    # The drawing: the disc, the polar cap tilted towards us, the dark part and its
    # terminator, a dot per top feature, the pole straight up, and the arrow to celestial
    # north with its N in HTML over the drawing
    svg = card["svg"]
    assert svg["label"] == "MARS DIAGRAM"
    assert [c["pole"] for c in svg["caps"]] == [c["pole"] for c in d["caps"]] == ["north"]
    assert svg["caps"][0]["d"] == d["caps"][0]["d"] and svg["caps"][0]["title"] == "CAP N"
    assert svg["shaded"] == (1 if d["shaded"] else 0)
    assert svg["terminator"] == (1 if d["terminator"] else 0)
    assert [m["n"] for m in svg["markers"]] == [str(m["n"]) for m in d["markers"]]
    for marker, m in zip(svg["markers"], d["markers"]):
        assert marker["cx"] == pytest.approx(m["x"], abs=1e-6)
        assert marker["cy"] == pytest.approx(m["y"], abs=1e-6)
        assert marker["r"] == 0.034 and marker["title"] == m["name"]
        assert abs(marker["cx"]) <= 1 and abs(marker["cy"]) <= 1, "inside the disc"
    assert len(svg["pole"]) == 1
    assert svg["pole"][0]["d"].startswith("M ") and svg["pole"][0]["d"].count(" ") == 5
    assert svg["pole"][0]["x"] == pytest.approx(float(f"{d['pole']['x']:.3f}"), abs=1e-9)
    assert svg["pole"][0]["y"] == pytest.approx(float(f"{d['pole']['y']:.3f}"), abs=1e-9)
    assert svg["pole"][0]["title"] == words(d["tilt"], "N-US", "S-US")
    assert (d["pole"]["x"], d["pole"]["y"]) == (0, -1), "Mars's north up"
    assert svg["svgNorth"] == 0, "N is not drawn in the SVG, where it would scale"
    assert len(svg["north"]) == 1 and svg["north"][0]["text"] == "N"
    assert svg["north"][0]["title"] == "CELESTIAL N"
    assert svg["north"][0]["left"] == pytest.approx((d["north"]["x"] * 1.24 + 1.3) / 2.6 * 100, abs=0.01)
    assert svg["north"][0]["top"] == pytest.approx((d["north"]["y"] * 1.24 + 1.3) / 2.6 * 100, abs=0.01)
    # The arrow: from just outside the limb to its tip along celestial north, two barbs
    assert len(svg["arrow"]) == 1 and svg["arrow"][0]["title"] == "CELESTIAL N"
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", svg["arrow"][0]["d"])]
    pts = [tuple(nums[i:i + 2]) for i in range(0, len(nums), 2)]
    assert len(pts) == 5
    assert pts[0] == pytest.approx((d["north"]["x"] * 1.03, d["north"]["y"] * 1.03), abs=1e-3)
    tip = (d["north"]["x"] * 1.15, d["north"]["y"] * 1.15)
    assert pts[1] == pytest.approx(tip, abs=1e-3) and pts[3] == pytest.approx(tip, abs=1e-3)
    assert abs(d["north"]["x"]) > 0.05, "the night's pole angle turns north off the vertical"


def test_mars_card_rings_the_feature_under_the_pointer(tmp_path):
    """The chips carry only the name, so the dot of the chip under the pointer or the
    keyboard is ringed on the drawing, over everything -- and only one at a time."""
    got = _run(tmp_path, _mars("2026-11-17") + """
        h.online();
        await h.load('bodies');
        const svg = svgOf('solar-mars-diagram');
        const chips = h.$('solar-mars-features').children;
        const out = { before: marsCard().hot, thirdN: marsCard().features[2].n,
                      firstN: marsCard().features[0].n,
                      markers: d.markers.map(m => ({ n: String(m.n), x: m.x, y: m.y })) };
        await chips[2].fire('mouseenter');
        out.third = marsCard().hot;
        await chips[2].fire('mouseleave');
        out.after = marsCard().hot;
        await chips[0].fire('focus');
        out.first = marsCard().hot;
        await chips[0].fire('blur');
        out.blurred = marsCard().hot;
        out.sameSvg = svgOf('solar-mars-diagram') === svg && chips.length === 8;
        return out;
    """, search="?body=mars", sim="2026-11-17")
    assert got["before"] == [] and got["after"] == [] and got["blurred"] == []
    assert got["sameSvg"]
    dot = {m["n"]: m for m in got["markers"]}
    for hot, n in ((got["third"], got["thirdN"]), (got["first"], got["firstN"])):
        assert len(hot) == 1, hot
        circle = hot[0]
        assert circle["tag"] == "circle" and circle["text"] == ""
        assert float(circle["r"]) == 0.07
        assert float(circle["cx"]) == pytest.approx(dot[n]["x"], abs=1e-9)
        assert float(circle["cy"]) == pytest.approx(dot[n]["y"], abs=1e-9)


def test_mars_card_is_muted_when_mars_is_low(tmp_path):
    """1 Nov 2027: Mars sets in the evening, seven degrees up at best. The card still
    names the features on the disc that night, muted, and says why."""
    got = _run(tmp_path, _mars("2027-11-01") + """
        h.online();
        await h.load('bodies');
        return { card: marsCard(), maxAlt: t.maxAlt, threshold: t.threshold, reason: t.mutedReason };
    """, search="?body=mars", sim="2027-11-01")
    assert got["reason"] == "altitude" and got["maxAlt"] < got["threshold"] == 20
    assert got["card"]["muted"] and got["card"]["empty"] == "BELOW"
    assert got["card"]["features"] and got["card"]["svg"]["markers"]
    # Never above the threshold: no best time, and the tile says why
    best = got["card"]["tiles"][1]
    assert best["label"] == "BEST" and best["value"] == "–" and best["note"] is None
    assert best["title"] == "BELOW"


def test_mars_card_error_leaves_the_rest_of_the_page(tmp_path):
    """A failure while computing the card: the card's own line, and the rest of the page --
    the bodies table, the charts, the Calculator -- untouched."""
    got = _run(tmp_path, """
        h.online();
        h.context.NovaSolarMars.tonight = () => { throw new Error('boom'); };
        await h.load('bodies');
        const out = { card: marsCard() };
        const rows = h.$('solar-body').children;
        await h.clickRow('solar-body', rows.findIndex(tr => tr.dataset.body === 'jupiter'));
        out.jupiter = marsCard();
        await h.clickRow('solar-body', rows.findIndex(tr => tr.dataset.body === 'mars'));
        out.back = marsCard();
        out.snap = h.snap();
        out.calc = h.$('solar-calc').hidden;
        return out;
    """, search="?body=mars", sim="2026-11-17")
    assert got["card"]["error"] == "MARS ERROR" and got["card"]["bodyHidden"]
    assert got["card"]["title"] == "MARS" and not got["card"]["hidden"]
    assert got["jupiter"]["hidden"], "hidden while another body is selected"
    assert got["back"]["error"] == "MARS ERROR" and got["back"]["bodyHidden"]
    assert got["snap"]["solarError"] is None and got["snap"]["bodyRows"] > 0
    assert got["snap"]["bodiesShown"] and not got["calc"]


def test_mars_card_is_computed_once_a_night(tmp_path):
    """The 60 s tick and a view change redraw but do not recompute: the card is keyed on the
    night, the location, the threshold and the sampling interval. A new night recomputes."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const M = h.context.NovaSolarMars;
        const calls = { tonight: 0 };
        const tonight = M.tonight;
        M.tonight = function () { calls.tonight++; return tonight.apply(this, arguments); };
        const svg = svgOf('solar-mars-diagram');
        await h.tick();
        await h.tick();
        const after = { tonight: calls.tonight, sameSvg: svgOf('solar-mars-diagram') === svg };
        await h.$('mars-view-inverted').fire('click');
        after.redrawn = svgOf('solar-mars-diagram') !== svg;
        after.pressed = marsCard().pressed;
        h.$('sim-date-input').value = '2026-12-20';
        await h.tick();
        return { after: after, calls: calls };
    """, search="?body=mars", sim="2026-11-17")
    assert got["after"] == {"tonight": 0, "sameSvg": True, "redrawn": True, "pressed": ["inverted"]}
    assert got["calls"] == {"tonight": 1}, "a new night recomputes the card"


def test_the_mars_disc_mirrors_in_the_shared_view(tmp_path):
    """The Moon's own buttons switch all three cards: the disc turns over with them -- the
    markers left-right in a star diagonal, both ways in a Newtonian -- and the pole with it."""
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { normal: marsCard() };
        await h.$('moon-view-diagonal').fire('click');
        out.diagonal = marsCard();
        await h.$('moon-view-inverted').fire('click');
        out.inverted = marsCard();
        out.pressed = pressed(moonButtons);
        out.stored = h.stored('nova_solar_view');
        return out;
    """, search="?body=mars", sim="2026-11-17")
    assert got["pressed"] == ["inverted"] and got["stored"] == "inverted"
    assert got["normal"]["pressed"] == ["normal"] and got["diagonal"]["pressed"] == ["diagonal"]
    normal = list(zip([m["n"] for m in got["normal"]["svg"]["markers"]],
                      [m["cx"] for m in got["normal"]["svg"]["markers"]],
                      [m["cy"] for m in got["normal"]["svg"]["markers"]]))
    for (n, cx, cy) in normal:
        diagonal = [m for m in got["diagonal"]["svg"]["markers"] if m["n"] == n][0]
        inverted = [m for m in got["inverted"]["svg"]["markers"] if m["n"] == n][0]
        assert diagonal["cx"] == pytest.approx(-cx, abs=1e-6) and diagonal["cy"] == pytest.approx(cy, abs=1e-6)
        assert inverted["cx"] == pytest.approx(-cx, abs=1e-6) and inverted["cy"] == pytest.approx(-cy, abs=1e-6)
    # The pole's tick, the N beside it, and the dark part, mirror with them
    pole = got["normal"]["svg"]["pole"][0]
    assert got["diagonal"]["svg"]["pole"][0]["x"] == pytest.approx(-pole["x"], abs=1e-6)
    assert got["inverted"]["svg"]["pole"][0]["y"] == pytest.approx(-pole["y"], abs=1e-6)
    # N and its arrow, in percent of the box: mirrored about its middle
    north = got["normal"]["svg"]["north"][0]
    assert got["diagonal"]["svg"]["north"][0]["left"] == pytest.approx(100 - north["left"], abs=0.02)
    assert got["inverted"]["svg"]["north"][0]["top"] == pytest.approx(100 - north["top"], abs=0.02)
    assert got["normal"]["svg"]["shaded"] == got["inverted"]["svg"]["shaded"] == 1
    assert got["normal"]["credit"] == got["inverted"]["credit"]
