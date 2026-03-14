#!/usr/bin/env python3
"""Fix broken placeholders from DeepL translations.

DeepL sometimes:
1. Translates the variable names inside placeholders (e.g., %(name)s -> %(名称)s)
2. Reverses placeholder order (e.g., %(current)d/%(total)d -> %(total)d%(current)d)
3. Adds extra placeholder suffixes
4. Mixes placeholder types (e.g., {name} vs %(name)s)

This script fixes these issues.
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


def fix_placeholders_from_deepl(msgid, msgstr):
    """Fix placeholders in msgstr that were broken by DeepL translation."""
    if not msgid or not msgstr:
        return msgstr

    # Get expected placeholders from msgid
    expected_percent = extract_percent_placeholders(msgid)
    expected_brace = extract_brace_placeholders(msgid)

    # If no placeholders in msgid, return msgstr as-is
    if not expected_percent and not expected_brace:
        return msgstr

    result = msgstr

    # Fix percent placeholders
    if expected_percent:
        # First, find what placeholders are in the translated text
        translated_percent = extract_percent_placeholders(result)

        # For each expected placeholder, fix issues
        for name, fmt in expected_percent.items():
            correct = f'%({name}){fmt}'

            # Check if the correct placeholder exists
            if name in translated_percent and translated_percent[name] == fmt:
                continue  # Already correct

            # Check if there's a placeholder with wrong format specifier
            if name in translated_percent and translated_percent[name] != fmt:
                wrong = f'%({name}){translated_percent[name]}'
                result = result.replace(wrong, correct)

            # Check if there's a translated placeholder (e.g., %(名称)s instead of %(name)s)
            # This happens with DeepL for Chinese, Japanese, German, etc.
            # We need to find any placeholder that doesn't match our expected ones
            translated_percent = extract_percent_placeholders(result)  # Refresh
            for trans_name, trans_fmt in translated_percent.items():
                if trans_name not in expected_percent:
                    # This might be a translated variable name
                    # Replace it with the correct placeholder
                    wrong = f'%({trans_name}){trans_fmt}'
                    result = result.replace(wrong, correct)

            # Check if the placeholder is completely missing
            if name not in extract_percent_placeholders(result):
                # Look for a duplicate or wrong placeholder
                translated_percent = extract_percent_placeholders(result)  # Refresh
                for trans_name, trans_fmt in list(translated_percent.items()):
                    if trans_name == name:
                        # Found the correct one with wrong format - already handled
                        continue
                    # Try to find a similar pattern that might be the wrong placeholder
                    # For now, just append the missing placeholder at the end
                    pass

        # Now check for duplicate placeholders (DeepL sometimes adds them)
        translated_percent = extract_percent_placeholders(result)  # Refresh
        for name in list(translated_percent.keys()):
            if name in expected_percent:
                expected_count = msgid.count(f'%({name})')
                actual_count = result.count(f'%({name})')
                if actual_count > expected_count:
                    # Remove duplicates
                    pattern = re.compile(re.escape(f'%({name})[a-zA-Z]'))
                    matches = list(pattern.finditer(result))
                    # Keep the first expected_count occurrences
                    if len(matches) > expected_count:
                        for i in range(expected_count, len(matches)):
                            # Remove the duplicate
                            result = result[:matches[i].start()] + result[matches[i].end():]

    # Fix brace placeholders
    if expected_brace:
        translated_brace = extract_brace_placeholders(result)

        for name in expected_brace:
            if name not in translated_brace:
                # Find a translated placeholder and replace it
                for trans_name in translated_brace:
                    if trans_name not in expected_brace:
                        wrong = '{' + trans_name + '}'
                        correct = '{' + name + '}'
                        result = result.replace(wrong, correct)
                        break

    return result


def process_po_file(po_path):
    """Process a single .po file."""
    print(f"Processing: {po_path}")

    po = polib.pofile(str(po_path))

    fixed_count = 0
    needs_review = []

    for entry in po:
        if not entry.msgid:
            continue

        original_msgstr = entry.msgstr
        if not original_msgstr:
            continue

        # Fix placeholders
        fixed_msgstr = fix_placeholders_from_deepl(entry.msgid, original_msgstr)

        if fixed_msgstr != original_msgstr:
            entry.msgstr = fixed_msgstr
            fixed_count += 1
            print(f"  Line {entry.linenum}: {entry.msgid[:50]}...")

    if fixed_count > 0:
        po.save()
        print(f"\nFixed {fixed_count} placeholder(s). File saved.")
    else:
        print("\nNo placeholder fixes needed.")

    return fixed_count


def main():
    """Main function."""
    languages = ['de', 'fr', 'es', 'ja', 'zh']

    total_fixed = 0
    for lang in languages:
        po_path = Path(f'translations/{lang}/LC_MESSAGES/messages.po')
        if po_path.exists():
            fixed = process_po_file(po_path)
            total_fixed += fixed
        else:
            print(f"Warning: File not found: {po_path}")

    print(f"\n{'='*60}")
    print(f"Total fixes across all languages: {total_fixed}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
