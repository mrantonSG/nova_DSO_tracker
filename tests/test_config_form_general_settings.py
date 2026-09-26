"""General settings save when UiPref stores imaging_criteria as null.

A null imaging_criteria must be replaced by a dict holding the submitted
values instead of raising TypeError and rolling back the whole save.
"""

import json

from nova import DbUser, UiPref


def test_general_settings_save_with_null_imaging_criteria(su_client_logged_in, db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    prefs = {"altitude_threshold": 20, "imaging_criteria": None}
    pref = db_session.query(UiPref).filter_by(user_id=user.id).one_or_none()
    if pref is None:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(prefs)))
    else:
        pref.json_blob = json.dumps(prefs)
    db_session.commit()

    response = su_client_logged_in.post("/config_form", data={
        "submit_general": "1",
        "altitude_threshold": "25",
        "min_observable_minutes": "90",
        "min_max_altitude": "40",
        "max_moon_illumination": "15",
        "min_angular_separation": "45",
        "search_horizon_months": "3",
    })

    assert response.status_code == 302
    db_session.expire_all()
    saved = json.loads(db_session.query(UiPref).filter_by(user_id=user.id).one().json_blob)
    assert saved["altitude_threshold"] == 25
    assert saved["imaging_criteria"] == {
        "min_observable_minutes": 90,
        "min_max_altitude": 40,
        "max_moon_illumination": 15,
        "min_angular_separation": 45,
        "search_horizon_months": 3,
    }


def test_general_settings_save_with_null_telemetry(su_client_logged_in, db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    prefs = {"altitude_threshold": 20, "telemetry": None}
    pref = db_session.query(UiPref).filter_by(user_id=user.id).one_or_none()
    if pref is None:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(prefs)))
    else:
        pref.json_blob = json.dumps(prefs)
    db_session.commit()

    response = su_client_logged_in.post("/config_form", data={
        "submit_general": "1",
        "altitude_threshold": "25",
        "telemetry_enabled": "on",
    })

    assert response.status_code == 302
    db_session.expire_all()
    saved = json.loads(db_session.query(UiPref).filter_by(user_id=user.id).one().json_blob)
    assert saved["altitude_threshold"] == 25
    assert saved["telemetry"] == {"enabled": True}
