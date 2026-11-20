"""Survey facts (properties + MOC) and the Norder3/Allsky path. No network.

Fixtures are tiny: a hand-written properties text and a MOC built in-test with
astropy.io.fits (one order-1 cell + one order-3 cell). Upstream fetches are
monkeypatched; files land in the per-test cache dir (conftest.isolated_cache_dir).
"""

import io

import pytest
from astropy.io import fits
import numpy as np

import nova.offline_sky_facts as facts
from nova.offline_sky_store import read_file, store_file

DSS = "dss2-color"
NSNS = "nsns-rgb8"


@pytest.fixture(autouse=True)
def _clear_memo():
    facts.clear_memo()
    yield
    facts.clear_memo()


def _server_on(monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "server")


def _no_upstream(monkeypatch):
    def fail(base, url):
        raise AssertionError(f"upstream contacted: {url}")
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fail)


def _props(order=5, fmt="png", frame="equatorial", order_min=0):
    return (
        f"hips_frame           = {frame}\n"
        f"hips_order           = {order}\n"
        f"hips_order_min       = {order_min}\n"
        f"hips_tile_format     = {fmt}\n"
        "hips_tile_width      = 512\n"
    ).encode("utf-8")


def _uniq(order, npix):
    return 4 * 4 ** order + npix


def _moc_bytes(cells, ordering="NUNIQ", coordsys="C"):
    """Minimal NUNIQ MOC.fits for [(order, npix), ...]."""
    col = fits.Column(name="UNIQ", format="1J",
                      array=np.array([_uniq(o, n) for o, n in cells], dtype=np.int32))
    hdu = fits.BinTableHDU.from_columns([col])
    hdu.header["PIXTYPE"] = "HEALPIX"
    hdu.header["ORDERING"] = ordering
    hdu.header["COORDSYS"] = coordsys
    hdu.header["MOCORDER"] = max(o for o, _ in cells)
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(buf)
    return buf.getvalue()


# One order-1 cell (npix 5) and one order-3 cell (npix 100).
_FIXTURE_CELLS = [(1, 5), (3, 100)]


# ---------------------------------------------------------------------------
# properties -> effective max order, format, frame
# ---------------------------------------------------------------------------

def test_effective_max_order_is_lower_of_whitelist_and_properties():
    store_file(NSNS, "properties", _props(order=5), count=False)
    f = facts.survey_facts(NSNS, fetch=False)
    assert f.properties_found
    assert f.whitelist_max_order == 9
    assert f.hips_order == 5
    assert f.hips_order_min == 0
    assert f.hips_frame == "equatorial"
    assert f.hips_tile_format == "png"
    assert f.tile_ext == "png"
    assert f.effective_max_order == 5


def test_whitelist_caps_a_deeper_survey():
    store_file(DSS, "properties", _props(order=11, fmt="jpeg"), count=False)
    f = facts.survey_facts(DSS, fetch=False)
    assert f.hips_order == 11
    assert f.effective_max_order == 9
    assert f.tile_ext == "jpg"


def test_multi_format_picks_first_accepted():
    store_file(DSS, "properties", _props(order=9, fmt="fits png jpeg"), count=False)
    assert facts.survey_facts(DSS, fetch=False).tile_ext == "png"


def test_no_properties_falls_back_to_whitelist_without_fetch(monkeypatch):
    _no_upstream(monkeypatch)
    f = facts.survey_facts(NSNS, fetch=False)
    assert not f.properties_found
    assert f.hips_order is None
    assert f.effective_max_order == 9
    assert f.tile_ext == "jpg"


def test_missing_properties_fetched_through_proxy(monkeypatch):
    calls = []

    def fake(base, url):
        calls.append(url)
        return _props(order=6) + b"hips_service_url = https://elsewhere.example\n"

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fake)
    f = facts.survey_facts(NSNS)
    assert calls == ["https://www.simg.de/nebulae3/dr0_2/rgb8/properties"]
    assert f.effective_max_order == 6
    # cached via the proxy logic, service URLs stripped as usual
    assert b"hips_service_url" not in read_file(NSNS, "properties")
    # second call is served from disk
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream",
                        lambda b, u: (_ for _ in ()).throw(AssertionError("refetch")))
    assert facts.effective_max_order(NSNS) == 6


