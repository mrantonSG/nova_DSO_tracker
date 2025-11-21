import pytest
import requests
import os
import time

# 1. Configuration
# By default, we assume the app is running locally on port 5001.
# You can override this with an environment variable if you change ports.
BASE_URL = os.environ.get("NOVA_LIVE_URL", "http://0.0.0.0:5001")


def test_server_is_reachable():
    """
    CRITICAL: Can we actually connect to the server?
    If this fails, the app isn't running or the port is blocked.
    """
    try:
        # We hit the login page because it's always public/accessible
        url = f"{BASE_URL}/login"
        print(f"Ping {url}...")
        response = requests.get(url, timeout=5)

        # A 200 means the page loaded.
        # A 302 means it redirected (also fine, means logic is working).
        assert response.status_code in [200, 302]
    except requests.exceptions.ConnectionError:
        pytest.fail(f"CRITICAL: Could not connect to {BASE_URL}. Is the server running?")


def test_static_files_serving():
    """
    Verifies that static assets (CSS/Images) are being served.
    This often breaks in new Docker/Gunicorn setups.
    """
    url = f"{BASE_URL}/static/favicon.ico"
    response = requests.get(url, timeout=5)

    assert response.status_code == 200
    assert "image" in response.headers.get("Content-Type", "")


def test_api_endpoint_json():
    """
    Verifies that the API is returning valid JSON (application logic is running).
    """
    url = f"{BASE_URL}/api/latest_version"
    response = requests.get(url, timeout=5)

    assert response.status_code == 200
    assert response.headers.get("Content-Type") == "application/json"

    data = response.json()
    # Just ensure it's a dictionary, meaning JSON parsing worked
    assert isinstance(data, dict)


def test_no_server_error_on_home():
    """
    Hits the home page to ensure we don't get a 500 Internal Server Error.
    """
    # This might redirect to login, which is fine. We just don't want 500.
    response = requests.get(f"{BASE_URL}/", allow_redirects=True)
    assert response.status_code != 500
    assert response.status_code != 502