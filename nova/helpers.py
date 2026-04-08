import os
import re
import json
import uuid
import logging
import tempfile
import shutil
import traceback
import time
import yaml
import requests
import numpy as np
import pytz
from datetime import datetime, timedelta
from pathlib import Path
from math import atan, degrees
from typing import Optional

from flask import g, has_request_context, current_app, session, request
from sqlalchemy.orm import selectinload
from astroquery.simbad import Simbad
from astropy.coordinates import SkyCoord, get_constellation, EarthLocation, AltAz, get_body
from astropy.time import Time
import astropy.units as u

from nova.models import SessionLocal, Location, AstroObject, Component, SavedFraming
from nova.config import (
    INSTANCE_PATH, BACKUP_DIR, ALLOWED_EXTENSIONS, SINGLE_USER_MODE, SIMBAD_TIMEOUT,
    nightly_curves_cache, NOVA_CATALOG_URL, CATALOG_MANIFEST_CACHE, DEFAULT_HTTP_TIMEOUT
)
from modules.astro_calculations import (
    get_common_time_arrays, hms_to_hours, dms_to_degrees,
    calculate_transit_time, calculate_observable_duration_vectorized,
    ra_dec_to_alt_az, get_utc_time_for_local_11pm, interpolate_horizon
)

logger = logging.getLogger(__name__)

try:
    import fcntl
    _HAS_FCNTL = True
except Exception:
    _HAS_FCNTL = False


# === Core DB helper ===

def get_db():
    """
    Get a database session.

    Returns a thread-local scoped session. The session is automatically
    cleaned up at the end of each request context by the Flask
    @app.teardown_appcontext hook that calls SessionLocal.remove().

    No manual close() or remove() calls are needed in route handlers.
    """
    return SessionLocal()


def get_user_log_string(user_id, username):
    """Creates a privacy-aware but debuggable log string."""

    # This logic handles the None user_id case
    user_id_str = str(user_id) if user_id is not None else "None"

    # This logic handles the None/empty username case
    if not username or not str(username).strip():
        log_name = "unknown"
    else:
        # This logic handles the name hint
        try:
            username_clean = str(username).strip()  # Clean it
            parts = username_clean.split()
            if len(parts) > 1:
                # "Jane van der Beek" -> "Jane B."
                log_name = f"{parts[0]} {parts[-1][0]}."
            else:
                # "mrantonSG" -> "mrantonSG"
                log_name = username_clean
        except Exception:
            # Fallback for any weird names
            log_name = f"{username_clean[:5]}..."

    # Return with the parentheses
    return f"({user_id_str} | {log_name})"


# === File & YAML IO helpers ===

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _yaml_dump_pretty(data):
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _mkdirp(path):
    os.makedirs(path, exist_ok=True)
    return path


def _backup_with_rotation(src_path: str, keep: int = 10):
    try:
        _mkdirp(BACKUP_DIR)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        stem = Path(src_path).stem
        dst = os.path.join(BACKUP_DIR, f"{stem}_{ts}.yaml")
        if os.path.exists(src_path):
            shutil.copy2(src_path, dst)
        # prune old
        siblings = sorted([p for p in Path(BACKUP_DIR).glob(f"{stem}_*.yaml")],
                          key=lambda p: p.stat().st_mtime, reverse=True)
        for p in siblings[keep:]:
            try: p.unlink()
            except: pass
        return dst
    except Exception as e:
        print(f"[BACKUP] warning: {e}")


def _atomic_write_yaml(path: str, data: dict):
    dir_ = os.path.dirname(path)
    _mkdirp(dir_)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=dir_, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_yaml_dump_pretty(data))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


class _FileLock:
    """Simple advisory lock; no-ops if fcntl is unavailable."""
    def __init__(self, path: str):
        self.path = path
        self._fh = None
    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        except Exception:
            pass
        try:
            self._fh = open(self.path + ".lock", "a+")
            if _HAS_FCNTL:
                fcntl.flock(self._fh, fcntl.LOCK_EX)
        except Exception:
            pass
        return self
    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh:
                if _HAS_FCNTL:
                    fcntl.flock(self._fh, fcntl.LOCK_UN)
                self._fh.close()
        except Exception:
            pass


def to_yaml_filter(data):
    """Jinja2 filter to convert a Python object to a YAML string for form display."""
    if data is None:
        return ''
    try:
        # CORRECT: Force flow style AND provide a large width to prevent any wrapping.
        return yaml.dump(data, default_flow_style=True, width=9999, sort_keys=False).strip()
    except Exception:
        return ''


# === Log file storage helpers ===

LOGS_DIR = os.path.join(INSTANCE_PATH, 'logs')
ASIAIR_LOGS_DIR = os.path.join(LOGS_DIR, 'asiair')
PHD2_LOGS_DIR = os.path.join(LOGS_DIR, 'phd2')
NINA_LOGS_DIR = os.path.join(LOGS_DIR, 'nina')


def _ensure_log_dirs():
    """Ensure log directories exist."""
    os.makedirs(ASIAIR_LOGS_DIR, exist_ok=True)
    os.makedirs(PHD2_LOGS_DIR, exist_ok=True)
    os.makedirs(NINA_LOGS_DIR, exist_ok=True)


