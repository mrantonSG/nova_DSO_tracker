"""Single source of truth for the offline-sky survey whitelist.

The Framing Assistant's survey values live in static/js/graph_view_chart.js
(HIPS_ID_TO_URL) and static/js/framing-utils.js (SURVEY_FALLBACK, DSS_MIRRORS);
the same survey options are spelled out in templates/graph_view.html's
#survey-select and #blend-survey-select. This module is the *server* authority
that ties them together, so the /hips/ proxy and the JS cannot drift.

``survey_id`` is the exact value the frontend uses -- a CDS HiPS ID
(e.g. ``"P/DSS2/color"``) or an NSNS URL (e.g. ``"https://www.simg.de/nebulae3/
dr0_2/rgb8"``). ``slug`` is the safe path/disk segment for that survey under
/hips/<slug>. ``upstreams`` is the ordered list of base HiPS URLs to try. CDS
comes first for every survey it hosts, so a healthy CDS is always preferred and
is used again as soon as it answers; the mirrors after it are registered copies
of the same survey (verified to serve the same tiles) and carry the load while
CDS does not. Tiles from any upstream of a survey are identical, so they all
land in the same on-disk directory keyed by ``slug``.

Mirror hosts, all listed on CDS's own HiPS availability page
(https://cds.unistra.fr/hips_available_2026*.html):

  * alasky / alaskybis -- CDS itself, the primary and its bis mirror
  * skies.esac.esa.int -- ESA ESASky
  * casda.csiro.au -- CSIRO CASDA
  * stpubdata.s3... -- MAST sky backgrounds (STScI)
  * hips.china-vo.org -- China-VO CDS mirror

``build_js_map()`` is what templates inject as ``window.NOVA_OFFLINE_SKY_SURVEYS``;
the frontend never hard-codes a local root, so adding/renaming a survey here is
enough to keep both sides in sync.
"""

from dataclasses import dataclass

# The two CDS hosts every HiPS survey is mirrored on.
_ALASKY = "https://alasky.cds.unistra.fr"
_ALASKYBIS = "https://alaskybis.cds.unistra.fr"

# Registered copies of the CDS surveys, used while CDS itself does not answer.
# DSS2 color mirrors (order 9, jpeg; see framingUtils.DSS_MIRRORS).
_DSS_COLOR_MIRRORS = (
    "https://skies.esac.esa.int/DSSColor",
    "https://casda.csiro.au/hips/DSS2/color",
)
_SKIES = "https://skies.esac.esa.int"
_CASDA = "https://casda.csiro.au/hips"
_CHINAVO = "https://hips.china-vo.org/m"
_MAST = "https://stpubdata.s3.us-east-1.amazonaws.com/mast/skybackgrounds"

# NSNS host + the six filter sub-paths offered in #survey-select.
_NSNS = "https://www.simg.de/nebulae3/dr0_2"


@dataclass(frozen=True)
class SurveySource:
    slug: str
    label: str
    upstreams: tuple
    max_order: int = 9
    # Tile extensions we accept (and pass through to the upstream as-is). No
    # whitelisted survey uses FITS, so it is deliberately absent.
    exts: tuple = ("jpg", "jpeg", "png", "webp")


