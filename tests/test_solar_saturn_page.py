"""The Solar System page's Saturn card, in node.

Runs static/js/solar_system.js with the real engines and the stand-in DOM of
tests/test_solar_system_js.py (its driver, unchanged), with the Moon's and the three new
cards' modules loaded too, as the template loads them. The harness -- the cards' view
buttons and the snapshots -- lives in test_solar_jupiter_page.py, which this imports;
what the page puts in the DOM is checked against the module's own output for the same
inputs (the module itself is pinned to the engines in tests/test_solar_saturn.py).

Every string the card writes is the i18n stub's, not the template's English, so a case
that passes here has gone through i18n.saturn (the catalogues themselves are checked
with the translations).
"""

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from test_solar_jupiter_page import SITE, _run  # noqa: F401 (SITE: the case templates)

# The module's own answer for the same inputs the page uses, with the figure in each view
_SATURN = """
const NS = h.context.NovaSolarSaturn;
const opts = {inputs: {location: %(site)s}, nowMs: NOW_MS, simDate: '%(sim)s'};
const t = NS.tonight(opts);
const views = {};
NS.VIEWS.forEach(v => { views[v] = NS.diagram(t.geometry, v, t.moons, t.rings.radiusRs); });
const d = views.normal;
const source = NS.SOURCE, moonMag = NS.MOON_MAG, strip = NS.STRIP_RS;
"""


def _saturn(sim):
    return _SATURN % {"site": SITE, "sim": sim}


def _run_saturn(tmp_path, steps, sim, search="?body=saturn"):
    return _run(tmp_path, _saturn(sim) + steps, search=search, sim=sim)


def _local(utc_ms):
    return datetime.fromtimestamp(utc_ms / 1000, tz=ZoneInfo("Europe/London"))


def _day(utc_ms):
    """The page's own date without a year, as localDateTime() writes it: 'Sat 6 Feb'."""
    local = _local(utc_ms)
    return f"{local:%a} {local.day} {local:%b}"


def _full_day(utc_ms):
    """The page's own date with a year, as localDate() writes it: '9 Oct 2038'."""
    local = _local(utc_ms)
    return f"{local.day} {local:%b} {local:%Y}"


def _hm(utc_ms):
    return f"{_local(utc_ms):%H:%M}"


# The i18n stub's own words for the four states a moon can be in, in the module's order
_PLACE = {"onDisc": "on the disc", "hidden": "hidden behind the globe",
          "front": "in front", "far": "far side"}


def _where(m):
    return _PLACE["onDisc" if m["onDisc"] else "hidden" if m["behindDisc"]
                  else "front" if m["inFront"] else "far"]