def save_log_to_filesystem(session_id: int, log_type: str, content: str, original_filename: str = None) -> str:
    """
    Save log content to filesystem and return the relative path.

    Args:
        session_id: The session ID
        log_type: 'asiair', 'phd2', or 'nina'
        content: The raw log content
        original_filename: Optional original filename for reference

    Returns:
        Relative path like 'instance/logs/asiair/278_Autorun_Log.txt'
    """
    _ensure_log_dirs()

    # Sanitize filename
    safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', original_filename or 'log.txt')
    if len(safe_name) > 100:
        safe_name = safe_name[:100]

    filename = f"{session_id}_{safe_name}"

    if log_type == 'asiair':
        filepath = os.path.join(ASIAIR_LOGS_DIR, filename)
    elif log_type == 'phd2':
        filepath = os.path.join(PHD2_LOGS_DIR, filename)
    elif log_type == 'nina':
        filepath = os.path.join(NINA_LOGS_DIR, filename)
    else:
        raise ValueError(f"Unknown log_type: {log_type}")

    with open(filepath, 'w', encoding='utf-8', errors='ignore') as f:
        f.write(content)

    # Return path relative to instance/
    return os.path.join('instance', 'logs', log_type, filename)


def read_log_content(db_value: str) -> str:
    """
    Read log content from either filesystem path or raw content.

    Args:
        db_value: Either a filesystem path or raw log content

    Returns:
        The raw log content
    """
    if not db_value:
        return None

    # Check if it's a filesystem path (no newlines = path, has newlines = raw content)
    if '\n' not in db_value and db_value.startswith('instance/logs/'):
        # It's a filesystem path - read from file
        # Convert relative path to absolute
        if db_value.startswith('instance/'):
            filepath = os.path.join(os.path.dirname(INSTANCE_PATH), db_value)
        else:
            filepath = db_value

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except FileNotFoundError:
            return None
    else:
        # It's raw content (legacy format)
        return db_value


def is_log_path(db_value: str) -> bool:
    """Check if the db_value is a filesystem path vs raw content."""
    if not db_value:
        return False
    return '\n' not in db_value and db_value.startswith('instance/logs/')


# === Data conversion helpers ===

import math

def calculate_dither_recommendation(
    main_pixel_size_um: float,
    main_focal_length_mm: float,
    guide_pixel_size_um: float,
    guide_focal_length_mm: float,
    desired_main_shift_px: int = 10
) -> dict:
    """
    Calculate dither pixel recommendation for guide-based capture systems (ASIAIR, etc.).

    The dither value in these systems is based on the guide camera's pixel scale,
    not the main camera. This function computes the recommended dither pixels
    to achieve a desired shift on the main sensor.

    Args:
        main_pixel_size_um: Main camera pixel size in micrometers
        main_focal_length_mm: Main telescope focal length in millimeters
        guide_pixel_size_um: Guide camera pixel size in micrometers
        guide_focal_length_mm: Guide scope focal length in millimeters
        desired_main_shift_px: Desired shift on main sensor in pixels (default 10)

    Returns:
        dict with:
            - main_scale_arcsec_px: Main camera plate scale
            - guide_scale_arcsec_px: Guide camera plate scale
            - ratio: Guide scale / main scale
            - raw_pixels: Unrounded dither pixels
            - recommended_pixels: Ceil-rounded dither pixels for ASIAIR
            - desired_main_shift_px: Input desired shift
        Returns None if any input is invalid (zero, negative, or None)
    """
    # Validate inputs - all must be positive non-zero values
    if not all(v is not None and v > 0 for v in [
        main_pixel_size_um, main_focal_length_mm,
        guide_pixel_size_um, guide_focal_length_mm
    ]):
        return None

    try:
        # Plate scale formula: (pixel_size_um / focal_length_mm) * 206.265 = arcsec/pixel
        main_scale = (main_pixel_size_um / main_focal_length_mm) * 206.265
        guide_scale = (guide_pixel_size_um / guide_focal_length_mm) * 206.265

        # Ratio of guide to main plate scale
        ratio = guide_scale / main_scale

        # Calculate raw pixels needed on guide camera
        raw_pixels = desired_main_shift_px / ratio

        # Round up for ASIAIR (conservative - ensures at least desired shift)
        recommended = math.ceil(raw_pixels)

        return {
            "main_scale_arcsec_px": round(main_scale, 2),
            "guide_scale_arcsec_px": round(guide_scale, 2),
            "ratio": round(ratio, 2),
            "raw_pixels": round(raw_pixels, 2),
            "recommended_pixels": recommended,
            "desired_main_shift_px": desired_main_shift_px,
        }
    except (ZeroDivisionError, ValueError, TypeError):
        return None


def safe_float(value_str):
    """Safely converts a string to a float, returning None if empty or invalid."""
    if value_str is None or str(value_str).strip() == "":
        return None
    try:
        return float(value_str)
    except (ValueError, TypeError):
        print(f"Warning: Could not convert '{value_str}' to float.")
        return None


def dither_display(session) -> str:
    """
    Returns a human-readable dither string.

    Uses structured fields if present, falls back to legacy dither_details.

    Args:
        session: A JournalSession instance

    Returns:
        A formatted dither string like "7 px, every 3 subs (disabled for Ha)"
        or the legacy dither_details value if structured fields are not set.
    """
    if session.dither_pixels is None:
        return session.dither_details or ''

    parts = [f"{session.dither_pixels} px"]

    if session.dither_every_n:
        if session.dither_every_n == 1:
            parts.append("every sub")
        else:
            parts.append(f"every {session.dither_every_n} subs")

    result = ', '.join(parts)

    if session.dither_notes:
        result += f" ({session.dither_notes})"

    return result


