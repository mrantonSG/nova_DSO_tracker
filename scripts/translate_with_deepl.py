#!/usr/bin/env python3
"""Translate empty msgstr entries in .po files using DeepL API.

Usage:
    python3 scripts/translate_with_deepl.py

This script will:
1. Load each translation file (de, fr, es, ja, zh)
2. Find all entries with empty msgstr
3. Translate them using DeepL API
4. Preserve placeholders exactly: %(name)s, %(count)d, %(current)d, %(total)d, %(error)s, %(action)s
"""

import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

try:
    import polib
except ImportError:
    print("polib is required. Install with: pip install polib")
    sys.exit(1)

try:
    import deepl
except ImportError:
    print("deepl is required. Install with: pip install deepl")
    sys.exit(1)


# Load environment variables
load_dotenv('instance/.env')

# Language mapping for DeepL
LANG_CODE_MAP = {
    'de': 'DE',  # German
    'fr': 'FR',  # French
    'es': 'ES',  # Spanish
    'ja': 'JA',  # Japanese
    'zh': 'ZH-HANS',  # Chinese (Simplified)
}

# Regex patterns for placeholders
PERCENT_PATTERN = re.compile(r'%\((\w+)\)([a-zA-Z])')  # %(name)s, %(count)d, etc.


def extract_placeholders(text):
    """Extract %(name)s style placeholders from text."""
    if not text:
        return set()
    return set(m.group(0) for m in PERCENT_PATTERN.finditer(text))


def preserve_placeholders(original_text, translated_text):
    """Ensure all placeholders from original_text are present in translated_text.

    This preserves placeholders exactly as they appear in the original.
    """
    original_placeholders = extract_placeholders(original_text)
    translated_placeholders = extract_placeholders(translated_text)

    result = translated_text

    # For each placeholder in the original
    for placeholder in original_placeholders:
        if placeholder not in translated_placeholders:
            # The placeholder is missing - add it to the translated text
            # We need to find where to insert it. For simplicity, we append it.
            # A better approach would be to find a suitable location, but DeepL
            # usually preserves placeholders reasonably well.
            result = result + placeholder

    return result


def translate_text(text, target_lang, translator):
    """Translate text using DeepL API."""
    try:
        result = translator.translate_text(
            text,
            target_lang=target_lang,
            source_lang='EN'
        )
        return result.text
    except deepl.DeepLException as e:
        print(f"  Error translating '{text[:50]}...': {e}")
        return None


# Terms that should NOT be translated (keep identical across all languages)
SKIP_TRANSLATION = {
    'DSO',
    'NASA',
    'Wikipedia',
    'N/A',
    'Aladin Sky Atlas / DSS2',
}


def process_po_file(po_path, lang, translator, dry_run=False):
    """Process a single .po file, translating empty entries."""
    print(f"\nProcessing {po_path}...")

    target_lang = LANG_CODE_MAP.get(lang)
    if not target_lang:
        print(f"  Error: Unknown language code '{lang}'")
        return 0

    po = polib.pofile(str(po_path))

    translated_count = 0
    failed_count = 0
    skipped_count = 0

    for entry in po:
        # Skip if msgid is empty (header)
        if not entry.msgid:
            continue

        # Skip library strings
        if entry.occurrences:
            is_library = any(
                'site-packages' in occ[0] or 'venv/lib/python' in occ[0] or
                '/usr/lib/python' in occ[0] or '/usr/local/lib/python' in occ[0]
                for occ in entry.occurrences
            )
            if is_library:
                continue

        # Skip terms that should not be translated (keep identical)
        if entry.msgid in SKIP_TRANSLATION:
            if entry.msgstr == "":
                if not dry_run:
                    entry.msgstr = entry.msgid
                skipped_count += 1
                print(f"  Skipped (no translation needed): '{entry.msgid}'")
            continue

        # Check if msgstr is empty
        if entry.msgstr == "":
            # Translate the msgid
            translation = translate_text(entry.msgid, target_lang, translator)

            if translation:
                # Preserve placeholders exactly
                translation = preserve_placeholders(entry.msgid, translation)

                if not dry_run:
                    entry.msgstr = translation

                translated_count += 1
                print(f"  Translated: '{entry.msgid[:50]}...' -> '{translation[:50]}...'")
            else:
                failed_count += 1
        else:
            skipped_count += 1

    if not dry_run and translated_count > 0:
        po.save()
        print(f"  Saved {translated_count} translations to {po_path}")

    print(f"  Summary: {translated_count} translated, {failed_count} failed, {skipped_count} skipped")

    return translated_count


def main():
    # Get DeepL API key from environment
    api_key = os.getenv('DEEPL_API_KEY')
    if not api_key:
        print("Error: DEEPL_API_KEY not found in environment variables.")
        print("Please add it to your instance/.env file: DEEPL_API_KEY=your_api_key")
        sys.exit(1)

    # Initialize DeepL translator
    translator = deepl.Translator(api_key)

    # Check if it's a free or pro account
    usage = translator.get_usage()
    if usage.character.limit == 500000:
        print("Using DeepL Free API (500,000 character limit)")
    else:
        print(f"Using DeepL Pro API (character limit: {usage.character.limit:,})")
    print(f"Current usage: {usage.character.count:,} / {usage.character.limit:,} characters")

    # Process each language
    total_translated = 0
    for lang in ['de', 'fr', 'es', 'ja', 'zh']:
        po_path = Path(f'translations/{lang}/LC_MESSAGES/messages.po')
        if po_path.exists():
            count = process_po_file(po_path, lang, translator)
            total_translated += count
        else:
            print(f"  Warning: File not found: {po_path}")

    print(f"\n{'='*60}")
    print(f"Total translations: {total_translated}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
