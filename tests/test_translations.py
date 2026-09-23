import glob
import os

import pytest
from babel.messages.pofile import read_po

TRANSLATIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'translations'))
PO_FILES = sorted(glob.glob(os.path.join(TRANSLATIONS_DIR, '*', 'LC_MESSAGES', 'messages.po')))


def test_po_files_found():
    assert PO_FILES, f"No .po files found under {TRANSLATIONS_DIR}"


@pytest.mark.parametrize('po_path', PO_FILES, ids=lambda p: p.split(os.sep)[-3])
def test_no_fuzzy_entries(po_path):
    """Fuzzy entries hold msgstrs copied from similar-looking msgids and are skipped at compile time.

    Run `pybabel update` with -N (--no-fuzzy-matching) and translate new entries explicitly.
    """
    with open(po_path, 'rb') as f:
        catalog = read_po(f)
    assert not catalog.fuzzy, f"Catalog header is marked fuzzy in {po_path}"
    fuzzy = [m.id for m in catalog if m.id and m.fuzzy]
    assert not fuzzy, f"{len(fuzzy)} fuzzy entries in {po_path}: {fuzzy[:10]}"
