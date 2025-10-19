"""
Nova DSO Tracker
------------------------
This application provides endpoints to fetch and plot astronomical data
based on user-specific configuration details (e.g., locations and objects).
It uses Astroquery, Astropy, Ephem, and Matplotlib to calculate object altitudes,
transit times, and generate altitude curves for both celestial objects and the Moon.
It also integrates Flask-Login for user authentication.

March 2025, Anton Gutscher

"""

# =============================================================================
# Imports
# =============================================================================
import os
from decouple import config
from ics import Calendar, Event
import arrow
import requests
import secrets
from dotenv import load_dotenv
import calendar
import json
import numpy as np
from yaml.constructor import ConstructorError
import threading
import glob
from datetime import datetime, timedelta, timezone, UTC, date
import traceback
import io
import zipfile
import pytz
import ephem
import yaml
import shutil
import subprocess
import sys
import time
from modules.config_validation import validate_config
import uuid
from pathlib import Path
import platform

from math import atan, degrees


from flask import render_template, jsonify, request, send_file, redirect, url_for, flash, g, current_app
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from flask import session
from flask import Flask, send_from_directory, has_request_context

from astroquery.simbad import Simbad
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body, get_constellation
from astropy.time import Time
import astropy.units as u

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
import getpass
import jwt

from modules.astro_calculations import (
    calculate_transit_time,
    get_utc_time_for_local_11pm,
    hms_to_hours,
    dms_to_degrees,
    ra_dec_to_alt_az,
    get_common_time_arrays,
    calculate_sun_events_cached,
    calculate_observable_duration_vectorized,
    interpolate_horizon
)


from modules import nova_data_fetcher
from modules import rig_config

# === DB: Unified SQLAlchemy setup ============================================
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, Date,
    ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session

INSTANCE_PATH = globals().get("INSTANCE_PATH") or os.path.join(os.getcwd(), "instance")
os.makedirs(INSTANCE_PATH, exist_ok=True)
DB_PATH = os.path.join(INSTANCE_PATH, 'app.db')
DB_URI = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URI, echo=False, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
Base = declarative_base()

def get_or_create_db_user(db_session, username: str) -> 'DbUser':
    """
    Finds a user in the `DbUser` table by username or creates them if they don't exist.
    This is the bridge between the authentication DB and the application DB.

    Returns:
        The SQLAlchemy DbUser object for the given username.
    """
    if not username:
        return None

    # Try to find the user in our application database
    user = db_session.query(DbUser).filter_by(username=username).one_or_none()

    if user:
        # The user already exists, just return them
        return user
    else:
        # User exists in WordPress/users.db but not here. Create them now.
        print(f"[PROVISIONING] User '{username}' not found in app.db. Creating new record.")
        new_user = DbUser(username=username)
        db_session.add(new_user)
        try:
            db_session.commit()
            print(f"   -> Successfully provisioned '{username}' in app database.")
            # We need to re-fetch to get the fully loaded object with its new ID
            return db_session.query(DbUser).filter_by(username=username).one()
        except Exception as e:
            db_session.rollback()
            print(f"   -> FAILED to provision '{username}'. Error: {e}")
            return None

def get_db():
    """Use inside request context or background tasks."""
    return SessionLocal()

