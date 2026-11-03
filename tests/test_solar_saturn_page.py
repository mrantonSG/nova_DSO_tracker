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

import math
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
// The page's own figure at a card width: the scale that width sets, and the height it draws
// the strip at -- the Jupiter strip's own height as the floor, and the page's cap above it,
// so a moon within the two radii the page reaches is drawn as a dot at its own place
const satK = W => (W / 2 - 8) / strip;
const satH = W => Math.round(Math.min(240, Math.max(160, 2 * (2 * satK(W) + 4 + 2))));
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


def _points(d):
    """The points of a path's `d`, in order: 'M a b L c d L e f' is three of them."""
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", d)]
    assert len(nums) % 2 == 0 and len(nums) >= 4, d
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]


def _tip(d):
    """A chevron's tip: the point it is drawn to, between its two arms."""
    pts = _points(d)
    assert len(pts) == 3, d
    return pts[1]


def _on_ray(point, origin, target):
    """True while `point` lies on the line from `origin` towards `target`, on the target's
    own side of it -- the direction a moon is in, which is what a chevron marks. The point
    may be nearer the centre than the moon or further out: it is where the moon's own line
    leaves the figure, which is not where the moon is."""
    ox, oy = origin
    tx, ty = target[0] - ox, target[1] - oy
    px_, py_ = point[0] - ox, point[1] - oy
    length = (tx * tx + ty * ty) ** 0.5
    return (abs(px_ * ty - py_ * tx) / length <= 0.05
            and (px_ * tx + py_ * ty) / length > 0)


def _off_line(point, origin, target):
    """How far off the line from `origin` to `target` a point is, signed: two points with
    opposite signs are on either side of it."""
    ox, oy = origin
    tx, ty = target[0] - ox, target[1] - oy
    length = (tx * tx + ty * ty) ** 0.5
    return ((point[0] - ox) * ty - (point[1] - oy) * tx) / length


def _bbox(d):
    """A path's own bounding box: (x0, y0, x1, y1)."""
    pts = _points(d)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _marker_box(group):
    """The box the card keeps a marker's name off: a dot's own circle, a chevron's three
    points, each with the 1 px the card adds."""
    if group["tag"] == "circle":
        return (group["cx"] - group["r"] - 1, group["cx"] + group["r"] + 1,
                group["cy"] - group["r"] - 1, group["cy"] + group["r"] + 1)
    xs = [p[0] for p in _points(group["d"])]
    ys = [p[1] for p in _points(group["d"])]
    return min(xs) - 1, max(xs) + 1, min(ys) - 1, max(ys) + 1


def _label_box(label):
    """The box the card places a label in: the width it measures with, and 12 px of text."""
    half = (7 * len(label["text"]) + 4) / 2
    return (label["x"] - half, label["x"] + half, label["y"] - 7, label["y"] + 7)