def test_unknown_slug_raises():
    with pytest.raises(KeyError):
        facts.survey_facts("nope", fetch=False)


# ---------------------------------------------------------------------------
# MOC coverage
# ---------------------------------------------------------------------------

def _store_fixture_moc(slug=NSNS):
    store_file(slug, "properties", _props(order=6), count=False)
    store_file(slug, "Moc.fits", _moc_bytes(_FIXTURE_CELLS), count=False)


def test_tile_in_coverage_inside():
    _store_fixture_moc()
    inside = [
        (1, 5),                       # the cell itself
        (0, 1),                       # its parent (5 >> 2)
        (2, 20), (2, 23),             # its children
        (5, 5 * 256 + 17),            # a deep descendant
        (3, 100),                     # the order-3 cell
        (2, 25), (1, 6), (0, 1),      # ancestors of (3, 100)
        (4, 400), (4, 403),           # children of (3, 100)
    ]
    for order, npix in inside:
        assert facts.tile_in_coverage(NSNS, order, npix, fetch=False), (order, npix)


def test_tile_in_coverage_outside():
    _store_fixture_moc()
    outside = [(0, 0), (0, 11), (1, 4), (2, 24), (2, 26), (3, 101), (4, 404), (6, 0)]
    for order, npix in outside:
        assert not facts.tile_in_coverage(NSNS, order, npix, fetch=False), (order, npix)


def test_covered_fraction():
    _store_fixture_moc()
    cov = facts.survey_coverage(NSNS, fetch=False)
    assert not cov.full_sky
    assert cov.reason == "MOC"
    assert cov.fraction == pytest.approx(1 / 48 + 1 / 768)
    assert facts.covered_sky_fraction(NSNS, fetch=False) == pytest.approx(17 / 768)


def test_overlapping_cells_not_double_counted():
    store_file(NSNS, "Moc.fits", _moc_bytes([(0, 1), (1, 5), (2, 20)]), count=False)
    assert facts.covered_sky_fraction(NSNS, fetch=False) == pytest.approx(1 / 12)


def test_twelve_order0_cells_is_full_sky():
    store_file(DSS, "Moc.fits", _moc_bytes([(0, n) for n in range(12)]), count=False)
    cov = facts.survey_coverage(DSS, fetch=False)
    assert cov.full_sky and cov.fraction == 1.0
    assert cov.reason == "MOC: full sky"


def test_no_moc_is_full_sky_and_says_so(monkeypatch):
    _no_upstream(monkeypatch)
    cov = facts.survey_coverage(NSNS, fetch=False)
    assert cov.full_sky
    assert cov.fraction == 1.0
    assert "no MOC" in cov.reason
    assert facts.tile_in_coverage(NSNS, 7, 12345, fetch=False)


def test_no_moc_upstream_is_full_sky(monkeypatch):
    # properties exist upstream, Moc.fits does not (404 -> None)
    def fake(base, url):
        return _props(order=6) if url.endswith("/properties") else None
    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fake)
    cov = facts.survey_coverage(NSNS)
    assert cov.full_sky and "no MOC" in cov.reason
    assert read_file(NSNS, "Moc.fits") is None


def test_unreadable_moc_is_full_sky():
    store_file(NSNS, "Moc.fits", b"\x00SIMPLE  = garbage", count=False)
    cov = facts.survey_coverage(NSNS, fetch=False)
    assert cov.full_sky and "unreadable" in cov.reason


def test_range_ordering_unsupported_is_full_sky():
    store_file(NSNS, "Moc.fits", _moc_bytes(_FIXTURE_CELLS, ordering="RANGE"), count=False)
    cov = facts.survey_coverage(NSNS, fetch=False)
    assert cov.full_sky and "RANGE" in cov.reason


