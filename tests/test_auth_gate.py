"""Multi-user, logged-out, no guest_mode: the auth gate redirects pages to /login and 401s data endpoints."""
from urllib.parse import parse_qs, urlsplit

GATE_401 = {"error": "authentication required"}


def _login_redirect(response):
    """Assert a 302 to /login and return the parsed query dict."""
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.path == "/login"
    return parse_qs(location.query)


def _is_gate_401(response):
    return response.status_code == 401 and response.is_json and response.get_json() == GATE_401


def _is_login_redirect(response):
    return (response.status_code in (301, 302, 303, 307, 308)
            and urlsplit(response.headers.get("Location", "")).path == "/login")


def _assert_not_gated(response):
    assert not _is_login_redirect(response)
    assert not _is_gate_401(response)


def test_index_redirects_without_next(mu_client_logged_out):
    query = _login_redirect(mu_client_logged_out.get("/"))
    assert "next" not in query


def test_config_form_redirects_with_next(mu_client_logged_out):
    query = _login_redirect(mu_client_logged_out.get("/config_form"))
    assert query["next"] == ["/config_form"]


def test_graph_dashboard_next_keeps_query_string(mu_client_logged_out):
    query = _login_redirect(mu_client_logged_out.get("/graph_dashboard/M42?location=X"))
    assert query["next"] == ["/graph_dashboard/M42?location=X"]


def test_post_redirects_without_next(mu_client_logged_out):
    query = _login_redirect(mu_client_logged_out.post("/update_project"))
    assert "next" not in query


def test_data_endpoints_return_401_json(mu_client_logged_out):
    for path in ("/api/get_object_list", "/get_locations", "/sun_events"):
        response = mu_client_logged_out.get(path)
        assert response.status_code == 401, path
        assert response.get_json() == GATE_401, path


def test_login_page_not_gated(mu_client_logged_out):
    response = mu_client_logged_out.get("/login")
    assert response.status_code == 200


def test_service_worker_not_gated(mu_client_logged_out):
    response = mu_client_logged_out.get("/sw.js")
    assert response.status_code not in (301, 302, 303, 307, 308)
    _assert_not_gated(response)


def test_latest_version_not_gated(mu_client_logged_out):
    _assert_not_gated(mu_client_logged_out.get("/api/latest_version"))


def test_analytics_without_secret_is_403(mu_client_logged_out, monkeypatch):
    monkeypatch.setenv("ANALYTICS_SECRET", "test-analytics-secret")
    response = mu_client_logged_out.get("/analytics")
    assert response.status_code == 403
    _assert_not_gated(response)


def test_provision_user_without_key_reaches_endpoint(mu_client_logged_out, monkeypatch):
    monkeypatch.setenv("PROVISIONING_API_KEY", "test-provisioning-key")
    response = mu_client_logged_out.post("/api/internal/provision_user", json={})
    _assert_not_gated(response)
    assert response.is_json
    assert response.get_json().get("status") == "error"


# --- Guest flow ---------------------------------------------------------------

def _redirects_to_index(response):
    return (response.status_code in (301, 302, 303, 307, 308)
            and urlsplit(response.headers["Location"]).path == "/")


def test_guest_entry_sets_guest_mode(mu_client_logged_out):
    response = mu_client_logged_out.get("/guest")
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/"
    with mu_client_logged_out.session_transaction() as sess:
        assert sess.get("guest_mode") is True


def test_guest_can_reach_index_and_data(mu_client_logged_out):
    mu_client_logged_out.get("/guest")
    assert mu_client_logged_out.get("/").status_code == 200
    assert not _is_gate_401(mu_client_logged_out.get("/sun_events"))


def test_logout_clears_guest_mode(mu_client_logged_out):
    mu_client_logged_out.get("/guest")
    mu_client_logged_out.post("/logout")
    _login_redirect(mu_client_logged_out.get("/"))
    with mu_client_logged_out.session_transaction() as sess:
        assert "guest_mode" not in sess


def test_login_after_guest_clears_guest_mode(mu_client_logged_out):
    mu_client_logged_out.get("/guest")
    response = mu_client_logged_out.post(
        "/login", data={"username": "UserA", "password": "password123"})
    assert response.status_code == 303
    with mu_client_logged_out.session_transaction() as sess:
        assert "guest_mode" not in sess


def test_guest_entry_when_logged_in_does_not_set_guest_mode(multi_user_client):
    client, _ = multi_user_client
    response = client.get("/guest")
    assert response.status_code == 302
    assert urlsplit(response.headers["Location"]).path == "/"
    with client.session_transaction() as sess:
        assert "guest_mode" not in sess


def test_guest_entry_single_user_mode(su_client_logged_in):
    response = su_client_logged_in.get("/guest")
    assert _redirects_to_index(response)
    with su_client_logged_in.session_transaction() as sess:
        assert "guest_mode" not in sess
    response = su_client_logged_in.get("/")
    assert response.status_code == 200
    assert not _is_login_redirect(response)


# --- next round trip ----------------------------------------------------------

def test_next_round_trip_after_login(mu_client_logged_out):
    query = _login_redirect(mu_client_logged_out.get("/config_form"))
    next_page = query["next"][0]
    response = mu_client_logged_out.post(
        "/login", data={"username": "UserA", "password": "password123", "next": next_page})
    assert response.status_code == 303
    assert urlsplit(response.headers["Location"]).path == "/config_form"


def test_failed_login_preserves_next_in_form(mu_client_logged_out):
    response = mu_client_logged_out.post(
        "/login", data={"username": "UserA", "password": "wrong", "next": "/config_form"})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="next" value="/config_form"' in html


def test_login_rejects_protocol_relative_next(mu_client_logged_out):
    response = mu_client_logged_out.post(
        "/login", data={"username": "UserA", "password": "password123", "next": "//evil.com"})
    assert response.status_code == 303
    location = urlsplit(response.headers["Location"])
    assert location.path == "/"
    assert location.netloc in ("", "localhost")
    assert "evil.com" not in response.headers["Location"]