def _overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]


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
               source: source, moonMag: moonMag, drawn: NS.DRAWN };
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

    # The moons: the card's own seven on the drawing, in the module's own order, each with
    # its own name and, in its tooltip, its magnitude, which side of Saturn it is on and
    # what it is doing
    assert t["sameFace"] and t["face"] == t["sunFace"] == "south"
    drawn_names = [m["name"] for m in t["moons"] if m["name"] in got["drawn"]]
    assert drawn_names == got["drawn"] and "Hyperion" not in drawn_names
    moons = card["svg"]["moons"]
    assert [m["moon"] for m in moons] == drawn_names
    for drawn, m in zip(moons, [m for m in t["moons"] if m["name"] in got["drawn"]]):
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
    """The drawing: the four clipped layers in the order they cover each other -- the far
    half of the rings, the globe with the rings' shadow on it, the near half, and the near
    half again over the globe -- the ring paths the module's own, the moons a dot where the
    figure reaches and a chevron where its own line leaves the figure, the labels on the
    five the figure always names and on the two small ones in the room that is left, and the
    arrow to celestial north."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        const k = satK(720);
        const rw = t.rings.radiusRs * k;
        const rh = rw * Math.abs(Math.sin(t.rings.B * Math.PI / 180));
        return { card: satCard(), H: satH(720), B: t.rings.B, north: d.north,
                 shadow: d.shadow,
                 rings: d.rings, out: Math.max(k, rh), k: k, diffs: NS.DRAWN,
                 drawn: d.moons.map(m => ({name: m.name, x: m.x, y: m.y, off: m.off,
                     inside: m.inside, labelled: m.labelled})),
                 sky: t.moons.filter(m => NS.DRAWN.indexOf(m.name) >= 0).map(
                     m => ({name: m.name, side: m.side, brightness: m.brightness,
                            behindDisc: m.behindDisc})),
                 labelled: NS.LABELLED,
                 small: NS.DRAWN.filter(n => NS.LABELLED.indexOf(n) < 0) };
    """, "2026-11-02")
    svg = got["card"]["svg"]
    assert svg["label"] == "SAT DIAGRAM" and svg["scale"] == pytest.approx(got["k"], abs=1e-6)
    assert (svg["width"], svg["height"]) == (720, got["H"])
    assert got["H"] == 160, "at the card's default width the strip is the Jupiter strip's"
    assert svg["legend"].split("\n") == ["SAT DIAGRAM", "PLANET NORTH UP", "L-FILLED",
                                        "L-HOLLOW", "L-EDGE", "L-SHADOW", "L-SMALL"]

    # The clips: the two halves of the rings split at the centre, the near half the one the
    # rings' own sign puts in front, the globe's own circle for the shadow, and the globe's
    # own near half-disc for the pass that puts the rings' band across it
    assert [c["id"] for c in svg["clips"]] == ["solar-saturn-far", "solar-saturn-near",
                                              "solar-saturn-globe", "solar-saturn-front"]
    far, near, globe, front = svg["clips"]
    assert (far["x"], far["width"], far["height"]) == (-2, 724, 82)
    assert (near["x"], near["width"], near["height"]) == (-2, 724, 82)
    # B is negative and the view is not inverted, so the near half is the upper one
    assert got["B"] < 0 and (far["y"], near["y"]) == (80, -2)
    assert globe["r"] == 1 and globe["width"] is None, "the globe's clip is a circle"
    assert far["r"] is None and near["r"] is None
    # The front clip is an arc of the globe's own radius, from the left limb to the right,
    # over the top: the same half the rings' own sign puts in front
    k = svg["scale"]
    assert front["d"] == (f"M {round(360 - k, 2)} 80 A {round(k, 2)} {round(k, 2)} 0 1 1 "
                          f"{round(360 + k, 2)} 80 Z")
    assert front["r"] is None and front["width"] is None

    # The layers, in the order they cover each other, each with the module's own paths
    layers = svg["layers"]
    assert [l["clip"] for l in layers] == ["url(#solar-saturn-far)", None,
                                          "url(#solar-saturn-near)", "url(#solar-saturn-front)"]
    assert [l["cls"] for l in layers] == [None, None, None, "solar-saturn-front"]
    assert layers[1]["transform"].startswith("translate(360 80) scale(25.142857")
    for i in (0, 2, 3):
        assert [r["d"] for r in layers[i]["rings"]] == [r["d"] for r in got["rings"]]
        assert [r["cls"] for r in layers[i]["rings"]] == ["solar-saturn-ring solar-saturn-ring-c",
            "solar-saturn-ring solar-saturn-ring-b", "solar-saturn-ring solar-saturn-ring-a"]
        assert all(r["ve"] == "non-scaling-stroke" for r in layers[i]["rings"])
    assert layers[1]["globe"] == [1] and layers[0]["globe"] == [] and layers[2]["globe"] == []
    assert layers[3]["globe"] == [] and layers[3]["shadow"] == []
    # The shadow, on the globe and clipped to it: the module's own outline, or none at all
    shadow = layers[1]["shadow"]
    assert len(shadow) == 1 and shadow[0]["clip"] == "url(#solar-saturn-globe)"
    assert shadow[0]["d"] == ([got["shadow"]] if got["shadow"] else [])

    # The moons: the seven the figure is for, in the module's own order, each with its own
    # side and brightness; a dot at its own place where the figure reaches it, a chevron
    # where its own line leaves the figure
    assert len(svg["moons"]) == len(got["drawn"]) == len(got["sky"]) == 7
    assert got["diffs"] == ["Mimas", "Enceladus", "Tethys", "Dione", "Rhea", "Titan", "Iapetus"]
    assert [d["name"] for d in got["drawn"]] == got["diffs"], "the engine's order, less Hyperion"
    assert sum(1 for m in svg["moons"] if m["tag"] == "circle") > 0, "dots, for the moons"
    assert sum(1 for m in svg["moons"] if m["tag"] == "path") > 0, "chevrons, for the far ones"
    for group, drawn, sky in zip(svg["moons"], got["drawn"], got["sky"]):
        assert group["moon"] == drawn["name"] == sky["name"]
        assert group["side"] == sky["side"]
        assert group["brightness"] == (sky["brightness"] or None)
        assert ("solar-saturn-moon-behind" in group["cls"]) == sky["behindDisc"]
        assert ("solar-saturn-moon-small" in group["cls"]) == (not drawn["labelled"])
        assert group["title"].startswith(sky["name"] + "\n")
        # The moon's own place in the figure, and where its own line leaves it
        tx = 360 + drawn["x"] * k
        ty = 80 + drawn["y"] * k
        rad = 4 if drawn["labelled"] else 2.5
        size = 5 if drawn["off"] else rad
        fits = (not drawn["off"] and abs(drawn["x"] * k) <= 360 - size - 2
                and abs(drawn["y"] * k) <= 80 - size - 2)
        if fits:
            assert group["tag"] == "circle" and group["r"] == rad
            assert (group["cx"], group["cy"]) == pytest.approx((tx, ty), abs=0.01), \
                "a dot at its own place, never at one it does not have"
            assert rad <= group["cy"] <= 160 - rad, "the whole dot is in the box"
        else:
            assert group["tag"] == "path" and group["r"] is None
            tip = _tip(group["d"])
            assert _on_ray(tip, (360, 80), (tx, ty)), "the tip is on the line to the moon"
            assert min(tip[0], 720 - tip[0], tip[1], 160 - tip[1]) == \
                pytest.approx(size + 2, abs=0.01), \
                "and on the figure's edge, where the line leaves it"
        assert group["cx"] is None or 0 <= group["cx"] <= 720
    assert sum(1 for d in got["drawn"] if d["off"]) > 0, "the figure reaches past its edge"
    assert sum(1 for d in got["drawn"] if d["inside"]) > 0, "and holds the inner moons"

    # The labels: the five the figure always names, each once, and Mimas and Enceladus with
    # them where the lanes leave room -- at this width they do -- each inside the figure's box
    labels = svg["labels"]
    assert len(got["labelled"]) == 5 and got["small"] == ["Mimas", "Enceladus"]
    assert sorted(l["moon"] for l in labels) == sorted(got["labelled"] + got["small"])
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
    assert len(got["card"]["svg"]["moons"]) == 7 and got["moons"] == 8
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


