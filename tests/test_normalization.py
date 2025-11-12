import pytest
import sys, os
from datetime import date

# Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# --- Function to test ---
from nova import normalize_object_name
# --- Function to test (downstream) ---
from nova import _migrate_journal

# --- DB models needed for the integration test ---
# These are imported to create test data
from nova import DbUser, AstroObject, JournalSession

# Note: This file assumes your 'conftest.py' provides the 'db_session' fixture.


# =============================================================================
# 1. Unit Test: Check all normalization rules directly
# =============================================================================

# A comprehensive list of test cases based on every rule in your nova.py file
# (Input, Expected Output)
normalization_test_cases = [
    # --- 1. Known Corrupt Inputs (from the regex rules) ---
    ("SH2155", "SH 2-155"),
    ("SH2-155", "SH 2-155"),  # Test idempotency (already correct)
    ("SH2129", "SH 2-129"),
    ("NGC1976", "NGC 1976"),
    ("VDB1", "VDB 1"),
    ("GUM16", "GUM 16"),
    ("TGUH1867", "TGU H1867"),
    ("LHA120N70", "LHA 120-N 70"),
    ("SNRG180.001.7", "SNR G180.0-01.7"),
    ("CTA1", "CTA 1"),
    ("HB3", "HB 3"),
    ("PNARO121", "PN ARO 121"),
    ("LIESTO1", "LIESTO 1"),
    ("PK08114.1", "PK 081-14.1"),
    ("PNG093.302.4", "PN G093.3-02.4"),
    ("WR134", "WR 134"),
    ("ABELL21", "ABELL 21"),
    ("BARNARD33", "BARNARD 33"),

    # --- 2. Simple Space Removal (M-objects) ---
    ("M 42", "M42"),
    ("m 42", "M42"),  # Check case
    ("M 101", "M101"),

    # --- 3. Default Fallback (Whitespace, Case, Correct) ---
    (" M 42 ", "M42"),  # Stripping and rule
    ("m42", "M42"),  # Case
    ("   M31   ", "M31"),  # Stripping
    ("IC 405", "IC 405"),  # Already correct
    ("LHA 120-N 70", "LHA 120-N 70"),  # Already correct
    ("NGC 1976", "NGC 1976"),  # Already correct
    ("Andromeda Galaxy", "ANDROMEDA GALAXY"),  # Case and multiple words
    ("Heart  Nebula", "HEART NEBULA"),  # Collapse internal whitespace

    # --- 4. Edge Cases ---
    (None, None),
    ("", None),
    ("   ", None),  # Whitespace only
]

@pytest.mark.parametrize("corrupt_input, expected_output", normalization_test_cases)
def test_normalize_object_name_rules(corrupt_input, expected_output):
    """
    Tests every single normalization rule defined in the function.
    """
    assert normalize_object_name(corrupt_input) == expected_output


# =============================================================================
# 2. Integration Test: Check the downstream effect on the database
# =============================================================================

def test_journal_migration_links_normalized_names(db_session):
    """
    Tests that _migrate_journal correctly uses normalize_object_name
    to link a journal entry (with a "corrupt" name) to the
    AstroObject (with the "correct" name). This confirms the critical
    downstream behavior.
    """
    # 1. ARRANGE
    # Create a user
    user = DbUser(username="test_user")
    db_session.add(user)
    db_session.commit()  # Get user.id

    # Create the AstroObject with the *correct, normalized* name
    correct_object = AstroObject(
        user_id=user.id,
        object_name="SH 2-129",  # The correct ID
        common_name="Flying Bat Nebula",
        ra_hours=21.2,
        dec_deg=58.5
    )
    db_session.add(correct_object)
    db_session.commit()

    # Create a mock YAML journal dictionary with the *corrupt* name
    corrupt_journal_yaml = {
        "projects": [],
        "sessions": [
            {
                "session_id": "s1",
                "session_date": "2025-01-01",
                "target_object_id": "SH2129"  # The corrupt ID
            },
            {
                "session_id": "s2",
                "session_date": "2025-01-02",
                "object_name": "NGC1976"  # Another corrupt ID
            }
        ]
    }

    # 2. ACT
    # Run the migration function (which uses normalize_object_name internally)
    _migrate_journal(db_session, user, corrupt_journal_yaml)
    db_session.commit()

    # 3. ASSERT
    # Check that the sessions were created in the DB with the *normalized* name
    session1 = db_session.query(JournalSession).filter_by(external_id="s1").one_or_none()
    assert session1 is not None
    # This is the key check:
    assert session1.object_name == "SH 2-129"  # Asserts normalization worked

    session2 = db_session.query(JournalSession).filter_by(external_id="s2").one_or_none()
    assert session2 is not None
    # This is the second key check:
    assert session2.object_name == "NGC 1976"  # Asserts normalization worked