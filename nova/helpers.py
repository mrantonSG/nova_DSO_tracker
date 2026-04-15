import os
import re
import tempfile
import shutil
import yaml
import uuid
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from flask import g, jsonify
from PIL import Image as _PILImage
from werkzeug.utils import secure_filename

from nova.models import SessionLocal
from nova.config import (
    INSTANCE_PATH,
    BACKUP_DIR,
    ALLOWED_EXTENSIONS,
    SINGLE_USER_MODE,
    BLOG_UPLOAD_FOLDER,
)

if TYPE_CHECKING:
    from nova.models import BlogImage

# === Blog image constants ===
try:
    _BLOG_LANCZOS = _PILImage.Resampling.LANCZOS
except AttributeError:
    _BLOG_LANCZOS = _PILImage.LANCZOS

BLOG_THUMB_MAX = (400, 400)
BLOG_THUMB_QUAL = 85
BLOG_COMMENT_MAX_LEN = 2000

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


def get_current_username() -> str:
    """Return 'default' in single-user mode, else current Flask-Login user's username."""
    from nova.config import SINGLE_USER_MODE
    from flask_login import current_user

    return "default" if SINGLE_USER_MODE else current_user.username


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
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _yaml_dump_pretty(data):
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _mkdirp(path):
    os.makedirs(path, exist_ok=True)
    return path