def test_no_moon_is_moved_to_a_place_it_does_not_have(tmp_path):
    """Anton's second look: a moon is drawn where it is, and where the figure cannot reach
    it its own line is followed out to the edge and marked there, with its name -- never a
    dot squeezed to a position the moon does not have. The figure is the height the width
    sets, so the moons within two radii of the ring plane are dots inside it -- and a moon's
    line still runs far outside it past that, at any tilt from the smallest; the distance
    that says how far out it is is the tooltip's. At the card's own width the inner moons
    are dots at their own places, and the ones the figure cannot reach are chevrons on the
    edge, each on the line from the centre to its own moon."""
    out = {}
    for sim in ("2026-11-02", "2031-11-02"):
        got = _run_saturn(tmp_path, f"""
            Object.defineProperty(h.$('solar-saturn-diagram'), 'clientWidth',
                {{ get: () => {WIDE} }});
            h.online();
            await h.load('bodies');
            return {{ card: satCard(), H: satH({WIDE}), B: t.rings.B,
                     drawn: d.moons.map(m => ({{name: m.name, x: m.x, y: m.y, off: m.off,
                         labelled: m.labelled}})),
                     moons: t.moons.filter(m => NS.DRAWN.indexOf(m.name) >= 0).map(
                         m => ({{name: m.name, dist: m.dist}})) }};
        """, sim)
        svg = got["card"]["svg"]
        assert svg["width"] == WIDE and svg["height"] == got["H"]
        # The seven the figure is for: the engine's eight less Hyperion, which is never
        # inside it at any tilt, and would only ever be a chevron at the figure's edge
        assert len(svg["moons"]) == len(got["drawn"]) == len(got["moons"]) == 7
        cx, cy, scale = WIDE / 2, got["H"] / 2, svg["scale"]
        dots = chevrons = 0
        for group, drawn, sky in zip(svg["moons"], got["drawn"], got["moons"]):
            assert group["moon"] == drawn["name"] == sky["name"]
            assert drawn["labelled"] or group["r"] in (None, 2.5)
            # Its own place in the figure, and the marker the card chose for it
            x = cx + drawn["x"] * scale
            y = cy + drawn["y"] * scale
            rad = 4 if drawn["labelled"] else 2.5
            size = 5 if drawn["off"] else rad
            fits = (not drawn["off"] and abs(drawn["x"] * scale) <= cx - size - 2
                    and abs(drawn["y"] * scale) <= cy - size - 2)
            if fits:
                dots += 1
                assert group["tag"] == "circle" and group["r"] == rad
                assert (group["cx"], group["cy"]) == pytest.approx((x, y), abs=0.01), \
                    "the dot at the moon's own place, to the pixel"
                assert rad <= group["cy"] <= got["H"] - rad, "the whole dot is in the box"
            else:
                chevrons += 1
                assert group["tag"] == "path", "out of the figure: a chevron at its edge"
                pts = _points(group["d"])
                assert len(pts) == 3 and all(0 <= p[0] <= WIDE and 0 <= p[1] <= got["H"]
                                             for p in pts), "the whole chevron is in the box"
                assert _on_ray(_tip(group["d"]), (cx, cy), (x, y)), \
                    "its tip is on the line from the centre to its own moon"
                assert min(_tip(group["d"])[0], WIDE - _tip(group["d"])[0],
                           _tip(group["d"])[1], got["H"] - _tip(group["d"])[1]) == \
                    pytest.approx(size + 2, abs=0.01), "and where that line leaves the figure"
                # Its two arms are the same small distance either side of that line and
                # behind the tip, so the mark points the way the moon is
                sides = [_off_line(p, (cx, cy), (x, y)) for p in (pts[0], pts[2])]
                assert all(abs(s) == pytest.approx(5, abs=0.02) for s in sides), \
                    "a chevron of its own size"
                assert sides[0] * sides[1] < 0, "one arm either side of the line"
                assert all(math.dist(p, _tip(group["d"])) == pytest.approx(7.07, abs=0.03)
                           for p in (pts[0], pts[2])), "both behind the tip"
        assert dots > 0, "the moons the figure reaches are dots at their own places"
        assert chevrons > 0, "and the ones it cannot reach are marked at its edge"
        out[sim] = (got["B"], dots, chevrons)
    assert out["2031-11-02"][0] < -20, "the widest tilt: the figure is at its extreme"
    assert out["2026-11-02"][1] > 0 and out["2026-11-02"][2] > 0


