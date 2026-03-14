"""
Nova DSO Tracker — Weather API Blueprint

REST endpoints for weather forecast data.
Prefix: /api/v1/weather  (set during blueprint registration)
"""

from flask import Blueprint, request, jsonify

from nova.api_auth import api_key_or_login_required
from nova.permissions import api_permission_required

weather_bp = Blueprint("weather", __name__)

# ──────────────────────────────────────────────────────────
#  Helpers (mirrored from rest_api.py for self-containment)
# ──────────────────────────────────────────────────────────


def _ok(data, meta=None, status=200):
    body = {"data": data}
    if meta is not None:
        body["meta"] = meta
    return jsonify(body), status


def _err(message, status=400):
    return jsonify({"error": message}), status


def _parse_lat_lon():
    """Parse and validate lat/lon from query string. Returns (lat, lon) or raises ValueError."""
    lat_str = request.args.get("lat")
    lon_str = request.args.get("lon")

    if lat_str is None or lon_str is None:
        raise ValueError("Missing required parameters: lat and lon")

    try:
        lat = float(lat_str)
        lon = float(lon_str)
    except (ValueError, TypeError):
        raise ValueError("Parameters lat and lon must be valid numbers")

    if not (-90.0 <= lat <= 90.0):
        raise ValueError("Parameter lat must be between -90 and 90")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError("Parameter lon must be between -180 and 180")

    return lat, lon


def _get_satellite_region(lat, lon):
    """Determine satellite image region from coordinates."""
    # Europe: lat 35-70, lon -10 to 40
    if 35 <= lat <= 70 and -10 <= lon <= 40:
        return "europe"
    # Americas: lat -55 to 70, lon -170 to -30
    if -55 <= lat <= 70 and -170 <= lon <= -30:
        return "americas"
    return "global"


def _build_satellite_urls(lat, lon, region):
    """Build satellite image URLs based on region."""
    if region == "europe":
        return {
            "region": "europe",
            "provider": "EUMETSAT",
            "urls": {
                "visible": (
                    "https://eumetview.eumetsat.int/static-images/MSG/IMAGERY/"
                    "EUROPE/RGB_NATURALENHNCD/FULLRESOLUTION/"
                ),
                "infrared": (
                    "https://eumetview.eumetsat.int/static-images/MSG/IMAGERY/"
                    "EUROPE/IR_108/FULLRESOLUTION/"
                ),
                "water_vapour": (
                    "https://eumetview.eumetsat.int/static-images/MSG/IMAGERY/"
                    "EUROPE/WV_062/FULLRESOLUTION/"
                ),
                "embed": (
                    f"https://embed.windy.com/embed2.html?lat={lat}&lon={lon}"
                    "&detailLat={lat}&detailLon={lon}&width=650&height=450"
                    "&zoom=5&level=surface&overlay=clouds&product=ecmwf"
                    "&menu=&message=&marker=&calendar=now&pressure=&type=map"
                    "&location=coordinates&detail=&metricWind=default"
                    "&metricTemp=default&radarRange=-1"
                ),
            },
        }
    elif region == "americas":
        return {
            "region": "americas",
            "provider": "GOES",
            "urls": {
                "visible": (
                    "https://cdn.star.nesdis.noaa.gov/GOES16/ABI/CONUS/GEOCOLOR/latest.jpg"
                ),
                "infrared": (
                    "https://cdn.star.nesdis.noaa.gov/GOES16/ABI/CONUS/11/latest.jpg"
                ),
                "water_vapour": (
                    "https://cdn.star.nesdis.noaa.gov/GOES16/ABI/CONUS/09/latest.jpg"
                ),
                "embed": (
                    f"https://embed.windy.com/embed2.html?lat={lat}&lon={lon}"
                    "&detailLat={lat}&detailLon={lon}&width=650&height=450"
                    "&zoom=4&level=surface&overlay=clouds&product=ecmwf"
                    "&menu=&message=&marker=&calendar=now&pressure=&type=map"
                    "&location=coordinates&detail=&metricWind=default"
                    "&metricTemp=default&radarRange=-1"
                ),
            },
        }
    else:
        return {
            "region": "global",
            "provider": "Windy",
            "urls": {
                "embed": (
                    f"https://embed.windy.com/embed2.html?lat={lat}&lon={lon}"
                    "&detailLat={lat}&detailLon={lon}&width=650&height=450"
                    "&zoom=4&level=surface&overlay=clouds&product=ecmwf"
                    "&menu=&message=&marker=&calendar=now&pressure=&type=map"
                    "&location=coordinates&detail=&metricWind=default"
                    "&metricTemp=default&radarRange=-1"
                ),
            },
        }


