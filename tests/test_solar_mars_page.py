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

from test_solar_jupiter_page import SITE, _num, _run  # noqa: F401 (SITE: the case templates)

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


def _signed(x, digits=1):
    return ("−" if x < 0 else "+") + f"{abs(x):.{digits}f}"


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
    assert len(got["after"]["tiles"]) == 6 and got["after"]["svg"]["surface"] == 1


def test_mars_card_shows_the_disc_the_features_and_the_source(tmp_path):
    """17 Nov 2026, London: six tiles, the eight features an imager identifies first --
    numbered, each with its type and how close it stands to the central meridian -- the
    rest behind "More features", and the disc drawn as the sky has it. Every value is the
    module's own for the same inputs."""
    got = _run(tmp_path, _mars("2026-11-17") + """
        h.online();
        await h.load('bodies');
        return { card: marsCard(),
                 t: { hm: t.hm, mag: t.mag, illuminated: t.illuminated, ge: t.geometry,
                      features: t.features.length, top: t.top.map(f => ({name: f.name,
                          type: f.type, cmDist: f.cmDist, diameterKm: f.diameterKm,
                          nearLimb: f.nearLimb, nextHm: f.nextHm, nextHmDay: f.nextHmDay,
                          cm: Math.abs(Math.round(f.cmDist)),
                          bar: Math.round(Math.max(0, 1 - Math.abs(f.cmDist) /
                              M.NEAR_LIMB_DEG) * 100) + '%'})),
                      more: t.more.map(f => f.name), empty: t.empty },
                 d: d, source: source.credit, constants: {top: M.TOP_COUNT, limb: M.NEAR_LIMB_DEG} };
    """, search="?body=mars", sim="2026-11-17")
    card, t, d = got["card"], got["t"], got["d"]
    assert card["title"] == "MARS · at " + t["hm"]
    assert not card["hidden"] and card["error"] is None and not card["muted"]

    # The tiles: diameter, magnitude, illumination, distance, the central meridian, De
    tiles = card["tiles"]
    assert [tile["label"] for tile in tiles] == ["DIA", "MAG", "ILLUM", "DIST", "CM", "DE"]
    assert tiles[0]["value"].endswith("″") and _num(tiles[0]["value"]) == pytest.approx(
        t["ge"]["diameterArcsec"], abs=0.05)
    assert _num(tiles[1]["value"]) == pytest.approx(t["mag"], abs=0.05)
    assert tiles[2]["value"].endswith("%") and _num(tiles[2]["value"]) == pytest.approx(
        t["illuminated"] * 100, abs=0.5)
    assert tiles[3]["value"].endswith(" AU") and _num(tiles[3]["value"]) == pytest.approx(
        t["ge"]["distanceAU"], abs=0.005)
    assert tiles[4]["value"].endswith("°") and _num(tiles[4]["value"]) == pytest.approx(
        t["ge"]["cm"], abs=0.05)
    assert tiles[5]["value"][0] == ("−" if t["ge"]["de"] < 0 else "+")
    assert _num(tiles[5]["value"]) == pytest.approx(t["ge"]["de"], abs=0.05)

    # The features: the module's own top eight, in its order, numbered 1..8, each named,
    # typed and with a bar for how close it stands to the central meridian, the type
    # translated; the rest behind "More features", unnumbered
    types = {"mons": "Mountain", "crater": "Crater", "albedo": "Albedo"}
    features = card["features"]
    assert len(features) == got["constants"]["top"] == 8
    assert [f["n"] for f in features] == [str(i + 1) for i in range(8)]
    for chip, f in zip(features, t["top"]):
        assert chip["num"] == chip["n"] and chip["name"] == f["name"]
        assert chip["type"] == types.get(f["type"], f["type"])
        assert chip["bar"] == f["bar"], "the bar's width is the distance from the meridian"
        assert chip["side"] == ("east" if f["cmDist"] >= 0 else "west")
        assert chip["cls"].split(" ")[0] == "solar-card-item"
        assert ("solar-card-feature-limb" in chip["cls"]) == f["nearLimb"]
        assert chip["focusable"] == "0", "reachable by keyboard, for its marker"
        assert chip["label"] == ", ".join([f["name"], chip["type"], f"cm {f['cm']}"])
        assert f"cm {f['cm']}" in chip["title"]
        assert ("LIMB" in chip["title"]) == f["nearLimb"]
        assert ("TONIGHT" in chip["title"]) == (f["nextHmDay"] == 0)
        assert ("TOMORROW" in chip["title"]) == (f["nextHmDay"] == 1)
        if f["diameterKm"]:
            assert f"{round(f['diameterKm'])} km" in chip["title"].split("\n")[0]
            assert " km" not in chip["name"] and " km" not in chip["type"]
    assert [f["name"] for f in features] == [f["name"] for f in t["top"]]
    assert card["more"] == len(t["more"]) > 0 and not card["moreHidden"]
    assert [f["name"] for f in card["moreList"]] == t["more"]
    assert [f["num"] for f in card["moreList"]] == [None] * len(t["more"])
    assert card["moreList"][0]["bar"] and card["moreList"][0]["type"]

    # The credit line: the gazetteer the names come from
    assert card["credit"] == "SRC " + got["source"]

    # The drawing: the disc, the dark part and its terminator, a dot per top feature, and
    # the pole it leans by
    svg = card["svg"]
    assert svg["label"] == "MARS DIAGRAM"
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
    assert svg["pole"][0]["title"] == "tilt " + _signed(d["tilt"]) + "°"
    assert len(svg["north"]) == 1 and svg["north"][0]["text"] == "N"
    assert svg["north"][0]["x"] == pytest.approx(float(f"{d['pole']['x'] * 1.16:.3f}"), abs=1e-9)
    assert svg["north"][0]["y"] == pytest.approx(float(f"{d['pole']['y'] * 1.16:.3f}"), abs=1e-9)


def test_mars_card_numbers_the_feature_under_the_pointer(tmp_path):
    """The dots on the drawing carry no numbers: the chip's own does, large, over
    everything, while the pointer or the keyboard is on it -- and only one at a time."""
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
    # The dot the number sits on is the module's own for that chip, so the label is
    # where the feature is; the dots themselves carry nothing until then
    dot = {m["n"]: m for m in got["markers"]}
    for hot, n in ((got["third"], got["thirdN"]), (got["first"], got["firstN"])):
        assert len(hot) == 2, hot
        circle, label = hot
        assert circle["tag"] == "circle" and circle["text"] == ""
        assert float(circle["r"]) == 0.085
        assert float(circle["cx"]) == pytest.approx(dot[n]["x"], abs=1e-9)
        assert float(circle["cy"]) == pytest.approx(dot[n]["y"], abs=1e-9)
        assert label["tag"] == "text" and label["text"] == n
        assert float(label["x"]) == float(circle["cx"]) and float(label["y"]) == float(circle["cy"])


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
    north = got["normal"]["svg"]["north"][0]
    assert got["diagonal"]["svg"]["north"][0]["x"] == pytest.approx(-north["x"], abs=1e-6)
    assert got["inverted"]["svg"]["north"][0]["y"] == pytest.approx(-north["y"], abs=1e-6)
    assert got["normal"]["svg"]["shaded"] == got["inverted"]["svg"]["shaded"] == 1
    assert got["normal"]["credit"] == got["inverted"]["credit"]
