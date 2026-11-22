"""The Framing tab's Result block and how the sky moves, in node.

Runs the real functions from static/js/graph_view_chart.js in the harness of
tests/test_framing_reference_js.py (same stand-in DOM and Aladin, same frozen "now"):

- the saved framing's RA/Dec (JNow) and angle show as soon as /api/get_framing answers,
  before Aladin exists, and stay when the sky can't load (offline);
- the "Differs from saved" marker follows the view centre and the rotation;
- clicks on the sky (plain or Shift) never move the frame: only dragging moves the sky;
- "Open saved framing" during the first open runs after it (Aladin created once, the
  clicked framing is what stays on screen).
"""

import pytest

from tests.test_framing_reference_js import CASES, NODE, REFERENCE, run_driver

_MAIN = r"""
const out = {};
const textOf = els => ({
    ra: els['ra-readout'].value, dec: els['dec-readout'].value, angle: els['result-angle'].value,
    chip: els['result-saved-chip'].hidden ? null : els['result-saved-chip'].textContent,
    line: els['result-saved-line'].hidden ? null : els['result-saved-line'].textContent,
});

(async () => {
    const c = input.cases[input.caseKey];

    // 1. First open: saved values before Aladin exists (the survey probe is still running)
    {
        const b = buildCase(c, { aladinReady: false, probeDelay: 5 });
        const p = b.api.firstOpen();
        await settle();   // /api/get_framing answered, the probe has not
        out.beforeAladin = Object.assign(textOf(b.els), { aladinNull: b.api.aladinIsNull() });
        await p; for (let k = 0; k < 10; k++) await settle();
        out.afterAladin = Object.assign(textOf(b.els), { aladinNull: b.api.aladinIsNull() });
    }

    // 2. Offline (no offline sky): the sky can't load, the saved values stay
    {
        const b = buildCase(c, { aladinReady: false });
        b.ctx.navigator.onLine = false;
        await b.api.firstOpen();
        await settle();
        out.offline = Object.assign(textOf(b.els), { aladinNull: b.api.aladinIsNull() });
    }

    // 3. Nothing saved: no marker
    {
        const b = buildCase(c, { aladinReady: false });
        b.setResponse({ status: 'not_found' });
        await b.api.firstOpen(); await settle();
        out.nothingSaved = textOf(b.els);
    }

    // 4. Differs marker: drag the sky (the view centre moves), rotate, and back
    {
        const b = buildCase(c, { aladinReady: false });
        await b.api.firstOpen(); await settle();
        const [ra0, dec0] = b.api.view();
        out.opened = textOf(b.els);
        b.api.moveView(ra0 + 0.05, dec0 - 0.03); b.api.updateReadoutFromCenter();
        out.dragged = textOf(b.els);
        b.api.moveView(ra0, dec0); b.api.updateReadoutFromCenter();
        out.draggedBack = textOf(b.els);
        const rot = b.els['framing-rotation'];
        const before = rot.value;
        rot.value = (parseFloat(before) + 10) % 360; b.ctx.updateFramingChart(false);
        out.rotated = textOf(b.els);
        rot.value = before; b.ctx.updateFramingChart(false);
        out.rotatedBack = textOf(b.els);
        // after Save the button refresh re-reads the saved framing: the new values are "saved"
        b.api.moveView(ra0 + 0.05, dec0 - 0.03); b.api.updateReadoutFromCenter();
        b.setResponse(Object.assign({}, c.framing, { ra: ra0 + 0.05, dec: dec0 - 0.03 }));
        b.api.showSaved(); await settle();
        out.afterSave = textOf(b.els);
    }

    // 5. Clicks on the sky don't move the frame
    {
        const b = buildCase(c, { aladinReady: false });
        await b.api.firstOpen(); await settle();
        const host = b.els['aladin-lite-div'];
        const before = { view: b.api.view(), text: textOf(b.els) };
        const types = Object.keys(host.listeners);
        for (const type of types) {
            for (const shiftKey of [false, true]) {
                for (const f of host.listeners[type]) {
                    try {
                        f({ type, shiftKey, clientX: 123, clientY: 45, button: 0, deltaY: 0, deltaMode: 0,
                            ctrlKey: true, preventDefault() {}, stopPropagation() {} });
                    } catch (e) { /* a listener needing more of the DOM is not a centre change */ }
                }
            }
        }
        b.api.updateReadoutFromCenter();
        out.clicks = { types, viewBefore: before.view, viewAfter: b.api.view(),
                       textBefore: before.text, textAfter: textOf(b.els) };
    }

    // 6. "Open saved framing" while the first (plain) open still runs: the first open's
    //    probe is slower, so without the queue it would finish last and overwrite the
    //    clicked framing with the defaults. Queued, the opens run one after the other.
    for (const queued of [true, false]) {
        const b = buildCase(c, { aladinReady: false, probeDelay: [6, 1] });
        b.setResponse({ status: 'not_found' });   // the first open: defaults (the object)
        const first = b.api.firstOpen().finally(() => b.api.setOpening(null));
        b.api.setOpening(first);
        await settle();
        b.setResponse(c.framing);
        b.api.showSaved(); await settle();
        // Structural, not text-based: the short label is "Open", so find the ghost-styled
        // saved-framing button by its class and data attribute instead.
        const openBtn = b.els['project-framing-buttons'].children
            .find(x => x.getAttribute('data-saved-framing') === 'true'
                       && x.className === 'inline-button-ghost');
        if (!queued) b.api.setOpening(null);      // as before: the button opened straight away
        const second = openBtn.onclick();
        await Promise.all([first, second]);
        for (let k = 0; k < 10; k++) await settle();
        out[queued ? 'queued' : 'unqueued'] = {
            creates: b.log.filter(l => l === 'create').length,
            view: b.api.view(), rotation: b.els['framing-rotation'].value,
            text: textOf(b.els),
        };
    }

    fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.fixture(scope="module", params=["rotated", "mosaic"])
def out(request, tmp_path_factory):
    if NODE is None:
        pytest.skip("node is not installed")
    res = run_driver(_MAIN, {"cases": CASES, "caseKey": request.param},
                     tmp_path_factory.mktemp("framing_result"))
    res["case"] = request.param
    return res


def _pinned(case):
    ref = REFERENCE[case]["fresh"]
    return ref["readout"]["ra"], ref["readout"]["dec"], ref["rotationBadge"]


def test_saved_values_show_before_aladin_loads(out):
    ra, dec, angle = _pinned(out["case"])
    assert out["beforeAladin"] == {"ra": ra, "dec": dec, "angle": angle, "chip": "Saved framing",
                                   "line": None, "aladinNull": True}
    # once the sky is live the same values come from the view: still "saved"
    assert out["afterAladin"] == dict(out["beforeAladin"], aladinNull=False)


def test_saved_values_stay_when_the_sky_cannot_load(out):
    ra, dec, angle = _pinned(out["case"])
    assert out["offline"] == {"ra": ra, "dec": dec, "angle": angle, "chip": "Saved framing",
                              "line": None, "aladinNull": True}


def test_no_marker_without_a_saved_framing(out):
    assert out["nothingSaved"]["chip"] is None and out["nothingSaved"]["line"] is None


def test_differs_marker_follows_the_view_and_the_rotation(out):
    ra, dec, angle = _pinned(out["case"])
    saved_line = f"Saved: RA {ra} · Dec {dec} · Angle {angle}"
    assert out["opened"]["chip"] == "Saved framing"
    dragged = out["dragged"]
    assert (dragged["ra"], dragged["dec"]) != (ra, dec)
    assert dragged["chip"] == "Differs from saved" and dragged["line"] == saved_line
    assert out["draggedBack"] == out["opened"]
    assert out["rotated"]["angle"] != angle
    assert out["rotated"]["chip"] == "Differs from saved" and out["rotated"]["line"] == saved_line
    assert out["rotatedBack"] == out["opened"]
    assert out["afterSave"]["chip"] == "Saved framing" and out["afterSave"]["line"] is None


def test_clicking_the_sky_does_not_move_the_frame(out):
    clicks = out["clicks"]
    assert "click" not in clicks["types"] and "mouseup" not in clicks["types"]
    assert clicks["viewAfter"] == clicks["viewBefore"]
    assert clicks["textAfter"] == clicks["textBefore"]


def test_open_saved_during_the_first_open_wins_and_creates_aladin_once(out):
    ra, dec, angle = _pinned(out["case"])
    framing = CASES[out["case"]]["framing"]
    queued = out["queued"]
    assert queued["creates"] == 1
    assert queued["view"] == [framing["ra"], framing["dec"]]
    assert float(queued["rotation"]) == framing["rotation"]
    assert queued["text"] == {"ra": ra, "dec": dec, "angle": angle, "chip": "Saved framing", "line": None}
    # the stand-in reproduces the race: without the queue the slower first open lands last
    # and the clicked framing is lost
    unqueued = out["unqueued"]
    assert unqueued["creates"] == 1
    assert (unqueued["view"], unqueued["rotation"]) != (queued["view"], queued["rotation"])