def test_titan_is_a_dot_tonight_and_a_chevron_at_the_widest_tilt(tmp_path):
    """Anton's third look, at Titan: the figure is now the height the card's width sets, so
    tonight Titan's own place is inside it at every width the card is drawn at, the widest
    included, and it is a dot there -- the same dot the same night, not a chevron at the
    edge because the box was too short for it. On 15 May 2032 the rings are near their
    widest and Titan is past the two radii the height is set for, so it is a chevron at the
    top edge on the line to it, not a dot squeezed onto the edge."""
    tonight = {}
    for width in (720, 1200, 1378):
        got = _run_saturn(tmp_path, f"""
            Object.defineProperty(h.$('solar-saturn-diagram'), 'clientWidth',
                {{ get: () => {width} }});
            h.online();
            await h.load('bodies');
            const m = d.moons.filter(x => x.name === 'Titan')[0];
            const g = svgOf('solar-saturn-diagram').children
                .filter(c => c.tagName === 'g' && c.attrs['data-moon'] === 'Titan')[0];
            return {{ card: satCard(), H: satH({width}), drawn: {{x: m.x, y: m.y, off: m.off}},
                     tag: g.children[0].tagName, r: +g.children[0].attrs.r,
                     cx: +g.children[0].attrs.cx, cy: +g.children[0].attrs.cy,
                     d: g.children[0].attrs.d }};
        """, "2026-11-02")
        svg = got["card"]["svg"]
        assert svg["width"] == width and svg["height"] == got["H"]
        k, cx, cy = svg["scale"], width / 2, got["H"] / 2
        x, y = cx + got["drawn"]["x"] * k, cy + got["drawn"]["y"] * k
        assert not got["drawn"]["off"], "Titan is inside the strip's width tonight"
        # The height the width sets leaves the whole dot room, with the 2 px the figure
        # keeps clear of its own edge -- at the widest width too, which is the fix
        assert abs(got["drawn"]["y"] * k) <= cy - 4 - 2
        assert abs(got["drawn"]["x"] * k) <= cx - 4 - 2
        tonight[width] = (got["H"], round(y, 1), got["tag"], got["r"])
        assert got["tag"] == "circle" and got["r"] == 4, \
            "a dot at this width, as at the narrow ones: the figure is tall enough for it"
        assert (got["cx"], got["cy"]) == pytest.approx((x, y), abs=0.01), \
            "the dot at Titan's own place, to the pixel"
    # The three widths and the height each sets, which is what moved Titan's dot back into
    # the figure: 160 px is the Jupiter strip's floor and the narrow card's own height,
    # and the two wider ones are the figure growing until the 240 px cap takes over
    assert tonight[720] == (160, 121.9, "circle", 4)
    assert tonight[1200] == (181, 161.0, "circle", 4)
    assert tonight[1378] == (207, 184.6, "circle", 4)

    wide = _run_saturn(tmp_path, f"""
        Object.defineProperty(h.$('solar-saturn-diagram'), 'clientWidth',
            {{ get: () => {WIDE} }});
        h.online();
        await h.load('bodies');
        const m = d.moons.filter(x => x.name === 'Titan')[0];
        const g = svgOf('solar-saturn-diagram').children
            .filter(c => c.tagName === 'g' && c.attrs['data-moon'] === 'Titan')[0];
        return {{ card: satCard(), B: t.rings.B, H: satH({WIDE}), drawn: {{x: m.x, y: m.y}},
                  tag: g.children[0].tagName, r: +g.children[0].attrs.r,
                  d: g.children[0].attrs.d }};
    """, "2032-05-15")
    svg = wide["card"]["svg"]
    cy = wide["H"] / 2
    assert svg["height"] == wide["H"] and cy < 240 / 2, "a cap, and not at it"
    assert wide["B"] < -20, "the rings near their widest"
    assert wide["tag"] == "path" and wide["r"] is None, \
        "past the two radii: a chevron, since 2032"
    tip = _tip(wide["d"])
    assert _on_ray(tip, (WIDE / 2, cy),
                   (WIDE / 2 + wide["drawn"]["x"] * svg["scale"],
                    cy + wide["drawn"]["y"] * svg["scale"]))
    assert tip[1] == pytest.approx(7, abs=0.01), "an edge chevron, at the top edge"
    labels = [l for l in svg["labels"] if l["moon"] == "Titan"]
    assert len(labels) == 1 and labels[0]["y"] > tip[1] + 7, "and its name below it"


