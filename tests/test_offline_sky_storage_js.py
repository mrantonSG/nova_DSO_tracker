"""The Offline sky storage block's two conditioned lines, in node.

Two sentences in the Configuration -> General "Offline sky" card must follow the
selected limit, on page load and after every change:

* "Downloaded sky (...) already exceeds this limit." -- only when the measured
  pinned bytes exceed the limit. Regression: this was once built from the
  ``.pinned`` sidecar, which a pending eviction pass had not reconciled, so it
  showed a stale-high number and stayed wrong because nothing re-evaluated it.
* "Sky browsing keeps about 1 GB of disk space free." -- only for "No limit".
  With a limit chosen, the disk is not what bounds the store, and showing the
  sentence anyway was the bug this pins.

The real functions are cut out of static/js/config_form.js by name and run
against a small stand-in DOM, so what is pinned is the real applyStatus /
applyNoLimitNote / applyLimitOptions, not a copy of them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFIG_JS = REPO / "static" / "js" / "config_form.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const [jsFile, casesFile, outFile] = process.argv.slice(2);
const src = fs.readFileSync(jsFile, 'utf8');

// --- cut a top-level function or `const x = function` out of the file by name ---
function cutFn(name) {
  const patterns = [
    new RegExp('\\n(\\s*)function ' + name + '\\s*\\(', 'g'),
    new RegExp('\\n(\\s*)const ' + name + '\\s*=\\s*function', 'g'),
  ];
  for (const re of patterns) {
    const m = re.exec(src);
    if (!m) continue;
    const start = m.index + 1;
    // brace-match from the first '{' after the signature
    let i = src.indexOf('{', m.index);
    let depth = 0;
    for (; i < src.length; i++) {
      if (src[i] === '{') depth++;
      else if (src[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    return src.slice(start, i);
  }
  throw new Error('function not found: ' + name);
}

// --- the stand-in DOM: just enough for these functions ---
function makeEl(id) {
  const el = {
    id: id,
    _classes: new Set(),
    textContent: '',
    _options: [],
    value: '',
    dataset: {},
    classList: {
      add: c => el._classes.add(c),
      remove: c => el._classes.delete(c),
      contains: c => el._classes.has(c),
      toggle: (c, on) => { if (on === undefined) { el._classes.has(c) ? el._classes.delete(c) : el._classes.add(c); }
                           else { on ? el._classes.add(c) : el._classes.delete(c); } },
    },
    querySelectorAll: sel => {
      if (sel === 'option[data-custom]') return el._options.filter(o => o.dataset.custom);
      if (sel === 'option[value="none"]') return el._options.filter(o => o.value === 'none');
      return [];
    },
    querySelector: sel => {
      const r = el.querySelectorAll(sel);
      return r.length ? r[0] : null;
    },
    insertBefore: (node, ref) => {
      const i = el._options.indexOf(ref);
      if (i === -1) el._options.push(node); else el._options.splice(i, 0, node);
    },
    appendChild: node => { el._options.push(node); return node; },
  };
  return el;
}

const els = {
  'offline-sky-usage': makeEl('offline-sky-usage'),
  'offline-sky-limit': makeEl('offline-sky-limit'),
  'offline-sky-clear': makeEl('offline-sky-clear'),
  'offline-sky-over-limit': makeEl('offline-sky-over-limit'),
  'offline-sky-no-limit-note': makeEl('offline-sky-no-limit-note'),
};
// The limit select's real options: four presets + No limit (with its label).
els['offline-sky-limit']._options = [
  {value: '2048', textContent: '2 GB', dataset: {}, selected: false},
  {value: '5120', textContent: '5 GB', dataset: {}, selected: false},
  {value: '10240', textContent: '10 GB', dataset: {}, selected: false},
  {value: '25600', textContent: '25 GB', dataset: {}, selected: false},
  {value: 'none', textContent: 'No limit', dataset: {}, selected: false},
];

const document = {
  getElementById: id => els[id] || null,
  createElement: tag => { const e = makeEl('created'); e.tagName = tag; return e; },
};
const window = {
  t: function (key, params) {
    const TABLE = {
      offline_sky_usage: 'Using {used} of {limit}',
      offline_sky_usage_no_limit: 'Using {used}',
      offline_sky_over_limit: 'Downloaded sky ({pinned}) already exceeds this limit. Viewed tiles are not kept.',
      none: 'None',
    };
    let s = TABLE[key];
    if (s === undefined) { global.__missing = global.__missing || []; global.__missing.push(key); return key; }
    if (params) Object.entries(params).forEach(([k, v]) => { s = s.split('{' + k + '}').join(v); });
    return s;
  },
};
const novaConfirm = () => Promise.resolve(false);

// --- run the cut functions in one scope ---
const sandbox = {document, window, novaConfirm, console, Number, JSON, Object, Array, Math, String,
                 module: {}, exports: {}};
vm.createContext(sandbox);

// The constants applyStatus/applyLimitOptions close over.
sandbox.usageEl = els['offline-sky-usage'];
sandbox.limitEl = els['offline-sky-limit'];
sandbox.overEl = els['offline-sky-over-limit'];
sandbox.noLimitEl = els['offline-sky-no-limit-note'];
sandbox.section = {dataset: {presets: JSON.stringify([2048, 5120, 10240, 25600]),
                            statusUrl: '/api/offline-sky/status'}};
sandbox.presets = [2048, 5120, 10240, 25600];
sandbox.noneLabel = 'No limit';

// The real formatters and handlers, cut from config_form.js. A `const`/`function`
// declared at this scope is visible to functions run in the same context, so the
// declarations go first and the closures resolve against them.
const decls = [
  'const OFFLINE_SKY_GB = 1024 * 1024 * 1024;',
  cutFn('formatSkySize'),
  cutFn('formatSkyLimit'),
  cutFn('applyNoLimitNote'),
  cutFn('applyStatus'),
  cutFn('applyLimitOptions'),
].join('\n');
vm.runInContext(decls, sandbox);

const cases = JSON.parse(fs.readFileSync(casesFile, 'utf8'));
const out = [];
for (const c of cases) {
  // Reset the elements the way a fresh page would have them.
  els['offline-sky-over-limit']._classes = new Set(c.over_initial_hidden ? ['hidden'] : []);
  els['offline-sky-no-limit-note']._classes = new Set(c.hint_initial_hidden ? ['hidden'] : []);
  els['offline-sky-over-limit'].textContent = '';
  sandbox.applyStatus(c.status);
  out.push({
    name: c.name,
    overHidden: els['offline-sky-over-limit']._classes.has('hidden'),
    overText: els['offline-sky-over-limit'].textContent,
    hintHidden: els['offline-sky-no-limit-note']._classes.has('hidden'),
    usage: els['offline-sky-usage'].textContent,
  });
}
fs.writeFileSync(outFile, JSON.stringify(out, null, 1));
"""


