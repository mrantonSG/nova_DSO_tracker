"""The Framing pre-flights against the /hips/ proxy, and tile 503s.

* Online (D2): ``/hips/<slug>/properties?preflight=1`` asks the upstreams instead
  of answering from a fresh disk copy -- 200 when one delivers, 503 when none did
  and at least one was unavailable, 404 when every upstream said "not there".
  Breaker and absence marks are respected.
* Tiles: a tile no upstream could deliver is a 503 when every upstream was
  unavailable, and stays a 404 when one said it does not exist.
* Offline view (D1): ``/hips/offline/<slug>/properties?ra=&dec=`` is 200 only
  when a tile at the frame centre is on disk, at any order up to the survey's max
  order; without ra/dec it is the plain properties file.

No network: requests' session.get is monkeypatched, so the real breaker and
absence-mark code runs. The store writes into the per-test cache dir.
"""

import math
import random

import pytest
import requests

import nova.hips_healpix as hp
from nova.offline_sky_store import store_file, read_file
from nova.offline_sky_sources import SURVEY_BY_SLUG

DSS = "dss2-color"          # four upstreams
HALPHA = "nsns-halpha8"     # one upstream


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


def _props(fmt="jpeg", order=9):
    return (f"hips_order={order}\nhips_tile_format={fmt}\nhips_frame=equatorial\n"
            "hips_service_url=https://alasky.cds.unistra.fr/DSS/DSSColor\n").encode()


@pytest.fixture
def bp(monkeypatch):
    from nova.blueprints import offline_sky
    monkeypatch.setattr(offline_sky, "OFFLINE_SKY_STORE", "server")
    from nova import offline_sky_facts
    offline_sky_facts.clear_memo()
    yield offline_sky
    offline_sky_facts.clear_memo()


def _upstreams(monkeypatch, bp, answers):
    """session.get answering per upstream base: 'down' (connection error), '5xx',
    '404', or bytes (a 200). Returns the list of requested URLs."""
    calls = []

    def get(url, **kw):
        calls.append(url)
        for base, answer in answers.items():
            if url.startswith(base.rstrip("/") + "/"):
                if answer == "down":
                    raise requests.ConnectionError("down")
                if answer == "5xx":
                    return _Resp(503)
                if answer == "404":
                    return _Resp(404)
                return _Resp(200, answer)
        raise AssertionError(f"unexpected upstream request {url}")

    monkeypatch.setattr(bp._session, "get", get)
    return calls


def _all(slug, answer):
    return {b: answer for b in SURVEY_BY_SLUG[slug].upstreams}


# ---------------------------------------------------------------------------
# D2: online properties pre-flight
# ---------------------------------------------------------------------------

def test_the_preflight_asks_the_upstreams_although_properties_are_fresh_on_disk(client, bp, monkeypatch):
    store_file(DSS, "properties", _props(), count=False)
    calls = _upstreams(monkeypatch, bp, _all(DSS, "down"))
    # Aladin's own request: still the disk copy, no upstream.
    assert client.get(f"/hips/{DSS}/properties").status_code == 200
    assert calls == []
    # The pre-flight: every upstream down -> 503.
    resp = client.get(f"/hips/{DSS}/properties?preflight=1")
    assert resp.status_code == 503
    assert len(calls) == len(SURVEY_BY_SLUG[DSS].upstreams)
    assert resp.headers.get("Cache-Control") == "no-store"


def test_the_preflight_is_200_when_a_later_upstream_answers(client, bp, monkeypatch):
    ups = SURVEY_BY_SLUG[DSS].upstreams
    answers = _all(DSS, "down")
    answers[ups[1]] = _props() + b"hips_service_url_2=https://elsewhere\n"
    _upstreams(monkeypatch, bp, answers)
    resp = client.get(f"/hips/{DSS}/properties?preflight=1")
    assert resp.status_code == 200
    assert b"hips_service_url" not in resp.data
    # stored for Aladin's own request, which follows
    assert read_file(DSS, "properties") == resp.data


def test_the_preflight_is_503_when_one_upstream_lacks_the_survey_and_the_rest_are_down(client, bp, monkeypatch):
    ups = SURVEY_BY_SLUG[DSS].upstreams
    answers = _all(DSS, "5xx")
    answers[ups[0]] = "404"
    _upstreams(monkeypatch, bp, answers)
    assert client.get(f"/hips/{DSS}/properties?preflight=1").status_code == 503


def test_the_preflight_is_404_when_every_upstream_says_the_survey_is_not_there(client, bp, monkeypatch):
    _upstreams(monkeypatch, bp, _all(DSS, "404"))
    assert client.get(f"/hips/{DSS}/properties?preflight=1").status_code == 404