def test_a_moon_s_name_never_covers_its_own_marker(tmp_path):
    """Anton's second look: a name belongs beside its moon, never on it. The lanes the card
    lays names out in are the Jupiter strip's, and a moon in the figure's top or bottom rows
    has its first lane pulled onto itself -- so its own marker is what the lane has to
    clear first, before any other name it might cover. Checked on tonight's drawing, where
    Iapetus sits on the top edge with its chevron, and on 15 May 2032, where Titan's is."""
    for sim in ("2026-11-02", "2032-05-15"):
        got = _run_saturn(tmp_path, f"""
            Object.defineProperty(h.$('solar-saturn-diagram'), 'clientWidth',
                {{ get: () => {WIDE} }});
            h.online();
            await h.load('bodies');
            return {{ card: satCard() }};
        """, sim)
        svg = got["card"]["svg"]
        assert svg["labels"], sim
        for label in svg["labels"]:
            own = [m for m in svg["moons"] if m["moon"] == label["moon"]]
            assert len(own) == 1, f"{sim}: {label['moon']} has no marker of its own"
            assert not _overlaps(_label_box(label), _marker_box(own[0])), \
                f"{sim}: {label['moon']}'s name is on its own marker"
        # The two the second look was about, each a chevron at an edge with its name clear of
        # it (Iapetus's sat on its chevron before the fix). Titan is the chevron on 2032's
        # drawing only: at tonight's height the figure reaches its dot, which the test above
        # is the one that says so.
        for moon in {"2026-11-02": ("Iapetus",),
                     "2032-05-15": ("Iapetus", "Titan")}[sim]:
            marker = [m for m in svg["moons"] if m["moon"] == moon][0]
            label = [l for l in svg["labels"] if l["moon"] == moon][0]
            assert marker["tag"] == "path", f"{sim}: {moon} is a chevron on an edge"
            box = _marker_box(marker)
            assert (label["y"] + 7 <= box[2]) or (label["y"] - 7 >= box[3]), \
                f"{sim}: {moon}'s name is clear of its chevron, above it or below it"


def test_a_chevron_points_the_way_its_moon_is(tmp_path):
    """Anton's third look: the mark at the figure's edge is the line to the moon drawn, not a
    flag on the edge -- a chevron that leaves through the bottom of the figure points down,
    and to the side its moon is on, not merely left or right. On 1 Jun 2033 Rhea is down and
    a little to the left of the centre and its mark is at the bottom edge, and Iapetus that
    night is off to the left and its mark is on the left edge: in both, the tip is the point
    of the three furthest along the line to the moon and both components of where it sits
    from the centre carry the moon's own sign."""
    for moon in ("Rhea", "Iapetus"):
        got = _run_saturn(tmp_path, f"""
            Object.defineProperty(h.$('solar-saturn-diagram'), 'clientWidth',
                {{ get: () => {WIDE} }});
            h.online();
            await h.load('bodies');
            const m = d.moons.filter(x => x.name === '{moon}')[0];
            const g = svgOf('solar-saturn-diagram').children
                .filter(c => c.tagName === 'g' && c.attrs['data-moon'] === '{moon}')[0];
            return {{ card: satCard(), H: satH({WIDE}), drawn: {{x: m.x, y: m.y}},
                     d: g.children[0].attrs.d }};
        """, "2033-06-01")
        svg = got["card"]["svg"]
        cx, cy = WIDE / 2, got["H"] / 2
        mx = cx + got["drawn"]["x"] * svg["scale"]
        my = cy + got["drawn"]["y"] * svg["scale"]
        pts = _points(got["d"])
        assert len(pts) == 3, f"{moon} is a chevron on an edge"
        tip, arms = pts[1], (pts[0], pts[2])
        assert _on_ray(tip, (cx, cy), (mx, my)), f"{moon}'s tip is on the line to its moon"
        mid = ((arms[0][0] + arms[1][0]) / 2, (arms[0][1] + arms[1][1]) / 2)
        assert math.dist(tip, (cx, cy)) > math.dist(mid, (cx, cy)), \
            f"{moon}'s tip is the point furthest the way its moon is, the arms behind it"
        # The mark's own direction from the centre, axis by axis, is the moon's: down for a
        # moon below the plane, left for one to the left -- a downward-left chevron where the
        # moon is down and to the left, not a fixed mark on the edge
        for axis, own, off in (("x", tip[0] - cx, got["drawn"]["x"]),
                               ("y", tip[1] - cy, got["drawn"]["y"])):
            assert off and math.copysign(1, own) == math.copysign(1, off), \
                f"{moon} leaves through the edge its moon is towards, along {axis}"
        # Concretely, the two cases: Rhea's moon is below the plane, so its chevron is at the
        # bottom and points down; Iapetus's is off to the left, so its chevron is at the left
        # edge, level with its moon
        if moon == "Rhea":
            assert got["drawn"]["y"] > 0 and tip[1] > mid[1] and \
                tip[1] > max(arms[0][1], arms[1][1]), "a downward chevron at the bottom edge"
        else:
            assert got["drawn"]["x"] < -1 and tip[0] < mid[0] and tip[0] < arms[0][0]
            assert abs(tip[1] - mid[1]) < 0.5, "and it leaves through the left, not the top"