def test_saturn_card_only_while_saturn_is_selected(tmp_path):
    got = _run(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { saturn: satCard() };
        const rows = h.$('solar-body').children;
        await h.clickRow('solar-body', rows.findIndex(tr => tr.dataset.body === 'saturn'));
        out.after = satCard();
        return out;
    """, search="?body=jupiter", sim="2026-11-02")
    assert got["saturn"]["hidden"]
    assert not got["after"]["hidden"] and got["after"]["error"] is None
    assert len(got["after"]["tiles"]) == 1 and got["after"]["svg"]["label"] == "SAT DIAGRAM"


def test_saturn_card_shows_the_rings_the_moons_and_the_season(tmp_path):
    """2 Nov 2026, London: Saturn 40 degrees up at 21:52. The Rings tile -- the opening
    angle with the face it is tilted towards, the two years' direction in the note, the
    two-year curve under it -- the eight moons, each with what it is doing in its own
    tooltip on the drawing, the season's transit answer, and the credit line with the
    magnitudes' sources. Every value is the module's own for the same inputs."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), t: { hm: t.hm, B: t.rings.B, Bprime: t.rings.Bprime,
                 trend: t.rings.trend, face: t.rings.face, sunFace: t.rings.sunFace,
                 sameFace: t.rings.sameFace, aArcsec: t.rings.aArcsec,
                 radiusRs: t.rings.radiusRs, muted: t.muted,
                 months: t.rings.months.map(m => m.B),
                 moons: t.moons.map(m => ({name: m.name, labelled: m.labelled, side: m.side,
                     dist: m.dist, mag: m.mag, brightness: m.brightness, onDisc: m.onDisc,
                     behindDisc: m.behindDisc, inFront: m.inFront})),
                 crossing: t.transits.crossing.map(x => ({name: x.name, closestRs: x.closestRs,
                     utcMs: x.utcMs})),
                 titan: t.transits.titan, titanNext: t.transits.titanNext },
               source: source, moonMag: moonMag };
    """, "2026-11-02")
    card, t = got["card"], got["t"]
    assert card["title"] == "SATURN · at " + t["hm"]
    assert not card["hidden"] and card["error"] is None and not card["muted"]

    # The tile: one, the rings'. Its value is the tilt without a sign -- B's sign is the face,
    # and the face is in the value -- and its note is the direction over the two years the
    # curve draws, which is not the module's own month (that one is the tooltip's wobble)
    tiles = card["tiles"]
    assert [tile["label"] for tile in tiles] == ["RINGS"]
    assert t["B"] < 0 and t["face"] == "south", "the south face is towards us tonight"
    assert tiles[0]["value"] == f"{abs(t['B']):.1f}° · S-FACE"
    assert "−" not in tiles[0]["value"] and "+" not in tiles[0]["value"]
    two = ("opening" if abs(t["months"][-1]) >= abs(t["months"][0]) else "closing")
    assert tiles[0]["notes"] == [two] == ["opening"] and t["trend"] == "closing"
    assert tiles[0]["title"].split("\n") == [
        "RINGS TIP", f"BPRIME {'−' if t['Bprime'] < 0 else '+'}{abs(t['Bprime']):.1f}",
        "S-FACE US", "LIT S-FACE SUN", f"SPAN {t['aArcsec']:.1f}",
        f"EDGE {t['radiusRs']:.2f}", "TWO YEARS opening", "WOBBLE"]

    # The curve: the tile's own content width -- the harness cannot measure it, so it is the
    # 66 px the card was written against -- with the edge-on line across it, one point per
    # sample of the two years, and the two years' direction as an arrow at its end
    spark = tiles[0]["spark"]
    assert spark["label"] == "SPARK" and spark["viewBox"] == "0 0 66 22"
    assert len(spark["zero"]) == 1 and len(spark["head"]) == 1
    n = len(t["months"])
    assert n == 49
    assert len(spark["line"]) == 1 and spark["line"][0].count("L ") == n - 1
    assert spark["line"][0].startswith("M ")

    # The moons: all eight on the drawing, in the module's own order, each with its own name
    # and, in its tooltip, its magnitude, which side of Saturn it is on and what it is doing
    assert t["sameFace"] and t["face"] == t["sunFace"] == "south"
    moons = card["svg"]["moons"]
    assert [m["moon"] for m in moons] == [m["name"] for m in t["moons"]]
    for drawn, m in zip(moons, t["moons"]):
        assert ("solar-saturn-moon-small" in drawn["cls"]) == (not m["labelled"])
        assert drawn["brightness"] == (m["brightness"] or None)
        lines = drawn["title"].split("\n")
        assert lines[0] == m["name"]
        assert lines[1] == f"mag {m['mag']:.1f}"
        assert lines[2] == f"{m['side']} of Saturn · {_where(m)}"
        assert lines[3] == f"{m['dist']:.1f} radii"
        assert ("IAP" in drawn["title"]) == (m["name"] == "Iapetus")
    iap = [m for m in moons if m["moon"] == "Iapetus"][0]
    assert iap["brightness"] in ("bright", "faint")
    # Iapetus's own bright or dark side, and where the numbers come from
    assert iap["brightness"] == "faint", "eastern elongation tonight"
    assert iap["title"].split("\n")[4:] == ["IAP FAINT", "IAP TIP", "IAP SRC"]

    # The season: this one has no Titan, so the sentence says so and names both the season
    # Titan next crosses in and the moons that do cross this one -- from the data, not from
    # any list the card was written against -- with what "crossing the disc" means last
    assert t["titan"] is None
    assert card["transitsNone"] == "NO TITAN"
    others = [x["name"] for x in t["crossing"] if x["name"] != "Titan"]
    assert card["transitsNoneTitle"] == (f"NEXT {_full_day(t['titanNext']['utcMs'])}\n"
                                         f"SMALL {', '.join(others)}\nTRANSIT TIP")
    assert others[:4] == ["Mimas", "Enceladus", "Tethys", "Dione"]

    # The credit line: the two sources' short names, the whole of them in its tooltip
    assert card["credit"] == "SRC " + got["source"]["short"] == "SRC JPL; Iapetus: JPL/Cassini"
    assert card["creditTitle"] == got["source"]["credit"]
    assert len(card["credit"]) < len(card["creditTitle"]), "the line is the short form"
    assert "East is Least" in card["creditTitle"] and "East is Least" not in card["credit"]


def test_saturn_card_names_titan_s_crossing_when_the_season_has_one(tmp_path):
    """15 Jan 2026: seven of the eight cross the disc this season -- Iapetus never does --
    Titan among them, and the rings are within a degree and a half of edge-on. The sentence
    is Titan's crossing, and tonight Titan is simply somewhere on its orbit: no list, and
    the moons' own states are the drawing's."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), B: t.rings.B, titan: t.transits.titan,
                 next: t.transits.titanNext, crossing: t.transits.crossing.length,
                 tonight: t.moons.filter(m => m.name === 'Titan').map(
                     m => ({onDisc: m.onDisc, behindDisc: m.behindDisc, inFront: m.inFront})) };
    """, "2026-01-15")
    card, titan = got["card"], got["titan"]
    assert abs(got["B"]) < 2, "the rings are near edge-on"
    assert titan is not None and got["next"] is None and got["crossing"] == 7
    assert card["transitsNone"] == "TITAN CROSSES"
    assert card["transitsNoneTitle"] == (f"CLOSEST {titan['closestRs']:.2f} on "
                                         f"{_day(titan['utcMs'])} {_hm(titan['utcMs'])}\n"
                                         f"TRANSIT TIP")
    # The season's crossings are the season's: tonight Titan is simply somewhere on its orbit
    assert sum(got["tonight"][0].values()) == 0
    titanDot = [m for m in card["svg"]["moons"] if m["moon"] == "Titan"][0]
    assert "far side" in titanDot["title"]


def test_the_saturn_figure_is_the_module_s_own_rings_moons_and_north(tmp_path):
    """The drawing: the three clipped layers in the order they cover each other -- the far
    half of the rings, the globe with the rings' shadow on it, the near half -- the ring
    paths the module's own, the moons a dot or a chevron at the edge of the strip, the
    labels only on the five the figure names, and the arrow to celestial north."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        const k = (720 / 2 - 8) / strip;
        const rw = t.rings.radiusRs * k;
        const rh = rw * Math.abs(Math.sin(t.rings.B * Math.PI / 180));
        return { card: satCard(), B: t.rings.B, north: d.north, shadow: d.shadow,
                 rings: d.rings, out: Math.max(k, rh), k: k,
                 drawn: d.moons.map(m => ({name: m.name, x: m.x, y: m.y, off: m.off,
                     inside: m.inside, labelled: m.labelled})),
                 sky: t.moons.map(m => ({name: m.name, side: m.side, brightness: m.brightness,
                     behindDisc: m.behindDisc})),
                 labelled: NS.LABELLED };
    """, "2026-11-02")
    svg = got["card"]["svg"]
    assert svg["label"] == "SAT DIAGRAM" and svg["scale"] == pytest.approx(got["k"], abs=1e-6)
    assert (svg["width"], svg["height"]) == (720, 160)
    assert svg["legend"].split("\n") == ["SAT DIAGRAM", "PLANET NORTH UP", "L-FILLED",
                                        "L-HOLLOW", "L-EDGE", "L-SHADOW", "L-SMALL"]

    # The clips: the two halves of the rings split at the centre, the near half the one the
    # rings' own sign puts in front, and the globe's own circle for the shadow
    assert [c["id"] for c in svg["clips"]] == ["solar-saturn-far", "solar-saturn-near",
                                              "solar-saturn-globe"]
    far, near, globe = svg["clips"]
    assert (far["x"], far["width"], far["height"]) == (-2, 724, 82)
    assert (near["x"], near["width"], near["height"]) == (-2, 724, 82)
    # B is negative and the view is not inverted, so the near half is the upper one
    assert got["B"] < 0 and (far["y"], near["y"]) == (80, -2)
    assert globe["r"] == 1 and globe["width"] is None, "the globe's clip is a circle"
    assert far["r"] is None and near["r"] is None

    # The layers, in the order they cover each other, each with the module's own paths
    layers = svg["layers"]
    assert [l["clip"] for l in layers] == ["url(#solar-saturn-far)", None,
                                          "url(#solar-saturn-near)"]
    assert layers[1]["transform"].startswith("translate(360 80) scale(25.142857")
    for i in (0, 2):
        assert [r["d"] for r in layers[i]["rings"]] == [r["d"] for r in got["rings"]]
        assert [r["cls"] for r in layers[i]["rings"]] == ["solar-saturn-ring solar-saturn-ring-c",
            "solar-saturn-ring solar-saturn-ring-b", "solar-saturn-ring solar-saturn-ring-a"]
        assert all(r["ve"] == "non-scaling-stroke" for r in layers[i]["rings"])
    assert layers[1]["globe"] == [1] and layers[0]["globe"] == [] and layers[2]["globe"] == []
    # The shadow, on the globe and clipped to it: the module's own outline, or none at all
    shadow = layers[1]["shadow"]
    assert len(shadow) == 1 and shadow[0]["clip"] == "url(#solar-saturn-globe)"
    assert shadow[0]["d"] == ([got["shadow"]] if got["shadow"] else [])

    # The moons: where the module draws them, in the module's own order, each with its own
    # side and brightness; a dot where the figure reaches, a chevron where it does not
    assert len(svg["moons"]) == len(got["drawn"]) == 8
    assert sum(1 for m in svg["moons"] if m["tag"] == "circle") > 0, "dots, for the moons"
    assert sum(1 for m in svg["moons"] if m["tag"] == "path") > 0, "chevrons, for the far ones"
    for group, drawn, sky in zip(svg["moons"], got["drawn"], got["sky"]):
        assert group["moon"] == drawn["name"] == sky["name"]
        assert group["side"] == sky["side"]
        assert group["brightness"] == (sky["brightness"] or None)
        assert ("solar-saturn-moon-behind" in group["cls"]) == sky["behindDisc"]
        assert ("solar-saturn-moon-small" in group["cls"]) == (not drawn["labelled"])
        assert group["title"].startswith(sky["name"] + "\n")
        # The moon's own line across the figure can run out of its box -- at this tilt the
        # strip is under two radii tall -- so the marker is kept inside it, as its name is
        rad = 4 if drawn["labelled"] else 2.5
        edge = 7 if drawn["off"] else rad + 2
        want = max(edge, min(160 - edge, 80 + drawn["y"] * svg["scale"]))
        if drawn["off"]:
            assert group["tag"] == "path" and group["d"] is not None and group["r"] is None
            nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", group["d"])]
            assert [nums[i] for i in range(1, len(nums), 2)] == pytest.approx(
                [want - 5, want, want + 5], abs=0.01), "a chevron of its own size"
        else:
            assert group["tag"] == "circle" and group["r"] == (4 if drawn["labelled"] else 2.5)
            assert group["cx"] == pytest.approx(360 + drawn["x"] * svg["scale"], abs=0.01)
            assert group["cy"] == pytest.approx(want, abs=0.01)
            assert group["r"] <= group["cy"] <= 160 - group["r"], "the whole dot is in the box"
        assert group["cx"] is None or 0 <= group["cx"] <= 720
    assert sum(1 for d in got["drawn"] if d["off"]) > 0, "the figure reaches past its edge"
    assert sum(1 for d in got["drawn"] if d["inside"]) > 0, "and holds the inner moons"

    # The labels: only the moons the figure names, each once, inside the figure's box
    labels = svg["labels"]
    assert len(labels) == len(got["labelled"]) == 5
    assert sorted(l["moon"] for l in labels) == sorted(got["labelled"])
    assert all(l["text"] == l["moon"] for l in labels)
    assert all(0 <= l["y"] <= 160 and 0 < l["x"] < 720 for l in labels)
    named = [d for d in got["drawn"] if d["labelled"] and not d["off"]]
    for label in labels:
        drawn = [d for d in named if d["name"] == label["moon"]]
        if drawn:
            assert abs(label["x"] - (360 + drawn[0]["x"] * svg["scale"])) <= 60, "near its moon"

    # Celestial north: the module's own direction, out past the ring system, and the arrow
    # from just outside the rings to its tip, with N beside it
    out = got["out"]
    assert len(svg["north"]) == 1 and svg["north"][0]["text"] == "N"
    assert svg["north"][0]["title"] == "CELESTIAL N"
    assert svg["north"][0]["x"] == pytest.approx(360 + got["north"]["x"] * (out + 25), abs=0.01)
    assert svg["north"][0]["y"] == pytest.approx(80 + got["north"]["y"] * (out + 25), abs=0.01)
    assert len(svg["arrow"]) == 1 and svg["arrow"][0]["title"] == "CELESTIAL N"
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", svg["arrow"][0]["d"])]
    pts = [tuple(nums[i:i + 2]) for i in range(0, len(nums), 2)]
    assert len(pts) == 5 and pts[0] == pytest.approx(
        (360 + got["north"]["x"] * (out + 3), 80 + got["north"]["y"] * (out + 3)), abs=0.01)
    tip = (360 + got["north"]["x"] * (out + 17), 80 + got["north"]["y"] * (out + 17))
    assert pts[1] == pytest.approx(tip, abs=0.01) and pts[3] == pytest.approx(tip, abs=0.01)
    assert abs(got["north"]["y"]) > 0.9, "north is close to the vertical on this night"


def test_the_saturn_system_mirrors_in_the_shared_view(tmp_path):
    """The Moon's own buttons switch all four cards: the system turns over with them --
    the moons left-right in a star diagonal, both ways in a Newtonian -- and the ring
    halves swap, because which half is in front is the view's own sign."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        const out = { normal: satCard() };
        await h.$('moon-view-diagonal').fire('click');
        out.diagonal = satCard();
        await h.$('moon-view-inverted').fire('click');
        out.inverted = satCard();
        out.pressed = pressed(moonButtons);
        out.stored = h.stored('nova_solar_view');
        return out;
    """, "2026-11-02")
    assert got["pressed"] == ["inverted"] and got["stored"] == "inverted"
    assert got["normal"]["pressed"] == ["normal"] and got["diagonal"]["pressed"] == ["diagonal"]
    dots = [(m["moon"], m["cx"], m["cy"]) for m in got["normal"]["svg"]["moons"]
            if m["tag"] == "circle"]
    assert dots, "the inner moons are drawn as dots"
    for (moon, cx, cy) in dots:
        diag = [m for m in got["diagonal"]["svg"]["moons"] if m["moon"] == moon][0]
        inv = [m for m in got["inverted"]["svg"]["moons"] if m["moon"] == moon][0]
        assert diag["cx"] == pytest.approx(720 - cx, abs=0.01), moon
        assert diag["cy"] == pytest.approx(cy, abs=0.01), moon
        assert inv["cx"] == pytest.approx(720 - cx, abs=0.01), moon
        assert inv["cy"] == pytest.approx(160 - cy, abs=0.01), moon
    # The two halves of the rings swap over, and the globe stays where it is
    assert got["normal"]["svg"]["clips"][0]["y"] == 80
    assert got["diagonal"]["svg"]["clips"][0]["y"] == 80
    assert got["inverted"]["svg"]["clips"][0]["y"] == -2
    for view in ("normal", "diagonal", "inverted"):
        assert got[view]["svg"]["layers"][1]["transform"].startswith("translate(360 80) scale(")
        assert got[view]["credit"] == got["normal"]["credit"]


def test_saturn_card_is_muted_when_saturn_is_low(tmp_path):
    """20 May 2027: Saturn is seven degrees up at best. The card still draws the system and
    still names the moons, muted, and says why."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), maxAlt: t.maxAlt, threshold: t.threshold,
                 reason: t.mutedReason, moons: t.moons.length };
    """, "2027-05-20")
    assert got["reason"] == "altitude" and got["maxAlt"] < got["threshold"] == 20
    assert got["card"]["muted"] and got["card"]["empty"] == "BELOW"
    assert len(got["card"]["svg"]["moons"]) == got["moons"] == 8
    assert got["card"]["svg"]["labels"] and len(got["card"]["tiles"]) == 1


def test_the_faces_note_is_shown_only_when_the_two_faces_differ(tmp_path):
    """24 Mar 2025: the rings are within a tenth of a degree of edge-on and the Earth and
    the Sun are on opposite faces of them, so the tile says so, and its own value keeps the
    face towards us -- B's sign -- while the Sun's own latitude, B', is in the tooltip. 2
    Nov 2026: both are on the south face, and it says nothing."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), B: t.rings.B, Bprime: t.rings.Bprime,
                 sameFace: t.rings.sameFace, face: t.rings.face, sunFace: t.rings.sunFace };
    """, "2025-03-24")
    tile = got["card"]["tiles"][0]
    assert not got["sameFace"] and got["face"] != got["sunFace"]
    assert (got["B"] < 0) != (got["Bprime"] < 0), "the two faces are on opposite sides"
    assert tile["value"] == f"{abs(got['B']):.1f}° · S-FACE", "the face towards us, not the Sun's"
    assert tile["notes"] == ["opening", "FACES DIFFER"]
    assert "S-FACE US" in tile["title"] and "LIT N-FACE SUN" in tile["title"]
    assert f"BPRIME +{abs(got['Bprime']):.1f}" in tile["title"], "the Sun's own latitude"

    same = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), sameFace: t.rings.sameFace, trend: t.rings.trend };
    """, "2026-11-02")
    tile = same["card"]["tiles"][0]
    assert same["sameFace"]
    # The note is the two years' direction, which is opening; the module's own month is
    # closing, so the tooltip carries it as the wobble it is
    assert tile["notes"] == ["opening"] and same["trend"] == "closing"
    assert "TWO YEARS opening" in tile["title"] and "WOBBLE" in tile["title"]
    assert "FACES DIFFER" not in tile["title"]


