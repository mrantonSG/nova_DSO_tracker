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
_PLACE = {"onDisc": "on the disc", "hidden": "hidden behind the disc",
          "front": "in front", "behind": "behind"}


def _where(m):
    return _PLACE["onDisc" if m["onDisc"] else "hidden" if m["behindDisc"]
                  else "front" if m["inFront"] else "behind"]


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
    assert len(got["after"]["tiles"]) == 3 and got["after"]["svg"]["label"] == "SAT DIAGRAM"


def test_saturn_card_shows_the_rings_the_moons_and_the_transits(tmp_path):
    """2 Nov 2026, London: Saturn 40 degrees up at 21:52. The three tiles -- the rings'
    opening angle with the trend in the note and the two-year curve, the disc and the
    magnitude -- the eight moons with what each is doing, the moons whose orbits cross the
    disc in the coming season, and the credit line.
    Every value is the module's own for the same inputs."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), t: { hm: t.hm, B: t.rings.B, trend: t.rings.trend,
                 face: t.rings.face, sunFace: t.rings.sunFace, sameFace: t.rings.sameFace,
                 aArcsec: t.rings.aArcsec, radiusRs: t.rings.radiusRs,
                 diameter: t.diameterArcsec, mag: t.mag, muted: t.muted,
                 moons: t.moons.map(m => ({name: m.name, labelled: m.labelled, side: m.side,
                     dist: m.dist, mag: m.mag, brightness: m.brightness, onDisc: m.onDisc,
                     behindDisc: m.behindDisc, inFront: m.inFront})),
                 crossing: t.transits.crossing.map(x => ({name: x.name, closestRs: x.closestRs,
                     utcMs: x.utcMs})),
                 titan: t.transits.titan, titanNext: t.transits.titanNext,
                 months: t.rings.months.length },
               source: source.credit, moonMag: moonMag };
    """, "2026-11-02")
    card, t = got["card"], got["t"]
    assert card["title"] == "SATURN · at " + t["hm"]
    assert not card["hidden"] and card["error"] is None and not card["muted"]

    # The tiles: the rings' opening angle, the disc, the magnitude. The rings' own tile
    # carries the trend as its note, the whole of the geometry in its tooltip, and the
    # two-year curve with the edge-on line across it
    tiles = card["tiles"]
    assert [tile["label"] for tile in tiles] == ["RINGS", "DISC", "MAG"]
    assert tiles[0]["value"] == ("−" if t["B"] < 0 else "+") + f"{abs(t['B']):.1f}°"
    assert tiles[0]["notes"] == [t["trend"]]
    assert tiles[0]["title"].split("\n") == [
        "RINGS TIP", "S-FACE US", "LIT S-FACE SUN", f"SPAN {t['aArcsec']:.1f}",
        f"EDGE {t['radiusRs']:.2f}", t["trend"]]
    assert t["sameFace"] and t["face"] == t["sunFace"] == "south"
    spark = tiles[0]["spark"]
    assert spark["label"] == "SPARK" and spark["viewBox"] == "0 0 66 22"
    assert len(spark["zero"]) == 1 and len(spark["head"]) == 1
    # One point per sample of the two years: 24 months at the module's own 15-day step
    n = t["months"]
    assert n == 49
    assert len(spark["line"]) == 1 and spark["line"][0].count("L ") == n - 1
    assert spark["line"][0].startswith("M ")
    assert tiles[1]["value"] == f"{t['diameter']:.1f}″" and tiles[1]["title"] == "DISC TIP"
    assert tiles[2]["value"] == f"{t['mag']:.1f}" and tiles[2]["title"] == "MAG TIP"

    # The moons: all eight, in the module's own order, each the name and what it is doing,
    # the whole of it in the tooltip; the ones the figure does not name, and Iapetus's own
    # side, are marked on the chip
    moons = card["moons"]
    assert [m["moon"] for m in moons] == [m["name"] for m in t["moons"]]
    assert len(moons) == 8
    for chip, m in zip(moons, t["moons"]):
        assert chip["parts"] == [{"cls": "solar-card-name", "text": m["name"]},
                                 {"cls": "solar-card-note", "text": _where(m)}]
        assert ("solar-card-moon-small" in chip["cls"]) == (not m["labelled"])
        assert chip["brightness"] == (m["brightness"] or None)
        lines = chip["title"].split("\n")
        assert lines[0] == f"{m['side']} of Saturn · {_where(m)}"
        assert lines[1] == f"{m['dist']:.1f} radii"
        assert lines[2] == f"mag {m['mag']:.1f}"
        assert ("IAP" in chip["title"]) == (m["name"] == "Iapetus")
    iap = [m for m in moons if m["moon"] == "Iapetus"][0]
    assert iap["brightness"] in ("bright", "faint") and len(iap["title"].split("\n")) == 5

    # The transits: this season has no Titan, so the line above the list says so and names
    # both the season it next crosses in and the small moons that do cross this one; the
    # list itself is every crossing the module found, innermost first
    assert t["titan"] is None
    assert card["transitsNone"] == "NO TITAN"
    others = [x["name"] for x in t["crossing"] if x["name"] != "Titan"]
    assert card["transitsNoneTitle"] == (f"NEXT {_full_day(t['titanNext']['utcMs'])}\n"
                                         f"SMALL {', '.join(others)}")
    assert card["transitsTitle"] == "TRANSIT TIP"
    assert [x["moon"] for x in card["transits"]] == [x["name"] for x in t["crossing"]]
    for chip, x in zip(card["transits"], t["crossing"]):
        assert chip["parts"][0] == {"cls": "solar-card-name", "text": x["name"]}
        assert chip["parts"][1]["cls"] == "solar-card-note"
        assert chip["parts"][1]["text"] == f"{x['closestRs']:.2f} radii"
        assert chip["title"] == (f"CLOSEST {x['closestRs']:.2f} on {_day(x['utcMs'])} "
                                 f"{_hm(x['utcMs'])}\nmag {got['moonMag'][x['name']]:.1f}")

    # The credit line: the magnitudes' two sources
    assert card["credit"] == "SRC " + got["source"]
    assert "JPL" in card["credit"] and "Cassini" in card["credit"]