def test_the_preflight_makes_no_request_to_a_base_whose_breaker_is_open(client, bp, monkeypatch):
    store_file(HALPHA, "properties", _props("png"), count=False)
    calls = _upstreams(monkeypatch, bp, _all(HALPHA, b"x"))
    for base in SURVEY_BY_SLUG[HALPHA].upstreams:
        bp._breaker_trip(base)
    assert client.get(f"/hips/{HALPHA}/properties?preflight=1").status_code == 503
    assert calls == []


def test_the_preflight_skips_an_absent_base_while_another_is_left(client, bp, monkeypatch):
    ups = SURVEY_BY_SLUG[DSS].upstreams
    bp._mark_absent(ups[0])
    calls = _upstreams(monkeypatch, bp, {**_all(DSS, "down"), ups[1]: _props()})
    assert client.get(f"/hips/{DSS}/properties?preflight=1").status_code == 200
    assert not any(u.startswith(ups[0]) for u in calls)


def test_the_preflight_asks_again_when_every_base_is_marked_absent(client, bp, monkeypatch):
    """Otherwise nothing could ever clear the marks: a /properties 200 is the only
    thing that does, and a failed pre-flight never lets Aladin's request through."""
    (base,) = SURVEY_BY_SLUG[HALPHA].upstreams
    bp._mark_absent(base)
    _upstreams(monkeypatch, bp, {base: _props("png")})
    assert client.get(f"/hips/{HALPHA}/properties?preflight=1").status_code == 200
    assert bp._base_absent(base) is False


# ---------------------------------------------------------------------------
# Tiles: 503 instead of 404 when every upstream was unavailable
# ---------------------------------------------------------------------------

TILE = "Norder3/Dir0/Npix5.jpg"


def test_a_tile_is_503_when_every_upstream_was_unavailable(client, bp, monkeypatch):
    answers = _all(DSS, "down")
    answers[SURVEY_BY_SLUG[DSS].upstreams[2]] = "5xx"
    _upstreams(monkeypatch, bp, answers)
    resp = client.get(f"/hips/{DSS}/{TILE}")
    assert resp.status_code == 503
    assert resp.headers.get("Cache-Control") == "no-store"


def test_a_tile_is_503_when_every_breaker_is_open(client, bp, monkeypatch):
    calls = _upstreams(monkeypatch, bp, _all(DSS, b"x"))
    for base in SURVEY_BY_SLUG[DSS].upstreams:
        bp._breaker_trip(base)
    assert client.get(f"/hips/{DSS}/{TILE}").status_code == 503
    assert calls == []


def test_a_tile_stays_404_when_an_upstream_says_it_does_not_exist(client, bp, monkeypatch):
    answers = _all(DSS, "down")
    answers[SURVEY_BY_SLUG[DSS].upstreams[1]] = "404"
    _upstreams(monkeypatch, bp, answers)
    assert client.get(f"/hips/{DSS}/{TILE}").status_code == 404


def test_a_tile_is_503_when_the_only_base_left_after_absent_ones_is_down(client, bp, monkeypatch):
    ups = SURVEY_BY_SLUG[DSS].upstreams
    for base in ups[:-1]:
        bp._mark_absent(base)
    calls = _upstreams(monkeypatch, bp, {ups[-1]: "down"})
    assert client.get(f"/hips/{DSS}/{TILE}").status_code == 503
    assert len(calls) == 1


def test_a_tile_is_404_when_every_base_is_absent(client, bp, monkeypatch):
    for base in SURVEY_BY_SLUG[DSS].upstreams:
        bp._mark_absent(base)
    calls = _upstreams(monkeypatch, bp, {})
    assert client.get(f"/hips/{DSS}/{TILE}").status_code == 404
    assert calls == []


# ---------------------------------------------------------------------------
# D1: Offline view pre-flight checks the tile at the frame centre
# ---------------------------------------------------------------------------

RA, DEC = 94.27, 22.52   # IC 443


def _tile_rel(order, ra, dec, ext):
    npix = hp.radec_to_nest(order, ra, dec)
    return f"Norder{order}/Dir{(npix // 10000) * 10000}/Npix{npix}.{ext}"


