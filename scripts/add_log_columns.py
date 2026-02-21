#!/usr/bin/env python
"""
Manual migration to add log content columns to journal_sessions table.

Run with: python scripts/add_log_columns.py
"""
import sqlite3
import os

# Determine DB path relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, '..', 'instance', 'app.db')


def migrate():
    """Add log content columns to journal_sessions table if they don't exist."""
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        print("It will be created automatically when the app starts.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get existing columns
    cursor.execute("PRAGMA table_info(journal_sessions)")
    columns = [col[1] for col in cursor.fetchall()]

    added = []

    if 'asiair_log_content' not in columns:
        cursor.execute("ALTER TABLE journal_sessions ADD COLUMN asiair_log_content TEXT")
        added.append('asiair_log_content')

    if 'phd2_log_content' not in columns:
        cursor.execute("ALTER TABLE journal_sessions ADD COLUMN phd2_log_content TEXT")
        added.append('phd2_log_content')

    if 'log_analysis_cache' not in columns:
        cursor.execute("ALTER TABLE journal_sessions ADD COLUMN log_analysis_cache TEXT")
        added.append('log_analysis_cache')

    conn.commit()
    conn.close()

    if added:
        print(f"Migration complete. Added columns: {', '.join(added)}")
    else:
        print("Migration complete. All columns already exist.")


if __name__ == '__main__':
    migrate()