def _aggregate_daily(dataseries):
    """
    Aggregate hourly dataseries into daily summaries.

    Night-time window (18:00–06:00 next day) is used for seeing, cloud, and
    transparency averages — the hours that matter for astrophotography.

    Returns a list of dicts, one per calendar day, sorted by date.
    """
    from collections import defaultdict

    # Group blocks by calendar day (based on timepoint offset from init)
    # Each block has a 'timepoint' (hours from init) and an 'iso_time' if present,
    # or we derive the hour-of-day from timepoint % 24.
    days = defaultdict(list)

    for block in dataseries:
        tp = block.get("timepoint", 0)
        day_index = tp // 24  # day 0, 1, 2, …
        days[day_index].append(block)

    result = []
    for day_index in sorted(days.keys()):
        blocks = days[day_index]

        # Night-time blocks: hour-of-day >= 18 OR hour-of-day < 6
        night_blocks = [
            b
            for b in blocks
            if (b.get("timepoint", 0) % 24) >= 18 or (b.get("timepoint", 0) % 24) < 6
        ]
        # Fall back to all blocks if no night blocks available
        eval_blocks = night_blocks if night_blocks else blocks

        def _avg_valid(key, block_list):
            """Average of values that are not -9999."""
            vals = [b[key] for b in block_list if b.get(key) not in (None, -9999)]
            return round(sum(vals) / len(vals), 2) if vals else None

        def _avg_all(key, block_list):
            """Average of all non-None values."""
            vals = [b[key] for b in block_list if b.get(key) is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        result.append(
            {
                "day_index": day_index,
                "timepoint_start": blocks[0].get("timepoint"),
                "timepoint_end": blocks[-1].get("timepoint"),
                "night_cloudcover_avg": _avg_all("cloudcover", eval_blocks),
                "night_seeing_avg": _avg_valid("seeing", eval_blocks),
                "night_transparency_avg": _avg_valid("transparency", eval_blocks),
                "temp2m_avg": _avg_all("temp2m", blocks),
                "rh2m_avg": _avg_all("rh2m", blocks),
                "hourly_count": len(blocks),
                "night_hourly_count": len(eval_blocks),
            }
        )

    return result


# ──────────────────────────────────────────────────────────
#  Endpoints
# ──────────────────────────────────────────────────────────


@weather_bp.route("/hourly")
@api_key_or_login_required
@api_permission_required("dashboard.weather")
def get_hourly_forecast():
    """
    GET /api/v1/weather/hourly?lat={lat}&lon={lon}

    Returns the full 7-day hourly weather forecast for the given coordinates.
    Each entry in `dataseries` represents one hour.

    Fields per entry:
      - timepoint: hours from forecast init
      - cloudcover: 1 (clear) – 9 (overcast)
      - seeing: 1 (excellent) – 8 (bad), or -9999 if unavailable
      - transparency: 1 (excellent) – 8 (bad), or -9999 if unavailable
      - temp2m: temperature at 2 m (°C)
      - rh2m: relative humidity at 2 m (%)
    """
    try:
        lat, lon = _parse_lat_lon()
    except ValueError as exc:
        return _err(str(exc), 400)

    try:
        from nova import get_hybrid_weather_forecast

        forecast = get_hybrid_weather_forecast(lat, lon)
    except Exception as exc:
        return _err(f"Failed to fetch weather data: {exc}", 502)

    if forecast is None:
        return _err("Weather data unavailable for the requested location", 503)

    dataseries = forecast.get("dataseries", [])
    init = forecast.get("init", "")

    meta = {
        "lat": lat,
        "lon": lon,
        "init": init,
        "count": len(dataseries),
    }

    return _ok(dataseries, meta)


@weather_bp.route("/daily")
@api_key_or_login_required
@api_permission_required("dashboard.weather")
def get_daily_forecast():
    """
    GET /api/v1/weather/daily?lat={lat}&lon={lon}

    Returns a 7-day daily summary aggregated from hourly data.
    Night-time averages (18:00–06:00) are used for seeing, cloud cover, and
    transparency — the hours relevant for astrophotography.

    Fields per entry:
      - day_index: 0 = today, 1 = tomorrow, …
      - timepoint_start / timepoint_end: hour offsets bounding this day
      - night_cloudcover_avg: avg cloudcover (1–9) during night hours
      - night_seeing_avg: avg seeing (1–8) during night hours, null if unavailable
      - night_transparency_avg: avg transparency (1–8) during night hours, null if unavailable
      - temp2m_avg: avg temperature over the full day (°C)
      - rh2m_avg: avg relative humidity over the full day (%)
      - hourly_count: total hourly blocks for this day
      - night_hourly_count: night-time blocks used for astro averages
    """
    try:
        lat, lon = _parse_lat_lon()
    except ValueError as exc:
        return _err(str(exc), 400)

    try:
        from nova import get_hybrid_weather_forecast

        forecast = get_hybrid_weather_forecast(lat, lon)
    except Exception as exc:
        return _err(f"Failed to fetch weather data: {exc}", 502)

    if forecast is None:
        return _err("Weather data unavailable for the requested location", 503)

    dataseries = forecast.get("dataseries", [])
    init = forecast.get("init", "")

    daily = _aggregate_daily(dataseries)

    meta = {
        "lat": lat,
        "lon": lon,
        "init": init,
        "days": len(daily),
    }

    return _ok(daily, meta)


@weather_bp.route("/satellite")
@api_key_or_login_required
@api_permission_required("dashboard.weather")
def get_satellite_urls():
    """
    GET /api/v1/weather/satellite?lat={lat}&lon={lon}

    Returns satellite image URLs appropriate for the given location.

    Region detection:
      - Europe  (lat 35–70, lon -10–40):   EUMETSAT imagery
      - Americas (lat -55–70, lon -170–-30): GOES imagery
      - Fallback:                            Windy embed URL

    Response fields:
      - region: detected region name
      - provider: imagery provider name
      - urls: dict of available image/embed URLs
    """
    try:
        lat, lon = _parse_lat_lon()
    except ValueError as exc:
        return _err(str(exc), 400)

    region = _get_satellite_region(lat, lon)
    satellite_data = _build_satellite_urls(lat, lon, region)

    meta = {
        "lat": lat,
        "lon": lon,
    }

    return _ok(satellite_data, meta)
