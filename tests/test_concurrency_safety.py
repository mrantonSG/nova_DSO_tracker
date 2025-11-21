import pytest
import threading
import inspect
import sys, os
from unittest.mock import MagicMock

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import (
    warm_main_cache,
    update_outlook_cache,
    trigger_outlook_update_for_user,
    app
)


# --- 1. The Magic Helper ---
def assert_thread_target_signature(mock_thread_cls):
    """
    Checks every call made to threading.Thread.
    It grabs the 'target' function and the 'args'/'kwargs',
    and attempts to bind them. If arguments are missing/wrong,
    inspect.signature.bind() raises a TypeError immediately.
    """
    for call in mock_thread_cls.call_args_list:
        # Extract arguments passed to threading.Thread(...)
        call_kwargs = call.kwargs
        target_func = call_kwargs.get('target')
        target_args = call_kwargs.get('args', ())
        target_kwargs = call_kwargs.get('kwargs', {})

        if target_func:
            # Get the actual definition of the function being called
            sig = inspect.signature(target_func)

            try:
                # THIS IS THE KEY: Python will throw an error here if
                # you passed 4 args but the function needs 6.
                sig.bind(*target_args, **target_kwargs)
            except TypeError as e:
                pytest.fail(f"Thread Signature Mismatch for '{target_func.__name__}': {e}")


@pytest.fixture
def strict_thread_mock(monkeypatch):
    """
    Replaces threading.Thread with a Mock that we can inspect later.
    """
    mock_thread = MagicMock()
    monkeypatch.setattr('threading.Thread', mock_thread)
    return mock_thread


# --- 2. The Tests ---

def test_warm_main_cache_calls_outlook_with_correct_args(strict_thread_mock, db_session):
    """
    Regression Test for Fix A:
    Ensures warm_main_cache passes all 6 arguments to update_outlook_cache.
    """
    # Arrange: Dummy data to satisfy the function internals
    username = "default"
    loc_name = "Default Test Loc"
    user_config = {
        "locations": {
            "Default Test Loc": {"lat": 10, "lon": 10, "timezone": "UTC"}
        },
        "objects": []
    }

    # Act: Call the function that spawns the thread
    warm_main_cache(username, loc_name, user_config, sampling_interval=15)

    # Assert: Check signatures
    assert_thread_target_signature(strict_thread_mock)

    # Verify specifically that it called update_outlook_cache
    calls = strict_thread_mock.call_args_list
    targets = [c.kwargs.get('target').__name__ for c in calls]
    assert "update_outlook_cache" in targets


def test_import_config_spawns_thread_with_correct_args(strict_thread_mock, client, db_session):
    """
    Regression Test for Fix B:
    Ensures /import_config route spawns the background thread with correct arguments.
    """
    import io

    # Arrange: Valid YAML to pass validation
    yaml_content = b"""
    default_location: Home
    locations:
      Home:
        lat: 50
        lon: 10
        timezone: UTC
        active: true
    objects: []
    """

    # Act: Post to the import route
    client.post('/import_config', data={
        'file': (io.BytesIO(yaml_content), 'config.yaml')
    }, follow_redirects=True)

    # Assert: Verify signatures of any threads spawned
    assert_thread_target_signature(strict_thread_mock)

    # Verify specifically that update_outlook_cache was the target
    # (It runs only if the location is new/cache missing, which it is in this test env)
    calls = strict_thread_mock.call_args_list
    targets = [c.kwargs.get('target').__name__ for c in calls if c.kwargs.get('target')]
    assert "update_outlook_cache" in targets


def test_trigger_outlook_update_signature(strict_thread_mock, db_session):
    """
    General Safety Test:
    Ensures the helper function `trigger_outlook_update_for_user`
    (used by the 'Active Project' toggle) uses the correct signature.
    """
    from flask import g

    # Arrange: Setup Global Context (g)
    class MockUser:
        id = 1
        username = "test_user"

    # We need to fake the 'g' context and db lookup inside the function
    with app.app_context():
        g.db_user = MockUser()

        # Act
        trigger_outlook_update_for_user("test_user")

        # Assert
        assert_thread_target_signature(strict_thread_mock)