# --- MODELS ------------------------------------------------------------------
class DbUser(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    locations = relationship("Location", back_populates="user", cascade="all, delete-orphan")
    objects = relationship("AstroObject", back_populates="user", cascade="all, delete-orphan")
    components = relationship("Component", back_populates="user", cascade="all, delete-orphan")
    rigs = relationship("Rig", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("JournalSession", back_populates="user", cascade="all, delete-orphan")
    ui_prefs = relationship("UiPref", back_populates="user", uselist=False, cascade="all, delete-orphan")

class Project(Base):
    __tablename__ = 'projects'
    # The project_id from YAML will be our primary key. It's a string (UUID).
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    name = Column(String(256), nullable=False)

    user = relationship("DbUser")
    sessions = relationship("JournalSession", back_populates="project")

    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_user_project_name'),)



class Location(Base):
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    name = Column(String(128), nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    timezone = Column(String(64), nullable=False)
    altitude_threshold = Column(Float, nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    comments = Column(String(500), nullable=True)
    user = relationship("DbUser", back_populates="locations")
    horizon_points = relationship("HorizonPoint", back_populates="location", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_user_location_name'),)

class HorizonPoint(Base):
    __tablename__ = 'horizon_points'
    id = Column(Integer, primary_key=True)
    location_id = Column(Integer, ForeignKey('locations.id', ondelete="CASCADE"), index=True)
    az_deg = Column(Float, nullable=False)
    alt_min_deg = Column(Float, nullable=False)
    location = relationship("Location", back_populates="horizon_points")

class AstroObject(Base):
    __tablename__ = 'astro_objects'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    object_name = Column(String(256), nullable=False)
    common_name = Column(String(256), nullable=True)
    ra_hours = Column(Float, nullable=False)
    dec_deg = Column(Float, nullable=False)
    type = Column(String(128), nullable=True)
    constellation = Column(String(64), nullable=True)
    magnitude = Column(String(32), nullable=True)
    size = Column(String(64), nullable=True)
    sb = Column(String(64), nullable=True)
    active_project = Column(Boolean, nullable=False, default=False)
    project_name = Column(String(256), nullable=True)
    user = relationship("DbUser", back_populates="objects")
    __table_args__ = (UniqueConstraint('user_id', 'object_name', name='uq_user_object'),)

class Component(Base):
    __tablename__ = 'components'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    kind = Column(String(32), nullable=False)           # 'telescope' | 'camera' | 'reducer_extender'
    name = Column(String(256), nullable=False)
    aperture_mm = Column(Float, nullable=True)
    focal_length_mm = Column(Float, nullable=True)
    sensor_width_mm = Column(Float, nullable=True)
    sensor_height_mm = Column(Float, nullable=True)
    pixel_size_um = Column(Float, nullable=True)
    factor = Column(Float, nullable=True)
    user = relationship("DbUser", back_populates="components")
    rigs_using = relationship("Rig", back_populates="telescope", foreign_keys="Rig.telescope_id")

class Rig(Base):
    __tablename__ = 'rigs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    rig_name = Column(String(256), nullable=False)
    telescope_id = Column(Integer, ForeignKey('components.id', ondelete="SET NULL"), nullable=True)
    camera_id = Column(Integer, ForeignKey('components.id', ondelete="SET NULL"), nullable=True)
    reducer_extender_id = Column(Integer, ForeignKey('components.id', ondelete="SET NULL"), nullable=True)
    effective_focal_length = Column(Float, nullable=True)
    f_ratio = Column(Float, nullable=True)
    image_scale = Column(Float, nullable=True)
    fov_w_arcmin = Column(Float, nullable=True)
    user = relationship("DbUser", back_populates="rigs")
    telescope = relationship("Component", foreign_keys=[telescope_id])
    camera = relationship("Component", foreign_keys=[camera_id])
    reducer_extender = relationship("Component", foreign_keys=[reducer_extender_id])
    __table_args__ = (UniqueConstraint('user_id', 'rig_name', name='uq_user_rig_name'),)


class JournalSession(Base):
    __tablename__ = 'journal_sessions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    project_id = Column(String(64), ForeignKey('projects.id', ondelete="SET NULL"), nullable=True, index=True)
    date_utc = Column(Date, nullable=False)
    object_name = Column(String(256), nullable=True)
    notes = Column(Text, nullable=True)
    session_image_file = Column(String(256), nullable=True)

    # --- NEW & CORRECTED COLUMNS START HERE ---
    location_name = Column(String(128), nullable=True)
    seeing_observed_fwhm = Column(Float, nullable=True)
    sky_sqm_observed = Column(Float, nullable=True)
    moon_illumination_session = Column(Integer, nullable=True)
    moon_angular_separation_session = Column(Float, nullable=True)
    weather_notes = Column(Text, nullable=True)
    telescope_setup_notes = Column(Text, nullable=True)
    filter_used_session = Column(String(128), nullable=True)
    guiding_rms_avg_arcsec = Column(Float, nullable=True)
    guiding_equipment = Column(String(256), nullable=True)
    dither_details = Column(String(256), nullable=True)
    acquisition_software = Column(String(128), nullable=True)
    gain_setting = Column(Integer, nullable=True)
    offset_setting = Column(Integer, nullable=True)
    camera_temp_setpoint_c = Column(Float, nullable=True)
    camera_temp_actual_avg_c = Column(Float, nullable=True)
    binning_session = Column(String(16), nullable=True)
    darks_strategy = Column(Text, nullable=True)
    flats_strategy = Column(Text, nullable=True)
    bias_darkflats_strategy = Column(Text, nullable=True)
    session_rating_subjective = Column(Integer, nullable=True)
    transparency_observed_scale = Column(String(64), nullable=True)
    # --- END OF NEW COLUMNS ---

    number_of_subs_light = Column(Integer, nullable=True)
    exposure_time_per_sub_sec = Column(Integer, nullable=True)
    filter_L_subs = Column(Integer, nullable=True);
    filter_L_exposure_sec = Column(Integer, nullable=True)
    filter_R_subs = Column(Integer, nullable=True);
    filter_R_exposure_sec = Column(Integer, nullable=True)
    filter_G_subs = Column(Integer, nullable=True);
    filter_G_exposure_sec = Column(Integer, nullable=True)
    filter_B_subs = Column(Integer, nullable=True);
    filter_B_exposure_sec = Column(Integer, nullable=True)
    filter_Ha_subs = Column(Integer, nullable=True);
    filter_Ha_exposure_sec = Column(Integer, nullable=True)
    filter_OIII_subs = Column(Integer, nullable=True);
    filter_OIII_exposure_sec = Column(Integer, nullable=True)
    filter_SII_subs = Column(Integer, nullable=True);
    filter_SII_exposure_sec = Column(Integer, nullable=True)
    calculated_integration_time_minutes = Column(Float, nullable=True)
    external_id = Column(String(64), nullable=True, unique=True)  # Added unique constraint

    user = relationship("DbUser", back_populates="sessions")
    project = relationship("Project", back_populates="sessions")

class UiPref(Base):
    __tablename__ = 'ui_prefs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    json_blob = Column(Text, nullable=True)
    user = relationship("DbUser", back_populates="ui_prefs")

# --- Create tables if needed (non-destructive) -------------------------------
def ensure_db_initialized_unified():
    """
    Create tables if missing, ensure schema patches (external_id column),
    and set SQLite pragmas before any queries or migrations run.
    """
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        # Ensure external_id exists on journal_sessions
        cols = conn.exec_driver_sql("PRAGMA table_info(journal_sessions);").fetchall()
        colnames = {row[1] for row in cols}  # (cid, name, type, notnull, dflt_value, pk)
        if "external_id" not in colnames:
            conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN external_id TEXT;")
            print("[DB PATCH] Added missing column journal_sessions.external_id")
            try:
                conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_external_id ON journal_sessions(external_id) WHERE external_id IS NOT NULL;"
                )
            except Exception:
                pass
        # Pragmas for better concurrency / durability
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")

# --- Ensure DB schema and patches are applied before any migration/backfill ---
ensure_db_initialized_unified()

# --- Early paths & helpers for migration (must exist before first call) ----
def _mkdirp(path: str):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        print(f"[INIT] WARN could not create directory '{path}': {e}")

# Ensure config/backup directories exist before migration runs
CONFIG_DIR = globals().get("CONFIG_DIR") or os.path.join(INSTANCE_PATH, "configs")
BACKUP_DIR = globals().get("BACKUP_DIR") or os.path.join(INSTANCE_PATH, "backups")
_mkdirp(CONFIG_DIR)
_mkdirp(BACKUP_DIR)


# === YAML → DB one-time migration ===========================================
def _timestamped_copy(src_dir: str):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(BACKUP_DIR, f"yaml_backup_{ts}")
    try:
        shutil.copytree(src_dir, dst)
        print(f"[MIGRATION] YAML backup saved to {dst}")
    except Exception as e:
        print(f"[MIGRATION] WARNING: could not backup YAMLs: {e}")


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

def _select_rigs_yaml(username: str) -> dict:
    """Prefer per-user rigs_<user>.yaml; only use rigs_default.yaml for the 'default' account."""
    user_path = os.path.join(CONFIG_DIR, f"rigs_{username}.yaml")
    default_path = os.path.join(CONFIG_DIR, "rigs_default.yaml")
    try:
        if os.path.exists(user_path):
            doc = _read_yaml(user_path) or {}
            try:
                n = len((doc.get("rigs") or []))
            except Exception:
                n = 0
            print(f"[MIGRATION] Rigs for '{username}': {n} entries (source: {os.path.basename(user_path)})")
            return doc
        if username == "default" and os.path.exists(default_path):
            doc = _read_yaml(default_path) or {}
            try:
                n = len((doc.get("rigs") or []))
            except Exception:
                n = 0
            print(f"[MIGRATION] Rigs for 'default': {n} entries (source: rigs_default.yaml)")
            return doc
    except Exception as e:
        print(f"[MIGRATION] WARN reading rigs YAML for '{username}': {e}")
    print(f"[MIGRATION] Rigs for '{username}': 0 entries (source: none)")
    return {}

def _iter_candidate_users():
    names = set()
    for p in glob.glob(os.path.join(CONFIG_DIR, "config_*.yaml")):
        names.add(Path(p).stem.replace("config_", ""))
    for p in glob.glob(os.path.join(CONFIG_DIR, "journal_*.yaml")):
        names.add(Path(p).stem.replace("journal_", ""))
    names.update(["default", "guest_user"])
    return sorted(n for n in names if n)

def _upsert_user(db, username: str) -> DbUser:
    u = db.query(DbUser).filter_by(username=username).one_or_none()
    if not u:
        u = DbUser(username=username, active=True)
        db.add(u)
        db.flush()
    return u

def _migrate_locations(db, user: DbUser, config: dict):
    """
    Idempotent import of locations:
      - Upsert per (user_id, name)
      - Replace horizon points on update
      - Ensure only default_location has is_default=True
    """
    locs = (config or {}).get("locations", {}) or {}
    default_name = (config or {}).get("default_location")

    # First, clear default flags for this user's locations. We'll set the correct one below.
    db.query(Location).filter_by(user_id=user.id).update({Location.is_default: False})
    db.flush()

    for name, loc in locs.items():
        try:
            lat = float(loc.get("lat"))
            lon = float(loc.get("lon"))
            tz = loc.get("timezone", "UTC")
            alt_thr_val = loc.get("altitude_threshold")
            alt_thr = float(alt_thr_val) if alt_thr_val is not None else None
            new_is_default = (name == default_name)

            existing = db.query(Location).filter_by(user_id=user.id, name=name).one_or_none()
            if existing:
                # --- UPDATE existing row
                existing.lat = lat
                existing.lon = lon
                existing.timezone = tz
                existing.altitude_threshold = alt_thr
                existing.is_default = new_is_default
                existing.active = loc.get("active", True)

                # Replace horizon points
                db.query(HorizonPoint).filter_by(location_id=existing.id).delete()
                hm = loc.get("horizon_mask")
                if isinstance(hm, list):
                    for pair in hm:
                        try:
                            az, altmin = float(pair[0]), float(pair[1])
                            db.add(HorizonPoint(location_id=existing.id, az_deg=az, alt_min_deg=altmin))
                        except Exception:
                            pass
            else:
                # --- INSERT new row
                row = Location(
                    user_id=user.id,
                    name=name,
                    lat=lat,
                    lon=lon,
                    timezone=tz,
                    altitude_threshold=alt_thr,
                    is_default=new_is_default
                )
                db.add(row); db.flush()

                hm = loc.get("horizon_mask")
                if isinstance(hm, list):
                    for pair in hm:
                        try:
                            az, altmin = float(pair[0]), float(pair[1])
                            db.add(HorizonPoint(location_id=row.id, az_deg=az, alt_min_deg=altmin))
                        except Exception:
                            pass
        except Exception as e:
            print(f"[MIGRATION] Skip/repair location '{name}': {e}")

def _migrate_objects(db, user: DbUser, config: dict):
    """
    Idempotently migrates astronomical objects from a YAML configuration dictionary to the database.

    This function performs an "upsert" (update or insert) for each object based on its
    unique name for a given user. It prevents duplicates, handles various legacy key names,
    and automatically calculates the constellation if it's missing but coordinates are present.

    Args:
        db: The SQLAlchemy session object.
        user (DbUser): The user account to which these objects will be associated.
        config (dict): The user's configuration dictionary, expected to contain an 'objects' list.
    """
    # Safely get the list of objects, defaulting to an empty list if missing.
    objs = (config or {}).get("objects", []) or []

    for o in objs:
        try:
            # --- 1. Robustly Parse Object Data from Dictionary ---
            # Use .get() with fallbacks to handle different key names found in older YAML files.
            ra_val = o.get("RA") if o.get("RA") is not None else o.get("RA (hours)")
            dec_val = o.get("DEC") if o.get("DEC") is not None else o.get("DEC (degrees)")

            # The canonical object identifier is crucial. Skip if it's missing or blank.
            raw_obj_name = o.get("Object") or o.get("object") or o.get("object_name")
            if not raw_obj_name or not str(raw_obj_name).strip():
                print(f"[MIGRATION][OBJECT SKIP] Entry is missing an 'Object' identifier: {o}")
                continue
            # Sanitize the name to ensure consistency (e.g., " M31 " becomes "M31").
            object_name = " ".join(str(raw_obj_name).strip().split())

            common_name = o.get("Common Name") or o.get("Name") or o.get("common_name")
            obj_type = o.get("Type") or o.get("type")
            constellation = o.get("Constellation") or o.get("constellation")
            magnitude = o.get("Magnitude") if o.get("Magnitude") is not None else o.get("magnitude")
            size = o.get("Size") if o.get("Size") is not None else o.get("size")
            sb = o.get("SB") if o.get("SB") is not None else o.get("sb")
            active_project = bool(o.get("ActiveProject") or o.get("active_project") or False)
            project_name = o.get("Project") or o.get("project_name")

            ra_f = float(ra_val) if ra_val is not None else None
            dec_f = float(dec_val) if dec_val is not None else None

            # --- 2. Enrich Data: Calculate Constellation if Missing ---
            # This integrates the logic from the old `backfill_missing_fields` function.
            if (not constellation) and (ra_f is not None) and (dec_f is not None):
                try:
                    # Create a coordinate object and use Astropy to find its constellation.
                    coords = SkyCoord(ra=ra_f * u.hourangle, dec=dec_f * u.deg)
                    constellation = get_constellation(coords)
                except Exception:
                    constellation = None # Avoid crashing if coordinates are invalid.

            # --- 3. Perform the Idempotent "Upsert" ---
            # Query for an existing object with the same name for this user (case-insensitive).
            existing = db.query(AstroObject).filter(
                AstroObject.user_id == user.id,
                AstroObject.object_name.collate('NOCASE') == object_name
            ).one_or_none()

            if existing:
                # UPDATE PATH: The object already exists, so we update its fields.
                # This overwrites existing data with what's in the YAML, ensuring the
                # migration reflects the source of truth.
                existing.common_name = common_name
                existing.ra_hours = ra_f
                existing.dec_deg = dec_f
                existing.type = obj_type
                existing.constellation = constellation
                existing.magnitude = str(magnitude) if magnitude is not None else None
                existing.size = str(size) if size is not None else None
                existing.sb = str(sb) if sb is not None else None
                existing.active_project = active_project
                existing.project_name = project_name
            else:
                # INSERT PATH: The object is new, so we create a new database record.
                new_object = AstroObject(
                    user_id=user.id,
                    object_name=object_name,
                    common_name=common_name,
                    ra_hours=ra_f,
                    dec_deg=dec_f,
                    type=obj_type,
                    constellation=constellation,
                    magnitude=str(magnitude) if magnitude is not None else None,
                    size=str(size) if size is not None else None,
                    sb=str(sb) if sb is not None else None,
                    active_project=active_project,
                    project_name=project_name,
                )
                db.add(new_object)

        except Exception as e:
            # If one object entry is malformed, log the error and continue with the rest.
            print(f"[MIGRATION] Could not process object entry '{o}'. Error: {e}")


def _try_float(v):
    try:
        return float(v) if v is not None else None
    except:
        return None

def _as_int(v):
    try:
        return int(str(v)) if v is not None else None
    except:
        return None

def _norm_name(s: str | None) -> str | None:
    """
    Normalize names for consistent lookups:
    - strip outer whitespace
    - collapse internal whitespace to single spaces
    - casefold for case-insensitive matching
    """
    if not s:
        return None
    s2 = " ".join(str(s).strip().split())
    return s2.casefold()

def _compute_rig_metrics_from_components(telescope: Component | None,
                                          camera: Component | None,
                                          reducer: Component | None):
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
    except Exception:
        return (None, None, None, None)


def _migrate_components_and_rigs(db, user: DbUser, rigs_yaml: dict, username: str):
    """
    Idempotent import for components and rigs that unifies all logic.
    - UPSERTS components by (user_id, kind, normalized_name), preventing duplicates.
    - Creates components on-the-fly if referenced by a rig but not explicitly defined.
    - UPSERTS rigs by (user_id, rig_name).
    - Skips creating rigs if a valid telescope or camera cannot be found/created.
    - Removes the need for post-migration deduplication or cleanup.
    """
    if not isinstance(rigs_yaml, dict):
        return

    comps = rigs_yaml.get("components", {}) or {}
    rig_list = rigs_yaml.get("rigs", []) or []

    # --- Internal Helper Functions ---

    def _coerce_float(x):
        try:
            return float(x) if x is not None else None
        except (ValueError, TypeError):
            return None

    # This helper function is already correct from our previous step.
    def _get_or_create_component(kind: str, name: str, **fields) -> Component | None:
        if not kind or not name:
            return None
        trimmed_name = " ".join(str(name).strip().split())
        existing_row = db.query(Component).filter(
            Component.user_id == user.id,
            Component.kind == kind,
            Component.name.collate('NOCASE') == trimmed_name
        ).one_or_none()
        if existing_row:
            if existing_row.aperture_mm is None: existing_row.aperture_mm = _coerce_float(fields.get("aperture_mm"))
            if existing_row.focal_length_mm is None: existing_row.focal_length_mm = _coerce_float(
                fields.get("focal_length_mm"))
            if existing_row.sensor_width_mm is None: existing_row.sensor_width_mm = _coerce_float(
                fields.get("sensor_width_mm"))
            if existing_row.sensor_height_mm is None: existing_row.sensor_height_mm = _coerce_float(
                fields.get("sensor_height_mm"))
            if existing_row.pixel_size_um is None: existing_row.pixel_size_um = _coerce_float(
                fields.get("pixel_size_um"))
            if existing_row.factor is None: existing_row.factor = _coerce_float(fields.get("factor"))
            db.flush()
            return existing_row
        new_row = Component(
            user_id=user.id, kind=kind, name=trimmed_name,
            aperture_mm=_coerce_float(fields.get("aperture_mm")),
            focal_length_mm=_coerce_float(fields.get("focal_length_mm")),
            sensor_width_mm=_coerce_float(fields.get("sensor_width_mm")),
            sensor_height_mm=_coerce_float(fields.get("sensor_height_mm")),
            pixel_size_um=_coerce_float(fields.get("pixel_size_um")),
            factor=_coerce_float(fields.get("factor")),
        )
        db.add(new_row)
        db.flush()
        return new_row

    # Use a string-keyed dictionary for the legacy IDs.
    legacy_id_to_component_id: dict[str, int] = {}
    name_to_component_id: dict[tuple[str, str | None], int] = {}

    def _remember_component(row: Component | None, kind: str, name: str, legacy_id):
        if row is None or legacy_id is None: return
        # ❗ FIX: Store the legacy_id as a string, removing the int() conversion.
        legacy_id_to_component_id[str(legacy_id)] = row.id
        if name:
            name_to_component_id[(kind, _norm_name(name))] = row.id

    def _get_alias(d: dict, key: str, *aliases):
        if key in d and d.get(key) is not None: return d.get(key)
        for a in aliases:
            if a in d and d.get(a) is not None: return d.get(a)
        return None

    # --- 1. Process Components Section ---
    for t in comps.get("telescopes", []):
        row = _get_or_create_component("telescope", _get_alias(t, "name"), aperture_mm=_get_alias(t, "aperture_mm"),
                                       focal_length_mm=_get_alias(t, "focal_length_mm"))
        _remember_component(row, "telescope", _get_alias(t, "name"), t.get("id"))
    for c in comps.get("cameras", []):
        row = _get_or_create_component("camera", _get_alias(c, "name"),
                                       sensor_width_mm=_get_alias(c, "sensor_width_mm"),
                                       sensor_height_mm=_get_alias(c, "sensor_height_mm"),
                                       pixel_size_um=_get_alias(c, "pixel_size_um"))
        _remember_component(row, "camera", _get_alias(c, "name"), c.get("id"))
    for r in comps.get("reducers_extenders", []):
        row = _get_or_create_component("reducer_extender", _get_alias(r, "name"), factor=_get_alias(r, "factor"))
        _remember_component(row, "reducer_extender", _get_alias(r, "name"), r.get("id"))

    def _resolve_component_id(kind: str, legacy_id, name) -> int | None:
        if legacy_id is not None:
            # ❗ FIX: Look up the ID as a string, removing the int() conversion.
            legacy_id_str = str(legacy_id)
            if legacy_id_str in legacy_id_to_component_id:
                return legacy_id_to_component_id[legacy_id_str]

        if name:
            norm_key = (kind, _norm_name(name))
            if norm_key in name_to_component_id:
                return name_to_component_id[norm_key]
            row = _get_or_create_component(kind, str(name))
            if row:
                name_to_component_id[norm_key] = row.id
                return row.id
        return None

    # --- 2. Process Rigs Section ---
    for r in rig_list:
        try:
            rig_name = _get_alias(r, "rig_name", "name")
            if not rig_name: continue

            tel_name = _get_alias(r, "telescope", "telescope_name")
            cam_name = _get_alias(r, "camera", "camera_name")
            red_name = _get_alias(r, "reducer_extender", "reducer_extender_name")

            if (not tel_name or not cam_name) and isinstance(rig_name, str) and '+' in rig_name:
                parts = [p.strip() for p in rig_name.split('+')]
                if len(parts) >= 2:
                    tel_name = tel_name or parts[0]
                    cam_name = cam_name or parts[-1]
                    if len(parts) == 3:
                        red_name = red_name or parts[1]

            tel_id = _resolve_component_id("telescope", r.get("telescope_id"), tel_name)
            cam_id = _resolve_component_id("camera", r.get("camera_id"), cam_name)
            red_id = _resolve_component_id("reducer_extender", r.get("reducer_extender_id"), red_name)

            if not (tel_id and cam_id):
                print(
                    f"[MIGRATION][RIG SKIP] Rig '{rig_name}' for user '{username}' is missing a valid telescope or camera link. Skipping.")
                continue

            eff_fl, f_ratio, scale, fov_w = (_coerce_float(r.get(k)) for k in
                                             ["effective_focal_length", "f_ratio", "image_scale", "fov_w_arcmin"])
            if any(v is None for v in [eff_fl, f_ratio, scale, fov_w]):
                tel_obj, cam_obj = db.get(Component, tel_id), db.get(Component, cam_id)
                red_obj = db.get(Component, red_id) if red_id else None
                ce_fl, cf_ratio, c_scale, c_fovw = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)
                eff_fl, f_ratio, scale, fov_w = (ce_fl if eff_fl is None else eff_fl,
                                                 cf_ratio if f_ratio is None else f_ratio,
                                                 c_scale if scale is None else scale,
                                                 c_fovw if fov_w is None else fov_w)

            existing_rig = db.query(Rig).filter_by(user_id=user.id, rig_name=rig_name).one_or_none()
            if existing_rig:
                existing_rig.telescope_id, existing_rig.camera_id, existing_rig.reducer_extender_id = tel_id, cam_id, red_id
                existing_rig.effective_focal_length, existing_rig.f_ratio, existing_rig.image_scale, existing_rig.fov_w_arcmin = eff_fl, f_ratio, scale, fov_w
            else:
                db.add(Rig(user_id=user.id, rig_name=rig_name, telescope_id=tel_id, camera_id=cam_id,
                           reducer_extender_id=red_id, effective_focal_length=eff_fl, f_ratio=f_ratio,
                           image_scale=scale, fov_w_arcmin=fov_w))
            db.flush()

        except Exception as e:
            print(f"[MIGRATION] Skip/repair rig '{r}': {e}")


def _migrate_journal(db, user: DbUser, journal_yaml: dict):
    data = journal_yaml or {}
    # Normalize old list-based journals to the new dict structure
    if isinstance(data, list):
        data = {"projects": [], "sessions": data}
    else:
        data.setdefault("projects", [])
        data.setdefault("sessions", data.get("entries", []))

    # --- 1. Migrate Projects ---
    for p in (data.get("projects") or []):
        if p.get("project_id") and p.get("project_name"):
            if not db.query(Project).filter_by(id=p["project_id"]).one_or_none():
                db.add(Project(id=p["project_id"], user_id=user.id, name=p["project_name"]))
    db.flush()

    # --- 2. Migrate Sessions with ALL fields ---
    for s in (data.get("sessions") or []):
        ext_id = s.get("session_id") or s.get("id")
        date_str = s.get("session_date") or s.get("date")
        if not date_str: continue

        try:
            dt = datetime.fromisoformat(date_str).date()
        except:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                continue

        # This dictionary now maps every key from your YAML to the database column
        row_values = {
            "user_id": user.id,
            "project_id": s.get("project_id"),
            "date_utc": dt,
            "object_name": s.get("target_object_id") or s.get("object_name"),
            "notes": s.get("general_notes_problems_learnings"),
            "session_image_file": s.get("session_image_file"),
            "location_name": s.get("location_name"),
            "seeing_observed_fwhm": _try_float(s.get("seeing_observed_fwhm")),
            "sky_sqm_observed": _try_float(s.get("sky_sqm_observed")),
            "moon_illumination_session": _as_int(s.get("moon_illumination_session")),
            "moon_angular_separation_session": _try_float(s.get("moon_angular_separation_session")),
            "weather_notes": s.get("weather_notes"),
            "telescope_setup_notes": s.get("telescope_setup_notes"),
            "filter_used_session": s.get("filter_used_session"),
            "guiding_rms_avg_arcsec": _try_float(s.get("guiding_rms_avg_arcsec")),
            "guiding_equipment": s.get("guiding_equipment"),
            "dither_details": s.get("dither_details"),
            "acquisition_software": s.get("acquisition_software"),
            "gain_setting": _as_int(s.get("gain_setting")),
            "offset_setting": _as_int(s.get("offset_setting")),
            "camera_temp_setpoint_c": _try_float(s.get("camera_temp_setpoint_c")),
            "camera_temp_actual_avg_c": _try_float(s.get("camera_temp_actual_avg_c")),
            "binning_session": s.get("binning_session"),
            "darks_strategy": s.get("darks_strategy"),
            "flats_strategy": s.get("flats_strategy"),
            "bias_darkflats_strategy": s.get("bias_darkflats_strategy"),
            "session_rating_subjective": _as_int(s.get("session_rating_subjective")),
            "transparency_observed_scale": s.get("transparency_observed_scale"),
            "number_of_subs_light": _as_int(s.get("number_of_subs_light")),
            "exposure_time_per_sub_sec": _as_int(s.get("exposure_time_per_sub_sec")),
            "filter_L_subs": _as_int(s.get("filter_L_subs")),
            "filter_L_exposure_sec": _as_int(s.get("filter_L_exposure_sec")),
            "filter_R_subs": _as_int(s.get("filter_R_subs")),
            "filter_R_exposure_sec": _as_int(s.get("filter_R_exposure_sec")),
            "filter_G_subs": _as_int(s.get("filter_G_subs")),
            "filter_G_exposure_sec": _as_int(s.get("filter_G_exposure_sec")),
            "filter_B_subs": _as_int(s.get("filter_B_subs")),
            "filter_B_exposure_sec": _as_int(s.get("filter_B_exposure_sec")),
            "filter_Ha_subs": _as_int(s.get("filter_Ha_subs")),
            "filter_Ha_exposure_sec": _as_int(s.get("filter_Ha_exposure_sec")),
            "filter_OIII_subs": _as_int(s.get("filter_OIII_subs")),
            "filter_OIII_exposure_sec": _as_int(s.get("filter_OIII_exposure_sec")),
            "filter_SII_subs": _as_int(s.get("filter_SII_subs")),
            "filter_SII_exposure_sec": _as_int(s.get("filter_SII_exposure_sec")),
            "calculated_integration_time_minutes": _try_float(s.get("calculated_integration_time_minutes")),
            "external_id": str(ext_id) if ext_id else None
        }

        # Upsert logic - More Defensive
        should_add = True
        if ext_id:
            # Check if it *already exists in the DB*
            existing_in_db = db.query(JournalSession).filter_by(user_id=user.id, external_id=str(ext_id)).one_or_none()
            if existing_in_db:
                 # It's already committed in the DB, just update it
                for k, v in row_values.items():
                    if v is not None:
                        setattr(existing_in_db, k, v)
                should_add = False # Don't try to add it again
            else:
                 # Check if we *just added* it in this same transaction (prevent double-add before commit)
                 # This uses SQLAlchemy's identity map implicitly
                 maybe_added_session = db.query(JournalSession).filter_by(user_id=user.id, external_id=str(ext_id)).with_for_update().one_or_none()
                 if maybe_added_session in db.new:
                     # Already staged for insert, maybe update if needed, but don't add again
                     for k, v in row_values.items():
                         if v is not None:
                             setattr(maybe_added_session, k, v)
                     should_add = False

        if should_add:
             # Only add if it doesn't exist in DB and isn't already staged for insertion
            new_session = JournalSession(**row_values)
            db.add(new_session)


def _migrate_ui_prefs(db, user: DbUser, config: dict):
    """
    Saves all general, user-specific settings from the config YAML
    into a single JSON blob in the ui_prefs table.
    """
    # Gather all the top-level settings we want to save
    settings_to_save = {
        "altitude_threshold": config.get("altitude_threshold"),
        "default_location": config.get("default_location"),
        "imaging_criteria": config.get("imaging_criteria"),
        "sampling_interval_minutes": config.get("sampling_interval_minutes"),
        "telemetry": config.get("telemetry"),
        "rig_sort": (config.get("ui") or {}).get("rig_sort")
    }

    # Only create a record if there's at least one setting to save
    if any(v is not None for v in settings_to_save.values()):
        # Upsert logic: find existing pref or create a new one
        existing_pref = db.query(UiPref).filter_by(user_id=user.id).one_or_none()

        blob = json.dumps(settings_to_save, ensure_ascii=False)

        if existing_pref:
            existing_pref.json_blob = blob
        else:
            new_pref = UiPref(user_id=user.id, json_blob=blob)
            db.add(new_pref)


def build_user_config_from_db(username: str) -> dict:
    """
    Builds a complete, YAML-like user config dictionary from the database tables.
    This is the new single source of truth for runtime configuration.
    """
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            return {}

        # --- 1. Load the general settings from the UiPref table ---
        user_config = {}
        prefs = db.query(UiPref).filter_by(user_id=u.id).one_or_none()
        if prefs and prefs.json_blob:
            try:
                user_config = json.loads(prefs.json_blob)
            except json.JSONDecodeError:
                print(f"[CONFIG BUILD] WARNING: Could not parse UI prefs JSON for user '{username}'")

        # --- 2. Load Locations (your existing logic is perfect) ---
        loc_rows = db.query(Location).filter_by(user_id=u.id).all()
        locations = {}
        for l in loc_rows:
            mask = [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
            locations[l.name] = {
                "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                "altitude_threshold": l.altitude_threshold, "horizon_mask": mask
            }
        user_config["locations"] = locations

        # --- 3. Load Objects (your existing logic is perfect) ---
        obj_rows = db.query(AstroObject).filter_by(user_id=u.id).all()
        objects = []
        for o in obj_rows:
            row = {
                "Object": o.object_name,
                "Name": o.common_name,  # For compatibility with config_form.html
                "Common Name": o.common_name,  # For compatibility with the index page
                "RA": o.ra_hours,
                "DEC": o.dec_deg,
                # ... (the rest of the fields are the same)
                "RA (hours)": o.ra_hours, "DEC (degrees)": o.dec_deg,
                "Type": o.type, "Constellation": o.constellation, "Magnitude": o.magnitude,
                "Size": o.size, "SB": o.sb, "ActiveProject": o.active_project, "Project": o.project_name
            }
            objects.append(row)
        user_config["objects"] = objects

        return user_config
    finally:
        db.close()

# === YAML Portability: Export / Import ======================================
def export_user_to_yaml(username: str, out_dir: str = None) -> bool:
    """
    Write three YAML files (config_*.yaml, rigs_default.yaml, journal_*.yaml) in out_dir.
    """
    db = get_db()
    try:
        out_dir = out_dir or CONFIG_DIR
        os.makedirs(out_dir, exist_ok=True)

        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            return False

        # CONFIG (locations + objects + defaults)
        locs = db.query(Location).filter_by(user_id=u.id).all()
        default_loc = next((l.name for l in locs if l.is_default), None)
        cfg = {
            "default_location": default_loc,
            "locations": {
                l.name: {
                    "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                    "altitude_threshold": l.altitude_threshold,
                    "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
                } for l in locs
            },
            "objects": [
                {
                    "Object": o.object_name, "Common Name": o.common_name,
                    "RA (hours)": o.ra_hours, "DEC (degrees)": o.dec_deg,
                    "Type": o.type, "Constellation": o.constellation,
                    "Magnitude": o.magnitude, "Size": o.size, "SB": o.sb,
                    "ActiveProject": o.active_project, "Project": o.project_name
                } for o in db.query(AstroObject).filter_by(user_id=u.id).all()
            ]
        }
        cfg_file = "config_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"config_{username}.yaml"
        _atomic_write_yaml(os.path.join(out_dir, cfg_file), cfg)

        # RIGS/COMPONENTS
        comps = db.query(Component).filter_by(user_id=u.id).all()
        rigs = db.query(Rig).filter_by(user_id=u.id).all()
        def bykind(k): return [c for c in comps if c.kind == k]
        rigs_doc = {
            "components": {
                "telescopes": [
                    {"id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm}
                    for c in bykind("telescope")
                ],
                "cameras": [
                    {"id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                     "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um}
                    for c in bykind("camera")
                ],
                "reducers_extenders": [
                    {"id": c.id, "name": c.name, "factor": c.factor}
                    for c in bykind("reducer_extender")
                ],
            },
            "rigs": [
                {
                    "rig_name": r.rig_name,
                    "telescope_id": r.telescope_id,
                    "camera_id": r.camera_id,
                    "reducer_extender_id": r.reducer_extender_id,
                    "effective_focal_length": r.effective_focal_length,
                    "f_ratio": r.f_ratio,
                    "image_scale": r.image_scale,
                    "fov_w_arcmin": r.fov_w_arcmin
                } for r in rigs
            ]
        }
        rig_file = "rigs_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"rigs_{username}.yaml"
        _atomic_write_yaml(os.path.join(out_dir, rig_file), rigs_doc)
        try:
            print(f"[EXPORT] Rigs for '{username}' written to {rig_file} (count={len(rigs)})")
        except Exception:
            pass

        # JOURNAL
        sessions = db.query(JournalSession).filter_by(user_id=u.id).order_by(JournalSession.date_utc.asc()).all()
        jdoc = {
            "projects": [],
            "sessions": [
                {
                    "date": s.date_utc.isoformat(), "object_name": s.object_name, "notes": s.notes,
                    "number_of_subs_light": s.number_of_subs_light,
                    "exposure_time_per_sub_sec": s.exposure_time_per_sub_sec,
                    "filter_L_subs": s.filter_L_subs, "filter_L_exposure_sec": s.filter_L_exposure_sec,
                    "filter_R_subs": s.filter_R_subs, "filter_R_exposure_sec": s.filter_R_exposure_sec,
                    "filter_G_subs": s.filter_G_subs, "filter_G_exposure_sec": s.filter_G_exposure_sec,
                    "filter_B_subs": s.filter_B_subs, "filter_B_exposure_sec": s.filter_B_exposure_sec,
                    "filter_Ha_subs": s.filter_Ha_subs, "filter_Ha_exposure_sec": s.filter_Ha_exposure_sec,
                    "filter_OIII_subs": s.filter_OIII_subs, "filter_OIII_exposure_sec": s.filter_OIII_exposure_sec,
                    "filter_SII_subs": s.filter_SII_subs, "filter_SII_exposure_sec": s.filter_SII_exposure_sec,
                    "calculated_integration_time_minutes": s.calculated_integration_time_minutes
                } for s in sessions
            ]
        }
        jfile = "journal_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"journal_{username}.yaml"
        _atomic_write_yaml(os.path.join(out_dir, jfile), jdoc)
        return True
    finally:
        db.close()

def import_user_from_yaml(username: str,
                          config_path: str,
                          rigs_path: str,
                          journal_path: str,
                          clear_existing: bool = False) -> bool:
    """
    Upsert from YAML into DB. Optionally clears existing user data first.
    """
    db = get_db()
    try:
        user = _upsert_user(db, username)
        if clear_existing:
            # cascades remove all
            db.delete(user); db.flush()
            user = _upsert_user(db, username)

        cfg_tuple = _read_yaml(config_path);
        rigs_tuple = _read_yaml(rigs_path);
        jrn_tuple = _read_yaml(journal_path)

        # Extract the data dictionary (first element) from each tuple
        cfg_data = cfg_tuple[0]
        rigs_data = rigs_tuple[0]
        jrn_data = jrn_tuple[0]

        # Pass the extracted dictionaries to the migration functions
        _migrate_locations(db, user, cfg_data)
        _migrate_objects(db, user, cfg_data)
        _migrate_components_and_rigs(db, user, rigs_data, username)
        _migrate_journal(db, user, jrn_data)
        _migrate_ui_prefs(db, user, cfg_data)
        db.commit()
        return True
    except Exception:
        db.rollback()
        traceback.print_exc()
        return False
    finally:
        db.close()

def import_from_existing_yaml(username: str, clear_existing: bool = False) -> bool:
    """
    Import for `username` from YAML files that are already on disk in CONFIG_DIR.
    This keeps the SQLite database in sync after any UI-based YAML import.
    """
    s_mode = bool(globals().get("SINGLE_USER_MODE", True))
    cfg_file = "config_default.yaml" if (s_mode and username == "default") else f"config_{username}.yaml"
    jrn_file = "journal_default.yaml" if (s_mode and username == "default") else f"journal_{username}.yaml"

    # Prefer per-user rigs; fall back to default
    per_user_rigs = os.path.join(CONFIG_DIR, f"rigs_{username}.yaml")
    rigs_path = per_user_rigs if os.path.exists(per_user_rigs) else os.path.join(CONFIG_DIR, "rigs_default.yaml")

    cfg_path = os.path.join(CONFIG_DIR, cfg_file)
    jrn_path = os.path.join(CONFIG_DIR, jrn_file)
    return import_user_from_yaml(username, cfg_path, rigs_path, jrn_path, clear_existing=clear_existing)

def repair_journals(dry_run: bool = False):
    """
    Deduplicate journal_sessions and backfill missing object_name from YAML if possible.

    Dedupe key:
      (user_id, date_utc, object_name, notes, number_of_subs_light, exposure_time_per_sub_sec,
       filter_*_subs, filter_*_exposure_sec, calculated_integration_time_minutes)

    Keep the first row (lowest id) for each identical key; delete the rest.
    Then try to backfill object_name from the user's YAML by date if missing.
    """
    db = get_db()
    try:
        changes = []
        users = db.query(DbUser).all()
        for u in users:
            rows = db.query(JournalSession).filter_by(user_id=u.id).order_by(JournalSession.id.asc()).all()
            seen = {}
            to_delete = []

            def sig(r: JournalSession):
                # tuple signature for exact duplicates
                return (
                    r.date_utc.isoformat() if r.date_utc else "",
                    (r.object_name or "").strip(),
                    (r.notes or "").strip(),
                    r.number_of_subs_light,
                    r.exposure_time_per_sub_sec,
                    r.filter_L_subs, r.filter_L_exposure_sec,
                    r.filter_R_subs, r.filter_R_exposure_sec,
                    r.filter_G_subs, r.filter_G_exposure_sec,
                    r.filter_B_subs, r.filter_B_exposure_sec,
                    r.filter_Ha_subs, r.filter_Ha_exposure_sec,
                    r.filter_OIII_subs, r.filter_OIII_exposure_sec,
                    r.filter_SII_subs, r.filter_SII_exposure_sec,
                    r.calculated_integration_time_minutes,
                )

            for r in rows:
                key = sig(r)
                if key in seen:
                    to_delete.append(r)
                else:
                    seen[key] = r

            if to_delete:
                changes.append(f"[JOURNAL REPAIR] user={u.username} deleting {len(to_delete)} exact duplicates")
                if not dry_run:
                    for r in to_delete:
                        db.delete(r)

            # Backfill missing object_name where possible from YAML (by date)
            # YAML path: per user -> journal_<username>.yaml, single-user -> journal_default.yaml
            s_mode = bool(globals().get("SINGLE_USER_MODE", True))
            jfile = os.path.join(CONFIG_DIR, "journal_default.yaml" if (s_mode and u.username == "default") else f"journal_{u.username}.yaml")
            by_date = {}
            if os.path.exists(jfile):
                try:
                    y = _read_yaml(jfile)
                    if isinstance(y, dict):
                        for s in (y.get("sessions") or []):
                            # find a name variant
                            name = None
                            for k in ("object_name", "Object", "object", "target", "Name", "name"):
                                v = s.get(k)
                                if isinstance(v, str) and v.strip():
                                    name = v.strip(); break
                            d = s.get("date")
                            if isinstance(d, str) and name:
                                by_date.setdefault(d, []).append(name)
                except Exception as e:
                    print(f"[JOURNAL REPAIR] WARN cannot read YAML for '{u.username}': {e}")

            filled = 0
            if by_date:
                for r in db.query(JournalSession).filter_by(user_id=u.id).all():
                    if not r.object_name and r.date_utc:
                        names = by_date.get(r.date_utc.isoformat())
                        if names:
                            # pick the first available name for that date
                            r.object_name = names[0]
                            filled += 1
                if filled:
                    changes.append(f"[JOURNAL REPAIR] user={u.username} backfilled object_name for {filled} sessions")

        if dry_run:
            for line in changes:
                print(line)
            print("[JOURNAL REPAIR] Dry-run complete; no DB changes.")
            db.rollback()
        else:
            db.commit()
            for line in changes:
                print(line)
            print("[JOURNAL REPAIR] Commit complete.")
    except Exception as e:
        db.rollback()
        print(f"[JOURNAL REPAIR] ERROR: {e}")
    finally:
        db.close()

def _select_rigs_yaml_path(username: str) -> str:
    """Returns the path to the correct rigs YAML file for a user."""
    user_path = os.path.join(CONFIG_DIR, f"rigs_{username}.yaml")
    default_path = os.path.join(CONFIG_DIR, "rigs_default.yaml")
    if os.path.exists(user_path):
        return user_path
    # Only the 'default' user should fall back to rigs_default.yaml
    if username == "default" and os.path.exists(default_path):
        return default_path
    # For any other user, return their specific (potentially non-existent) path.
    return user_path


def run_one_time_yaml_migration():
    """
    Drives the one-time migration from YAML files to the app.db database.

    This function uses the separate users.db as the source of truth for which users
    to migrate, ensuring consistency with the external authentication system.
    """
    db = get_db()
    try:
        # Safety Check: Prevent re-running on an already populated database
        if db.query(DbUser).first() and db.query(Location).first():
            print("[MIGRATION] Database (app.db) already appears to be populated. Skipping YAML migration.")
            return

        print("[MIGRATION] Starting one-time migration from YAML files to the database...")

        # Backup Existing Configuration Directory
        _mkdirp(BACKUP_DIR)
        _timestamped_copy(CONFIG_DIR)

        usernames_to_migrate = set()
        # FIX: Read SINGLE_USER_MODE directly from environment/config here
        is_single_user_mode_for_migration = config('SINGLE_USER_MODE', default='True') == 'True'

        if is_single_user_mode_for_migration:
            print("[MIGRATION] Single-User Mode: Migrating only the 'default' user.")
            usernames_to_migrate.add("default")
        else:
            # Multi-User Mode: Get users from the authentication DB
            auth_db_path = os.path.join(INSTANCE_PATH, "users.db")
            if os.path.exists(auth_db_path):
                print(
                    f"[MIGRATION] Multi-User Mode: Reading user list from authentication database: {os.path.basename(auth_db_path)}")
                AuthBase = declarative_base()

                class AuthUser(AuthBase):
                    __tablename__ = 'user';
                    id = Column(Integer, primary_key=True);
                    username = Column(String)

                auth_engine = create_engine(f'sqlite:///{auth_db_path}');
                AuthSession = sessionmaker(bind=auth_engine);
                auth_session = AuthSession()
                try:
                    all_auth_users = auth_session.query(AuthUser).all()
                    usernames_to_migrate.update(u.username for u in all_auth_users)
                    print(f"   -> Found {len(all_auth_users)} users in users.db.")
                finally:
                    auth_session.close()
            else:
                print(
                    "[MIGRATION] WARNING: Multi-User Mode but users.db not found. Migrating based on YAML files only.")
                usernames_to_migrate.update(_iter_candidate_users())

            # Always include default and guest in multi-user mode as well
            usernames_to_migrate.update(["default", "guest_user"])

        # --- Iterate Through Each User and Migrate Their Data ---
        for username in sorted(list(usernames_to_migrate)):
            print(f"--- Processing migration for user: '{username}' ---")

            s_mode = bool(globals().get("SINGLE_USER_MODE", True))
            cfg_path = os.path.join(CONFIG_DIR, "config_default.yaml" if (
                        s_mode and username == "default") else f"config_{username}.yaml")

            cfg_user, error = _read_yaml(cfg_path)

            if cfg_user is None:
                print(f"[MIGRATION] FATAL ERROR for user '{username}'. The primary config file is corrupt.")
                print(f"   └─ Details: {error}")
                print(f"   └─ Skipping all migration for this user. Please fix the YAML file.")
                continue

            # Load other, non-critical files
            rigs_path = _select_rigs_yaml_path(username)
            rigs_yaml, _ = _read_yaml(rigs_path)

            jrn_path = os.path.join(CONFIG_DIR, "journal_default.yaml" if (
                        s_mode and username == "default") else f"journal_{username}.yaml")
            jrn_yaml, _ = _read_yaml(jrn_path)

            # Get or Create the User Record in app.db
            user = _upsert_user(db, username)
            db.flush()

            # Execute the Migration Steps for this user
            _migrate_locations(db, user, cfg_user)
            _migrate_objects(db, user, cfg_user)
            _migrate_components_and_rigs(db, user, rigs_yaml, username)
            _migrate_journal(db, user, jrn_yaml)
            _migrate_ui_prefs(db, user, cfg_user)

            db.commit()
            print(f"--- Successfully committed data for user: '{username}' ---")

        print(f"[MIGRATION] YAML to Database migration completed.")

    except Exception as e:
        db.rollback()
        print(f"[MIGRATION] FATAL UNEXPECTED ERROR during migration.")
        print(traceback.format_exc())
    finally:
        db.close()

run_one_time_yaml_migration()

# =============================================================================
# Flask and Flask-Login Setup
# =============================================================================

APP_VERSION = "3.7.2"

# One-time init flag for startup telemetry in Flask >= 3
_telemetry_startup_once = threading.Event()

TELEMETRY_DEBUG_STATE = {
    'endpoint': None,
    'last_payload': None,
    'last_result': None,
    'last_error': None,
    'last_ts': None
}

# Flag to indicate if this is the first run and .env was just created
FIRST_RUN_ENV_CREATED = False

INSTANCE_PATH = os.path.join(os.path.dirname(__file__), "instance")
# Directory where master template files live (used across the module)
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "config_templates")
ENV_FILE = os.path.join(INSTANCE_PATH, ".env")
load_dotenv(dotenv_path=ENV_FILE)

# --- Ensure existing .env files get upgraded with required keys (no overwrite) ---
def _ensure_env_defaults(env_path: str = ENV_FILE):
    try:
        os.makedirs(os.path.dirname(env_path), exist_ok=True)
        if not os.path.exists(env_path):
            return  # fresh creation is handled below
        # Read existing lines
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()
        needs_write = False
        def _has_key(k: str) -> bool:
            # match beginning-of-line KEY=... (simple, robust)
            return any(line.strip().startswith(k + "=") for line in content.splitlines())

        additions = []
        if not _has_key("INSTANCE_ID"):
            additions.append(f"INSTANCE_ID={secrets.token_hex(16)}")
        if not _has_key("NOVA_TELEMETRY_ENDPOINT"):
            additions.append("NOVA_TELEMETRY_ENDPOINT=https://script.google.com/macros/s/AKfycbz9Up3EEFuuwcbLnXtnsagyZjoE4oASl2PIjr4qgnaNhOsXzNQJykgtzhbCINXFVCDh-w/exec")

        if additions:
            with open(env_path, "a", encoding="utf-8") as f:
                for line in additions:
                    f.write("\n" + line)
            needs_write = True

        # Also reflect into the current process so subsequent code sees values immediately
        if needs_write:
            for line in additions:
                try:
                    k, v = line.split("=", 1)
                    os.environ[k] = v
                except Exception:
                    pass
    except Exception as _e:
        print(f"[ENV UPGRADE] Warning: could not ensure .env defaults: {_e}")

SINGLE_USER_MODE = config('SINGLE_USER_MODE',  default='True') == 'True'

# load_dotenv()
static_cache = {}
moon_separation_cache = {}
nightly_curves_cache = {}
cache_worker_status = {}
monthly_top_targets_cache = {}
config_cache = {}
config_mtime = {}
journal_cache = {}
journal_mtime = {}
LATEST_VERSION_INFO = {}
rig_data_cache = {}
weather_cache = {}
MAX_ACTIVE_LOCATIONS = 5

# CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config_default.yaml")


STELLARIUM_ERROR_MESSAGE = os.getenv("STELLARIUM_ERROR_MESSAGE")

CACHE_DIR = os.path.join(INSTANCE_PATH, "cache")
CONFIG_DIR = os.path.join(INSTANCE_PATH, "configs") # This is the only directory we need for YAMLs
BACKUP_DIR = os.path.join(INSTANCE_PATH, "backups")
UPLOAD_FOLDER = os.path.join(INSTANCE_PATH, 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# === Safe YAML IO helpers (atomic writes + rotating backups) ===
import tempfile
try:
    import fcntl  # POSIX-only; no-op lock on Windows if import fails
    _HAS_FCNTL = True
except Exception:
    _HAS_FCNTL = False

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

def initialize_instance_directory():
    """
    Checks if the instance directory and default configs exist.
    If not, it creates them from the templates. This makes the app
    work correctly on first run after a fresh git clone.
    """
    # Use the module-level TEMPLATE_DIR

    # The user-specific config directory
    config_dir = os.path.join(INSTANCE_PATH, "configs")

    # Only run this if the user's config directory doesn't exist
    if not os.path.exists(config_dir):
        print("First run detected. Initializing instance directory...")
        try:
            # Create all necessary directories
            os.makedirs(CONFIG_DIR, exist_ok=True)
            os.makedirs(CACHE_DIR, exist_ok=True)
            os.makedirs(BACKUP_DIR, exist_ok=True)

            # List of (template_filename, final_filename) pairs
            files_to_create = [
                ('config_default.yaml', 'config_default.yaml'),
                ('journal_default.yaml', 'journal_default.yaml'),
                ('rigs_default.yaml', 'rigs_default.yaml'),
                ('config_guest_user.yaml', 'config_guest_user.yaml'),
                # Add a journal for the guest user too
                ('journal_default.yaml', 'journal_guest_user.yaml'),
            ]

            for template_name, final_name in files_to_create:
                src_path = os.path.join(TEMPLATE_DIR, template_name)
                dest_path = os.path.join(config_dir, final_name)

                if os.path.exists(src_path):
                    shutil.copy(src_path, dest_path)
                    print(f"   -> Created '{final_name}' from template.")
                else:
                    print(f"   -> WARNING: Template file '{template_name}' not found. Cannot create '{final_name}'.")

            print("✅ Initialization complete.")
        except Exception as e:
            print(f"❌ FATAL ERROR during first-run initialization: {e}")
            # You might want the app to exit if this fails
            # import sys
            # sys.exit(1)

initialize_instance_directory()

# --- Stellarium API URL Configuration ---
# Default URL for running directly on the host
DEFAULT_STELLARIUM_HOST_URL = "http://localhost:8090"
# Special DNS name for Docker Desktop to access the host
DOCKER_DESKTOP_HOST_URL = "http://host.docker.internal:8090"

# Start with the standard default
stellarium_api_url = DEFAULT_STELLARIUM_HOST_URL
print(f"[INIT] Default Stellarium URL: {stellarium_api_url}")

# Check if running inside a Docker container by looking for /.dockerenv
if os.path.exists('/.dockerenv'):
    print("[INIT] Docker environment detected (found /.dockerenv).")
    print(f"[INIT] Attempting to use Docker Desktop host URL for Stellarium: {DOCKER_DESKTOP_HOST_URL}")
    stellarium_api_url = DOCKER_DESKTOP_HOST_URL
    # Note: For Linux Docker (non-Docker Desktop), host.docker.internal might not resolve.
    # In such cases, setting the STELLARIUM_API_URL_BASE environment variable is recommended.
else:
    print("[INIT] Not a Docker environment (/.dockerenv not found).")

# Allow the environment variable to override any automatic detection (highest priority)
STELLARIUM_API_URL_BASE_ENV_VAR = os.getenv("STELLARIUM_API_URL_BASE")
if STELLARIUM_API_URL_BASE_ENV_VAR:
    print(f"[INIT] Environment variable STELLARIUM_API_URL_BASE is set to: '{STELLARIUM_API_URL_BASE_ENV_VAR}'. This will be used.")
    STELLARIUM_API_URL_BASE = STELLARIUM_API_URL_BASE_ENV_VAR
else:
    STELLARIUM_API_URL_BASE = stellarium_api_url
    if os.path.exists('/.dockerenv'):
        print(f"[INIT] STELLARIUM_API_URL_BASE environment variable not set. Using auto-detected Docker host URL: {STELLARIUM_API_URL_BASE}")
    else:
        print(f"[INIT] STELLARIUM_API_URL_BASE environment variable not set. Using default host URL: {STELLARIUM_API_URL_BASE}")

print(f"[INIT] Final Stellarium API URL base for requests: {STELLARIUM_API_URL_BASE}")
# --- End of Stellarium API URL Configuration ---

# Automatically create .env if it doesn't exist
if not os.path.exists(ENV_FILE):
    secret_key = secrets.token_hex(32)

    default_user = "admin"
    default_password = "admin123"

    with open(ENV_FILE, "w") as f:
        f.write(f"SECRET_KEY={secret_key}\n")
        f.write(
            "NOVA_TELEMETRY_ENDPOINT=https://script.google.com/macros/s/AKfycbz9Up3EEFuuwcbLnXtnsagyZjoE4oASl2PIjr4qgnaNhOsXzNQJykgtzhbCINXFVCDh-w/exec\n")
        instance_id = secrets.token_hex(16)
        f.write(f"INSTANCE_ID={instance_id}\n")

    # After creating the .env, reload it into the current process and set the first-run flag
    try:
        load_dotenv(dotenv_path=ENV_FILE, override=True)
        print("[ENV INIT] .env created and reloaded into current process")
    except Exception as _e:
        print(f"[ENV INIT] Warning: could not reload .env into process: {_e}")
    FIRST_RUN_ENV_CREATED = True

# Upgrade existing .env files that may be missing new keys (from older installs)
_ensure_env_defaults(ENV_FILE)

# Load SECRET_KEY and users from the .env file
SECRET_KEY = config('SECRET_KEY', default=secrets.token_hex(32))  # Ensure a fallback key

def to_yaml_filter(data):
    """Jinja2 filter to convert a Python object to a YAML string for form display."""
    if data is None:
        return ''
    try:
        # CORRECT: Force flow style AND provide a large width to prevent any wrapping.
        return yaml.dump(data, default_flow_style=True, width=9999, sort_keys=False).strip()
    except Exception:
        return ''
app = Flask(__name__)
app.jinja_env.filters['toyaml'] = to_yaml_filter
app.secret_key = SECRET_KEY

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

if not SINGLE_USER_MODE:
    # --- MULTI-USER MODE SETUP ---
    db_path = os.path.join(INSTANCE_PATH, 'users.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db = SQLAlchemy(app)

    class User(UserMixin, db.Model):
        id = db.Column(db.Integer, primary_key=True)
        username = db.Column(db.String(80), unique=True, nullable=False)
        password_hash = db.Column(db.String(256), nullable=False)

        # NEW: user is active by default
        active = db.Column(db.Boolean, nullable=False, default=True)

        def set_password(self, password):
            self.password_hash = generate_password_hash(password)

        def check_password(self, password):
            return check_password_hash(self.password_hash, password)

        @property
        def is_active(self):
            # Flask-Login uses this to decide if the user can authenticate
            return bool(self.active)

    # Ensure DB tables exist on first run / after switching modes
    def ensure_db_initialized():
        with app.app_context():
            try:
                # Probe the user table; if it fails, create all tables
                db.session.execute(text("SELECT 1 FROM user LIMIT 1"))
            except Exception:
                try:
                    print("[MIGRATION] User table missing. Creating all tables...")
                    db.create_all()
                    print("✅ [MIGRATION] Database initialized.")
                except Exception as e:
                    print(f"❌ [MIGRATION] Failed to initialize DB: {e}")

    # Run the DB initialization once at startup
    ensure_db_initialized()
else:
    # --- SINGLE-USER MODE SETUP ---
    class User(UserMixin):
        def __init__(self, user_id, username):
            self.id = user_id
            self.username = username

# --- SINGLE, UNIFIED USER LOADER ---
# This one function now correctly handles both modes, and guards against stale session IDs.
@login_manager.user_loader
def load_user(user_id):
    """
    Unified loader:
    - SINGLE_USER_MODE: expect the sentinel 'default'
    - Multi-user: only accept integer IDs; any other value is considered stale and ignored
    """
    if SINGLE_USER_MODE:
        return User(user_id="default", username="default") if user_id == "default" else None

    # Multi-user path: guard against stale 'default' / non-integer IDs in session cookies
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(User, uid)


# --- Guard against stale _user_id left in session when switching from single-user to multi-user mode ---
@app.before_request
def _fix_mode_switch_sessions():
    """
    If we are in multi-user mode but the session carries a non-integer _user_id
    (e.g., leftover 'default' from single-user mode), drop it so Flask-Login
    treats the request as anonymous instead of exploding in the user_loader.
    """
    if not SINGLE_USER_MODE:
        try:
            uid = session.get('_user_id')
            if uid is not None and not str(uid).isdigit():
                # purge stale login state
                session.pop('_user_id', None)
                session.pop('_fresh', None)
        except Exception:
            # never block a request due to cleanup logic
            pass


@app.before_request
def inject_user_data():
    """
    Ensures that for every authenticated user, we have a corresponding record
    in our application database (app.db) and load their data into the request context (g).
    """
    # Determine the username from the authentication system (Flask-Login)
    username = None
    if SINGLE_USER_MODE:
        username = "default"
    elif hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
        username = current_user.username

    if not username:
        # No user logged in, or not in single-user mode. Nothing to do.
        g.db_user = None
        return

    db = get_db()
    try:
        # Use our new helper to find or create the user in app.db
        app_db_user = get_or_create_db_user(db, username)
        g.db_user = app_db_user  # Store this for other functions if needed

        if not app_db_user:
            # If provisioning failed, we can't load any data.
            return

        # --- The rest of your data loading logic remains the same ---
        # It now correctly uses the ID from the app.db user record.
        loc_rows = db.query(Location).filter_by(user_id=app_db_user.id).all()
        g.locations = {}
        for l in loc_rows:
            mask = [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
            g.locations[l.name] = {
                "name": l.name, "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                "altitude_threshold": l.altitude_threshold, "is_default": l.is_default,
                "horizon_mask": mask
            }

        g.selected_location = next((n for n, v in g.locations.items() if v.get("is_default")), None) or \
                              (list(g.locations.keys())[:1] or [None])[0]

        obj_rows = db.query(AstroObject).filter_by(user_id=app_db_user.id).all()
        g.objects_list = [
            {"Object": o.object_name, "Common Name": o.common_name, "RA (hours)": o.ra_hours,
             "DEC (degrees)": o.dec_deg,
             "Type": o.type, "Constellation": o.constellation, "Magnitude": o.magnitude, "Size": o.size, "SB": o.sb,
             "ActiveProject": o.active_project, "Project": o.project_name}
            for o in obj_rows
        ]
    finally:
        db.close()

def load_effective_settings():
    """
    Determines the effective settings for telemetry and calculation precision
    based on the application mode (single-user vs. multi-user).
    """
    if SINGLE_USER_MODE:
        # In single-user mode, read from the user's config file.
        g.sampling_interval = g.user_config.get('sampling_interval_minutes', 15)
        g.telemetry_enabled = g.user_config.get('telemetry', {}).get('enabled', True)
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


def convert_to_native_python(val):
    """Converts a NumPy data type to a native Python type if necessary."""
    if isinstance(val, np.generic):
        return val.item()  # .item() is the key function here
    return val


def recursively_clean_numpy_types(data):
    """
    Recursively traverses a dict or list and converts any NumPy
    numeric types to native Python types.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            data[key] = recursively_clean_numpy_types(value)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            data[i] = recursively_clean_numpy_types(item)
    elif isinstance(data, np.generic):
        return data.item()  # This is the core conversion

    return data

def python_format_date_eu(value_iso_str):
    """Jinja filter to convert YYYY-MM-DD string to DD.MM.YYYY string."""
    if not value_iso_str or not isinstance(value_iso_str, str):
        return value_iso_str  # Return as is if not a valid string
    try:
        # If it's already DD.MM.YYYY (e.g. from form input passed back on error)
        if '.' in value_iso_str and len(value_iso_str.split('.')[0]) <= 2:
            try:
                # Validate it is indeed DD.MM.YYYY then return it
                datetime.strptime(value_iso_str, '%d.%m.%Y')
                return value_iso_str
            except ValueError:
                # It had dots but wasn't DD.MM.YYYY, so try parsing as YYYY-MM-DD
                pass # Fall through to YYYY-MM-DD parsing

        date_obj = datetime.strptime(value_iso_str, '%Y-%m-%d')
        return date_obj.strftime('%d.%m.%Y')
    except ValueError:
        return value_iso_str  # Return original if any parsing fails

app.jinja_env.filters['date_eu'] = python_format_date_eu


def load_journal(username):
    """
    Loads journal data, ensuring it conforms to the new {projects, sessions} structure.
    Automatically migrates old journal files.
    """
    if SINGLE_USER_MODE:
        filename = "journal_default.yaml"
    else:
        filename = f"journal_{username}.yaml"
    filepath = os.path.join(CONFIG_DIR, filename)

    if not SINGLE_USER_MODE and not os.path.exists(filepath):
        try:
            default_template_path = os.path.join(TEMPLATE_DIR, 'journal_default.yaml')
            shutil.copy(default_template_path, filepath)
            print(f"   -> Successfully created {filename}.")
        except Exception as e:
            print(f"   -> ❌ ERROR: Could not create journal for '{username}': {e}")
            return {"projects": [], "sessions": []}

    last_modified = os.path.getmtime(filepath) if os.path.exists(filepath) else 0
    if filepath in journal_cache and last_modified <= journal_mtime.get(filepath, 0):
        return journal_cache[filepath]

    if not os.path.exists(filepath):
        return {"projects": [], "sessions": []}

    try:
        with open(filepath, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}

        # --- NEW MIGRATION LOGIC ---
        # If 'projects' key is missing, it's an old format.
        if 'projects' not in data:
            print(f"-> Migrating old journal format for '{filename}'.")
            # Old format might just be a dict with 'sessions' or an empty dict
            sessions_list = data.get('sessions', []) if isinstance(data, dict) else []
            migrated_data = {
                'projects': [],
                'sessions': sessions_list
            }
            data = migrated_data
            # We don't save it back immediately, but the next save will fix it.

        # Ensure both keys exist even if the file was just empty
        if 'projects' not in data:
            data['projects'] = []
        if 'sessions' not in data:
            data['sessions'] = []

        journal_cache[filepath] = data
        journal_mtime[filepath] = last_modified
        return data
    except Exception as e:
        print(f"❌ ERROR: Failed to load journal '{filename}': {e}")
        return {"projects": [], "sessions": []}


def save_journal(username, journal_data):
    """
    Saves journal data safely (schema guard + backup + atomic write + DB sync).
    """
    if (not isinstance(journal_data, dict) or
            "sessions" not in journal_data or
            not isinstance(journal_data.get("sessions"), list)):
        print(f"❌ CRITICAL SAVE ABORTED for journal of user '{username}': invalid/malformed journal data.")
        return

    filename = "journal_default.yaml" if SINGLE_USER_MODE else f"journal_{username}.yaml"
    filepath = os.path.join(CONFIG_DIR, filename)

    try:
        # --- Start of combined operation block ---

        # 1. Perform file operations (backup + atomic write)
        with _FileLock(filepath):
            if os.path.exists(filepath):
                _backup_with_rotation(filepath, keep=10)
            _atomic_write_yaml(filepath, journal_data)

        # 2. If file write succeeded, print success and sync DB
        print(f" Journal saved to '{filename}' successfully (atomic).")

        # 3. Keep DB in sync with freshly saved YAMLs
        try:  # Separate try-except specifically for the DB sync part
            if username:
                import_from_existing_yaml(username, clear_existing=False)
                print(f"[IMPORT→DB] Synced DB from YAMLs for user '{username}' after journal save.")
        except Exception as sync_e:
            # Log a warning if ONLY the sync fails, but the file was saved
            print(f"[IMPORT→DB] WARNING: File saved, but could not sync DB from YAMLs after journal save: {sync_e}")
            # Optionally: re-raise sync_e or flash a message if you want the user to know

    except Exception as e:
        # This catches errors from file operations (_FileLock, _backup, _atomic_write)
        print(f"❌ ERROR: Failed to save journal '{filename}' or sync DB: {e}")
        # Note: We don't print the "successfully saved" message if we end up here.

def sort_rigs_list(rigs_list, sort_key='name-asc'):
    """Sorts a list of rig dictionaries based on a given key."""
    key, direction = sort_key.split('-')

    def get_sort_value(rig):
        # This maps the sort key from the frontend to the data key in the rig dictionary
        if key == 'name':
            return (rig.get('rig_name') or '').lower()
        if key == 'fl':
            return rig.get('effective_focal_length')
        if key == 'fr':
            return rig.get('f_ratio')
        if key == 'scale':
            return rig.get('image_scale')
        if key == 'fovw':
            return rig.get('fov_w_arcmin')
        # Add a fallback for 'recent' or any other key
        return rig.get('rig_id') # A stable fallback sort

    # Use a lambda with a try-except to handle non-numeric or missing values gracefully
    # This makes the sorting robust against incomplete rig data.
    rigs_list.sort(key=lambda r: get_sort_value(r) if get_sort_value(r) is not None else float('inf'),
                   reverse=(direction == 'desc'))
    return rigs_list

def migrate_journal_data():
    """
    Runs once on startup to find and update old journal entries that are missing
    the pre-calculated integration time.
    """
    print("[MIGRATION] Checking for old journal entries to update...")
    search_path = os.path.join(CONFIG_DIR, 'journal_*.yaml')
    default_path = os.path.join(CONFIG_DIR, 'journal_default.yaml')
    journal_files = glob.glob(search_path) + glob.glob(default_path)

    for journal_file in journal_files:
        try:
            with open(journal_file, 'r', encoding='utf-8') as f:
                journal_data = yaml.safe_load(f)

            if not journal_data or 'sessions' not in journal_data:
                continue

            made_changes = False
            for session in journal_data['sessions']:
                # Check if the key is missing from the session
                if 'calculated_integration_time_minutes' not in session:
                    made_changes = True  # Mark that we need to save this file
                    total_integration_seconds = 0
                    has_any_integration_data = False

                    try:
                        num_subs = int(str(session.get('number_of_subs_light', 0)))
                        exp_time = int(str(session.get('exposure_time_per_sub_sec', 0)))
                        if num_subs > 0 and exp_time > 0:
                            total_integration_seconds += (num_subs * exp_time)
                            has_any_integration_data = True
                    except (ValueError, TypeError):
                        pass

                    mono_filters = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']
                    for filt in mono_filters:
                        try:
                            subs_val = int(str(session.get(f'filter_{filt}_subs', 0)))
                            exp_val = int(str(session.get(f'filter_{filt}_exposure_sec', 0)))
                            if subs_val > 0 and exp_val > 0:
                                total_integration_seconds += (subs_val * exp_val)
                                has_any_integration_data = True
                        except (ValueError, TypeError):
                            pass

                    if has_any_integration_data:
                        session['calculated_integration_time_minutes'] = round(total_integration_seconds / 60.0, 0)
                    else:
                        session['calculated_integration_time_minutes'] = 'N/A'

            if made_changes:
                print(f"    -> Found and updated entries in {journal_file}. Saving changes.")
                # We can't know the username from the filename alone in multi-user mode,
                # but we can save the file directly. This is safe.
                with open(journal_file, 'w', encoding='utf-8') as f:
                    yaml.dump(journal_data, f, sort_keys=False, allow_unicode=True, indent=2)

        except Exception as e:
            print(f"    -> ERROR: Could not process {journal_file}: {e}")
    print("[MIGRATION] Check complete.")

def get_hybrid_weather_forecast(lat, lon):
    """
    Fetch 8-day 'meteo' (base) and optionally merge 3-day 'astro'.
    On failure:
      - return the last successful cached result (if any)
      - rate-limit error logs to avoid console spam
    """
    cache_key = f"hybrid_{lat}_{lon}"
    now = datetime.now(UTC)
    entry = weather_cache.get(cache_key) or {}
    last_good = entry.get('data')
    last_err_ts = entry.get('last_err_ts')

    # serve unexpired cache fast-path
    if entry and 'expires' in entry and now < entry['expires']:
        return entry['data']

    def _update_cache_ok(data, ttl_hours=3):
        weather_cache[cache_key] = {'data': data, 'expires': now + timedelta(hours=ttl_hours), 'last_err_ts': None}

    def _rate_limited_error(msg):
        nonlocal last_err_ts
        if not last_err_ts or (now - last_err_ts) > timedelta(minutes=15):
            print(msg)
            weather_cache.setdefault(cache_key, {})['last_err_ts'] = now

    # --- Try base 'meteo' ---
    try:
        meteo_url = f"http://www.7timer.info/bin/api.pl?lon={lon}&lat={lat}&product=meteo&output=json&unit=metric"
        resp = requests.get(meteo_url, timeout=10)
        resp.raise_for_status()
        meteo_data = resp.json()
        # Optional: quiet success log
        # print("DEBUG: Successfully fetched 8-day 'meteo' forecast.")
    except Exception as e:
        _rate_limited_error(f"ERROR: Could not fetch 'meteo' weather data: {e}")
        # print(f"       Response text from server: {resp.text[:500]}")
        return last_good or None

    if not meteo_data or 'dataseries' not in meteo_data:
        _rate_limited_error("DEBUG: Base 'meteo' forecast failed. Returning last good (if any).")
        # print(f"       Response text from server: {resp.text[:500]}")
        return last_good or None

    # index for merge
    base = {blk.get('timepoint'): dict(blk) for blk in meteo_data['dataseries']
            if isinstance(blk, dict) and 'timepoint' in blk}

    # --- Optional 'astro' merge ---
    try:
        astro_url = f"http://www.7timer.info/bin/api.pl?lon={lon}&lat={lat}&product=astro&output=json&unit=metric"
        resp = requests.get(astro_url, timeout=10)
        if resp.ok:
            astro_data = resp.json()
            for ablk in astro_data.get('dataseries', []):
                tp = ablk.get('timepoint')
                if tp in base:
                    base[tp].update(ablk)
            # Optional: quiet success log
            # print("DEBUG: Successfully fetched 3-day 'astro' forecast for enhancement.")
    except Exception as e:
        _rate_limited_error(f"WARNING: 'astro' merge skipped: {e}")

    final = {'init': meteo_data.get('init'), 'dataseries': list(base.values())}
    _update_cache_ok(final, ttl_hours=3)
    return final

def generate_session_id():
    """Generates a unique session ID."""
    return uuid.uuid4().hex


def check_for_updates():
    """
    Checks GitHub for the latest release version in a background thread.
    """
    global LATEST_VERSION_INFO
    owner = "mrantonSG"
    repo = "nova_DSO_tracker"

    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    print(f"[VERSION CHECK] Fetching latest release info from {url}")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes

        data = response.json()
        latest_version = data.get("tag_name", "").lower().lstrip('v') # Get version and remove leading 'v'
        current_version = APP_VERSION

        if latest_version and latest_version != current_version:
            print(f"[VERSION CHECK] New version found: {latest_version}")
            LATEST_VERSION_INFO = {
                "new_version": latest_version,
                "url": data.get("html_url")
            }
        else:
            print("[VERSION CHECK] You are running the latest version.")

    except requests.exceptions.RequestException as e:
        print(f"❌ [VERSION CHECK] Could not connect to GitHub API: {e}")
    except Exception as e:
        print(f"❌ [VERSION CHECK] An unexpected error occurred: {e}")


def trigger_outlook_update_for_user(username):
    """
    Loads a user's config and starts Outlook cache workers for all their locations.
    """
    print(f"[TRIGGER] Firing Outlook cache update for user '{username}' due to a project note change.")
    try:
        user_cfg = load_user_config(username)
        locations = user_cfg.get('locations', {})
        for loc_name in locations.keys():
            # We don't need to check for staleness here, we want to force the update.
            # print(f"    -> Starting Outlook worker for location '{loc_name}'.")
            thread = threading.Thread(target=update_outlook_cache, args=(username, loc_name, user_cfg.copy(), g.sampling_interval))
            thread.start()
    except Exception as e:
        print(f"❌ ERROR: Failed to trigger background Outlook update: {e}")

def trigger_startup_cache_workers():
    """
    REVISED FOR DATABASE: Gets users from the DB to warm caches for ACTIVE locations only.
    """
    print("[STARTUP] Checking all caches for freshness...")

    # We need an application context to talk to the database
    with app.app_context():
        if SINGLE_USER_MODE:
            usernames_to_check = ["default"]
        else:
            # Pull usernames from the unified DB (DbUser)
            try:
                _db = get_db()
                try:
                    # Query only active users from the DbUser table
                    all_db_users = _db.query(DbUser).filter(DbUser.active == True).all()
                    usernames_to_check = [u.username for u in all_db_users]
                finally:
                    _db.close()
            except Exception as e:
                print(f"⚠️ [STARTUP] Could not query unified DB users. Error: {e}")
                usernames_to_check = [] # Fallback to empty list on error

        # Prepare tasks only for active locations
        all_tasks = []
        for username in set(usernames_to_check):
            try:
                print(f"--- Preparing tasks for user: {username} ---")
                # Build the user's config dictionary directly from the database
                config = build_user_config_from_db(username)
                if not config or not config.get("locations"):
                    print(f"    -> No locations in DB for user '{username}', skipping.")
                    continue

                locations = config.get("locations", {})
                default_location_name = config.get("default_location")

                # --- NEW FILTERING LOGIC ---
                active_location_names = []
                default_active_location = None
                for loc_name, loc_details in locations.items():
                    # Use .get('active', True) to default to active if the flag isn't set (older configs)
                    # We check the 'active' flag from the config dict built from the DB
                    if loc_details.get('active', True):
                        active_location_names.append(loc_name)
                        if loc_name == default_location_name:
                            default_active_location = loc_name
                # --- END NEW FILTERING LOGIC ---

                if not active_location_names:
                    print(f"    -> No ACTIVE locations found for user '{username}', skipping cache warming.")
                    continue

                # Prioritize the default location if it's active
                if default_active_location:
                    # Add the default location first
                    all_tasks.insert(0, (username, default_active_location, config.copy()))
                    # Add remaining active locations
                    for loc_name in active_location_names:
                        if loc_name != default_active_location:
                            all_tasks.append((username, loc_name, config.copy()))
                else:
                    # If default isn't active (or doesn't exist), just add all active ones
                     for loc_name in active_location_names:
                         all_tasks.append((username, loc_name, config.copy()))

            except Exception as e:
                print(f"❌ [STARTUP] ERROR: Could not prepare startup tasks for user '{username}': {e}")
                traceback.print_exc() # Print traceback for detailed debugging

        # Function to run tasks sequentially with a delay
        def run_tasks_sequentially(tasks):
            if not tasks:
                print("[STARTUP] All cache workers have completed.")
                return

            username, loc_name, cfg = tasks.pop(0)
            print(f"[STARTUP] Now processing task for user '{username}' at location '{loc_name}'.")

            # Determine the sampling interval to pass to the thread
            sampling_interval = 15 # Default
            if SINGLE_USER_MODE:
                # In single-user mode, get it from the user's config (UiPref blob)
                sampling_interval = cfg.get('sampling_interval_minutes', 15)
            else:
                # In multi-user mode, get it from environment variables (or default)
                sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))

            # Start the cache warming thread for the main data cache
            # This thread will subsequently trigger the outlook cache update
            worker_thread = threading.Thread(target=warm_main_cache, args=(username, loc_name, cfg, sampling_interval))
            worker_thread.start()

            # Schedule the next task after a delay (e.g., 15 seconds)
            threading.Timer(15.0, run_tasks_sequentially, args=[tasks]).start()

        print(f"[STARTUP] Found a total of {len(all_tasks)} active user/location tasks to process.")
        if all_tasks:
            # Start the sequential processing
            run_tasks_sequentially(all_tasks)
        else:
            print("[STARTUP] No active locations found across all users. No cache warming needed.")

def update_outlook_cache(username, location_name, user_config, sampling_interval):
    """
    NEW LOGIC: Finds ALL good imaging opportunities for PROJECT objects only
    and saves them sorted by date.
    """
    with app.app_context():
        # Create a unique key for the status dictionary
        status_key = f"{username}_{location_name}"

        # print(f"[OUTLOOK WORKER] Starting for user '{username}' at location '{location_name}'.")
        cache_worker_status[status_key] = "running"
        cache_filename = os.path.join(CACHE_DIR,
                                      f"outlook_cache_{username}_{location_name.lower().replace(' ', '_')}.json")

        try:
            g.user_config = user_config
            g.locations = user_config.get("locations", {})
            loc_cfg = g.locations.get(location_name, {})
            g.lat, g.lon, g.tz_name = loc_cfg.get("lat"), loc_cfg.get("lon"), loc_cfg.get("timezone", "UTC")
            g.objects_list = g.user_config.get("objects", [])

            lat, lon, tz_name = g.lat, g.lon, g.tz_name
            altitude_threshold = user_config.get("altitude_threshold", 20)
            if not all([lat, lon, tz_name]): raise ValueError(f"Missing lat/lon/tz for '{location_name}'")

            criteria = get_imaging_criteria()

            all_objects_from_config = user_config.get("objects", [])
            project_objects = [
                obj for obj in all_objects_from_config
                if obj.get("ActiveProject")
            ]
            # print(f"[OUTLOOK WORKER] Found {len(project_objects)} objects with active projects for user '{username}'.")

            all_good_opportunities = []
            local_tz = pytz.timezone(tz_name)
            start_date = datetime.now(local_tz).date()
            dates_to_check = [start_date + timedelta(days=i) for i in range(30)]

            for obj_config_entry in project_objects:
                try:
                    time.sleep(0.01)
                    object_name_from_config = obj_config_entry.get("Object")
                    if not object_name_from_config: continue

                    obj_details = get_ra_dec(object_name_from_config)
                    object_name, ra, dec = obj_details.get("Object"), obj_details.get("RA (hours)"), obj_details.get(
                        "DEC (degrees)")
                    if not all([object_name, ra, dec]): continue

                    # --- NEW: Loop through dates and collect ALL good nights ---
                    for d in dates_to_check:
                        date_str = d.strftime('%Y-%m-%d')
                        # Respect per-azimuth horizon mask (houses/trees etc.)
                        try:
                            horizon_mask = (g.locations.get(location_name, {}).get("horizon_mask")
                                            if isinstance(g.locations, dict) else None)
                        except Exception:
                            horizon_mask = None
                        obs_duration, max_altitude, _, _ = calculate_observable_duration_vectorized(
                            ra, dec, lat, lon,
                            date_str, tz_name,
                            altitude_threshold, sampling_interval,
                            horizon_mask=horizon_mask
                        )

                        if max_altitude < criteria["min_max_altitude"] or (obs_duration.total_seconds() / 60) < \
                                criteria["min_observable_minutes"]:
                            continue

                        # Perform scoring (same as before)
                        moon_phase = ephem.Moon(
                            local_tz.localize(datetime.combine(d, datetime.now().time())).astimezone(pytz.utc)).phase
                        sun_events = calculate_sun_events_cached(date_str, tz_name, lat, lon)
                        dusk = sun_events.get("astronomical_dusk", "20:00")
                        dusk_dt = local_tz.localize(datetime.combine(d, datetime.strptime(dusk, "%H:%M").time()))
                        location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
                        frame = AltAz(obstime=Time(dusk_dt.astimezone(pytz.utc)), location=location_obj)
                        obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                        moon_coord = get_body('moon', Time(dusk_dt.astimezone(pytz.utc)), location=location_obj)
                        separation = obj_coord.transform_to(frame).separation(moon_coord.transform_to(frame)).deg
                        score_alt = min((max_altitude - 20) / 70, 1) if max_altitude > 20 else 0
                        score_duration = min(obs_duration.total_seconds() / (3600 * 12), 1)
                        score_moon_illum = 1 - min(moon_phase / 100, 1)
                        score_moon_sep_dynamic = (1 - (moon_phase / 100)) + (moon_phase / 100) * min(separation / 180,
                                                                                                     1)
                        composite_score = 100 * (
                                    0.20 * score_alt + 0.15 * score_duration + 0.45 * score_moon_illum + 0.20 * score_moon_sep_dynamic)

                        # --- NEW: If score is good, add it to the list ---
                        if composite_score > 75:  # Set a threshold for what constitutes a "good" opportunity
                            stars = int(round((composite_score / 100) * 4)) + 1
                            good_night_opportunity = {
                                "object_name": object_name,
                                "common_name": obj_details.get("Common Name", object_name),
                                "date": date_str,  # Note the key is now 'date'
                                "score": composite_score,
                                "rating": "★" * stars + "☆" * (5 - stars),
                                "rating_num": stars,
                                "max_alt": round(max_altitude, 1),
                                "obs_dur": int(obs_duration.total_seconds() / 60),
                                "project": obj_config_entry.get("Project", "none"),
                                "type": obj_details.get("Type", "N/A"),
                                "constellation": obj_details.get("Constellation", "N/A"),
                                "magnitude": obj_details.get("Magnitude", "N/A"),
                                "size": obj_details.get("Size", "N/A"),
                                "sb": obj_details.get("SB", "N/A")
                            }
                            all_good_opportunities.append(good_night_opportunity)

                except Exception as e:
                    import traceback
                    print(
                        f"[OUTLOOK WORKER] WARNING: Skipping object '{obj_config_entry.get('Object', 'Unknown')}' due to an error: {e}")
                    traceback.print_exc()
                    continue

            # --- NEW: Sort the final list of all opportunities by date ---
            opportunities_sorted_by_date = sorted(all_good_opportunities, key=lambda x: x['date'])

            cache_content = {
                "metadata": {"last_successful_run_utc": datetime.now(pytz.utc).isoformat(), "location": location_name, "user": username},
                "opportunities": opportunities_sorted_by_date
            }

            with open(cache_filename, 'w') as f:
                json.dump(cache_content, f)
            # print(f"[OUTLOOK WORKER] Successfully updated cache file: {cache_filename}")
            cache_worker_status[status_key] = "complete"

        except Exception as e:
            import traceback
            print(f"❌ [OUTLOOK WORKER] FATAL ERROR for user '{username}' at location '{location_name}': {e}")
            traceback.print_exc()
            cache_worker_status[status_key] = "error"

def warm_main_cache(username, location_name, user_config, sampling_interval):
    """
    Warms the main data cache on startup and then triggers the Outlook cache
    update for the same location.
    """
    # print(f"[CACHE WARMER] Starting for main data at location '{location_name}'.")
    try:
        local_tz = pytz.timezone(user_config["locations"][location_name]["timezone"])
        observing_date_for_calcs = datetime.now(local_tz) - timedelta(hours=12)
        local_date = observing_date_for_calcs.strftime('%Y-%m-%d')

        for obj_entry in user_config.get("objects", []):
            time.sleep(0.01)
            obj_name = obj_entry.get("Object")
            if not obj_name: continue

            cache_key = f"{obj_name.lower()}_{local_date}_{location_name.lower()}"
            if cache_key in nightly_curves_cache:
                continue

            ra = float(obj_entry.get("RA", 0))
            dec = float(obj_entry.get("DEC", 0))
            lat = float(user_config["locations"][location_name]["lat"])
            lon = float(user_config["locations"][location_name]["lon"])
            tz_name = user_config["locations"][location_name]["timezone"]

            altitude_threshold = user_config.get("altitude_threshold", 20)

            times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)
            location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
            altaz_frame = AltAz(obstime=times_utc, location=location)
            altitudes = sky_coord.transform_to(altaz_frame).alt.deg
            azimuths = sky_coord.transform_to(altaz_frame).az.deg
            transit_time = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
            # Apply horizon mask from location (if any)
            try:
                horizon_mask = user_config.get("locations", {}).get(location_name, {}).get("horizon_mask")
            except Exception:
                horizon_mask = None
            obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
                ra, dec, lat, lon,
                local_date, tz_name,
                altitude_threshold, sampling_interval,
                horizon_mask=horizon_mask
            )
            fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)
            alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)
            is_obstructed_at_11pm = False
            if horizon_mask and isinstance(horizon_mask, list) and len(horizon_mask) > 1:
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

        # print(f"[CACHE WARMER] Main data cache warming complete for '{location_name}'.")

        # --- NEW: Now, sequentially trigger the Outlook worker for this location ---
        # print(f"[CACHE WARMER] Now triggering Outlook cache update for '{location_name}'.")
        cache_filename = os.path.join(CACHE_DIR, f"outlook_cache_{username}_{location_name.lower().replace(' ', '_')}.json")


        needs_update = False
        if not os.path.exists(cache_filename):
            needs_update = True
            print(f"    -> Outlook cache for '{location_name}' not found. Triggering update.")
        else:
            try:
                with open(cache_filename, 'r') as f:
                    data = json.load(f)
                last_run_str = data.get("metadata", {}).get("last_successful_run_utc")
                if not last_run_str or (
                        datetime.now(pytz.utc) - datetime.fromisoformat(last_run_str)).total_seconds() > 86400:
                    needs_update = True
                    print(f"    -> Outlook cache for '{location_name}' is stale. Triggering update.")
                else:
                    print(f"    -> Outlook cache for '{location_name}' is already fresh. Skipping.")
            except (json.JSONDecodeError, KeyError):
                needs_update = True
                print(f"    -> Outlook cache for '{location_name}' is corrupted. Triggering update.")

        if needs_update:
            # Pass username to the thread's target function
            thread = threading.Thread(target=update_outlook_cache,
                                      args=(username, location_name, user_config.copy(), sampling_interval))
            thread.start()

    except Exception as e:
        import traceback
        print(f"❌ [CACHE WARMER] FATAL ERROR during cache warming for '{location_name}': {e}")
        traceback.print_exc()

def sort_rigs(rigs, sort_key: str):
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

# --- Anonymous telemetry helpers ---
def is_docker_env():
    try:
        if os.path.exists('/.dockerenv'):
            return True
        with open('/proc/1/cgroup', 'rt') as f:
            s = f.read()
            return 'docker' in s or 'kubepods' in s
    except Exception:
        return False

def ensure_instance_id(user_config):
    """Return (instance_id, enabled) without mutating YAML; ID comes from .env."""
    tcfg = user_config.setdefault('telemetry', {})
    enabled = tcfg.get('enabled', True)
    env_id = os.environ.get('INSTANCE_ID')
    if not env_id:
        env_id = secrets.token_hex(16)
    return env_id, enabled

def telemetry_should_send(state_dir: Path) -> bool:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        stamp = state_dir / 'telemetry_last.json'
        if not stamp.exists():
            return True
        data = json.loads(stamp.read_text())
        last = float(data.get('ts', 0))
        return (time.time() - last) > 24*60*60
    except Exception:
        return False

def telemetry_mark_sent(state_dir: Path):
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / 'telemetry_last.json').write_text(json.dumps({'ts': time.time()}))
    except Exception:
        pass

def build_telemetry_payload(user_config, browser_user_agent: str = ''):
    # --- Add anonymized counts (numbers only, never contents) ---
    cfg = user_config or {}
    def _len_any(x):
        if isinstance(x, dict):
            return len(x)
        if isinstance(x, list):
            return len(x)
        # support sets/tuples just in case
        try:
            return len(x)
        except Exception:
            return 0

    def pick_first(*candidates):
        for c in candidates:
            if c is not None and c != {} and c != []:
                return c
        return None

    objects_count = _len_any(cfg.get("objects"))

    # Rigs: prefer canonical rig file used by the UI; fall back to possible in-config locations
    try:
        username_eff = "default" if SINGLE_USER_MODE else (
            current_user.username if getattr(current_user, "is_authenticated", False) else "default"
        )
        rd = rig_config.load_rig_config(username_eff, SINGLE_USER_MODE) or {}
        rigs_count = _len_any(rd.get("rigs"))
    except Exception:
        # Fallbacks if rig config couldn't be loaded
        rigs_container = pick_first(
            cfg.get("rigs"),
            cfg.get("rig_list"),
            cfg.get("available_rigs"),
            (cfg.get("equipment") or {}).get("rigs"),
            (cfg.get("user") or {}).get("rigs"),
        )
        rigs_count = _len_any(rigs_container)

    # Locations: use container variants resolved above
    locations_container = pick_first(
        cfg.get("locations"),
        cfg.get("sites"),
        (cfg.get("user") or {}).get("locations"),
        (cfg.get("observing") or {}).get("locations"),
    )
    locations_count = _len_any(locations_container)
    # Journals: load via canonical loader (journal lives in nova.py), fallback to config keys if needed
    try:
        username_eff = "default" if SINGLE_USER_MODE else (
            current_user.username if getattr(current_user, "is_authenticated", False) else "default"
        )
        jd = load_journal(username_eff) or {}
        sessions = jd.get("sessions") or []
        journals_count = _len_any(sessions)
    except Exception:
        # Fallback for older in-config layouts
        journals_count = _len_any(cfg.get("journals")) or _len_any(cfg.get("journal_entries"))
    instance_id, enabled = ensure_instance_id(user_config)
    mode = 'single' if SINGLE_USER_MODE else 'multi'
    return {
        'instance_id': instance_id,
        'app_version': APP_VERSION,
        'os': platform.platform(),
        'python_version': platform.python_version(),
        'is_docker': bool(is_docker_env()),
        'mode': mode,
        'browser_user_agent': browser_user_agent or '',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        "objects_count": objects_count,
        "rigs_count": rigs_count,
        "locations_count": locations_count,
        "journals_count": journals_count,
    }

def send_telemetry_async(user_config, browser_user_agent: str = '', force: bool = False):
    """Non-blocking send; obeys enable flag and once-per-24h rule."""
    try:
        tcfg = user_config.get('telemetry', {})
        enabled_flag = tcfg.get('enabled', True)
        # print(f"[TELEMETRY] send_telemetry_async called (force={force}, enabled={enabled_flag})")

        if not enabled_flag:
            # print("[TELEMETRY] Telemetry disabled; skipping send.")
            TELEMETRY_DEBUG_STATE['last_error'] = "disabled"
            return

        # Prefer env var, else fallback to user_config's telemetry.endpoint
        endpoint = (os.environ.get('NOVA_TELEMETRY_ENDPOINT', '').strip()
                    or (tcfg.get('endpoint', '') if isinstance(tcfg, dict) else ''))
        TELEMETRY_DEBUG_STATE['endpoint'] = endpoint
        if not endpoint:
            TELEMETRY_DEBUG_STATE['last_error'] = "no-endpoint"
            return

        state_dir = Path(os.environ.get('NOVA_STATE_DIR', CACHE_DIR))
        if (not force) and (not telemetry_should_send(state_dir)):
            # print("[TELEMETRY] Throttled (within 24h); skipping.")
            TELEMETRY_DEBUG_STATE['last_error'] = "throttled"
            return

        # --- NEW: Resolve UA if not explicitly passed ---
        try:
            if not browser_user_agent:
                if has_request_context():
                    browser_user_agent = request.headers.get("User-Agent", "") or ""
                if not browser_user_agent:
                    # Fallback to cached UA captured on a real HTML request
                    browser_user_agent = current_app.config.get("_LAST_UA", "") or ""
        except Exception:
            # Never fail because of UA resolution
            pass

        payload = build_telemetry_payload(user_config, browser_user_agent)

        def _worker():
            try:
                # print("[TELEMETRY] Sending to:", endpoint)
                resp = requests.post(endpoint, json=payload, timeout=5)
                TELEMETRY_DEBUG_STATE['last_result'] = f"HTTP {getattr(resp, 'status_code', 'unknown')}"
                TELEMETRY_DEBUG_STATE['last_error'] = None
                TELEMETRY_DEBUG_STATE['last_ts'] = datetime.now(timezone.utc).isoformat()
                # print("[TELEMETRY] OK:", TELEMETRY_DEBUG_STATE['last_result'])
            except Exception as e:
                TELEMETRY_DEBUG_STATE['last_result'] = None
                TELEMETRY_DEBUG_STATE['last_error'] = str(e)
                TELEMETRY_DEBUG_STATE['last_ts'] = datetime.now(timezone.utc).isoformat()
                # print("[TELEMETRY] ERROR:", e)
            finally:
                telemetry_mark_sent(state_dir)

        TELEMETRY_DEBUG_STATE['last_payload'] = payload
        threading.Thread(target=_worker, daemon=True).start()
    except Exception as e:
        # print("[TELEMETRY] Outer exception:", e)
        TELEMETRY_DEBUG_STATE['last_error'] = str(e)

# --- Telemetry startup + daily scheduler ---
def _start_telemetry_scheduler_once():
    """On first request after (re)start: send telemetry once, then schedule daily pings."""
    if _telemetry_startup_once.is_set():
        return
    _telemetry_startup_once.set()
    try:
        username = "default" if SINGLE_USER_MODE else "default"
        try:
            cfg = load_user_config(username)
        except Exception:
            cfg = {}
        # Send immediately on restart (explicitly allowed)
        # print("[TELEMETRY] Startup ping: sending now (force=True)")
        send_telemetry_async(cfg, browser_user_agent='', force=True)

        # Background daily scheduler (respects 24h guard)
        def _daily_loop():
            while True:
                try:
                    time.sleep(24 * 60 * 60)
                    try:
                        daily_cfg = load_user_config(username)
                    except Exception:
                        daily_cfg = cfg or {}
                    # print("[TELEMETRY] Daily ping: attempting send (force=False)")
                    send_telemetry_async(daily_cfg, browser_user_agent='', force=False)
                except Exception:
                    # Keep the loop alive even if something odd happens
                    pass

        threading.Thread(target=_daily_loop, daemon=True).start()
    except Exception as e:
        print(f"[TELEMETRY] Scheduler init error: {e}")

def to_yaml_filter(data):
    """Jinja2 filter to convert a Python object to a YAML string for form display."""
    if data is None:
        return ''
    try:
        # Dumps to a string, flow style makes it look like "- [0, 35]"
        return yaml.dump(data, default_flow_style=None, indent=2, sort_keys=False).strip()
    except Exception:
        return ''

@app.before_request
def _telemetry_bootstrap_hook():
    # Ensure the once-per-process startup scheduler is kicked off
    _start_telemetry_scheduler_once()

    # Compute routing flags once
    try:
        is_get = request.method == "GET"
        accepts_html = "text/html" in (request.headers.get("Accept", "") or "")
        is_static = request.path.startswith("/static/")
        is_telemetry = request.path.startswith("/telemetry/")
    except Exception:
        # If anything odd happens, just don't do telemetry here
        return

    # 1) Always cache last seen UA on real HTML navigations
    if is_get and accepts_html and not is_static and not is_telemetry:
        try:
            current_app.config["_LAST_UA"] = request.headers.get("User-Agent", "") or ""
        except Exception:
            pass

    # 2) Only once per process, trigger a normal (throttled) send that includes UA
    try:
        if not current_app.config.get("_UA_BOOTSTRAP_SENT", False):
            if is_get and accepts_html and not is_static and not is_telemetry:
                ua = current_app.config.get("_LAST_UA", "")  # use the cached UA

                # Resolve a username to load config for telemetry enabled flag, etc.
                if SINGLE_USER_MODE:
                    username = "default"
                else:
                    username = current_user.username if getattr(current_user, "is_authenticated", False) else "guest_user"

                try:
                    cfg = g.user_config if hasattr(g, "user_config") else load_user_config(username)
                except Exception:
                    cfg = {}

                send_telemetry_async(cfg, browser_user_agent=ua, force=False)

                current_app.config["_UA_BOOTSTRAP_SENT"] = True
    except Exception:
        # Never let telemetry issues affect page handling
        pass

# If this is a fresh first run (we just created .env), trigger telemetry scheduler shortly after startup
if FIRST_RUN_ENV_CREATED:
    def _telemetry_first_run_timer():
        try:
            _start_telemetry_scheduler_once()
        except Exception as _e:
            print(f"[TELEMETRY] first-run timer init failed: {_e}")
    threading.Timer(1.0, _telemetry_first_run_timer).start()

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

# --- Telemetry diagnostics route ---
@app.route('/telemetry/debug', methods=['GET'])
def telemetry_debug():
    # Report current telemetry config and last attempt
    try:
        username = "default" if SINGLE_USER_MODE else (current_user.username if current_user.is_authenticated else "guest_user")
    except Exception:
        username = "default"
    try:
        cfg = g.user_config if hasattr(g, 'user_config') else load_user_config(username)
    except Exception:
        cfg = {}
    enabled = bool(cfg.get('telemetry', {}).get('enabled', True))
    return jsonify({
        'enabled': enabled,
        'endpoint': TELEMETRY_DEBUG_STATE.get('endpoint'),
        'last_payload': TELEMETRY_DEBUG_STATE.get('last_payload'),
        'last_result': TELEMETRY_DEBUG_STATE.get('last_result'),
        'last_error': TELEMETRY_DEBUG_STATE.get('last_error'),
        'last_ts': TELEMETRY_DEBUG_STATE.get('last_ts')
    })

@app.route('/get_outlook_data')
def get_outlook_data():
    # --- NEW: Check for guest user first ---
    if hasattr(g, 'is_guest') and g.is_guest:
        # Guests have no projects, so their outlook is always empty.
        return jsonify({"status": "complete", "results": []})

    # --- Original logic for logged-in users continues below ---
    if SINGLE_USER_MODE:
        username = "default"
    elif current_user.is_authenticated:
        username = current_user.username
    else:
        # Handle cases where no user is logged in in multi-user mode
        return jsonify({"status": "error", "message": "User not authenticated"}), 401

    location_name = g.selected_location
    cache_filename = os.path.join(CACHE_DIR, f"outlook_cache_{username}_{location_name.lower().replace(' ', '_')}.json")

    if os.path.exists(cache_filename):
        try:
            with open(cache_filename, 'r') as f:
                data = json.load(f)
            return jsonify({"status": "complete", "results": data.get("opportunities", [])})
        except (json.JSONDecodeError, IOError) as e:
            print(f"❌ ERROR: Could not read or parse outlook cache file '{cache_filename}': {e}")
            return jsonify({"status": "error", "results": []})
    else:
        # Create the unique key to check the worker's status
        status_key = f"{username}_{location_name}"
        worker_status = cache_worker_status.get(status_key, "idle")
        return jsonify({"status": worker_status, "results": []})

@app.route('/api/latest_version')
def get_latest_version():
    """An API endpoint for the frontend to check for updates."""
    return jsonify(LATEST_VERSION_INFO)

@app.route('/add_component', methods=['POST'])
@login_required
def add_component():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        form = request.form
        form_kind = form.get('component_type')
        kind_map = {
            'telescopes': 'telescope',
            'cameras': 'camera',
            'reducers_extenders': 'reducer_extender'
        }
        kind = kind_map.get(form_kind)

        if not kind:
            db.rollback()
            flash(f"Error: Unknown component type '{form_kind}'", "error")
            return redirect(url_for('config_form'))

        new_comp = Component(user_id=user.id, kind=kind, name=form.get('name'))
        if kind == 'telescope':
            new_comp.aperture_mm = float(form.get('aperture_mm'))
            new_comp.focal_length_mm = float(form.get('focal_length_mm'))
        elif kind == 'camera':
            new_comp.sensor_width_mm = float(form.get('sensor_width_mm'))
            new_comp.sensor_height_mm = float(form.get('sensor_height_mm'))
            new_comp.pixel_size_um = float(form.get('pixel_size_um'))
        elif kind == 'reducer_extender':
            new_comp.factor = float(form.get('factor'))

        db.add(new_comp)
        db.commit()
        flash(f"Component '{new_comp.name}' added successfully.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error adding component: {e}", "error")
    finally:
        db.close()
    return redirect(url_for('config_form'))

@app.route('/update_component', methods=['POST'])
@login_required
def update_component():
    db = get_db()
    try:
        form = request.form
        comp_id = int(form.get('component_id'))
        comp = db.get(Component, comp_id)

        # Security check: ensure component belongs to the current user
        if comp.user.username != ("default" if SINGLE_USER_MODE else current_user.username):
            flash("Authorization error.", "error")
            return redirect(url_for('config_form'))

        comp.name = form.get('name')
        if comp.kind == 'telescope':
            comp.aperture_mm = float(form.get('aperture_mm'))
            comp.focal_length_mm = float(form.get('focal_length_mm'))
        elif comp.kind == 'camera':
            comp.sensor_width_mm = float(form.get('sensor_width_mm'))
            comp.sensor_height_mm = float(form.get('sensor_height_mm'))
            comp.pixel_size_um = float(form.get('pixel_size_um'))
        elif comp.kind == 'reducer_extender':
            comp.factor = float(form.get('factor'))

        db.commit()
        flash(f"Component '{comp.name}' updated successfully.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error updating component: {e}", "error")
    finally:
        db.close()
    return redirect(url_for('config_form'))

@app.route('/add_rig', methods=['POST'])
@login_required
def add_rig():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        form = request.form
        rig_id = form.get('rig_id')

        tel_id = int(form.get('telescope_id'))
        cam_id = int(form.get('camera_id'))
        red_id_str = form.get('reducer_extender_id')
        red_id = int(red_id_str) if red_id_str else None

        if rig_id: # Update
            rig = db.get(Rig, int(rig_id))
            rig.rig_name = form.get('rig_name')
            rig.telescope_id, rig.camera_id, rig.reducer_extender_id = tel_id, cam_id, red_id
            flash(f"Rig '{rig.rig_name}' updated successfully.", "success")
        else: # Add
            new_rig = Rig(
                user_id=user.id, rig_name=form.get('rig_name'),
                telescope_id=tel_id, camera_id=cam_id, reducer_extender_id=red_id
            )
            db.add(new_rig)
            flash(f"Rig '{new_rig.rig_name}' created successfully.", "success")

        db.commit()
    except Exception as e:
        db.rollback()
        flash(f"Error saving rig: {e}", "error")
    finally:
        db.close()
    return redirect(url_for('config_form'))

@app.route('/delete_component', methods=['POST'])
@login_required
def delete_component():
    db = get_db()
    try:
        comp_id = int(request.form.get('component_id'))
        # Check if component is in use by any rig for this user
        in_use = db.query(Rig).filter(
            (Rig.telescope_id == comp_id) |
            (Rig.camera_id == comp_id) |
            (Rig.reducer_extender_id == comp_id)
        ).first()

        if in_use:
            flash("Cannot delete component: It is used in at least one rig.", "error")
        else:
            comp_to_delete = db.get(Component, comp_id)
            db.delete(comp_to_delete)
            db.commit()
            flash("Component deleted successfully.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error deleting component: {e}", "error")
    finally:
        db.close()
    return redirect(url_for('config_form'))

@app.route('/delete_rig', methods=['POST'])
@login_required
def delete_rig():
    db = get_db()
    try:
        rig_id = int(request.form.get('rig_id'))
        rig_to_delete = db.get(Rig, rig_id)
        db.delete(rig_to_delete)
        db.commit()
        flash("Rig deleted successfully.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error deleting rig: {e}", "error")
    finally:
        db.close()
    return redirect(url_for('config_form'))


@app.route('/set_rig_sort_preference', methods=['POST'])
@login_required
def set_rig_sort_preference():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        prefs = db.query(UiPref).filter_by(user_id=user.id).one_or_none()
        if not prefs:
            prefs = UiPref(user_id=user.id, json_blob='{}')
            db.add(prefs)

        try:
            settings = json.loads(prefs.json_blob or '{}')
        except json.JSONDecodeError:
            settings = {}

        sort_value = request.get_json(force=True).get('sort', 'name-asc')
        settings['rig_sort'] = sort_value
        prefs.json_blob = json.dumps(settings)

        db.commit()
        return jsonify({"status": "ok", "sort_order": sort_value})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/get_rig_data')
@login_required
def get_rig_data():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()

        # Fetch all components for the user
        components = db.query(Component).filter_by(user_id=user.id).all()
        telescopes = [c for c in components if c.kind == 'telescope']
        cameras = [c for c in components if c.kind == 'camera']
        reducers = [c for c in components if c.kind == 'reducer_extender']

        # Fetch all rigs and their related components eagerly
        rigs_from_db = db.query(Rig).filter_by(user_id=user.id).all()

        # Assemble the data structure for the frontend
        components_dict = {
            "telescopes": [{"id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm} for c in telescopes],
            "cameras": [{"id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm, "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um} for c in cameras],
            "reducers_extenders": [{"id": c.id, "name": c.name, "factor": c.factor} for c in reducers]
        }

        rigs_list = []
        for r in rigs_from_db:
            # Use the already fetched components to calculate rig data
            tel_obj = next((c for c in telescopes if c.id == r.telescope_id), None)
            cam_obj = next((c for c in cameras if c.id == r.camera_id), None)
            red_obj = next((c for c in reducers if c.id == r.reducer_extender_id), None)
            efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)
            fov_h = (degrees(2 * atan((cam_obj.sensor_height_mm / 2.0) / efl)) * 60.0) if cam_obj and cam_obj.sensor_height_mm and efl else None

            rigs_list.append({
                "rig_id": r.id, "rig_name": r.rig_name,
                "telescope_id": r.telescope_id, "camera_id": r.camera_id, "reducer_extender_id": r.reducer_extender_id,
                "effective_focal_length": efl, "f_ratio": f_ratio,
                "image_scale": scale, "fov_w_arcmin": fov_w, "fov_h_arcmin": fov_h
            })

        # Get sorting preference from UiPref
        prefs = db.query(UiPref).filter_by(user_id=user.id).one_or_none()
        sort_preference = 'name-asc'
        if prefs and prefs.json_blob:
            try:
                sort_preference = json.loads(prefs.json_blob).get('rig_sort', 'name-asc')
            except json.JSONDecodeError: pass

        sorted_rigs = sort_rigs(rigs_list, sort_preference)

        return jsonify({
            "components": components_dict,
            "rigs": sorted_rigs,
            "sort_preference": sort_preference
        })
    finally:
        db.close()


@app.route('/journal')
@login_required
def journal_list_view():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        # Query sessions and order by date descending
        sessions = db.query(JournalSession).filter_by(user_id=user.id).order_by(JournalSession.date_utc.desc()).all()

        # Create a lookup for object common names for efficiency
        objects_from_db = db.query(AstroObject).filter_by(user_id=user.id).all()
        object_names_lookup = {o.object_name: o.common_name for o in objects_from_db}

        # Add the common name to each session object for the template
        for s in sessions:
            s.target_common_name = object_names_lookup.get(s.object_name, s.object_name)

        return render_template('journal_list.html', journal_sessions=sessions)
    finally:
        db.close()

@app.route('/journal/add', methods=['GET', 'POST'])
@login_required
def journal_add():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()

        if request.method == 'POST':
            # --- Handle Project Creation/Selection ---
            project_id_for_session = None
            project_selection = request.form.get("project_selection")
            new_project_name = request.form.get("new_project_name", "").strip()

            if project_selection == "new_project" and new_project_name:
                new_project = Project(id=uuid.uuid4().hex, user_id=user.id, name=new_project_name)
                db.add(new_project)
                db.flush()
                project_id_for_session = new_project.id
            elif project_selection and project_selection not in ["standalone", "new_project"]:
                project_id_for_session = project_selection

            # --- Create New Session Object with ALL fields from the form ---
            new_session = JournalSession(
                user_id=user.id,
                project_id=project_id_for_session,
                date_utc=datetime.strptime(request.form.get("session_date"), '%Y-%m-%d').date(),
                object_name=request.form.get("target_object_id", "").strip(),
                notes=request.form.get("general_notes_problems_learnings", "").strip(),
                seeing_observed_fwhm=safe_float(request.form.get("seeing_observed_fwhm")),
                sky_sqm_observed=safe_float(request.form.get("sky_sqm_observed")),
                moon_illumination_session=safe_int(request.form.get("moon_illumination_session")),
                moon_angular_separation_session=safe_float(request.form.get("moon_angular_separation_session")),
                telescope_setup_notes=request.form.get("telescope_setup_notes", "").strip(),
                guiding_rms_avg_arcsec=safe_float(request.form.get("guiding_rms_avg_arcsec")),
                exposure_time_per_sub_sec=safe_int(request.form.get("exposure_time_per_sub_sec")),
                number_of_subs_light=safe_int(request.form.get("number_of_subs_light")),
                filter_used_session=request.form.get("filter_used_session", "").strip(),
                gain_setting=safe_int(request.form.get("gain_setting")),
                offset_setting=safe_int(request.form.get("offset_setting")),
                session_rating_subjective=safe_int(request.form.get("session_rating_subjective")),
                filter_L_subs=safe_int(request.form.get("filter_L_subs")), filter_L_exposure_sec=safe_int(request.form.get("filter_L_exposure_sec")),
                filter_R_subs=safe_int(request.form.get("filter_R_subs")), filter_R_exposure_sec=safe_int(request.form.get("filter_R_exposure_sec")),
                filter_G_subs=safe_int(request.form.get("filter_G_subs")), filter_G_exposure_sec=safe_int(request.form.get("filter_G_exposure_sec")),
                filter_B_subs=safe_int(request.form.get("filter_B_subs")), filter_B_exposure_sec=safe_int(request.form.get("filter_B_exposure_sec")),
                filter_Ha_subs=safe_int(request.form.get("filter_Ha_subs")), filter_Ha_exposure_sec=safe_int(request.form.get("filter_Ha_exposure_sec")),
                filter_OIII_subs=safe_int(request.form.get("filter_OIII_subs")), filter_OIII_exposure_sec=safe_int(request.form.get("filter_OIII_exposure_sec")),
                filter_SII_subs=safe_int(request.form.get("filter_SII_subs")), filter_SII_exposure_sec=safe_int(request.form.get("filter_SII_exposure_sec")),
                external_id=generate_session_id(),
                weather_notes=request.form.get("weather_notes", "").strip() or None,
                guiding_equipment=request.form.get("guiding_equipment", "").strip() or None,
                dither_details=request.form.get("dither_details", "").strip() or None,
                acquisition_software=request.form.get("acquisition_software", "").strip() or None,
                camera_temp_setpoint_c=safe_float(request.form.get("camera_temp_setpoint_c")),
                camera_temp_actual_avg_c=safe_float(request.form.get("camera_temp_actual_avg_c")),
                binning_session=request.form.get("binning_session", "").strip() or None,
                darks_strategy=request.form.get("darks_strategy", "").strip() or None,  # Added calibration
                flats_strategy=request.form.get("flats_strategy", "").strip() or None,  # Added calibration
                bias_darkflats_strategy=request.form.get("bias_darkflats_strategy", "").strip() or None
                # Added calibration
            )

            total_seconds = (new_session.number_of_subs_light or 0) * (new_session.exposure_time_per_sub_sec or 0) + \
                            (new_session.filter_L_subs or 0) * (new_session.filter_L_exposure_sec or 0) + \
                            (new_session.filter_R_subs or 0) * (new_session.filter_R_exposure_sec or 0) + \
                            (new_session.filter_G_subs or 0) * (new_session.filter_G_exposure_sec or 0) + \
                            (new_session.filter_B_subs or 0) * (new_session.filter_B_exposure_sec or 0) + \
                            (new_session.filter_Ha_subs or 0) * (new_session.filter_Ha_exposure_sec or 0) + \
                            (new_session.filter_OIII_subs or 0) * (new_session.filter_OIII_exposure_sec or 0) + \
                            (new_session.filter_SII_subs or 0) * (new_session.filter_SII_exposure_sec or 0)
            new_session.calculated_integration_time_minutes = round(total_seconds / 60.0, 1) if total_seconds > 0 else None

            db.add(new_session)
            db.flush()

            if 'session_image' in request.files:
                file = request.files['session_image']
                if file and file.filename != '' and allowed_file(file.filename):
                    file_extension = file.filename.rsplit('.', 1)[1].lower()
                    new_filename = f"{new_session.id}.{file_extension}"
                    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
                    os.makedirs(user_upload_dir, exist_ok=True)
                    file.save(os.path.join(user_upload_dir, new_filename))
                    new_session.session_image_file = new_filename

            db.commit()
            flash("New journal entry added successfully!", "success")
            return redirect(url_for('graph_dashboard', object_name=new_session.object_name, session_id=new_session.id))

        # --- GET Request Logic (this part remains the same) ---
        available_objects = db.query(AstroObject).filter_by(user_id=user.id).order_by(AstroObject.object_name).all()
        available_locations = db.query(Location).filter_by(user_id=user.id, active=True).order_by(Location.name).all()
        available_rigs = db.query(Rig).filter_by(user_id=user.id).order_by(Rig.rig_name).all()
        entry_for_form = {
            "target_object_id": request.args.get('target', ''),
            "session_date": datetime.now(pytz.timezone(g.tz_name or 'UTC')).strftime('%Y-%m-%d'),
            "location_name": g.selected_location or ''
        }
        cancel_url = url_for('index')
        if entry_for_form["target_object_id"]:
            cancel_url = url_for('graph_dashboard', object_name=entry_for_form["target_object_id"])

        return render_template('journal_form.html',
                               form_title="Add New Imaging Session",
                               form_action_url=url_for('journal_add'),
                               submit_button_text="Add Session",
                               available_objects=available_objects,
                               available_locations=available_locations,
                               available_rigs=available_rigs,
                               entry=entry_for_form,
                               cancel_url=cancel_url)
    finally:
        db.close()


@app.route('/journal/edit/<int:session_id>', methods=['GET', 'POST'])
@login_required
def journal_edit(session_id):
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        session_to_edit = db.query(JournalSession).filter_by(id=session_id, user_id=user.id).one_or_none()

        if not session_to_edit:
            flash("Journal entry not found or you do not have permission to edit it.", "error")
            return redirect(url_for('journal_list_view'))

        if request.method == 'POST':
            # --- Update ALL fields from the form ---
            session_to_edit.date_utc = datetime.strptime(request.form.get("session_date"), '%Y-%m-%d').date()
            session_to_edit.object_name = request.form.get("target_object_id", "").strip()
            session_to_edit.notes = request.form.get("general_notes_problems_learnings", "").strip()
            session_to_edit.seeing_observed_fwhm = safe_float(request.form.get("seeing_observed_fwhm"))
            session_to_edit.sky_sqm_observed = safe_float(request.form.get("sky_sqm_observed"))
            session_to_edit.moon_illumination_session = safe_int(request.form.get("moon_illumination_session"))
            session_to_edit.moon_angular_separation_session = safe_float(
                request.form.get("moon_angular_separation_session"))
            session_to_edit.telescope_setup_notes = request.form.get("telescope_setup_notes", "").strip()
            session_to_edit.guiding_rms_avg_arcsec = safe_float(request.form.get("guiding_rms_avg_arcsec"))
            session_to_edit.exposure_time_per_sub_sec = safe_int(request.form.get("exposure_time_per_sub_sec"))
            session_to_edit.number_of_subs_light = safe_int(request.form.get("number_of_subs_light"))
            session_to_edit.filter_used_session = request.form.get("filter_used_session", "").strip()
            session_to_edit.gain_setting = safe_int(request.form.get("gain_setting"))
            session_to_edit.offset_setting = safe_int(request.form.get("offset_setting"))
            session_to_edit.session_rating_subjective = safe_int(request.form.get("session_rating_subjective"))
            session_to_edit.filter_L_subs = safe_int(request.form.get("filter_L_subs"));
            session_to_edit.filter_L_exposure_sec = safe_int(request.form.get("filter_L_exposure_sec"))
            session_to_edit.filter_R_subs = safe_int(request.form.get("filter_R_subs"));
            session_to_edit.filter_R_exposure_sec = safe_int(request.form.get("filter_R_exposure_sec"))
            session_to_edit.filter_G_subs = safe_int(request.form.get("filter_G_subs"));
            session_to_edit.filter_G_exposure_sec = safe_int(request.form.get("filter_G_exposure_sec"))
            session_to_edit.filter_B_subs = safe_int(request.form.get("filter_B_subs"));
            session_to_edit.filter_B_exposure_sec = safe_int(request.form.get("filter_B_exposure_sec"))
            session_to_edit.filter_Ha_subs = safe_int(request.form.get("filter_Ha_subs"));
            session_to_edit.filter_Ha_exposure_sec = safe_int(request.form.get("filter_Ha_exposure_sec"))
            session_to_edit.filter_OIII_subs = safe_int(request.form.get("filter_OIII_subs"));
            session_to_edit.filter_OIII_exposure_sec = safe_int(request.form.get("filter_OIII_exposure_sec"))
            session_to_edit.filter_SII_subs = safe_int(request.form.get("filter_SII_subs"));
            session_to_edit.filter_SII_exposure_sec = safe_int(request.form.get("filter_SII_exposure_sec"))
            session_to_edit.weather_notes = request.form.get("weather_notes", "").strip() or None
            session_to_edit.guiding_equipment = request.form.get("guiding_equipment", "").strip() or None
            session_to_edit.dither_details = request.form.get("dither_details", "").strip() or None
            session_to_edit.acquisition_software = request.form.get("acquisition_software", "").strip() or None
            session_to_edit.camera_temp_setpoint_c = safe_float(request.form.get("camera_temp_setpoint_c"))
            session_to_edit.camera_temp_actual_avg_c = safe_float(request.form.get("camera_temp_actual_avg_c"))
            session_to_edit.binning_session = request.form.get("binning_session", "").strip() or None
            session_to_edit.darks_strategy = request.form.get("darks_strategy", "").strip() or None  # Added calibration
            session_to_edit.flats_strategy = request.form.get("flats_strategy", "").strip() or None  # Added calibration
            session_to_edit.bias_darkflats_strategy = request.form.get("bias_darkflats_strategy",
                                                                       "").strip() or None  # Added calibration

            total_seconds = (session_to_edit.number_of_subs_light or 0) * (
                        session_to_edit.exposure_time_per_sub_sec or 0) + \
                            (session_to_edit.filter_L_subs or 0) * (session_to_edit.filter_L_exposure_sec or 0) + \
                            (session_to_edit.filter_R_subs or 0) * (session_to_edit.filter_R_exposure_sec or 0) + \
                            (session_to_edit.filter_G_subs or 0) * (session_to_edit.filter_G_exposure_sec or 0) + \
                            (session_to_edit.filter_B_subs or 0) * (session_to_edit.filter_B_exposure_sec or 0) + \
                            (session_to_edit.filter_Ha_subs or 0) * (session_to_edit.filter_Ha_exposure_sec or 0) + \
                            (session_to_edit.filter_OIII_subs or 0) * (session_to_edit.filter_OIII_exposure_sec or 0) + \
                            (session_to_edit.filter_SII_subs or 0) * (session_to_edit.filter_SII_exposure_sec or 0)
            session_to_edit.calculated_integration_time_minutes = round(total_seconds / 60.0,
                                                                        1) if total_seconds > 0 else None

            if request.form.get('delete_session_image') == '1' and session_to_edit.session_image_file:
                image_path = os.path.join(UPLOAD_FOLDER, username, session_to_edit.session_image_file)
                if os.path.exists(image_path): os.remove(image_path)
                session_to_edit.session_image_file = None

            if 'session_image' in request.files:
                file = request.files['session_image']
                if file and file.filename != '' and allowed_file(file.filename):
                    file_extension = file.filename.rsplit('.', 1)[1].lower()
                    new_filename = f"{session_to_edit.id}.{file_extension}"
                    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
                    os.makedirs(user_upload_dir, exist_ok=True)
                    file.save(os.path.join(user_upload_dir, new_filename))
                    session_to_edit.session_image_file = new_filename

            db.commit()
            flash("Journal entry updated successfully!", "success")
            return redirect(
                url_for('graph_dashboard', object_name=session_to_edit.object_name, session_id=session_to_edit.id))

        # --- GET Request Logic ---
        available_objects = db.query(AstroObject).filter_by(user_id=user.id).order_by(AstroObject.object_name).all()
        available_locations = db.query(Location).filter_by(user_id=user.id, active=True).order_by(Location.name).all()
        available_rigs = db.query(Rig).filter_by(user_id=user.id).order_by(Rig.rig_name).all()

        entry_dict = {c.name: getattr(session_to_edit, c.name) for c in session_to_edit.__table__.columns}
        if isinstance(entry_dict.get('date_utc'), (datetime, date)):
            entry_dict['session_date'] = entry_dict['date_utc'].strftime('%Y-%m-%d')
        entry_dict['target_object_id'] = entry_dict.get('object_name')

        cancel_url = url_for('graph_dashboard', object_name=session_to_edit.object_name, session_id=session_to_edit.id)

        return render_template('journal_form.html',
                               form_title="Edit Imaging Session",
                               form_action_url=url_for('journal_edit', session_id=session_id),
                               submit_button_text="Save Changes",
                               entry=entry_dict,
                               available_objects=available_objects,
                               available_locations=available_locations,
                               available_rigs=available_rigs,
                               cancel_url=cancel_url)
    finally:
        db.close()

@app.route('/journal/delete/<int:session_id>', methods=['POST'])
@login_required
def journal_delete(session_id):
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        session_to_delete = db.query(JournalSession).filter_by(id=session_id, user_id=user.id).one_or_none()

        if session_to_delete:
            object_name_redirect = session_to_delete.object_name
            # Delete associated image file
            if session_to_delete.session_image_file:
                image_path = os.path.join(UPLOAD_FOLDER, username, session_to_delete.session_image_file)
                if os.path.exists(image_path):
                    os.remove(image_path)

            db.delete(session_to_delete)
            db.commit()
            flash("Journal entry deleted successfully.", "success")
            if object_name_redirect:
                return redirect(url_for('graph_dashboard', object_name=object_name_redirect))
            else:
                return redirect(url_for('journal_list_view'))
        else:
            flash("Journal entry not found or you do not have permission to delete it.", "error")
            return redirect(url_for('journal_list_view'))
    finally:
        db.close()

def _cleanup_orphan_projects(journal_data):
    """
    Scans a journal's projects and sessions, removing any project
    that has no sessions linked to it.
    """
    if 'projects' not in journal_data or 'sessions' not in journal_data:
        return journal_data  # Not a valid journal structure

    projects = journal_data.get('projects', [])
    sessions = journal_data.get('sessions', [])

    # Create a set of all project_ids that are actually being used by sessions
    project_ids_in_use = {s['project_id'] for s in sessions if s.get('project_id')}

    # Filter the projects list, keeping only those whose IDs are in use
    cleaned_projects = [p for p in projects if p.get('project_id') in project_ids_in_use]

    if len(cleaned_projects) < len(projects):
        print(f"[CLEANUP] Removed {len(projects) - len(cleaned_projects)} orphan project(s).")
        journal_data['projects'] = cleaned_projects

    return journal_data

@app.route('/journal/add_for_target/<path:object_name>', methods=['GET', 'POST'])
@login_required
def journal_add_for_target(object_name):
    if request.method == 'POST':
        # If the form is submitted, redirect the POST request to the main journal_add function
        # which already contains all the logic to process the form data.
        return redirect(url_for('journal_add'), code=307)

    # For GET requests, the original behavior is maintained.
    return redirect(url_for('journal_add', target=object_name))


# simbad sometimes needs Ids with a / between numbers. this creates a conflict with the app.
def sanitize_object_name(object_name):
    return object_name.replace("/", "-")

@app.context_processor
def inject_user_mode():
    from flask_login import current_user
    return {
        "SINGLE_USER_MODE": SINGLE_USER_MODE,
        "current_user": current_user,
        "is_guest": getattr(g, "is_guest", False)
    }

@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
    session.clear()  # Optional: reset session if needed
    flash("Logged out successfully!", "success")
    return redirect(url_for('login'))

def get_static_cache_key(obj_name, date_str, location):
    return f"{obj_name.lower()}_{date_str}_{location.lower()}"

def get_static_nightly_values(ra, dec, obj_name, local_date, fixed_time_utc_str, location, lat, lon, tz_name, alt_threshold):
    key = get_static_cache_key(obj_name, local_date, location)
    if key in static_cache:
        return static_cache[key]

    # Otherwise calculate and cache
    alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)
    transit_time = calculate_transit_time(ra, lat, lon, tz_name)
    altitude_threshold = g.user_config.get("altitude_threshold", 20)
    obs_duration, max_altitude, _obs_from, _obs_to = calculate_observable_duration_vectorized(
        ra, dec, lat, lon, local_date, tz_name, altitude_threshold
    )
    static_cache[key] = {
        "Altitude 11PM": alt_11pm,
        "Azimuth 11PM": az_11pm,
        "Transit Time": transit_time,
        "Observable Duration (min)": int(obs_duration.total_seconds() / 60),
        "Max Altitude (°)": round(max_altitude, 1) if max_altitude is not None else "N/A"
    }
    return static_cache[key]

@app.route('/trigger_update', methods=['POST'])
def trigger_update():
    try:
        script_path = os.path.join(os.path.dirname(__file__), 'updater.py')
        subprocess.Popen([sys.executable, script_path])
        print("Exiting current app to allow updater to restart it...")
        sys.exit(0)  # Force exit without cleanup
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


def load_user_config(username):
    """
    Loads user configuration from a YAML file.
    - Uses caching for performance.
    - If a user's config is not found in multi-user mode, it creates one
      by copying the default template.
    - If the file contains unsafe NumPy tags, it will automatically repair it.
    """
    global config_cache, config_mtime
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{username}.yaml"

    filepath = os.path.join(CONFIG_DIR, filename)

    # --- NEW: Create config from template if it doesn't exist for a multi-user ---
    if not SINGLE_USER_MODE and not os.path.exists(filepath):
        print(f"-> Config for user '{username}' not found. Creating from default template.")
        try:
            default_template_path = os.path.join(TEMPLATE_DIR, 'config_default.yaml')
            shutil.copy(default_template_path, filepath)
            print(f"   -> Successfully created {filename}.")
        except Exception as e:
            print(f"   -> ❌ ERROR: Could not create config for '{username}': {e}")
            return {}  # Return empty on failure to prevent a crash

    # --- Caching and loading logic continues below ---
    if filepath in config_cache and os.path.exists(filepath) and os.path.getmtime(filepath) <= config_mtime.get(
            filepath, 0):
        return config_cache[filepath]

    if not os.path.exists(filepath):
        print(f"⚠️ Config file '{filename}' not found in '{CONFIG_DIR}'. Using default empty config.")
        return {}

    try:
        with open(filepath, "r", encoding='utf-8') as file:
            config_data = yaml.safe_load(file) or {}
        print(f"[LOAD CONFIG] Successfully loaded '{filename}' using safe_load.")
        # --- Auto-restore if obviously broken/empty ---
        try:
            broken = (not isinstance(config_data, dict)) or (len(config_data) == 0) or \
                     (not isinstance(config_data.get("locations"), dict)) or (
                                 len(config_data.get("locations") or {}) == 0)
        except Exception:
            broken = True

        if broken:
            print(f"⚠️ [LOAD CONFIG] '{filename}' appears empty/invalid. Attempting restore from latest backup...")
            try:
                backups = sorted(Path(BACKUP_DIR).glob(f"{Path(filepath).stem}_*.yaml"),
                                 key=lambda p: p.stat().st_mtime, reverse=True)
                for b in backups:
                    try:
                        recovered = yaml.safe_load(open(b, "r", encoding="utf-8")) or {}
                        if isinstance(recovered, dict) and isinstance(recovered.get("locations"), dict) and len(
                                recovered["locations"]) > 0:
                            with _FileLock(filepath):
                                _atomic_write_yaml(filepath, recovered)
                            config_data = recovered
                            print(f"   -> Restored from {b.name}")
                            break
                    except Exception:
                        continue
            except Exception as e:
                print(f"   -> Restore failed: {e}")

    except ConstructorError as e:
        if 'numpy' in str(e):
            print(f"⚠️ [CONFIG REPAIR] Unsafe NumPy tag detected in '{filename}'. Attempting automatic repair...")
            try:
                backup_dir = os.path.join(INSTANCE_PATH, "backups")
                os.makedirs(backup_dir, exist_ok=True)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_path = os.path.join(backup_dir,
                                           f"{os.path.basename(filename)}_corrupted_backup_{timestamp}.yaml")
                shutil.copy(filepath, backup_path)
                print(f"    -> Backed up corrupted file to '{backup_path}'")

                with open(filepath, "r", encoding='utf-8') as file:
                    corrupted_data = yaml.load(file, Loader=yaml.UnsafeLoader)

                cleaned_data = recursively_clean_numpy_types(corrupted_data)
                print("    -> Successfully cleaned data in memory.")

                save_user_config(username, cleaned_data)
                print(f"    -> Repaired and saved clean data to '{filename}'.")
                config_data = cleaned_data

            except Exception as repair_e:
                print(f"❌ [CONFIG REPAIR] Automatic repair failed: {repair_e}")
                return {}
        else:
            print(f"❌ ERROR: Unrecoverable YAML error in '{filename}': {e}")
            return {}

    except Exception as e:
        print(f"❌ ERROR: A critical error occurred while loading config '{filename}': {e}")
        return {}

    config_cache[filepath] = config_data
    config_mtime[filepath] = os.path.getmtime(filepath)
    return config_data

def save_user_config(username, config_data, *, require_objects=True, require_locations=True):
    """
    Safe, validated, atomic save with backup + rotation.
    Guards against empty/invalid locations/objects; conservative merge avoids
    dropping keys on partial form posts.
    """
    config_data = dict(config_data or {})

    # Invariants
    locs = config_data.get("locations")
    objs = config_data.get("objects")
    if require_locations and (not isinstance(locs, dict) or len(locs) == 0):
        print(f"❌ ABORT SAVE for '{username}': empty or missing 'locations'.")
        return False
    if require_objects and (objs is not None) and (not isinstance(objs, list) or any(not isinstance(o, dict) for o in objs)):
        print(f"❌ ABORT SAVE for '{username}': 'objects' invalid (must be a list of dicts).")
        return False

    # Validate schema
    try:
        ok, errors = validate_config(config_data)
        if not ok:
            print(f"❌ ABORT SAVE for '{username}': validation failed: {errors}")
            return False
    except Exception as e:
        print(f"❌ ABORT SAVE for '{username}': validator error: {e}")
        return False

    # Resolve path
    filename = "config_default.yaml" if SINGLE_USER_MODE else f"config_{username}.yaml"
    filepath = os.path.join(CONFIG_DIR, filename)

    # Conservative merge with existing (prevents partial form posts from nuking keys)
    try:
        if os.path.exists(filepath):
            existing = yaml.safe_load(open(filepath, "r", encoding="utf-8")) or {}
            merged = dict(existing)
            for k in ("altitude_threshold", "default_location", "sampling_interval_minutes",
                      "telemetry", "imaging_criteria", "objects", "locations", "rigs", "ui_preferences"):
                if k in config_data:
                    merged[k] = config_data[k]
            config_data = merged
    except Exception as e:
        print(f"[SAVE] merge warning (using new data as-is): {e}")

    # Backup + atomic write
    with _FileLock(filepath):
        try:
            if os.path.exists(filepath):
                _backup_with_rotation(filepath, keep=10)
            _atomic_write_yaml(filepath, config_data)
            config_cache[filepath] = config_data
            try:
                config_mtime[filepath] = os.path.getmtime(filepath)
            except Exception:
                pass
            print(f"[SAVE] '{filename}' saved safely.")
            return True
        except Exception as e:
            print(f"❌ SAVE FAILED for '{username}': {e}")
            return False

@app.route('/login', methods=['GET', 'POST'])
def login():
    if SINGLE_USER_MODE:
        # In single-user mode, the login page is not needed, just redirect.
        return redirect(url_for('index'))
    else:
        # --- MULTI-USER MODE LOGIC ---
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            user = db.session.scalar(db.select(User).where(User.username == username))
            if user and user.check_password(password):
                login_user(user)
                flash("Logged in successfully!", "success")
                return redirect(url_for('index'))
            else:
                flash("Invalid username or password.", "error")
        return render_template('login.html')

@app.route('/sso/login')
def sso_login():
    # First, check if the app is in single-user mode. SSO is not applicable here.
    if SINGLE_USER_MODE:
        flash("Single Sign-On is not applicable in single-user mode.", "error")
        return redirect(url_for('index'))

    # Get the token from the URL (e.g., ?token=...)
    token = request.args.get('token')
    if not token:
        flash("SSO Error: No token provided.", "error")
        return redirect(url_for('login'))

    # Get the shared secret key from the .env file
    secret_key = os.environ.get('JWT_SECRET_KEY')
    if not secret_key:
        flash("SSO Error: SSO is not configured on the server.", "error")
        return redirect(url_for('login'))

    try:
        # Decode the token. This automatically verifies the signature and expiration.
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        username = payload.get('username')

        if not username:
            raise jwt.InvalidTokenError("Token is missing username.")

        # Find the user in the Nova database
        user = db.session.scalar(db.select(User).where(User.username == username))

        if user and user.is_active:
            login_user(user)  # Log the user in using Flask-Login
            flash(f"Welcome back, {user.username}!", "success")
            return redirect(url_for('index'))
        else:
            flash(f"SSO Error: User '{username}' not found or is disabled in Nova.", "error")
            return redirect(url_for('login'))

    except jwt.ExpiredSignatureError:
        flash("SSO Error: The login link has expired. Please try again from WordPress.", "error")
        return redirect(url_for('login'))
    except jwt.InvalidTokenError:
        flash("SSO Error: Invalid login token.", "error")
        return redirect(url_for('login'))


@app.route('/proxy_focus', methods=['POST'])
def proxy_focus():
    payload = request.form
    try:
        # This line ensures the dynamically determined STELLARIUM_API_URL_BASE is used:
        stellarium_focus_url = f"{STELLARIUM_API_URL_BASE}/api/main/focus"

        # print(f"[PROXY FOCUS] Attempting to connect to Stellarium at: {stellarium_focus_url}")  # For debugging

        # Make the request to Stellarium
        r = requests.post(stellarium_focus_url, data=payload, timeout=10)  # Added timeout
        r.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        # print(f"[PROXY FOCUS] Stellarium response: {r.status_code}")  # For debugging
        return jsonify({"status": "success", "stellarium_response": r.text})

    except requests.exceptions.ConnectionError:
        # Specific error if Stellarium isn't running or reachable at the URL
        message = f"Could not connect to Stellarium at {STELLARIUM_API_URL_BASE}. Ensure Stellarium is running, Remote Control is enabled, and the URL is correct."
        if STELLARIUM_ERROR_MESSAGE:  # User-defined message overrides if present
            message = STELLARIUM_ERROR_MESSAGE
        print(f"[PROXY FOCUS ERROR] ConnectionError: {message}")
        return jsonify({"status": "error", "message": message}), 503  # 503 Service Unavailable

    except requests.exceptions.Timeout:
        # Specific error for timeouts
        message = f"Connection to Stellarium at {STELLARIUM_API_URL_BASE} timed out after 10 seconds."
        print(f"[PROXY FOCUS ERROR] Timeout: {message}")
        return jsonify({"status": "error", "message": message}), 504  # 504 Gateway Timeout

    except requests.exceptions.HTTPError as http_err:
        # Specific error for HTTP errors from Stellarium (e.g., API errors)
        error_details = http_err.response.text if http_err.response is not None else "No response details"
        message = f"Stellarium at {STELLARIUM_API_URL_BASE} returned an error: {http_err}. Details: {error_details}"
        status_code = http_err.response.status_code if http_err.response is not None else 500
        print(f"[PROXY FOCUS ERROR] HTTPError {status_code}: {message}")
        return jsonify({"status": "error", "message": message}), status_code

    except Exception as e:
        # Catch-all for other unexpected errors
        message = STELLARIUM_ERROR_MESSAGE or f"An unexpected error occurred while attempting to contact Stellarium: {str(e)}"
        print(f"[PROXY FOCUS ERROR] Unexpected error: {e}")  # Log the actual error
        return jsonify({"status": "error", "message": message}), 500

@app.before_request
def load_config_for_request():
    """
    Loads all necessary user configuration and data from the DATABASE at the
    beginning of each request. This function is the bridge between the database
    and the application's runtime logic, populating the Flask global `g` object.
    """
    # 1. Determine the current user's username
    if SINGLE_USER_MODE:
        username = "default"
        g.is_guest = False
    elif current_user.is_authenticated:
        username = current_user.username
        g.is_guest = False
    else:
        username = "guest_user"
        g.is_guest = True

    # 2. CRITICAL CHANGE: Load the entire configuration from the database
    #    This replaces the old call to load_user_config(username) which read from YAML files.
    g.user_config = build_user_config_from_db(username)

    # 3. Populate the request context (`g`) with the DB-backed configuration.
    #    The rest of the application will use these `g` variables.
    g.locations = g.user_config.get("locations", {})
    g.active_locations = {
        name: details for name, details in g.locations.items()
        if details.get('active', True) # Defaults to active for older configs
    }

    # Validate the default location, falling back if it's inactive or doesn't exist.
    default_loc_name = g.user_config.get("default_location")
    validated_location = default_loc_name

    if not default_loc_name or default_loc_name not in g.active_locations:
        if g.active_locations:
            first_active_loc = next(iter(g.active_locations))
            validated_location = first_active_loc
            print(f"⚠️ WARNING: Default location '{default_loc_name}' not found or is inactive. "
                  f"Falling back to first available active location: '{validated_location}'.")
        else:
            validated_location = None
            print(f"⚠️ WARNING: No active locations are defined in the configuration.")

    g.selected_location = validated_location

    # Set safe defaults to prevent crashes if no location is configured
    g.altitude_threshold = g.user_config.get("altitude_threshold", 20)
    if g.selected_location:
        loc_cfg = g.locations.get(g.selected_location, {})
        g.lat = loc_cfg.get("lat")
        g.lon = loc_cfg.get("lon")
        g.tz_name = loc_cfg.get("timezone", "UTC")
    else:
        g.lat = None
        g.lon = None
        g.tz_name = "UTC"

    # Populate object lists for use in dropdowns and lookups
    g.objects_list = g.user_config.get("objects", [])
    g.alternative_names = {
        obj.get("Object").lower(): obj.get("Name")
        for obj in g.objects_list if obj.get("Object")
    }
    g.projects = {
        obj.get("Object").lower(): obj.get("Project")
        for obj in g.objects_list if obj.get("Object")
    }
    g.objects = [ obj.get("Object") for obj in g.objects_list if obj.get("Object") ]

    # Finally, load other settings like calculation precision
    load_effective_settings()

@app.before_request
def ensure_telemetry_defaults():
    """
    Ensures telemetry defaults IN MEMORY for the current request,
    without writing back to any files.
    """
    try:
        if hasattr(g, 'user_config') and isinstance(g.user_config, dict):
            # Use setdefault to add the 'telemetry' key if it's missing.
            # This modifies the g.user_config dictionary for this request only.
            telemetry_config = g.user_config.setdefault('telemetry', {})
            # Now ensure the 'enabled' key has a default value.
            telemetry_config.setdefault('enabled', True)
    except Exception as e:
        print(f"❌ ERROR in ensure_telemetry_defaults: {e}")

@app.before_request
def telemetry_startup_ping_once():
    # Emulate old before_first_request semantics with a thread-safe guard
    if not _telemetry_startup_once.is_set():
        _telemetry_startup_once.set()
        try:
            username = "default" if SINGLE_USER_MODE else (current_user.username if current_user.is_authenticated else "guest_user")
            cfg = g.user_config if hasattr(g, 'user_config') else load_user_config(username)
            send_telemetry_async(cfg, browser_user_agent='')
        except Exception:
            pass

@app.route('/set_location', methods=['POST'])
def set_location_api():
    data = request.get_json()
    location_name = data.get("location")
    if location_name not in g.locations:
        return jsonify({"status": "error", "message": "Invalid location"}), 404

    # Update in-memory config and selection
    g.user_config['default_location'] = location_name
    g.selected_location = location_name

    # Save to appropriate config file
    username = current_user.username if current_user.is_authenticated else 'guest_user'
    save_user_config(username, g.user_config)

    # Proactively warm the weather cache for the new location in the background.
    try:
        new_loc_details = g.locations.get(location_name, {})
        new_lat = new_loc_details.get("lat")
        new_lon = new_loc_details.get("lon")
        if new_lat is not None and new_lon is not None:
            # Run the weather fetch in a separate thread so it doesn't block
            # the current request. We don't need its return value here.
            thread = threading.Thread(target=get_hybrid_weather_forecast, args=(new_lat, new_lon))
            thread.start()
            print(f"[CACHE WARMING] Triggered background weather fetch for new location: {location_name}")
    except Exception as e:
        print(f"[CACHE WARMING] Failed to trigger background weather fetch: {e}")

    return jsonify({"status": "success", "message": f"Location set to {location_name}"})


@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.context_processor
def inject_version():
    return dict(version=APP_VERSION)


@app.route('/download_config')
@login_required
def download_config():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash("User not found.", "error")
            return redirect(url_for('config_form'))

        # --- 1. Load base settings from UiPref ---
        config_doc = {}
        prefs = db.query(UiPref).filter_by(user_id=u.id).one_or_none()
        if prefs and prefs.json_blob:
            try:
                config_doc = json.loads(prefs.json_blob)
            except json.JSONDecodeError:
                pass  # Start with empty doc if JSON is corrupt

        # --- 2. Load Locations ---
        locs = db.query(Location).filter_by(user_id=u.id).all()
        default_loc_name = next((l.name for l in locs if l.is_default), None)
        config_doc["default_location"] = default_loc_name
        config_doc["locations"] = {
            l.name: {
                "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                "altitude_threshold": l.altitude_threshold,
                "active": l.active,
                "comments": l.comments,
                "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
            } for l in locs
        }

        # --- 3. Load Objects ---
        config_doc["objects"] = [
            {
                "Object": o.object_name,
                "Name": o.common_name,  # "Name" is used by the form
                "Common Name": o.common_name,  # "Common Name" is used by the index page
                "RA": o.ra_hours, "DEC": o.dec_deg,
                "RA (hours)": o.ra_hours, "DEC (degrees)": o.dec_deg,  # Redundant for compatibility
                "Type": o.type, "Constellation": o.constellation,
                "Magnitude": o.magnitude, "Size": o.size, "SB": o.sb,
                "ActiveProject": o.active_project, "Project": o.project_name
            } for o in db.query(AstroObject).filter_by(user_id=u.id).order_by(AstroObject.object_name).all()
        ]

        # --- 4. Create in-memory file ---
        yaml_string = yaml.dump(config_doc, sort_keys=False, allow_unicode=True, indent=2, default_flow_style=False)
        str_io = io.BytesIO(yaml_string.encode('utf-8'))

        # Determine filename
        if SINGLE_USER_MODE:
            download_name = "config_default.yaml"
        else:
            download_name = f"config_{username}.yaml"

        return send_file(str_io, as_attachment=True, download_name=download_name, mimetype='text/yaml')

    except Exception as e:
        db.rollback()
        flash(f"Error generating config file: {e}", "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('config_form'))
    finally:
        db.close()


@app.route('/download_journal')
@login_required
def download_journal():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash("User not found.", "error")
            return redirect(url_for('config_form'))

        # --- 1. Load Projects ---
        projects = db.query(Project).filter_by(user_id=u.id).order_by(Project.name).all()
        projects_list = [
            {"project_id": p.id, "project_name": p.name} for p in projects
        ]

        # --- 2. Load Sessions ---
        sessions = db.query(JournalSession).filter_by(user_id=u.id).order_by(JournalSession.date_utc.asc()).all()
        sessions_list = []
        for s in sessions:
            sessions_list.append({
                "session_id": s.external_id or s.id,
                "project_id": s.project_id,
                "session_date": s.date_utc.isoformat(),
                "target_object_id": s.object_name,
                "general_notes_problems_learnings": s.notes,
                "session_image_file": s.session_image_file,
                "location_name": s.location_name,
                "seeing_observed_fwhm": s.seeing_observed_fwhm,
                "sky_sqm_observed": s.sky_sqm_observed,
                "moon_illumination_session": s.moon_illumination_session,
                "moon_angular_separation_session": s.moon_angular_separation_session,
                "weather_notes": s.weather_notes,
                "telescope_setup_notes": s.telescope_setup_notes,
                "filter_used_session": s.filter_used_session,
                "guiding_rms_avg_arcsec": s.guiding_rms_avg_arcsec,
                "guiding_equipment": s.guiding_equipment,
                "dither_details": s.dither_details,
                "acquisition_software": s.acquisition_software,
                "gain_setting": s.gain_setting,
                "offset_setting": s.offset_setting,
                "camera_temp_setpoint_c": s.camera_temp_setpoint_c,
                "camera_temp_actual_avg_c": s.camera_temp_actual_avg_c,
                "binning_session": s.binning_session,
                "darks_strategy": s.darks_strategy,
                "flats_strategy": s.flats_strategy,
                "bias_darkflats_strategy": s.bias_darkflats_strategy,
                "session_rating_subjective": s.session_rating_subjective,
                "transparency_observed_scale": s.transparency_observed_scale,
                "number_of_subs_light": s.number_of_subs_light,
                "exposure_time_per_sub_sec": s.exposure_time_per_sub_sec,
                "filter_L_subs": s.filter_L_subs, "filter_L_exposure_sec": s.filter_L_exposure_sec,
                "filter_R_subs": s.filter_R_subs, "filter_R_exposure_sec": s.filter_R_exposure_sec,
                "filter_G_subs": s.filter_G_subs, "filter_G_exposure_sec": s.filter_G_exposure_sec,
                "filter_B_subs": s.filter_B_subs, "filter_B_exposure_sec": s.filter_B_exposure_sec,
                "filter_Ha_subs": s.filter_Ha_subs, "filter_Ha_exposure_sec": s.filter_Ha_exposure_sec,
                "filter_OIII_subs": s.filter_OIII_subs, "filter_OIII_exposure_sec": s.filter_OIII_exposure_sec,
                "filter_SII_subs": s.filter_SII_subs, "filter_SII_exposure_sec": s.filter_SII_exposure_sec,
                "calculated_integration_time_minutes": s.calculated_integration_time_minutes
            })

        journal_doc = {"projects": projects_list, "sessions": sessions_list}

        # --- 3. Create in-memory file ---
        yaml_string = yaml.dump(journal_doc, sort_keys=False, allow_unicode=True, indent=2, default_flow_style=False)
        str_io = io.BytesIO(yaml_string.encode('utf-8'))

        # Determine filename
        if SINGLE_USER_MODE:
            download_name = "journal_default.yaml"
        else:
            download_name = f"journal_{username}.yaml"

        return send_file(str_io, as_attachment=True, download_name=download_name, mimetype='text/yaml')

    except Exception as e:
        db.rollback()
        flash(f"Error generating journal file: {e}", "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('config_form'))
    finally:
        db.close()

def validate_journal_data(journal_data):
    """
    Basic validation for imported journal data.
    Returns True if valid, False otherwise.
    Can be expanded for more detailed schema validation later.
    """
    if not isinstance(journal_data, dict):
        return False, "Uploaded journal is not a valid dictionary structure."
    if "sessions" not in journal_data:
        return False, "Uploaded journal is missing the top-level 'sessions' key."
    if not isinstance(journal_data["sessions"], list):
        return False, "The 'sessions' key in the uploaded journal must be a list."

    # Optional: Check if each session has a session_id (basic check)
    for i, session in enumerate(journal_data["sessions"]):
        if not isinstance(session, dict):
            return False, f"Session entry at index {i} is not a valid dictionary."
        if "session_id" not in session or not session["session_id"]:
            return False, f"Session entry at index {i} is missing a 'session_id'."
        # Add more checks per session if desired (e.g., session_date format)
    return True, "Journal data seems structurally valid."

@app.route('/import_journal', methods=['POST'])
@login_required
def import_journal():
    if 'file' not in request.files:
        flash("No file selected for journal import.", "error")
        return redirect(url_for('config_form'))

    file = request.files['file']
    if file.filename == '':
        flash("No file selected for journal import.", "error")
        return redirect(url_for('config_form'))

    if file and file.filename.endswith('.yaml'):
        try:
            contents = file.read().decode('utf-8')
            new_journal_data = yaml.safe_load(contents)

            if new_journal_data is None:
                new_journal_data = {"sessions": []}

            is_valid, message = validate_journal_data(new_journal_data)
            if not is_valid:
                flash(f"Invalid journal file structure: {message}", "error")
                return redirect(url_for('config_form'))

            if SINGLE_USER_MODE:
                username = "default"
                journal_filename = "journal_default.yaml"
            else:
                if not current_user.is_authenticated:
                    flash("Please log in to import a journal.", "warning")
                    return redirect(url_for('login'))
                username = current_user.username
                journal_filename = f"journal_{username}.yaml"

            journal_filepath = os.path.join(CONFIG_DIR, journal_filename)

            if os.path.exists(journal_filepath):
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_filename = f"{journal_filename}_backup_{timestamp}.yaml"
                backup_path = os.path.join(BACKUP_DIR, backup_filename)
                try:
                    shutil.copy(journal_filepath, backup_path)
                    print(f"[IMPORT JOURNAL] Backed up current journal to {backup_path}")
                except Exception as backup_e:
                    print(f"Warning: Could not back up existing journal: {backup_e}")

            save_journal(username, new_journal_data)
            flash("Journal imported successfully! Your old journal (if any) has been backed up.", "success")
            return redirect(url_for('config_form'))

        except yaml.YAMLError as ye:
            print(f"[IMPORT JOURNAL ERROR] Invalid YAML format: {ye}")
            flash(f"Import failed: Invalid YAML format in the journal file. {ye}", "error")
            return redirect(url_for('config_form'))
        except Exception as e:
            print(f"[IMPORT JOURNAL ERROR] {e}")
            flash(f"Import failed: An unexpected error occurred. {str(e)}", "error")
            return redirect(url_for('config_form'))
    else:
        flash("Invalid file type. Please upload a .yaml journal file.", "error")
        return redirect(url_for('config_form'))


@app.route('/import_config', methods=['POST'])
@login_required
def import_config():
    if SINGLE_USER_MODE:
        username = "default"
    elif current_user.is_authenticated:
        username = current_user.username
    else:
        # This case should ideally not be reached due to @login_required
        flash("Authentication error during import.", "error")
        return redirect(url_for('login'))
    try:
        if 'file' not in request.files:
            flash("No file selected for import.", "error")
            return redirect(url_for('config_form'))

        file = request.files['file']
        if file.filename == '':
            flash("No file selected for import.", "error")
            return redirect(url_for('config_form'))

        contents = file.read().decode('utf-8')
        new_config = yaml.safe_load(contents)

        valid, errors = validate_config(new_config)
        if not valid:
            error_message = f"Configuration validation failed: {json.dumps(errors, indent=2)}"
            flash(error_message, "error")
            return redirect(url_for('config_form'))

        if SINGLE_USER_MODE:
            username_for_backup = "default"
            config_filename = "config_default.yaml"
        else:
            if not current_user.is_authenticated:
                flash("You must be logged in to import a configuration.", "error")
                return redirect(url_for('login'))
            username_for_backup = current_user.username
            config_filename = f"config_{username_for_backup}.yaml"

        config_path = os.path.join(CONFIG_DIR, config_filename)

        if os.path.exists(config_path):
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_filename = f"{username_for_backup}_backup_{timestamp}.yaml"
            backup_path = os.path.join(BACKUP_DIR, backup_filename)
            shutil.copy(config_path, backup_path)
            print(f"[IMPORT] Backed up current config to {backup_path}")

        if os.path.exists(config_path):
            _backup_with_rotation(config_path, keep=10)
        with _FileLock(config_path):
            _atomic_write_yaml(config_path, new_config)
        print(f"[IMPORT] Overwrote {config_path} successfully with new config (atomic).")
        try:
            _username = username if 'username' in locals() else (
                current_user.username if getattr(current_user, "is_authenticated", False) else "default")
            import_from_existing_yaml(_username, clear_existing=False)
            print(f"[IMPORT→DB] Synced DB from YAMLs for user '{_username}' after config import.")
        except Exception as e:
            print(f"[IMPORT→DB] WARNING: Could not sync DB from YAMLs after config import: {e}")
        flash("Config imported successfully! Your old config (if any) has been backed up.", "success")

        user_config_for_thread = new_config.copy()
        for loc_name in user_config_for_thread.get('locations', {}).keys():
            cache_filename = f"outlook_cache_{username_for_backup}_{loc_name.lower().replace(' ', '_')}.json"
            cache_filepath = os.path.join(CACHE_DIR, cache_filename)
            if not os.path.exists(cache_filepath):
                print(f"    -> New location '{loc_name}' found. Triggering Outlook cache update.")
                thread = threading.Thread(target=update_outlook_cache,
                                          args=(username_for_backup, loc_name, user_config_for_thread))
                thread.start()

        return redirect(url_for('config_form'))

    except yaml.YAMLError as ye:
        print(f"[IMPORT ERROR] Invalid YAML: {ye}")
        flash(f"Import failed: The uploaded file was not valid YAML. ({ye})", "error")
        return redirect(url_for('config_form'))
    except Exception as e:
        print(f"[IMPORT ERROR] {e}")
        flash(f"Import failed: An unexpected error occurred. {str(e)}", "error")
        return redirect(url_for('config_form'))

# =============================================================================
# Astronomical Calculations
# =============================================================================

def get_ra_dec(object_name):
    obj_key = object_name.lower()
    # g.user_config is built from the database and available globally in the request
    objects_config = g.user_config.get("objects", [])
    obj_entry = next((item for item in objects_config if item["Object"].lower() == obj_key), None)

    # --- Define defaults ---
    default_type = "N/A"
    default_magnitude = "N/A"
    default_size = "N/A"
    default_sb = "N/A"
    default_project = "none"
    default_constellation = "N/A"
    default_active_project = False

    # --- Path 1: Object is found in the user's configuration ---
    if obj_entry:
        ra_str = obj_entry.get("RA")
        dec_str = obj_entry.get("DEC")
        constellation_val = obj_entry.get("Constellation", default_constellation)
        type_val = obj_entry.get("Type", default_type)
        magnitude_val = obj_entry.get("Magnitude", default_magnitude)
        size_val = obj_entry.get("Size", default_size)
        sb_val = obj_entry.get("SB", default_sb)
        project_val = obj_entry.get("Project", default_project)
        common_name_val = obj_entry.get("Name", object_name)
        active_project_val = obj_entry.get("ActiveProject", default_active_project)

        # Sub-path 1a: Config entry has coordinates, so we can return directly.
        if ra_str is not None and dec_str is not None:
            try:
                ra_hours_float = float(ra_str)
                dec_degrees_float = float(dec_str)

                # Auto-calculate constellation if missing
                if constellation_val in [None, "N/A", ""]:
                    try:
                        coords = SkyCoord(ra=ra_hours_float * u.hourangle, dec=dec_degrees_float * u.deg)
                        constellation_val = get_constellation(coords, short_name=True)
                    except Exception:
                        constellation_val = "N/A"

                return {
                    "Object": object_name,
                    "Constellation": constellation_val,
                    "Common Name": common_name_val,
                    "RA (hours)": ra_hours_float,
                    "DEC (degrees)": dec_degrees_float,
                    "Project": project_val,
                    "Type": type_val if type_val else default_type,
                    "Magnitude": magnitude_val if magnitude_val else default_magnitude,
                    "Size": size_val if size_val else default_size,
                    "SB": sb_val if sb_val else default_sb,
                    "ActiveProject": active_project_val
                }
            except ValueError:
                return {
                    "Object": object_name, "Constellation": "N/A", "Common Name": "Error: Invalid RA/DEC in config",
                    "RA (hours)": None, "DEC (degrees)": None, "Project": project_val, "Type": type_val,
                    "Magnitude": magnitude_val, "Size": size_val, "SB": sb_val, "ActiveProject": active_project_val
                }
        # Sub-path 1b: Config entry exists but is missing coordinates. Fall through to SIMBAD lookup.
        else:
            pass

    # --- Path 2: Object not in config, or was missing coordinates. Query SIMBAD. ---
    project_to_use = obj_entry.get("Project", default_project) if obj_entry else default_project
    active_project_to_use = obj_entry.get("ActiveProject",
                                          default_active_project) if obj_entry else default_active_project

    try:
        custom_simbad = Simbad()
        custom_simbad.ROW_LIMIT = 1
        custom_simbad.TIMEOUT = 60
        custom_simbad.add_votable_fields('main_id', 'ra', 'dec', 'otype')
        result = custom_simbad.query_object(object_name)

        if result is None or len(result) == 0:
            raise ValueError(f"No results for '{object_name}' in SIMBAD.")

        ra_col = 'RA' if 'RA' in result.colnames else 'ra'
        dec_col = 'DEC' if 'DEC' in result.colnames else 'dec'
        ra_value_simbad = str(result[ra_col][0])
        dec_value_simbad = str(result[dec_col][0])

        ra_hours_simbad = hms_to_hours(ra_value_simbad)
        dec_degrees_simbad = dms_to_degrees(dec_value_simbad)
        simbad_main_id = str(result['MAIN_ID'][0]) if 'MAIN_ID' in result.colnames else object_name

        try:
            coords = SkyCoord(ra=ra_hours_simbad * u.hourangle, dec=dec_degrees_simbad * u.deg)
            constellation_simbad = get_constellation(coords, short_name=True)
        except Exception:
            constellation_simbad = "N/A"

        return {
            "Object": object_name,
            "Constellation": constellation_simbad,
            "Common Name": simbad_main_id,
            "RA (hours)": ra_hours_simbad,
            "DEC (degrees)": dec_degrees_simbad,
            "Project": project_to_use,
            "Type": str(result['OTYPE'][0]) if 'OTYPE' in result.colnames else "N/A",
            "Magnitude": "N/A",  # Not fetched in this simple query
            "Size": "N/A",  # Not fetched in this simple query
            "SB": "N/A",  # Not fetched in this simple query
            "ActiveProject": active_project_to_use
        }
    except Exception as ex:
        return {
            "Object": object_name, "Constellation": "N/A",
            "Common Name": f"Error: SIMBAD lookup failed ({type(ex).__name__})",
            "RA (hours)": None, "DEC (degrees)": None, "Project": project_to_use, "Type": "N/A",
            "Magnitude": "N/A", "Size": "N/A", "SB": "N/A", "ActiveProject": active_project_to_use
        }

# =============================================================================
# Protected Routes
# =============================================================================

@app.route('/get_locations')
def get_locations():
    """Returns only ACTIVE locations for the main UI dropdown and the user's default."""
    # Determine username based on mode and authentication status
    username = "default" if SINGLE_USER_MODE else (current_user.username if current_user.is_authenticated else "guest_user")
    db = get_db()
    try:
        # Find the user record in the application database
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            # If the user doesn't exist in app.db, return empty lists
            return jsonify({"locations": [], "selected": None})

        # Query the database for locations belonging to this user that are marked as active
        active_locs = db.query(Location).filter_by(user_id=user.id, active=True).order_by(Location.name).all()
        # Extract just the names for the dropdown list
        active_loc_names = [loc.name for loc in active_locs]

        # Determine which location should be pre-selected in the dropdown
        selected = None
        # Find if any of the active locations is also marked as the default
        default_loc = next((loc.name for loc in active_locs if loc.is_default), None)

        if default_loc:
            # If an active default location exists, use it
            selected = default_loc
        elif active_loc_names:
            # Otherwise, if there are any active locations, use the first one in the list
            selected = active_loc_names[0]
        # If there are no active locations, 'selected' remains None

        # Return the list of active location names and the name of the location to be selected
        return jsonify({"locations": active_loc_names, "selected": selected})
    except Exception as e:
        # Log any unexpected errors during database access
        print(f"Error in get_locations for user '{username}': {e}")
        # Return an error response or an empty list in case of failure
        return jsonify({"locations": [], "selected": None, "error": str(e)}), 500
    finally:
        # Ensure the database session is closed regardless of success or failure
        db.close()

@app.route('/search_object', methods=['POST'])
@login_required
def search_object():
    # Expect JSON input with the object identifier.
    object_name = request.json.get('object')
    if not object_name:
        return jsonify({"status": "error", "message": "No object specified."}), 400

    data = get_ra_dec(object_name)
    if data and data.get("RA (hours)") is not None:
        return jsonify({"status": "success", "data": data})
    else:
        # Return an error message from the lookup.
        return jsonify({"status": "error", "message": data.get("Common Name", "Object not found.")}), 404


def check_and_fill_object_data(config_data):
    """
    Iterates through objects in config_data, fetches missing or placeholder details
    using nova_data_fetcher, and updates the config_data dictionary in place.
    Returns True if any data was modified, False otherwise.
    """
    if not config_data or 'objects' not in config_data:
        print("[CONFIG CHECK/FETCH] No 'objects' key in config_data or config_data is empty.")
        return False

    objects_list = config_data.get('objects', [])
    if not isinstance(objects_list, list):
        print("[WARNING] 'objects' in config is not a list. Skipping auto-fill.")
        return False

    modified = False
    # Fields to check and their corresponding keys in the data returned by nova_data_fetcher
    fields_to_check = {
        # Config Key : Fetcher Key from nova_data_fetcher.get_astronomical_data()
        "Type": "object_type",
        "Magnitude": "magnitude",
        "Size": "size_arcmin",  # Assuming your YAML uses "Size" for size_arcmin
        "SB": "surface_brightness",
    }

    # Values that indicate a field should be (re-)fetched
    refetch_trigger_values = [None, "", "Not Found", "Fetch Error"]
    # Value to set if fetcher returns None for a field that was attempted
    placeholder_on_fetch_failure = "Not Found"
    # Value to set if the fetch operation itself throws an exception for an object
    placeholder_on_exception = "Fetch Error"

    print("[CONFIG CHECK/FETCH] Checking objects for missing or placeholder data...")
    objects_processed_for_fetching = 0
    objects_actually_updated = 0

    for obj_entry in objects_list:
        if not isinstance(obj_entry, dict) or "Object" not in obj_entry:
            print(f"[WARNING] Skipping invalid object entry: {obj_entry}")
            continue

        object_name = obj_entry["Object"]

        # --- NEW: Auto-calculate Constellation if missing ---
        current_constellation = obj_entry.get("Constellation")
        refetch_triggers = [None, "", "N/A", "Not Found", "Fetch Error"]

        # Check if constellation is missing and if RA/DEC are present and valid
        if current_constellation in refetch_triggers and 'RA' in obj_entry and 'DEC' in obj_entry:
            try:
                ra_h = float(obj_entry['RA'])
                dec_d = float(obj_entry['DEC'])
                coords = SkyCoord(ra=ra_h*u.hourangle, dec=dec_d*u.deg)
                new_constellation = get_constellation(coords, short_name=True)
                obj_entry['Constellation'] = new_constellation
                print(f"    Calculated and updated 'Constellation' for {object_name} = {new_constellation}")
                modified = True
            except (ValueError, TypeError, KeyError) as e:
                print(f"    Could not calculate constellation for {object_name} due to invalid RA/DEC: {e}")
        # --- END NEW ---

        fields_that_need_update = {}
        for config_key, fetcher_key in fields_to_check.items():
            current_value = obj_entry.get(config_key)

            # MODIFIED: Condition to trigger refetch
            needs_refetch = False
            if current_value in refetch_trigger_values:
                needs_refetch = True
            elif isinstance(current_value, str):
                # Check for empty string after stripping, or case-insensitive "none"
                if current_value.strip() == "" or current_value.strip().lower() == 'none':
                    needs_refetch = True

            if needs_refetch:
                fields_that_need_update[config_key] = fetcher_key

        if fields_that_need_update:
            print(
                f"--- Attempting to fetch/update data for {object_name} for fields: {list(fields_that_need_update.keys())} ---")
            objects_processed_for_fetching += 1
            object_had_an_update_this_round = False

            try:
                fetched_data = nova_data_fetcher.get_astronomical_data(object_name)

                for config_key, fetcher_key in fields_that_need_update.items():
                    new_value_from_fetcher = fetched_data.get(fetcher_key)

                    if new_value_from_fetcher is not None and new_value_from_fetcher != "":

                        # --- NEW ROBUST TYPE CONVERSION ---
                        try:
                            # Check if it's any kind of number (Python float, int, or any NumPy number)
                            if isinstance(new_value_from_fetcher, (np.number, float, int)):
                                # Convert to a standard Python float first
                                native_float = float(new_value_from_fetcher)

                                # Apply rounding based on the field
                                if config_key in ["Magnitude", "Size", "SB"]:
                                    new_value_formatted = round(native_float, 2)
                                else:
                                    new_value_formatted = native_float
                            else:
                                # If it's not a number, treat it as a string
                                new_value_formatted = str(new_value_from_fetcher).strip()
                        except (ValueError, TypeError):
                            print(
                                f"    [WARN] Could not format fetched value '{new_value_from_fetcher}' for {config_key}. Storing as string or placeholder.")
                            new_value_formatted = str(
                                new_value_from_fetcher).strip() if new_value_from_fetcher else placeholder_on_fetch_failure
                        # --- END OF NEW CONVERSION ---

                        current_config_value = obj_entry.get(config_key)
                        should_update = False
                        if current_config_value in refetch_trigger_values or \
                                (isinstance(current_config_value,
                                            str) and current_config_value.strip().lower() == 'none'):
                            should_update = True
                        elif current_config_value != new_value_formatted:
                            should_update = True

                        if should_update:
                            obj_entry[config_key] = new_value_formatted
                            print(
                                f"    Updated '{config_key}' for {object_name} = {new_value_formatted} (Source: {fetched_data.get(fetcher_key.replace('_arcmin', '').replace('object_', '') + '_source', 'N/A')})")
                            modified = True
                            object_had_an_update_this_round = True
                    else:
                        if obj_entry.get(config_key) != placeholder_on_fetch_failure:
                            obj_entry[config_key] = placeholder_on_fetch_failure
                            print(
                                f"    Marked '{config_key}' as '{placeholder_on_fetch_failure}' for {object_name} (fetcher returned no data for this field).")
                            modified = True
                            object_had_an_update_this_round = True

                if object_had_an_update_this_round:
                    objects_actually_updated += 1
            except Exception as e:
                print(f"[ERROR] Fetch operation failed for {object_name}: {e}")
                for config_key in fields_that_need_update:
                    if obj_entry.get(config_key) != placeholder_on_exception:
                        obj_entry[config_key] = placeholder_on_exception
                        modified = True
                        object_had_an_update_this_round = True
                if fields_that_need_update and not object_had_an_update_this_round:
                    objects_actually_updated += 1

            time.sleep(0.5)

    if modified:
        print(
            f"[CONFIG CHECK/FETCH] Processed {objects_processed_for_fetching} objects for potential updates, {objects_actually_updated} objects had at least one field updated/marked. Config needs saving.")
    else:
        print("[CONFIG CHECK/FETCH] No objects required data fetching or re-fetching based on current criteria.")

    return modified


@app.route('/fetch_object_details', methods=['POST'])
@login_required
def fetch_object_details():
    """
    Fetch exactly Type, Magnitude, Size, SB for one object
    using nova_data_fetcher.
    """
    req = request.get_json()
    object_name = req.get("object")
    if not object_name:
        return jsonify({"status": "error", "message": "No object specified."}), 400

    try:
        fetched = nova_data_fetcher.get_astronomical_data(object_name)

        # --- FIX: Convert NumPy types to native Python types before sending to browser ---
        clean_data = {
            "Type": convert_to_native_python(fetched.get("object_type")),
            "Magnitude": convert_to_native_python(fetched.get("magnitude")),
            "Size": convert_to_native_python(fetched.get("size_arcmin")),
            "SB": convert_to_native_python(fetched.get("surface_brightness"))
        }

        return jsonify({
            "status": "success",
            "data": {
                "Type": clean_data.get("Type") or "",
                "Magnitude": clean_data.get("Magnitude") or "",
                "Size": clean_data.get("Size") or "",
                "SB": clean_data.get("SB") or ""
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/confirm_object', methods=['POST'])
@login_required
def confirm_object():
    req = request.get_json()
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        app_db_user = db.query(DbUser).filter_by(username=username).one()

        object_name = req.get('object')
        # Check if object already exists for this user
        existing = db.query(AstroObject).filter_by(user_id=app_db_user.id, object_name=object_name).one_or_none()
        if existing:
            # Update existing object's details
            existing.common_name = req.get('name')
            existing.ra_hours = float(req.get('ra'))
            existing.dec_deg = float(req.get('dec'))
            existing.project_name = req.get('project', 'none')
            existing.constellation = req.get('constellation')
            existing.type = convert_to_native_python(req.get('type'))
            existing.magnitude = str(convert_to_native_python(req.get('magnitude')) or '')
            existing.size = str(convert_to_native_python(req.get('size')) or '')
            existing.sb = str(convert_to_native_python(req.get('sb')) or '')
        else:
            # Create a new object record
            new_obj = AstroObject(
                user_id=app_db_user.id,
                object_name=object_name,
                common_name=req.get('name'),
                ra_hours=float(req.get('ra')),
                dec_deg=float(req.get('dec')),
                project_name=req.get('project', 'none'),
                constellation=req.get('constellation'),
                type=convert_to_native_python(req.get('type')),
                magnitude=str(convert_to_native_python(req.get('magnitude')) or ''),
                size=str(convert_to_native_python(req.get('size')) or ''),
                sb=str(convert_to_native_python(req.get('sb')) or '')
            )
            db.add(new_obj)

        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/fetch_all_details', methods=['POST'])
@login_required
def fetch_all_details():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        app_db_user = db.query(DbUser).filter_by(username=username).one()
        objects_to_check = db.query(AstroObject).filter_by(user_id=app_db_user.id).all()

        modified = False
        refetch_triggers = [None, "", "N/A", "Not Found", "Fetch Error"]

        for obj in objects_to_check:
            needs_update = (
                obj.type in refetch_triggers or
                obj.magnitude in refetch_triggers or
                obj.size in refetch_triggers or
                obj.sb in refetch_triggers or
                obj.constellation in refetch_triggers
            )
            if needs_update:
                try:
                    # Auto-calculate Constellation if missing
                    if obj.constellation in refetch_triggers and obj.ra_hours is not None and obj.dec_deg is not None:
                        coords = SkyCoord(ra=obj.ra_hours*u.hourangle, dec=obj.dec_deg*u.deg)
                        obj.constellation = get_constellation(coords, short_name=True)
                        modified = True

                    # Fetch other details from external API
                    fetched_data = nova_data_fetcher.get_astronomical_data(obj.object_name)
                    if fetched_data.get("object_type"): obj.type = fetched_data["object_type"]
                    if fetched_data.get("magnitude"): obj.magnitude = str(fetched_data["magnitude"])
                    if fetched_data.get("size_arcmin"): obj.size = str(fetched_data["size_arcmin"])
                    if fetched_data.get("surface_brightness"): obj.sb = str(fetched_data["surface_brightness"])
                    modified = True
                    time.sleep(0.5) # Be kind to external APIs
                except Exception as e:
                    print(f"Failed to fetch details for {obj.object_name}: {e}")

        if modified:
            db.commit()
            flash("Fetched and saved missing object details.", "success")
        else:
            flash("No missing data found or no updates needed.", "info")

    except Exception as e:
        db.rollback()
        flash(f"An error occurred during data fetching: {e}", "error")
    finally:
        db.close()

    return redirect(url_for('config_form'))

@app.route('/api/get_object_list')
def get_object_list():
    """
    A new, very fast endpoint that just returns the list of object names.
    """
    # g.objects is already loaded by the @app.before_request
    return jsonify({"objects": g.objects})


@app.route('/api/get_object_data/<path:object_name>')
def get_object_data(object_name):
    """
    API endpoint that calculates and returns detailed astronomical data
    (current position, nightly overview) for a single object,
    using the location specified in the 'location' query parameter
    or falling back to the user's default location.
    """
    # --- ADD: Determine location to use ---
    requested_location_name = request.args.get('location')
    lat, lon, tz_name, selected_location_name = g.lat, g.lon, g.tz_name, g.selected_location # Defaults from g
    current_location_config = {} # Default empty config for horizon mask etc.

    # Prioritize the location passed in the query parameter
    if requested_location_name and requested_location_name in g.locations:
        loc_cfg = g.locations[requested_location_name]
        lat = loc_cfg.get("lat")
        lon = loc_cfg.get("lon")
        tz_name = loc_cfg.get("timezone", "UTC")
        selected_location_name = requested_location_name
        current_location_config = loc_cfg # Use the specific location's config
        # print(f"[API Get Object Data] Using requested location: {selected_location_name}") # Debug print
    elif g.selected_location and g.selected_location in g.locations:
         # Fallback to default location if request param is missing/invalid but default exists
         loc_cfg = g.locations[g.selected_location]
         lat = loc_cfg.get("lat", g.lat) # Use default g value if key missing
         lon = loc_cfg.get("lon", g.lon)
         tz_name = loc_cfg.get("timezone", g.tz_name or "UTC")
         selected_location_name = g.selected_location
         current_location_config = loc_cfg
         # print(f"[API Get Object Data] Using default location: {g.selected_location}") # Debug print
    else:
         # print(f"[API Get Object Data] Warning: No location specified or default found.") # Debug print
         # lat, lon, tz_name remain the initial g values (which might be None)
         pass # Proceed with potentially None values, handle error below

    # If after checks, we don't have valid coordinates, return an error
    if lat is None or lon is None:
         return jsonify({
             'Object': object_name, 'Common Name': "Error: Location not set or invalid.",
             'Altitude Current': "N/A", 'Azimuth Current': "N/A", 'Trend': "–",
             'Altitude 11PM': "N/A", 'Azimuth 11PM': "N/A", 'Transit Time': "N/A",
             'Observable Duration (min)': "N/A", 'Max Altitude (°)': "N/A",
             'Angular Separation (°)': "N/A", 'Project': "N/A", 'Time': "N/A",
             'Constellation': 'N/A', 'Type': 'N/A', 'Magnitude': 'N/A',
             'Size': 'N/A', 'SB': 'N/A', 'is_obstructed_now': False,
             'is_obstructed_at_11pm': False, 'ActiveProject': False,
             'error': True
         }), 400
    # --- END Location Determination ---

    # --- Use the determined lat, lon, tz_name variables below ---
    local_tz = pytz.timezone(tz_name) # Use determined tz_name
    current_datetime_local = datetime.now(local_tz)

    # Determine the "astronomical night" date (adjust if it's past midnight but before dawn)
    today_str = current_datetime_local.strftime('%Y-%m-%d')
    # Use determined lat, lon, tz_name for sun events
    dawn_today_str = calculate_sun_events_cached(today_str, tz_name, lat, lon).get("astronomical_dawn")
    local_date = today_str # Start assuming today is the correct night
    if dawn_today_str:
        try:
            dawn_today_dt = local_tz.localize(
                datetime.combine(current_datetime_local.date(), datetime.strptime(dawn_today_str, "%H:%M").time()))
            # If current time is before today's dawn, the relevant "night" started yesterday
            if current_datetime_local < dawn_today_dt:
                local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            pass # Use today_str if dawn time parsing fails

    # --- Horizon mask from the determined location's config ---
    horizon_mask = current_location_config.get("horizon_mask")
    # --- END Horizon Mask ---

    # Get altitude threshold and sampling interval (these remain global/user-wide settings for now)
    altitude_threshold = g.user_config.get("altitude_threshold", 20)
    sampling_interval = g.user_config.get('sampling_interval_minutes', 15)

    # Get object details (RA/DEC etc.)
    obj_details = get_ra_dec(object_name)

    # Handle case where object lookup fails (e.g., not in config, SIMBAD fails)
    if not obj_details or obj_details.get("RA (hours)") is None:
        error_message = (obj_details.get("Common Name") if obj_details else "Object data not found.")
        return jsonify({
            'Object': object_name, 'Common Name': f"Error: {error_message}",
            'Altitude Current': "N/A", 'Azimuth Current': "N/A", 'Trend': "–",
            'Altitude 11PM': "N/A", 'Azimuth 11PM': "N/A", 'Transit Time': "N/A",
            'Observable Duration (min)': "N/A", 'Max Altitude (°)': "N/A",
            'Angular Separation (°)': "N/A", 'Project': "N/A", 'Time': "N/A",
            'Constellation': 'N/A', 'Type': 'N/A', 'Magnitude': 'N/A',
            'Size': 'N/A', 'SB': 'N/A', 'is_obstructed_now': False,
            'is_obstructed_at_11pm': False, 'ActiveProject': False,
            'error': True
        }), 404 # Use 404 for "Not Found" type errors

    ra = obj_details["RA (hours)"]
    dec = obj_details["DEC (degrees)"]

    # Use a cache key incorporating the specific location name being used
    cache_key = f"{object_name.lower()}_{local_date}_{selected_location_name.lower().replace(' ', '_')}"

    # Calculate or retrieve cached nightly data using determined location parameters
    if cache_key not in nightly_curves_cache:
        # Use determined tz_name, lat, lon
        times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)
        location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg) # Use determined lat, lon
        sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        altaz_frame = AltAz(obstime=times_utc, location=location)
        altitudes = sky_coord.transform_to(altaz_frame).alt.deg
        azimuths = sky_coord.transform_to(altaz_frame).az.deg
        # Use determined lat, lon, tz_name
        transit_time = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)

        # Pass determined lat, lon, tz_name AND the specific horizon_mask
        obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
            ra, dec, lat, lon, local_date, tz_name, altitude_threshold, sampling_interval,
            horizon_mask=horizon_mask # Pass the mask obtained earlier
        )

        # Use determined tz_name, lat, lon
        fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)
        alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)

        # Check obstruction at 11pm using determined horizon_mask
        is_obstructed_at_11pm = False
        if horizon_mask and isinstance(horizon_mask, list) and len(horizon_mask) > 1:
            sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
            # Use determined az_11pm and altitude_threshold
            required_altitude_11pm = interpolate_horizon(az_11pm, sorted_mask, altitude_threshold)
            if alt_11pm >= altitude_threshold and alt_11pm < required_altitude_11pm:
                is_obstructed_at_11pm = True

        # Store calculated data in cache
        nightly_curves_cache[cache_key] = {
            "times_local": times_local, "altitudes": altitudes, "azimuths": azimuths, "transit_time": transit_time,
            "obs_duration_minutes": int(obs_duration.total_seconds() / 60) if obs_duration else 0,
            "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
            "alt_11pm": f"{alt_11pm:.2f}", "az_11pm": f"{az_11pm:.2f}",
            "is_obstructed_at_11pm": is_obstructed_at_11pm
        }

    # Retrieve data (either fresh or from cache)
    cached_night_data = nightly_curves_cache[cache_key]

    # Calculate current position and trend using cached data
    now_utc = datetime.now(pytz.utc)
    # Find the index closest to the current time
    time_diffs = [abs((t - now_utc).total_seconds()) for t in cached_night_data["times_local"]]
    current_index = np.argmin(time_diffs)
    current_alt = cached_night_data["altitudes"][current_index]
    current_az = cached_night_data["azimuths"][current_index]

    # Determine trend by looking at the next point
    next_index = min(current_index + 1, len(cached_night_data["altitudes"]) - 1)
    next_alt = cached_night_data["altitudes"][next_index]
    trend = '–'
    if abs(next_alt - current_alt) > 0.01: # Check for significant change
        trend = '↑' if next_alt > current_alt else '↓'

    # Check obstruction *now* using determined horizon_mask
    is_obstructed_now = False
    if horizon_mask and isinstance(horizon_mask, list) and len(horizon_mask) > 1:
        sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
        # Use determined current_az and altitude_threshold
        required_altitude_now = interpolate_horizon(current_az, sorted_mask, altitude_threshold)
        if current_alt >= altitude_threshold and current_alt < required_altitude_now:
            is_obstructed_now = True

    # Calculate Moon separation using determined lat, lon
    time_obj = Time(datetime.now(pytz.utc))
    location_for_moon = EarthLocation(lat=lat * u.deg, lon=lon * u.deg) # Use determined lat, lon
    moon_coord = get_body('moon', time_obj, location_for_moon)
    obj_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    frame = AltAz(obstime=time_obj, location=location_for_moon)
    angular_sep = obj_coord_sky.transform_to(frame).separation(moon_coord.transform_to(frame)).deg

    # Get obstruction flag for 11pm from cached data
    is_obstructed_at_11pm = cached_night_data.get('is_obstructed_at_11pm', False)

    # Get ActiveProject flag (remains from global user config lookup for now)
    active_flag = bool(obj_details.get('ActiveProject', False))
    # You could potentially make this location-specific if needed later

    # Assemble the final JSON payload
    single_object_data = {
        'Object': obj_details['Object'],
        'Common Name': obj_details['Common Name'],
        'Altitude Current': f"{current_alt:.2f}",
        'Azimuth Current': f"{current_az:.2f}",
        'Trend': trend,
        'Altitude 11PM': cached_night_data['alt_11pm'],
        'Azimuth 11PM': cached_night_data['az_11pm'],
        'Transit Time': cached_night_data['transit_time'],
        'Observable Duration (min)': cached_night_data['obs_duration_minutes'],
        'Max Altitude (°)': cached_night_data['max_altitude'],
        'Angular Separation (°)': round(angular_sep),
        'Project': obj_details.get('Project', "none"),
        'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'), # Time at the selected location
        'Constellation': obj_details.get('Constellation', 'N/A'),
        'Type': obj_details.get('Type', 'N/A'),
        'Magnitude': obj_details.get('Magnitude', 'N/A'),
        'Size': obj_details.get('Size', 'N/A'),
        'SB': obj_details.get('SB', 'N/A'),
        'is_obstructed_now': is_obstructed_now,
        'is_obstructed_at_11pm': is_obstructed_at_11pm,
        'ActiveProject': active_flag,
        'error': False # Indicate success
    }
    return jsonify(single_object_data)

@app.route('/')
def index():
    if not (current_user.is_authenticated or SINGLE_USER_MODE):
        return redirect(url_for('login'))

    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            # Handle case where user is authenticated but not yet in app.db
            return render_template('index.html', journal_sessions=[])

        sessions = db.query(JournalSession).filter_by(user_id=user.id).order_by(JournalSession.date_utc.desc()).all()
        objects_from_db = db.query(AstroObject).filter_by(user_id=user.id).all()
        object_names_lookup = {o.object_name: o.common_name for o in objects_from_db}

        # --- THIS IS THE CRITICAL FIX ---
        # Convert the list of objects into a list of JSON-safe dictionaries
        sessions_for_template = []
        for session in sessions:
            # Create a dictionary from the database object's columns
            session_dict = {c.name: getattr(session, c.name) for c in session.__table__.columns}

            # Convert the date object to an ISO string for JavaScript
            if isinstance(session_dict.get('date_utc'), (datetime, date)):
                session_dict['date_utc'] = session_dict['date_utc'].isoformat()

            # Add the common name for convenience in the template
            session_dict['target_common_name'] = object_names_lookup.get(session.object_name, session.object_name)

            sessions_for_template.append(session_dict)
        # --- END OF FIX ---

        local_tz = pytz.timezone(g.tz_name or 'UTC')
        now_local = datetime.now(local_tz)

        return render_template('index.html',
                               journal_sessions=sessions_for_template,  # Pass the new list of dictionaries
                               selected_day=now_local.day,
                               selected_month=now_local.month,
                               selected_year=now_local.year)
    finally:
        db.close()

@app.route('/sun_events')
def sun_events():
    """
    API endpoint to calculate and return sun event times (dusk, dawn, etc.)
    and the current moon phase for a specific location. Uses the location
    specified in the 'location' query parameter or falls back to the
    user's default location.
    """
    # --- Determine location to use ---
    requested_location_name = request.args.get('location')
    lat, lon, tz_name = g.lat, g.lon, g.tz_name # Defaults from flask global 'g'

    # Prioritize the location passed in the query parameter
    if requested_location_name and requested_location_name in g.locations:
        loc_cfg = g.locations[requested_location_name]
        lat = loc_cfg.get("lat")
        lon = loc_cfg.get("lon")
        tz_name = loc_cfg.get("timezone", "UTC")
        # print(f"[API Sun Events] Using requested location: {requested_location_name}") # Optional debug print
    elif g.selected_location and g.selected_location in g.locations:
         # Fallback to default location if request param is missing/invalid but default exists
         loc_cfg = g.locations[g.selected_location]
         lat = loc_cfg.get("lat", g.lat) # Use default g value if key missing in specific config
         lon = loc_cfg.get("lon", g.lon)
         tz_name = loc_cfg.get("timezone", g.tz_name or "UTC") # Use g.tz_name as fallback if timezone missing
         # print(f"[API Sun Events] Using default location: {g.selected_location}") # Optional debug print
    else:
         # print(f"[API Sun Events] Warning: No location specified or default found.") # Optional debug print
         # lat, lon, tz_name remain the initial g values (which might be None)
         pass # Proceed, error handled below

    # If after checks, we don't have valid coordinates, return an error immediately
    if lat is None or lon is None:
        # print("[API Sun Events] Error: Invalid coordinates (lat or lon is None).") # Optional debug print
        return jsonify({
            "date": datetime.now().strftime('%Y-%m-%d'),
            "time": datetime.now().strftime('%H:%M'),
            "phase": 0, # Default phase
            "error": "No location set or location has invalid coordinates."
        }), 400 # Bad request status
    # --- END Location Determination ---

    # --- Use the determined (valid) lat, lon, tz_name variables below ---
    try:
        local_tz = pytz.timezone(tz_name) # Use determined tz_name
    except pytz.UnknownTimeZoneError:
        # Handle invalid timezone string
        # print(f"[API Sun Events] Error: Invalid timezone '{tz_name}'. Falling back to UTC.") # Optional debug print
        tz_name = "UTC"
        local_tz = pytz.utc

    now_local = datetime.now(local_tz)
    local_date = now_local.strftime('%Y-%m-%d')

    # Calculate sun events using determined variables
    events = calculate_sun_events_cached(local_date, tz_name, lat, lon)

    # Calculate moon phase using determined variables
    try:
        moon = ephem.Moon()
        observer = ephem.Observer()
        observer.lat = str(lat) # Use determined lat (ephem needs string)
        observer.lon = str(lon) # Use determined lon (ephem needs string)
        observer.date = now_local.astimezone(pytz.utc) # Use current time converted to UTC
        moon.compute(observer)
        moon_phase = round(moon.phase, 1)
    except Exception as e:
        # Handle potential errors during ephem calculation
        print(f"[API Sun Events] Error calculating moon phase: {e}") # Log error
        moon_phase = "N/A" # Indicate error in response

    # Add all data to the response JSON
    events["date"] = local_date
    events["time"] = now_local.strftime('%H:%M')
    events["phase"] = moon_phase # Use calculated (or N/A) phase
    # Add error field if moon phase calculation failed
    if moon_phase == "N/A":
        events["error"] = events.get("error","") + " Moon phase calculation failed."

    return jsonify(events)

@app.route("/telemetry/ping", methods=["POST"])
def telemetry_ping():
    # Respect opt-out as usual
    try:
        username = "default" if SINGLE_USER_MODE else (
            current_user.username if getattr(current_user, "is_authenticated", False) else "guest_user"
        )
    except Exception:
        username = "default"

    try:
        cfg = g.user_config if hasattr(g, "user_config") else load_user_config(username)
    except Exception:
        cfg = {}

    tcfg = (cfg.get("telemetry") or {})
    if not tcfg.get("enabled", True):
        return jsonify({"status": "disabled"}), 200

    # Parse client-provided UA (optional) and also store the request header UA as fallback
    payload = request.get_json(silent=True) or {}
    ua_client = payload.get("browser_user_agent") or ""
    ua_header = request.headers.get("User-Agent", "") or ""
    ua_final = ua_client or ua_header

    # Cache UA for scheduled sends (so daily pings outside a request still include it)
    try:
        current_app.config["_LAST_UA"] = ua_final
    except Exception:
        pass

    # DO NOT force a send here; avoid doubling the startup/daily sends.
    # Only trigger a send if the 24h gate says it's okay right now.
    try:
        state_dir = Path(os.environ.get('NOVA_STATE_DIR', './cache'))
        if telemetry_should_send(state_dir):
            send_telemetry_async(cfg, browser_user_agent=ua_final, force=False)
        # else: silently skip; scheduler or next allowed window will send
    except Exception:
        pass

    return jsonify({"status": "ok"}), 200

@app.route('/config_form', methods=['GET', 'POST'])
@login_required
def config_form():
    error = None
    message = None
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        # Get the primary user object from our application DB
        app_db_user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not app_db_user:
            flash(f"Could not find user '{username}' in the database.", "error")
            return redirect(url_for('index'))

        # --- POST Request: Handle Form Submissions ---
        if request.method == 'POST':
            # --- General Settings Tab ---
            if 'submit_general' in request.form:
                # Load or create the user's preferences record
                prefs = db.query(UiPref).filter_by(user_id=app_db_user.id).one_or_none()
                if not prefs:
                    prefs = UiPref(user_id=app_db_user.id, json_blob='{}')
                    db.add(prefs)

                # Safely parse existing JSON, update it, and write it back
                try:
                    settings = json.loads(prefs.json_blob or '{}')
                except json.JSONDecodeError:
                    settings = {}

                # Update settings from the form
                settings['altitude_threshold'] = int(request.form.get('altitude_threshold', 20))
                settings['default_location'] = request.form.get('default_location', settings.get('default_location'))

                if SINGLE_USER_MODE:
                    settings['sampling_interval_minutes'] = int(request.form.get("sampling_interval", 15))
                    settings.setdefault('telemetry', {})['enabled'] = bool(request.form.get('telemetry_enabled'))

                # Update imaging criteria
                imaging_criteria = settings.setdefault("imaging_criteria", {})
                imaging_criteria["min_observable_minutes"] = int(request.form.get("min_observable_minutes", 60))
                imaging_criteria["min_max_altitude"] = int(request.form.get("min_max_altitude", 30))
                imaging_criteria["max_moon_illumination"] = int(request.form.get("max_moon_illumination", 20))
                imaging_criteria["min_angular_separation"] = int(request.form.get("min_angular_separation", 30))
                imaging_criteria["search_horizon_months"] = int(request.form.get("search_horizon_months", 6))

                prefs.json_blob = json.dumps(settings)
                message = "General settings updated."

            # --- Add New Location ---
            elif 'submit_new_location' in request.form:
                new_name = request.form.get("new_location").strip()
                # Check for existing location with the same name for this user
                existing = db.query(Location).filter_by(user_id=app_db_user.id, name=new_name).first()
                if existing:
                     error = f"A location named '{new_name}' already exists."
                elif not all([new_name, request.form.get("new_lat"), request.form.get("new_lon"), request.form.get("new_timezone")]):
                    error = "Name, Latitude, Longitude, and Timezone are required."
                else:
                    new_loc = Location(
                        user_id=app_db_user.id,
                        name=new_name,
                        lat=float(request.form.get("new_lat")),
                        lon=float(request.form.get("new_lon")),
                        timezone=request.form.get("new_timezone"),
                        active=request.form.get("new_active") == "on",
                        comments=request.form.get("new_comments", "").strip()[:500]
                    )
                    db.add(new_loc)
                    db.flush() # So new_loc gets an ID
                    # Add horizon mask points if provided
                    mask_str = request.form.get("new_horizon_mask", "").strip()
                    if mask_str:
                        try:
                            mask_data = yaml.safe_load(mask_str)
                            if isinstance(mask_data, list):
                                for point in mask_data:
                                    db.add(HorizonPoint(location_id=new_loc.id, az_deg=float(point[0]), alt_min_deg=float(point[1])))
                        except (yaml.YAMLError, ValueError, TypeError):
                             flash("Warning: Horizon Mask was invalid and was ignored.", "warning")
                    message = "New location added."

            # --- Update Existing Locations ---
            elif 'submit_locations' in request.form:
                locs_to_update = db.query(Location).filter_by(user_id=app_db_user.id).all()
                for loc in locs_to_update:
                    if request.form.get(f"delete_loc_{loc.name}") == "on":
                        db.delete(loc)
                        continue
                    # Update fields from form
                    loc.lat = float(request.form.get(f"lat_{loc.name}"))
                    loc.lon = float(request.form.get(f"lon_{loc.name}"))
                    loc.timezone = request.form.get(f"timezone_{loc.name}")
                    loc.active = request.form.get(f"active_{loc.name}") == "on"
                    loc.comments = request.form.get(f"comments_{loc.name}", "").strip()[:500]
                    # Update horizon mask
                    db.query(HorizonPoint).filter_by(location_id=loc.id).delete()
                    mask_str = request.form.get(f"horizon_mask_{loc.name}", "").strip()
                    if mask_str:
                        try:
                            mask_data = yaml.safe_load(mask_str)
                            if isinstance(mask_data, list):
                                for point in mask_data:
                                    db.add(HorizonPoint(location_id=loc.id, az_deg=float(point[0]), alt_min_deg=float(point[1])))
                        except Exception:
                            flash(f"Warning: Horizon Mask for '{loc.name}' was invalid and ignored.", "warning")

                message = "Locations updated."

            # --- Update Existing Objects ---
            elif 'submit_objects' in request.form:
                objs_to_update = db.query(AstroObject).filter_by(user_id=app_db_user.id).all()
                for obj in objs_to_update:
                    if request.form.get(f"delete_{obj.object_name}") == "on":
                        db.delete(obj)
                        continue
                    # Update fields from form
                    obj.common_name = request.form.get(f"name_{obj.object_name}")
                    obj.ra_hours = float(request.form.get(f"ra_{obj.object_name}"))
                    obj.dec_deg = float(request.form.get(f"dec_{obj.object_name}"))
                    obj.project_name = request.form.get(f"project_{obj.object_name}")
                    obj.type = request.form.get(f"type_{obj.object_name}")
                    obj.magnitude = request.form.get(f"magnitude_{obj.object_name}")
                    obj.size = request.form.get(f"size_{obj.object_name}")
                    obj.sb = request.form.get(f"sb_{obj.object_name}")
                message = "Objects updated."

            # --- Final Commit and Redirect ---
            if not error:
                db.commit()
                flash(f"{message or 'Configuration'} updated successfully.", "success")
                return redirect(url_for('config_form'))
            else:
                db.rollback()
                flash(error, "error")

        # --- GET Request: Populate Template Context from DB ---
        # Build a config-like dictionary for the template to use
        config_for_template = {}
        prefs = db.query(UiPref).filter_by(user_id=app_db_user.id).one_or_none()
        if prefs and prefs.json_blob:
            try:
                config_for_template = json.loads(prefs.json_blob)
            except json.JSONDecodeError:
                pass # Use empty dict on parse error

        # Load locations for the template
        locations_for_template = {}
        db_locations = db.query(Location).filter_by(user_id=app_db_user.id).order_by(Location.name).all()
        for loc in db_locations:
            locations_for_template[loc.name] = {
                "lat": loc.lat,
                "lon": loc.lon,
                "timezone": loc.timezone,
                "active": loc.active,
                "comments": loc.comments,
                "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in sorted(loc.horizon_points, key=lambda p: p.az_deg)]
            }
            if loc.is_default:
                config_for_template['default_location'] = loc.name

        # Load objects for the template
        db_objects = db.query(AstroObject).filter_by(user_id=app_db_user.id).order_by(AstroObject.object_name).all()
        config_for_template['objects'] = [{
            "Object": o.object_name,
            "Name": o.common_name,
            "RA": o.ra_hours,
            "DEC": o.dec_deg,
            "Project": o.project_name,
            "Constellation": o.constellation,
            "Type": o.type,
            "Magnitude": o.magnitude,
            "Size": o.size,
            "SB": o.sb
        } for o in db_objects]

        return render_template('config_form.html', config=config_for_template, locations=locations_for_template)

    except Exception as e:
        db.rollback()
        flash(f"A database error occurred: {e}", "error")
        traceback.print_exc()
        return redirect(url_for('index'))
    finally:
        db.close()


@app.before_request
def precompute_time_arrays():
    if current_user.is_authenticated and g.tz_name:
        local_tz = pytz.timezone(g.tz_name)
        local_date = datetime.now(local_tz).strftime('%Y-%m-%d')
        g.times_local, g.times_utc = get_common_time_arrays(g.tz_name, local_date)


@app.route('/update_project', methods=['POST'])
@login_required
def update_project():
    data = request.get_json()
    object_name = data.get('object')
    new_project_notes = data.get('project')
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        obj_to_update = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if obj_to_update:
            obj_to_update.project_name = new_project_notes
            db.commit()
            trigger_outlook_update_for_user(username)
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "error": "Object not found."}), 404
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        db.close()

@app.route('/update_project_active', methods=['POST'])
@login_required
def update_project_active():
    data = request.get_json()
    object_name = data.get('object')
    is_active = data.get('active')
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        obj_to_update = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if obj_to_update:
            obj_to_update.active_project = bool(is_active)
            db.commit()
            trigger_outlook_update_for_user(username)
            return jsonify({"status": "success", "active": obj_to_update.active_project})
        else:
            return jsonify({"status": "error", "error": "Object not found."}), 404
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500
    finally:
        db.close()


@app.before_request
def bypass_login_in_single_user():
    if SINGLE_USER_MODE and not current_user.is_authenticated:
        # Create a dummy user.
        dummy_user = User("default", "default")
        # Log in the dummy user.
        login_user(dummy_user)

def get_object_list_from_config():
    """Helper function to get the list of objects from the current user's config."""
    if hasattr(g, 'user_config') and g.user_config and "objects" in g.user_config:
        return g.user_config.get("objects", [])
    return []


@app.route('/api/get_moon_data')
@login_required
def get_moon_data_for_session():
    try:
        date_str = request.args.get('date')
        lat = float(request.args.get('lat'))
        lon = float(request.args.get('lon'))
        ra = float(request.args.get('ra'))
        dec = float(request.args.get('dec'))
        tz_name = request.args.get('tz')

        if not all([date_str, tz_name]):
            raise ValueError("Missing date or timezone.")

        local_tz = pytz.timezone(tz_name)
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')

        # Calculate moon phase at dusk for consistency
        sun_events = calculate_sun_events_cached(date_str, tz_name, lat, lon)
        dusk_str = sun_events.get("astronomical_dusk", "21:00")
        dusk_time_obj = datetime.strptime(dusk_str, "%H:%M").time()
        time_for_calc_local = local_tz.localize(datetime.combine(date_obj.date(), dusk_time_obj))
        moon_phase = round(ephem.Moon(time_for_calc_local.astimezone(pytz.utc)).phase, 1)

        # Calculate angular separation
        location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        time_obj = Time(time_for_calc_local.astimezone(pytz.utc))
        frame = AltAz(obstime=time_obj, location=location_obj)
        obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        moon_coord = get_body('moon', time_obj, location=location_obj)
        separation = obj_coord.transform_to(frame).separation(moon_coord.transform_to(frame)).deg

        return jsonify({
            "status": "success",
            "moon_illumination": moon_phase,
            "angular_separation": round(separation, 1)
        })

    except Exception as e:
        print(f"ERROR in /api/get_moon_data: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/graph_dashboard/<path:object_name>')
def graph_dashboard(object_name):
    # --- 1. Initialize effective context ---
    effective_location_name = g.selected_location or "Unknown"
    effective_lat = g.lat or 0.0
    effective_lon = g.lon or 0.0
    effective_tz_name = g.tz_name or 'UTC'
    requested_location_name_from_url = request.args.get('location')
    if requested_location_name_from_url and requested_location_name_from_url in g.locations:
        # If a valid location is provided in the URL, use its details instead of the default
        loc_cfg = g.locations[requested_location_name_from_url]
        effective_location_name = requested_location_name_from_url
        effective_lat = loc_cfg.get("lat")
        effective_lon = loc_cfg.get("lon")
        effective_tz_name = loc_cfg.get("timezone", "UTC")
        print(f"[Graph View] Using location from URL: {effective_location_name}")  # Optional debug print
    elif g.selected_location:
        # If no valid URL location, make sure we use the actual default's details
        loc_cfg = g.locations.get(g.selected_location, {})
        effective_lat = loc_cfg.get("lat", g.lat)
        effective_lon = loc_cfg.get("lon", g.lon)
        effective_tz_name = loc_cfg.get("timezone", g.tz_name or "UTC")
        print(f"[Graph View] Using default location: {effective_location_name}")  # Optional debug print
    else:
        print("[Graph View] Warning: No default or URL location found.")  # Optional debug print
        # effective_lat, lon, tz_name might be None, handle potential errors later

    # Ensure we have coordinates before proceeding
    if effective_lat is None or effective_lon is None:
        flash("Error: Could not determine a valid location for the graph.", "error")
        # Redirect or render an error template might be better here
        return redirect(url_for('index'))

    try:
        now_at_effective_location = datetime.now(pytz.timezone(effective_tz_name))
    except pytz.UnknownTimeZoneError:
        flash(f"Warning: Invalid timezone '{effective_tz_name}', using UTC.", "warning")
        effective_tz_name = 'UTC'
        now_at_effective_location = datetime.now(pytz.utc)

    # --- 2. Determine username for DB queries ---
    username = "default" if SINGLE_USER_MODE else (
        current_user.username if current_user.is_authenticated else "guest_user")

    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            flash(f"User '{username}' not found in database.", "error")
            return redirect(url_for('index'))

        # --- 3. Handle Journal Data and Date Overrides from Selected Session ---
        requested_session_id = request.args.get('session_id')
        selected_session_data = db.query(JournalSession).filter_by(id=requested_session_id,
                                                                   user_id=user.id).one_or_none() if requested_session_id else None
        # Convert the SQLAlchemy object to a JSON-serializable dictionary
        selected_session_data_dict = None
        if selected_session_data:
            selected_session_data_dict = {c.name: getattr(selected_session_data, c.name) for c in
                                          selected_session_data.__table__.columns}
            # Date objects are not JSON serializable, so convert to a string
            if isinstance(selected_session_data_dict.get('date_utc'), (datetime, date)):
                selected_session_data_dict['date_utc'] = selected_session_data_dict['date_utc'].isoformat()
        # Base date is today, or from URL params
        effective_day = int(request.args.get('day', now_at_effective_location.day))
        effective_month = int(request.args.get('month', now_at_effective_location.month))
        effective_year = int(request.args.get('year', now_at_effective_location.year))

        if selected_session_data:
            if selected_session_data.date_utc:
                effective_day, effective_month, effective_year = selected_session_data.date_utc.day, selected_session_data.date_utc.month, selected_session_data.date_utc.year

            # Assuming JournalSession model will have a 'location_name' field
            session_loc_name = getattr(selected_session_data, 'location_name', None)
            if session_loc_name:
                session_loc_details = db.query(Location).filter_by(user_id=user.id, name=session_loc_name).one_or_none()
                if session_loc_details:
                    effective_lat, effective_lon, effective_tz_name = session_loc_details.lat, session_loc_details.lon, session_loc_details.timezone
                    effective_location_name = session_loc_name
                else:
                    flash(f"Location '{session_loc_name}' from session not found. Using default.", "warning")
        elif requested_session_id:
            flash(f"Requested session ID '{requested_session_id}' not found.", "info")

        try:
            effective_date_obj = datetime(effective_year, effective_month, effective_day)
            effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
        except ValueError:
            effective_date_obj = now_at_effective_location.date()
            effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
            flash("Invalid date components provided, defaulting to today.", "warning")

        sun_events_for_effective_date = calculate_sun_events_cached(effective_date_str, effective_tz_name,
                                                                    effective_lat, effective_lon)
        try:
            effective_local_tz = pytz.timezone(effective_tz_name)
            dusk_str = sun_events_for_effective_date.get("astronomical_dusk", "21:00")
            dusk_time_obj = datetime.strptime(dusk_str, "%H:%M").time()
            dt_for_moon_local = effective_local_tz.localize(datetime.combine(effective_date_obj.date(), dusk_time_obj))
            moon_phase_for_effective_date = round(ephem.Moon(dt_for_moon_local.astimezone(pytz.utc)).phase, 1)
        except Exception:
            moon_phase_for_effective_date = "N/A"

        all_projects = db.query(Project).filter_by(user_id=user.id).order_by(Project.name).all()
        object_specific_sessions = db.query(JournalSession).filter_by(user_id=user.id,
                                                                      object_name=object_name).order_by(
            JournalSession.date_utc.desc()).all()

        projects_map = {p.id: p.name for p in all_projects}
        grouped_sessions_dict = {}
        for session in object_specific_sessions:
            project_id = session.project_id
            grouped_sessions_dict.setdefault(project_id, []).append(session)

        grouped_sessions = []
        sorted_project_ids = sorted([pid for pid in grouped_sessions_dict if pid],
                                    key=lambda pid: projects_map.get(pid, ''))
        for project_id in sorted_project_ids:
            sessions_in_group = grouped_sessions_dict[project_id]
            total_minutes = sum(s.calculated_integration_time_minutes or 0 for s in sessions_in_group)
            grouped_sessions.append({
                'is_project': True, 'project_name': projects_map.get(project_id, 'Unknown Project'),
                'sessions': sessions_in_group, 'total_integration_time': total_minutes
            })
        if None in grouped_sessions_dict:
            grouped_sessions.append({
                'is_project': False, 'project_name': 'Standalone Sessions', 'sessions': grouped_sessions_dict[None]
            })

        # --- RIG DATA LOADING ---
        rigs_from_db = db.query(Rig).filter_by(user_id=user.id).all()
        final_rigs_for_template = []
        for rig in rigs_from_db:
            efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(rig.telescope, rig.camera,
                                                                              rig.reducer_extender)
            fov_h = (degrees(2 * atan((
                                                  rig.camera.sensor_height_mm / 2.0) / efl)) * 60.0) if rig.camera and rig.camera.sensor_height_mm and efl else None
            final_rigs_for_template.append({
                "rig_id": rig.id,  # <-- THIS LINE IS THE FIX
                "rig_name": rig.rig_name,
                "effective_focal_length": efl,
                "f_ratio": f_ratio,
                "image_scale": scale,
                "fov_w_arcmin": fov_w,
                "fov_h_arcmin": fov_h
            })

        prefs = db.query(UiPref).filter_by(user_id=user.id).one_or_none()
        sort_preference = 'name-asc'
        if prefs and prefs.json_blob:
            try:
                sort_preference = json.loads(prefs.json_blob).get('rig_sort', 'name-asc')
            except:
                pass
        sorted_rigs = sort_rigs(final_rigs_for_template, sort_preference)

        object_main_details = get_ra_dec(object_name)
        if not object_main_details or object_main_details.get("RA (hours)") is None:
            flash(f"Details for '{object_name}' could not be found.", "error")
            return redirect(url_for('index'))

        is_project_active = object_main_details.get('ActiveProject', False)
        available_objects = db.query(AstroObject).filter_by(user_id=user.id).all()
        available_locations = db.query(Location).filter_by(user_id=user.id, active=True).all()
        default_location_obj = db.query(Location).filter_by(user_id=user.id, is_default=True).first()
        default_location_name = default_location_obj.name if default_location_obj else None

        return render_template('graph_view.html',
                               object_name=object_name,
                               alt_name=object_main_details.get("Common Name", object_name),
                               object_main_details=object_main_details,
                               available_rigs=sorted_rigs,
                               selected_day=effective_date_obj.day,
                               selected_month=effective_date_obj.month,
                               selected_year=effective_date_obj.year,
                               header_location_name=effective_location_name,
                               header_date_display=effective_date_obj.strftime('%d.%m.%Y'),
                               header_moon_phase=moon_phase_for_effective_date,
                               header_astro_dusk=sun_events_for_effective_date.get("astronomical_dusk", "N/A"),
                               header_astro_dawn=sun_events_for_effective_date.get("astronomical_dawn", "N/A"),
                               project_notes_from_config=object_main_details.get("Project", "N/A"),
                               is_project_active=is_project_active,
                               grouped_sessions=grouped_sessions,
                               object_specific_sessions=object_specific_sessions,
                               selected_session_data=selected_session_data,
                               selected_session_data_dict=selected_session_data_dict,
                               current_session_id=requested_session_id if selected_session_data else None,
                               graph_lat_param=effective_lat,
                               graph_lon_param=effective_lon,
                               graph_tz_name_param=effective_tz_name,
                               available_objects=available_objects,
                               all_projects=all_projects,
                               available_locations=available_locations,
                               default_location=default_location_name,
                               stellarium_api_url_base=STELLARIUM_API_URL_BASE,
                               today_date=datetime.now().strftime('%Y-%m-%d'))
    finally:
        db.close()

@app.route('/get_date_info/<object_name>')
def get_date_info(object_name):
    tz = pytz.timezone(g.tz_name)
    now = datetime.now(tz)  # current time in user's local timezone

    day = int(request.args.get('day') or now.day)
    month = int(request.args.get('month') or now.month)
    year = int(request.args.get('year') or now.year)

    # Use same time-of-day as index: current hour/minute
    local_time = tz.localize(datetime(year, month, day, now.hour, now.minute))
    phase = round(ephem.Moon(local_time).phase)

    local_date_str = f"{year}-{month:02d}-{day:02d}"
    sun_events = calculate_sun_events_cached(local_date_str,g.tz_name, g.lat, g.lon)

    return jsonify({
        "date": local_date_str,
        "phase": phase,
        "astronomical_dawn": sun_events.get("astronomical_dawn", "N/A"),
        "astronomical_dusk": sun_events.get("astronomical_dusk", "N/A")
    })


@app.route('/get_imaging_opportunities/<path:object_name>')
def get_imaging_opportunities(object_name):
    # Load object data from config or SIMBAD.
    data = get_ra_dec(object_name)
    if not data or data.get("RA (hours)") is None or data.get("DEC (degrees)") is None:
        return jsonify({"status": "error", "message": "Object has no valid RA/DEC."}), 400

    ra = data["RA (hours)"]
    dec = data["DEC (degrees)"]
    alt_name = data.get("Common Name", object_name)

    # Get imaging criteria.
    criteria = get_imaging_criteria()
    min_obs = criteria["min_observable_minutes"]
    min_alt = criteria["min_max_altitude"]
    max_moon = criteria["max_moon_illumination"]
    min_sep = criteria["min_angular_separation"]
    months = criteria.get("search_horizon_months", 6)

    local_tz = pytz.timezone(g.tz_name)
    today = datetime.now(local_tz).date()
    end_date = today + timedelta(days=months * 30)
    dates = [today + timedelta(days=i) for i in range((end_date - today).days)]

    # Local cache for sun events so each date is calculated only once.
    sun_events_cache = {}

    final_results = []

    for d in dates:
        date_str = d.strftime('%Y-%m-%d')
        # Check cache first. If not there, compute and store.
        if date_str not in sun_events_cache:
            sun_events_cache[date_str] = calculate_sun_events_cached(date_str, g.tz_name, g.lat, g.lon)
        sun_events = sun_events_cache[date_str]

        # Use the sun events to get, for example, the dusk time.
        dusk = sun_events.get("astronomical_dusk", "20:00")

        # Calculate observable duration and maximum altitude.
        altitude_threshold = g.user_config.get("altitude_threshold", 20)
        obs_duration, max_altitude, obs_from, obs_to = calculate_observable_duration_vectorized(
            ra, dec, g.lat, g.lon, date_str, g.tz_name, altitude_threshold
        )
        # Apply thresholds.
        if obs_duration.total_seconds() / 60 < min_obs:
            continue
        if max_altitude < min_alt:
            continue

        # Get the moon phase.
        local_time = local_tz.localize(datetime.combine(d, datetime.now().time()))
        moon_phase = ephem.Moon(local_time.astimezone(pytz.utc)).phase
        if moon_phase > max_moon:
            continue

        # Compute angular separation at dusk.
        try:
            dusk_time_obj = datetime.strptime(dusk, "%H:%M").time()
        except Exception:
            dusk_time_obj = datetime.strptime("20:00", "%H:%M").time()
        dusk_dt = local_tz.localize(datetime.combine(d, dusk_time_obj))
        dusk_utc = dusk_dt.astimezone(pytz.utc)

        location_obj = EarthLocation(lat=g.lat * u.deg, lon=g.lon * u.deg)
        frame = AltAz(obstime=Time(dusk_utc), location=location_obj)
        obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        moon_coord = get_body('moon', Time(dusk_utc), location=location_obj)
        obj_altaz = obj_coord.transform_to(frame)
        moon_altaz = moon_coord.transform_to(frame)
        separation = obj_altaz.separation(moon_altaz).deg
        if separation < min_sep:
            continue

        # Calculate individual scores.
        MIN_ALTITUDE = 20  # degrees threshold for a "good" altitude
        score_alt = 0 if max_altitude < MIN_ALTITUDE else min((max_altitude - MIN_ALTITUDE) / (90 - MIN_ALTITUDE), 1)
        score_duration = min(obs_duration.total_seconds() / (3600 * 12), 1)  # maximum 12 hours
        score_moon_illum = 1 - min(moon_phase / 100, 1)
        score_moon_sep = min(separation / 180, 1)
        score_moon_sep_dynamic = (1 - (moon_phase / 100)) + (moon_phase / 100) * score_moon_sep

        # Composite score using equal weights (adjust weights as desired).
        composite_score = 100 * (
                0.20 * score_alt + 0.15 * score_duration + 0.45 * score_moon_illum + 0.20 * score_moon_sep_dynamic
        )
        # Map composite score to stars (1 to 5 stars).
        stars = int(round((composite_score / 100) * 4)) + 1
        star_string = "★" * stars + "☆" * (5 - stars)

        final_results.append({
            "date": date_str,
            "obs_minutes": int(obs_duration.total_seconds() / 60),
            "from_time": obs_from.strftime('%H:%M') if obs_from else "N/A",
            "to_time": obs_to.strftime('%H:%M') if obs_to else "N/A",
            "max_alt": round(max_altitude, 1),
            "moon_illumination": round(moon_phase, 1),
            "moon_separation": round(separation, 1),
            "rating": star_string
        })

    return jsonify({"status": "success", "object": object_name, "alt_name": alt_name, "results": final_results})


@app.route('/generate_ics/<object_name>')
def generate_ics(object_name):
    # --- 1. Get parameters from the URL query string ---
    date_str = request.args.get('date')
    tz_name = request.args.get('tz')
    lat = float(request.args.get('lat'))
    lon = float(request.args.get('lon'))
    from_time_str = request.args.get('from_time')
    to_time_str = request.args.get('to_time')

    # Optional parameters for description
    max_alt = request.args.get('max_alt', 'N/A')
    moon_illum = request.args.get('moon_illum', 'N/A')
    obs_dur = request.args.get('obs_dur', 'N/A')

    if not all([date_str, tz_name, from_time_str, to_time_str]):
        return "Error: Missing required parameters.", 400
    if "N/A" in [from_time_str, to_time_str]:
        return "Error: Cannot create calendar event for an object with no observable time.", 400

    try:
        # --- 2. Calculate Precise Start and End Datetimes ---
        target_night_start_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        local_tz = pytz.timezone(tz_name)

        from_time = datetime.strptime(from_time_str, "%H:%M").time()
        to_time = datetime.strptime(to_time_str, "%H:%M").time()

        # --- NEW LOGIC to determine the correct calendar date ---
        # Calculate dusk on the "night of" date to use as a reference.
        sun_events_today = calculate_sun_events_cached(date_str, tz_name, lat, lon)
        dusk_str = sun_events_today.get("astronomical_dusk", "20:00")
        dusk_time = datetime.strptime(dusk_str, "%H:%M").time()

        # If the observation starts before that evening's dusk, it must be on the next calendar day.
        start_date = target_night_start_date
        if from_time < dusk_time:
            start_date += timedelta(days=1)

        # Determine the end date. If the 'to_time' is earlier than 'from_time', it crosses another midnight.
        end_date = start_date
        if to_time < from_time:
            end_date += timedelta(days=1)
        # --- END NEW LOGIC ---

        start_time_local_naive = datetime.combine(start_date, from_time)
        end_time_local_naive = datetime.combine(end_date, to_time)

        start_time_local = local_tz.localize(start_time_local_naive)
        end_time_local = local_tz.localize(end_time_local_naive)

        # --- 3. Get Object's Common Name ---
        object_details = get_ra_dec(object_name)
        common_name = object_details.get("Common Name", object_name)

        # --- 4. Create the Calendar Event ---
        c = Calendar()
        e = Event()
        e.name = f"Imaging: {common_name}"
        e.begin = arrow.get(start_time_local)
        e.end = arrow.get(end_time_local)
        e.location = f"Lat: {lat}, Lon: {lon}"
        e.description = (
            f"Astrophotography opportunity for {common_name} ({object_name}).\n\n"
            f"Details for the night starting {date_str}:\n"
            f"- Observable From: {from_time_str}\n"
            f"- Observable To: {to_time_str}\n"
            f"- Observable Duration: {obs_dur} min\n"
            f"- Max Altitude: {max_alt}°\n"
            f"- Moon Illumination: {moon_illum}%\n\n"
            f"Event times are set to the calculated observable window for this night."
        )
        c.events.add(e)

        # --- 5. Return the .ics file ---
        ics_content = c.serialize()
        filename = f"imaging_{object_name.replace(' ', '_')}_{start_date.strftime('%Y-%m-%d')}.ics"

        return ics_content, 200, {
            'Content-Type': 'text/calendar; charset=utf-8',
            'Content-Disposition': f'attachment; filename="{filename}"'
        }

    except Exception as ex:
        print(f"ERROR generating ICS file: {ex}")
        return f"An error occurred while generating the calendar file: {ex}", 500


@app.route('/download_rig_config')
@login_required
def download_rig_config():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash("User not found.", "error")
            return redirect(url_for('config_form'))

        # --- Generate rigs doc from DB ---
        comps = db.query(Component).filter_by(user_id=u.id).all()
        rigs = db.query(Rig).filter_by(user_id=u.id).order_by(Rig.rig_name).all()

        def bykind(k):
            return [c for c in comps if c.kind == k]

        rigs_doc = {
            "components": {
                "telescopes": [
                    {"id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm}
                    for c in bykind("telescope")
                ],
                "cameras": [
                    {"id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                     "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um}
                    for c in bykind("camera")
                ],
                "reducers_extenders": [
                    {"id": c.id, "name": c.name, "factor": c.factor}
                    for c in bykind("reducer_extender")
                ],
            },
            "rigs": []  # We will populate this next
        }

        # --- Calculate metrics for each rig ---
        final_rigs_list = []
        for r in rigs:
            tel_obj = next((c for c in comps if c.id == r.telescope_id), None)
            cam_obj = next((c for c in comps if c.id == r.camera_id), None)
            red_obj = next((c for c in comps if c.id == r.reducer_extender_id), None)

            efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)

            final_rigs_list.append({
                "rig_id": r.id,
                "rig_name": r.rig_name,
                "telescope_id": r.telescope_id,
                "camera_id": r.camera_id,
                "reducer_extender_id": r.reducer_extender_id,
                "effective_focal_length": efl,
                "f_ratio": f_ratio,
                "image_scale": scale,
                "fov_w_arcmin": fov_w
            })

        rigs_doc["rigs"] = final_rigs_list  # Add the populated list to the doc

        # --- Create in-memory file ---
        yaml_string = yaml.dump(rigs_doc, sort_keys=False, allow_unicode=True)
        str_io = io.BytesIO(yaml_string.encode('utf-8'))

        # Determine filename
        if SINGLE_USER_MODE:
            download_name = "rigs_default.yaml"
        else:
            download_name = f"rigs_{username}.yaml"

        return send_file(str_io, as_attachment=True, download_name=download_name, mimetype='text/yaml')

    except Exception as e:
        db.rollback()
        flash(f"Error generating rig config: {e}", "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('config_form'))
    finally:
        db.close()

@app.route('/download_journal_photos')
@login_required
def download_journal_photos():
    """
    Finds all journal images for the current user, creates a ZIP file in memory,
    and sends it as a download.
    """
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)

    # Check if the user's upload directory exists and has files
    if not os.path.isdir(user_upload_dir):
        flash("No journal photos found to download.", "info")
        return redirect(url_for('config_form'))

    # Use an in-memory buffer to build the ZIP file without writing to disk
    memory_file = io.BytesIO()

    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Walk through the user's directory and add all files to the ZIP
        for root, dirs, files in os.walk(user_upload_dir):
            for file in files:
                # Create the full path to the file
                file_path = os.path.join(root, file)
                # Add the file to the zip, using just the filename as the archive name
                zf.write(file_path, arcname=file)

    # After the 'with' block, the ZIP is built in memory_file.
    # Move the buffer's cursor to the beginning.
    memory_file.seek(0)

    # Create a dynamic filename for the download
    timestamp = datetime.now().strftime('%Y-%m-%d')
    download_name = f"nova_journal_photos_{username}_{timestamp}.zip"

    # Send the in-memory file to the user
    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name=download_name
    )


@app.route('/import_journal_photos', methods=['POST'])
@login_required
def import_journal_photos():
    """
    Handles the upload of a ZIP archive and safely extracts its contents
    into the user's upload directory.
    """
    if 'file' not in request.files:
        flash("No file selected for photo import.", "error")
        return redirect(url_for('config_form'))

    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith('.zip'):
        flash("Please select a valid .zip file to import.", "error")
        return redirect(url_for('config_form'))

    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
    os.makedirs(user_upload_dir, exist_ok=True)  # Ensure the destination exists

    try:
        # Check if the file is a valid ZIP archive
        if not zipfile.is_zipfile(file):
            flash("Import failed: The uploaded file is not a valid ZIP archive.", "error")
            return redirect(url_for('config_form'))

        # Rewind the file stream after the check
        file.seek(0)

        with zipfile.ZipFile(file, 'r') as zf:
            for member in zf.infolist():
                # 🔒 Security Check: Prevent path traversal attacks (Zip Slip)
                # This ensures files are only extracted inside the user's directory.
                target_path = os.path.join(user_upload_dir, member.filename)
                if not os.path.abspath(target_path).startswith(os.path.abspath(user_upload_dir)):
                    raise ValueError(f"Illegal file path in ZIP: {member.filename}")

                # We only want to extract files, not directories
                if not member.is_dir():
                    # extract() will overwrite existing files, which is what we want for a restore
                    zf.extract(member, user_upload_dir)

        flash("Journal photos imported successfully!", "success")

    except zipfile.BadZipFile:
        flash("Import failed: The ZIP file appears to be corrupted.", "error")
    except Exception as e:
        flash(f"An unexpected error occurred during import: {e}", "error")

    return redirect(url_for('config_form'))
@app.route('/import_rig_config', methods=['POST'])
@login_required
def import_rig_config():
    if 'file' not in request.files:
        flash("No file selected for rigs import.", "error")
        return redirect(url_for('config_form'))

    file = request.files['file']
    if not file or file.filename == '':
        flash("No file selected for rigs import.", "error")
        return redirect(url_for('config_form'))

    if file and file.filename.lower().endswith(('.yaml', '.yml')):
        try:
            new_rigs_data = yaml.safe_load(file.read().decode('utf-8'))
            if not isinstance(new_rigs_data, dict) or 'components' not in new_rigs_data or 'rigs' not in new_rigs_data:
                raise yaml.YAMLError("Invalid rigs file structure. Missing 'components' or 'rigs' keys.")

            username = "default" if SINGLE_USER_MODE else current_user.username

            # Use the new central function to get the correct path
            rigs_filepath = rig_config.get_rig_config_path(username, SINGLE_USER_MODE)

            if os.path.exists(rigs_filepath):
                backup_dir = os.path.join(os.path.dirname(rigs_filepath), "backups")
                os.makedirs(backup_dir, exist_ok=True)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_path = os.path.join(backup_dir, f"{os.path.basename(rigs_filepath)}_backup_{timestamp}.yaml")
                shutil.copy(rigs_filepath, backup_path)

            # Use save_rig_config which now also uses the central path function
            rig_config.save_rig_config(username, new_rigs_data, SINGLE_USER_MODE)
            try:
                import_from_existing_yaml(username, clear_existing=False)
                print(f"[IMPORT→DB] Synced DB from YAMLs for user '{username}' after rig import.")
            except Exception as e:
                print(f"[IMPORT→DB] WARNING: Could not sync DB from YAMLs after rig import: {e}")
                flash("File saved, but syncing to database failed. Please check logs.", "warning")

            flash("Rigs configuration imported successfully.", "success")
        except (yaml.YAMLError, Exception) as e:
            flash(f"Error importing rigs file: {e}", "error")

    else:
        flash("Invalid file type. Please upload a .yaml or .yml file.", "error")

    return redirect(url_for('config_form'))

@app.route('/api/get_monthly_plot_data/<path:object_name>')
def get_monthly_plot_data(object_name):
    # This function provides data for the monthly chart view.
    data = get_ra_dec(object_name)
    if not data or data.get('RA (hours)') is None:
        return jsonify({"error": "Object data not found"}), 404

    year = int(request.args.get('year'))
    month = int(request.args.get('month'))
    lat = float(request.args.get('plot_lat', g.lat))
    lon = float(request.args.get('plot_lon', g.lon))
    tz_name = request.args.get('plot_tz', g.tz_name)
    local_tz = pytz.timezone(tz_name)

    num_days = calendar.monthrange(year, month)[1]
    dates, obj_altitudes, moon_altitudes = [], [], []

    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=data['RA (hours)'] * u.hourangle, dec=data['DEC (degrees)'] * u.deg)

    for day in range(1, num_days + 1):
        local_midnight = local_tz.localize(datetime(year, month, day, 0, 0))
        time_astropy = Time(local_midnight.astimezone(pytz.utc))

        altaz_frame = AltAz(obstime=time_astropy, location=location)
        obj_alt = sky_coord.transform_to(altaz_frame).alt.deg
        moon_coord = get_body('moon', time_astropy, location)
        moon_alt = moon_coord.transform_to(altaz_frame).alt.deg

        dates.append(local_midnight.strftime('%Y-%m-%d'))
        obj_altitudes.append(obj_alt)
        moon_altitudes.append(moon_alt)

    return jsonify({
        "dates": dates,
        "object_alt": obj_altitudes,
        "moon_alt": moon_altitudes
    })


