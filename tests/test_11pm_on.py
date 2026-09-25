from modules.astro_calculations import get_utc_time_for_local_11pm_on


def test_vienna_before_dst_change_is_cest():
    # 2026-10-24 is still CEST (UTC+2)
    assert get_utc_time_for_local_11pm_on("2026-10-24", "Europe/Vienna") == "2026-10-24T21:00:00"


def test_vienna_on_dst_change_night_is_cet():
    # DST ends 2026-10-25 03:00, so 23:00 that evening is CET (UTC+1)
    assert get_utc_time_for_local_11pm_on("2026-10-25", "Europe/Vienna") == "2026-10-25T22:00:00"


def test_utc_is_2300_same_day():
    assert get_utc_time_for_local_11pm_on("2026-03-01", "UTC") == "2026-03-01T23:00:00"
