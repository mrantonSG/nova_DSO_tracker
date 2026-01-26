import pytest
import json
from datetime import datetime, timedelta
from nova import DbUser, Location, AstroObject, get_db, app

# --- Helpers ---
def parse_hhmm_to_minutes(time_str):
    """Converts 'HH:MM' string to total minutes."""
    if not time_str or ":" not in time_str:
        return 0
    h, m = map(int, time_str.split(':'))
    return h * 60 + m


# --- Tests ---

def test_sun_events_simulation_mode_moon_phase(client):
    """
    Verifies that passing ?sim_date changes the calculated moon phase
    in the /sun_events endpoint.
    """
    # 1. Pick a known Full Moon date (e.g., Jan 13, 2025)
    full_moon_date = "2025-01-13"
    res_full = client.get(f'/sun_events?location=default&sim_date={full_moon_date}')
    assert res_full.status_code == 200
    data_full = res_full.json
    # Phase should be high (98-100%)
    assert data_full['phase'] >= 98.0, f"Expected Full Moon on {full_moon_date}, got {data_full['phase']}%"

    # 2. Pick a known New Moon date (e.g., Jan 29, 2025)
    new_moon_date = "2025-01-29"
    res_new = client.get(f'/sun_events?location=default&sim_date={new_moon_date}')
    assert res_new.status_code == 200
    data_new = res_new.json
    # Phase should be low (0-5%)
    assert data_new['phase'] <= 5.0, f"Expected New Moon on {new_moon_date}, got {data_new['phase']}%"

    # 3. Verify date echo
    assert data_full['date'] == full_moon_date
    assert data_new['date'] == new_moon_date

def test_desktop_batch_simulation_transit_shift(client):
    """
    Verifies that passing ?sim_date to the batch endpoint shifts calculations
    (specifically Transit Time) correctly.
    """
    # 1. Setup DB state (User, Location, Object)
    with app.app_context():
        db = get_db()
        # In Single User Mode (default for tests), the app forces usage of the 'default' user.
        # We must attach our test data to 'default' so the app finds it.
        target_username = "default"

        u = db.query(DbUser).filter_by(username=target_username).one_or_none()
        if not u:
            u = DbUser(username=target_username, active=True)
            db.add(u)
            db.flush()

        # Create Location
        loc = Location(user_id=u.id, name="SimLoc", lat=50.0, lon=10.0, timezone="UTC", is_default=True)
        db.add(loc)

        # Create Object (M42 - Orion Nebula)
        # RA: ~5.5h.
        obj = db.query(AstroObject).filter_by(user_id=u.id, object_name="M42").one_or_none()
        if not obj:
            obj = AstroObject(user_id=u.id, object_name="M42", common_name="Orion Nebula",
                              ra_hours=5.588, dec_deg=-5.39, enabled=True)
            db.add(obj)
        else:
            # Ensure coordinates are what we expect for the test math
            obj.ra_hours = 5.588
            obj.dec_deg = -5.39
            obj.enabled = True

        db.commit()

        user_id = u.id

    # 2. Simulate Login (using session cookie trick or SINGLE_USER_MODE reliance)
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True

    # 3. Request Baseline Data (Date 1)
    date_1 = "2025-01-01"
    res1 = client.get(f'/api/get_desktop_data_batch?location=SimLoc&sim_date={date_1}')
    assert res1.status_code == 200
    item1 = res1.json['results'][0]
    transit1_str = item1['Transit Time']

    # 4. Request Future Data (Date 1 + 30 days)
    # Transit time shifts earlier by ~4 mins per day. 30 days = ~120 mins earlier.
    date_2 = "2025-01-31"
    res2 = client.get(f'/api/get_desktop_data_batch?location=SimLoc&sim_date={date_2}')
    assert res2.status_code == 200
    item2 = res2.json['results'][0]
    transit2_str = item2['Transit Time']

    # 5. Assertions
    t1_mins = parse_hhmm_to_minutes(transit1_str)
    t2_mins = parse_hhmm_to_minutes(transit2_str)

    diff = t1_mins - t2_mins

    # Allow small margin of error (e.g., 115-125 mins) due to rounding or exact orbital mechanics
    print(f"DEBUG: Transit 1 ({date_1}): {transit1_str}, Transit 2 ({date_2}): {transit2_str}, Diff: {diff} min")

    assert 110 <= diff <= 130, \
        f"Transit time did not shift correctly. Expected ~120min diff, got {diff}min ({transit1_str} vs {transit2_str})"

    # Verify other calculated fields are present
    assert item1['Altitude Current'] != "N/A"
    assert item2['Altitude Current'] != "N/A"