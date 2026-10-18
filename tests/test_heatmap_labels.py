"""Unit checks for the two label helpers in static/js/heatmap_section.js.

uniqueRowLabels: Plotly merges heatmap rows with equal y labels, so a label that
repeats gets its object's name appended and every row keeps its own label; the
click goes label -> index, so the labels must stay unique.
localizedWeekLabels: the week labels in the page's language, made from the chunk
dates; the click goes label -> index -> dates[index], so the 52 labels must stay
unique in every language the app ships.

The file runs in node the way the page loads it (a plain script, window and document
stubbed), with TZ set west of UTC so a date read in local time lands a day early.
"""

import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SECTION_JS = REPO / "static" / "js" / "heatmap_section.js"
NODE = shutil.which("node")
LANGUAGES = ("en", "de", "es", "fr", "ja", "zh")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const [file, inFile, outFile] = process.argv.slice(2);
const context = { console: console, document: { documentElement: { lang: '' } } };
context.window = context;
context.addEventListener = () => {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
const L = context._heatmapLabels;

const out = JSON.parse(fs.readFileSync(inFile, 'utf8')).map(c => {
    if (c.call === 'rows') {
        const labels = c.labels.slice();
        return { id: c.id, result: L.uniqueRowLabels(labels, c.ids), untouched: labels.join('\n') === c.labels.join('\n') };
    }
    context.document.documentElement.lang = c.lang;
    return { id: c.id, result: L.localizedWeekLabels(c.labels, c.dates) };
});
fs.writeFileSync(outFile, JSON.stringify(out));
"""


def _run(tmp_path, cases):
    (tmp_path / "labels_driver.js").write_text(_DRIVER)
    (tmp_path / "labels_cases.json").write_text(json.dumps(cases))
    run = subprocess.run(
        [NODE, str(tmp_path / "labels_driver.js"), str(SECTION_JS),
         str(tmp_path / "labels_cases.json"), str(tmp_path / "labels_out.json")],
        capture_output=True, text=True, check=False, env={"TZ": "America/Los_Angeles"},
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return {r["id"]: r for r in json.loads((tmp_path / "labels_out.json").read_text())}


def _rows(cid, labels, ids):
    return {"id": cid, "call": "rows", "labels": labels, "ids": ids}


def _weeks(cid, lang, labels, dates):
    return {"id": cid, "call": "weeks", "lang": lang, "labels": labels, "dates": dates}


# --- row labels ---------------------------------------------------------------------

def test_row_labels_that_occur_once_stay_and_repeats_get_the_object_name(tmp_path):
    labels = ["Orion Nebula [Neb]", "M31 [Gal]", "Orion Nebula [Neb]"]
    r = _run(tmp_path, [_rows("x", labels, ["M42", "M31", "NGC1976"])])["x"]
    assert r["result"] == ["Orion Nebula [Neb] (M42)", "M31 [Gal]", "Orion Nebula [Neb] (NGC1976)"]
    assert r["untouched"]


def test_row_labels_never_take_a_label_that_occurs_once(tmp_path):
    """'X (A)' is some other object's own label, so the repeat for A moves on to #2."""
    r = _run(tmp_path, [_rows("x", ["X", "X", "X (A)"], ["A", "B", "C"])])["x"]
    assert r["result"] == ["X (A) #2", "X (B)", "X (A)"]


def test_row_labels_with_the_same_name_twice_are_numbered(tmp_path):
    """The server path can send two rows with one object name (the browser path throws
    on a duplicate name and falls back to it)."""
    r = _run(tmp_path, [_rows("x", ["X", "X", "X"], ["A", "A", "A"])])["x"]
    assert r["result"] == ["X (A)", "X (A) #2", "X (A) #3"]


# --- week labels --------------------------------------------------------------------

def test_week_labels_follow_the_page_language_and_the_date_not_local_time(tmp_path):
    labels, dates = ["Oct 12", "Jan 04"], ["2026-10-12", "2027-01-04"]
    r = _run(tmp_path, [_weeks("en", "en", labels, dates), _weeks("ja", "ja", labels, dates),
                        _weeks("none", "", labels, dates)])
    assert r["en"]["result"] == ["Oct 12", "Jan 4"]
    assert r["ja"]["result"] == ["10月12日", "1月4日"]
    assert r["none"]["result"] == r["en"]["result"]


def test_week_labels_keep_the_chunk_label_without_a_usable_date_or_language(tmp_path):
    labels = ["Oct 12", "Oct 19", "Oct 26"]
    r = _run(tmp_path, [_weeks("dates", "de", labels, [None, "2026-10-19", "2026-10-26"]),
                        _weeks("garbled", "en", labels, ["2026-10-12", "2026-10-1", "x"]),
                        _weeks("no_dates", "en", labels, None),
                        _weeks("bad_lang", "!!", labels, ["2026-10-12", "2026-10-19", "2026-10-26"])])
    assert r["dates"]["result"][0] == "Oct 12" and r["dates"]["result"][1] != "Oct 19"
    assert r["garbled"]["result"] == ["Oct 12", "Oct 19", "Oct 26"]
    assert r["no_dates"]["result"] == labels
    assert r["bad_lang"]["result"] == labels


def test_52_week_labels_are_unique_in_every_shipped_language(tmp_path):
    days = [date(2026, 10, 12) + timedelta(weeks=i) for i in range(52)]
    labels = [d.strftime("%b %d") for d in days]
    dates = [d.strftime("%Y-%m-%d") for d in days]
    r = _run(tmp_path, [_weeks(lang, lang, labels, dates) for lang in LANGUAGES])
    for lang in LANGUAGES:
        assert len(set(r[lang]["result"])) == 52, lang
    assert r["de"]["result"] != labels
