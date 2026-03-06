#!/usr/bin/env python3
"""
Nova DSO Tracker — Translation Validation Script
Run before every pybabel compile to catch translation gaps.
Exit code 0 = clean. Exit code 1 = problems found (blocks compile).

Usage:
    python3 scripts/check_translations.py
    python3 scripts/check_translations.py --lang de
    python3 scripts/check_translations.py --warn-only
"""

import argparse
import sys
from pathlib import Path

try:
    import polib
except ImportError:
    print("ERROR: polib not installed. Run: pip install polib --break-system-packages")
    sys.exit(1)

# ── Configuration ──────────────────────────────────────────────────────────────

TRANSLATIONS_DIR = Path(__file__).parent.parent / "translations"

# Languages that must be fully translated (not source language)
TARGET_LANGUAGES = ["de", "fr", "es", "ja", "zh"]

# English is the source — empty msgstr is correct and expected
SOURCE_LANGUAGE = "en"

# Terms that are legitimately identical in all languages (proper nouns, units, etc.)
ALLOWED_IDENTICAL = {
    # existing entries...
    # Add these:
    'Name', 'Name:', 'Status', 'Status:', 'Version', 'Admin',
    'Dashboard', 'Simulation', 'Position', 'Heatmap', 'Trend',
    'Integration', 'Integration:', 'Filter', 'Filter:',
    'Inspiration', 'Rotation (PA)', 'Gamma', 'Name (A→Z)', 'Name (Z→A)',
    'Reducer/Extender', 'SQM:', 'Darks:', 'Binning:', 'RA:',
    'Constellation', 'Multiple', 'Type', 'Source', 'Description',
    'Action', 'Actions', 'Date (UTC)', 'Configuration', 'Journal',
    'Img', 'Saturation', 'Altitude', 'Observable', 'minutes',
    'observable (°)', 'Magnitude', 'SB', 'Altitude °', 'Angle',
    'Points', 'Distance', 'Options', '%(prog)s, version %(version)s',
    'Nova – Configuration', 'Nova Pocket', 'Dithering', 'Darks', 'Flats',
    'Mode',  # Same word in English and French
    # Abbreviations and single characters (intentionally identical)
    'Integ.', 'Total:', 'min', 'x', 's', 'FL:', 'subs x ', 'Flats:',
    'Bias/DarkFlats:', 'Dithers', '#', 'RA', 'Dec', 'RA RMS:', 'Dec RMS:',
    'Dec:', 'Zoom', '# Subs', 'Bias / Dark Flats:', 'Bias / Dark Flats', 'Con', 'Mag',
    'SQM', 'ID', 'Lat:', 'Lon:', 'Factor:', 'Rig', 'Rig:', 'Temp', 'Exp',
    'Subs', '# Subs:', 'Error:', 'Config:',
    # Product names (never translate)
    'Nova Analytics', 'Nova DSO Tracker', 'Nova Companion', 'Nova',
    'DSO Tracker', 'SIMBAD', 'Rigs', 'General',
}

# Strings that look like format strings or code — skip identical check
SKIP_PATTERNS = [
    lambda s: s.startswith("%(prog)s"),
    lambda s: s.startswith("%s"),
    lambda s: s.startswith("http"),
    lambda s: len(s.strip()) == 0,
    lambda s: s.strip() in ("", "-", "—", "→", "←", "↑", "↓"),
    lambda s: all(c in "0123456789.,+-→←↑↓°%()[]{}|/ " for c in s),
]

# Paths that indicate library strings (Click, WTForms, Flask-WTF)
LIBRARY_PATH_PATTERNS = [
    "site-packages",
    "venv/lib/python",
    "/usr/lib/python",
    "/usr/local/lib/python",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def is_skip_pattern(s: str) -> bool:
    return any(fn(s) for fn in SKIP_PATTERNS)


def is_library_string(entry) -> bool:
    """
    Check if a PO entry is from a library (Click, WTForms, Flask-WTF, etc.).
    Library strings have source locations in site-packages or venv directories.
    """
    if not entry.occurrences:
        return False
    for filename, _ in entry.occurrences:
        for pattern in LIBRARY_PATH_PATTERNS:
            if pattern in filename:
                return True
    return False


def check_language(lang: str, warn_only: bool) -> list[str]:
    po_path = TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.po"

    if not po_path.exists():
        return [f"[{lang}] ✗ File not found: {po_path}"]

    po = polib.pofile(str(po_path))
    errors = []

    # ── Check 1: Empty msgstr ──────────────────────────────────────────────────
    empty = [e for e in po.untranslated_entries() if not is_library_string(e)]
    if empty:
        errors.append(f"\n[{lang}] ✗ {len(empty)} EMPTY translations:")
        for e in empty:
            errors.append(f"     line {e.linenum:4d} | {repr(e.msgid[:70])}")

    # ── Check 2: msgstr == msgid (not translated, just copied) ────────────────
    identical = [
        e for e in po.translated_entries()
        if e.msgstr == e.msgid
        and e.msgid not in ALLOWED_IDENTICAL
        and not is_skip_pattern(e.msgid)
        and not is_library_string(e)
    ]
    if identical:
        errors.append(f"\n[{lang}] ✗ {len(identical)} IDENTICAL msgstr (not translated):")
        for e in identical:
            errors.append(f"     line {e.linenum:4d} | {repr(e.msgid[:70])}")

    # ── Check 3: Fuzzy entries (need review) ──────────────────────────────────
    fuzzy = [e for e in po.fuzzy_entries() if not is_library_string(e)]
    if fuzzy:
        label = "WARN" if warn_only else "✗"
        errors.append(f"\n[{lang}] {label} {len(fuzzy)} FUZZY entries (need review):")
        for e in fuzzy:
            errors.append(f"     line {e.linenum:4d} | {repr(e.msgid[:70])}")

    if not errors:
        total = len(po.translated_entries())
        print(f"[{lang}] ✓ {total} strings — all translated")

    return errors


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Validate Nova translation files")
    parser.add_argument("--lang", help="Check a single language only (e.g. --lang de)")
    parser.add_argument("--warn-only", action="store_true",
                        help="Exit 0 even if problems found (CI soft mode)")
    args = parser.parse_args()

    langs = [args.lang] if args.lang else TARGET_LANGUAGES

    print(f"Nova Translation Check — {len(langs)} language(s)\n" + "─" * 50)

    all_errors = []
    for lang in langs:
        errors = check_language(lang, args.warn_only)
        all_errors.extend(errors)

    if all_errors:
        print("\n" + "─" * 50)
        print("PROBLEMS FOUND — fix before compiling:\n")
        for e in all_errors:
            print(e)
        print("\n" + "─" * 50)
        print("Fix these, then run: pybabel compile -d translations")
        if not args.warn_only:
            sys.exit(1)
    else:
        print("\n" + "─" * 50)
        print("✓ All translations clean. Safe to compile.")
        print("  Run: pybabel compile -d translations")


if __name__ == "__main__":
    main()