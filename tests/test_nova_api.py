import pytest
from datetime import date
import sys, os

# 1. Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 2. Import only what the TESTS need
from nova import (
    DbUser,
    Location,
    AstroObject,
    Project,
    JournalSession,
    get_db
)

# --- All Fixtures are GONE (moved to conftest.py) ---


# --- 5. Your API Tests (Unchanged) ---

def test_homepage_loads(client):
    """ Tests if the homepage (/) loads without errors. """
    response = client.get('/')
    assert response.status_code == 200
    assert b"<h1>Nova</h1>" in response.data
    assert b"DSO Tracker" in response.data

# ... (all your other tests remain exactly as they are) ...

def test_moon_api_bug_with_no_ra(client):
    # ...
    pass

def test_graph_dashboard_404(client):
    # ...
    pass

def test_add_journal_session_post(client):
    # ...
    pass

def test_journal_add_redirects_when_logged_out(client_logged_out):
    # ...
    pass

def test_add_journal_session_fails_with_bad_date(client):
    # ...
    pass