# Ordered dict: survey_id -> SurveySource. Order is irrelevant to serving but is
# the canonical spelling of what the frontend offers.
SURVEY_SOURCES = {
    # --- CDS HiPS surveys (HIPS_ID_TO_URL) ---
    "P/DSS2/color": SurveySource(
        slug="dss2-color", label="DSS2 (color)",
        upstreams=(f"{_ALASKY}/DSS/DSSColor",
                   f"{_ALASKYBIS}/DSS/DSSColor") + _DSS_COLOR_MIRRORS,
        max_order=9),
    "P/DSS2/red": SurveySource(
        slug="dss2-red", label="DSS2 (red)",
        upstreams=(f"{_ALASKY}/DSS/DSS2Merged",
                   f"{_CHINAVO}/DSS2Merged"),
        max_order=9),
    "P/allWISE/color": SurveySource(
        slug="allwise-color", label="AllWISE (color)",
        upstreams=(f"{_ALASKY}/AllWISE/RGB-W4-W2-W1",
                   f"{_SKIES}/AllWISEColor",
                   f"{_CHINAVO}/CDS_P_allWISE_color"),
        max_order=9),
    "P/2MASS/color": SurveySource(
        slug="2mass-color", label="2MASS (color)",
        upstreams=(f"{_ALASKY}/2MASS/Color",
                   f"{_SKIES}/2MASSColor",
                   f"{_CASDA}/2MASS/Color",
                   f"{_CHINAVO}/CDS_P_2MASS_color"),
        max_order=9),
    "P/GALEXGR6/AIS/color": SurveySource(
        slug="galex-ais-color", label="GALEX (color)",
        upstreams=(f"{_ALASKY}/GALEX/GR6-03-2014/AIS-Color",
                   f"{_CHINAVO}/CDS_P_GALEXGR6_AIS_color",
                   f"{_MAST}/GALEXColor"),
        max_order=9),
    "P/PanSTARRS/DR1/color-z-zg-g": SurveySource(
        slug="panstarrs-dr1-color", label="Pan-STARRS (color)",
        upstreams=(f"{_ALASKY}/Pan-STARRS/DR1/color-z-zg-g",
                   f"{_MAST}/PanSTARRS"),
        max_order=9),
    "P/SDSS9/color": SurveySource(
        slug="sdss9-color", label="SDSS9 (color)",
        upstreams=(f"{_ALASKY}/SDSS/DR9/color",
                   f"{_SKIES}/SDSS9Color",
                   f"{_CHINAVO}/CDS_P_SDSS9_color"),
        max_order=9),

    # --- NSNS (SURVEY_FALLBACK.NSNS_* and the #survey-select options) ---
    "https://www.simg.de/nebulae3/dr0_2/rgb8": SurveySource(
        slug="nsns-rgb8", label="NSNS DR0.2 (True Color)",
        upstreams=(f"{_NSNS}/rgb8",), max_order=9),
    "https://www.simg.de/nebulae3/dr0_2/ohs8": SurveySource(
        slug="nsns-ohs8", label="NSNS DR0.2 (OHS)",
        upstreams=(f"{_NSNS}/ohs8",), max_order=9),
    "https://www.simg.de/nebulae3/dr0_2/hbr8": SurveySource(
        slug="nsns-hbr8", label="NSNS DR0.2 (HBR)",
        upstreams=(f"{_NSNS}/hbr8",), max_order=9),
    "https://www.simg.de/nebulae3/dr0_2/halpha8": SurveySource(
        slug="nsns-halpha8", label="NSNS DR0.2 (H-alpha)",
        upstreams=(f"{_NSNS}/halpha8",), max_order=9),
    "https://www.simg.de/nebulae3/dr0_2/oiii8": SurveySource(
        slug="nsns-oiii8", label="NSNS DR0.2 ([OIII])",
        upstreams=(f"{_NSNS}/oiii8",), max_order=9),
    "https://www.simg.de/nebulae3/dr0_2/sii8": SurveySource(
        slug="nsns-sii8", label="NSNS DR0.2 ([SII])",
        upstreams=(f"{_NSNS}/sii8",), max_order=9),
}

# slug -> SurveySource, for routing /hips/<slug>/...
SURVEY_BY_SLUG = {src.slug: src for src in SURVEY_SOURCES.values()}
# slug -> survey_id, the reverse map (the frontend's survey value).
SURVEY_ID_BY_SLUG = {src.slug: sid for sid, src in SURVEY_SOURCES.items()}


def resolve_slug(value):
    """Survey ID (``"P/DSS2/color"``, an NSNS URL) or slug -> slug, else None.

    The one place the offline-sky API normalises what a client sends; estimate,
    save, status, base and progress all go through it.
    """
    if value is None:
        return None
    key = str(value).strip()
    if not key:
        return None
    src = SURVEY_SOURCES.get(key)
    if src is not None:
        return src.slug
    return key if key in SURVEY_BY_SLUG else None


def resolve_slugs(values):
    """resolve_slug over a list (or comma-separated string); unknowns dropped,
    order kept, duplicates (an ID and its slug) collapsed."""
    if isinstance(values, str):
        values = values.split(",")
    out = []
    for v in values or []:
        slug = resolve_slug(v)
        if slug and slug not in out:
            out.append(slug)
    return out


def build_js_map():
    """survey_id -> {root, slug, maxOrder, label}, injected as window.NOVA_OFFLINE_SKY_SURVEYS.

    ``root`` is the same-origin /hips/ root (a relative path); the frontend turns
    it into an absolute URL before handing it to Aladin (see framingUtils.
    absoluteHipsRoot), and prefixes "offline/" for cache-only mode.

    ``maxOrder`` is the survey's *effective* max order (min of the whitelist and
    its published hips_order, from the cached properties; never fetched here).
    """
    from nova.offline_sky_facts import effective_max_order  # facts imports this module
    out = {}
    for sid, src in SURVEY_SOURCES.items():
        try:
            max_order = effective_max_order(src.slug, fetch=False)
        except Exception:
            max_order = src.max_order
        out[sid] = {"root": f"/hips/{src.slug}", "slug": src.slug,
                    "maxOrder": max_order, "label": src.label}
    return out