def test_a_moon_s_name_is_never_set_on_the_planet_or_its_rings(tmp_path):
    """Anton's third look: a name belongs beside its moon, and the planet and the rings are
    not places to put one -- a moon behind or in front of the globe has its name outside the
    planet, in the nearest lane that is free, even though the lane nearest its own marker is
    over the disc. On 8 Aug 2038 Mimas is properly on the disc, its own marker inside the
    planet's own box, so the lane nearest it lands on the globe and is refused. Checked at
    both widths, against the globe's own box and the ring system's, on that night and on
    14 Mar 2027 and 4 Apr 2037, where the moons are inside the globe's own radius."""
    for sim in ("2027-03-14", "2037-04-04", "2038-08-08"):
        for width in (720, WIDE):
            got = _run_saturn(tmp_path, f"""
                Object.defineProperty(h.$('solar-saturn-diagram'), 'clientWidth',
                    {{ get: () => {width} }});
                h.online();
                await h.load('bodies');
                return {{ card: satCard(), H: satH({width}), B: t.rings.B,
                         radiusRs: t.rings.radiusRs,
                         drawn: d.moons.map(m => ({{name: m.name, x: m.x, off: m.off}})) }};
            """, sim)
            svg = got["card"]["svg"]
            cx, cy, k = width / 2, got["H"] / 2, svg["scale"]
            rw = got["radiusRs"] * k
            rh = rw * abs(math.sin(math.radians(got["B"])))
            planet = (cx - k - 2, cx + k + 2, cy - k - 2, cy + k + 2)
            rings = (cx - rw - 2, cx + rw + 2, cy - rh - 2, cy + rh + 2)
            assert svg["labels"], f"{sim}: at {width} px"
            for label in svg["labels"]:
                box = _label_box(label)
                assert not _overlaps(box, planet), \
                    f"{sim} at {width}: {label['moon']}'s name is on the globe"
                assert not _overlaps(box, rings), \
                    f"{sim} at {width}: {label['moon']}'s name is on the rings"
            # Which is the fix: there really are moons within the globe's own radius here --
            # this is the case the lanes are for -- and a moon on the disc is named, its name
            # above the planet or below it, never across it
            inside = [d for d in got["drawn"] if abs(d["x"] * k) < k and not d["off"]]
            assert inside, f"{sim} at {width}: a moon on the disc, which is the case here"
            named = [d for d in inside if any(l["moon"] == d["name"] for l in svg["labels"])]
            assert named, f"{sim} at {width}: a moon on the disc is named"
            for moon in named:
                label = [l for l in svg["labels"] if l["moon"] == moon["name"]][0]
                assert (label["y"] + 7 <= planet[2]) or (label["y"] - 7 >= planet[3]), \
                    f"{sim} at {width}: {moon['name']}, on the disc, is named off it"


