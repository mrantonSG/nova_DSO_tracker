"""API error responses carry a fixed message, never the exception's own text.

CodeQL py/stack-trace-exposure: /api/scan_frame, /api/delete_framing and
/api/create_object used to return str(e). Each handler is driven into its
except branch with an exception whose text is a recognisable marker, and the
response body must not contain it. Status codes are unchanged.
"""

from modules import simbad_service

SECRET = "secret-marker-7f3a"


def _body_text(resp):
    return resp.get_data(as_text=True)


def test_scan_frame_bad_parameters_hides_the_exception(client):
    # float("secret-marker-7f3a") -> ValueError whose text contains the marker
    resp = client.post('/api/scan_frame',
                       json={'ra': SECRET, 'dec': 0, 'fov_w': 1, 'fov_h': 1})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['status'] == 'error'
    assert body['message'] == "Invalid scan parameters."
    assert SECRET not in _body_text(resp)


def test_scan_frame_query_failure_hides_the_exception(client, monkeypatch):
    def _boom(adql, timeout=None, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(simbad_service, 'tap_csv', _boom)
    resp = client.post('/api/scan_frame',
                       json={'ra': 10.0, 'dec': 20.0, 'fov_w': 1.0, 'fov_h': 1.0})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['status'] == 'error'
    assert body['message'] == "An internal error occurred."
    assert SECRET not in _body_text(resp)


def test_delete_framing_failure_hides_the_exception(client, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr('nova.blueprints.api.SavedFraming', _Boom(_boom))
    resp = client.post('/api/delete_framing', json={'object_name': 'M42'})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body['status'] == 'error'
    assert body['message'] == "An internal error occurred."
    assert SECRET not in _body_text(resp)


def test_create_object_bad_coordinate_hides_the_exception(client):
    # _parse_float_from_request raises ValueError quoting the bad value
    resp = client.post('/api/create_object',
                       json={'name': 'Leak Test 1', 'ra': SECRET, 'dec': 10.0})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body['status'] == 'error'
    assert body['message'] == "Invalid RA/DEC format for object."
    assert SECRET not in _body_text(resp)


def test_create_object_failure_hides_the_exception(client, monkeypatch):
    def _boom(name):
        raise RuntimeError(SECRET)
    monkeypatch.setattr('nova.blueprints.api.normalize_object_name', _boom)
    resp = client.post('/api/create_object',
                       json={'name': 'Leak Test 2', 'ra': 5.0, 'dec': 10.0})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body['status'] == 'error'
    assert body['message'] == "An internal error occurred."
    assert SECRET not in _body_text(resp)


class _Boom:
    """Stand-in model class: any attribute access (e.g. a column used in a
    query filter) raises, which sends the route into its generic except."""

    def __init__(self, fn):
        self._fn = fn

    def __getattr__(self, name):
        self._fn()