def _run(cases):
    driver = REPO / "tests" / "_tmp_offline_storage_driver.js"
    cases_path = REPO / "tests" / "_tmp_offline_storage_cases.json"
    out_path = REPO / "tests" / "_tmp_offline_storage_out.json"
    driver.write_text(_DRIVER)
    cases_path.write_text(json.dumps(cases))
    try:
        proc = subprocess.run([NODE, str(driver), str(CONFIG_JS), str(cases_path), str(out_path)],
                              capture_output=True, text=True, cwd=str(REPO), timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(out_path.read_text())
    finally:
        for p in (driver, cases_path, out_path):
            try:
                p.unlink()
            except OSError:
                pass


GB = 1024 ** 3

# The real shapes the route returns. pinned is the MEASURED value (the fix).
CASES = [
    {"name": "no limit: hint shown, warning hidden",
     "status": {"limit_mb": None, "pinned": 150 * 1024 * 1024, "used": int(1.7 * GB),
                "browsed": int(1.5 * GB), "evicting": False},
     "over_initial_hidden": True, "hint_initial_hidden": True},
    {"name": "2 GB limit over 150 MB pinned: hint hidden, warning hidden",
     "status": {"limit_mb": 2048, "pinned": 150 * 1024 * 1024, "used": int(1.7 * GB),
                "browsed": int(1.55 * GB), "evicting": False},
     "over_initial_hidden": False, "hint_initial_hidden": False},
    {"name": "2 GB limit with 3 GB pinned: warning shown, hint hidden",
     "status": {"limit_mb": 2048, "pinned": int(3 * GB), "used": int(4.5 * GB),
                "browsed": int(1.5 * GB), "evicting": False},
     "over_initial_hidden": True, "hint_initial_hidden": True},
    {"name": "switching back to No limit shows the hint again",
     "status": {"limit_mb": None, "pinned": int(3 * GB), "used": int(4.5 * GB),
                "browsed": int(1.5 * GB), "evicting": False},
     "over_initial_hidden": False, "hint_initial_hidden": False},
]


def test_the_hint_is_shown_only_for_no_limit():
    out = {r["name"]: r for r in _run(CASES)}
    for name, expect in [
        ("no limit: hint shown, warning hidden", False),
        ("2 GB limit over 150 MB pinned: hint hidden, warning hidden", True),
        ("2 GB limit with 3 GB pinned: warning shown, hint hidden", True),
        ("switching back to No limit shows the hint again", False),
    ]:
        assert out[name]["hintHidden"] is expect, f"{name}: hintHidden"


def test_the_warning_follows_the_measured_pinned_bytes():
    out = {r["name"]: r for r in _run(CASES)}
    # 150 MB pinned under a 2 GB limit: nothing exceeds anything.
    assert out["2 GB limit over 150 MB pinned: hint hidden, warning hidden"]["overHidden"] is True
    # 3 GB pinned under a 2 GB limit: warned, and the number in it is the real one.
    warned = out["2 GB limit with 3 GB pinned: warning shown, hint hidden"]
    assert warned["overHidden"] is False
    assert "3 GB" in warned["overText"]
    # Under No limit the warning is off however big pinned is.
    assert out["no limit: hint shown, warning hidden"]["overHidden"] is True


def test_the_usage_line_reports_used_and_limit():
    out = {r["name"]: r for r in _run(CASES)}
    # A limit: the two-part sentence.
    assert out["2 GB limit over 150 MB pinned: hint hidden, warning hidden"]["usage"] == "Using 1.7 GB of 2 GB"


def test_the_usage_line_switches_to_the_no_limit_sentence():
    """With No limit the line must be its own sentence, not "of No limit":
    "Using 1.7 GB of No limit" reads as "of nothing"."""
    out = {r["name"]: r for r in _run(CASES)}
    no_limit = out["no limit: hint shown, warning hidden"]["usage"]
    assert no_limit == "Using 1.7 GB", no_limit
    assert "of No limit" not in no_limit
    # And back to the two-part sentence when a limit is chosen again -- the same
    # applyStatus call does both, so the swap is in place, not a reload.
    assert out["2 GB limit with 3 GB pinned: warning shown, hint hidden"]["usage"] == "Using 4.5 GB of 2 GB"


def test_the_swap_uses_distinct_msgids():
    """One msgid per sentence: the JS must not build the No-limit line by
    substituting "No limit" into the limit sentence, or translators would have
    no way to change the phrasing."""
    src = CONFIG_JS.read_text()
    assert "offline_sky_usage_no_limit" in src, "the No-limit sentence has its own key"
    assert "'offline_sky_usage'" in src and "'offline_sky_usage_no_limit'" in src
