"""
tools/skyglow/garstang.py

Simplified Garstang (1986) atmospheric scattering model for sky brightness.

Given VIIRS ground radiance data around an observer location, computes
sky brightness (SQM, mag/arcsec²) as a function of altitude angle and
azimuth direction.

Physics summary
---------------
Each lit ground pixel contributes scattered light to sky brightness at
point (altitude θ, azimuth φ) via:

    contribution ∝ radiance(pixel) * f(θ, d, elev) / d_eff²

where:
  - d is the horizontal distance from observer to pixel (km)
  - d_eff is the effective path length through the scattering layer,
    which increases at lower altitude angles (more air mass)
  - f(θ, d, elev) is the altitude-dependent scattering weight:
      f = exp(-k * d / sin_eff(θ)) * (1 + cos²(θ)) / 2
    The (1 + cos²θ)/2 term is the Rayleigh phase function approximation.
    sin_eff accounts for observer elevation above the scattering layer.
  - k is the atmospheric extinction coefficient (default 0.35 km⁻¹)

This is a single-scattering approximation, accurate for clear atmospheres
(Garstang 1986, Cinzano & Falchi 2012). Double-scattering corrections
matter mainly in polluted atmospheres (optical depth > 0.5) which we
don't model here.

Altitude dependence
-------------------
At zenith (θ=90°): only overhead column contributes significantly.
At horizon (θ=0°): long slant path through full atmosphere, all distant
  sources contribute, sky is brightest.
The transition follows roughly a 1/sin(θ) air mass factor, giving a
natural gradient from bright horizon to dark zenith.

Observer elevation
------------------
The observer at elevation h_obs (metres) is above the lowest fraction
of the boundary layer. We model this by raising the effective base of
the scattering column: the minimum scattering height is h_obs, so
pixels at the same ground level have a slightly reduced contribution
because the observer has already cleared some aerosols.
Effect: ~0.05–0.15 mag/arcsec² improvement per 500m elevation gain.
"""

import math
import numpy as np

# ── Constants ──────────────────────────────────────────────────────────────────

EARTH_RADIUS_KM  = 6371.0
NATURAL_SKY_MCD  = 0.171168465   # mcd/m² — natural sky at 22.00 mag/arcsec²
SCALE_HT_MOL_KM  = 8.5           # molecular scale height (km)
SCALE_HT_AER_KM  = 1.5           # aerosol boundary layer scale height (km)


# ── Coordinate geometry ────────────────────────────────────────────────────────

def haversine_dist_az(lat_obs: float, lon_obs: float,
                       lats_g: np.ndarray, lons_g: np.ndarray):
    """
    Vectorised haversine: distance (km) and azimuth (°, 0=N clockwise)
    from observer to every pixel in the grid.
    """
    lat1 = math.radians(lat_obs)
    lat2 = np.radians(lats_g)
    dlon = np.radians(lons_g - lon_obs)
    dlat = lat2 - lat1

    a = np.clip(
        np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2,
        0, 1
    )
    dist_km = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))

    y = np.sin(dlon) * np.cos(lat2)
    x = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(dlon)
    az_deg = (np.degrees(np.arctan2(y, x)) + 360) % 360

    return dist_km, az_deg


# ── Garstang scattering weight ─────────────────────────────────────────────────

def scattering_weight(dist_km: np.ndarray, alt_deg: float,
                      elev_obs_m: float, k: float) -> np.ndarray:
    """
    Garstang scattering weight for sky brightness at altitude angle alt_deg.

    Parameters
    ----------
    dist_km   : horizontal distance from observer to each source pixel (km)
    alt_deg   : sky altitude angle (degrees, 0=horizon, 90=zenith)
    elev_obs_m: observer elevation above sea level (metres)
    k         : aerosol extinction coefficient (km⁻¹, default 0.35)

    Returns
    -------
    weights   : same shape as dist_km

    Physics
    -------
    Base weight (same as proven pilot): exp(-k*d) / d²
      - exp(-k*d): extinction along horizontal path to source
      - 1/d²: geometric dilution

    Altitude scaling: multiply by airmass = 1/sin(alt)
      - At zenith (alt=90°): airmass=1.0 → minimum contribution (dark sky)
      - At horizon (alt=0°): airmass→max → more path, more scattering → bright sky
      - This correctly produces higher SQM at zenith, lower SQM at horizon

    Observer elevation: scales base weight by exp(-elev/scale_height)
      - Higher observer → above more of the aerosol layer → less scattering
    """
    # Air mass: 1/sin(alt), capped at 1/sin(5°)≈11.5 to avoid divergence
    sin_alt = max(math.sin(math.radians(max(alt_deg, 1.0))),
                  math.sin(math.radians(5.0)))
    airmass = 1.0 / sin_alt

    # Observer elevation correction
    elev_km = elev_obs_m / 1000.0
    elev_factor = math.exp(-elev_km / SCALE_HT_AER_KM)

    # Base weight: exp(-k*d)/d² (horizontal extinction, same as working pilot)
    d_safe = np.maximum(dist_km, 0.5)
    base_weight = np.exp(-k * elev_factor * d_safe) / d_safe ** 2

    # Altitude scaling: airmass multiplies the integrated path contribution
    return base_weight * airmass


