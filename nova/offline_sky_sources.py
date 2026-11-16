"""Single source of truth for the offline-sky survey whitelist.

The Framing Assistant's survey values live in static/js/graph_view_chart.js
(HIPS_ID_TO_URL) and static/js/framing-utils.js (SURVEY_FALLBACK, DSS_MIRRORS);
the same survey options are spelled out in templates/graph_view.html's
#survey-select and #blend-survey-select. This module is the *server* authority
that ties them together, so the /hips/ proxy and the JS cannot drift.

``survey_id`` is the exact value the frontend uses -- a CDS HiPS ID
(e.g. ``"P/DSS2/color"``) or an NSNS URL (e.g. ``"https://www.simg.de/nebulae3/
dr0_2/rgb8"``). ``slug`` is the safe path/disk segment for that survey under
/hips/<slug>. ``upstreams`` is the ordered list of base HiPS URLs to try (CDS
primary, alaskybis, then the DSS2-colour mirrors; NSNS is its own single-host
survey). Tiles from any upstream of a survey are identical, so they all land in
the same on-disk directory keyed by ``slug``.

``build_js_map()`` is what templates inject as ``window.NOVA_OFFLINE_SKY_SURVEYS``;
the frontend never hard-codes a local root, so adding/renaming a survey here is
enough to keep both sides in sync.
"""

from dataclasses import dataclass

# The two CDS hosts every HiPS survey is mirrored on.
_ALASKY = "https://alasky.cds.unistra.fr"
_ALASKYBIS = "https://alaskybis.cds.unistra.fr"

# DSS2 colour mirrors (same tiles, order 9, jpeg; see framingUtils.DSS_MIRRORS).
_DSS_COLOR_MIRRORS = (
    "https://skies.esac.esa.int/DSSColor",
    "https://casda.csiro.au/hips/DSS2/color",
)

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
        upstreams=(f"{_ALASKYBIS}/DSS/DSS2Merged",
                   f"{_ALASKY}/DSS/DSS2Merged"),
        max_order=9),
    "P/DSS2/blue": SurveySource(
        slug="dss2-blue", label="DSS2 (blue)",
        upstreams=(f"{_ALASKY}/DSS/DSS2-blue-XJ-S",),
        max_order=9),
    "P/allWISE/color": SurveySource(
        slug="allwise-color", label="AllWISE (color)",
        upstreams=(f"{_ALASKYBIS}/AllWISE/RGB-W4-W2-W1",
                   f"{_ALASKY}/AllWISE/RGB-W4-W2-W1"),
        max_order=9),
    "P/2MASS/color": SurveySource(
        slug="2mass-color", label="2MASS (color)",
        upstreams=(f"{_ALASKYBIS}/2MASS/Color",
                   f"{_ALASKY}/2MASS/Color"),
        max_order=9),
    "P/2MASS/H": SurveySource(
        slug="2mass-h", label="2MASS (H)",
        upstreams=(f"{_ALASKY}/2MASS/H",
                   f"{_ALASKYBIS}/2MASS/H"),
        max_order=9),
    "P/GALEXGR6/AIS/color": SurveySource(
        slug="galex-ais-color", label="GALEX (color)",
        upstreams=(f"{_ALASKY}/GALEX/GR6-03-2014/AIS-Color",),
        max_order=9),
    "P/PanSTARRS/DR1/color-z-zg-g": SurveySource(
        slug="panstarrs-dr1-color", label="Pan-STARRS (color)",
        upstreams=(f"{_ALASKY}/Pan-STARRS/DR1/color-z-zg-g",),
        max_order=9),
    "P/SDSS9/color": SurveySource(
        slug="sdss9-color", label="SDSS9 (color)",
        upstreams=(f"{_ALASKY}/SDSS/DR9/color",
                   f"{_ALASKYBIS}/SDSS/DR9/color"),
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


def build_js_map():
    """survey_id -> {root, maxOrder}, injected as window.NOVA_OFFLINE_SKY_SURVEYS."""
    return {
        sid: {"root": f"/hips/{src.slug}", "maxOrder": src.max_order}
        for sid, src in SURVEY_SOURCES.items()
    }