def safe_int(value_str):
    """Safely converts a string to an integer, returning None if empty or invalid."""
    if value_str is None or str(value_str).strip() == "":
        return None
    try:
        # Convert to float first to handle inputs like "10.0"
        return int(float(value_str))
    except (ValueError, TypeError):
        print(f"Warning: Could not convert '{value_str}' to int.")
        return None


def convert_to_native_python(val):
    """Converts a NumPy data type to a native Python type if necessary."""
    if isinstance(val, np.generic):
        return val.item()  # .item() is the key function here
    return val


def recursively_clean_numpy_types(data):
    """
    Recursively traverses a dict or list and converts any NumPy
    numeric types to native Python types. Needed before jsonify.
    """
    if isinstance(data, dict):
        return {key: recursively_clean_numpy_types(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [recursively_clean_numpy_types(item) for item in data]
    elif isinstance(data, np.generic):
        return data.item()
    return data


# === Settings helpers ===

def load_effective_settings():
    """
    Determines the effective settings for telemetry and calculation precision
    based on the application mode (single-user vs. multi-user).
    """
    if SINGLE_USER_MODE:
        # In single-user mode, read from the user's config file.
        g.sampling_interval = g.user_config.get('sampling_interval_minutes') or 15
        # --- START FIX ---
        # Handle case where 'telemetry' key exists but is None
        telemetry_config = g.user_config.get('telemetry') or {}
        g.telemetry_enabled = telemetry_config.get('enabled', True)
        # --- END FIX ---

    else:
        # In multi-user mode, read from the .env file with hardcoded defaults.
        g.sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))
        g.telemetry_enabled = os.environ.get('TELEMETRY_ENABLED', 'true').lower() == 'true'


def get_imaging_criteria():
    """
    Return normalized imaging criteria from the current user config (g.user_config)
    with safe defaults.
    """
    defaults = {
        "min_observable_minutes": 60,
        "min_max_altitude": 30,
        "max_moon_illumination": 20,
        "min_angular_separation": 30,
        "search_horizon_months": 6
    }
    try:
        cfg = getattr(g, 'user_config', {}) or {}
        raw = cfg.get("imaging_criteria") or {}
        out = dict(defaults) # Start with defaults

        if isinstance(raw, dict):
            def _update_key(key, cast_func):
                if key in raw and raw[key] is not None:
                    try:
                        out[key] = cast_func(str(raw[key]))
                    except (ValueError, TypeError):
                        pass # Keep default if parsing fails

            _update_key("min_observable_minutes", int)
            _update_key("min_max_altitude", float)
            _update_key("max_moon_illumination", int)
            _update_key("min_angular_separation", int)
            _update_key("search_horizon_months", int)

        # Clamp to sensible ranges
        out["min_observable_minutes"] = max(0, out.get("min_observable_minutes", 0))
        out["min_max_altitude"] = max(0.0, min(90.0, out.get("min_max_altitude", 0.0)))
        out["max_moon_illumination"] = max(0, min(100, out.get("max_moon_illumination", 100)))
        out["min_angular_separation"] = max(0, min(180, out.get("min_angular_separation", 0)))
        out["search_horizon_months"] = max(1, min(12, out.get("search_horizon_months", 1)))
        return out
    except Exception:
        return dict(defaults)


# === Session ID Generation ===

def generate_session_id():
    """Generates a unique session ID."""
    return uuid.uuid4().hex


# === Rig Metrics Computation ===

def _compute_rig_metrics_from_components(telescope: Optional["Component"],
                                          camera: Optional["Component"],
                                          reducer: Optional["Component"]):
    """
    Compute (effective_focal_length_mm, f_ratio, image_scale_arcsec_per_px, fov_w_arcmin)
    based on Component columns.
    telescope: uses focal_length_mm and aperture_mm
    camera: uses pixel_size_um and sensor_width_mm
    reducer: uses factor (e.g., 0.8 for reducer, 2.0 for extender)
    """
    try:
        if not telescope or not camera:
            return (None, None, None, None)
        fl = telescope.focal_length_mm
        ap = telescope.aperture_mm
        px = camera.pixel_size_um
        sw = camera.sensor_width_mm
        fac = reducer.factor if (reducer and reducer.factor is not None) else 1.0
        if fl is None or ap is None:
            return (None, None, None, None)
        efl = fl * fac if fl is not None else None
        f_ratio = (efl / ap) if (efl and ap) else None
        image_scale = (206.265 * px / efl) if (efl and px) else None
        fov_w_arcmin = (degrees(2 * atan((sw / 2.0) / efl)) * 60.0) if (sw and efl) else None
        return (efl, f_ratio, image_scale, fov_w_arcmin)
    except Exception as calc_err:
        logger.warning(f"[RIG METRICS] Failed to compute metrics for telescope={telescope.name if telescope else None}, camera={camera.name if camera else None}: {calc_err}")
        return (None, None, None, None)


# === Astro Context Loading ===

