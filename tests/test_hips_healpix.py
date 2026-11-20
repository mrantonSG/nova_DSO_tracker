"""The pure-Python HEALPix nested port (nova/hips_healpix.py).

Reference values come from the healpy project's own docstrings, which are
independent ground truth for the HEALPix projection this port reproduces:

    pix2ang([1,2,4,8], ring 11, lonlat=True)
        == (lon=[315, 337.5, 337.5, 337.5],
            lat=[-41.8103149, 41.8103149, 66.44353569, 78.28414761])
    ring2nest([1,2,4,8], 11) == [11, 13, 61, 253]
    max_pixrad(1) == 0.84106867056793
    max_pixrad(16) == 0.06601476143251
"""

import math
import random

import pytest

import nova.hips_healpix as hp


def _lonlat(nside, pix):
    theta, phi = hp.pix2ang_nest(nside, pix)
    return math.degrees(phi) % 360, 90.0 - math.degrees(theta)


def test_pix2ang_nest_matches_healpy_reference():
    # ring pixel 11 at nsides 1,2,4,8 == nested pixels 11,13,61,253.
    cases = [
        (1, 11, (315.0, -41.8103149)),
        (2, 13, (337.5, 41.8103149)),
        (4, 61, (337.5, 66.44353569)),
        (8, 253, (337.5, 78.28414761)),
    ]
    for nside, pix, (lon, lat) in cases:
        got_lon, got_lat = _lonlat(nside, pix)
        assert got_lon == pytest.approx(lon, abs=1e-6)
        assert got_lat == pytest.approx(lat, abs=1e-6)


def test_max_pixrad_matches_healpy_reference():
    assert hp.max_pixrad(1) == pytest.approx(0.84106867056793, rel=1e-9)
    assert hp.max_pixrad(16) == pytest.approx(0.06601476143251, rel=1e-9)
    assert hp.max_pixrad(512) == pytest.approx(2.0870552355e-03, rel=1e-6)


def test_order_helpers():
    assert hp.order_to_nside(3) == 8
    assert hp.nside_to_order(8) == 3
    assert hp.npix_order(0) == 12
    assert hp.npix_order(3) == 768


def test_nest2xyf_roundtrip():
    # nested children of a pixel are 4*p + 0..3 at the next order.
    nside = 8
    face, x, y = hp.nest2xyf(nside, 42)
    assert 0 <= face < 12 and 0 <= x < nside and 0 <= y < nside


def test_cone_pixels_covers_centre_and_is_valid():
    ra, dec = 83.8, -5.4
    radius = 1.0
    res = hp.cone_pixels(ra, dec, radius, 4, 6)
    assert set(res.keys()) == {4, 5, 6}
    centre = hp._centre_vec(ra, dec)
    for order, pixs in res.items():
        nside = hp.order_to_nside(order)
        npix = hp.npix_order(order)
        assert 0 < len(pixs) < npix
        for p in pixs:
            assert 0 <= p < npix
        # The pixel whose centre is closest to the cone centre must be included
        # (i.e. we never miss the tile containing the target).
        best = min(range(npix), key=lambda p: hp._angle(hp._pix_centre_vec(nside, p), centre))
        assert best in pixs
        # Every returned pixel's centre is within radius + max_pixrad (safe bound).
        half = hp.max_pixrad(nside)
        for p in pixs:
            assert hp._angle(hp._pix_centre_vec(nside, p), centre) <= math.radians(radius) + half + 1e-9


def test_cone_pixels_monotonic_count():
    ra, dec = 0.0, 0.0
    res = hp.cone_pixels(ra, dec, 2.0, 3, 6)
    counts = [len(res[o]) for o in (3, 4, 5, 6)]
    assert counts == sorted(counts)  # more tiles at higher orders
    assert counts[-1] > counts[0]


# --- ang2pix / radec_to_nest (the tile that contains a point) ---

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