def test_mimas_and_enceladus_are_named_only_where_a_lane_is_free(tmp_path):
    """Anton's third look: the two smallest moons keep their small dots and their tooltips
    whatever happens, and a name is set for them only where the lane search finds a lane
    free outright -- the five the figure always names are seen to first. At the card's width
    on 2 Nov 2026 both earn a name; on 2 Nov 2031, where the tilts put more of the others
    into the figure, Mimas does not and its dot stands unnamed, while the same night drawn
    at 720 px, where there are fewer names to place, names it again."""
    named = {}
    for sim in ("2026-11-02", "2031-11-02"):
        for width in (720, WIDE):
            got = _run_saturn(tmp_path, f"""
                Object.defineProperty(h.$('solar-saturn-diagram'), 'clientWidth',
                    {{ get: () => {width} }});
                h.online();
                await h.load('bodies');
                return {{ card: satCard() }};
            """, sim)
            svg = got["card"]["svg"]
            names = {l["moon"] for l in svg["labels"]}
            named[(sim, width)] = sorted(n for n in names if n in ("Mimas", "Enceladus"))
            # The five the figure always names are always named, at every width
            assert {"Titan", "Rhea", "Tethys", "Dione", "Iapetus"} <= names, f"{sim} at {width}"
            # And the two small ones are always there as dots, named or not, with their own
            # tooltips: a name is what is optional, never the moon
            for moon in ("Mimas", "Enceladus"):
                marker = [m for m in svg["moons"] if m["moon"] == moon]
                assert len(marker) == 1, f"{sim} at {width}: {moon} has no marker"
                marker = marker[0]
                assert marker["tag"] == "circle" and marker["r"] == 2.5, \
                    f"{sim} at {width}: {moon} keeps the small dot"
                assert "solar-saturn-moon-small" in marker["cls"]
                assert marker["title"].startswith(moon + "\n"), "and its tooltip"
                assert len([l for l in svg["labels"] if l["moon"] == moon]) == (1 if moon in names else 0), \
                    f"{sim} at {width}: {moon} named once, or not at all"
            # A name, where there is one, is in a lane that clears every marker, its own
            # included -- the same rule the five are held to
            for label in svg["labels"]:
                own = [m for m in svg["moons"] if m["moon"] == label["moon"]][0]
                assert not _overlaps(_label_box(label), _marker_box(own)), \
                    f"{sim} at {width}: {label['moon']}'s name is on its own marker"
    assert named[("2026-11-02", WIDE)] == ["Enceladus", "Mimas"], "room for both, tonight"
    assert named[("2031-11-02", WIDE)] == ["Enceladus"], \
        "no lane free for Mimas at the card's own width"
    assert named[("2031-11-02", 720)] == ["Enceladus", "Mimas"], \
        "and a lane free for it at 720 px, the same night"