@app.route('/api/get_yearly_plot_data/<path:object_name>')
def get_yearly_plot_data(object_name):
    # This function provides data for the yearly chart view.
    data = get_ra_dec(object_name)
    if not data or data.get('RA (hours)') is None:
        return jsonify({"error": "Object data not found"}), 404

    year = int(request.args.get('year'))
    lat = float(request.args.get('plot_lat', g.lat))
    lon = float(request.args.get('plot_lon', g.lon))
    tz_name = request.args.get('plot_tz', g.tz_name)
    local_tz = pytz.timezone(tz_name)

    dates, obj_altitudes, moon_altitudes = [], [], []

    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=data['RA (hours)'] * u.hourangle, dec=data['DEC (degrees)'] * u.deg)

    for month in range(1, 13):
        local_midnight = local_tz.localize(datetime(year, month, 15, 0, 0))  # Check mid-month
        time_astropy = Time(local_midnight.astimezone(pytz.utc))

        altaz_frame = AltAz(obstime=time_astropy, location=location)
        obj_alt = sky_coord.transform_to(altaz_frame).alt.deg
        moon_coord = get_body('moon', time_astropy, location)
        moon_alt = moon_coord.transform_to(altaz_frame).alt.deg

        dates.append(local_midnight.strftime('%Y-%m-15'))
        obj_altitudes.append(obj_alt)
        moon_altitudes.append(moon_alt)

    return jsonify({
        "dates": dates,
        "object_alt": obj_altitudes,
        "moon_alt": moon_altitudes
    })

