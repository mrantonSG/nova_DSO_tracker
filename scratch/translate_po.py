import deepl
import polib
import sys

API_KEY = "a9bd4ff7-0081-4351-9af6-fb6cf2deb17b:fx"   # paste your key

LANG_MAP = {
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "ja": "JA",
    "zh": "ZH",
}

def translate(lang_code):
    deepl_lang = LANG_MAP[lang_code]
    po_path = f"translations/{lang_code}/LC_MESSAGES/messages.po"

    translator = deepl.Translator(API_KEY)
    po = polib.pofile(po_path)

    untranslated = po.untranslated_entries()
    print(f"Translating {len(untranslated)} strings to {deepl_lang}...")

    for i, entry in enumerate(untranslated):
        if not entry.msgid:          # skip the header entry
            continue
        try:
            result = translator.translate_text(entry.msgid, target_lang=deepl_lang)
            entry.msgstr = result.text
        except Exception as e:
            print(f"  Error on entry {i}: {e}")
            continue

        if i % 50 == 0:
            print(f"  {i}/{len(untranslated)} done...")

    po.save()
    print(f"Saved {po_path}")

if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "fr"
    translate(lang)