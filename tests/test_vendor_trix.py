"""
The desktop and mobile base templates load Trix from static/vendor/trix/, not a
CDN, so the notes and journal editors work offline.
"""


def _assert_self_hosted_trix(html):
    assert "vendor/trix/trix.css" in html
    assert "vendor/trix/trix.umd.min.js" in html
    assert "unpkg.com" not in html


def test_desktop_base_loads_vendored_trix(su_client_logged_in):
    resp = su_client_logged_in.get("/")
    assert resp.status_code == 200
    _assert_self_hosted_trix(resp.get_data(as_text=True))


def test_mobile_base_loads_vendored_trix(su_client_logged_in):
    resp = su_client_logged_in.get("/m")
    assert resp.status_code == 200
    _assert_self_hosted_trix(resp.get_data(as_text=True))
