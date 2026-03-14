#!/usr/bin/env python3
"""Fix all placeholder names in all .po files for ALL languages.

DeepL translated placeholder variable names like %(アクション)s, %(数量)d.
This script repairs ALL placeholder names back to English.
"""

import polib
import re
from pathlib import Path

# Pattern to find ALL placeholder names that are not the canonical English names
# Matches any placeholder name that is NOT: action, count, current, total, error, message, name, shown
CANONICAL_PLACEHOLDER_PATTERN = re.compile(r'%\((?!action|count|current|total|error|message|name|shown)([a-zA-Z0-9]+)\)([sd])')

# Original placeholder names (canonical)
ORIGINAL_NAMES = {
    'action': 'action',
    'count': 'count',
    'current': 'current',
    'total': 'total',
    'error': 'error',
    'message': 'message',
    'name': 'name',
    'shown': 'shown',
}

# Language-specific mappings
LANGUAGE_FIXES = {
    # Japanese
    'アクション': 'action', '数量': 'count', '名前': 'name',
    '現在': 'current', '合計': 'total', 'エラー': 'error', '示す': 'shown',
    # Chinese
    '动作': 'action', '数量': 'count', '名称': 'name',
    '当前': 'current', '总计': 'total', '错误': 'error', '显示': 'shown',
    # French
    'action': 'action', 'nombre': 'count', 'nom': 'name',
    'actuel': 'current', 'total': 'total', 'erreur': 'error',
    # German
    'Aktion': 'action', 'Anzahl': 'count', 'Name': 'name',
    'aktuell': 'current', 'gesamt': 'total', 'Fehler': 'error',
    # Spanish
    'acción': 'action', 'cantidad': 'count', 'nombre': 'name',
    'actual': 'current', 'total': 'total', 'error': 'error',
}

def fix_placeholders_in_po(po_path, lang):
    """Fix translated placeholder names back to English."""
    print(f"Processing {lang}...")

    po = polib.pofile(str(po_path))
    fixed_count = 0
    errors = []

    for entry in po:
        if not entry.msgstr:
            continue

        # Check if msgstr has translated placeholder names
        matches = list(CANONICAL_PLACEHOLDER_PATTERN.finditer(entry.msgstr))

        if not matches:
            continue

        # Build replacement map
        replacements = {}
        for match in matches:
            translated_name = match.group(1)
            format_char = match.group(2)

            # Find the original name
            original_name = LANGUAGE_FIXES.get(translated_name, translated_name)

            if original_name != translated_name and original_name in ORIGINAL_NAMES.values():
                old_placeholder = f'%({translated_name}){format_char}'
                new_placeholder = f'%({original_name}){format_char}'
                replacements[old_placeholder] = new_placeholder
                fixed_count += 1

        # Apply replacements
        if replacements:
            for old, new in replacements.items():
                entry.msgstr = entry.msgstr.replace(old, new)

    # Also fix plural forms
    for entry in po:
        if entry.msgstr_plural:
            for key, msgstr in entry.msgstr_plural.items():
                matches = list(CANONICAL_PLACEHOLDER_PATTERN.finditer(msgstr))
                if matches:
                    for match in matches:
                        translated_name = match.group(1)
                        format_char = match.group(2)
                        original_name = LANGUAGE_FIXES.get(translated_name, translated_name)
                        if original_name != translated_name and original_name in ORIGINAL_NAMES.values():
                            old_placeholder = f'%({translated_name}){format_char}'
                            new_placeholder = f'%({original_name}){format_char}'
                            entry.msgstr_plural[key] = msgstr.replace(old, new_placeholder)
                            fixed_count += 1

    if fixed_count > 0:
        po.save()
        print(f"  Fixed {fixed_count} placeholder(s). File saved.")
    else:
        print(f"  No fixes needed for {lang}.")

    return fixed_count, errors


def main():
    """Main function."""
    languages = ['de', 'fr', 'es', 'ja', 'zh']
    total_fixed = 0

    for lang in languages:
        po_path = Path(f'translations/{lang}/LC_MESSAGES/messages.po')
        if not po_path.exists():
            print(f"Warning: File not found: {po_path}")
            continue

        fixed, errors = fix_placeholders_in_po(po_path, lang)
        total_fixed += fixed

    print(f"\n{'='*60}")
    print(f"Total placeholder fixes across all languages: {total_fixed}")

    # Verification
    print("\n" + "="*60)
    print("VERIFICATION - Checking critical entries:")

    critical_entries = [
        "Are you sure you want to %(action)s %(count)d objects?",
        "Found: %(name)s. Details loaded from SIMBAD.",
        "Showing %(count)d objects",
        "Showing %(shown)d of %(total)d objects",
        "Merging Session %(current)d/%(total)d...",
        "Fetching details for %(name)s...",
        "Failed to update Active Project: %(error)s",
        "Error loading opportunities: %(error)s",
        "Failed to load imaging opportunities. Check console for details. (%(error)s)",
    ]

    for lang in languages:
        po_path = Path(f'translations/{lang}/LC_MESSAGES/messages.po')
        po = polib.pofile(str(po_path))

        for msgid in critical_entries:
            entry = po.find(msgid)
            if entry and entry.msgstr:
                print(f"\n[{lang}] {msgid}")
                print(f"  msgstr: {entry.msgstr}")

                # Check if placeholders are intact
                has_action = '%(action)s' in entry.msgstr
                has_count = '%(count)d' in entry.msgstr
                has_name = '%(name)s' in entry.msgstr
                has_total = '%(total)d' in entry.msgstr
                has_shown = '%(shown)d' in entry.msgstr
                has_current = '%(current)d' in entry.msgstr
                has_error = '%(error)s' in entry.msgstr

                print(f"  Placeholders: action={has_action}, count={has_count}, name={has_name}, total={has_total}, shown={has_shown}, current={has_current}, error={has_error}")

    print(f"\n{'='*60}")
    print("Run: pybabel compile -d translations")


if __name__ == '__main__':
    main()