def _backup_with_rotation(src_path: str, keep: int = 10):
    try:
        _mkdirp(BACKUP_DIR)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = Path(src_path).stem
        dst = os.path.join(BACKUP_DIR, f"{stem}_{ts}.yaml")
        if os.path.exists(src_path):
            shutil.copy2(src_path, dst)
        # prune old
        siblings = sorted(
            [p for p in Path(BACKUP_DIR).glob(f"{stem}_*.yaml")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for p in siblings[keep:]:
            try:
                p.unlink()
            except OSError:
                pass
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
        return ""
    try:
        # CORRECT: Force flow style AND provide a large width to prevent any wrapping.
        return yaml.dump(
            data, default_flow_style=True, width=9999, sort_keys=False
        ).strip()
    except Exception:
        return ""


# === Log file storage helpers ===

LOGS_DIR = os.path.join(INSTANCE_PATH, "logs")
ASIAIR_LOGS_DIR = os.path.join(LOGS_DIR, "asiair")
PHD2_LOGS_DIR = os.path.join(LOGS_DIR, "phd2")


def _ensure_log_dirs():
    """Ensure log directories exist."""
    os.makedirs(ASIAIR_LOGS_DIR, exist_ok=True)
    os.makedirs(PHD2_LOGS_DIR, exist_ok=True)


def save_log_to_filesystem(
    session_id: int, log_type: str, content: str, original_filename: str = None
) -> str:
    """
    Save log content to filesystem and return the relative path.

    Args:
        session_id: The session ID
        log_type: 'asiair' or 'phd2'
        content: The raw log content
        original_filename: Optional original filename for reference

    Returns:
        Relative path like 'instance/logs/asiair/278_Autorun_Log.txt'
    """
    _ensure_log_dirs()

    # Sanitize filename
    safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", original_filename or "log.txt")
    if len(safe_name) > 100:
        safe_name = safe_name[:100]

    filename = f"{session_id}_{safe_name}"

    if log_type == "asiair":
        filepath = os.path.join(ASIAIR_LOGS_DIR, filename)
    else:
        filepath = os.path.join(PHD2_LOGS_DIR, filename)

    with open(filepath, "w", encoding="utf-8", errors="ignore") as f:
        f.write(content)

    # Return path relative to instance/
    return os.path.join("instance", "logs", log_type, filename)


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
    if "\n" not in db_value and db_value.startswith("instance/logs/"):
        # It's a filesystem path - read from file
        # Convert relative path to absolute
        if db_value.startswith("instance/"):
            filepath = os.path.join(os.path.dirname(INSTANCE_PATH), db_value)
        else:
            filepath = db_value

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except FileNotFoundError:
            return None
    else:
        # It's raw content (legacy format)
        return db_value


# === Data conversion helpers ===

import math


def calculate_dither_recommendation(
    main_pixel_size_um: float,
    main_focal_length_mm: float,
    guide_pixel_size_um: float,
    guide_focal_length_mm: float,
    desired_main_shift_px: int = 10,
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
    if not all(
        v is not None and v > 0
        for v in [
            main_pixel_size_um,
            main_focal_length_mm,
            guide_pixel_size_um,
            guide_focal_length_mm,
        ]
    ):
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
        return session.dither_details or ""

    parts = [f"{session.dither_pixels} px"]

    if session.dither_every_n:
        if session.dither_every_n == 1:
            parts.append("every sub")
        else:
            parts.append(f"every {session.dither_every_n} subs")

    result = ", ".join(parts)

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


# === Settings helpers ===


def load_effective_settings():
    """
    Determines the effective settings for telemetry and calculation precision
    based on the application mode (single-user vs. multi-user).
    """
    if SINGLE_USER_MODE:
        # In single-user mode, read from the user's config file.
        g.sampling_interval = g.user_config.get("sampling_interval_minutes") or 15
        # --- START FIX ---
        # Handle case where 'telemetry' key exists but is None
        telemetry_config = g.user_config.get("telemetry") or {}
        g.telemetry_enabled = telemetry_config.get("enabled", True)
        # --- END FIX ---

    else:
        # In multi-user mode, read from the .env file with hardcoded defaults.
        g.sampling_interval = int(os.environ.get("CALCULATION_PRECISION", 15))
        g.telemetry_enabled = (
            os.environ.get("TELEMETRY_ENABLED", "true").lower() == "true"
        )


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
        "search_horizon_months": 6,
    }
    try:
        cfg = getattr(g, "user_config", {}) or {}
        raw = cfg.get("imaging_criteria") or {}
        out = dict(defaults)  # Start with defaults

        if isinstance(raw, dict):

            def _update_key(key, cast_func):
                if key in raw and raw[key] is not None:
                    try:
                        out[key] = cast_func(str(raw[key]))
                    except (ValueError, TypeError):
                        pass  # Keep default if parsing fails

            _update_key("min_observable_minutes", int)
            _update_key("min_max_altitude", float)
            _update_key("max_moon_illumination", int)
            _update_key("min_angular_separation", int)
            _update_key("search_horizon_months", int)

        # Clamp to sensible ranges
        out["min_observable_minutes"] = max(0, out.get("min_observable_minutes", 0))
        out["min_max_altitude"] = max(0.0, min(90.0, out.get("min_max_altitude", 0.0)))
        out["max_moon_illumination"] = max(
            0, min(100, out.get("max_moon_illumination", 100))
        )
        out["min_angular_separation"] = max(
            0, min(180, out.get("min_angular_separation", 0))
        )
        out["search_horizon_months"] = max(
            1, min(12, out.get("search_horizon_months", 1))
        )
        return out
    except Exception:
        return dict(defaults)


# === Blog image helpers ===


def _save_blog_image(
    file, user_id: int, post_id: int, order: int, caption: str
) -> "BlogImage | None":
    """
    Validate, save, and thumbnail a blog image upload.

    Storage layout:
        instance/uploads/blog/<user_id>/blog_<uuid>.ext     (original)
        instance/uploads/blog/<user_id>/blog_thumb_<uuid>.jpg (≤400×400)

    Returns a BlogImage ORM object (not yet added to session) or None on failure.
    """
    from nova.models import BlogImage

    if not file or file.filename == "":
        return None
    # Sanitize filename for extension extraction
    safe_name = secure_filename(file.filename)
    if not safe_name or not allowed_file(safe_name):
        return None

    ext = safe_name.rsplit(".", 1)[1].lower()
    uid = uuid.uuid4().hex
    orig_name = f"blog_{uid}.{ext}"
    thumb_name = f"blog_thumb_{uid}.jpg"

    user_blog_dir = os.path.join(BLOG_UPLOAD_FOLDER, str(user_id))
    os.makedirs(user_blog_dir, exist_ok=True)

    orig_path = os.path.join(user_blog_dir, orig_name)
    thumb_path = os.path.join(user_blog_dir, thumb_name)

    try:
        file.save(orig_path)

        with _PILImage.open(orig_path) as img:
            # Normalise colour mode for JPEG output
            if img.mode in ("RGBA", "P", "LA"):
                background = _PILImage.new("RGB", img.size, (0, 0, 0))
                alpha = img.convert("RGBA").split()[-1]
                background.paste(img.convert("RGBA"), mask=alpha)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            img.thumbnail(BLOG_THUMB_MAX, resample=_BLOG_LANCZOS)
            img.save(
                thumb_path,
                format="JPEG",
                quality=BLOG_THUMB_QUAL,
                optimize=True,
                progressive=True,
            )

        return BlogImage(
            post_id=post_id,
            filename=orig_name,
            thumb_filename=thumb_name,
            caption=caption or "",
            display_order=order,
        )

    except Exception as e:
        print(f"[BLOG] Error saving blog image: {e}")
        # Clean up partial files
        for p in (orig_path, thumb_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return None


def _delete_blog_image_files(image: "BlogImage", user_id: int) -> None:
    """
    Delete the original and thumbnail files for a BlogImage from disk.
    Silently ignores missing files.
    """
    user_blog_dir = os.path.join(BLOG_UPLOAD_FOLDER, str(user_id))
    for filename in (image.filename, image.thumb_filename):
        if not filename:
            continue
        path = os.path.join(user_blog_dir, filename)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as e:
                print(f"[BLOG] Could not delete image file {path}: {e}")