def load_full_astro_context():
    """
    Loads heavy astro data (locations, objects) into the global 'g' context.
    Assumes g.db_user and g.user_config are already populated.
    """
    # If this is already loaded for this request, don't do it again
    if hasattr(g, 'locations'):
        return

    # If there's no user, there's nothing to load
    if not hasattr(g, 'db_user') or not g.db_user:
        g.locations, g.active_locations, g.objects_list, g.objects_map = {}, {}, [], {}
        g.lat, g.lon, g.tz_name, g.selected_location = None, None, "UTC", None
        g.altitude_threshold = 20
        g.times_local, g.times_utc = [], []
        return

    db = get_db()
    try:
        # --- Load Locations with Horizon Points (Fixes N+1 query) ---
        loc_rows = db.query(Location).options(
            selectinload(Location.horizon_points)  # Eagerly load horizon points
        ).filter_by(user_id=g.db_user.id).all()

        g.locations = {}
        g.active_locations = {}
        default_loc_name = g.user_config.get("default_location")
        validated_location = default_loc_name

        for l in loc_rows:
            # The l.horizon_points access is now free, no new query
            mask = [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
            loc_data = {
                "name": l.name, "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                "altitude_threshold": l.altitude_threshold, "is_default": l.is_default,
                "horizon_mask": mask, "active": l.active, "comments": l.comments
            }
            g.locations[l.name] = loc_data
            if l.active:
                g.active_locations[l.name] = loc_data

        # Validate default location
        if not default_loc_name or default_loc_name not in g.active_locations:
            validated_location = next(iter(g.active_locations), None)

        g.selected_location = validated_location

        # Set safe defaults
        g.altitude_threshold = g.user_config.get("altitude_threshold", 20)
        if g.selected_location:
            loc_cfg = g.locations.get(g.selected_location, {})
            g.lat = loc_cfg.get("lat")
            g.lon = loc_cfg.get("lon")
            g.tz_name = loc_cfg.get("timezone", "UTC")
        else:
            g.lat, g.lon, g.tz_name = None, None, "UTC"

        # --- Load Objects ---
        obj_rows = db.query(AstroObject).filter_by(user_id=g.db_user.id).all()
        g.objects_list = []  # List for iteration
        g.objects_map = {}  # <<< NEW: Dictionary for fast lookups
        g.alternative_names = {}
        g.projects = {}
        g.objects = []

        for o in obj_rows:
            # Get all fields from our new method
            obj_data = o.to_dict()

            # Append to the list and map
            g.objects_list.append(obj_data)
            g.objects.append(o.object_name)
            if o.object_name:
                obj_key = o.object_name.lower()
                g.objects_map[obj_key] = obj_data  # <<< Add to map
                g.alternative_names[obj_key] = o.common_name
                g.projects[obj_key] = o.project_name

        # Add objects list to user_config dict for compatibility
        g.user_config["objects"] = g.objects_list
        g.user_config["locations"] = g.locations

        # --- Precompute time arrays ---
        if g.tz_name:
            local_tz = pytz.timezone(g.tz_name)
            local_date = datetime.now(local_tz).strftime('%Y-%m-%d')
            g.times_local, g.times_utc = get_common_time_arrays(g.tz_name, local_date, g.sampling_interval)
        else:
            g.times_local, g.times_utc = [], []

    except Exception as e:
        print(f"Error in load_full_astro_context: {e}")
        traceback.print_exc()


# === RA/DEC Lookup ===

def get_ra_dec(object_name, objects_map=None):
    """
    Looks up RA/DEC and other details for an object.
    Prioritizes the provided objects_map (if given), then falls back to g.objects_map (in request context),
    then queries SIMBAD.
    """
    obj_key = object_name.lower()

    # --- Use the provided map first, then g, then None ---
    obj_map_to_use = objects_map  # Use the one passed in (e.g., from the worker thread)
    if obj_map_to_use is None and has_request_context():
        # Fallback to g ONLY if in a request context and no map was passed
        obj_map_to_use = getattr(g, 'objects_map', None)

    obj_entry = obj_map_to_use.get(obj_key) if obj_map_to_use else None  # Use the determined map

    # --- Define defaults ---
    default_type = "N/A"
    default_magnitude = "N/A"
    default_size = "N/A"
    default_sb = "N/A"
    default_project = "none"
    default_constellation = "N/A"
    default_active_project = False

    # --- Path 1: Object found in config (using obj_map_to_use) ---
    if obj_entry:
        ra_str = obj_entry.get("RA")
        dec_str = obj_entry.get("DEC")
        constellation_val = obj_entry.get("Constellation", default_constellation)
        type_val = obj_entry.get("Type", default_type)
        magnitude_val = obj_entry.get("Magnitude", default_magnitude)
        size_val = obj_entry.get("Size", default_size)
        sb_val = obj_entry.get("SB", default_sb)
        project_val = obj_entry.get("Project", default_project)
        common_name_val = obj_entry.get("Name", object_name)  # Uses "Name" for config form compatibility
        active_project_val = obj_entry.get("ActiveProject", default_active_project)

        if ra_str is not None and dec_str is not None:
            try:
                ra_hours_float = float(ra_str)
                dec_degrees_float = float(dec_str)
                if constellation_val in [None, "N/A", ""]:
                    try:
                        coords = SkyCoord(ra=ra_hours_float * u.hourangle, dec=dec_degrees_float * u.deg)
                        constellation_val = get_constellation(coords, short_name=True)
                    except Exception:
                        constellation_val = "N/A"
                return {
                    "Object": object_name, "Constellation": constellation_val, "Common Name": common_name_val,
                    "RA (hours)": ra_hours_float, "DEC (degrees)": dec_degrees_float, "Project": project_val,
                    "Type": type_val or default_type, "Magnitude": magnitude_val or default_magnitude,
                    "Size": size_val or default_size, "SB": sb_val or default_sb, "ActiveProject": active_project_val
                }
            except ValueError:
                # Return error but keep other config data
                return {
                    "Object": object_name, "Constellation": "N/A", "Common Name": "Error: Invalid RA/DEC in config",
                    "RA (hours)": None, "DEC (degrees)": None, "Project": project_val, "Type": type_val,
                    "Magnitude": magnitude_val, "Size": size_val, "SB": sb_val, "ActiveProject": active_project_val
                }
        # Fall through to SIMBAD if coordinates missing in config

    # --- Path 2: Object not in config OR missing coords -> Query SIMBAD ---
    project_to_use = obj_entry.get("Project", default_project) if obj_entry else default_project
    active_project_to_use = obj_entry.get("ActiveProject", default_active_project) if obj_entry else default_active_project

    try:
        custom_simbad = Simbad()
        custom_simbad.ROW_LIMIT = 1
        custom_simbad.TIMEOUT = SIMBAD_TIMEOUT
        # Request standard coordinates.
        # Our parsing logic handles both Decimal (try block) and Sexagesimal (except block).
        custom_simbad.add_votable_fields('main_id', 'ra', 'dec', 'otype')

        result = custom_simbad.query_object(object_name)
        if result is None or len(result) == 0:
            raise ValueError(f"No results for '{object_name}' in SIMBAD.")

        # Find the columns (handling the rename to generic 'ra'/'dec')
        ra_col = next((c for c in result.colnames if c.lower() in ['ra', 'ra(d)', 'ra_d']), 'ra')
        dec_col = next((c for c in result.colnames if c.lower() in ['dec', 'dec(d)', 'dec_d']), 'dec')

        val_ra = result[ra_col][0]
        val_dec = result[dec_col][0]

        # --- CRITICAL FIX: FORCE DEGREE CONVERSION ---
        # Since we requested ra(d), any numeric result IS degrees.
        # We unconditionally divide numeric results by 15.0 to get hours.
        try:
            # Try to treat as pure decimal degrees
            ra_float = float(val_ra)
            ra_hours_simbad = ra_float / 15.0  # 14.75 deg / 15 = 0.98 hours
            dec_degrees_simbad = float(val_dec)  # Dec is already in degrees
        except (ValueError, TypeError):
            # If Simbad ignored us and sent a string (e.g. "00 59 01"), use the parser
            ra_hours_simbad = hms_to_hours(str(val_ra))
            dec_degrees_simbad = dms_to_degrees(str(val_dec))

        simbad_main_id = str(result['MAIN_ID'][0]) if 'MAIN_ID' in result.colnames else object_name
        try:
            coords = SkyCoord(ra=ra_hours_simbad * u.hourangle, dec=dec_degrees_simbad * u.deg)
            constellation_simbad = get_constellation(coords, short_name=True)
        except Exception:
            constellation_simbad = "N/A"

        return {
            "Object": object_name, "Constellation": constellation_simbad, "Common Name": simbad_main_id,
            "RA (hours)": ra_hours_simbad, "DEC (degrees)": dec_degrees_simbad, "Project": project_to_use,
            "Type": str(result['OTYPE'][0]) if 'OTYPE' in result.colnames else "N/A",
            "Magnitude": "N/A", "Size": "N/A", "SB": "N/A", "ActiveProject": active_project_to_use
        }
    except Exception as ex:
        return {
            "Object": object_name, "Constellation": "N/A",
            "Common Name": f"Error: SIMBAD lookup failed ({type(ex).__name__})",
            "RA (hours)": None, "DEC (degrees)": None, "Project": project_to_use, "Type": "N/A",
            "Magnitude": "N/A", "Size": "N/A", "SB": "N/A", "ActiveProject": active_project_to_use
        }


# === Object Name Normalization ===

def normalize_object_name(name: str) -> str:
    """
    Converts messy object names into a standard primary key.
    This function is designed to handle user input and convert it
    to the canonical format.
    """
    if not name: return None
    name_str = str(name).strip().upper()
    if not name_str: return None  # Catches whitespace-only input

    # --- 1. Fix known "corrupt" inputs (add spaces/hyphens) ---
    # This list should mirror the rules from the Python repair script.

    # SH 2-155 -> SH2155 (Fix: SH2 + 1 or more digits)
    match = re.match(r'^(SH2)(\d+)$', name_str)
    if match: return f"SH 2-{match.group(2)}"

    # SH 2-155 -> SH2-155 (Fix: SH2- + 1 or more digits)
    match = re.match(r'^(SH2)-(\d+)$', name_str)
    if match: return f"SH 2-{match.group(2)}"

    # NGC 1976 -> NGC1976 (Fix: NGC + 1 or more digits)
    match = re.match(r'^(NGC)(\d+)$', name_str)
    if match: return f"NGC {match.group(2)}"

    # IC 1805 -> IC1805 (Fix: IC + 1 or more digits)
    match = re.match(r'^(IC)(\d+)$', name_str)
    if match: return f"IC {match.group(2)}"

    # VDB 1 -> VDB1
    match = re.match(r'^(VDB)(\d+)$', name_str)
    if match: return f"VDB {match.group(2)}"

    # GUM 16 -> GUM16
    match = re.match(r'^(GUM)(\d+)$', name_str)
    if match: return f"GUM {match.group(2)}"

    # TGU H1867 -> TGUH1867
    match = re.match(r'^(TGUH)(\d+)$', name_str)
    if match: return f"TGU H{match.group(2)}"

    # LHA 120-N 70 -> LHA120N70
    # The regex now splits 'N' and '70' into separate groups
    match = re.match(r'^(LHA)(\d+)(N)(\d+)$', name_str)
    if match: return f"LHA {match.group(2)}-{match.group(3)} {match.group(4)}"

    # SNR G180.0-01.7 -> SNRG180.001.7
    # Made first decimal match non-greedy with +?
    match = re.match(r'^(SNRG)(\d+\.\d+?)(\d+\.\d+)$', name_str)
    if match: return f"SNR G{match.group(2)}-{match.group(3)}"

    # CTA 1 -> CTA1
    match = re.match(r'^(CTA)(\d+)$', name_str)
    if match: return f"CTA {match.group(2)}"

    # HB 3 -> HB3
    match = re.match(r'^(HB)(\d+)$', name_str)
    if match: return f"HB {match.group(2)}"

    # PN ARO 121 -> PNARO121
    match = re.match(r'^(PNARO)(\d+)$', name_str)
    if match: return f"PN ARO {match.group(2)}"

    # LIESTO 1 -> LIESTO1
    match = re.match(r'^(LIESTO)(\d+)$', name_str)
    if match: return f"LIESTO {match.group(2)}"

    # PK 081-14.1 -> PK08114.1
    match = re.match(r'^(PK)(\d+)(\d{2}\.\d+)$', name_str)
    if match: return f"PK {match.group(2)}-{match.group(3)}"

    # PN G093.3-02.4 -> PNG093.302.4
    # Made first decimal match non-greedy with +?
    match = re.match(r'^(PNG)(\d+\.\d+?)(\d+\.\d+)$', name_str)
    if match: return f"PN G{match.group(2)}-{match.group(3)}"

    # WR 134 -> WR134
    match = re.match(r'^(WR)(\d+)$', name_str)
    if match: return f"WR {match.group(2)}"

    # ABELL 21 -> ABELL21
    match = re.match(r'^(ABELL)(\d+)$', name_str)
    if match: return f"ABELL {match.group(2)}"

    # BARNARD 33 -> BARNARD33
    match = re.match(r'^(BARNARD)(\d+)$', name_str)
    if match: return f"BARNARD {match.group(2)}"

    # --- 2. Fix simple space removal (M, IC, etc.) ---
    # This rule handles user input like "M 42"
    match = re.match(r'^(M)\s+(.*)$', name_str)
    if match:
        prefix = match.group(1)
        number_part = match.group(2).replace(" ", "")
        return prefix + number_part

    # --- 3. Default Fallback ---
    # For names that are already correct (e.g., "M42", "NGC 1976", "SH 2-155")
    # just collapse whitespace.
    return " ".join(name_str.split())


# === Request Parsing Helpers ===

def _parse_float_from_request(value, field_name="field"):
    """Helper to convert request values to float, raising a clear ValueError."""
    if value is None:
        raise ValueError(f"{field_name} is required and cannot be empty.")
    try:
        return float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid non-numeric value '{value}' received for {field_name}.")


# === Rig Sorting Helpers ===

def sort_rigs(rigs, sort_key: str):
    """Sort rigs by various criteria with None-safe handling."""
    # FIX: Add a fallback for None to prevent the AttributeError
    if not sort_key:
        sort_key = 'name-asc'  # A sensible default if no preference is set

    key, _, direction = sort_key.partition('-')
    reverse = (direction == 'desc')

    def to_num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def getter(r):
        if key == 'name':
            return (r.get('rig_name') or '').lower()
        if key == 'fl':
            return to_num(r.get('effective_focal_length'))
        if key == 'fr':
            return to_num(r.get('f_ratio'))
        if key == 'scale':
            return to_num(r.get('image_scale'))
        if key == 'fovw':
            return to_num(r.get('fov_w_arcmin'))
        if key == 'recent':
            ts = r.get('updated_at') or r.get('created_at') or ''
            try:
                return datetime.fromisoformat(ts.replace('Z','+00:00'))
            except Exception:
                return r.get('rig_id') or ''
        # default to name
        return (r.get('rig_name') or '').lower()

    # sort with None-safe behavior (None values are sorted to the bottom)
    def none_safe(x):
        v = getter(x)
        return (v is None, v)

    return sorted(rigs, key=none_safe, reverse=reverse)


def get_locale():
    """
    Locale selector for Flask-Babel.
    Reads language preference from user config or session, falls back to browser preference or 'en'.
    """
    # Try user preference first (set by load_global_request_context)
    if hasattr(g, 'user_config') and g.user_config:
        user_lang = g.user_config.get('language')
        if user_lang and user_lang in current_app.config['BABEL_SUPPORTED_LOCALES']:
            return user_lang
    # Try session (for guest users)
    session_lang = session.get('language')
    if session_lang and session_lang in current_app.config['BABEL_SUPPORTED_LOCALES']:
        return session_lang
    # Fall back to browser preference
    browser_locale = request.accept_languages.best_match(current_app.config['BABEL_SUPPORTED_LOCALES'])
    if browser_locale:
        return browser_locale
    # Default
    return 'en'


def get_all_mobile_up_now_data(user, location, user_prefs_dict, objects_list, db=None):
    """
    Server-side function to get all data for the mobile 'Up Now' page in one pass.
    """
    # Pre-fetch framing status for the user
    framed_objects = set()
    if db:
        try:
            rows = db.query(SavedFraming.object_name).filter_by(user_id=user.id).all()
            framed_objects = {r[0] for r in rows}
        except Exception:
            pass

    # --- 1. Get Location & Time Details ---
    try:
        lat = location.lat
        lon = location.lon
        tz_name = location.timezone
        local_tz = pytz.timezone(tz_name)
    except Exception as e:
        print(f"[Mobile Helper] Error getting location details: {e}")
        return []  # Return empty on location error

    current_datetime_local = datetime.now(local_tz)

    # Determine "Observing Night" Date (Noon-to-Noon Logic)
    # Fixes bug where morning observations were snapping to the wrong day's noon
    if current_datetime_local.hour < 12:
        local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        local_date = current_datetime_local.strftime('%Y-%m-%d')

    # --- 2. Get Calculation Settings ---
    altitude_threshold = user_prefs_dict.get("altitude_threshold", 20)
    if location.altitude_threshold is not None:
        altitude_threshold = location.altitude_threshold

    sampling_interval = 15  # Default
    if SINGLE_USER_MODE:
        sampling_interval = user_prefs_dict.get('sampling_interval_minutes') or 15
    else:
        sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))

    horizon_mask = [[hp.az_deg, hp.alt_min_deg] for hp in sorted(location.horizon_points, key=lambda p: p.az_deg)]
    location_name_key = location.name.lower().replace(' ', '_')

    # --- 3. Pre-calculate Moon Position ---
    try:
        time_obj_now = Time(datetime.now(pytz.utc))
        location_for_moon = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        moon_coord = get_body('moon', time_obj_now, location_for_moon)
        frame_now = AltAz(obstime=time_obj_now, location=location_for_moon)
        moon_in_frame = moon_coord.transform_to(frame_now)
    except Exception:
        moon_in_frame = None  # Handle moon calc failure

    # --- 4. Loop Through All Objects ---
    all_objects_data = []

    for obj_record in objects_list:
        try:
            object_name = obj_record.object_name
            ra = obj_record.ra_hours
            dec = obj_record.dec_deg

            if ra is None or dec is None:
                continue  # Skip objects with no coordinates

            # --- 5. Get Nightly Cached Data ---
            cache_key = f"{user.username}_{object_name.lower().replace(' ', '_')}_{local_date}_{lat:.4f}_{lon:.4f}_{altitude_threshold}_{sampling_interval}"
            if cache_key not in nightly_curves_cache:
                # Cache miss - calculate it now
                times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)
                location_ephem = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
                sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                altaz_frame = AltAz(obstime=times_utc, location=location_ephem)
                altitudes = sky_coord.transform_to(altaz_frame).alt.deg
                azimuths = sky_coord.transform_to(altaz_frame).az.deg
                transit_time = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
                obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
                    ra, dec, lat, lon, local_date, tz_name, altitude_threshold, sampling_interval,
                    horizon_mask=horizon_mask
                )
                fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)
                alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)
                is_obstructed_at_11pm = False
                if horizon_mask and len(horizon_mask) > 1:
                    sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
                    required_altitude_11pm = interpolate_horizon(az_11pm, sorted_mask, altitude_threshold)
                    if alt_11pm >= altitude_threshold and alt_11pm < required_altitude_11pm:
                        is_obstructed_at_11pm = True

                nightly_curves_cache[cache_key] = {
                    "times_local": times_local, "altitudes": altitudes, "azimuths": azimuths,
                    "transit_time": transit_time,
                    "obs_duration_minutes": int(obs_duration.total_seconds() / 60) if obs_duration else 0,
                    "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
                    "alt_11pm": f"{alt_11pm:.2f}", "az_11pm": f"{az_11pm:.2f}",
                    "is_obstructed_at_11pm": is_obstructed_at_11pm
                }

            cached_night_data = nightly_curves_cache[cache_key]

            # --- 6. Calculate Current Position ---
            now_utc = datetime.now(pytz.utc)
            time_diffs = [abs((t - now_utc).total_seconds()) for t in cached_night_data["times_local"]]
            current_index = np.argmin(time_diffs)
            current_alt = cached_night_data["altitudes"][current_index]
            current_az = cached_night_data["azimuths"][current_index]
            next_index = min(current_index + 1, len(cached_night_data["altitudes"]) - 1)
            next_alt = cached_night_data["altitudes"][next_index]
            trend = '–'
            if abs(next_alt - current_alt) > 0.01: trend = '↑' if next_alt > current_alt else '↓'

            # --- 7. Calculate Moon Separation ---
            angular_sep = "N/A"
            if moon_in_frame:
                try:
                    obj_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    obj_in_frame = obj_coord_sky.transform_to(frame_now)
                    angular_sep = round(obj_in_frame.separation(moon_in_frame).deg)
                except Exception:
                    pass  # Keep N/A

            # --- 8. Assemble Final Dictionary ---
            all_objects_data.append({
                # Static data from the object record
                "Object": obj_record.object_name,
                "Common Name": obj_record.common_name or obj_record.object_name,
                "ActiveProject": obj_record.active_project,
                "has_framing": obj_record.object_name in framed_objects,

                # Calculated data
                'Altitude Current': f"{current_alt:.2f}",
                'Azimuth Current': f"{current_az:.2f}",
                'Trend': trend,
                'Observable Duration (min)': cached_night_data['obs_duration_minutes'],
                'Max Altitude (°)': cached_night_data['max_altitude'],
                'Angular Separation (°)': angular_sep,
                "Type": obj_record.type or "N/A",
                "Constellation": obj_record.constellation or "",
            })
        except Exception as e:
            print(f"[Mobile Helper] Failed to process object {obj_record.object_name}: {e}")
            continue  # Skip this object

    return all_objects_data