def test_the_rings_cross_the_globe_in_front_of_it(tmp_path):
    """The rings' near half is drawn over the globe, in the module's own paths and the same
    three ring regions the far half is, so the band across the globe is the ring system at
    the width it has at the sides, with the Cassini gap in it. The band has to read as
    brighter than the planet it crosses, so it is drawn a second time on the globe's own
    near half-disc, in the rings' own colours at full strength -- and the planet is the pale
    yellow it is, a shade paler than the ring fills rather than darker, so what tells the
    band apart is the thin dark outline on it, in both themes. The shadow the rings cast
    stays a band of its own, darker and narrower, beside it."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), rings: d.rings, B: t.rings.B, shadow: d.shadow,
                 radiusRs: t.rings.radiusRs, edgeInner: t.rings.edges[0].inner };
    """, "2026-11-02")
    svg = got["card"]["svg"]
    layers = svg["layers"]
    assert [l["clip"] for l in layers] == ["url(#solar-saturn-far)", None,
                                          "url(#solar-saturn-near)", "url(#solar-saturn-front)"]
    far, globe, near, front = layers
    assert [r["d"] for r in near["rings"]] == [r["d"] for r in far["rings"]] == \
        [r["d"] for r in got["rings"]], "one ring system, drawn twice, in the module's paths"
    assert [r["d"] for r in front["rings"]] == [r["d"] for r in got["rings"]], \
        "and once more over the globe, the same paths"
    assert [r["cls"] for r in near["rings"]] == ["solar-saturn-ring solar-saturn-ring-c",
        "solar-saturn-ring solar-saturn-ring-b", "solar-saturn-ring solar-saturn-ring-a"]
    assert globe["globe"] == [1] and far["globe"] == [] and near["globe"] == []
    assert globe["shadow"] and globe["shadow"][0]["clip"] == "url(#solar-saturn-globe)", \
        "and the rings' shadow is on the globe, beside the band"
    # B is negative and the view is normal, so the near half is the upper one -- and the far
    # half is the other, hidden behind the globe where the globe is
    assert got["B"] < 0
    assert [c["y"] for c in svg["clips"][:2]] == [80, -2]
    # The pass over the globe is clipped to the globe's own near half-disc, so it cannot
    # paint the band anywhere the globe is not
    assert svg["clips"][3]["id"] == "solar-saturn-front" and svg["clips"][3]["d"].endswith("Z")
    assert front["cls"] == "solar-saturn-front"

    css = (Path(__file__).resolve().parent.parent
           / "static" / "css" / "solar_system.css").read_text()
    rule = lambda sel: css[css.index(sel + " {"):css.index("}", css.index(sel + " {"))]
    # One colour for the rings, wherever they are drawn: the sides, the band over the globe
    # and the darker globe under it are what the card's own rules say they are
    for key in ("b", "a", "c"):
        assert "fill: var(--solar-body-saturn);" in rule(
            f"#bodies-tab .solar-saturn-svg .solar-saturn-ring-{key}")
        assert "fill: var(--solar-body-saturn);" not in rule(
            f"#bodies-tab .solar-saturn-svg .solar-saturn-front .solar-saturn-ring-{key}"), \
            "the front pass keeps the ring's own colour, and only its strength is its own"
    assert "fill: var(--solar-saturn-globe);" in rule(
        "#bodies-tab .solar-saturn-svg .solar-saturn-globe")
    assert "fill: var(--solar-saturn-shadow);" in rule(
        "#bodies-tab .solar-saturn-svg .solar-saturn-shadow")
    scoped = rule("#solar-saturn")
    assert "--solar-saturn-globe: " in scoped and "--solar-body-saturn" not in scoped, \
        "the globe's own value, in the card's own scope"
    # The band over the globe is the rings' own colour at full strength, where the sides
    # have it at less: over the body colour their own strengths would leave only a stripe
    strength = lambda sel: float(rule(sel).split("fill-opacity: ")[1].split(";")[0])
    sides = strength("#bodies-tab .solar-saturn-svg .solar-saturn-ring-b")
    over = strength("#bodies-tab .solar-saturn-svg .solar-saturn-front .solar-saturn-ring-b")
    assert over == 1 and sides < over, "the band is the ring colour at full strength"
    assert strength("#bodies-tab .solar-saturn-svg .solar-saturn-front .solar-saturn-ring-a") > \
        strength("#bodies-tab .solar-saturn-svg .solar-saturn-ring-a"), "and so is the A ring"
    # The globe is the planet's own pale yellow and the rings the same material, so the two
    # are told apart by a thin dark outline on the band that crosses it, not by darkening the
    # planet: the planet is not the rings' own value, and what has to be dark is the line
    globe_fill = rule("#bodies-tab .solar-saturn-svg .solar-saturn-globe")
    assert "fill: var(--solar-saturn-globe);" in globe_fill
    disc = "#d3bd8c"
    globe_tone = scoped.split("--solar-saturn-globe: ")[1].split(";")[0]
    assert globe_tone.lower() != disc, "a colour of its own, not the rings' own"
    edge_tone = scoped.split("--solar-saturn-ring-edge: ")[1].split(";")[0]
    lit = [int(globe_tone[i:i + 2], 16) for i in (1, 3, 5)]
    line = [int(edge_tone[i:i + 2], 16) for i in (1, 3, 5)]
    assert sum(lit) - sum(line) > 90, "the outline a good deal darker: 30 a channel, at least"
    assert sum(lit) > sum(int(disc[i:i + 2], 16) for i in (1, 3, 5)), \
        "and the globe paler than the rings that cross it, not darker"
    # The outline is the front pass's own, on the ring paths themselves -- one thin line on
    # each region's edges, which is where the rings really are and carries the Cassini gap
    # with them -- and the sides' own faint stroke is left as it was
    band = rule("#bodies-tab .solar-saturn-svg .solar-saturn-front .solar-saturn-ring")
    assert "stroke: var(--solar-saturn-ring-edge);" in band
    assert "stroke-opacity: 1;" in band, "at full strength, unlike the sides'"
    assert float(band.split("stroke-width: ")[1].split(";")[0]) < 1, "a thin line"
    sides_rule = rule("#bodies-tab .solar-saturn-svg .solar-saturn-ring")
    assert "stroke: var(--text-muted);" in sides_rule and \
        float(sides_rule.split("stroke-opacity: ")[1].split(";")[0]) < 1, "the sides as they were"
    # Both themes: the card carries no theme of its own, so the same two values are what
    # either one draws -- the globe paler than the rings and the outline darker than both
    assert ':root[data-theme="dark"] #solar-saturn {' not in css, \
        "one set of values, for both themes"

    # The shadow's band: the darker of the two, and the narrower -- the ring band across the
    # globe is the whole ring system's own width there, the C ring's inner edge out to the A
    # ring's outer edge, projected by the tilt the module reports
    shadow = rule("#bodies-tab .solar-saturn-svg .solar-saturn-shadow")
    assert "fill: var(--solar-saturn-shadow);" in shadow
    assert float(shadow.split("fill-opacity: ")[1].split(";")[0]) <= 0.6, "the darker band"

    assert got["shadow"], "the shadow has an outline of its own"
    band = math.sin(math.radians(abs(got["B"]))) * (got["radiusRs"] - got["edgeInner"])
    assert band > 0
    shadow_box = _bbox(got["shadow"])
    assert (shadow_box[3] - shadow_box[1]) / svg["scale"] < band, \
        "the shadow band is the narrower of the two, as the brief says"


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