def test_saturn_card_error_leaves_the_rest_of_the_page(tmp_path):
    """A failure while computing the card: the card's own line, and the rest of the page --
    the bodies table, the charts, the Calculator -- untouched."""
    got = _run(tmp_path, """
        h.online();
        h.context.NovaSolarSaturn.tonight = () => { throw new Error('boom'); };
        await h.load('bodies');
        const out = { card: satCard() };
        const rows = h.$('solar-body').children;
        await h.clickRow('solar-body', rows.findIndex(tr => tr.dataset.body === 'jupiter'));
        out.jupiter = satCard();
        await h.clickRow('solar-body', rows.findIndex(tr => tr.dataset.body === 'saturn'));
        out.back = satCard();
        out.snap = h.snap();
        out.calc = h.$('solar-calc').hidden;
        return out;
    """, search="?body=saturn", sim="2026-11-02")
    assert got["card"]["error"] == "SAT ERROR" and got["card"]["bodyHidden"]
    assert got["card"]["title"] == "SATURN" and not got["card"]["hidden"]
    assert got["jupiter"]["hidden"], "hidden while another body is selected"
    assert got["back"]["error"] == "SAT ERROR" and got["back"]["bodyHidden"]
    assert got["snap"]["solarError"] is None and got["snap"]["bodyRows"] > 0
    assert got["snap"]["bodiesShown"] and not got["calc"]


