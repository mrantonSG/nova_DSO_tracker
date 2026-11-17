"""What each whitelisted survey actually offers, read from its own metadata.

The whitelist in nova/offline_sky_sources.py says which upstreams may be
contacted and gives an *upper bound* on the order (``SurveySource.max_order``).
What a survey really serves comes from two files at its HiPS root, both cached
by the /hips/ proxy under CACHE_DIR/hips/<slug>/:

  * ``properties`` -- ``hips_order`` (deepest order), ``hips_order_min``,
    ``hips_tile_format``, ``hips_frame``.
  * ``Moc.fits``   -- the sky coverage, as a NUNIQ-ordered MOC (FITS binary
    table, IVOA MOC 1.x/2.0). Read with astropy.io.fits (already a dependency);
    there is no MOC library (mocpy) in Nova and none is needed for this.

``survey_facts(slug)`` and ``survey_coverage(slug)`` read the cached file and,
when ``fetch=True`` (the default) and nothing is cached yet, fetch it once via
the proxy's own logic (``_serve_properties`` / ``_serve_moc``: same whitelist,
breaker, store). ``fetch=False`` never contacts upstream.

Fallbacks are explicit, never silent: no properties -> the whitelist values with
``properties_found=False``; no MOC (or one we cannot use) -> full sky with a
``reason`` saying why.

Results are memoised in-process keyed on the cached file's mtime, so re-reads
are free and a refreshed file is picked up. A miss (no file) is not memoised.
"""

import os
import threading
from dataclasses import dataclass, field
from typing import Optional

from nova.offline_sky_sources import SURVEY_BY_SLUG
from nova.offline_sky_store import read_file, tile_path

PROPERTIES_FILE = "properties"
MOC_FILE = "Moc.fits"

# HiPS 1.0: the Allsky preview always lives in Norder3 (it is the order-3 tiles
# downsampled into one mosaic), whatever the survey's own order range.
ALLSKY_ORDER = 3

_FORMAT_TO_EXT = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}


def allsky_rel(ext: str) -> str:
    """Rel path of a survey's Allsky file under its HiPS root."""
    return f"Norder{ALLSKY_ORDER}/Allsky.{ext}"


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SurveyFacts:
    slug: str
    whitelist_max_order: int
    properties_found: bool
    hips_order: Optional[int]          # as published; None when absent/unparseable
    hips_order_min: Optional[int]
    hips_tile_format: Optional[str]    # raw value, e.g. "jpeg" or "png jpeg"
    hips_frame: Optional[str]
    effective_max_order: int           # min(whitelist, hips_order)
    tile_ext: str                      # first published format we accept, else whitelist default


def parse_properties(raw: bytes) -> dict:
    """``key = value`` lines -> dict (first occurrence wins; ``#`` comments skipped)."""
    out = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        out.setdefault(key.strip(), value.strip())
    return out