def enable_user(username: str) -> bool:
    """
    Re-enable a previously disabled user.
    Returns True if the user was found and enabled, False otherwise.
    """
    from nova.auth import db as auth_db, User
    with current_app.app_context():
        user = auth_db.session.scalar(auth_db.select(User).where(User.username == username))
        if not user:
            return False
        try:
            user.active = True
            auth_db.session.commit()
            print(f"✅ Enabled user '{username}'.")
            return True
        except Exception as e:
            auth_db.session.rollback()
            print(f"❌ Failed to enable user '{username}': {e}")
            return False


def disable_user(username: str) -> bool:
    """
    Mark a user as inactive/disabled without deleting them.
    Returns True if the user was found and disabled, False otherwise.
    """
    from nova.auth import db as auth_db, User
    with current_app.app_context():
        user = auth_db.session.scalar(auth_db.select(User).where(User.username == username))
        if not user:
            return False
        try:
            user.active = False
            auth_db.session.commit()
            print(f"✅ Disabled user '{username}'.")
            return True
        except Exception as e:
            auth_db.session.rollback()
            print(f"❌ Failed to disable user '{username}': {e}")
            return False


def delete_user(username: str) -> bool:
    """
    Hard-delete a user record. Optionally remove that user's on-disk files if you add that logic.
    """
    from nova.auth import db as auth_db, User
    with current_app.app_context():
        user = auth_db.session.scalar(auth_db.select(User).where(User.username == username))
        if not user:
            return False
        try:
            auth_db.session.delete(user)
            auth_db.session.commit()
            print(f"✅ Deleted user '{username}' from DB.")
            # If you also want to remove YAML/journal/config files, call your remover here.
            return True
        except Exception as e:
            auth_db.session.rollback()
            print(f"❌ Failed to delete user '{username}': {e}")
            return False


