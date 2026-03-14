#!/usr/bin/env python3
"""Fix specific placeholder issues from DeepL translations.

This script fixes:
1. Translated variable names (e.g., %(Name)s -> %(name)s, %(aktuell)d -> %(current)d)
2. Extra placeholders at the end of strings
3. Reversed placeholder order
"""

import polib
import re
from pathlib import Path

# Mapping of translated placeholder names back to original
# DeepL sometimes translates variable names in placeholders
PLACEHOLDER_FIXES = {
    # German
    'Name': 'name',
    'aktuell': 'current',
    'gesamt': 'total',
    'Anzahl': 'count',
    'Fehler': 'error',
    'Nachricht': 'message',

    # French
    'nom': 'name',
    'actuel': 'current',
    'total': 'total',  # Same
    'compte': 'count',
    'erreur': 'error',
    'message': 'message',  # Same

    # Spanish
    'nombre': 'name',
    'actual': 'current',
    'total': 'total',  # Same
    'cuenta': 'count',
    'recuento': 'count',
    'acción': 'action',
    'error': 'error',  # Same
    'mensaje': 'message',

    # Japanese
    '名称': 'name',
    '現在': 'current',
    '合計': 'total',
    'カウント': 'count',
    'アクション': 'action',
    'エラー': 'error',
    'メッセージ': 'message',

    # Chinese
    '名称': 'name',
    '当前': 'current',
    '总': 'total',
    '总计': 'total',
    '计数': 'count',
    '操作': 'action',
    '错误': 'error',
    '消息': 'message',
}


def fix_placeholders_in_text(msgid, msgstr):
    """Fix placeholders in msgstr based on msgid."""
    if not msgid or not msgstr:
        return msgstr

    result = msgstr

    # Extract placeholders from msgid (the source of truth)
    msgid_placeholders = {}
    for match in re.finditer(r'%\((\w+)\)([a-zA-Z])', msgid):
        msgid_placeholders[match.group(1)] = match.group(2)

    # Find all placeholders in msgstr
    msgstr_matches = list(re.finditer(r'%\((\w+)\)([a-zA-Z])', msgstr))

    # For each placeholder in msgstr, check if it matches msgid
    for match in msgstr_matches:
        name = match.group(1)
        fmt = match.group(2)

        # If the placeholder name was translated, fix it
        if name in PLACEHOLDER_FIXES:
            original_name = PLACEHOLDER_FIXES[name]
            if original_name in msgid_placeholders:
                wrong = f'%({name}){fmt}'
                correct = f'%({original_name}){msgid_placeholders[original_name]}'
                result = result.replace(wrong, correct)

    # Now check for extra placeholders at the end
    # First, get the expected placeholders after all fixes
    msgstr_matches = list(re.finditer(r'%\((\w+)\)([a-zA-Z])', result))
    msgstr_placeholders = set(m.group(1) for m in msgstr_matches)

    # Count expected occurrences
    expected_counts = {}
    for name in msgid_placeholders:
        expected_counts[name] = msgid.count(f'%({name})')

    # Count actual occurrences
    actual_counts = {}
    for name in msgstr_placeholders:
        actual_counts[name] = result.count(f'%({name})')

    # Remove duplicates
    for name, count in actual_counts.items():
        if name in expected_counts and count > expected_counts[name]:
            # Find and remove the extra occurrences
            pattern = re.compile(re.escape(f'%({name})[a-zA-Z]'))
            matches = list(pattern.finditer(result))
            # Remove the extra ones from the end
            extras = count - expected_counts[name]
            if extras > 0:
                # Remove the last 'extras' occurrences
                removed = 0
                new_result = list(result)
                i = len(new_result) - 1
                while i >= 0 and removed < extras:
                    # Check if we're at the start of a placeholder
                    if i >= 4 and new_result[i-4:i] == ['%', '(', name[0], ')', msgid_placeholders.get(name, 's')]:
                        # This might be our placeholder, but need to check the full name
                        placeholder_start = i - len(f'%({name}){msgid_placeholders.get(name, "s")}') + 1
                        placeholder = ''.join(new_result[placeholder_start:i+1])
                        if placeholder == f'%({name}){msgid_placeholders.get(name, "s")}':
                            # Remove this placeholder
                            del new_result[placeholder_start:i+1]
                            removed += 1
                            i -= len(placeholder)
                        i -= 1
                    else:
                        i -= 1
                result = ''.join(new_result)

    return result


def process_po_file(po_path, lang):
    """Process a single .po file."""
    print(f"Processing {lang}...")

    po = polib.pofile(str(po_path))
    fixed_count = 0

    for entry in po:
        if not entry.msgid or not entry.msgstr:
            continue

        original_msgstr = entry.msgstr
        fixed_msgstr = fix_placeholders_in_text(entry.msgid, original_msgstr)

        if fixed_msgstr != original_msgstr:
            entry.msgstr = fixed_msgstr
            fixed_count += 1
            print(f"  Line {entry.linenum}: {entry.msgid[:50]}...")
            print(f"    Was: {original_msgstr[:70]}...")
            print(f"    Now: {fixed_msgstr[:70]}...")

    if fixed_count > 0:
        po.save()
        print(f"  Saved {fixed_count} fixes.")
    else:
        print(f"  No fixes needed.")

    return fixed_count


def main():
    languages = ['de', 'fr', 'es', 'ja', 'zh']
    total_fixed = 0

    for lang in languages:
        po_path = Path(f'translations/{lang}/LC_MESSAGES/messages.po')
        if po_path.exists():
            fixed = process_po_file(po_path, lang)
            total_fixed += fixed
        else:
            print(f"Warning: {po_path} not found")

    print(f"\nTotal fixes: {total_fixed}")


if __name__ == '__main__':
    main()