@app.route('/api/get_plot_data/<path:object_name>')
def get_plot_data(object_name):
    """
    API endpoint to provide all necessary data for client-side chart rendering.
    Returns:
      {
        times: [ISO strings incl. sentinel first/last],
        object_alt, object_az, moon_alt, moon_az, horizon_mask_alt: same length as times,
        sun_events: { current:{sunset,astronomical_dusk,...}, next:{astronomical_dawn,sunrise,...} },
        transit_time, date, timezone
      }
    """
    # --- 1) Resolve object RA/DEC ---
    data = get_ra_dec(object_name)
    if not data:
        return jsonify({"error": "Object data not found or invalid."}), 404

    ra = data.get('RA (hours)')
    dec = data.get('DEC (deg)', data.get('DEC (degrees)'))  # tolerate both keys

    if ra is None or dec is None:
        return jsonify({"error": "RA/DEC missing for object."}), 404

    ra = float(ra)
    dec = float(dec)

    # --- 2) Read params with SAFE fallbacks (treat blank as missing) ---
    plot_lat_str  = (request.args.get('plot_lat', '') or '').strip()
    plot_lon_str  = (request.args.get('plot_lon', '') or '').strip()
    plot_tz_name  = (request.args.get('plot_tz', '') or '').strip()
    plot_loc_name = (request.args.get('plot_loc_name', '') or '').strip()

    # Fallbacks from g / config if blanks were passed
    try:
        lat = float(plot_lat_str) if plot_lat_str else float(getattr(g, "lat", 0.0))
        lon = float(plot_lon_str) if plot_lon_str else float(getattr(g, "lon", 0.0))
        if not plot_tz_name:
            plot_tz_name = getattr(g, "tz_name", "UTC")
        if not plot_loc_name:
            plot_loc_name = getattr(g, "location_name", None) or g.user_config.get("default_location", "")
        local_tz = pytz.timezone(plot_tz_name)
    except Exception:
        return jsonify({"error": "Invalid location or timezone data."}), 400

    now_local = datetime.now(local_tz)
    day   = int(request.args.get('day',   now_local.day))
    month = int(request.args.get('month', now_local.month))
    year  = int(request.args.get('year',  now_local.year))
    local_date = f"{year:04d}-{month:02d}-{day:02d}"

    # 1. Fetch the weather forecast for the location
    weather_data = get_hybrid_weather_forecast(lat, lon)
    weather_forecast_series = []

    # 2. Process the data if the fetch was successful
    if weather_data and isinstance(weather_data.get('dataseries'), list):
        try:
            # The 'init' time is a string like "YYYYMMDDHH" (e.g., "2025100400")
            init_str = weather_data.get('init', '')
            init_time = datetime(
                year=int(init_str[0:4]), month=int(init_str[4:6]), day=int(init_str[6:8]),
                hour=int(init_str[8:10]), tzinfo=timezone.utc
            )

            for block in weather_data['dataseries']:
                # Use .get to avoid KeyError for fields not present in the 'meteo' product
                timepoint_hours = block.get('timepoint')
                if timepoint_hours is None:
                    # Skip malformed entries
                    continue

                start_time = init_time + timedelta(hours=int(timepoint_hours))
                end_time = start_time + timedelta(hours=3)  # 7Timer! uses 3-hour blocks

                weather_forecast_series.append({
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "cloudcover": block.get("cloudcover"),       # present in astro & meteo
                    "seeing": block.get("seeing"),               # present only in astro (first ~72h)
                    "transparency": block.get("transparency"),   # present only in astro (first ~72h)
                })
        except Exception as e:
            # Keep a single concise log; avoid spamming runtime when optional fields are missing
            print(f"Error processing 7Timer! data (tolerated): {e}")



    # --- 3) Build time grid and object/Moon series ---
    times_local, times_utc = get_common_time_arrays(plot_tz_name, local_date, sampling_interval_minutes=5)

    location   = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)

    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_obj = sky_coord.transform_to(altaz_frame)
    altitudes = altaz_obj.alt.deg
    azimuths  = (altaz_obj.az.deg + 360.0) % 360.0  # normalize to [0,360)


    # Horizon mask (per-location)
    location_config   = g.user_config.get("locations", {}).get(plot_loc_name, {}) if isinstance(g.user_config, dict) else {}
    horizon_mask      = location_config.get("horizon_mask")
    altitude_threshold = g.user_config.get("altitude_threshold", 20)

    if horizon_mask and isinstance(horizon_mask, list) and len(horizon_mask) > 1:
        sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
        horizon_mask_altitudes = [interpolate_horizon(az, sorted_mask, altitude_threshold) for az in azimuths]
    else:
        # No custom mask => flat threshold
        horizon_mask_altitudes = [altitude_threshold] * len(azimuths)

    # Moon series (vector-safe path is fine, but loop is explicit & robust)
    moon_altitudes = []
    moon_azimuths = []
    for t in times_utc:
        t_ast = Time(t)
        moon_icrs = get_body('moon', t_ast, location=location)
        moon_altaz = moon_icrs.transform_to(AltAz(obstime=t_ast, location=location))
        moon_altitudes.append(moon_altaz.alt.deg)
        moon_azimuths.append((moon_altaz.az.deg + 360.0) % 360.0)

    # Sun events and transit
    sun_events_curr = calculate_sun_events_cached(local_date, plot_tz_name, lat, lon)
    next_date_str   = (datetime.strptime(local_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    sun_events_next = calculate_sun_events_cached(next_date_str, plot_tz_name, lat, lon)
    transit_time_str = calculate_transit_time(ra, dec, lat, lon, plot_tz_name, local_date)

    # --- 4) Force exactly 24h window: prepend/append sentinels ---
    start_time = times_local[0]
    end_time   = start_time + timedelta(hours=24)
    final_times_iso = [start_time.isoformat()] + [t.isoformat() for t in times_local] + [end_time.isoformat()]

    plot_data = {
        "times": final_times_iso,
        "object_alt": [None] + list(altitudes) + [None],
        "object_az":  [None] + list(azimuths)  + [None],
        "moon_alt":   [None] + moon_altitudes  + [None],
        "moon_az":    [None] + moon_azimuths   + [None],
        "horizon_mask_alt": [None] + horizon_mask_altitudes + [None],
        "sun_events": {"current": sun_events_curr, "next": sun_events_next},
        "transit_time": transit_time_str,
        "date": local_date,
        "timezone": plot_tz_name,
        "weather_forecast": weather_forecast_series
    }
    return jsonify(plot_data)

# =============================================================================
# Main Entry Point
# =============================================================================
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
    migrate_journal_data()
    trigger_startup_cache_workers() # This runs second

import logging

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

if not SINGLE_USER_MODE:
    @app.cli.command("init-db")
    def init_db_command():
        """Creates database tables and the first admin user."""
        # Create the tables based on your db.Model classes
        db.create_all()
        print("✅ Initialized the database tables.")

        # Check if a user already exists to prevent running this twice
        if db.session.scalar(db.select(User).limit(1)):
            print("-> Database already contains users. Skipping admin creation.")
            return

        # If no users exist, prompt to create the first one
        print("--- Create First Admin User ---")
        username = input("Enter username for admin: ")
        password = getpass.getpass("Enter password for admin: ")

        # Create the user object and save it to the database
        admin_user = User(username=username)
        admin_user.set_password(password)
        db.session.add(admin_user)
        db.session.commit()
        print(f"✅ Admin user '{username}' created successfully!")

@app.route('/api/internal/provision_user', methods=['POST'])
def provision_user():
    data = request.get_json()
    provided_key = request.headers.get('X-Api-Key')
    expected_key = os.environ.get('PROVISIONING_API_KEY')

    if not expected_key or provided_key != expected_key:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password required"}), 400

    with app.app_context():
        # Check if the user already exists
        existing_user = db.session.scalar(db.select(User).where(User.username == username))

        if existing_user:
            # If the user exists, UPDATE their password
            existing_user.set_password(password)
            db.session.commit()
            print(f"✅ Password updated for user '{username}' via API.")
            return jsonify({"status": "success", "message": f"User {username} password updated"}), 200
        else:
            # If the user does not exist, CREATE them
            new_user = User(username=username)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            print(f"✅ User '{username}' provisioned in database via API.")
            try:
                load_user_config(username)
                load_journal(username)
            except Exception as e:
                print(f"❌ ERROR: Could not create YAML files for '{username}': {e}")
            return jsonify({"status": "success", "message": f"User {username} provisioned"}), 201

def disable_user(username: str) -> bool:
    """
    Mark a user as inactive/disabled without deleting them.
    Returns True if the user was found and disabled, False otherwise.
    """
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == username))
        if not user:
            return False
        try:
            user.active = False
            db.session.commit()
            print(f"✅ Disabled user '{username}'.")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Failed to disable user '{username}': {e}")
            return False

