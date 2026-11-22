"""A direct open waits for Aladin's engine (A.init) before building the viewer.

Aladin Lite v3 loads its WASM asynchronously and exposes A.init for it; A.aladin()
constructs a WebClient from it, so calling it on a page still loading throws
"undefined is not an object (evaluating 'new A.WebClient')". That is exactly the
direct-open case: ?tab=framing, a framing URL, or the restored last tab, all of
which open the assistant during page load.

Run in the harness of tests/test_framing_reference_js.py. The stand-in logs the
order of A.init settling and A.aladin() being called, which is the whole fix.
"""

import pytest

from tests.test_framing_reference_js import CASES, NODE, run_driver

_MAIN = r"""
const out = {};
(async () => {
    const c = input.cases[input.caseKey];

    // A.init still pending when the open runs: the viewer is built only after it.
    {
        const b = buildCase(c, { aladinReady: false, aInitTicks: 3 });
        await b.api.open('');
        for (let k = 0; k < 10; k++) await settle();
        out.withInit = { log: b.log.slice(), aladinCreated: !b.api.aladinIsNull() };
    }

    // No A.init (an Aladin that has no such property): the open still builds the viewer.
    {
        const b = buildCase(c, { aladinReady: false });
        await b.api.open('');
        for (let k = 0; k < 10; k++) await settle();
        out.noInit = { log: b.log.slice(), aladinCreated: !b.api.aladinIsNull() };
    }

    fs.writeFileSync(outFile, JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_direct_open_waits_for_aladin_init(tmp_path):
    out = run_driver(_MAIN, {"cases": CASES, "caseKey": "plain"}, tmp_path)

    log = out["withInit"]["log"]
    assert "init" in log and "create" in log
    # The order is the fix: the viewer is built after the engine has settled.
    assert log.index("init") < log.index("create")
    assert out["withInit"]["aladinCreated"] is True

    # An Aladin without A.init is unchanged: it still builds the viewer.
    assert out["noInit"]["aladinCreated"] is True
