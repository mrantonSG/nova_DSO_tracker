"""The session-row fields the page reads, checked against the server contracts.

nova/journal_rows.py claims JOURNAL_SESSION_FIELDS is "exactly the keys
static/js/dashboard.js reads" and GRAPH_SESSION_JS_FIELDS is "exactly the keys
static/js/journal_section.js reads". The payload tests compare a payload to a
literal copy of each tuple, which catches a field the builder stops emitting but
not one deleted from the tuple itself - the builder and the expectation move
together. These tests read the JavaScript: every property the page reads off a
session row must be in the tuple, so dropping one (and blanking it in the edit
form on the next save) fails here.

The extraction is textual and brace-sliced by function name, so a renamed
function fails loudly; test_extractor_finds_its_anchors pins the keys that prove
each slice really found what it claims.
"""
import re
from pathlib import Path

from nova.journal_rows import GRAPH_SESSION_JS_FIELDS, JOURNAL_SESSION_FIELDS

REPO = Path(__file__).resolve().parent.parent
JOURNAL_SECTION_JS = REPO / "static" / "js" / "journal_section.js"
DASHBOARD_JS = REPO / "static" / "js" / "dashboard.js"

_MONO_FILTERS = ("L", "R", "G", "B", "Ha", "OIII", "SII")

# The functions that read window.selectedSessionData - directly, or because it is
# passed in as their `data` argument (renderEditFormTitle is called with it from
# setupEditMode and initializeFormState).
_GRAPH_SLICES = ("populateEditForm", "setupEditMode", "initializeFormState",
                 "updateGuidingRmsHint", "renderEditFormTitle")

# renderGalleryGrid holds composeRigString; populateJournalTable holds
# getRigDisplayString. The other journal readers (_createJournalRow,
# _applyJournalFilters, _sortJournalSessions) are covered by the file-wide
# `session.<key>` scan and by journalColumnConfig's dataKeys.
_DASHBOARD_SLICES = ("renderGalleryGrid", "populateJournalTable")
_GALLERY_SLICE = "renderGalleryGrid"      # the gallery calls its rows `s`


def _brace_slice(source, start):
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("unbalanced braces from offset %d" % start)


def _slice_function(source, name):
    match = re.search(r"^[ \t]*(?:async )?function %s\(" % re.escape(name), source, re.M)
    assert match, "function %s() not found - renamed?" % name
    return _brace_slice(source, source.index("{", match.end() - 1))


def _slice_object(source, name):
    match = re.search(r"^[ \t]*(?:const|let|var) %s\s*=\s*\{" % re.escape(name), source, re.M)
    assert match, "%s not found - renamed?" % name
    return _brace_slice(source, source.index("{", match.end() - 1))


def _graph_js_reads():
    source = JOURNAL_SECTION_JS.read_text(encoding="utf-8")
    reads = set()
    for name in _GRAPH_SLICES:
        body = _slice_function(source, name)
        reads |= set(re.findall(r"\bdata\.([A-Za-z_]\w*)", body))
        reads |= set(re.findall(r"selectedSessionData[?]?\.([A-Za-z_]\w*)", body))
        # data[`filter_${filt}_subs`] / data[`filter_${filt}_exposure_sec`]
        for prefix, suffix in re.findall(r"data\[`([a-z_]+)\$\{\w+\}_([a-z_]+)`\]", body):
            reads |= {prefix + filt + "_" + suffix for filt in _MONO_FILTERS}
    reads.discard("hasOwnProperty")
    return reads


def _dashboard_session_reads():
    source = DASHBOARD_JS.read_text(encoding="utf-8")
    reads = set(re.findall(r"\bsession\.([A-Za-z_]\w*)", source))
    for name in _DASHBOARD_SLICES:
        reads |= set(re.findall(r"\bsession\.([A-Za-z_]\w*)",
                                _slice_function(source, name)))
    # The gallery aliases its rows to `s`.
    reads |= set(re.findall(r"\bs\.([A-Za-z_]\w*)",
                            _slice_function(source, _GALLERY_SLICE)))
    # session[config.dataKey] in _createJournalRow / _applyJournalFilters /
    # _sortJournalSessions.
    config = _slice_object(source, "journalColumnConfig")
    reads |= set(re.findall(r"dataKey: '([^']+)'", config))
    return reads


def test_graph_js_reads_are_in_the_graph_contract():
    missing = _graph_js_reads() - set(GRAPH_SESSION_JS_FIELDS)
    assert not missing, (
        "static/js/journal_section.js reads these off window.selectedSessionData, "
        "but nova/journal_rows.py:GRAPH_SESSION_JS_FIELDS does not carry them - "
        "the edit form would blank them and the next save would wipe them: %s"
        % sorted(missing)
    )


def test_dashboard_js_reads_are_in_the_journal_contract():
    missing = _dashboard_session_reads() - set(JOURNAL_SESSION_FIELDS)
    assert not missing, (
        "static/js/dashboard.js reads these off a journal session, but "
        "nova/journal_rows.py:JOURNAL_SESSION_FIELDS does not carry them: %s"
        % sorted(missing)
    )


def test_extractor_finds_its_anchors():
    """A stale marker would shrink an extraction and let both tests pass
    vacuously."""
    assert {"gain_setting", "notes", "project_ids",
            "filter_Ha_exposure_sec"} <= _graph_js_reads()
    assert {"image_url", "other_projects", "calculated_integration_time_minutes",
            "telescope_name_snapshot"} <= _dashboard_session_reads()
