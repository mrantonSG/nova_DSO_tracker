"""window.selectedSessionData - the graph page's selected-session payload.

core.graph_dashboard() builds it from nova.journal_rows.GRAPH_SESSION_JS_FIELDS:
exactly the keys static/js/journal_section.js reads (populateEditForm,
setupEditMode / initializeFormState, updateGuidingRmsHint). The four log columns
stay server-side - the Logs tab fetches /api/session/<id>/log-analysis when it
opens - and the server-rendered view and edit form keep using the full row,
which test_server_render_still_uses_the_full_row() pins.

The payload is pinned to a literal _FIELDS here, not to the tuple: a key deleted
from GRAPH_SESSION_JS_FIELDS must fail, not silently shrink both sides.
"""
import json
from datetime import date

from nova import DbUser, JournalSession
from nova.journal_rows import GRAPH_SESSION_JS_FIELDS
from nova.record_links import sync_session_links

_LOG_COLUMNS = ("log_analysis_cache", "asiair_log_content",
                "phd2_log_content", "nina_log_content")
_MARKER = "window.selectedSessionData = "

# The contract, pinned independently of nova/journal_rows.py: a key deleted from
# the tuple must fail here, so the tuple and its expectation cannot move together.
_FIELDS = {
    "id", "date_utc", "location_name", "project_ids", "notes",
    "rig_record_uid", "rig_scale_snapshot", "seeing_observed_fwhm",
    "sky_sqm_observed", "moon_illumination_session",
    "moon_angular_separation_session", "telescope_setup_notes",
    "guiding_rms_avg_arcsec", "guiding_equipment", "exposure_time_per_sub_sec",
    "number_of_subs_light", "filter_used_session", "gain_setting",
    "offset_setting", "session_rating_subjective", "transparency_observed_scale",
    "weather_notes", "dither_pixels", "dither_every_n", "dither_notes",
    "dither_details", "acquisition_software", "camera_temp_setpoint_c",
    "camera_temp_actual_avg_c", "binning_session", "darks_strategy",
    "flats_strategy", "bias_darkflats_strategy",
    "filter_L_subs", "filter_L_exposure_sec",
    "filter_R_subs", "filter_R_exposure_sec",
    "filter_G_subs", "filter_G_exposure_sec",
    "filter_B_subs", "filter_B_exposure_sec",
    "filter_Ha_subs", "filter_Ha_exposure_sec",
    "filter_OIII_subs", "filter_OIII_exposure_sec",
    "filter_SII_subs", "filter_SII_exposure_sec",
}


def _session(db, **cols):
    user = db.query(DbUser).filter_by(username="default").one()
    session = JournalSession(user_id=user.id, date_utc=date(2026, 1, 10),
                             object_name="M42")
    for key, value in cols.items():
        setattr(session, key, value)
    db.add(session)
    sync_session_links(db, session)   # the object page finds sessions by UID
    db.commit()
    return session


def _page(client, session_id):
    return client.get(
        f"/graph_dashboard/M42?tab=journal&session_id={session_id}"
    ).get_data(as_text=True)


def _blob(html):
    start = html.index(_MARKER) + len(_MARKER)
    end = html.index(";\n    window.availableRigs", start)
    return json.loads(html[start:end])


def test_payload_field_set_is_exactly_the_contract(client, db_session):
    session = _session(db_session)
    assert set(_blob(_page(client, session.id))) == _FIELDS


def test_tuple_excludes_the_log_columns():
    assert not set(_LOG_COLUMNS) & set(GRAPH_SESSION_JS_FIELDS)


def test_no_log_data_reaches_the_page(client, db_session):
    session = _session(
        db_session,
        log_analysis_cache=json.dumps({"ZZ_CACHE_MARKER": 1}),
        asiair_log_content="asiair.log",
        phd2_log_content="phd2.log",
        nina_log_content="nina.log",
    )
    html = _page(client, session.id)
    blob = _blob(html)
    for column in _LOG_COLUMNS:
        assert column not in blob
    assert "ZZ_CACHE_MARKER" not in html


def test_values_are_the_session_values(client, db_session):
    session = _session(db_session, location_name="Default Test Loc",
                       gain_setting=100, number_of_subs_light=180,
                       notes="<p>Clear night</p>")
    blob = _blob(_page(client, session.id))
    assert blob["id"] == session.id
    assert blob["date_utc"] == "2026-01-10"
    assert blob["location_name"] == "Default Test Loc"
    assert blob["gain_setting"] == 100
    assert blob["number_of_subs_light"] == 180
    assert blob["notes"] == "<p>Clear night</p>"
    assert blob["rig_scale_snapshot"] is None


def test_server_render_still_uses_the_full_row(client, db_session):
    """dither_display is built server-side from the full dict and is not in the
    JS tuple: the view tab must still render it."""
    session = _session(db_session, dither_pixels=7, dither_every_n=3)
    assert "dither_display" not in GRAPH_SESSION_JS_FIELDS
    assert "7 px, every 3 subs" in _page(client, session.id)