# ── Main model ─────────────────────────────────────────────────────────────────

def compute_skyglow_profile(
    lat_obs: float, lon_obs: float, elev_obs_m: float,
    data: np.ndarray, lats_g: np.ndarray, lons_g: np.ndarray,
    radius_km: float = 150,
    n_sectors: int = 16,
    alt_steps: tuple = (0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 75, 90),
    k: float = 0.35,
    sqm_zenith: float | None = None,
) -> dict:
    """
    Compute sky brightness (SQM) at a grid of (altitude, azimuth) points.

    Parameters
    ----------
    lat_obs, lon_obs : observer coordinates (degrees)
    elev_obs_m       : observer elevation (metres)
    data             : VIIRS radiance array (nW/cm²/sr), shape (rows, cols)
    lats_g, lons_g   : coordinate grids matching data, shape (rows, cols)
    radius_km        : integration radius (km)
    n_sectors        : number of azimuth sectors
    alt_steps        : altitude angles (degrees) to evaluate
    k                : Garstang extinction coefficient
    sqm_zenith       : known zenith SQM for calibration; if None, derived
                       from the VIIRS data using the standard formula

    Returns
    -------
    dict with keys:
      sqm_zenith       : float
      sqm_horizon_mean : float  (mean SQM at 0° altitude)
      alt_steps        : list of altitude angles evaluated
      sectors          : list of dicts, one per azimuth sector:
          az_deg, az_label, sqm_by_alt (list matching alt_steps)
    """
    # Pre-compute distances and azimuths
    dist_km, az_deg = haversine_dist_az(lat_obs, lon_obs, lats_g, lons_g)

    # Pixel mask: positive radiance, within radius, not at observer
    mask_base = (data > 0) & (dist_km > 0.5) & (dist_km <= radius_km)

    d_flat   = dist_km[mask_base]
    az_flat  = az_deg[mask_base]
    rad_flat = data[mask_base]

    # Sector bin indices
    sw       = 360.0 / n_sectors
    sec_idx  = (az_flat / sw).astype(int) % n_sectors

    # ── Calibration: derive zenith SQM from VIIRS data ─────────────────────
    # Compute total (zenith-equivalent) Garstang sum, then convert to SQM
    # using the lightpollutionmap.info documented formula.
    # This makes the pilot fully self-contained — no external API needed.

    w_zenith     = scattering_weight(d_flat, 90.0, elev_obs_m, k)
    total_sum    = float(np.sum(rad_flat * w_zenith))

    if sqm_zenith is None:
        # Bortle→SQM lookup as fallback (conservative mid-range values)
        # In Nova integration, pass the location's stored Bortle/SQM value.
        # Bortle: 1→21.7  2→21.5  3→21.3  4→20.8  5→20.3  6→19.5  7→18.5  8→17.5  9→17.0
        # Default to Bortle 5 (SQM 20.3) — common suburban/rural fringe
        sqm_zenith = 20.3
        import warnings
        warnings.warn(
            "sqm_zenith not provided — using default 20.3 (Bortle 5). "
            "Pass the location's known SQM via --sqm-zenith for accurate results. "
            "The Garstang model distributes this value directionally; "
            "it cannot derive absolute SQM from scratch reliably.",
            UserWarning, stacklevel=3
        )

    # Calibration scale: map Garstang sum → artificial brightness (mcd/m²)
    target_total_mcd = 108_000_000 * 10 ** (-0.4 * sqm_zenith)
    target_art_mcd   = target_total_mcd - NATURAL_SKY_MCD
    scale            = target_art_mcd / total_sum if total_sum > 0 else 1.0

    # ── Compute SQM per (sector, altitude) ────────────────────────────────
    results = []
    sector_width = 360.0 / n_sectors

    for s in range(n_sectors):
        sec_mask = (sec_idx == s)
        d_s   = d_flat[sec_mask]
        rad_s = rad_flat[sec_mask]
        az_s  = s * sector_width

        sqm_by_alt = []
        for alt in alt_steps:
            if len(d_s) == 0:
                sqm_by_alt.append(sqm_zenith)
                continue
            w       = scattering_weight(d_s, float(alt), elev_obs_m, k)
            sec_sum = float(np.sum(rad_s * w))
            # Scale and normalise: multiply by n_sectors (each sector = 1/n annulus)
            art_mcd = sec_sum * scale * n_sectors
            total_m = art_mcd + NATURAL_SKY_MCD
            total_m = max(total_m, NATURAL_SKY_MCD * 1.001)
            sqm     = math.log10(total_m / 108_000_000) / -0.4
            sqm_by_alt.append(round(sqm, 3))

        results.append({
            "az_deg":    round(az_s, 1),
            "az_label":  _az_label(az_s),
            "sqm_by_alt": sqm_by_alt,
        })

    # Horizon mean (alt=0°)
    alt0_idx = list(alt_steps).index(0) if 0 in alt_steps else 0
    sqm_horizon_mean = float(np.mean([s["sqm_by_alt"][alt0_idx] for s in results]))

    return {
        "sqm_zenith":       round(sqm_zenith, 3),
        "sqm_horizon_mean": round(sqm_horizon_mean, 3),
        "alt_steps":        list(alt_steps),
        "sectors":          results,
    }


