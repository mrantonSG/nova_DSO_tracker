# In tests/test_nova_cli.py
import pytest
import sys, os
from datetime import date  # <-- 1. IMPORT THE 'date' OBJECT

# 1. Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 2. Import the app itself, and the models we need to test
from nova import (
    app,
    DbUser,
    AstroObject,
    JournalSession,
    get_db
)


def test_cli_repair_corrupt_ids(db_session):
    """
    Tests the 'flask repair-corrupt-ids' command.
    """
    # 1. ARRANGE
    user = DbUser(username="cli_test_user")
    db_session.add(user)
    db_session.commit()

    # --- FIX 2: Store the user_id before it gets detached ---
    user_id = user.id

    corrupt_obj = AstroObject(
        user_id=user_id,  # Use the variable
        object_name="SH2129",
        ra_hours=1, dec_deg=1
    )
    correct_obj = AstroObject(
        user_id=user_id,  # Use the variable
        object_name="M42",
        ra_hours=5.58, dec_deg=-5.4
    )
    db_session.add_all([corrupt_obj, correct_obj])

    journal = JournalSession(
        user_id=user_id,  # Use the variable
        object_name="SH2129",
        date_utc=date(2025, 1, 1)  # <-- FIX 3: Use date() object
    )
    db_session.add(journal)
    db_session.commit()

    journal_id = journal.id

    # 2. ACT
    runner = app.test_cli_runner()
    result = runner.invoke(args=["repair-corrupt-ids"])

    # 3. ASSERT
    assert result.exit_code == 0
    assert "Repairing: 'SH2129' -> 'SH 2-129'" in result.output

    # Use the user_id variable in our queries
    obj_corrupt_check = db_session.query(AstroObject).filter_by(
        user_id=user_id, object_name="SH2129"
    ).one_or_none()
    assert obj_corrupt_check is None

    obj_repaired_check = db_session.query(AstroObject).filter_by(
        user_id=user_id, object_name="SH 2-129"
    ).one_or_none()
    assert obj_repaired_check is not None

    obj_correct_check = db_session.query(AstroObject).filter_by(
        user_id=user_id, object_name="M42"
    ).one_or_none()
    assert obj_correct_check is not None

    journal_check = db_session.get(JournalSession, journal_id)
    assert journal_check.object_name == "SH 2-129"


def test_cli_repair_image_links(db_session, monkeypatch):
    """
    Tests the 'flask repair-image-links' command.
    """
    # 1. ARRANGE
    monkeypatch.setattr('nova.SINGLE_USER_MODE', True)

    user = DbUser(username="default")
    db_session.add(user)
    db_session.commit()

    # --- FIX 2: Store the user_id before it gets detached ---
    user_id = user.id

    obj = AstroObject(
        user_id=user_id,  # Use the variable
        object_name="M31",
        ra_hours=1, dec_deg=1,
        project_name='<img src="http://localhost:5001/uploads/mrantonSG/pic.jpg">'
    )
    journal = JournalSession(
        user_id=user_id,  # Use the variable
        object_name="M31",
        date_utc=date(2025, 1, 1),  # <-- FIX 3: Use date() object
        notes='<img src="/uploads/someotheruser/img.png">'
    )
    db_session.add_all([obj, journal])
    db_session.commit()

    obj_id = obj.id
    journal_id = journal.id

    # 2. ACT
    runner = app.test_cli_runner()
    result = runner.invoke(args=["repair-image-links"])

    # 3. ASSERT
    assert result.exit_code == 0

    # --- FIX 4: Update assertions to match the CLI output ---
    assert "Fixed links in 1 AstroObject note(s)" in result.output
    assert "Fixed links in 1 JournalSession note(s)" in result.output

    obj_check = db_session.get(AstroObject, obj_id)
    assert obj_check.project_name == '<img src="/uploads/default/pic.jpg">'

    journal_check = db_session.get(JournalSession, journal_id)
    assert journal_check.notes == '<img src="/uploads/default/img.png">'