def _int_or_none(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _pick_ext(fmt: Optional[str], accepted: tuple) -> Optional[str]:
    """First format in a (space-separated) hips_tile_format that we accept."""
    if not fmt:
        return None
    for token in fmt.replace(",", " ").lower().split():
        ext = _FORMAT_TO_EXT.get(token)
        if ext and ext in accepted:
            return ext
    return None


def facts_from_properties(slug: str, raw: Optional[bytes]) -> SurveyFacts:
    src = SURVEY_BY_SLUG[slug]
    props = parse_properties(raw) if raw else {}
    hips_order = _int_or_none(props.get("hips_order"))
    effective = src.max_order if hips_order is None else max(0, min(src.max_order, hips_order))
    fmt = props.get("hips_tile_format")
    return SurveyFacts(
        slug=slug,
        whitelist_max_order=src.max_order,
        properties_found=raw is not None,
        hips_order=hips_order,
        hips_order_min=_int_or_none(props.get("hips_order_min")),
        hips_tile_format=fmt,
        hips_frame=props.get("hips_frame"),
        effective_max_order=effective,
        tile_ext=_pick_ext(fmt, src.exts) or src.exts[0],
    )


# ---------------------------------------------------------------------------
# MOC coverage
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Coverage:
    """Sky coverage of a survey at HEALPix NESTED tile granularity.

    ``cells[m]`` is the set of covered npix at MOC order m (as stored). A tile
    (order o, npix n) is in coverage when it overlaps any cell: either a cell at
    m <= o contains it, or a cell at m > o lies inside it. ``_touched[o]`` holds,
    for o <= the MOC's deepest order, every order-o pixel that contains a cell.
    """
    slug: str
    full_sky: bool
    reason: str                    # how this coverage was derived
    fraction: float                # covered fraction of the sphere, 0..1
    cells: dict = field(default_factory=dict, repr=False)
    _touched: dict = field(default_factory=dict, repr=False)

    def contains(self, order: int, npix: int) -> bool:
        if self.full_sky:
            return True
        touched = self._touched.get(order)
        if touched is not None and npix in touched:
            return True
        for m, pixels in self.cells.items():
            if m < order and (npix >> (2 * (order - m))) in pixels:
                return True
        return False


def _full_sky(slug: str, reason: str) -> Coverage:
    return Coverage(slug=slug, full_sky=True, reason=reason, fraction=1.0)


def _uniq_to_order_npix(uniq: int):
    order = (uniq.bit_length() - 3) // 2
    return order, uniq - 4 * (4 ** order)


def coverage_from_moc(slug: str, raw: Optional[bytes]) -> Coverage:
    """Parse a NUNIQ MOC.fits; anything unusable falls back to full sky."""
    if raw is None:
        return _full_sky(slug, "no MOC cached/available: assuming full sky")
    import io
    try:
        from astropy.io import fits
        with fits.open(io.BytesIO(raw), memmap=False) as hdul:
            if len(hdul) < 2 or hdul[1].data is None:
                return _full_sky(slug, "MOC has no table HDU: assuming full sky")
            hdr = hdul[1].header
            ordering = str(hdr.get("ORDERING", "NUNIQ")).strip().upper()
            if ordering != "NUNIQ":
                return _full_sky(slug, f"MOC ORDERING={ordering} unsupported: assuming full sky")
            coordsys = str(hdr.get("COORDSYS", "C")).strip().upper()
            if coordsys != "C":
                return _full_sky(slug, f"MOC COORDSYS={coordsys} unsupported: assuming full sky")
            uniqs = [int(u) for u in hdul[1].data.field(0)]
    except Exception as exc:  # unreadable FITS: don't let it break callers
        return _full_sky(slug, f"MOC unreadable ({type(exc).__name__}): assuming full sky")

    cells = {}
    for u in uniqs:
        if u < 4:
            continue
        m, n = _uniq_to_order_npix(u)
        cells.setdefault(m, set()).add(n)
    if not cells:
        return _full_sky(slug, "MOC is empty: assuming full sky")

    # Covered fraction: sum of cell areas, skipping any cell already inside a
    # coarser cell (a valid MOC has none, but don't double-count if it does).
    orders = sorted(cells)
    fraction = 0.0
    for m in orders:
        for n in cells[m]:
            if any((n >> (2 * (m - a))) in cells[a] for a in orders if a < m):
                continue
            fraction += 1.0 / (12 * 4 ** m)
    fraction = min(1.0, fraction)

    touched = {}
    for m, pixels in cells.items():
        for o in range(0, m + 1):
            shift = 2 * (m - o)
            touched.setdefault(o, set()).update(n >> shift for n in pixels)

    full = fraction >= 1.0 - 1e-12
    return Coverage(slug=slug, full_sky=full,
                    reason="MOC: full sky" if full else "MOC",
                    fraction=1.0 if full else fraction,
                    cells={} if full else cells,
                    _touched={} if full else touched)


# ---------------------------------------------------------------------------
# Cached readers (+ optional fetch through the proxy)
# ---------------------------------------------------------------------------

_memo_lock = threading.Lock()
_memo = {}  # (kind, slug) -> (mtime, value)


def _mtime(slug: str, rel: str):
    try:
        return os.path.getmtime(tile_path(slug, rel))
    except OSError:
        return None


def _ensure_cached(slug: str, rel: str) -> None:
    """Fetch ``rel`` through the /hips/ proxy logic when it is not on disk."""
    from nova.blueprints import offline_sky as bp
    try:
        if rel == PROPERTIES_FILE:
            bp._serve_properties(slug)
        elif rel == MOC_FILE:
            bp._serve_moc(slug)
    except Exception:
        pass


def _read(kind: str, slug: str, rel: str, fetch: bool, build):
    if fetch and _mtime(slug, rel) is None:
        _ensure_cached(slug, rel)
    mtime = _mtime(slug, rel)
    key = (kind, slug, tile_path(slug, rel))
    if mtime is not None:
        with _memo_lock:
            hit = _memo.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]
    value = build(slug, read_file(slug, rel) if mtime is not None else None)
    if mtime is not None:
        with _memo_lock:
            _memo[key] = (mtime, value)
    return value


def survey_facts(slug: str, fetch: bool = True) -> SurveyFacts:
    if slug not in SURVEY_BY_SLUG:
        raise KeyError(slug)
    return _read("facts", slug, PROPERTIES_FILE, fetch, facts_from_properties)


def effective_max_order(slug: str, fetch: bool = True) -> int:
    return survey_facts(slug, fetch).effective_max_order


def survey_coverage(slug: str, fetch: bool = True) -> Coverage:
    if slug not in SURVEY_BY_SLUG:
        raise KeyError(slug)
    facts = survey_facts(slug, fetch)
    frame = (facts.hips_frame or "equatorial").strip().lower()
    if frame not in ("equatorial", "icrs", "c"):
        # A MOC is always ICRS; tiles of a non-equatorial HiPS are numbered in
        # their own frame, so cell indices would not line up. None of the
        # whitelisted surveys is non-equatorial today.
        return _full_sky(slug, f"hips_frame={facts.hips_frame}: MOC not comparable, assuming full sky")
    return _read("moc", slug, MOC_FILE, fetch, coverage_from_moc)


def tile_in_coverage(slug: str, order: int, npix: int, fetch: bool = True) -> bool:
    return survey_coverage(slug, fetch).contains(order, npix)


def covered_sky_fraction(slug: str, fetch: bool = True) -> float:
    return survey_coverage(slug, fetch).fraction


def clear_memo() -> None:
    with _memo_lock:
        _memo.clear()