def _offline(client, slug, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return client.get(f"/hips/offline/{slug}/properties" + (f"?{qs}" if qs else ""))


def test_properties_alone_do_not_make_a_frame_stored_offline(client, bp, monkeypatch):
    store_file(HALPHA, "properties", _props("png"), count=False)
    _upstreams(monkeypatch, bp, {})                    # never upstream
    assert _offline(client, HALPHA).status_code == 200                       # Aladin's request
    assert _offline(client, HALPHA, ra=RA, dec=DEC).status_code == 404


def test_a_frame_centre_tile_makes_it_stored(client, bp, monkeypatch):
    store_file(HALPHA, "properties", _props("png"), count=False)
    store_file(HALPHA, _tile_rel(7, RA, DEC, "png"), b"tile")
    _upstreams(monkeypatch, bp, {})
    resp = _offline(client, HALPHA, ra=RA, dec=DEC)
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store"
    # Another frame is not covered by that tile.
    assert _offline(client, HALPHA, ra=RA + 30, dec=DEC).status_code == 404


@pytest.mark.parametrize("order", [0, 3, 6, 9])
def test_any_order_at_the_centre_counts(client, bp, monkeypatch, order):
    """Aladin shows the best order there is: a shallow tile is enough."""
    store_file(HALPHA, "properties", _props("png"), count=False)
    store_file(HALPHA, _tile_rel(order, RA, DEC, "png"), b"tile")
    _upstreams(monkeypatch, bp, {})
    assert _offline(client, HALPHA, ra=RA, dec=DEC).status_code == 200


def test_orders_above_the_surveys_max_order_are_not_looked_at(client, bp, monkeypatch):
    store_file(HALPHA, "properties", _props("png", order=6), count=False)
    store_file(HALPHA, _tile_rel(8, RA, DEC, "png"), b"tile")
    _upstreams(monkeypatch, bp, {})
    assert _offline(client, HALPHA, ra=RA, dec=DEC).status_code == 404


def test_a_tile_in_another_accepted_format_counts(client, bp, monkeypatch):
    store_file(DSS, "properties", _props("png jpeg"), count=False)
    store_file(DSS, _tile_rel(5, RA, DEC, "jpg"), b"tile")
    _upstreams(monkeypatch, bp, {})
    assert _offline(client, DSS, ra=RA, dec=DEC).status_code == 200


def test_a_centre_outside_the_survey_coverage_needs_no_tile(client, bp, monkeypatch):
    from nova import offline_sky_facts

    class _NoCoverage:
        def contains(self, order, npix):
            return False

    store_file(HALPHA, "properties", _props("png"), count=False)
    monkeypatch.setattr(offline_sky_facts, "survey_coverage", lambda slug, fetch=True: _NoCoverage())
    _upstreams(monkeypatch, bp, {})
    assert _offline(client, HALPHA, ra=RA, dec=-60).status_code == 200


def test_the_offline_preflight_still_needs_properties_on_disk(client, bp, monkeypatch):
    store_file(HALPHA, _tile_rel(7, RA, DEC, "png"), b"tile")
    _upstreams(monkeypatch, bp, {})
    assert _offline(client, HALPHA, ra=RA, dec=DEC).status_code == 404


def test_a_bad_centre_is_not_stored(client, bp, monkeypatch):
    store_file(HALPHA, "properties", _props("png"), count=False)
    _upstreams(monkeypatch, bp, {})
    assert _offline(client, HALPHA, ra="x", dec=DEC).status_code == 404
    assert _offline(client, HALPHA, ra=RA, dec=95).status_code == 404


# ---------------------------------------------------------------------------
# ang2pix (the frame-centre tile)
# ---------------------------------------------------------------------------

def test_ang2pix_nest_inverts_pix2ang_nest():
    rng = random.Random(7)
    for order in range(0, 12):
        nside = 1 << order
        for _ in range(400):
            pix = rng.randrange(12 * nside * nside)
            theta, phi = hp.pix2ang_nest(nside, pix)
            assert hp.ang2pix_nest(nside, theta, phi) == pix


def test_radec_to_nest_returns_the_tile_that_contains_the_point():
    rng = random.Random(11)
    for order in (0, 3, 6, 9):
        nside = 1 << order
        for _ in range(300):
            ra = rng.uniform(0, 360)
            dec = math.degrees(math.asin(rng.uniform(-1, 1)))
            pix = hp.radec_to_nest(order, ra, dec)
            centre = hp._pix_centre_vec(nside, pix)
            assert hp._angle(centre, hp._centre_vec(ra, dec)) <= hp.max_pixrad(nside) + 1e-12
            # and it is one of the cone's own pixels for a point-sized cone
            assert pix in hp.cone_pixels(ra, dec, 0.0, order, order)[order]


def test_radec_to_nest_face_layout():
    # Order 0 is the 12 base faces: 0..3 round the north pole, 4..7 on the
    # equator (face 4 centred on ra=0), 8..11 round the south pole.
    assert hp.radec_to_nest(0, 45, 89.99) == 0
    assert hp.radec_to_nest(0, 135, 89.99) == 1
    assert hp.radec_to_nest(0, 0, 0) == 4
    assert hp.radec_to_nest(0, 90, 0) == 5
    assert hp.radec_to_nest(0, 45, -89.99) == 8