def test_saturn_card_is_computed_once_a_night(tmp_path):
    """The 60 s tick and a view change redraw but do not recompute: the card is keyed on the
    night and the location. A new night recomputes."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        const M = h.context.NovaSolarSaturn;
        const calls = { tonight: 0 };
        const tonight = M.tonight;
        M.tonight = function () { calls.tonight++; return tonight.apply(this, arguments); };
        const svg = svgOf('solar-saturn-diagram');
        await h.tick();
        await h.tick();
        const after = { tonight: calls.tonight, sameSvg: svgOf('solar-saturn-diagram') === svg };
        await h.$('saturn-view-inverted').fire('click');
        after.redrawn = svgOf('solar-saturn-diagram') !== svg;
        after.pressed = satCard().pressed;
        h.$('sim-date-input').value = '2026-12-20';
        await h.tick();
        return { after: after, calls: calls };
    """, "2026-11-02")
    assert got["after"] == {"tonight": 0, "sameSvg": True, "redrawn": True, "pressed": ["inverted"]}
    assert got["calls"] == {"tonight": 1}, "a new night recomputes the card"


WIDE = 1278      # the card's own width in the browser window Anton looked at


def test_every_moon_has_a_marker_inside_the_figure(tmp_path):
    """Anton's first look: every moon has a dot or a chevron of its own, inside the box, and
    it keeps the figure's own x. A moon's line across the drawing runs far outside a strip
    under two radii tall -- at the card's own width most of them do, at any tilt from the
    smallest -- so the marker is held at the edge with its name, and the distance that says
    how far out it is is the tooltip's. Drawn at the position the line actually has, they
    all fall outside the box, which is what the first look saw: labels with no dots."""
    out = {}
    for sim in ("2026-11-02", "2031-11-02"):
        got = _run_saturn(tmp_path, f"""
            Object.defineProperty(h.$('solar-saturn-diagram'), 'clientWidth',
                {{ get: () => {WIDE} }});
            h.online();
            await h.load('bodies');
            return {{ card: satCard(), B: t.rings.B,
                     drawn: d.moons.map(m => ({{name: m.name, x: m.x, y: m.y, off: m.off,
                         labelled: m.labelled}})),
                     moons: t.moons.map(m => ({{name: m.name, dist: m.dist}})) }};
        """, sim)
        svg = got["card"]["svg"]
        assert svg["width"] == WIDE
        assert len(svg["moons"]) == len(got["drawn"]) == 8, "all eight, none dropped"
        cx = WIDE / 2
        held = 0
        for group, drawn, sky in zip(svg["moons"], got["drawn"], got["moons"]):
            assert group["moon"] == drawn["name"] == sky["name"]
            assert drawn["labelled"] or group["r"] in (None, 2.5)
            raw = cx + drawn["y"] * svg["scale"]
            if drawn["off"]:
                assert group["tag"] == "path", "beyond the strip: a chevron at its edge"
                xs = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", group["d"])[0::2]]
                ys = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", group["d"])[1::2]]
                assert all(0 <= y <= 160 for y in ys), "and the whole chevron is in the box"
                assert all(0 <= x <= WIDE for x in xs)
            else:
                assert group["tag"] == "circle" and group["r"] in (2.5, 4)
                assert group["cx"] == pytest.approx(cx + drawn["x"] * svg["scale"], abs=0.01)
                assert group["r"] <= group["cy"] <= 160 - group["r"], "the whole dot is in the box"
            if not 2 <= raw <= 158:
                held += 1
                assert group["cy" if not drawn["off"] else "d"] is not None
        assert held, "some moon's own line is outside the box and is held at its edge"
        out[sim] = (got["B"], held)
    assert out["2031-11-02"][0] < -20, "the widest tilt: the figure is at its extreme"
    assert out["2026-11-02"][1] > 0, "and tonight too, at the width the card is drawn at"


