# In tests/test_nova_auth.py
import pytest
import sys, os

# 1. Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 2. Import models needed
from nova import (
    DbUser,
    get_or_create_db_user
)


def test_login_page_loads(mu_client_logged_out):
    """
    Tests that the /login page loads correctly in multi-user mode.
    """
    response = mu_client_logged_out.get('/login')
    assert response.status_code == 200
    assert b"Log In" in response.data
    assert b"Username" in response.data


def test_login_success(mu_client_logged_out, db_session):
    """
    Tests a successful login POST to /login.
    We use the "UserA" user created in the mu_client_logged_out fixture.
    """
    client = mu_client_logged_out

    # 1. ARRANGE
    # The 'mu_client_logged_out' fixture already created the
    # mock 'UserA' auth user with password 'password123'
    # and the corresponding 'UserA' app.db user.

    # 2. ACT
    response = client.post(
        '/login',
        data={
            'username': 'UserA',
            'password': 'password123'
        },
        follow_redirects=True  # Follow the redirect to the index page
    )

    # 3. ASSERT
    assert response.status_code == 200
    assert b"Logged in successfully!" in response.data
    assert b"<h1>Nova</h1>" in response.data  # Landed on index

    # Check that the session cookie was set
    with client.session_transaction() as sess:
        assert sess['_user_id'] == '1'  # Auth ID for UserA
        assert sess['_fresh'] is True


def test_login_fail_password(mu_client_logged_out):
    """
    Tests a failed login POST due to an incorrect password.
    """
    client = mu_client_logged_out

    # 2. ACT
    response = client.post(
        '/login',
        data={
            'username': 'UserA',
            'password': 'wrong-password'  # <-- Wrong password
        },
        follow_redirects=True
    )

    # 3. ASSERT
    assert response.status_code == 200  # Stays on login page
    assert b"Invalid username or password." in response.data
    assert b"Logged in successfully!" not in response.data

    # Check that no session cookie was set
    with client.session_transaction() as sess:
        assert '_user_id' not in sess


def test_login_fail_username(mu_client_logged_out):
    """
    Tests a failed login POST due to a non-existent username.
    """
    client = mu_client_logged_out

    # 2. ACT
    response = client.post(
        '/login',
        data={
            'username': 'NoSuchUser',
            'password': 'password123'
        },
        follow_redirects=True
    )

    # 3. ASSERT
    assert response.status_code == 200  # Stays on login page
    assert b"Invalid username or password." in response.data

    with client.session_transaction() as sess:
        assert '_user_id' not in sess