def test_non_equatorial_frame_is_full_sky():
    store_file(NSNS, "properties", _props(frame="galactic"), count=False)
    store_file(NSNS, "Moc.fits", _moc_bytes(_FIXTURE_CELLS), count=False)
    cov = facts.survey_coverage(NSNS, fetch=False)
    assert cov.full_sky and "galactic" in cov.reason


def test_moc_fetched_through_proxy(monkeypatch):
    moc = _moc_bytes(_FIXTURE_CELLS)
    calls = []

    def fake(base, url):
        calls.append(url)
        return _props(order=6) if url.endswith("/properties") else moc

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fake)
    assert not facts.tile_in_coverage(NSNS, 0, 0)
    assert any(u.endswith("/Moc.fits") for u in calls)
    assert read_file(NSNS, "Moc.fits") == moc


def test_refreshed_moc_is_picked_up():
    import os
    _store_fixture_moc()
    assert not facts.tile_in_coverage(NSNS, 0, 0, fetch=False)
    path = facts.tile_path(NSNS, "Moc.fits")
    with open(path, "wb") as fh:
        fh.write(_moc_bytes([(0, 0)]))
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 10))
    assert facts.tile_in_coverage(NSNS, 0, 0, fetch=False)


# ---------------------------------------------------------------------------
# Allsky lives in Norder3/
# ---------------------------------------------------------------------------

def test_allsky_rel_is_norder3():
    assert facts.allsky_rel("jpg") == "Norder3/Allsky.jpg"


def test_base_rel_paths_use_norder3_allsky():
    import nova.offline_sky_jobs as jobs
    # base_rel_paths(slug, fetch=False): no second positional. Passing the extension
    # here landed in `fetch` and made this test hit the live upstreams.
    rels = jobs.base_rel_paths(DSS)
    assert rels[0] == "Norder3/Allsky.jpg"
    assert "Allsky.jpg" not in rels


def test_allsky_route_fetches_and_caches_norder3(client, monkeypatch):
    _server_on(monkeypatch)
    calls = []

    def fake(base, url):
        calls.append(url)
        return b"ALLSKY"

    monkeypatch.setattr("nova.blueprints.offline_sky._fetch_upstream", fake)
    resp = client.get("/hips/dss2-color/Norder3/Allsky.jpg")
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.get_data() == b"ALLSKY"
    assert calls == ["https://skies.esac.esa.int/DSSColor/Norder3/Allsky.jpg"]
    assert read_file(DSS, "Norder3/Allsky.jpg") == b"ALLSKY"

    _no_upstream(monkeypatch)
    again = client.get("/hips/dss2-color/Norder3/Allsky.jpg")
    assert again.status_code == 200 and again.get_data() == b"ALLSKY"
    offline = client.get("/hips/offline/dss2-color/Norder3/Allsky.jpg")
    assert offline.status_code == 200 and offline.get_data() == b"ALLSKY"


def test_offline_allsky_miss_is_404_without_upstream(client, monkeypatch):
    _server_on(monkeypatch)
    _no_upstream(monkeypatch)
    assert client.get("/hips/offline/nsns-rgb8/Norder3/Allsky.png").status_code == 404


def test_allsky_route_rejects_bad_ext_and_root_path(client, monkeypatch):
    _server_on(monkeypatch)
    _no_upstream(monkeypatch)
    assert client.get("/hips/dss2-color/Norder3/Allsky.fits").status_code == 404
    assert client.get("/hips/dss2-color/Allsky.jpg").status_code == 404
    assert client.get("/hips/offline/dss2-color/Allsky.jpg").status_code == 404


def test_allsky_route_404_when_store_off(client, monkeypatch):
    monkeypatch.setattr("nova.blueprints.offline_sky.OFFLINE_SKY_STORE", "off")
    _no_upstream(monkeypatch)
    assert client.get("/hips/dss2-color/Norder3/Allsky.jpg").status_code == 404
