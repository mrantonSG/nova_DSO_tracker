"""
Endpoint tests for the guiding_limits key in /api/session/<id>/log-analysis.

guiding_limits is derived from the session's rig_scale_snapshot at request
time and must never be written into JournalSession.log_analysis_cache.
"""

import json
from datetime import date

import pytest

from nova import get_or_create_db_user
from nova.models import JournalSession


CACHED_ANALYSIS = {
    'has_logs': True,
    'asiair': None,
    'phd2': {
        'session_start': '2025-01-01T21:00:00',
        'rms': [[0.0, 0.3, 0.4, 0.5], [0.1, 0.35, 0.45, 0.57]],
    },
    'nina': None,
}


def _make_session(db_session, scale):
    user = get_or_create_db_user(db_session, "default")
    session = JournalSession(
        user_id=user.id,
        date_utc=date(2025, 1, 1),
        object_name="M42",
        rig_scale_snapshot=scale,
        log_analysis_cache=json.dumps(CACHED_ANALYSIS),
    )
    db_session.add(session)
    db_session.commit()
    return session.id


@pytest.mark.parametrize(
    ("scale", "expected"),
    [
        (1.44, [0.48, 0.72, 1.44, 2.16]),
        (None, None),
    ],
)
def test_log_analysis_returns_guiding_limits(su_client_logged_in, db_session, scale, expected):
    session_id = _make_session(db_session, scale)

    response = su_client_logged_in.get(f'/api/session/{session_id}/log-analysis')

    assert response.status_code == 200
    data = response.get_json()
    assert data['guiding_limits'] == expected
    assert data['phd2']['rms'] == CACHED_ANALYSIS['phd2']['rms']

    db_session.expire_all()
    stored = db_session.get(JournalSession, session_id)
    assert 'guiding_limits' not in json.loads(stored.log_analysis_cache)
