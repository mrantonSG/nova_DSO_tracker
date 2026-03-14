#!/usr/bin/env python3
"""Fix Japanese placeholder names that were translated by DeepL.

DeepL sometimes translates placeholder variable names:
- %(action)s → %(アクション)s
- %(count)d → %(カウント)d

This script fixes those back to original English variable names.
"""

import polib
import re
from pathlib import Path

# Pattern to find translated placeholder names
# Matches %(アクション)s, %(カウント)d, etc.
TRANSLATED_PLACEHOLDER_PATTERN = re.compile(r'%\(([^)]+)\)([sd])')

# Original placeholder names
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

def fix_japanese_placeholders(po_file_path):
    """Fix translated placeholder names back to English."""
    print(f"Processing: {po_file_path}")

    po = polib.pofile(str(po_file_path))
    fixed_count = 0

    for entry in po:
        if not entry.msgstr:
            continue

        # Check if msgstr has translated placeholder names
        matches = list(TRANSLATED_PLACEHOLDER_PATTERN.finditer(entry.msgstr))

        if not matches:
            continue

        # Build replacement map
        replacements = {}
        for match in matches:
            # Extract the translated name (e.g., "アクション")
            translated_name = match.group(1)
            format_char = match.group(2)

            # Check if this is a known translation
            if translated_name in ORIGINAL_NAMES.values():
                # Find the original name
                original_name = None
                for orig, trans in ORIGINAL_NAMES.items():
                    if trans == translated_name:
                        original_name = orig
                        break

                if original_name:
                    old_placeholder = f'%({translated_name}){format_char}'
                    new_placeholder = f'%({original_name}){format_char}'
                    replacements[old_placeholder] = new_placeholder
                    print(f"  Line {entry.linenum}: {old_placeholder} → {new_placeholder}")

        # Apply replacements
        if replacements:
            for old, new in replacements.items():
                entry.msgstr = entry.msgstr.replace(old, new)
                fixed_count += 1

    if fixed_count > 0:
        po.save()
        print(f"\nFixed {fixed_count} placeholder(s). File saved.")
    else:
        print("\nNo placeholder fixes needed.")

    return fixed_count


def main():
    """Main function."""
    po_path = Path('translations/ja/LC_MESSAGES/messages.po')

    if not po_path.exists():
        print(f"Error: File not found: {po_path}")
        return

    fix_japanese_placeholders(po_path)

    print(f"\n{'='*60}")
    print("Verification: Checking 'Are you sure you want to %(action)s %(count)d objects?' entry")

    po = polib.pofile(str(po_path))
    for entry in po:
        if 'Are you sure you want to %(action)s %(count)d objects?' in entry.msgid:
            print(f"  msgid: {entry.msgid}")
            print(f"  msgstr: {entry.msgstr}")
            # Check placeholders
            has_action = '%(action)s' in entry.msgstr
            has_count = '%(count)d' in entry.msgstr
            print(f"  Has %(action)s: {has_action}, Has %(count)d: {has_count}")
            break


if __name__ == '__main__':
    main()