def test_the_rings_cross_the_globe_in_front_of_it(tmp_path):
    """The rings' near half is drawn over the globe, in the module's own paths and the same
    three ring regions the far half is, so the band across the globe is the ring system at
    the width it has at the sides, with the Cassini gap in it. The globe is a shade under
    the rings in both themes: drawn in the rings' own colour the crossing would be invisible,
    the same pixels over themselves."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), rings: d.rings, B: t.rings.B };
    """, "2026-11-02")
    svg = got["card"]["svg"]
    layers = svg["layers"]
    assert [l["clip"] for l in layers] == ["url(#solar-saturn-far)", None,
                                          "url(#solar-saturn-near)"]
    far, globe, near = layers
    assert [r["d"] for r in near["rings"]] == [r["d"] for r in far["rings"]] == \
        [r["d"] for r in got["rings"]], "one ring system, drawn twice, in the module's paths"
    assert [r["cls"] for r in near["rings"]] == ["solar-saturn-ring solar-saturn-ring-c",
        "solar-saturn-ring solar-saturn-ring-b", "solar-saturn-ring solar-saturn-ring-a"]
    assert globe["globe"] == [1] and far["globe"] == [] and near["globe"] == []
    assert globe["shadow"] and globe["shadow"][0]["clip"] == "url(#solar-saturn-globe)", \
        "and the rings' shadow is on the globe, beside the band"
    # B is negative and the view is normal, so the near half is the upper one -- and the far
    # half is the other, hidden behind the globe where the globe is
    assert got["B"] < 0
    assert [c["y"] for c in svg["clips"][:2]] == [80, -2]

    css = (Path(__file__).resolve().parent.parent
           / "static" / "css" / "solar_system.css").read_text()
    rule = lambda sel: css[css.index(sel + " {"):css.index("}", css.index(sel + " {"))]
    rings = rule("#bodies-tab .solar-saturn-svg .solar-saturn-ring-b")
    assert "fill: var(--solar-body-saturn);" in rings
    globe_fill = rule("#bodies-tab .solar-saturn-svg .solar-saturn-globe")
    assert "fill: var(--solar-saturn-globe);" in globe_fill
    scoped = rule("#solar-saturn")
    assert "--solar-saturn-globe: " in scoped and "--solar-body-saturn" not in scoped, \
        "the globe's own value, in the card's own scope"
    band, disc = scoped.split("--solar-saturn-globe: ")[1].split(";")[0], "#d3bd8c"
    assert band.lower() != disc, "a different colour from the rings, or the band is invisible"