def test_saturn_card_names_titan_s_crossing_when_the_season_has_one(tmp_path):
    """15 Jan 2026: seven of the eight cross the disc this season -- Iapetus never does --
    Titan among them, and the rings are within a degree and a half of edge-on. The line
    above the list is Titan's own crossing, and Titan is in the list with it."""
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
                                         f"{_day(titan['utcMs'])} {_hm(titan['utcMs'])}")
    # The season's crossings are the season's: tonight Titan is simply somewhere on its orbit
    assert got["tonight"][0]["onDisc"] + got["tonight"][0]["behindDisc"] <= 1
    assert "Titan" in [x["moon"] for x in card["transits"]]
    assert "Titan" in [m["moon"] for m in card["moons"]]


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
    for group, drawn, sky in zip(svg["moons"], got["drawn"], got["sky"]):
        assert group["moon"] == drawn["name"] == sky["name"]
        assert group["side"] == sky["side"]
        assert group["brightness"] == (sky["brightness"] or None)
        assert ("solar-saturn-moon-behind" in group["cls"]) == sky["behindDisc"]
        assert ("solar-saturn-moon-small" in group["cls"]) == (not drawn["labelled"])
        if drawn["off"]:
            assert group["tag"] == "path" and group["d"] is not None and group["r"] is None
        else:
            assert group["tag"] == "circle" and group["r"] == (4 if drawn["labelled"] else 2.5)
            assert group["cx"] == pytest.approx(360 + drawn["x"] * svg["scale"], abs=0.01)
            assert group["cy"] == pytest.approx(80 + drawn["y"] * svg["scale"], abs=0.01)
        assert group["title"].startswith(sky["name"] + "\n")
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
    assert len(got["card"]["moons"]) == got["moons"] == 8
    assert got["card"]["svg"]["moons"] and len(got["card"]["tiles"]) == 3


def test_the_faces_note_is_shown_only_when_the_two_faces_differ(tmp_path):
    """24 Mar 2025: the rings are within a tenth of a degree of edge-on and the Earth and
    the Sun are on opposite faces of them, so the tile says so. 2 Nov 2026: both are on the
    south face, and it says nothing."""
    got = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), B: t.rings.B, Bprime: t.rings.Bprime,
                 sameFace: t.rings.sameFace, face: t.rings.face, sunFace: t.rings.sunFace };
    """, "2025-03-24")
    assert not got["sameFace"] and got["face"] != got["sunFace"]
    assert (got["B"] < 0) != (got["Bprime"] < 0), "the two faces are on opposite sides"
    assert got["card"]["tiles"][0]["notes"] == ["opening", "FACES DIFFER"]
    assert "S-FACE US" in got["card"]["tiles"][0]["title"]
    assert "LIT N-FACE SUN" in got["card"]["tiles"][0]["title"]

    same = _run_saturn(tmp_path, """
        h.online();
        await h.load('bodies');
        return { card: satCard(), sameFace: t.rings.sameFace };
    """, "2026-11-02")
    assert same["sameFace"]
    assert same["card"]["tiles"][0]["notes"] == ["closing"]
    assert "FACES DIFFER" not in same["card"]["tiles"][0]["title"]


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
