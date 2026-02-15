import os
import re
import tempfile
import shutil
import yaml
import numpy as np
from datetime import datetime
from pathlib import Path

from flask import g

from nova.models import SessionLocal
from nova.config import (
    INSTANCE_PATH, BACKUP_DIR, ALLOWED_EXTENSIONS, SINGLE_USER_MODE
)

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


# === Data conversion helpers ===

def safe_float(value_str):
    """Safely converts a string to a float, returning None if empty or invalid."""
    if value_str is None or str(value_str).strip() == "":
        return None
    try:
        return float(value_str)
    except (ValueError, TypeError):
        print(f"Warning: Could not convert '{value_str}' to float.")
        return None


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