def _read_yaml(path: str) -> tuple[dict | None, str | None]:
    """
    Safely reads and parses a YAML file, returning data and any error.

    Returns:
        A tuple of (data, error_message).
        - On success: (dict, None)
        - If file not found: ({}, None) -> Non-fatal, treated as empty.
        - On parsing/other error: (None, str) -> Fatal error with a message.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            # Successfully parse the file. An empty file will correctly result in `None`.
            data = yaml.safe_load(f) or {}
            return (data, None)

    except FileNotFoundError:
        # This is a normal, non-fatal condition. The user just doesn't have this file.
        return ({}, None)

    except yaml.YAMLError as e:
        # This is a FATAL syntax error in the YAML file.
        # We return None for the data to signal a hard failure.
        error_msg = f"Invalid YAML syntax in '{os.path.basename(path)}': {e}"
        print(f"[MIGRATION] {error_msg}")
        return (None, error_msg)

    except Exception as e:
        # Catch any other unexpected errors during file reading.
        error_msg = f"Cannot read file '{os.path.basename(path)}': {e}"
        print(f"[MIGRATION] {error_msg}")
        return (None, error_msg)

def discover_catalog_packs() -> list[dict]:
    """Scan the central web repository for catalog packs."""
    global CATALOG_MANIFEST_CACHE
    now = time.time()

    # 1. Check if cache is valid
    if CATALOG_MANIFEST_CACHE["data"] is not None and now < CATALOG_MANIFEST_CACHE["expires"]:
        return CATALOG_MANIFEST_CACHE["data"]

    # --- THIS IS THE NEW LOGIC ---
    # Get the URL from the (possibly empty) config
    url_to_use = NOVA_CATALOG_URL
    if not url_to_use:
        # If it's not in the config, use the hardcoded default
        url_to_use = "https://catalogs.nova-tracker.com"
    # --- END NEW LOGIC ---

    # 2. Check if a URL is available (from either source)
    if not url_to_use:
        print("[CATALOG DISCOVER] No Catalog URL is configured. Catalog import is disabled.")
        return [] # Return empty list

    manifest_url = f"{url_to_use.rstrip('/')}/manifest.json"

    try:
        # 3. Fetch new manifest
        print(f"[CATALOG DISCOVER] Fetching new manifest from {manifest_url}")
        # Timeout reduced to 2.0s to prevent page load blocking if catalog server is slow/unreachable
        r = requests.get(manifest_url, timeout=DEFAULT_HTTP_TIMEOUT)
        r.raise_for_status()  # Raise error for bad status (404, 500)
        packs = r.json()

        if not isinstance(packs, list):
            print(f"[CATALOG DISCOVER] Error: Manifest is not a valid JSON list.")
            return []

        # 4. Update cache (e.g., for 1 hour)
        CATALOG_MANIFEST_CACHE = {
            "data": packs,
            "expires": now + 3600 # 1 hour cache
        }
        return packs

    except requests.exceptions.RequestException as e:
        print(f"[CATALOG DISCOVER] Failed to fetch manifest: {e}")
        return CATALOG_MANIFEST_CACHE["data"] or [] # Return old cache on error
    except json.JSONDecodeError as e:
        print(f"[CATALOG DISCOVER] Failed to parse manifest JSON: {e}")
        return CATALOG_MANIFEST_CACHE["data"] or []
    except Exception as e:
        print(f"[CATALOG DISCOVER] Error: {e}")
        return []