def enable_user(username: str) -> bool:
    """
    Re-enable a previously disabled user.
    """
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == username))
        if not user:
            return False
        try:
            user.active = True
            db.session.commit()
            print(f"✅ Enabled user '{username}'.")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Failed to enable user '{username}': {e}")
            return False

def delete_user(username: str) -> bool:
    """
    Hard-delete a user record. Optionally remove that user's on-disk files if you add that logic.
    """
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == username))
        if not user:
            return False
        try:
            db.session.delete(user)
            db.session.commit()
            print(f"✅ Deleted user '{username}' from DB.")
            # If you also want to remove YAML/journal/config files, call your remover here.
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Failed to delete user '{username}': {e}")
            return False

@app.route('/api/internal/deprovision_user', methods=['POST'])
def deprovision_user():
    api_key = request.headers.get('X-Api-Key')
    if api_key != os.environ.get('PROVISIONING_API_KEY'):
        return jsonify({"status":"error","message":"unauthorized"}), 401

    data = request.get_json(force=True) or {}
    username = data.get('username')
    action = (data.get('action') or 'disable').lower()  # 'disable' or 'delete'

    if not username:
        return jsonify({"status":"error","message":"missing username"}), 400

    if action == 'delete':
        ok = delete_user(username)
        return (jsonify({"status": "success", "message": "deleted"}), 200) if ok else (jsonify({"status":"not_found"}), 404)
    else:
        ok = disable_user(username)
        return (jsonify({"status": "success", "message": "disabled"}), 200) if ok else (jsonify({"status":"not_found"}), 404)

