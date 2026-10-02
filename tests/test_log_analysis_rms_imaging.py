"""
Endpoint tests for refreshing pre-rms_imaging caches in
/api/session/<id>/log-analysis.
"""

import json
from datetime import date

from nova import get_or_create_db_user
from nova.models import JournalSession


# Minimal PHD2 log: 40 frames, enough for the 30-frame rolling window
PHD2_LOG = "\n".join(
    [
        "Guiding Begins at 2025-01-01 21:00:00",
        "Pixel scale = 1.00 arc-sec/px",
        "Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,SNR",
    ]
    + [f'{n},{n * 2.0:.3f},"Mount",0.3,0.3,0.3,0.3,40.0' for n in range(1, 41)]
    + ["Guiding Ends at 2025-01-01 21:01:20"]
) + "\n"

OLD_CACHE = {
    'has_logs': True,
    'asiair': None,
    'phd2': {
        'session_start': '2025-01-01T21:00:00',
        'rms': [[0.0, 0.3, 0.4, 0.5], [0.1, 0.35, 0.45, 0.57]],
    },
    'nina': None,
}


def _make_session(db_session, phd2_log_content):
    user = get_or_create_db_user(db_session, "default")
    session = JournalSession(
        user_id=user.id,
        date_utc=date(2025, 1, 1),
        object_name="M42",
        phd2_log_content=phd2_log_content,
        log_analysis_cache=json.dumps(OLD_CACHE),
    )
    db_session.add(session)
    db_session.commit()
    return session.id


def test_stale_cache_served_unchanged_when_log_unreadable(su_client_logged_in, db_session):
    session_id = _make_session(db_session, 'instance/logs/phd2/does_not_exist.log')

    response = su_client_logged_in.get(f'/api/session/{session_id}/log-analysis')

    assert response.status_code == 200
    data = response.get_json()
    assert data['phd2'] == OLD_CACHE['phd2']

    db_session.expire_all()
    stored = db_session.get(JournalSession, session_id)
    assert stored.log_analysis_cache
    assert json.loads(stored.log_analysis_cache) == OLD_CACHE


def test_stale_cache_replaced_when_log_reparses(su_client_logged_in, db_session):
    # Legacy raw-content storage: read_log_content returns the value as-is
    session_id = _make_session(db_session, PHD2_LOG)

    response = su_client_logged_in.get(f'/api/session/{session_id}/log-analysis')

    assert response.status_code == 200
    data = response.get_json()
    assert data['phd2']['rms_imaging']

    db_session.expire_all()
    stored = json.loads(db_session.get(JournalSession, session_id).log_analysis_cache)
    assert stored['phd2']['rms_imaging'] == data['phd2']['rms_imaging']
    assert 'guiding_limits' not in stored


def test_stale_cache_kept_when_log_has_no_frames(su_client_logged_in, db_session):
    # Readable but truncated log: header only, no data rows
    truncated = "\n".join(PHD2_LOG.splitlines()[:3]) + "\n"
    session_id = _make_session(db_session, truncated)

    response = su_client_logged_in.get(f'/api/session/{session_id}/log-analysis')

    assert response.status_code == 200
    assert response.get_json()['phd2'] == OLD_CACHE['phd2']

    db_session.expire_all()
    stored = db_session.get(JournalSession, session_id)
    assert json.loads(stored.log_analysis_cache) == OLD_CACHE
