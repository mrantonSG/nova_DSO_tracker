#!/usr/bin/env python3
"""
Translate help documentation markdown files using DeepL API.
"""

import deepl
import os
from pathlib import Path

# Configuration
API_KEY = "a9bd4ff7-0081-4351-9af6-fb6cf2deb17b:fx"
SOURCE_DIR = Path("../help_docs/en")
TARGET_DIRS = {
    "es": Path("../help_docs/es"),
    "zh": Path("../help_docs/zh"),
}

# Terms that should not be translated (preserve exactly)
DO_NOT_TRANSLATE = {
    "Nova", "DSO", "SQM", "RA", "Dec", "FWHM", "RMS", "PHD2", "ASIAIR",
    "NINA", "OAG", "Darks", "Flats", "Bias", "SIMBAD", "HFR", "FITS",
    "PNG", "CSV", "JSON", "RGB", "Ha", "OIII", "SII", "PA"
}

# Language codes for DeepL
LANG_CODES = {
    "es": "ES",
    "zh": "ZH",
}


def protect_terms(text):
    """Replace protected terms with placeholders."""
    replacements = {}
    for i, term in enumerate(sorted(DO_NOT_TRANSLATE, key=len, reverse=True)):
        placeholder = f"__TERM_{i}__"
        replacements[placeholder] = term
        text = text.replace(term, placeholder)
    return text, replacements


def restore_terms(text, replacements):
    """Restore protected terms from placeholders."""
    for placeholder, term in replacements.items():
        text = text.replace(placeholder, term)
    return text


def translate_file(source_file, target_file, lang_code):
    """Translate a single markdown file."""
    translator = deepl.Translator(API_KEY)

    with open(source_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Protect terms before translation
    protected_content, replacements = protect_terms(content)

    try:
        result = translator.translate_text(
            protected_content,
            target_lang=LANG_CODES[lang_code],
            preserve_formatting=True
        )
        translated = result.text

        # Restore terms after translation
        translated = restore_terms(translated, replacements)

        # Write the translated file
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(translated)

        return True, None

    except Exception as e:
        return False, str(e)


def main():
    # Get all .md files from source directory
    source_files = sorted(SOURCE_DIR.glob("*.md"))

    if not source_files:
        print("No markdown files found in source directory.")
        return

    print(f"Found {len(source_files)} markdown files to translate.")
    print()

    results = {
        "es": {"success": [], "error": []},
        "zh": {"success": [], "error": []},
    }

    # Translate each file for each language
    for lang_code in TARGET_DIRS:
        print(f"Translating to {lang_code.upper()}...")
        target_dir = TARGET_DIRS[lang_code]

        for source_file in source_files:
            target_file = target_dir / source_file.name
            print(f"  - {source_file.name}...", end=" ")

            success, error = translate_file(source_file, target_file, lang_code)

            if success:
                print("OK")
                results[lang_code]["success"].append(source_file.name)
            else:
                print(f"ERROR: {error}")
                results[lang_code]["error"].append((source_file.name, error))

        print()

    # Print summary report
    print("=" * 60)
    print("TRANSLATION REPORT")
    print("=" * 60)

    for lang_code in TARGET_DIRS:
        print(f"\n{lang_code.upper()} ({LANG_CODES[lang_code]}):")
        print(f"  Files translated: {len(results[lang_code]['success'])}")
        print(f"  Files skipped/errored: {len(results[lang_code]['error'])}")

        if results[lang_code]["error"]:
            print("  Errors:")
            for filename, error in results[lang_code]["error"]:
                print(f"    - {filename}: {error}")

    # Verify file counts match
    source_count = len(source_files)
    es_count = len(list(TARGET_DIRS["es"].glob("*.md")))
    zh_count = len(list(TARGET_DIRS["zh"].glob("*.md")))

    print()
    print("=" * 60)
    print("FILE COUNT VERIFICATION")
    print("=" * 60)
    print(f"Source (en): {source_count} files")
    print(f"Target (es): {es_count} files")
    print(f"Target (zh): {zh_count} files")

    if source_count == es_count == zh_count:
        print("\nAll files translated successfully!")
    else:
        print("\nWARNING: File count mismatch!")


if __name__ == "__main__":
    main()