@app.route('/uploads/<path:username>/<path:filename>')
@login_required
def get_uploaded_image(username, filename):
    # Security check:
    # In multi-user mode, only let a user see their own uploads.
    # In single-user mode, 'default' user can see 'default' uploads.
    if not SINGLE_USER_MODE and current_user.username != username:
        return "Forbidden", 403

    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
    return send_from_directory(user_upload_dir, filename)

if __name__ == '__main__':
    # Start the background thread to check for updates
    update_thread = threading.Thread(target=check_for_updates)
    update_thread.daemon = True
    update_thread.start()
    # Automatically disable debugger and reloader if set by the updater
    disable_debug = os.environ.get("NOVA_NO_DEBUG") == "1"

    app.run(
        debug=not disable_debug,
        use_reloader=False,
        # use_reloader=not disable_debug,
        host='0.0.0.0',
        port=5001
    )


#
# --- YAML Portability Routes -----------------------------------------------
@app.route("/tools/export/<username>", methods=["GET"])
@login_required
def export_yaml_for_user(username):
    # Only allow exporting self in multi-user; admin can export anyone (basic guard, adjust as needed)
    if not SINGLE_USER_MODE and current_user.username != username and current_user.username != "admin":
        flash("Not authorized to export another user's data.", "error")
        return redirect(url_for("index"))
    ok = export_user_to_yaml(username, out_dir=CONFIG_DIR)
    if not ok:
        flash("Export failed (no such user or empty data).", "error")
        return redirect(url_for("index"))
    # Package into a ZIP so users get all three files at once
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    zip_name = f"nova_export_{username}_{ts}.zip"
    zip_path = os.path.join(INSTANCE_PATH, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        cfg_file = "config_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"config_{username}.yaml"
        jrn_file = "journal_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"journal_{username}.yaml"
        rigs_file = "rigs_default.yaml"
        for fn in [cfg_file, jrn_file, rigs_file]:
            full = os.path.join(CONFIG_DIR, fn)
            if os.path.exists(full):
                zf.write(full, arcname=fn)
    return send_file(zip_path, as_attachment=True, download_name=zip_name)

@app.route("/tools/import", methods=["POST"])
@login_required
def import_yaml_for_user():
    """
    Expect multipart form-data with fields:
      - username (optional in single-user; otherwise required)
      - config_file
      - rigs_file
      - journal_file
      - clear_existing = 'true'|'false'
    """
    username = request.form.get("username") or ("default" if SINGLE_USER_MODE else None)
    if not username:
        flash("Username is required in multi-user mode.", "error")
        return redirect(url_for("index"))

    # Basic guard: only allow importing for self unless admin
    if not SINGLE_USER_MODE and current_user.username != username and current_user.username != "admin":
        flash("Not authorized to import for another user.", "error")
        return redirect(url_for("index"))

    try:
        cfg = request.files.get("config_file")
        rigs = request.files.get("rigs_file")
        jrn = request.files.get("journal_file")
        if not (cfg and rigs and jrn):
            flash("Please provide config, rigs, and journal YAML files.", "error")
            return redirect(url_for("index"))

        # Persist to temp paths
        tmp_dir = os.path.join(INSTANCE_PATH, "tmp_import")
        os.makedirs(tmp_dir, exist_ok=True)
        cfg_path = os.path.join(tmp_dir, f"cfg_{uuid.uuid4().hex}.yaml")
        rigs_path = os.path.join(tmp_dir, f"rigs_{uuid.uuid4().hex}.yaml")
        jrn_path = os.path.join(tmp_dir, f"jrn_{uuid.uuid4().hex}.yaml")
        cfg.save(cfg_path); rigs.save(rigs_path); jrn.save(jrn_path)

        clear_existing = (request.form.get("clear_existing", "false").lower() == "true")
        ok = import_user_from_yaml(username, cfg_path, rigs_path, jrn_path, clear_existing=clear_existing)

        # Cleanup temp
        for p in [cfg_path, rigs_path, jrn_path]:
            try: os.remove(p)
            except Exception: pass

        if ok:
            flash("Import completed successfully!", "success")
        else:
            flash("Import failed. See server logs for details.", "error")
    except Exception as e:
        print(f"[IMPORT] ERROR: {e}")
        flash("Import crashed. Check logs.", "error")
    return redirect(url_for("index"))
# Admin-only repair route for deduplication and backfill
@app.route("/tools/repair_db", methods=["POST"])
@login_required
def repair_db_now():
    if not SINGLE_USER_MODE and current_user.username != "admin":
        flash("Not authorized.", "error")
        return redirect(url_for("index"))
    try:
        repair_journals(dry_run=False)
        flash("Database repair completed.", "success")
    except Exception as e:
        flash(f"Repair failed: {e}", "error")
    return redirect(url_for("index"))