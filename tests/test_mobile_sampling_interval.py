"""Mobile object detail when the stored sampling interval is null.

An imported config without sampling_interval_minutes is saved to UiPref as
null. mobile_object_detail must treat that as missing (15 min). The route
swallows calculation errors and still renders with fallback values
(transit_time "Error"), so the render context is checked, not only the status.
"""

import json

from flask import template_rendered

from nova import DbUser, UiPref, app
from nova.config import astro_context_cache


def test_mobile_object_detail_with_null_sampling_interval(su_client_logged_in, db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    prefs = {"altitude_threshold": 20, "sampling_interval_minutes": None}
    pref = db_session.query(UiPref).filter_by(user_id=user.id).one_or_none()
    if pref is None:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(prefs)))
    else:
        pref.json_blob = json.dumps(prefs)
    db_session.commit()
    astro_context_cache.clear()

    rendered = []

    def record(sender, template, context, **extra):
        rendered.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        resp = su_client_logged_in.get("/m/object/M42")
    finally:
        template_rendered.disconnect(record, app)

    assert resp.status_code == 200
    ctx = next(c for name, c in rendered if name == "mobile_object_detail.html")
    assert ctx["transit_time"] != "Error", "object calculation failed with a null sampling interval"