def test_the_rings_tile_is_read_at_the_width_it_is_drawn(tmp_path):
    """The Rings tile is a tile of the row, not a full-width block: it holds the value and
    the two-year curve, and the curve takes the tile's own content width. The stand-in DOM
    has no layout, so the card falls back to the 66 px it was written against; given a row
    that can be measured, the curve is drawn at that width instead, one point per sample."""
    got = _run_saturn(tmp_path, """
        const create = h.context.document.createElement;
        h.context.document.createElement = function (tag) {
            const el = create.call(this, tag);
            Object.defineProperty(el, 'clientWidth', { get: function () {
                return (this.className || '').indexOf('solar-card-tile-row-spark') >= 0
                    ? 137 : 0; } });
            return el;
        };
        h.online();
        await h.load('bodies');
        return { card: satCard(), months: t.rings.months.length };
    """, "2026-11-02")
    tile = got["card"]["tiles"][0]
    spark = tile["spark"]
    assert spark["viewBox"] == f"0 0 137 22", "the row's own width, measured"
    assert spark["line"][0].count("L ") == got["months"] - 1
    line = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", spark["line"][0])]
    assert min(line[0::2]) == 4 and max(line[0::2]) == 137 - 4, "the curve spans the row"
    assert line[-1] > 11, "tonight, on the south face, the tilt is below the edge-on line"
    # The two-year direction, at the curve's own end: |B| is growing, so the arrow leaves the
    # edge-on line, which on this face is downwards on the drawing. The arrow's tip is the
    # word's own direction drawn, so the two can never disagree.
    head = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", spark["head"][0])]
    assert len(head) == 6
    assert head[3] == head[5] == line[-1] and head[0] == line[-2], "its base is the curve's end"
    assert abs(head[1] - 11) > abs(head[5] - 11), "and its tip points away from the line"
    assert tile["notes"] == ["opening"] and "−" not in tile["value"], "the word is the same way"