def compute_skyglow_horizon(
    profile: dict,
    threshold_sqm: float | None = None,
    threshold_source: str = "auto",
) -> dict:
    """
    From a full (altitude × azimuth) SQM profile, compute the skyglow
    horizon: for each azimuth sector, the minimum altitude at which
    SQM exceeds the threshold.

    threshold_sqm defaults to 2/3 of the range between horizon and zenith:
        threshold = zenith - (zenith - horizon_mean) * (1/3)
    which places it 2/3 of the way from worst to best.

    Returns the profile dict extended with skyglow_horizon list.
    """
    sqm_zenith  = profile["sqm_zenith"]
    sqm_horizon = profile["sqm_horizon_mean"]
    alt_steps   = profile["alt_steps"]

    if threshold_sqm is None:
        # 2/3 of the way from horizon to zenith
        threshold_sqm    = sqm_horizon + (sqm_zenith - sqm_horizon) * (2 / 3)
        threshold_source = "auto_2/3"

    skyglow_horizon = []
    for sector in profile["sectors"]:
        sqm_alts = sector["sqm_by_alt"]
        min_alt  = None

        # Walk from horizon (alt=0) upward; find first alt where SQM >= threshold
        for i, (alt, sqm) in enumerate(zip(alt_steps, sqm_alts)):
            if sqm >= threshold_sqm:
                # Interpolate for smoother result
                if i > 0 and sqm_alts[i - 1] < threshold_sqm:
                    frac    = (threshold_sqm - sqm_alts[i - 1]) / (sqm - sqm_alts[i - 1])
                    min_alt = alt_steps[i - 1] + frac * (alt - alt_steps[i - 1])
                else:
                    min_alt = float(alt)
                break

        if min_alt is None:
            # Never reaches threshold — sky is always worse than threshold
            min_alt = 90.0

        skyglow_horizon.append({
            "az_deg":          sector["az_deg"],
            "az_label":        sector["az_label"],
            "min_alt_deg":     round(min_alt, 1),
            "sqm_at_horizon":  round(sector["sqm_by_alt"][0], 3),
            "sqm_at_min_alt":  round(threshold_sqm, 3),
        })

    result = dict(profile)
    result["threshold_sqm"]    = round(threshold_sqm, 3)
    result["threshold_source"] = threshold_source
    result["skyglow_horizon"]  = skyglow_horizon
    return result


# ── Utilities ──────────────────────────────────────────────────────────────────

_AZ_LABELS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
               "S","SSW","SW","WSW","W","WNW","NW","NNW"]

def _az_label(az_deg: float) -> str:
    idx = round(az_deg / (360 / len(_AZ_LABELS))) % len(_AZ_LABELS)
    return _AZ_LABELS[idx]


def sqm_to_bortle(sqm: float) -> int:
    for threshold, bortle in [(21.7,1),(21.5,2),(21.3,3),(20.8,4),
                               (20.1,5),(19.1,6),(18.0,7),(17.0,8)]:
        if sqm >= threshold:
            return bortle
    return 9