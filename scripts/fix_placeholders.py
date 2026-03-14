#!/usr/bin/env python3
"""Fix broken placeholders in .po translation files.

A placeholder is broken if:
- Variable name inside %(name)s differs between msgid and msgstr
- Variable name inside {name} differs between msgid and msgstr
- Format specifier is missing or changed
"""

import re
import sys
from pathlib import Path

try:
    import polib
except ImportError:
    print("polib is required. Install with: pip install polib")
    sys.exit(1)

# Regex patterns for placeholders
PERCENT_PATTERN = re.compile(r'%\((\w+)\)([a-zA-Z])')  # %(name)s, %(count)d, etc.
BRACE_PATTERN = re.compile(r'\{(\w+)\}')  # {name}, {count}, etc.


def extract_percent_placeholders(text):
    """Extract %(name)s style placeholders with their format specifiers."""
    if not text:
        return {}
    return {m.group(1): m.group(2) for m in PERCENT_PATTERN.finditer(text)}


def extract_brace_placeholders(text):
    """Extract {name} style placeholders."""
    if not text:
        return set()
    return set(m.group(1) for m in BRACE_PATTERN.finditer(text))


def fix_percent_placeholders(msgid, msgstr, placeholders):
    """Fix %(name)s placeholders in msgstr to match msgid."""
    if not msgstr:
        return msgstr, False

    fixed = False
    result = msgstr

    # Get placeholders currently in msgstr
    msgstr_placeholders = extract_percent_placeholders(msgstr)

    # For each placeholder that should be there
    for name, fmt in placeholders.items():
        correct = f'%({name}){fmt}'

        # Check if it's missing or has wrong format
        if name not in msgstr_placeholders:
            # Try to find similar placeholder with wrong name
            for wrong_name, wrong_fmt in msgstr_placeholders.items():
                if wrong_name not in placeholders:
                    # This might be a typo - replace it
                    wrong = f'%({wrong_name}){wrong_fmt}'
                    result = result.replace(wrong, correct)
                    fixed = True
                    break
        elif msgstr_placeholders[name] != fmt:
            # Correct name but wrong format specifier
            wrong = f'%({name}){msgstr_placeholders[name]}'
            result = result.replace(wrong, correct)
            fixed = True

    return result, fixed


def fix_brace_placeholders(msgid, msgstr, placeholders):
    """Fix {name} placeholders in msgstr to match msgid."""
    if not msgstr:
        return msgstr, False

    fixed = False
    result = msgstr

    # Get placeholders currently in msgstr
    msgstr_placeholders = extract_brace_placeholders(msgstr)

    # For each placeholder that should be there
    for name in placeholders:
        if name not in msgstr_placeholders:
            # Try to find similar placeholder with wrong name
            for wrong_name in msgstr_placeholders:
                if wrong_name not in placeholders:
                    # Replace wrong with correct
                    wrong = '{' + wrong_name + '}'
                    correct = '{' + name + '}'
                    result = result.replace(wrong, correct)
                    fixed = True
                    break

    return result, fixed


def fix_entry(entry):
    """Fix placeholders in a single entry. Returns (fixed, needs_manual_review)."""
    msgid = entry.msgid
    msgstr = entry.msgstr

    if not msgid or not msgstr:
        return False, False

    # Extract expected placeholders from msgid
    expected_percent = extract_percent_placeholders(msgid)
    expected_brace = extract_brace_placeholders(msgid)

    # Get actual placeholders in msgstr
    actual_percent = extract_percent_placeholders(msgstr)
    actual_brace = extract_brace_placeholders(msgstr)

    # Check if there's a mismatch
    has_percent_issue = (
        set(expected_percent.keys()) != set(actual_percent.keys()) or
        any(expected_percent.get(k) != actual_percent.get(k) for k in expected_percent)
    )
    has_brace_issue = expected_brace != actual_brace

    if not has_percent_issue and not has_brace_issue:
        return False, False

    # Try to fix
    original_msgstr = msgstr

    # Fix percent placeholders
    msgstr, percent_fixed = fix_percent_placeholders(msgid, msgstr, expected_percent)

    # Fix brace placeholders
    msgstr, brace_fixed = fix_brace_placeholders(msgid, msgstr, expected_brace)

    if percent_fixed or brace_fixed:
        entry.msgstr = msgstr
        return True, False

    # Couldn't auto-fix
    return False, True


def main(po_file_path):
    """Main function to fix placeholders in a .po file."""
    po_path = Path(po_file_path)

    if not po_path.exists():
        print(f"Error: File not found: {po_path}")
        sys.exit(1)

    print(f"Processing: {po_path}")

    po = polib.pofile(str(po_path))

    fixed_count = 0
    needs_review = []

    for entry in po:
        if entry.msgid_plural:
            # Handle plural forms
            for key, msgstr in entry.msgstr_plural.items():
                temp_entry = type('obj', (object,), {'msgid': entry.msgid, 'msgstr': msgstr})()
                fixed, needs_manual = fix_entry(temp_entry)
                if fixed:
                    entry.msgstr_plural[key] = temp_entry.msgstr
                    fixed_count += 1
                if needs_manual:
                    needs_review.append({
                        'linenum': entry.linenum,
                        'msgid': entry.msgid,
                        'msgstr': msgstr,
                        'plural_key': key
                    })
        else:
            fixed, needs_manual = fix_entry(entry)
            if fixed:
                fixed_count += 1
            if needs_manual:
                needs_review.append({
                    'linenum': entry.linenum,
                    'msgid': entry.msgid,
                    'msgstr': entry.msgstr
                })

    # Save if any fixes were made
    if fixed_count > 0:
        po.save()
        print(f"\nFixed {fixed_count} placeholder(s). File saved.")
    else:
        print("\nNo placeholder fixes needed.")

    # Report entries needing manual review
    if needs_review:
        print(f"\n{len(needs_review)} entr(y/ies) require manual review:")
        print("-" * 60)
        for item in needs_review:
            plural_info = f" (plural form: {item['plural_key']})" if 'plural_key' in item else ""
            print(f"\nLine {item['linenum']}{plural_info}:")
            print(f"  msgid:  {item['msgid'][:80]}{'...' if len(item['msgid']) > 80 else ''}")
            print(f"  msgstr: {item['msgstr'][:80]}{'...' if len(item['msgstr']) > 80 else ''}")
    else:
        print("\nAll placeholders verified.")

    return fixed_count, len(needs_review)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python fix_placeholders.py <path/to/messages.po>")
        sys.exit(1)

    main(sys.argv[1])
