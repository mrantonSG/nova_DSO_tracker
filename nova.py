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
from sqlalchemy import text, func
from sqlalchemy.orm import selectinload
from werkzeug.security import generate_password_hash, check_password_hash
import getpass
import jwt

from skyfield.api import Loader
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

# === DB: Unified SQLAlchemy setup ============================================
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, Date,
    ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session
import bleach
from bleach.css_sanitizer import CSSSanitizer

try:
    import fcntl  # POSIX-only; no-op lock on Windows if import fails
    _HAS_FCNTL = True
except Exception:
    _HAS_FCNTL = False

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

import re

APP_VERSION = "4.4.0"

INSTANCE_PATH = globals().get("INSTANCE_PATH") or os.path.join(os.getcwd(), "instance")
os.makedirs(INSTANCE_PATH, exist_ok=True)
DB_PATH = os.path.join(INSTANCE_PATH, 'app.db')
DB_URI = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URI, echo=False, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
Base = declarative_base()


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


def get_db():
    """Use inside request context or background tasks."""
    return SessionLocal()

# === DEFINE VARS NEEDED BY INIT FUNCTION ===
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "config_templates")
CACHE_DIR = os.path.join(INSTANCE_PATH, "cache")


def get_weather_data_single_attempt(url: str, lat: float, lon: float) -> dict | None:
    """
    Fetches weather data from a single URL with robust error handling.
    Returns a dictionary on success, None on any failure.
    """
    try:
        # Use a reasonable timeout (e.g., 10 seconds)
        r = requests.get(url, timeout=10)

        # --- 1. Check for HTTP errors (like 500, 502, 404, etc.) ---
        if r.status_code != 200:
            print(f"[Weather Func] ERROR: Received non-200 status code {r.status_code} for lat={lat}, lon={lon}")
            # Log the error page content for debugging
            print(f"[Weather Func] Response text (first 200 chars): {r.text[:200]}")
            return None

        # --- 2. Try to parse the JSON ---
        # r.json() will automatically handle content-type and raise JSONDecodeError
        data = r.json()
        return data

    except requests.exceptions.JSONDecodeError as e:
        # This is the exact error from your logs!
        print(f"[Weather Func] ERROR: Failed to decode JSON for lat={lat}, lon={lon}. Error: {e}")
        # Log the problematic text that isn't JSON
        print(f"[Weather Func] Response text (first 200 chars): {r.text[:200]}")
        return None

    except requests.exceptions.RequestException as e:
        # This handles timeouts, DNS errors, connection errors, etc.
        print(f"[Weather Func] ERROR: Request failed for lat={lat}, lon={lon}. Error: {e}")
        return None

    except Exception as e:
        # Catch any other unexpected errors
        print(f"[Weather Func] ERROR: An unexpected error occurred for lat={lat}, lon={lon}. Error: {e}")
        return None


def get_weather_data_with_retries(lat: float, lon: float, product: str = "meteo") -> dict | None:
    """
    Attempts to fetch weather data from 7Timer! with retries and exponential backoff.
    Builds the URL and calls the single-attempt helper function.

    product:
      - "meteo": standard meteorological product (meteo.php, with profiles etc.)
      - "astro": astronomical product (astro.php, includes seeing/transparency)
    """

    # --- Build the 7Timer! URL correctly depending on product ---
    if product == "astro":
        # ASTRO product uses astro.php; no separate 'product' parameter in the URL
        base_url = "http://www.7timer.info/bin/astro.php"
        url = f"{base_url}?lon={lon}&lat={lat}&ac=0&unit=metric&output=json"
    else:
        # Default / METEO product
        base_url = "http://www.7timer.info/bin/meteo.php"
        # Here the 'product' parameter is still useful (e.g. 'meteo')
        url = f"{base_url}?lon={lon}&lat={lat}&product={product}&ac=0&unit=metric&output=json"

    retries = 3
    delay_seconds = 5  # Start with a 5-second delay

    for i in range(retries):
        # Call our robust single-attempt helper
        data = get_weather_data_single_attempt(url, lat, lon)

        if data is not None:
            # Success!
            # print(f"[Weather Func] Successfully fetched data for lat={lat}, lon={lon} on attempt {i + 1} (product={product})")
            return data

        # If data is None, it failed. Log and retry (if not the last attempt).
        if i < retries - 1:
            print(f"[Weather Func] WARN: Attempt {i + 1} for product='{product}' failed for lat={lat}, lon={lon}. "
                  f"Retrying in {delay_seconds} seconds...")
            time.sleep(delay_seconds)
            delay_seconds *= 2  # Exponential backoff
        else:
            print(f"[Weather Func] ERROR: All attempts ({retries}) failed for product='{product}' at lat={lat}, lon={lon}.")

    return None

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
    config_file_path = os.path.join(config_dir, 'config_default.yaml')
    if not os.path.exists(config_file_path):
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
                ('rigs_default.yaml', 'rigs_guest_user.yaml'),
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


# --- MODELS ------------------------------------------------------------------
class DbUser(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=True)
    active = Column(Boolean, nullable=False, default=True)

    # --- Relationships ---
    locations = relationship("Location", back_populates="user", cascade="all, delete-orphan")
    objects = relationship("AstroObject", foreign_keys="AstroObject.user_id", back_populates="user",
                           cascade="all, delete-orphan")
    saved_views = relationship("SavedView", foreign_keys="SavedView.user_id", back_populates="user",
                               cascade="all, delete-orphan")
    components = relationship("Component", foreign_keys="Component.user_id", back_populates="user",
                              cascade="all, delete-orphan")
    rigs = relationship("Rig", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("JournalSession", back_populates="user", cascade="all, delete-orphan")
    ui_prefs = relationship("UiPref", back_populates="user", uselist=False, cascade="all, delete-orphan")

    # --- This is the line we added to fix the test ---
    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = 'projects'
    # The project_id from YAML will be our primary key. It's a string (UUID).
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    name = Column(String(256), nullable=False)

    # --- NEW FIELDS FOR PROJECT DETAIL PAGE ---
    target_object_name = Column(String(256), nullable=True)  # Primary object for the project
    description_notes = Column(Text, nullable=True)  # Project-level story/learnings (rich text)
    framing_notes = Column(Text, nullable=True)  # Framing/composition notes (rich text)
    processing_notes = Column(Text, nullable=True)  # Processing workflow (rich text)
    final_image_file = Column(String(256), nullable=True)  # Path to the final image (similar to session_image_file)
    goals = Column(Text, nullable=True)  # Goals and completion status (rich text)
    status = Column(String(32), nullable=False, default="In Progress")  # e.g., 'In Progress', 'Completed', 'On Hold'

    user = relationship("DbUser", back_populates="projects")
    sessions = relationship("JournalSession", back_populates="project")

    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_user_project_name'),)

class SavedView(Base):
    __tablename__ = 'saved_views'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    name = Column(String(256), nullable=False)
    description = Column(String(500), nullable=True) # <-- New
    settings_json = Column(Text, nullable=False)
    is_shared = Column(Boolean, nullable=False, default=False, index=True) # <-- New
    original_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True) # <-- New
    original_item_id = Column(Integer, nullable=True, index=True) # <-- New
    user = relationship("DbUser", foreign_keys=[user_id], back_populates="saved_views")
    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_user_view_name'),)

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


class SavedFraming(Base):
    __tablename__ = 'saved_framings'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    object_name = Column(String(256), nullable=False)

    # Framing Data
    rig_id = Column(Integer, ForeignKey('rigs.id', ondelete="SET NULL"), nullable=True)
    rig_name = Column(String(256), nullable=True)
    ra = Column(Float, nullable=True)
    dec = Column(Float, nullable=True)
    rotation = Column(Float, nullable=True)

    # Survey Data
    survey = Column(String(256), nullable=True)
    blend_survey = Column(String(256), nullable=True)
    blend_opacity = Column(Float, nullable=True)

    # Mosaic Data
    mosaic_cols = Column(Integer, default=1)
    mosaic_rows = Column(Integer, default=1)
    mosaic_overlap = Column(Float, default=10.0)

    updated_at = Column(Date, default=datetime.utcnow)

    user = relationship("DbUser", backref="saved_framings")
    __table_args__ = (UniqueConstraint('user_id', 'object_name', name='uq_user_object_framing'),)

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
    project_name = Column(Text, nullable=True)
    is_shared = Column(Boolean, nullable=False, default=False, index=True)
    shared_notes = Column(Text, nullable=True)
    original_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True)
    user = relationship("DbUser", foreign_keys=[user_id], back_populates="objects")
    original_item_id = Column(Integer, nullable=True, index=True)
    catalog_sources = Column(Text, nullable=True)
    catalog_info = Column(Text, nullable=True)
    __table_args__ = (UniqueConstraint('user_id', 'object_name', name='uq_user_object'),)

    def to_dict(self):
        """Converts this object into a YAML-safe dictionary."""
        return {
            # Base fields
            "Object": self.object_name,
            "Common Name": self.common_name,
            "RA (hours)": self.ra_hours,
            "DEC (degrees)": self.dec_deg,
            "Type": self.type,
            "Constellation": self.constellation,
            "Magnitude": self.magnitude,
            "Size": self.size,
            "SB": self.sb,

            # Compatibility aliases
            "Name": self.common_name,
            "RA": self.ra_hours,
            "DEC": self.dec_deg,

            # Project fields
            "ActiveProject": self.active_project,
            "Project": self.project_name,

            # Sharing fields
            "is_shared": self.is_shared,
            "shared_notes": self.shared_notes,
            "original_user_id": self.original_user_id,
            "original_item_id": self.original_item_id,

            # Catalog metadata
            "catalog_sources": self.catalog_sources,
            "catalog_info": self.catalog_info
        }


class Component(Base):
    __tablename__ = 'components'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    kind = Column(String(32), nullable=False)  # 'telescope' | 'camera' | 'reducer_extender'
    name = Column(String(256), nullable=False)
    aperture_mm = Column(Float, nullable=True)
    focal_length_mm = Column(Float, nullable=True)
    sensor_width_mm = Column(Float, nullable=True)
    sensor_height_mm = Column(Float, nullable=True)
    pixel_size_um = Column(Float, nullable=True)
    factor = Column(Float, nullable=True)
    is_shared = Column(Boolean, nullable=False, default=False, index=True)
    original_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True)
    original_item_id = Column(Integer, nullable=True, index=True)
    user = relationship("DbUser", foreign_keys=[user_id], back_populates="components")
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

    # --- NEW: Rig Snapshot Fields ---
    rig_id_snapshot = Column(Integer, ForeignKey('rigs.id', ondelete="SET NULL"), nullable=True) # <-- ADDED THIS
    rig_name_snapshot = Column(String(256), nullable=True)
    rig_efl_snapshot = Column(Float, nullable=True)
    rig_fr_snapshot = Column(Float, nullable=True)
    rig_scale_snapshot = Column(Float, nullable=True)
    rig_fov_w_snapshot = Column(Float, nullable=True)
    rig_fov_h_snapshot = Column(Float, nullable=True)
    telescope_name_snapshot = Column(String(256), nullable=True)
    reducer_name_snapshot = Column(String(256), nullable=True)
    camera_name_snapshot = Column(String(256), nullable=True)

    calculated_integration_time_minutes = Column(Float, nullable=True)
    external_id = Column(String(64), nullable=True, index=True)

    user = relationship("DbUser", back_populates="sessions")
    project = relationship("Project", back_populates="sessions")
    rig_snapshot = relationship("Rig", foreign_keys=[rig_id_snapshot]) # <-- ADDED THIS

class UiPref(Base):
    __tablename__ = 'ui_prefs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    json_blob = Column(Text, nullable=True)
    user = relationship("DbUser", back_populates="ui_prefs")


def _seed_user_from_guest_data(db_session, user_to_seed: 'DbUser'):
    """
    Copies all template data from the 'guest_user' account to the 'user_to_seed'.
    This function is now granular and non-destructive:
    - If no locations exist, it seeds all locations and UI prefs.
    - It iterates through default objects/components/rigs and only adds them
      if an item with the same name doesn't already exist.
    This safely fixes all partially provisioned users.
    """
    # 1. Get the template 'guest_user'
    guest_user = db_session.query(DbUser).filter_by(username="guest_user").one_or_none()
    if not guest_user:
        print(
            f"   -> [SEEDING] WARNING: 'guest_user' template not found. Cannot seed data for '{user_to_seed.username}'.")
        return

    new_user_id = user_to_seed.id
    guest_user_id = guest_user.id
    print(
        f"   -> [SEEDING] Found template user 'guest_user' (ID: {guest_user_id}). Checking data for '{user_to_seed.username}' (ID: {new_user_id}).")

    # 2. --- LOCATIONS & UI PREFS ---
    # This is still all-or-nothing. If a user has 0 locations, they get them all.
    # If they have 1 or more, we assume they've configured it and we don't touch it.
    existing_loc_count = db_session.query(Location).filter_by(user_id=new_user_id).count()
    # 3. Copy UiPref (only if no locations, as it's tied to location prefs)
    if existing_loc_count == 0:
        print("      -> User has 0 locations. Seeding UiPref...")

        # --- START FIX: Check if prefs already exist before adding ---
        existing_prefs = db_session.query(UiPref).filter_by(user_id=new_user_id).first()
        if not existing_prefs:
            guest_prefs = db_session.query(UiPref).filter_by(user_id=guest_user_id).first()  # Use .first()
            if guest_prefs:
                new_prefs = UiPref(user_id=new_user_id, json_blob=guest_prefs.json_blob)
                db_session.add(new_prefs)
                print("      -> Copied UiPref.")
        else:
            print("      -> User already has UiPref. Skipping.")

        # Copy Locations & HorizonPoints
        guest_locations = db_session.query(Location).options(selectinload(Location.horizon_points)).filter_by(
            user_id=guest_user_id).all()
        for g_loc in guest_locations:
            new_loc = Location(
                user_id=new_user_id, name=g_loc.name, lat=g_loc.lat, lon=g_loc.lon,
                timezone=g_loc.timezone, altitude_threshold=g_loc.altitude_threshold,
                is_default=g_loc.is_default, active=g_loc.active, comments=g_loc.comments
            )
            db_session.add(new_loc)
            db_session.flush()
            for g_hp in g_loc.horizon_points:
                new_hp = HorizonPoint(location_id=new_loc.id, az_deg=g_hp.az_deg, alt_min_deg=g_hp.alt_min_deg)
                db_session.add(new_hp)
        print(f"      -> Copied {len(guest_locations)} locations.")
    else:
        print(f"      -> User already has {existing_loc_count} locations. Skipping location/UI pref seeding.")

    # 3. --- ASTRO OBJECTS (Item-by-Item) ---
    print("      -> Checking for missing AstroObjects...")
    # Get all names of objects the user *already has*
    user_object_names = {
        name[0] for name in db_session.query(AstroObject.object_name).filter_by(user_id=new_user_id)
    }
    guest_objects = db_session.query(AstroObject).filter_by(user_id=guest_user_id).all()

    objects_added = 0
    for g_obj in guest_objects:
        # If user does NOT have an object with this name, add it
        if g_obj.object_name not in user_object_names:
            new_obj = AstroObject(
                user_id=new_user_id, object_name=g_obj.object_name, common_name=g_obj.common_name,
                ra_hours=g_obj.ra_hours, dec_deg=g_obj.dec_deg, type=g_obj.type,
                constellation=g_obj.constellation, magnitude=g_obj.magnitude, size=g_obj.size,
                sb=g_obj.sb, active_project=g_obj.active_project, project_name=g_obj.project_name,
                is_shared=False, shared_notes=None, original_user_id=None, original_item_id=None
            )
            db_session.add(new_obj)
            objects_added += 1
    print(f"      -> Copied {objects_added} new astro objects (skipped {len(guest_objects) - objects_added} existing).")

    # 4. --- COMPONENTS (Item-by-Item) & RIG ID MAPPING ---
    print("      -> Checking for missing Components...")
    # This part is tricky. We must create a map of [guest_id] -> [user_id]
    # to build rigs correctly.

    # Get all components the user *already has*, mapped by (kind, name) -> id
    user_components = db_session.query(Component).filter_by(user_id=new_user_id).all()
    user_component_map = {(c.kind, c.name): c.id for c in user_components}

    guest_components = db_session.query(Component).filter_by(user_id=guest_user_id).all()
    final_component_id_map = {}  # This will map {guest_comp_id -> user_comp_id}
    components_added = 0

    for g_comp in guest_components:
        # Check if user already has a component with this (kind, name)
        existing_user_comp_id = user_component_map.get((g_comp.kind, g_comp.name))

        if existing_user_comp_id:
            # User already has it. Just add it to our ID map for rig building.
            final_component_id_map[g_comp.id] = existing_user_comp_id
        else:
            # User doesn't have it. Create it.
            new_comp = Component(
                user_id=new_user_id, kind=g_comp.kind, name=g_comp.name,
                aperture_mm=g_comp.aperture_mm, focal_length_mm=g_comp.focal_length_mm,
                sensor_width_mm=g_comp.sensor_width_mm, sensor_height_mm=g_comp.sensor_height_mm,
                pixel_size_um=g_comp.pixel_size_um, factor=g_comp.factor,
                is_shared=False, original_user_id=None, original_item_id=None
            )
            db_session.add(new_comp)
            db_session.flush()  # IMPORTANT: We need the new_comp.id immediately

            # Add the new component's ID to our map
            final_component_id_map[g_comp.id] = new_comp.id
            components_added += 1
    print(
        f"      -> Copied {components_added} new components (skipped {len(guest_components) - components_added} existing).")

    # 5. --- RIGS (Item-by-Item) ---
    print("      -> Checking for missing Rigs...")
    # Get all names of rigs the user *already has*
    user_rig_names = {
        name[0] for name in db_session.query(Rig.rig_name).filter_by(user_id=new_user_id)
    }
    guest_rigs = db_session.query(Rig).filter_by(user_id=guest_user_id).all()

    rigs_added = 0
    for g_rig in guest_rigs:
        # If user does NOT have a rig with this name, add it
        if g_rig.rig_name not in user_rig_names:
            # Use our 'final_component_id_map' to correctly link the new rig
            # to the user's new (or existing) components
            new_rig = Rig(
                user_id=new_user_id, rig_name=g_rig.rig_name,
                telescope_id=final_component_id_map.get(g_rig.telescope_id),
                camera_id=final_component_id_map.get(g_rig.camera_id),
                reducer_extender_id=final_component_id_map.get(g_rig.reducer_extender_id),
                effective_focal_length=g_rig.effective_focal_length, f_ratio=g_rig.f_ratio,
                image_scale=g_rig.image_scale, fov_w_arcmin=g_rig.fov_w_arcmin
            )
            db_session.add(new_rig)
            rigs_added += 1
    print(f"      -> Copied {rigs_added} new rigs (skipped {len(guest_rigs) - rigs_added} existing).")

    print(f"   -> [SEEDING] Granular seeding complete for '{user_to_seed.username}'.")


def get_or_create_db_user(db_session, username: str) -> 'DbUser':
    """
    Finds a user in the `DbUser` table by username or creates them if they don't exist.
    If created, transactionally seeds them with data from the 'guest_user' template.
    This function is defined AFTER the DB models, so it can safely query them.

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

    # --- New User Provisioning Path ---
    try:
        # User exists in Auth DB but not here. Create them now.
        print(f"[PROVISIONING] User '{username}' not found in app.db. Creating new record.")
        new_user = DbUser(username=username)
        db_session.add(new_user)
        db_session.flush()  # Flush to get the new_user.id before seeding

        print(f"   -> User record created with ID {new_user.id}. Now seeding data...")

        # Call the helper function to ADD all default data to the session
        _seed_user_from_guest_data(db_session, new_user)

        # Commit the ENTIRE transaction (new user + all default data)
        db_session.commit()

        print(f"   -> Successfully provisioned and seeded '{username}'.")
        # We need to re-fetch to get the fully loaded object
        return db_session.query(DbUser).filter_by(username=username).one()

    except Exception as e:
        db_session.rollback()  # Roll back the entire transaction on any failure
        print(f"   -> FAILED to provision '{username}'. Rolled back. Error: {e}")
        traceback.print_exc()
        return None

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


def ensure_db_initialized_unified():
    """
    Create tables if missing, ensure schema patches (external_id column),
    and set SQLite pragmas before any queries or migrations run.
    """
    lock_path = os.path.join(INSTANCE_PATH, "schema_patch.lock")
    with _FileLock(lock_path):
        Base.metadata.create_all(bind=engine, checkfirst=True)
        with engine.begin() as conn:
            # --- Get table info for journal_sessions ---
            cols_journal = conn.exec_driver_sql("PRAGMA table_info(journal_sessions);").fetchall()
            colnames_journal = {row[1] for row in cols_journal}  # (cid, name, type, notnull, dflt_value, pk)
            if "external_id" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN external_id TEXT;")
                print("[DB PATCH] Added missing column journal_sessions.external_id")

            # --- ADD THIS BLOCK for Rig Snapshot ---
            if "rig_id_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN rig_id_snapshot INTEGER;")
                print("[DB PATCH] Added missing column journal_sessions.rig_id_snapshot")
            if "rig_name_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN rig_name_snapshot VARCHAR(256);")
                print("[DB PATCH] Added missing column journal_sessions.rig_name_snapshot")
            if "rig_efl_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN rig_efl_snapshot FLOAT;")
                print("[DB PATCH] Added missing column journal_sessions.rig_efl_snapshot")
            if "rig_fr_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN rig_fr_snapshot FLOAT;")
                print("[DB PATCH] Added missing column journal_sessions.rig_fr_snapshot")
            if "rig_scale_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN rig_scale_snapshot FLOAT;")
                print("[DB PATCH] Added missing column journal_sessions.rig_scale_snapshot")
            if "rig_fov_w_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN rig_fov_w_snapshot FLOAT;")
                print("[DB PATCH] Added missing column journal_sessions.rig_fov_w_snapshot")
            if "rig_fov_h_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN rig_fov_h_snapshot FLOAT;")
                print("[DB PATCH] Added missing column journal_sessions.rig_fov_h_snapshot")
            if "telescope_name_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN telescope_name_snapshot VARCHAR(256);")
                print("[DB PATCH] Added missing column journal_sessions.telescope_name_snapshot")
            if "reducer_name_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN reducer_name_snapshot VARCHAR(256);")
                print("[DB PATCH] Added missing column journal_sessions.reducer_name_snapshot")
            if "camera_name_snapshot" not in colnames_journal:
                conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN camera_name_snapshot VARCHAR(256);")
                print("[DB PATCH] Added missing column journal_sessions.camera_name_snapshot")

            try:
                conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_external_id ON journal_sessions(external_id) WHERE external_id IS NOT NULL;"
                )
            except Exception:
                pass

                # --- SavedView Patches ---
                cols_views = conn.exec_driver_sql("PRAGMA table_info(saved_views);").fetchall()
                colnames_views = {row[1] for row in cols_views}
                if "description" not in colnames_views:
                    conn.exec_driver_sql("ALTER TABLE saved_views ADD COLUMN description VARCHAR(500);")
                if "is_shared" not in colnames_views:
                    conn.exec_driver_sql("ALTER TABLE saved_views ADD COLUMN is_shared BOOLEAN DEFAULT 0 NOT NULL;")
                if "original_user_id" not in colnames_views:
                    conn.exec_driver_sql("ALTER TABLE saved_views ADD COLUMN original_user_id INTEGER;")
                if "original_item_id" not in colnames_views:
                    conn.exec_driver_sql("ALTER TABLE saved_views ADD COLUMN original_item_id INTEGER;")

            try:
                cols_framing = conn.exec_driver_sql("PRAGMA table_info(saved_framings);").fetchall()
                colnames_framing = {row[1] for row in cols_framing}
                if "rig_name" not in colnames_framing:
                    conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN rig_name VARCHAR(256);")
                    print("[DB PATCH] Added missing column saved_framings.rig_name")
                if "mosaic_cols" not in colnames_framing:
                    conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN mosaic_cols INTEGER DEFAULT 1;")
                if "mosaic_rows" not in colnames_framing:
                    conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN mosaic_rows INTEGER DEFAULT 1;")
                if "mosaic_overlap" not in colnames_framing:
                    conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN mosaic_overlap FLOAT DEFAULT 10.0;")
            except Exception as e:
                # Table might not exist yet if it's a fresh install, which is fine
                pass

            # --- Add new columns to 'components' table ---
            cols_components = conn.exec_driver_sql("PRAGMA table_info(components);").fetchall()
            colnames_components = {row[1] for row in cols_components}

            if "is_shared" not in colnames_components:
                conn.exec_driver_sql("ALTER TABLE components ADD COLUMN is_shared BOOLEAN DEFAULT 0 NOT NULL;")
                print("[DB PATCH] Added missing column components.is_shared")

            if "original_user_id" not in colnames_components:
                conn.exec_driver_sql("ALTER TABLE components ADD COLUMN original_user_id INTEGER;")
                print("[DB PATCH] Added missing column components.original_user_id")

            # --- ADD THIS BLOCK ---
            if "original_item_id" not in colnames_components:
                conn.exec_driver_sql("ALTER TABLE components ADD COLUMN original_item_id INTEGER;")
                print("[DB PATCH] Added missing column components.original_item_id")
            # --- END OF BLOCK ---

            # --- Add new columns to 'astro_objects' table ---
            cols_objects = conn.exec_driver_sql("PRAGMA table_info(astro_objects);").fetchall()
            colnames_objects = {row[1] for row in cols_objects}

            project_name_col_info = next((col for col in cols_objects if col[1] == 'project_name'), None)
            if project_name_col_info and 'TEXT' not in project_name_col_info[2].upper():
                print(
                    f"[DB PATCH] Note: astro_objects.project_name type is {project_name_col_info[2]}. Model updated to TEXT.")

            if "is_shared" not in colnames_objects:
                conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN is_shared BOOLEAN DEFAULT 0 NOT NULL;")
                print("[DB PATCH] Added missing column astro_objects.is_shared")

            if "shared_notes" not in colnames_objects:
                conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN shared_notes TEXT;")
                print("[DB PATCH] Added missing column astro_objects.shared_notes")

            if "original_user_id" not in colnames_objects:
                conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN original_user_id INTEGER;")
                print("[DB PATCH] Added missing column astro_objects.original_user_id")

            # --- ADD THIS BLOCK ---
            if "original_item_id" not in colnames_objects:
                conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN original_item_id INTEGER;")
                print("[DB PATCH] Added missing column astro_objects.original_item_id")
            # --- END OF BLOCK ---

            if "catalog_sources" not in colnames_objects:
                conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN catalog_sources TEXT;")
                print("[DB PATCH] Added missing column astro_objects.catalog_sources")

            if "catalog_info" not in colnames_objects:
                conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN catalog_info TEXT;")
                print("[DB PATCH] Added missing column astro_objects.catalog_info")

            # --- Project Model Patches ---
            cols_projects = conn.exec_driver_sql("PRAGMA table_info(projects);").fetchall()
            colnames_projects = {row[1] for row in cols_projects}

            if "target_object_name" not in colnames_projects:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN target_object_name VARCHAR(256);")
                print("[DB PATCH] Added missing column projects.target_object_name")

            if "description_notes" not in colnames_projects:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN description_notes TEXT;")
                print("[DB PATCH] Added missing column projects.description_notes")

            if "framing_notes" not in colnames_projects:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN framing_notes TEXT;")
                print("[DB PATCH] Added missing column projects.framing_notes")

            if "processing_notes" not in colnames_projects:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN processing_notes TEXT;")
                print("[DB PATCH] Added missing column projects.processing_notes")

            if "final_image_file" not in colnames_projects:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN final_image_file VARCHAR(256);")
                print("[DB PATCH] Added missing column projects.final_image_file")

            if "goals" not in colnames_projects:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN goals TEXT;")
                print("[DB PATCH] Added missing column projects.goals")

            if "status" not in colnames_projects:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN status VARCHAR(32) DEFAULT 'In Progress';")
                print("[DB PATCH] Added missing column projects.status")
            # --- End Project Model Patches ---

            # Pragmas
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
        r = requests.get(manifest_url, timeout=10)
        r.raise_for_status() # Raise error for bad status (404, 500)
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


def load_catalog_pack(pack_id: str) -> tuple[dict | None, dict | None]:
    """Load a specific catalog pack from the central web repository."""

    # 1. Get the manifest (this will be cached)
    all_packs_meta = discover_catalog_packs()

    # 2. Find the metadata for the requested pack
    meta = next((p for p in all_packs_meta if p.get("id") == pack_id), None)

    if not meta:
        print(f"[CATALOG LOAD] Pack ID '{pack_id}' not found in manifest.")
        return (None, None)

    filename = meta.get("filename")
    if not filename:
        print(f"[CATALOG LOAD] Pack ID '{pack_id}' has no filename in manifest.")
        return (None, None)

    # --- THIS IS THE NEW LOGIC ---
    # Get the URL from the (possibly empty) config
    url_to_use = NOVA_CATALOG_URL
    if not url_to_use:
        # If it's not in the config, use the hardcoded default
        url_to_use = "https://catalogs.nova-tracker.com"
    # --- END NEW LOGIC ---

    # 3. Check if a URL is available (from either source)
    if not url_to_use:
        print("[CATALOG LOAD] No Catalog URL is configured. Cannot download pack.")
        return (None, None)

    pack_url = f"{url_to_use.rstrip('/')}/{filename}"

    try:
        # 4. Fetch the YAML file
        print(f"[CATALOG LOAD] Downloading pack from {pack_url}")
        r = requests.get(pack_url, timeout=15)
        r.raise_for_status()

        # 5. Parse the YAML content from the response
        pack_data = yaml.safe_load(r.text) or {}

        return (pack_data, meta)

    except requests.exceptions.RequestException as e:
        print(f"[CATALOG LOAD] Failed to download pack '{filename}': {e}")
    except yaml.YAMLError as e:
        print(f"[CATALOG LOAD] Failed to parse YAML for '{filename}': {e}")
    except Exception as e:
        print(f"[CATALOG LOAD] An unexpected error occurred: {e}")

    return (None, None)

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

                # --- START FIX: Replace horizon points using relationship cascade ---
                new_horizon_points = []
                hm = loc.get("horizon_mask")
                if isinstance(hm, list):
                    for pair in hm:
                        try:
                            az, altmin = float(pair[0]), float(pair[1])
                            # Create the object, but don't add it to the session.
                            # Appending to the list handles the relationship.
                            new_horizon_points.append(
                                HorizonPoint(az_deg=az, alt_min_deg=altmin)
                            )
                        except Exception:
                            pass

                # Assigning the new list triggers the 'delete-orphan' cascade.
                # All old points are deleted, all new points are added.
                existing.horizon_points = new_horizon_points
                # --- END FIX ---

            else:
                # --- INSERT new row
                row = Location(
                    user_id=user.id,
                    name=name,
                    lat=lat,
                    lon=lon,
                    timezone=tz,
                    altitude_threshold=alt_thr,
                    is_default=new_is_default,
                    active=loc.get("active", True)
                )
                db.add(row);
                db.flush()  # Flush to get the row.id

                # --- START REFACTOR: Use the same pattern for consistency ---
                new_horizon_points = []
                hm = loc.get("horizon_mask")
                if isinstance(hm, list):
                    for pair in hm:
                        try:
                            az, altmin = float(pair[0]), float(pair[1])
                            new_horizon_points.append(
                                HorizonPoint(az_deg=az, alt_min_deg=altmin)
                            )
                        except Exception:
                            pass

                # Assign the new list to the new row object
                row.horizon_points = new_horizon_points
                # --- END REFACTOR ---
        except Exception as e:
            print(f"[MIGRATION] Skip/repair location '{name}': {e}")


def _heal_saved_framings(db, user: DbUser):
    """
    Scans for SavedFraming records that have a rig_name but no rig_id
    (orphaned because config was imported before rigs) and tries to link them.
    """
    try:
        orphans = db.query(SavedFraming).filter(
            SavedFraming.user_id == user.id,
            SavedFraming.rig_name != None,
            SavedFraming.rig_id == None
        ).all()

        count = 0
        for f in orphans:
            # Try to find the rig by name
            rig = db.query(Rig).filter_by(user_id=user.id, rig_name=f.rig_name).one_or_none()
            if rig:
                f.rig_id = rig.id
                count += 1

        if count > 0:
            print(f"[MIGRATION] Healed {count} saved framing links (connected to newly imported rigs).")
            db.flush()
    except Exception as e:
        print(f"[MIGRATION] Error healing saved framings: {e}")


def _migrate_saved_framings(db, user: DbUser, config: dict):
    framings = config.get("saved_framings", []) or []

    for f in framings:
        try:
            obj_name = f.get("object_name")
            if not obj_name: continue

            # Resolve rig_id from rig_name if possible
            rig_name_str = f.get("rig_name")
            rig_id = None
            if rig_name_str:
                rig = db.query(Rig).filter_by(user_id=user.id, rig_name=rig_name_str).one_or_none()
                if rig:
                    rig_id = rig.id

            # Upsert Logic
            existing = db.query(SavedFraming).filter_by(
                user_id=user.id,
                object_name=obj_name
            ).one_or_none()

            if existing:
                existing.rig_id = rig_id
                existing.rig_name = rig_name_str  # <-- Always save the name
                existing.ra = f.get("ra")
                existing.dec = f.get("dec")
                existing.rotation = f.get("rotation")
                existing.survey = f.get("survey")
                existing.blend_survey = f.get("blend_survey")
                existing.blend_opacity = f.get("blend_opacity")
            else:
                new_sf = SavedFraming(
                    user_id=user.id,
                    object_name=obj_name,
                    rig_id=rig_id,
                    rig_name=rig_name_str,  # <-- Always save the name
                    ra=f.get("ra"),
                    dec=f.get("dec"),
                    rotation=f.get("rotation"),
                    survey=f.get("survey"),
                    blend_survey=f.get("blend_survey"),
                    blend_opacity=f.get("blend_opacity")
                )
                db.add(new_sf)

        except Exception as e:
            print(f"[MIGRATION] Error migrating saved framing for {f.get('object_name')}: {e}")

    db.flush()


def _migrate_objects(db, user: DbUser, config: dict):
    """
    Idempotently migrates astronomical objects from a YAML configuration dictionary to the database.

    This function performs an "upsert" (update or insert) for each object based on its
    unique name for a given user. It prevents duplicates, handles various legacy key names,
    and automatically calculates the constellation if it's missing but coordinates are present.

    *** V2: Automatically rewrites '/uploads/...' image links in notes to point to
    *** the importing user's directory.
    """

    # === START: Link Rewriting Logic ===
    # Get the target username (e.g., 'default' or 'mrantonSG')
    target_username = user.username
    # This regex finds '/uploads/', captures the (old) username, and the rest of the path
    link_pattern = re.compile(r'(/uploads/)([^/]+)(/.*?["\'])')
    # This builds the replacement string, e.g., '/uploads/default/image.jpg"'
    replacement_str = r'\1' + re.escape(target_username) + r'\3'
    # === END: Link Rewriting Logic ===

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
            object_name = normalize_object_name(raw_obj_name)

            common_name = o.get("Common Name") or o.get("Name") or o.get("common_name")
            # If common_name is still blank, use the raw (pretty) object name as a fallback
            if not common_name or not str(common_name).strip():
                common_name = str(raw_obj_name).strip()

            obj_type = o.get("Type") or o.get("type")
            constellation = o.get("Constellation") or o.get("constellation")
            magnitude = o.get("Magnitude") if o.get("Magnitude") is not None else o.get("magnitude")
            size = o.get("Size") if o.get("Size") is not None else o.get("size")
            sb = o.get("SB") if o.get("SB") is not None else o.get("sb")
            active_project = bool(o.get("ActiveProject") or o.get("active_project") or False)

            # === START: Link Rewriting Application ===
            project_name = o.get("Project") or o.get("project_name")
            shared_notes = o.get("shared_notes")

            # Rewrite image links to point to the *importer's* directory
            if project_name:
                project_name = link_pattern.sub(replacement_str, project_name)
            if shared_notes:
                shared_notes = link_pattern.sub(replacement_str, shared_notes)
            # === END: Link Rewriting Application ===

            is_shared = bool(o.get("is_shared", False))
            original_user_id = _as_int(o.get("original_user_id"))
            original_item_id = _as_int(o.get("original_item_id"))
            catalog_sources = o.get("catalog_sources")
            catalog_info = o.get("catalog_info")

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
                    constellation = None  # Avoid crashing if coordinates are invalid.

            # --- 3. Perform the Idempotent "Upsert" ---
            # Query for an existing object with the normalized name.
            existing = db.query(AstroObject).filter_by(
                user_id=user.id,
                object_name=object_name
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

                # --- START NEW ROBUST MERGE LOGIC ---
                existing_notes = existing.project_name or ""
                new_notes = project_name or ""  # Use the *fixed* project_name

                # Define what counts as "empty"
                is_existing_empty = not existing_notes or existing_notes.lower().strip() in ('none', '<div>none</div>',
                                                                                             'null')
                is_new_empty = not new_notes or new_notes.lower().strip() in ('none', '<div>none</div>', 'null')

                if is_new_empty:
                    # New notes are empty, so do nothing. Keep the existing notes.
                    pass
                elif is_existing_empty:
                    # Existing notes are empty, so just replace them with the new notes.
                    existing.project_name = new_notes
                elif new_notes not in existing_notes:
                    # Both have notes, and they are different. Append them.
                    existing.project_name = existing_notes + f"<br>---<br><em>(Merged)</em><br>{new_notes}"
                # --- END NEW ROBUST MERGE LOGIC ---

                existing.is_shared = is_shared
                existing.shared_notes = shared_notes  # Use the *fixed* shared_notes
                existing.original_user_id = original_user_id
                existing.original_item_id = original_item_id
                existing.catalog_sources = catalog_sources
                existing.catalog_info = catalog_info
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
                    project_name=project_name,  # Use the *fixed* project_name
                    is_shared=is_shared,
                    shared_notes=shared_notes,  # Use the *fixed* shared_notes
                    original_user_id=original_user_id,
                    original_item_id=original_item_id,
                    catalog_sources=catalog_sources,
                    catalog_info=catalog_info,
                )
                db.add(new_object)
                db.flush()

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

        # --- NEW: Get sharing fields from the 'fields' dict ---
        is_shared = bool(fields.get("is_shared", False))
        original_user_id = _as_int(fields.get("original_user_id"))
        original_item_id = _as_int(fields.get("original_item_id"))

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

            # We only set them if they're not already set, to avoid overwriting original import data
            if existing_row.is_shared is False:
                existing_row.is_shared = is_shared
            if existing_row.original_user_id is None:
                existing_row.original_user_id = original_user_id
            if existing_row.original_item_id is None:
                existing_row.original_item_id = original_item_id
            # --- END OF BLOCK ---

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
            is_shared=is_shared,
            original_user_id=original_user_id,
            original_item_id=original_item_id
        )
        db.add(new_row)
        db.flush()
        return new_row

    # Use a string-keyed dictionary for the legacy IDs.
    legacy_id_to_component_id: dict[tuple[str, str], int] = {}
    name_to_component_id: dict[tuple[str, str | None], int] = {}

    def _remember_component(row: Component | None, kind: str, name: str, legacy_id):
        if row is None or legacy_id is None: return
        legacy_id_to_component_id[(kind, str(legacy_id))] = row.id
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
                                       focal_length_mm=_get_alias(t, "focal_length_mm"),
                                       is_shared=t.get("is_shared"), original_user_id=t.get("original_user_id"),
                                       original_item_id=t.get("original_item_id")
                                       )
        _remember_component(row, "telescope", _get_alias(t, "name"), t.get("id"))
    for c in comps.get("cameras", []):
        row = _get_or_create_component("camera", _get_alias(c, "name"),
                                       sensor_width_mm=_get_alias(c, "sensor_width_mm"),
                                       sensor_height_mm=_get_alias(c, "sensor_height_mm"),
                                       pixel_size_um=_get_alias(c, "pixel_size_um"),
                                       is_shared=c.get("is_shared"), original_user_id=c.get("original_user_id"),
                                       original_item_id=c.get("original_item_id")
                                       )
        _remember_component(row, "camera", _get_alias(c, "name"), c.get("id"))
    for r in comps.get("reducers_extenders", []):
        row = _get_or_create_component("reducer_extender", _get_alias(r, "name"), factor=_get_alias(r, "factor"),
                                       is_shared=r.get("is_shared"), original_user_id=r.get("original_user_id"),
                                       original_item_id=r.get("original_item_id")
                                       )
        _remember_component(row, "reducer_extender", _get_alias(r, "name"), r.get("id"))

    def _resolve_component_id(kind: str, legacy_id, name) -> int | None:
        if legacy_id is not None:
            legacy_id_str = str(legacy_id)
            # --- START FIX: Look up the namespaced (kind, id) key ---
            if (kind, legacy_id_str) in legacy_id_to_component_id:
                return legacy_id_to_component_id[(kind, legacy_id_str)]
            # --- END FIX ---
            # (The old lookup for just legacy_id_str is removed)

        # This part for name-based lookup is still correct and needed
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

    _heal_saved_framings(db, user)


def _migrate_saved_views(db, user: DbUser, config: dict):
    """
    Idempotent import of saved views. Deletes all existing views and replaces them.
    Now includes description and sharing status.
    """
    # 1. Delete all existing views for this user
    db.query(SavedView).filter_by(user_id=user.id).delete()
    db.flush()

    # 2. Add new views from the config
    views_list = (config or {}).get("saved_views", []) or []
    if not isinstance(views_list, list):
        print("[MIGRATION] 'saved_views' is not a list, skipping.")
        return

    for view_entry in views_list:
        try:
            name = view_entry.get("name")
            settings = view_entry.get("settings")

            # --- New Fields ---
            description = view_entry.get("description")
            is_shared = bool(view_entry.get("is_shared", False))

            if not name or not settings:
                print(f"[MIGRATION] Skipping invalid saved view (missing name or settings): {view_entry}")
                continue

            # Ensure settings are stored as a JSON string
            settings_str = json.dumps(settings)

            new_view = SavedView(
                user_id=user.id,
                name=name,
                description=description,  # <-- Added
                is_shared=is_shared,  # <-- Added
                settings_json=settings_str
            )
            db.add(new_view)
        except Exception as e:
            print(f"[MIGRATION] Could not process saved view '{view_entry.get('name')}'. Error: {e}")

    db.flush()


def _migrate_journal(db, user: DbUser, journal_yaml: dict):
    data = journal_yaml or {}
    # Normalize old list-based journals to the new dict structure
    if isinstance(data, list):
        data = {"projects": [], "sessions": data}
    else:
        # Ensure 'projects' and 'sessions' keys exist, handle legacy 'entries' key
        data.setdefault("projects", [])
        data.setdefault("sessions", data.get("entries", [])) # Use 'entries' as fallback

    # === START: Link Rewriting Logic ===
    # Get the target username (e.g., 'default' or 'mrantonSG')
    target_username = user.username
    # This regex finds '/uploads/', captures the (old) username, and the rest of the path
    link_pattern = re.compile(r'(/uploads/)([^/]+)(/.*?["\'])')
    # This builds the replacement string, e.g., '/uploads/default/image.jpg"'
    replacement_str = r'\1' + re.escape(target_username) + r'\3'
    # === END: Link Rewriting Logic ===

    # --- 1. Migrate Projects & Track Valid IDs ---
    valid_project_ids = set()

    for p in (data.get("projects") or []):
        # Check if both project_id and project_name are present and non-empty
        project_id_val = p.get("project_id")
        project_name_val = p.get("project_name")

        if project_id_val and str(project_id_val).strip():
            valid_project_ids.add(str(project_id_val)) # Track valid IDs from the import file

            # Check if project already exists by ID
            existing_project = db.query(Project).filter_by(id=str(project_id_val)).one_or_none()

            # --- NEW: Fields to set/update (Safely defaults to None if key missing) ---
            project_data = {
                "user_id": user.id,
                "name": str(project_name_val).strip() if project_name_val else "Unnamed Project",
                "target_object_name": p.get("target_object_id"),
                "description_notes": p.get("description_notes"),
                "framing_notes": p.get("framing_notes"),
                "processing_notes": p.get("processing_notes"),
                "final_image_file": p.get("final_image_file"),
                "goals": p.get("goals"),
                "status": p.get("status", "In Progress"),
            }

            if existing_project:
                # Update existing project
                for key, value in project_data.items():
                    if value is not None:
                        setattr(existing_project, key, value)
            else:
                # Check if a project with the same name already exists for the user (to avoid name duplicates if ID differs)
                existing_by_name = db.query(Project).filter_by(user_id=user.id, name=project_data["name"]).one_or_none()
                if not existing_by_name:
                    new_project = Project(id=str(project_id_val), **project_data)
                    db.add(new_project)

    db.flush()  # Flush after adding all valid projects from the YAML

    # --- 2. Migrate Sessions with ALL fields ---
    for s in (data.get("sessions") or []):
        # Get external ID, preferring 'session_id' then 'id'
        ext_id = s.get("session_id") or s.get("id")
        # Get date, preferring 'session_date' then 'date'
        date_str = s.get("session_date") or s.get("date")
        if not date_str: continue # Skip if no date

        # Try parsing date (ISO or YYYY-MM-DD)
        try:
            dt = datetime.fromisoformat(date_str).date()
        except:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                print(f"[MIGRATION][SESSION SKIP] Invalid date format '{date_str}' for session with external_id '{ext_id}'. Skipping.")
                continue # Skip if date parsing fails

        # === START: Link Rewriting Application ===
        # Get the raw HTML notes from the YAML
        notes_html = s.get("general_notes_problems_learnings") or s.get("notes")

        # Rewrite image links to point to the *importer's* directory
        if notes_html:
            notes_html = link_pattern.sub(replacement_str, notes_html)
        # === END: Link Rewriting Application ===

        # === START: Orphan Project Check ===
        sess_project_id = s.get("project_id")
        if sess_project_id:
            sess_project_id = str(sess_project_id)
            # If this ID wasn't in the YAML projects block...
            if sess_project_id not in valid_project_ids:
                # ...check if it exists in the DB (maybe from a previous import)
                exists_in_db = db.query(Project).filter_by(id=sess_project_id).first()

                if not exists_in_db:
                    # ORPHAN DETECTED: Auto-create a placeholder project to satisfy Foreign Key
                    print(f"[MIGRATION] Auto-creating missing project {sess_project_id} for session.")
                    placeholder_project = Project(
                        id=sess_project_id,
                        user_id=user.id,
                        name=s.get("project_name") or f"Legacy Project {sess_project_id[:8]}",
                        status="Completed" # Assume legacy projects are done
                    )
                    db.add(placeholder_project)
                    db.flush() # Commit immediately so the session insert works
                    valid_project_ids.add(sess_project_id)
        # === END: Orphan Project Check ===

        # Map all YAML keys to DB columns
        row_values = {
            "user_id": user.id,
            "project_id": sess_project_id, # Use the stringified/checked ID
            "date_utc": dt,
            "object_name": normalize_object_name(s.get("target_object_id") or s.get("object_name")),
            "notes": notes_html,  # <-- USE THE FIXED HTML
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
            "rig_id_snapshot": _as_int(s.get("rig_id_snapshot")),
            "rig_name_snapshot": s.get("rig_name_snapshot"),
            "rig_efl_snapshot": _try_float(s.get("rig_efl_snapshot")),
            "rig_fr_snapshot": _try_float(s.get("rig_fr_snapshot")),
            "rig_scale_snapshot": _try_float(s.get("rig_scale_snapshot")),
            "rig_fov_w_snapshot": _try_float(s.get("rig_fov_w_snapshot")),
            "rig_fov_h_snapshot": _try_float(s.get("rig_fov_h_snapshot")),
            "telescope_name_snapshot": s.get("telescope_name_snapshot"),
            "reducer_name_snapshot": s.get("reducer_name_snapshot"),
            "camera_name_snapshot": s.get("camera_name_snapshot"),
            "calculated_integration_time_minutes": _try_float(s.get("calculated_integration_time_minutes")),
            # Ensure external_id is stored as string if it exists
            "external_id": str(ext_id) if ext_id else None
        }
        # *** START: Simplified Upsert Logic ***
        if ext_id:
            # Try to find an existing session with this external_id for this user
            existing_session = db.query(JournalSession).filter_by(
                user_id=user.id,
                external_id=str(ext_id)
            ).one_or_none()

            if existing_session:
                # UPDATE: Session found, update its fields
                for k, v in row_values.items():
                    # Only update if the new value is not None
                    if v is not None:
                        setattr(existing_session, k, v)
                # No need to db.add() here
            else:
                # INSERT: Session not found, create a new one
                new_session = JournalSession(**row_values)
                db.add(new_session)
        else:
            # INSERT (No external ID provided): Always create a new session
            new_session = JournalSession(**row_values)
            db.add(new_session)
        # *** END: Simplified Upsert Logic ***

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

        # --- 2. Load Locations ---
        loc_rows = db.query(Location).filter_by(user_id=u.id).all()
        locations = {}
        for l in loc_rows:
            mask = [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
            locations[l.name] = {
                "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                "altitude_threshold": l.altitude_threshold, "horizon_mask": mask,
                "active": l.active,
                "is_default": l.is_default
            }
        user_config["locations"] = locations

        # --- 3. Load Objects ---
        obj_rows = db.query(AstroObject).filter_by(user_id=u.id).all()
        objects = []
        for o in obj_rows:
            objects.append(o.to_dict())
        user_config["objects"] = objects

        # --- 4. Load Saved Framings ---
        saved_framings_db = db.query(SavedFraming).filter_by(user_id=u.id).all()
        saved_framings_list = []
        for sf in saved_framings_db:
            # Resolve rig name for portability (ID is local to DB)
            r_name = None
            # Prefer the saved name if available (for portability), fallback to lookup
            if sf.rig_name:
                r_name = sf.rig_name
            elif sf.rig_id:
                rig_obj = db.get(Rig, sf.rig_id)
                if rig_obj: r_name = rig_obj.rig_name

            saved_framings_list.append({
                "object_name": sf.object_name,
                "rig_name": r_name,
                "ra": sf.ra,
                "dec": sf.dec,
                "rotation": sf.rotation,
                "survey": sf.survey,
                "blend_survey": sf.blend_survey,
                "blend_opacity": sf.blend_opacity
            })
        user_config["saved_framings"] = saved_framings_list

        # --- 5. Load Saved Views (NEW) ---
        saved_views_db = db.query(SavedView).filter_by(user_id=u.id).all()
        user_config["saved_views"] = [
            {
                "name": v.name,
                "description": v.description,
                "is_shared": v.is_shared,
                "settings": json.loads(v.settings_json)
            } for v in saved_views_db
        ]

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
        saved_framings_db = db.query(SavedFraming).filter_by(user_id=u.id).all()
        saved_framings_list = []
        for sf in saved_framings_db:
            # Resolve rig name for portability (ID is local to DB)
            r_name = None
            if sf.rig_id:
                # We can query efficiently or just let it be lazy if N is small
                rig_obj = db.get(Rig, sf.rig_id)
                if rig_obj: r_name = rig_obj.rig_name

            saved_framings_list.append({
                "object_name": sf.object_name,
                "rig_name": r_name,
                "ra": sf.ra,
                "dec": sf.dec,
                "rotation": sf.rotation,
                "survey": sf.survey,
                "blend_survey": sf.blend_survey,
                "blend_opacity": sf.blend_opacity
            })
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
                o.to_dict() for o in db.query(AstroObject).filter_by(user_id=u.id).all()
            ],
            "saved_framings": saved_framings_list,
            "saved_views": [
                {
                    "name": v.name,
                    "description": v.description,
                    "is_shared": v.is_shared,
                    "settings": json.loads(v.settings_json)
                }
                for v in db.query(SavedView).filter_by(user_id=u.id).order_by(SavedView.name).all()
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
                    {"id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm, "is_shared": c.is_shared, "original_user_id": c.original_user_id, "original_item_id": c.original_item_id}
                    for c in bykind("telescope")
                ],
                "cameras": [
                    {"id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                     "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um, "is_shared": c.is_shared, "original_user_id": c.original_user_id, "original_item_id": c.original_item_id}
                    for c in bykind("camera")
                ],
                "reducers_extenders": [
                    {"id": c.id, "name": c.name, "factor": c.factor, "is_shared": c.is_shared, "original_user_id": c.original_user_id, "original_item_id": c.original_item_id}
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

        db_projects = db.query(Project).filter_by(user_id=u.id).all()
        projects_list = []
        for p in db_projects:
            projects_list.append({
                "project_id": p.id,
                "project_name": p.name,
                "target_object_id": p.target_object_name,
                "status": p.status,
                "goals": p.goals,
                "description_notes": p.description_notes,
                "framing_notes": p.framing_notes,
                "processing_notes": p.processing_notes,
                "final_image_file": p.final_image_file
            })

        jdoc = {
            "projects": projects_list,
            "sessions": [
                {
                    "date": s.date_utc.isoformat(), "object_name": s.object_name, "notes": s.notes,
                    "session_id": s.external_id or s.id,  # Ensure ID is preserved
                    "project_id": s.project_id,  # Link is preserved
                    "object_name": s.object_name,
                    "notes": s.notes,
                    "number_of_subs_light": s.number_of_subs_light,
                    "exposure_time_per_sub_sec": s.exposure_time_per_sub_sec,
                    "filter_L_subs": s.filter_L_subs, "filter_L_exposure_sec": s.filter_L_exposure_sec,
                    "filter_R_subs": s.filter_R_subs, "filter_R_exposure_sec": s.filter_R_exposure_sec,
                    "filter_G_subs": s.filter_G_subs, "filter_G_exposure_sec": s.filter_G_exposure_sec,
                    "filter_B_subs": s.filter_B_subs, "filter_B_exposure_sec": s.filter_B_exposure_sec,
                    "filter_Ha_subs": s.filter_Ha_subs, "filter_Ha_exposure_sec": s.filter_Ha_exposure_sec,
                    "filter_OIII_subs": s.filter_OIII_subs, "filter_OIII_exposure_sec": s.filter_OIII_exposure_sec,
                    "filter_SII_subs": s.filter_SII_subs, "filter_SII_exposure_sec": s.filter_SII_exposure_sec,
                    "calculated_integration_time_minutes": s.calculated_integration_time_minutes,
                    "telescope_name_snapshot": s.telescope_name_snapshot,
                    "reducer_name_snapshot": s.reducer_name_snapshot,
                    "camera_name_snapshot": s.camera_name_snapshot,
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
        _migrate_saved_framings(db, user, cfg_data)
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

def import_catalog_pack_for_user(db, user: DbUser, catalog_config: dict, pack_id: str) -> tuple[int, int]:
    """Import a catalog pack into a user's library in a non-destructive way.

    - Adds new objects that do not already exist for the user (by object_name, case-insensitive).
    - If an object already exists, it is skipped (user data is not overwritten), but
      catalog_sources can be updated to include the pack_id for tracking.

    Returns (created_count, skipped_count).
    """
    created = 0
    skipped = 0

    objs = (catalog_config or {}).get("objects", []) or []

    def _merge_sources(current: str | None, new_id: str) -> str:
        if not new_id:
            return current or ""
        if not current:
            return new_id
        parts = {p.strip() for p in str(current).split(',') if p.strip()}
        parts.add(new_id)
        return ",".join(sorted(parts))

    for o in objs:
        try:
            ra_val = o.get("RA") if o.get("RA") is not None else o.get("RA (hours)")
            dec_val = o.get("DEC") if o.get("DEC") is not None else o.get("DEC (degrees)")

            raw_obj_name = o.get("Object") or o.get("object") or o.get("object_name")
            if not raw_obj_name or not str(raw_obj_name).strip():
                print(f"[CATALOG IMPORT][SKIP] Entry is missing an 'Object' identifier: {o}")
                skipped += 1
                continue

            object_name = normalize_object_name(raw_obj_name)

            common_name = o.get("Common Name") or o.get("Name") or o.get("common_name")
            # If common_name is still blank, use the raw (pretty) object name as a fallback
            if not common_name or not str(common_name).strip():
                common_name = str(raw_obj_name).strip()

            obj_type = o.get("Type") or o.get("type")
            constellation = o.get("Constellation") or o.get("constellation")
            magnitude = o.get("Magnitude") if o.get("Magnitude") is not None else o.get("magnitude")
            size = o.get("Size") if o.get("Size") is not None else o.get("size")
            sb = o.get("SB") if o.get("SB") is not None else o.get("sb")

            ra_f = float(ra_val) if ra_val is not None else None
            dec_f = float(dec_val) if dec_val is not None else None

            # Compute constellation if missing but coordinates exist
            if (not constellation) and (ra_f is not None) and (dec_f is not None):
                try:
                    coords = SkyCoord(ra=ra_f * u.hourangle, dec=dec_f * u.deg)
                    constellation = get_constellation(coords)
                except Exception:
                    constellation = None

            # Look up existing object by normalized name for this user
            existing = db.query(AstroObject).filter_by(
                user_id=user.id,
                object_name=object_name
            ).one_or_none()

            # Catalog info: allow explicit fields but always ensure pack_id is recorded
            raw_catalog_sources = o.get("catalog_sources")
            raw_catalog_info = o.get("catalog_info") or o.get("Info") or o.get("info")

            if existing:
                # Non-destructive: do not overwrite any user fields.
                # Only update catalog_sources to include this pack_id.
                existing_catalog_sources = existing.catalog_sources
                merged_sources = _merge_sources(existing_catalog_sources, pack_id)
                existing.catalog_sources = merged_sources
                # Keep existing.catalog_info as-is; do not overwrite.
                skipped += 1
                continue

            # New object: we require RA/DEC to be usable
            if (ra_f is None) or (dec_f is None):
                print(f"[CATALOG IMPORT][SKIP] Object '{object_name}' has no RA/DEC, skipping.")
                skipped += 1
                continue

            catalog_sources_merged = _merge_sources(str(raw_catalog_sources) if raw_catalog_sources else None, pack_id)
            catalog_info = raw_catalog_info

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
                active_project=False,
                project_name=None,
                is_shared=False,
                shared_notes=None,
                original_user_id=None,
                original_item_id=None,
                catalog_sources=catalog_sources_merged,
                catalog_info=catalog_info,
            )
            db.add(new_object)
            created += 1

        except Exception as e:
            print(f"[CATALOG IMPORT] Could not process object entry '{o}'. Error: {e}")
            skipped += 1

    return (created, skipped)

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
        _INSTANCE_PATH = globals().get("INSTANCE_PATH") or os.path.join(os.getcwd(), "instance")
        _ENV_FILE = os.path.join(_INSTANCE_PATH, ".env")
        load_dotenv(dotenv_path=_ENV_FILE, override=True)
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
            usernames_to_migrate.update(["guest_user"])
            print("[MIGRATION] Excluding 'default' user's YAML data from multi-user migration.")

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


# =============================================================================
# Flask and Flask-Login Setup
# =============================================================================

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
        if not _has_key("NOVA_CATALOG_URL"):
            additions.append(f"NOVA_CATALOG_URL=https://catalogs.nova-tracker.com")

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
CATALOG_MANIFEST_CACHE = {"data": None, "expires": 0}

# CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config_default.yaml")


STELLARIUM_ERROR_MESSAGE = os.getenv("STELLARIUM_ERROR_MESSAGE")
NOVA_CATALOG_URL = config('NOVA_CATALOG_URL', default='')
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

# =============================================================================
# Automated Single-User Migration (with lock)
# =============================================================================
# We only run this automated migration if in SINGLE_USER_MODE.
# In multi-user mode, the admin MUST run 'flask migrate-yaml-to-db' manually.
if SINGLE_USER_MODE:
    print("[MIGRATION] Single-User Mode: Attempting automated migration...")

    # Use the existing _FileLock class (defined around line 1709)
    # This ensures only ONE gunicorn worker runs the migration,
    # preventing database lock errors.
    lock_path = os.path.join(INSTANCE_PATH, "migration.lock")

    try:
        with _FileLock(lock_path):
            print("[MIGRATION] Acquired migration lock.")
            # Run the initialization and migration functions
            initialize_instance_directory()
            run_one_time_yaml_migration()
            print("[MIGRATION] Migration check complete. Releasing lock.")

    except Exception as e:
        print(f"❌ FATAL ERROR during automated migration: {e}")
        print("   -> This might be a file permission issue on 'instance/migration.lock'")
        print("   -> The application may not start correctly.")

else:
    print("[MIGRATION] Multi-User Mode: Automated migration is disabled.")
    print("   -> If this is a new setup, run 'docker compose run --rm nova flask migrate-yaml-to-db' to migrate data.")


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
        f.write(f"NOVA_CATALOG_URL=https://catalogs.nova-tracker.com\n")

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


@app.before_request
def load_global_request_context():
    # 1. Skip all expensive logic for static files
    if request.endpoint in ('static', 'favicon'):
        return

    # 2. Handle Single-User-Mode login bypass
    if SINGLE_USER_MODE and not current_user.is_authenticated:
        login_user(User("default", "default"))

    # 3. Determine username
    username = None
    if SINGLE_USER_MODE:
        username = "default"
        g.is_guest = False
    elif hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
        username = current_user.username
        g.is_guest = False
    else:
        username = "guest_user"
        g.is_guest = True

    if not username:
        g.db_user = None
        return

    # 4. Get DB user and UI preferences (FAST queries)
    db = get_db()
    try:
        # Get or create the user in app.db
        app_db_user = get_or_create_db_user(db, username)
        g.db_user = app_db_user

        if not app_db_user:
            return

        # --- Load UI Prefs (general settings) ---
        g.user_config = {}
        prefs = db.query(UiPref).filter_by(user_id=app_db_user.id).first()
        if prefs and prefs.json_blob:
            try:
                g.user_config = json.loads(prefs.json_blob)
            except json.JSONDecodeError:
                pass  # g.user_config remains {}

        # 5. Load effective settings (depends on g.user_config)
        load_effective_settings()

    except Exception as e:
        # Handle exceptions, maybe log them
        print(f"Error in slim load_global_request_context: {e}")
        traceback.print_exc()
    finally:
        db.close()

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
    finally:
        db.close()

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
# --- End Helper Function ---

@app.route('/api/get_plot_data/<path:object_name>')
def get_plot_data(object_name):
    load_full_astro_context()  # Ensures g context is loaded if needed
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
    data = get_ra_dec(object_name)  # Uses g.objects_map internally
    if not data:
        return jsonify({"error": "Object data not found or invalid."}), 404
    ra = data.get('RA (hours)')
    dec = data.get('DEC (deg)', data.get('DEC (degrees)'))
    if ra is None or dec is None:
        return jsonify({"error": "RA/DEC missing for object."}), 404
    try:
        ra = float(ra);
        dec = float(dec)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid RA/DEC format for object."}), 400

    # --- 2) Read params with SAFE fallbacks ---
    default_lat = getattr(g, "lat", None)
    default_lon = getattr(g, "lon", None)
    default_tz = getattr(g, "tz_name", "UTC")
    default_loc_name = getattr(g, "selected_location", "")

    plot_lat_str = request.args.get('plot_lat', '').strip()
    plot_lon_str = request.args.get('plot_lon', '').strip()
    plot_tz_name = request.args.get('plot_tz', '').strip()
    plot_loc_name = request.args.get('plot_loc_name', '').strip()

    try:
        lat = float(plot_lat_str) if plot_lat_str else default_lat
        lon = float(plot_lon_str) if plot_lon_str else default_lon
        tz_name = plot_tz_name if plot_tz_name else default_tz
        loc_name = plot_loc_name if plot_loc_name else default_loc_name

        if lat is None or lon is None:
            raise ValueError("Could not determine latitude or longitude.")
        if not tz_name:
            tz_name = "UTC"

        local_tz = pytz.timezone(tz_name)
    except Exception as e:
        print(f"[API Plot Data] Error parsing location parameters: {e}")
        return jsonify({"error": f"Invalid location or timezone data: {e}"}), 400

    now_local = datetime.now(local_tz)
    day = int(request.args.get('day', now_local.day))
    month = int(request.args.get('month', now_local.month))
    year = int(request.args.get('year', now_local.year))
    try:
        local_date_obj = datetime(year, month, day)
        local_date = local_date_obj.strftime('%Y-%m-%d')
    except ValueError:
        print(f"[API Plot Data] Error: Invalid date components ({year}-{month}-{day}). Using current date.")
        local_date_obj = now_local
        local_date = now_local.strftime('%Y-%m-%d')

    # --- 3) Build time grid and object series ---
    sampling_interval = getattr(g, 'sampling_interval', 15)
    times_local, times_utc = get_common_time_arrays(tz_name, local_date,
                                                    sampling_interval_minutes=5)
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_obj = sky_coord.transform_to(altaz_frame)
    altitudes = altaz_obj.alt.deg
    azimuths = (altaz_obj.az.deg + 360.0) % 360.0

    # --- 4) Weather forecast section (DECOUPLED) ---
    # Weather is now fetched asynchronously by the client after the chart loads.
    # We just send an empty array so the data structure is consistent.
    weather_forecast_series = []
    is_offline = request.args.get('offline') == 'true'
    print(f"[API Plot Data] Skipping weather fetch (will be loaded by client). Offline mode: {is_offline}")
    # --- END Weather Section ---

    # --- 5) Horizon mask (use g context for user config) ---
    location_config = {}
    try:
        user_cfg = getattr(g, 'user_config', {}) or {}
        locations_cfg = user_cfg.get("locations", {}) or {}
        location_config = locations_cfg.get(loc_name, {})
    except Exception as e:
        print(f"[API Plot Data] WARN: Could not load user/location config from g context: {e}")

    horizon_mask = location_config.get("horizon_mask")

    altitude_threshold = 20
    try:
        user_cfg = getattr(g, 'user_config', {}) or {}
        altitude_threshold = user_cfg.get("altitude_threshold", 20)
    except Exception:
        pass
    if location_config.get("altitude_threshold") is not None:
        altitude_threshold = location_config.get("altitude_threshold")

    if horizon_mask and isinstance(horizon_mask, list) and len(horizon_mask) > 1:
        try:
            sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
            horizon_mask_altitudes = [interpolate_horizon(az, sorted_mask, altitude_threshold) for az in azimuths]
        except Exception as hm_err:
            print(f"[API Plot Data] ERROR calculating horizon mask altitudes: {hm_err}")
            horizon_mask_altitudes = [altitude_threshold] * len(azimuths)
    else:
        horizon_mask_altitudes = [altitude_threshold] * len(azimuths)

    # --- 6) Moon series ---
    moon_altitudes = [];
    moon_azimuths = []
    try:
        if not times_utc or len(times_utc) == 0:
            raise ValueError("times_utc array is empty or invalid for Moon calculation.")

        for t in times_utc:
            t_ast = Time(t)
            moon_icrs = get_body('moon', t_ast, location=location)
            moon_altaz = moon_icrs.transform_to(AltAz(obstime=t_ast, location=location))
            moon_altitudes.append(moon_altaz.alt.deg)
            moon_azimuths.append((moon_altaz.az.deg + 360.0) % 360.0)
    except Exception as moon_err:
        print(f"[API Plot Data] ERROR calculating Moon series: {moon_err}")
        moon_altitudes = [None] * len(altitudes)
        moon_azimuths = [None] * len(altitudes)

    # --- 7) Sun events and transit ---
    try:
        sun_events_curr = calculate_sun_events_cached(local_date, tz_name, lat, lon)
        next_date_str = (local_date_obj + timedelta(days=1)).strftime('%Y-%m-%d')
        sun_events_next = calculate_sun_events_cached(next_date_str, tz_name, lat, lon)
        transit_time_str = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
    except Exception as sun_err:
        print(f"[API Plot Data] ERROR calculating Sun events/transit: {sun_err}")
        sun_events_curr = {}
        sun_events_next = {}
        transit_time_str = "Error"

    # --- 8) Force exactly 24h window ---
    if not times_local:
        print("[API Plot Data] ERROR: times_local array is empty. Cannot generate plot.")
        return jsonify({"error": "Could not generate time series for plot."}), 500
    start_time = times_local[0]
    end_time = start_time + timedelta(hours=24)
    final_times_iso = [start_time.isoformat()] + [t.isoformat() for t in times_local] + [end_time.isoformat()]

    # --- Final plot data structure ---
    plot_data = {
        "times": final_times_iso,
        "object_alt": [None] + list(altitudes) + [None],
        "object_az": [None] + list(azimuths) + [None],
        "moon_alt": [None] + moon_altitudes + [None],
        "moon_az": [None] + moon_azimuths + [None],
        "horizon_mask_alt": [None] + horizon_mask_altitudes + [None],
        "sun_events": {"current": sun_events_curr, "next": sun_events_next},
        "transit_time": transit_time_str,
        "date": local_date,
        "timezone": tz_name,
        "weather_forecast": weather_forecast_series # This is now always []
    }

    try:
        plot_data = recursively_clean_numpy_types(plot_data)
    except Exception as clean_err:
        print(f"[API Plot Data] ERROR cleaning data for JSON: {clean_err}")
        return jsonify({"error": "Failed to serialize plot data."}), 500

    return jsonify(plot_data)


@app.route('/api/get_weather_forecast')
def get_weather_forecast_api():
    """
    API endpoint to exclusively fetch and return processed weather forecast data.
    """
    # 1. Get lat, lon, tz from request args, with fallbacks to 'g'
    # We don't need the full astro context, just the location defaults
    if not hasattr(g, 'lat'):
        # A minimal load if 'g' context is missing (e.g., direct API hit)
        load_global_request_context()
        load_full_astro_context()

    try:
        # Use request args if provided and valid, otherwise fallback to g
        lat_str = request.args.get('lat')
        lon_str = request.args.get('lon')
        tz_name_req = request.args.get('tz')

        lat = float(lat_str) if lat_str else g.lat
        lon = float(lon_str) if lon_str else g.lon
        tz_name = tz_name_req if tz_name_req else g.tz_name

        if lat is None or lon is None: raise ValueError("Lat/Lon not found.")
        if not tz_name: tz_name = "UTC"

        local_tz = pytz.timezone(tz_name)  # Validate tz
    except Exception as e:
        return jsonify({"error": f"Invalid location/tz: {e}"}), 400

    # 2. Call the hybrid weather function (this uses the cache)
    print(f"[API Weather] Fetching hybrid weather for lat={lat}, lon={lon}")
    weather_data = get_hybrid_weather_forecast(lat, lon)

    # 3. Process the data (copying logic from old get_plot_data)
    weather_forecast_series = []
    if weather_data and isinstance(weather_data.get('dataseries'), list):
        try:
            init_str = weather_data.get('init', '')
            try:
                init_time = datetime.strptime(init_str, "%Y%m%d%H").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                init_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
                print(f"[API Weather] WARN: Invalid init string. Falling back to now: {init_time.isoformat()}")

            for block in weather_data['dataseries']:
                timepoint_hours = block.get('timepoint')
                if timepoint_hours is None: continue
                try:
                    start_time = init_time + timedelta(hours=int(timepoint_hours))

                    # --- FIX: The hybrid cache *always* produces 1-hour blocks. ---
                    # Remove the bad 3-hour guessing logic.
                    end_time = start_time + timedelta(hours=1)
                    # --- END FIX ---

                    seeing_val = block.get("seeing")
                    transparency_val = block.get("transparency")

                    # Pass the raw values (e.g., 5 or -9999) directly.
                    # The JavaScript is built to handle these.
                    processed_block = {
                        "start": start_time.isoformat(),
                        "end": end_time.isoformat(),
                        "cloudcover": block.get("cloudcover"),
                        "seeing": seeing_val,
                        "transparency": transparency_val,
                    }
                    # Append the entire block, not a stripped version.
                    weather_forecast_series.append(processed_block)

                except Exception as e:
                    print(f"[API Weather] WARN: Skipping bad block: {e}")
                    continue  # Skip bad block
        except Exception as e:
            print(f"[API Weather] ERROR: Failed processing dataseries: {e}")
            traceback.print_exc()
            weather_forecast_series = []  # Clear on error
    else:
        print("[API Weather] No dataseries found in weather_data.")

    # 4. Return the processed forecast (with ISO strings)
    return jsonify({"weather_forecast": weather_forecast_series})


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


def get_open_meteo_data(lat: float, lon: float) -> dict | None:
    """
    Fetches weather data from Open-Meteo with basic error handling.
    Returns parsed JSON data on success, None on failure.
    """
    # print(f"[Weather Fallback] Attempting fetch from Open-Meteo for lat={lat}, lon={lon}")
    try:
        # Request cloud cover at different levels, temperature, and relative humidity
        # We get hourly data for the next 7 days
        base_url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
            "forecast_days": 7,
            "timezone": "UTC"  # Request data in UTC for easier processing
        }

        r = requests.get(base_url, params=params, timeout=10)  # 10-second timeout

        # --- Check for HTTP errors ---
        if r.status_code != 200:
            print(f"[Weather Fallback] ERROR (Open-Meteo): Received non-200 status code {r.status_code}")
            print(f"[Weather Fallback] Response text (first 200 chars): {r.text[:200]}")
            return None

        # --- Try to parse the JSON ---
        data = r.json()

        # --- Basic validation ---
        if not data or 'hourly' not in data or 'time' not in data['hourly']:
            print(f"[Weather Fallback] ERROR (Open-Meteo): Invalid data structure received.")
            return None

        # print(f"[Weather Fallback] Successfully fetched data from Open-Meteo.")
        return data

    except requests.exceptions.RequestException as e:
        # Handles timeouts, connection errors, etc.
        print(f"[Weather Fallback] ERROR (Open-Meteo): Request failed. Error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[Weather Fallback] ERROR (Open-Meteo): Failed to decode JSON. Error: {e}")
        print(f"[Weather Fallback] Response text (first 200 chars): {r.text[:200]}")
        return None
    except Exception as e:
        # Catch any other unexpected errors
        print(f"[Weather Fallback] ERROR (Open-Meteo): An unexpected error occurred. Error: {e}")
        return None


def get_hybrid_weather_forecast(lat, lon):
    # --- Rounding and Cache Key (No change) ---
    rounded_lat = round(lat, 5)
    rounded_lon = round(lon, 5)
    cache_key = f"hybrid_{rounded_lat}_{rounded_lon}"
    # print(f"[Weather Func] Using cache key: '{cache_key}' for lat={lat}, lon={lon}")

    # --- Cache Check (No change) ---
    now = datetime.now(UTC)
    entry = weather_cache.get(cache_key) or {}
    last_good = entry.get('data')
    last_err_ts = entry.get('last_err_ts')
    if entry and 'expires' in entry:
        expires_dt = entry['expires']
        is_expired = False
        try:
            if expires_dt.tzinfo is not None:
                if now >= expires_dt: is_expired = True
            elif now.replace(tzinfo=None) >= expires_dt:
                is_expired = True
        except TypeError as te:
            print(f"[Weather Func] WARN: Timezone comparison error for key '{cache_key}': {te}")
            is_expired = True
        if not is_expired:
            return entry['data']

    # --- Helper Functions (No change) ---
    def _update_cache_ok(data, ttl_hours=3):
        expiry_time = datetime.now(UTC) + timedelta(hours=ttl_hours)
        weather_cache[cache_key] = {'data': data, 'expires': expiry_time, 'last_err_ts': None}
        # print(f"[Weather Func] Cache UPDATED for '{cache_key}', expires {expiry_time.isoformat()}")

    def _rate_limited_error(msg):
        nonlocal last_err_ts
        now_aware = datetime.now(UTC)
        if not last_err_ts or (now_aware - last_err_ts) > timedelta(minutes=15):
            print(msg)
            weather_cache.setdefault(cache_key, {})['last_err_ts'] = now_aware

    # --- START: NEW HYBRID FETCH LOGIC ---

    final_data_to_cache = None
    base_dataseries = {}

    # --- FIX: Initialize init_time and init_str to None in the outer scope ---
    init_time = None
    init_str = None
    # --- END FIX ---

    # === 1. Always fetch Open-Meteo for reliable cloud data ===
    # print(f"[Weather Func] Fetching base cloud data from Open-Meteo for key '{cache_key}'")
    open_meteo_data = get_open_meteo_data(lat, lon)

    if open_meteo_data and 'hourly' in open_meteo_data:
        # print(f"[Weather Func] Open-Meteo succeeded. Translating data...")
        try:
            translated_dataseries = {}  # Use a dict for easier merging
            om_hourly = open_meteo_data['hourly']
            times = om_hourly.get('time', [])

            # init_time is defined here (if successful)
            if times:
                # --- START FIX: Ensure parsed datetimes are offset-aware ---
                # 1. Parse the naive time (stripping 'Z' if it exists, fromisoformat handles T)
                first_time_naive = datetime.fromisoformat(times[0].split('Z')[0])
                # 2. Attach the UTC timezone to make it aware
                first_time_aware = first_time_naive.replace(tzinfo=UTC)
                # 3. Create the init_time (midnight), which is now guaranteed aware
                init_time = first_time_aware.replace(hour=0, minute=0, second=0, microsecond=0)
                init_str = init_time.strftime("%Y%m%d%H")
            else:
                init_time = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
                init_str = init_time.strftime("%Y%m%d%H")
                print("[Weather Func] WARN: Open-Meteo returned no times. Using current day as init.")

            for i, time_str in enumerate(times):
                # --- FIX: Apply the same logic to the loop variable ---
                current_time_naive = datetime.fromisoformat(time_str.split('Z')[0])
                current_time = current_time_naive.replace(tzinfo=UTC)
                timepoint = int((current_time - init_time).total_seconds() / 3600)
                # --- END FIX ---

                cc_low = om_hourly.get('cloud_cover_low', [0] * len(times))[i]
                cc_mid = om_hourly.get('cloud_cover_mid', [0] * len(times))[i]
                cc_high = om_hourly.get('cloud_cover_high', [0] * len(times))[i]
                total_cloud_percent = max(cc_low or 0, cc_mid or 0, cc_high or 0)

                if total_cloud_percent < 5:
                    cloudcover_1_9 = 1
                elif total_cloud_percent < 15:
                    cloudcover_1_9 = 2
                elif total_cloud_percent < 25:
                    cloudcover_1_9 = 3
                elif total_cloud_percent < 35:
                    cloudcover_1_9 = 4
                elif total_cloud_percent < 55:
                    cloudcover_1_9 = 5
                elif total_cloud_percent < 65:
                    cloudcover_1_9 = 6
                elif total_cloud_percent < 75:
                    cloudcover_1_9 = 7
                elif total_cloud_percent < 85:
                    cloudcover_1_9 = 8
                else:
                    cloudcover_1_9 = 9

                temp = om_hourly.get('temperature_2m', [None] * len(times))[i]
                rh = om_hourly.get('relative_humidity_2m', [None] * len(times))[i]

                block = {
                    "timepoint": timepoint,
                    "cloudcover": cloudcover_1_9,
                    "temp2m": temp,
                    "rh2m": rh,
                    "seeing": -9999,
                    "transparency": -9999,
                }
                translated_dataseries[timepoint] = block  # Store by timepoint

            base_dataseries = translated_dataseries
            # print(f"[Weather Func] Successfully translated {len(base_dataseries)} blocks from Open-Meteo.")

        except Exception as e:
            _rate_limited_error(f"[Weather Func] ERROR: Failed to translate Open-Meteo data: {e}")
            # Continue with empty base_dataseries

    else:
        _rate_limited_error(f"[Weather Func] ERROR: Open-Meteo (base) fetch failed for key '{cache_key}'.")
        # base_dataseries is still {}

    # === 2. Attempt to fetch 7Timer! 'astro' for seeing/transparency ===
    # print(f"[Weather Func] Fetching enhancement data (seeing) from 7Timer! 'astro' for key '{cache_key}'")
    astro_data_7t = get_weather_data_with_retries(lat, lon, product="astro")

    if astro_data_7t and astro_data_7t.get('dataseries'):
        # print("[DEBUG] 7Timer! RAW DATA (first 5 blocks):")
        # print(astro_data_7t['dataseries'][:5])
        # print(f"[Weather Func] 7Timer! 'astro' succeeded. Merging data...")

        # --- START FIX: Calculate 7Timer! init time ---
        astro_init_str = astro_data_7t.get('init')
        try:
            # Get the 7Timer! init time as a datetime object
            astro_init_time = datetime.strptime(astro_init_str, "%Y%m%d%H").replace(tzinfo=UTC)
        except (ValueError, TypeError, AttributeError):
            _rate_limited_error(
                f"[Weather Func] ERROR: 7Timer! 'astro' gave invalid init string: '{astro_init_str}'. Cannot merge seeing data.")
            astro_data_7t = None  # Treat as failed
        # --- END FIX ---

        if astro_data_7t:  # Check if it's still valid after parsing init

            # --- FIX: If init_time is STILL None, Open-Meteo failed. Use 7Timer's init as the base.
            if init_time is None:
                init_time = astro_init_time
                init_str = astro_init_str
                print(f"[Weather Func] WARN: Open-Meteo failed. Using 7Timer! init_time as base: {init_str}")
            # --- END FIX ---

            for ablk in astro_data_7t.get('dataseries', []):
                tp_7timer = ablk.get('timepoint')
                if tp_7timer is None: continue

                try:
                    # --- START FIX: Recalculate timepoint ---
                    # Get the absolute UTC time of the 7Timer! forecast block
                    abs_time = astro_init_time + timedelta(hours=int(tp_7timer))

                    # Calculate the timepoint relative to our *guaranteed* init_time
                    # This 'tp' is the correct key for our base_dataseries
                    tp = int((abs_time - init_time).total_seconds() / 3600)
                    # --- END FIX ---

                    if tp in base_dataseries:
                        # --- FIX: Only merge the keys we want from 7Timer! ---
                        # This preserves the correct 'timepoint' and high-res 'cloudcover'
                        # from the Open-Meteo block, while adding 'seeing' and 'transparency'.
                        if 'seeing' in ablk:
                            base_dataseries[tp]['seeing'] = ablk['seeing']
                        if 'transparency' in ablk:
                            base_dataseries[tp]['transparency'] = ablk['transparency']
                        # --- END FIX ---
                    else:
                        # Open-Meteo failed, use 7Timer! block as a fallback
                        # Add cloudcover placeholder if it doesn't exist
                        ablk.setdefault('cloudcover', 9)
                        base_dataseries[tp] = ablk

                except Exception as e:
                    print(f"[Weather Func] WARN: Skipping 7Timer! block, could not align timepoints. Error: {e}")

        # print(f"[Weather Func] Merge complete.")
    else:
        _rate_limited_error(
            f"[Weather Func] WARN: 7Timer! 'astro' (enhancement) fetch failed for key '{cache_key}'. Seeing data will be unavailable.")

    # --- END: NEW HYBRID FETCH LOGIC ---

    # --- Cache Update or Return Stale ---
    if base_dataseries:  # If we have *any* data (even just Open-Meteo)
        final_data_to_cache = {'init': init_str, 'dataseries': list(base_dataseries.values())}
        _update_cache_ok(final_data_to_cache, ttl_hours=3)
        return final_data_to_cache
    else:
        # Both failed. Return last good data if available.
        print(f"[Weather Func] All sources failed. Returning stale data (if available) for key '{cache_key}'.")
        return last_good or None

# --- Check weather_cache_worker ---
def weather_cache_worker():
    # ... (Keep existing startup wait) ...
    while True:
        print("[WEATHER WORKER] Starting background refresh cycle...")
        unique_locations = set()
        with app.app_context():
            db = None
            try:
                db = get_db()
                active_locs = db.query(Location).filter_by(active=True).all()
                for loc in active_locs:
                    if loc.lat is not None and loc.lon is not None:
                        # --- ADD ROUNDING HERE when adding to the set ---
                        unique_locations.add((round(loc.lat, 5), round(loc.lon, 5)))
                        # --- END ADD ROUNDING ---
            except Exception as e:
                print(f"[WEATHER WORKER] CRITICAL: Error querying locations from DB: {e}")
            finally:
                if db: db.close()

        print(f"[WEATHER WORKER] Found {len(unique_locations)} unique active locations to refresh.")
        refreshed_count = 0
        for lat, lon in unique_locations: # These lat/lon are now rounded
            try:
                # --- Pass the rounded lat/lon to the function ---
                # Although get_hybrid_weather_forecast now rounds internally,
                # passing the rounded values makes the log message below accurate.
                # print(f"[Weather Worker] Refreshing for rounded lat={lat}, lon={lon}") # Log rounded values
                get_hybrid_weather_forecast(lat, lon) # Call with potentially rounded values
                # --- End Pass Rounded ---
                refreshed_count += 1
                time.sleep(5)
            except Exception as e:
                print(f"[WEATHER WORKER] ERROR: Failed to fetch for ({lat}, {lon}): {e}")

        # ... (Keep existing sleep logic) ...
        print(f"[WEATHER WORKER] Refresh cycle complete ({refreshed_count}/{len(unique_locations)} successful). Sleeping for 2 hours.")
        time.sleep(2 * 60 * 60)

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
        latest_version_str = data.get("tag_name", "").lower().lstrip('v')  # Get version and remove leading 'v'
        current_version_str = APP_VERSION

        if not latest_version_str or not current_version_str:
            print("[VERSION CHECK] Could not determine current or latest version string.")
            return

        # --- START OF MODIFICATION ---

        # Convert version strings to comparable tuples of integers
        # e.g., "3.8.2" -> (3, 8, 2)
        # This will handle "3.10.0" > "3.9.0" correctly.
        current_version_tuple = tuple(map(int, current_version_str.split('.')))
        latest_version_tuple = tuple(map(int, latest_version_str.split('.')))

        # Compare the versions
        if latest_version_tuple > current_version_tuple:
            # --- END OF MODIFICATION --- (Original was: if latest_version and latest_version != current_version:)

            print(f"[VERSION CHECK] New version found: {latest_version_str}")
            LATEST_VERSION_INFO = {
                "new_version": latest_version_str,
                "url": data.get("html_url")
            }
        else:
            print(f"[VERSION CHECK] You are running the latest version (or a newer dev version).")
            LATEST_VERSION_INFO = {}  # Ensure info is cleared if no new version

    except requests.exceptions.RequestException as e:
        print(f"❌ [VERSION CHECK] Could not connect to GitHub API: {e}")
    except Exception as e:
        # This will also catch errors from tuple(map(int,...)) if versions are not like "1.2.3"
        print(f"❌ [VERSION CHECK] An unexpected error occurred (e.g., parsing versions): {e}")
        LATEST_VERSION_INFO = {}  # Clear on error

def trigger_outlook_update_for_user(username):
    """
    Loads a user's config and starts Outlook cache workers for all their locations.
    """
    print(f"[TRIGGER] Firing Outlook cache update for user '{username}' due to a project note change.")
    try:
        user_cfg = build_user_config_from_db(username)
        locations = user_cfg.get('locations', {})

        # --- START FIX: Determine sampling_interval correctly ---
        sampling_interval = 15  # Default
        if SINGLE_USER_MODE:
            # In single-user mode, get it from the user's config (UiPref blob)
            sampling_interval = user_cfg.get('sampling_interval_minutes', 15)
        else:
            # In multi-user mode, get it from environment variables (or default)
            sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))
        # --- END FIX ---

        for loc_name in locations.keys():
            # 1. Get the user ID and log string
            user_log_key = get_user_log_string(g.db_user.id, g.db_user.username)
            safe_log_key = user_log_key.replace(" | ", "_").replace(".", "").replace(" ", "_")

            # 2. Construct the key and filename
            status_key = f"({user_log_key})_{loc_name}"
            cache_filename = os.path.join(CACHE_DIR,
                                          f"outlook_cache_{safe_log_key}_{loc_name.lower().replace(' ', '_')}.json")

            # 3. Set status before starting
            cache_worker_status[status_key] = "starting"
            print(f"    -> Set status='starting' for {status_key}")

            # 4. Call the thread with all 6 arguments
            thread = threading.Thread(target=update_outlook_cache, args=(
                g.db_user.id,  # 1. user_id
                status_key,  # 2. status_key
                cache_filename,  # 3. cache_filename
                loc_name,  # 4. location_name
                user_cfg.copy(),  # 5. user_config
                sampling_interval  # 6. sampling_interval
            ))
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
                sampling_interval = cfg.get('sampling_interval_minutes') or 15
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


# --- CHANGE THIS (the function definition) ---
def update_outlook_cache(user_id, status_key, cache_filename, location_name, user_config, sampling_interval):
    # --- END CHANGE ---
    """
    Finds ALL good imaging opportunities for PROJECT objects only for the specified
    user and location, saving them sorted by date.
    Relies ONLY on passed arguments, not the global 'g' object.
    """
    # status_key and cache_filename are now passed in
    # e.g., status_key = "(123 | FirstName L.)_Home"
    # e.g., cache_filename = ".../outlook_cache_123_FirstName_L_home.json"

    with app.app_context():  # Keep app context for potential future DB needs, but avoid using 'g'
        print(f"--- [OUTLOOK WORKER {status_key}] Starting ---")
        cache_worker_status[status_key] = "running"

        try:
            # --- Extract Location Data from ARGUMENTS ---
            all_locations_from_config = user_config.get("locations", {})
            loc_cfg = all_locations_from_config.get(location_name)
            if not loc_cfg: raise ValueError(f"Location '{location_name}' not found.")
            lat = loc_cfg.get("lat");
            lon = loc_cfg.get("lon");
            tz_name = loc_cfg.get("timezone", "UTC")
            horizon_mask = loc_cfg.get("horizon_mask")
            if lat is None or lon is None: raise ValueError(f"Missing lat/lon for '{location_name}'.")
            print(f"[OUTLOOK WORKER {status_key}] Using Loc: lat={lat}, lon={lon}, tz={tz_name}")
            altitude_threshold = user_config.get("altitude_threshold", 20)

            # --- Extract Imaging Criteria from ARGUMENTS ---
            def _get_criteria_from_config(cfg):
                defaults = {"min_observable_minutes": 60, "min_max_altitude": 30, "max_moon_illumination": 20,
                            "min_angular_separation": 30, "search_horizon_months": 6}
                raw = (cfg or {}).get("imaging_criteria") or {};
                out = dict(defaults)
                if isinstance(raw, dict):
                    def _update_key(key, cast_func):
                        if key in raw and raw[key] is not None:
                            try:
                                out[key] = cast_func(str(raw[key]))
                            except:
                                pass

                    _update_key("min_observable_minutes", int);
                    _update_key("min_max_altitude", float);
                    _update_key("max_moon_illumination", int);
                    _update_key("min_angular_separation", int);
                    _update_key("search_horizon_months", int)
                out["min_observable_minutes"] = max(0, out.get("min_observable_minutes", 0));
                out["min_max_altitude"] = max(0.0, min(90.0, out.get("min_max_altitude", 0.0)));
                out["max_moon_illumination"] = max(0, min(100, out.get("max_moon_illumination", 100)));
                out["min_angular_separation"] = max(0, min(180, out.get("min_angular_separation", 0)));
                out["search_horizon_months"] = max(1, min(12, out.get("search_horizon_months", 1)))
                return out

            criteria = _get_criteria_from_config(user_config)

            # --- Extract Objects List from ARGUMENTS ---
            all_objects_from_config = user_config.get("objects", [])

            # Fetch framing status for Outlook
            framed_objects = set()
            db = get_db()
            try:
                rows = db.query(SavedFraming.object_name).filter_by(user_id=user_id).all()
                framed_objects = {r[0] for r in rows}
            finally:
                db.close()

            # --- START FIX: Build object map for this thread ---
            local_objects_map = {
                str(o.get("Object", "")).lower(): o
                for o in all_objects_from_config if isinstance(o, dict) and o.get("Object")
            }
            # --- END FIX ---

            # --- Filter Active Projects ---
            project_objects = []
            if isinstance(all_objects_from_config, list):
                for obj in all_objects_from_config:
                    if isinstance(obj, dict):
                        ap_val = obj.get("ActiveProject")
                        is_active = (ap_val is True or str(ap_val).lower() in ['true', '1', 'yes'])
                        if is_active: project_objects.append(obj)

            active_object_names = [o.get('Object', 'Unnamed') for o in project_objects]
            print(f"[OUTLOOK WORKER {status_key}] Found {len(project_objects)} active projects: {active_object_names}")

            if not project_objects:
                print(f"[OUTLOOK WORKER {status_key}] No active projects. Writing empty cache.")
                cache_content = {"metadata": {"last_successful_run_utc": datetime.now(pytz.utc).isoformat(),
                                              "location": location_name, "user_id": user_id}, "opportunities": []}
                with open(cache_filename, 'w') as f: json.dump(cache_content, f)
                cache_worker_status[status_key] = "complete"
                print(f"--- [OUTLOOK WORKER {status_key}] Finished (no active projects) ---")
                return  # Exit early

            # --- Calculation Loop ---
            all_good_opportunities = []
            local_tz = pytz.timezone(tz_name)
            start_date = datetime.now(local_tz).date()
            dates_to_check = [start_date + timedelta(days=i) for i in range(criteria["search_horizon_months"] * 30)]

            for obj_config_entry in project_objects:
                object_name_from_config = obj_config_entry.get("Object", "Unknown")
                try:
                    time.sleep(0.01)

                    # --- START FIX: Call get_ra_dec with the local map ---
                    obj_details = get_ra_dec(object_name_from_config, objects_map=local_objects_map)
                    # --- END FIX ---

                    object_name, ra, dec = obj_details.get("Object"), obj_details.get("RA (hours)"), obj_details.get(
                        "DEC (degrees)")
                    if not all([object_name, ra is not None, dec is not None]):
                        print(
                            f"[OUTLOOK WORKER {status_key}] Skipping {object_name_from_config}: Missing RA/DEC or lookup failed.")
                        continue

                    for d in dates_to_check:
                        date_str = d.strftime('%Y-%m-%d')

                        obs_duration, max_altitude, obs_from, obs_to = calculate_observable_duration_vectorized(
                            ra, dec, lat, lon, date_str, tz_name,
                            altitude_threshold, sampling_interval, horizon_mask=horizon_mask
                        )

                        if max_altitude < criteria["min_max_altitude"] or (obs_duration.total_seconds() / 60) < \
                                criteria["min_observable_minutes"]: continue

                        moon_phase = ephem.Moon(
                            local_tz.localize(datetime.combine(d, datetime.min.time().replace(hour=12))).astimezone(
                                pytz.utc)).phase
                        if moon_phase > criteria["max_moon_illumination"]: continue

                        sun_events = calculate_sun_events_cached(date_str, tz_name, lat, lon)
                        dusk = sun_events.get("astronomical_dusk", "20:00")
                        try:
                            dusk_time_obj = datetime.strptime(dusk, "%H:%M").time()
                        except ValueError:
                            dusk_time_obj = datetime.strptime("20:00", "%H:%M").time()
                        dusk_dt = local_tz.localize(datetime.combine(d, dusk_time_obj))
                        location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
                        time_obj = Time(dusk_dt.astimezone(pytz.utc))
                        frame = AltAz(obstime=time_obj, location=location_obj)
                        obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

                        # --- THIS IS THE CORRECTED LINE ---
                        moon_coord = get_body('moon', time_obj, location=location_obj)
                        # --- END CORRECTION ---

                        try:
                            separation = obj_coord.transform_to(frame).separation(moon_coord.transform_to(frame)).deg
                            if separation < criteria["min_angular_separation"]: continue
                        except Exception as sep_e:
                            print(
                                f"[OUTLOOK WORKER {status_key}] WARN Sep calc fail for {object_name} on {date_str}: {sep_e}")
                            continue

                        score_alt = max(0, min((max_altitude - 20) / 70, 1))
                        score_duration = min(obs_duration.total_seconds() / (3600 * 12), 1)
                        score_moon_illum = 1 - min(moon_phase / 100, 1)
                        score_moon_sep_dynamic = (1 - (moon_phase / 100)) + (moon_phase / 100) * min(separation / 180,
                                                                                                     1)
                        composite_score = 100 * (
                                    0.20 * score_alt + 0.15 * score_duration + 0.45 * score_moon_illum + 0.20 * score_moon_sep_dynamic)

                        if composite_score > 75:
                            stars = int(round((composite_score / 100) * 4)) + 1
                            # --- Ensure native Python floats are stored ---
                            opportunity_score = float(composite_score)
                            opportunity_max_alt = float(max_altitude)
                            # --- End ensure native floats ---
                            good_night_opportunity = {
                                "object_name": object_name, "common_name": obj_details.get("Common Name", object_name),
                                "has_framing": object_name in framed_objects,
                                "date": date_str, "score": opportunity_score, "rating": "★" * stars + "☆" * (5 - stars),
                                "rating_num": stars, "max_alt": round(opportunity_max_alt, 1),
                                "obs_dur": int(obs_duration.total_seconds() / 60),
                                "moon_illumination": round(moon_phase, 1),
                                "project": obj_config_entry.get("Project", "none"),
                                "type": obj_details.get("Type", "N/A"),
                                "constellation": obj_details.get("Constellation", "N/A"),
                                "magnitude": obj_details.get("Magnitude", "N/A"),
                                "size": obj_details.get("Size", "N/A"), "sb": obj_details.get("SB", "N/A")
                            }
                            all_good_opportunities.append(good_night_opportunity)

                except Exception as e:
                    print(f"❌ [OUTLOOK WORKER {status_key}] ERROR processing object '{object_name_from_config}': {e}")
                    # traceback.print_exc() # Uncomment for more detail if needed
                    continue
            # --- End Calculation Loop ---

            print(f"[OUTLOOK WORKER {status_key}] Found {len(all_good_opportunities)} total opportunities.")

            opportunities_sorted_by_date = sorted(all_good_opportunities, key=lambda x: x['date'])
            opportunities_sorted_by_date = recursively_clean_numpy_types(opportunities_sorted_by_date)

            # --- START CHANGE (inside the cache_content dictionary) ---
            cache_content = {
                "metadata": {"last_successful_run_utc": datetime.now(pytz.utc).isoformat(), "location": location_name,
                             "user_id": user_id},  # Use the real user_id
                "opportunities": opportunities_sorted_by_date
            }
            # --- END CHANGE ---

            # --- START CHANGE (to use the passed-in cache_filename) ---
            _atomic_write_yaml(cache_filename.replace('.json', '_debug.yaml'), cache_content)
            with open(cache_filename, 'w') as f:
                json.dump(cache_content, f)
            print(f"[OUTLOOK WORKER {status_key}] Successfully updated cache: {cache_filename}")
            # --- END CHANGE ---

            cache_worker_status[status_key] = "complete"

        except Exception as e:
            print(f"❌❌ [OUTLOOK WORKER {status_key}] FATAL ERROR: {e}")
            traceback.print_exc()
            cache_worker_status[status_key] = "error"
        finally:
            print(f"--- [OUTLOOK WORKER {status_key}] Finished (Status: {cache_worker_status.get(status_key)}) ---")

def warm_main_cache(username, location_name, user_config, sampling_interval):
    """
    Warms the main data cache on startup and then triggers the Outlook cache
    update for the same location.
    """
    # print(f"[CACHE WARMER] Starting for main data at location '{location_name}'.")
    try:
        # --- 1. DETERMINE LOCATION VARS (AND FIX BAD TIMEZONE) ---
        try:
            tz_name = user_config["locations"][location_name]["timezone"]
            local_tz = pytz.timezone(tz_name)
        except pytz.exceptions.UnknownTimeZoneError:
            print(
                f"❌ [CACHE WARMER] WARN: Invalid timezone '{tz_name}' for user '{username}' at location '{location_name}'. Falling back to UTC.")
            local_tz = pytz.timezone("UTC")
            tz_name = "UTC"  # <-- This is the fix

        # --- 2. GET LOCATION & DATE VARS (NOW OUTSIDE THE LOOP) ---
        # These are now read only ONCE, using the corrected tz_name
        observing_date_for_calcs = datetime.now(local_tz) - timedelta(hours=12)
        local_date = observing_date_for_calcs.strftime('%Y-%m-%d')
        lat = float(user_config["locations"][location_name]["lat"])
        lon = float(user_config["locations"][location_name]["lon"])
        altitude_threshold = user_config.get("altitude_threshold", 20)
        try:
            horizon_mask = user_config.get("locations", {}).get(location_name, {}).get("horizon_mask")
        except Exception:
            horizon_mask = None

        # --- 3. PROCESS ALL OBJECTS (LOOP) ---
        for obj_entry in user_config.get("objects", []):
            time.sleep(0.01)
            obj_name = obj_entry.get("Object")
            if not obj_name: continue

            cache_key = f"{obj_name.lower()}_{local_date}_{location_name.lower()}"
            if cache_key in nightly_curves_cache:
                continue

            ra = float(obj_entry.get("RA", 0))
            dec = float(obj_entry.get("DEC", 0))

            # --- THE BUG IS REMOVED ---
            # lat, lon, and tz_name are NO LONGER read here
            # --- END OF BUG FIX ---

            # This call is now safe because tz_name is the corrected one from step 1
            times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)

            location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
            altaz_frame = AltAz(obstime=times_utc, location=location)
            altitudes = sky_coord.transform_to(altaz_frame).alt.deg
            azimuths = sky_coord.transform_to(altaz_frame).az.deg
            transit_time = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)

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

        # --- 4. TRIGGER OUTLOOK CACHE (Unchanged) ---
        cache_filename = os.path.join(CACHE_DIR,
                                      f"outlook_cache_{username}_{location_name.lower().replace(' ', '_')}.json")

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
            # --- FIX START: Generate required arguments for the worker ---
            db = get_db()
            try:
                u_obj = db.query(DbUser).filter_by(username=username).first()
                u_id = u_obj.id if u_obj else 0
            finally:
                db.close()

            user_log_key = f"({u_id} | {username})"
            safe_log_key = f"{u_id}_{username}"
            status_key = f"{user_log_key}_{location_name}"
            cache_filename = os.path.join(CACHE_DIR,
                                          f"outlook_cache_{safe_log_key}_{location_name.lower().replace(' ', '_')}.json")

            thread = threading.Thread(target=update_outlook_cache,
                                      args=(u_id, status_key, cache_filename, location_name, user_config.copy(),
                                            sampling_interval))
            # --- FIX END ---
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
    # === START REFACTOR ===
    # Get counts directly from the database, which is the single source of truth.
    try:
        db = get_db()  # Get a DB session
        username_eff = "default" if SINGLE_USER_MODE else (
            current_user.username if getattr(current_user, "is_authenticated", False) else "default"
        )
        # Get the user from the app.db
        user = db.query(DbUser).filter_by(username=username_eff).one_or_none()

        if user:
            # Query the DB for the *actual* counts
            rigs_count = db.query(Rig).filter_by(user_id=user.id).count()
            objects_count = db.query(AstroObject).filter_by(user_id=user.id).count()
            locations_count = db.query(Location).filter_by(user_id=user.id).count()
            journals_count = db.query(JournalSession).filter_by(user_id=user.id).count()
        else:
            # Fallback if user not found for some reason
            rigs_count, objects_count, locations_count, journals_count = 0, 0, 0, 0

    except Exception as e:
        print(f"[TELEMETRY] DB count query failed: {e}")
        rigs_count, objects_count, locations_count, journals_count = 0, 0, 0, 0
    finally:
        if 'db' in locals() and db:
            db.close()
    # === END REFACTOR ===

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
            # === START REFACTOR ===
            # Use the DB-backed function instead of the deleted YAML function
            cfg = build_user_config_from_db(username)
            # === END REFACTOR ===
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
                        # === START REFACTOR ===
                        # Use the DB-backed function here as well
                        daily_cfg = build_user_config_from_db(username)
                        # === END REFACTOR ===
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
                    cfg = g.user_config if hasattr(g, 'user_config') else {}
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
        cfg = g.user_config if hasattr(g, 'user_config') else {}
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

def _handle_project_image_upload(file_object, project_id: str, username: str, existing_filename: str = None):
    """
    Handles image upload for a project.

    file_object: The file from request.files
    project_id: The ID of the project
    username: The current user's username
    existing_filename: The current filename (to delete the old one if replacing)

    Returns the new filename or None if no file was uploaded/valid.
    """
    if file_object and file_object.filename != '' and allowed_file(file_object.filename):
        try:
            # Delete old image if replacing
            if existing_filename:
                old_image_path = os.path.join(UPLOAD_FOLDER, username, existing_filename)
                if os.path.exists(old_image_path):
                    os.remove(old_image_path)

            file_extension = file_object.filename.rsplit('.', 1)[1].lower()
            new_filename = f"project_{project_id}.{file_extension}"
            user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
            os.makedirs(user_upload_dir, exist_ok=True)
            file_object.save(os.path.join(user_upload_dir, new_filename))
            return new_filename
        except Exception as e:
            print(f"Error handling project image upload: {e}")
            return None
    return existing_filename


@app.route('/project/<string:project_id>', methods=['GET', 'POST'])
@login_required
def project_detail(project_id):
    load_full_astro_context()  # Ensures g.db_user is loaded
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()

    try:
        project = db.query(Project).filter_by(id=project_id, user_id=g.db_user.id).one_or_none()
        if not project:
            flash("Project not found or you do not have permission to view it.", "error")
            return redirect(url_for('index'))

        # --- Aggregated Statistics ---
        total_integration_minutes = db.query(
            func.sum(JournalSession.calculated_integration_time_minutes)
        ).filter_by(project_id=project_id, user_id=g.db_user.id).scalar() or 0

        # Format integration time (e.g., 10h 30m)
        total_minutes = int(total_integration_minutes)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        total_integration_str = f"{hours}h {minutes}m"

        # Fetch all linked sessions eagerly to display them
        sessions = db.query(JournalSession).filter_by(project_id=project_id, user_id=g.db_user.id).order_by(
            JournalSession.date_utc.desc()).all()

        # --- Handle POST Request (Update Project) ---
        if request.method == 'POST':

            # 1. Handle image deletion
            if request.form.get('delete_final_image') == '1' and project.final_image_file:
                try:
                    os.remove(os.path.join(UPLOAD_FOLDER, username, project.final_image_file))
                    project.final_image_file = None
                except Exception as e:
                    print(f"Error deleting final image: {e}")
                    flash("Error deleting old image.", "warning")

            # 2. Handle image upload (returns new/existing filename)
            new_filename = _handle_project_image_upload(
                request.files.get('final_image'),
                project.id,
                username,
                project.final_image_file
            )

            # 3. Update all new fields (including the rich text from Trix)
            project.name = request.form.get('name')
            project.target_object_name = request.form.get('target_object_id')  # Note: Renamed from 'target_object_name'
            project.status = request.form.get('status')

            # Rich text notes (Trix content is received as raw HTML)
            project.goals = request.form.get('goals')
            project.description_notes = request.form.get('description_notes')
            project.framing_notes = request.form.get('framing_notes')
            project.processing_notes = request.form.get('processing_notes')

            # Finalize image file update
            if new_filename:
                project.final_image_file = new_filename

            # If the primary target is changed, check if the linked object has notes
            if project.target_object_name:
                target_obj_in_config = db.query(AstroObject).filter_by(
                    user_id=g.db_user.id, object_name=project.target_object_name
                ).one_or_none()
                if target_obj_in_config:
                    # Update active_project status based on this primary project
                    # Set active if status is "In Progress", otherwise set inactive
                    target_obj_in_config.active_project = (project.status == "In Progress")

            db.commit()
            flash("Project updated successfully.", "success")

            # --- Redirect Logic (Updated) ---
            # Check if we should return to a specific page (like the Journal tab)
            next_url = request.args.get('next')
            if next_url:
                return redirect(next_url)

            return redirect(url_for('project_detail', project_id=project_id))

        # --- Handle GET Request (Prepare Template Variables) ---

        # Helper to sanitize rich text for passing to the editor/view
        def _sanitize_for_display(html_content):
            if not html_content: return ""
            SAFE_TAGS = ['p', 'strong', 'em', 'ul', 'ol', 'li', 'br', 'div', 'img', 'a', 'figure', 'figcaption']
            SAFE_ATTRS = {'img': ['src', 'alt', 'width', 'height', 'style'], 'a': ['href'], '*': ['style']}
            SAFE_CSS = ['text-align', 'width', 'height', 'max-width', 'float', 'margin', 'margin-left', 'margin-right']
            css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS)
            return bleach.clean(html_content, tags=SAFE_TAGS, attributes=SAFE_ATTRS, css_sanitizer=css_sanitizer)

        return render_template(
            'project_detail.html',
            project=project,
            sessions=sessions,
            total_integration_str=total_integration_str,
            username=username,
            # Pass sanitized HTML for display/editor
            goals_html=_sanitize_for_display(project.goals),
            description_notes_html=_sanitize_for_display(project.description_notes),
            framing_notes_html=_sanitize_for_display(project.framing_notes),
            processing_notes_html=_sanitize_for_display(project.processing_notes),
            # Pass all available AstroObjects for the target selector dropdown
            all_objects=db.query(AstroObject).filter_by(user_id=g.db_user.id).order_by(AstroObject.object_name).all(),
            project_statuses=["In Progress", "Completed", "On Hold", "Abandoned"]
        )
    except Exception as e:
        db.rollback()
        flash(f"An error occurred: {e}", "error")
        print(f"Error in project_detail route: {e}")
        traceback.print_exc()
        return redirect(url_for('index'))
    finally:
        db.close()

@app.route('/get_outlook_data')
def get_outlook_data():
    load_full_astro_context()
    # --- Check for guest user first ---
    if hasattr(g, 'is_guest') and g.is_guest:
        return jsonify({"status": "complete", "results": []})

    # --- Determine user ID and username ---
    if SINGLE_USER_MODE:
        user_id = g.db_user.id
        username = g.db_user.username
    elif current_user.is_authenticated:
        user_id = g.db_user.id
        username = g.db_user.username
    else:
        return jsonify({"status": "error", "message": "User not authenticated"}), 401

    # --- Determine Location to Use ---
    requested_location_name = request.args.get('location')
    location_name_to_use = g.selected_location
    if requested_location_name and requested_location_name in g.locations:
        location_name_to_use = requested_location_name
    if not location_name_to_use:
        return jsonify({"status": "error", "message": "No valid location selected or configured."}), 400
    location_name = location_name_to_use

    # --- START OF CHANGES ---
    # 1. Get the new anonymous log ID string
    user_log_key = get_user_log_string(user_id, username) # e.g., "(123 | FirstName L.)"

    # 2. Construct cache filename and status key using the log ID
    # We must sanitize the key for filenames
    safe_log_key = user_log_key.replace(" | ", "_").replace(".", "").replace(" ", "_") # e.g., "123_FirstName_L"
    cache_filename = os.path.join(CACHE_DIR, f"outlook_cache_{safe_log_key}_{location_name.lower().replace(' ', '_')}.json")
    status_key = f"({user_log_key})_{location_name}" # e.g., "(123 | FirstName L.)_Home"
    # --- END OF CHANGES ---

    worker_status = cache_worker_status.get(status_key, "idle")
    if worker_status in ["running", "starting"]:
        print(f"[OUTLOOK] Worker for {status_key} is '{worker_status}'. Telling client to wait.")
        return jsonify({"status": worker_status, "results": []})

    if os.path.exists(cache_filename):
        try:
            cache_mtime = os.path.getmtime(cache_filename)
            is_stale = (datetime.now().timestamp() - cache_mtime) > 86400 # 1 day

            if not is_stale:
                with open(cache_filename, 'r') as f:
                    data = json.load(f)

                # Check if cache is from older version (missing has_framing)
                # We look at the first opportunity to see if it has the key
                opportunities = data.get("opportunities", [])
                if opportunities and 'has_framing' not in opportunities[0]:
                    print(f"[OUTLOOK] Cache for {status_key} is missing 'has_framing'. Forcing update.")
                    # Fall through to trigger new worker
                else:
                    return jsonify({"status": "complete", "results": opportunities})
            else:
                print(f"[OUTLOOK] Cache for {status_key} is stale. Will start new worker.")
        except (json.JSONDecodeError, IOError, OSError) as e:
            print(f"❌ ERROR: Could not read/parse outlook cache '{cache_filename}': {e}")

    print(f"[OUTLOOK] Triggering new worker for {status_key} (current status: {worker_status}).")
    try:
        if not hasattr(g, 'user_config') or not g.user_config:
             return jsonify({"status": "error", "message": "User configuration not loaded."}), 500

        sampling_interval = 15 # Default
        if SINGLE_USER_MODE:
            sampling_interval = g.user_config.get('sampling_interval_minutes', 15)
        else:
            sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))

        # --- START OF CHANGE (when starting the thread) ---
        # We pass the user_id (for metadata), the status_key (for logging), and the cache_filename
        thread = threading.Thread(target=update_outlook_cache,
                                  args=(user_id, status_key, cache_filename, location_name, g.user_config.copy(), sampling_interval))
        # --- END OF CHANGE ---
        thread.start()
        cache_worker_status[status_key] = "starting"
        return jsonify({"status": "starting", "results": []})

    except Exception as e:
        print(f"❌ ERROR: Failed to start outlook worker thread for {status_key}: {e}")
        traceback.print_exc()
        cache_worker_status[status_key] = "error" # Mark as error if thread start fails
        return jsonify({"status": "error", "message": "Failed to start background worker."}), 500

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

        # --- ADD THIS LINE ---
        new_comp.is_shared = request.form.get("is_shared") == "on"
        # --- END OF ADDITION ---

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

        # --- ADD THIS LOGIC ---
        # Only allow updating 'is_shared' if it's not an imported item
        if not comp.original_user_id:
            comp.is_shared = request.form.get("is_shared") == "on"
        # --- END OF ADDITION ---

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

        # --- NEW LOGIC START ---
        # 1. Fetch the component objects needed for calculation
        tel_obj = db.get(Component, tel_id)
        cam_obj = db.get(Component, cam_id)
        red_obj = db.get(Component, red_id) if red_id else None

        # 2. Calculate the derived properties (EFL, f-ratio, scale, FOV)
        efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)
        fov_h = None
        if cam_obj and cam_obj.sensor_height_mm and efl:
            try:
                # Calculate FOV height using the derived effective focal length
                fov_h = (degrees(2 * atan((cam_obj.sensor_height_mm / 2.0) / efl)) * 60.0)
            except:
                pass

        if rig_id:  # Update
            rig = db.get(Rig, int(rig_id))
            rig.rig_name = form.get('rig_name')
            rig.telescope_id, rig.camera_id, rig.reducer_extender_id = tel_id, cam_id, red_id
            flash(f"Rig '{rig.rig_name}' updated successfully.", "success")
        else:  # Add
            new_rig = Rig(
                user_id=user.id, rig_name=form.get('rig_name'),
                telescope_id=tel_id, camera_id=cam_id, reducer_extender_id=red_id
            )
            db.add(new_rig)
            rig = new_rig  # Reference the new object for update below
            flash(f"Rig '{new_rig.rig_name}' created successfully.", "success")

        # 3. Persist calculated values to the Rig object (for both ADD and UPDATE)
        rig.effective_focal_length = efl
        rig.f_ratio = f_ratio
        rig.image_scale = scale
        rig.fov_w_arcmin = fov_w
        # NOTE: fov_w_arcmin is used for width, but we should store the height too for a complete record
        # Although the Rig model currently only has fov_w_arcmin, we'll ensure we persist the calculated values.
        # Since the model is missing fov_h_arcmin, we'll only persist the existing fields:
        # We need to verify if fov_h_arcmin was added in a previous step, if not, we stick to fov_w_arcmin.
        # The provided JournalSession model (line 343) shows rig_fov_h_snapshot, so we assume the Rig model
        # was intended to have it. However, since it is not visible, we only update the ones we know exist:

        # --- NEW LOGIC END ---

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
        prefs = db.query(UiPref).filter_by(user_id=user.id).first()
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
            "telescopes": [
                {
                    "id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm,
                    "is_shared": c.is_shared, "original_user_id": c.original_user_id  # <-- ADDED
                } for c in telescopes
            ],
            "cameras": [
                {
                    "id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                    "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um,
                    "is_shared": c.is_shared, "original_user_id": c.original_user_id  # <-- ADDED
                } for c in cameras
            ],
            "reducers_extenders": [
                {
                    "id": c.id, "name": c.name, "factor": c.factor,
                    "is_shared": c.is_shared, "original_user_id": c.original_user_id  # <-- ADDED
                } for c in reducers
            ]
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
    load_full_astro_context()
    db = get_db()
    try:
        # 1. Use the pre-loaded g.db_user (from the consolidated before_request)
        if not g.db_user:
            flash("User session error, please log in again.", "error")
            return redirect(url_for('login'))

        user_id = g.db_user.id

        # 2. Query only for the sessions for this user
        sessions = db.query(JournalSession).filter_by(user_id=user_id).order_by(JournalSession.date_utc.desc()).all()

        # 3. Use the pre-loaded g.alternative_names map for common names
        #    This avoids a second DB query for AstroObject.
        #    The map is { "m31": "Andromeda Galaxy", ... }
        object_names_lookup = g.alternative_names or {}

        # 4. Add the common name to each session object for the template
        for s in sessions:
            # Safely look up the common name using the lowercase object name
            s.target_common_name = object_names_lookup.get(
                s.object_name.lower() if s.object_name else '',
                s.object_name  # Fallback to the object_name itself if not found
            )

        return render_template('journal_list.html', journal_sessions=sessions)
    finally:
        db.close()
@app.route('/journal/add', methods=['GET', 'POST'])
@login_required
def journal_add():
    load_full_astro_context()
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()

        if request.method == 'POST':
            # --- START FIX: Validate the date ---
            session_date_str = request.form.get("session_date")
            try:
                parsed_date_utc = datetime.strptime(session_date_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                # This is the new error handling: flash and redirect
                flash("Invalid date format.", "error")
                target_object_id = request.form.get("target_object_id")

                # Redirect back to the object's page where the form was
                if target_object_id:
                    return redirect(url_for('graph_dashboard', object_name=target_object_id))
                else:
                    # Fallback if no object was specified
                    return redirect(url_for('journal_list_view'))
            # --- END FIX ---

            # --- Handle Project Creation/Selection (This logic is still valid) ---
            project_id_for_session = None
            project_selection = request.form.get("project_selection")
            new_project_name = request.form.get("new_project_name", "").strip()

            if project_selection == "new_project" and new_project_name:
                new_project = Project(id=uuid.uuid4().hex, user_id=user.id, name=new_project_name)
                db.add(new_project)
                db.flush()
                project_id_for_session = new_project.id
                target_object_id = request.form.get("target_object_id", "").strip()
                if target_object_id:
                    new_project.target_object_name = target_object_id
            elif project_selection and project_selection not in ["standalone", "new_project"]:
                project_id_for_session = project_selection

            # --- NEW: Get Rig Snapshot Specs and Component Names ---
            rig_id_str = request.form.get("rig_id_snapshot")
            rig_id_snap, rig_name_snap, efl_snap, fr_snap, scale_snap, fov_w_snap, fov_h_snap = None, None, None, None, None, None, None
            tel_name_snap, reducer_name_snap, camera_name_snap = None, None, None

            if rig_id_str:
                try:
                    rig_id = int(rig_id_str)
                    # Use selectinload to ensure components are fetched efficiently
                    rig = db.query(Rig).options(
                        selectinload(Rig.telescope), selectinload(Rig.camera), selectinload(Rig.reducer_extender)
                    ).filter_by(id=rig_id, user_id=user.id).one_or_none()

                    if rig:
                        rig_id_snap = rig.id # <-- SAVE THE ID
                        rig_name_snap = rig.rig_name
                        efl_snap, fr_snap, scale_snap, fov_w_snap = _compute_rig_metrics_from_components(
                            rig.telescope, rig.camera, rig.reducer_extender
                        )
                        if rig.camera and rig.camera.sensor_height_mm and efl_snap:
                            fov_h_snap = (degrees(2 * atan((rig.camera.sensor_height_mm / 2.0) / efl_snap)) * 60.0)

                        # --- NEW: Save Component Names ---
                        tel_name_snap = rig.telescope.name if rig.telescope else None
                        reducer_name_snap = rig.reducer_extender.name if rig.reducer_extender else None
                        camera_name_snap = rig.camera.name if rig.camera else None
                        # --- END NEW ---
                except (ValueError, TypeError):
                    pass # rig_id_str was invalid (e.g., "")

            # --- Create New Session Object (This logic is still valid) ---
            new_session = JournalSession(
                user_id=user.id,
                project_id=project_id_for_session,
                date_utc=parsed_date_utc,  # <-- Use the validated date
                object_name=request.form.get("target_object_id", "").strip(),
                notes=request.form.get("general_notes_problems_learnings"),
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
                filter_L_subs=safe_int(request.form.get("filter_L_subs")),
                filter_L_exposure_sec=safe_int(request.form.get("filter_L_exposure_sec")),
                filter_R_subs=safe_int(request.form.get("filter_R_subs")),
                filter_R_exposure_sec=safe_int(request.form.get("filter_R_exposure_sec")),
                filter_G_subs=safe_int(request.form.get("filter_G_subs")),
                filter_G_exposure_sec=safe_int(request.form.get("filter_G_exposure_sec")),
                filter_B_subs=safe_int(request.form.get("filter_B_subs")),
                filter_B_exposure_sec=safe_int(request.form.get("filter_B_exposure_sec")),
                filter_Ha_subs=safe_int(request.form.get("filter_Ha_subs")),
                filter_Ha_exposure_sec=safe_int(request.form.get("filter_Ha_exposure_sec")),
                filter_OIII_subs=safe_int(request.form.get("filter_OIII_subs")),
                filter_OIII_exposure_sec=safe_int(request.form.get("filter_OIII_exposure_sec")),
                filter_SII_subs=safe_int(request.form.get("filter_SII_subs")),
                filter_SII_exposure_sec=safe_int(request.form.get("filter_SII_exposure_sec")),
                external_id=generate_session_id(),
                weather_notes=request.form.get("weather_notes", "").strip() or None,
                guiding_equipment=request.form.get("guiding_equipment", "").strip() or None,
                dither_details=request.form.get("dither_details", "").strip() or None,
                acquisition_software=request.form.get("acquisition_software", "").strip() or None,
                camera_temp_setpoint_c=safe_float(request.form.get("camera_temp_setpoint_c")),
                camera_temp_actual_avg_c=safe_float(request.form.get("camera_temp_actual_avg_c")),
                binning_session=request.form.get("binning_session", "").strip() or None,
                darks_strategy=request.form.get("darks_strategy", "").strip() or None,
                flats_strategy=request.form.get("flats_strategy", "").strip() or None,
                bias_darkflats_strategy=request.form.get("bias_darkflats_strategy", "").strip() or None,
                transparency_observed_scale=request.form.get("transparency_observed_scale", "").strip() or None,

                # --- Rig Snapshot Fields ---
                rig_id_snapshot=rig_id_snap,
                rig_name_snapshot=rig_name_snap,
                rig_efl_snapshot=efl_snap,
                rig_fr_snapshot=fr_snap,
                rig_scale_snapshot=scale_snap,
                rig_fov_w_snapshot=fov_w_snap,
                rig_fov_h_snapshot=fov_h_snap,

                # --- NEW COMPONENT NAME SNAPSHOTS ---
                telescope_name_snapshot=tel_name_snap,
                reducer_name_snapshot=reducer_name_snap,
                camera_name_snapshot=camera_name_snap
            )

            # ... (rest of session creation logic, file upload, etc.) ...
            total_seconds = (new_session.number_of_subs_light or 0) * (new_session.exposure_time_per_sub_sec or 0) + \
                            (new_session.filter_L_subs or 0) * (new_session.filter_L_exposure_sec or 0) + \
                            (new_session.filter_R_subs or 0) * (new_session.filter_R_exposure_sec or 0) + \
                            (new_session.filter_G_subs or 0) * (new_session.filter_G_exposure_sec or 0) + \
                            (new_session.filter_B_subs or 0) * (new_session.filter_B_exposure_sec or 0) + \
                            (new_session.filter_Ha_subs or 0) * (new_session.filter_Ha_exposure_sec or 0) + \
                            (new_session.filter_OIII_subs or 0) * (new_session.filter_OIII_exposure_sec or 0) + \
                            (new_session.filter_SII_subs or 0) * (new_session.filter_SII_exposure_sec or 0)
            new_session.calculated_integration_time_minutes = round(total_seconds / 60.0,
                                                                    1) if total_seconds > 0 else None

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

        # --- GET Request Logic ---
        target_object = request.args.get('target')
        if target_object:
            # If a target is specified, go to that object's dashboard
            return redirect(url_for('graph_dashboard', object_name=target_object))
        else:
            # If no target, go to the main journal list
            flash("To add a new session, please select an object first.", "info")
            return redirect(url_for('journal_list_view'))
    finally:
        db.close()


@app.route('/journal/edit/<int:session_id>', methods=['GET', 'POST'])
@login_required
def journal_edit(session_id):
    load_full_astro_context()
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        session_to_edit = db.query(JournalSession).filter_by(id=session_id, user_id=user.id).one_or_none()

        if not session_to_edit:
            flash("Journal entry not found or you do not have permission to edit it.", "error")
            return redirect(url_for('index'))

        if request.method == 'POST':
            # --- START FIX: Validate the date ---
            session_date_str = request.form.get("session_date")
            try:
                parsed_date_utc = datetime.strptime(session_date_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                flash("Invalid date format.", "error")

                # Redirect back to the graph view for this session
                return redirect(url_for('graph_dashboard',
                                        object_name=session_to_edit.object_name,
                                        session_id=session_id))
            # --- END FIX ---

            # --- NEW: Get Rig Snapshot Specs and Component Names ---
            rig_id_str = request.form.get("rig_id_snapshot")
            rig_id_snap, rig_name_snap, efl_snap, fr_snap, scale_snap, fov_w_snap, fov_h_snap = None, None, None, None, None, None, None
            tel_name_snap, reducer_name_snap, camera_name_snap = None, None, None  # Initialize new snapshots

            if rig_id_str:
                try:
                    rig_id = int(rig_id_str)
                    rig = db.query(Rig).options(
                        selectinload(Rig.telescope), selectinload(Rig.camera), selectinload(Rig.reducer_extender)
                    ).filter_by(id=rig_id, user_id=user.id).one_or_none()

                    if rig:
                        rig_id_snap = rig.id  # <-- SAVE THE ID
                        rig_name_snap = rig.rig_name
                        efl_snap, fr_snap, scale_snap, fov_w_snap = _compute_rig_metrics_from_components(
                            rig.telescope, rig.camera, rig.reducer_extender
                        )
                        if rig.camera and rig.camera.sensor_height_mm and efl_snap:
                            fov_h_snap = (degrees(2 * atan((rig.camera.sensor_height_mm / 2.0) / efl_snap)) * 60.0)

                        # --- GET AND SAVE COMPONENT NAMES ---
                        tel_name_snap = rig.telescope.name if rig.telescope else None
                        reducer_name_snap = rig.reducer_extender.name if rig.reducer_extender else None
                        camera_name_snap = rig.camera.name if rig.camera else None
                        # --- END COMPONENT NAMES ---
                except (ValueError, TypeError):
                    pass

            # --- Update ALL fields from the form ---
            session_to_edit.date_utc = parsed_date_utc
            session_to_edit.object_name = request.form.get("target_object_id", "").strip()
            session_to_edit.notes = request.form.get("general_notes_problems_learnings")

            form = request.form
            session_to_edit.seeing_observed_fwhm = safe_float(form.get("seeing_observed_fwhm"))
            session_to_edit.sky_sqm_observed = safe_float(form.get("sky_sqm_observed"))
            session_to_edit.moon_illumination_session = safe_int(form.get("moon_illumination_session"))
            session_to_edit.moon_angular_separation_session = safe_float(form.get("moon_angular_separation_session"))
            session_to_edit.telescope_setup_notes = form.get("telescope_setup_notes", "").strip()
            session_to_edit.guiding_rms_avg_arcsec = safe_float(form.get("guiding_rms_avg_arcsec"))
            session_to_edit.exposure_time_per_sub_sec = safe_int(form.get("exposure_time_per_sub_sec"))
            session_to_edit.number_of_subs_light = safe_int(form.get("number_of_subs_light"))
            session_to_edit.filter_used_session = form.get("filter_used_session", "").strip()
            session_to_edit.gain_setting = safe_int(form.get("gain_setting"))
            session_to_edit.offset_setting = safe_int(form.get("offset_setting"))
            session_to_edit.session_rating_subjective = safe_int(form.get("session_rating_subjective"))
            session_to_edit.filter_L_subs = safe_int(form.get("filter_L_subs"))
            session_to_edit.filter_L_exposure_sec = safe_int(form.get("filter_L_exposure_sec"))
            session_to_edit.filter_R_subs = safe_int(form.get("filter_R_subs"))
            session_to_edit.filter_R_exposure_sec = safe_int(form.get("filter_R_exposure_sec"))
            session_to_edit.filter_G_subs = safe_int(form.get("filter_G_subs"))
            session_to_edit.filter_G_exposure_sec = safe_int(form.get("filter_G_exposure_sec"))
            session_to_edit.filter_B_subs = safe_int(form.get("filter_B_subs"))
            session_to_edit.filter_B_exposure_sec = safe_int(form.get("filter_B_exposure_sec"))
            session_to_edit.filter_Ha_subs = safe_int(form.get("filter_Ha_subs"))
            session_to_edit.filter_Ha_exposure_sec = safe_int(form.get("filter_Ha_exposure_sec"))
            session_to_edit.filter_OIII_subs = safe_int(form.get("filter_OIII_subs"))
            session_to_edit.filter_OIII_exposure_sec = safe_int(form.get("filter_OIII_exposure_sec"))
            session_to_edit.filter_SII_subs = safe_int(form.get("filter_SII_subs"))
            session_to_edit.filter_SII_exposure_sec = safe_int(form.get("filter_SII_exposure_sec"))
            session_to_edit.weather_notes = form.get("weather_notes", "").strip() or None
            session_to_edit.guiding_equipment = form.get("guiding_equipment", "").strip() or None
            session_to_edit.dither_details = form.get("dither_details", "").strip() or None
            session_to_edit.acquisition_software = form.get("acquisition_software", "").strip() or None
            session_to_edit.camera_temp_setpoint_c = safe_float(form.get("camera_temp_setpoint_c"))
            session_to_edit.camera_temp_actual_avg_c = safe_float(form.get("camera_temp_actual_avg_c"))
            session_to_edit.binning_session = form.get("binning_session", "").strip() or None
            session_to_edit.darks_strategy = form.get("darks_strategy", "").strip() or None
            session_to_edit.flats_strategy = form.get("flats_strategy", "").strip() or None
            session_to_edit.bias_darkflats_strategy = form.get("bias_darkflats_strategy", "").strip() or None
            session_to_edit.transparency_observed_scale = form.get("transparency_observed_scale", "").strip() or None

            # --- START: Update Rig Snapshot Fields (Crucial Assignment) ---
            session_to_edit.rig_id_snapshot = rig_id_snap
            session_to_edit.rig_name_snapshot = rig_name_snap
            session_to_edit.rig_efl_snapshot = efl_snap
            session_to_edit.rig_fr_snapshot = fr_snap
            session_to_edit.rig_scale_snapshot = scale_snap
            session_to_edit.rig_fov_w_snapshot = fov_w_snap
            session_to_edit.rig_fov_h_snapshot = fov_h_snap

            # --- ASSIGN NEW COMPONENT NAME SNAPSHOTS ---
            session_to_edit.telescope_name_snapshot = tel_name_snap
            session_to_edit.reducer_name_snapshot = reducer_name_snap
            session_to_edit.camera_name_snapshot = camera_name_snap
            # --- END: Update Rig Snapshot Fields ---

            # Project logic
            project_id_for_session = None
            project_selection = request.form.get("project_selection")
            new_project_name = request.form.get("new_project_name", "").strip()

            # The object being viewed/edited (use this consistently)
            target_object_id = session_to_edit.object_name

            if project_selection == "new_project" and new_project_name:
                new_project = Project(id=uuid.uuid4().hex, user_id=user.id, name=new_project_name)
                db.add(new_project)
                db.flush()
                project_id_for_session = new_project.id

                # Link the NEW project to the object
                if target_object_id:
                    new_project.target_object_name = target_object_id

            elif project_selection and project_selection not in ["standalone", "new_project"]:
                project_id_for_session = project_selection

                # Link the EXISTING project to the object
                project_to_link = db.query(Project).filter_by(id=project_id_for_session, user_id=user.id).one_or_none()
                if project_to_link and target_object_id:
                    project_to_link.target_object_name = target_object_id

            session_to_edit.project_id = project_id_for_session

            # Integration time calculation
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
            # File Handling (Delete Image)
            if request.form.get('delete_session_image') == '1':
                old_image = session_to_edit.session_image_file
                if old_image:
                    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
                    old_image_path = os.path.join(user_upload_dir, old_image)
                    if os.path.exists(old_image_path):
                        try:
                            os.remove(old_image_path)
                        except Exception as e:
                            print(f"Error deleting file: {e}")
                    session_to_edit.session_image_file = None

            # File Handling (New Image Upload)
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
                url_for('graph_dashboard', object_name=session_to_edit.object_name, session_id=session_id))

        # --- GET Request Logic ---
        if not session_to_edit.object_name:
            flash("Cannot edit session: associated object name is missing.", "error")
            return redirect(url_for('journal_list_view'))

        return redirect(url_for('graph_dashboard',
                                object_name=session_to_edit.object_name,
                                session_id=session_id))
    finally:
        db.close()


@app.route('/journal/add_project', methods=['POST'])
@login_required
def add_project_from_journal():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    # Capture location to persist view state on redirect
    current_location = request.form.get('current_location')

    try:
        user = db.query(DbUser).filter_by(username=username).one()

        name = request.form.get('name')
        target_object_id = request.form.get('target_object_id')
        status = request.form.get('status', 'In Progress')
        goals = request.form.get('goals')

        if not name:
            flash("Project name is required.", "error")
            return redirect(
                url_for('graph_dashboard', object_name=target_object_id, tab='journal', location=current_location))

        existing = db.query(Project).filter_by(user_id=user.id, name=name).first()
        if existing:
            flash(f"A project named '{name}' already exists.", "error")
            return redirect(
                url_for('graph_dashboard', object_name=target_object_id, tab='journal', location=current_location))

        new_project = Project(
            id=uuid.uuid4().hex,
            user_id=user.id,
            name=name,
            target_object_name=target_object_id,
            status=status,
            goals=goals
        )
        db.add(new_project)

        # Auto-activate object if project is In Progress
        should_trigger_outlook = False
        if target_object_id and status == 'In Progress':
            obj = db.query(AstroObject).filter_by(user_id=user.id, object_name=target_object_id).first()
            if obj and not obj.active_project:
                obj.active_project = True
                should_trigger_outlook = True

        db.commit()

        if should_trigger_outlook:
            trigger_outlook_update_for_user(username)

        flash(f"Project '{name}' created successfully.", "success")

        # Build redirect args explicitly to ensure clean URL construction
        redirect_args = {
            'object_name': target_object_id,
            'tab': 'journal',
            'project_id': new_project.id
        }
        if current_location:
            redirect_args['location'] = current_location

        return redirect(url_for('graph_dashboard', **redirect_args))

    except Exception as e:
        db.rollback()
        print(f"Error creating project in journal: {e}")  # Log error for debugging
        flash(f"Error creating project: {e}", "error")
        return redirect(url_for('graph_dashboard', object_name=request.form.get('target_object_id'), tab='journal',
                                location=current_location))
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
                return redirect(url_for('index'))
        else:
            flash("Journal entry not found or you do not have permission to delete it.", "error")
            return redirect(url_for('index'))
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


@app.route('/journal/report_page/<int:session_id>')
@login_required
def show_journal_report_page(session_id):
    """
    Renders the HTML version of the report page.
    """
    db = get_db()
    try:
        # --- 1. Get Session Data ---
        session = db.query(JournalSession).filter_by(id=session_id, user_id=g.db_user.id).one_or_none()
        if not session:
            flash("Session not found.", "error")
            return redirect(url_for('index'))

        session_dict = {c.name: getattr(session, c.name) for c in session.__table__.columns}

        project = None  # <--- ADD THIS LINE
        project_name = "Standalone Session"

        if session.project_id:
            project = db.query(Project).filter_by(id=session.project_id).one_or_none()
            if project:
                project_name = project.name

        # --- 2. Get Related Data (FIXED: Fetch from DB) ---
        # Try to find the object in the user's database first
        obj_record = db.query(AstroObject).filter_by(user_id=g.db_user.id,
                                                     object_name=session.object_name).one_or_none()

        if obj_record:
            # Use the database record (contains your custom Common Name, Type, etc.)
            object_details = {
                'Object': obj_record.object_name,
                'Common Name': obj_record.common_name or obj_record.object_name,
                'Type': obj_record.type or 'Deep Sky Object',
                'Constellation': obj_record.constellation or 'N/A'
            }
        else:
            # Fallback if object not in DB (e.g. deleted)
            object_details = get_ra_dec(session.object_name) or {'Common Name': session.object_name,
                                                                 'Object': session.object_name}

        project_name = "Standalone Session"
        if session.project_id:
            project = db.query(Project).filter_by(id=session.project_id).one_or_none()
            if project:
                project_name = project.name

        # --- 3. Prepare Template Variables ---
        rating = session_dict.get('session_rating_subjective') or 0
        rating_stars = "★" * rating + "☆" * (5 - rating)

        integ_min = session_dict.get('calculated_integration_time_minutes') or 0
        integ_str = f"{integ_min // 60}h {integ_min % 60:.0f}m" if integ_min > 0 else "N/A"

        image_url = None
        image_source_label = "Session Image"  # Default label

        username = "default" if SINGLE_USER_MODE else current_user.username

        # 1. Try Session Image
        if session_dict.get('session_image_file'):
            image_url = url_for('get_uploaded_image', username=username, filename=session_dict['session_image_file'],
                                _external=True)
            image_source_label = "Session Result / Preview"

        # 2. Fallback to Project Image (if session image is missing)
        elif project and project.final_image_file:
            image_url = url_for('get_uploaded_image', username=username, filename=project.final_image_file,
                                _external=True)
            image_source_label = "Project Context (Final Image)"

        # [DELETED THE DUPLICATE LOGIC BLOCK HERE]

        logo_url = None

        if session_dict.get('session_image_file'):
            username = "default" if SINGLE_USER_MODE else current_user.username
            image_url = url_for('get_uploaded_image', username=username, filename=session_dict['session_image_file'],
                                _external=True)

        logo_url = None
        try:
            logo_path = os.path.join(app.static_folder, 'nova_logo.png')
            if os.path.exists(logo_path):
                logo_url = url_for('static', filename='nova_logo.png', _external=True)
        except Exception:
            pass

            # Sanitize notes
        raw_journal_notes = session_dict.get('notes') or ""
        if not raw_journal_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>")):
            escaped_text = bleach.clean(raw_journal_notes, tags=[], strip=True)
            sanitized_notes = escaped_text.replace("\n", "<br>")
        else:
            SAFE_TAGS = ['p', 'strong', 'em', 'ul', 'ol', 'li', 'br', 'div', 'img', 'a', 'figure', 'figcaption']
            SAFE_ATTRS = {'img': ['src', 'alt', 'width', 'height', 'style'], 'a': ['href'], '*': ['style']}
            SAFE_CSS = ['text-align', 'width', 'height', 'max-width', 'float', 'margin', 'margin-left', 'margin-right']
            css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS)
            sanitized_notes = bleach.clean(raw_journal_notes, tags=SAFE_TAGS, attributes=SAFE_ATTRS,
                                           css_sanitizer=css_sanitizer)
        session_dict['notes'] = sanitized_notes

        return render_template(
            'journal_report.html',
            session=session_dict,
            object_details=object_details,
            project_name=project_name,
            rating_stars=rating_stars,
            integ_str=integ_str,
            image_url=image_url,
            image_source_label=image_source_label,
            logo_url=logo_url,
            today_date=datetime.now().strftime('%d.%m.%Y')
        )

    except Exception as e:
        print(f"Error rendering report page: {e}")
        traceback.print_exc()
        return f"Error generating report: {e}", 500
    finally:
        if db:
            db.close()


@app.route('/journal/add_for_target/<path:object_name>', methods=['GET', 'POST'])
@login_required
def journal_add_for_target(object_name):
    if request.method == 'POST':
        # If the form is submitted, redirect the POST request to the main journal_add function
        # which already contains all the logic to process the form data.
        return redirect(url_for('journal_add'), code=307)

    # For GET requests, the original behavior is maintained.
    return redirect(url_for('journal_add', target=object_name))

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

                # --- START: THIS IS THE CORRECTED LOGIC ---
                # Read 'next' from the form's hidden input, not the URL
                next_page = request.form.get('next')

                # Security check: Only redirect if 'next' is a relative path
                if next_page and next_page.startswith('/'):
                    return redirect(next_page)

                # Default redirect if 'next' is missing or invalid
                return redirect(url_for('index'))
                # --- END OF CORRECTION ---

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
def ensure_telemetry_defaults():
    """
    Ensures telemetry defaults IN MEMORY for the current request,
    without writing back to any files.
    """
    try:
        if hasattr(g, 'user_config') and isinstance(g.user_config, dict):
            # --- START FIX ---
            # Ensure 'telemetry' is a dict, not None
            if g.user_config.get('telemetry') is None:
                g.user_config['telemetry'] = {}
            telemetry_config = g.user_config.setdefault('telemetry', {})
            # --- END FIX ---
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
            cfg = g.user_config if hasattr(g, 'user_config') else {}
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

    # Save to database
    username = "default" if SINGLE_USER_MODE else (
        current_user.username if current_user.is_authenticated else 'guest_user')
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if user:
            # 1. Update the Locations table (Source of Truth for Dashboard Fallback)
            # Reset all locations for this user to is_default=False
            db.query(Location).filter_by(user_id=user.id).update({Location.is_default: False})

            # Set the new location to is_default=True
            db.query(Location).filter_by(user_id=user.id, name=location_name).update(
                {Location.is_default: True})

            # 2. Update UiPref (for consistency/legacy reads)
            prefs = db.query(UiPref).filter_by(user_id=user.id).first()
            if not prefs:
                prefs = UiPref(user_id=user.id, json_blob='{}')
                db.add(prefs)

            try:
                settings = json.loads(prefs.json_blob or '{}')
            except json.JSONDecodeError:
                settings = {}

            settings['default_location'] = location_name
            prefs.json_blob = json.dumps(settings)

            db.commit()
        else:
            # User not found, but we can't save. Log it.
            print(f"[set_location] ERROR: Could not find user '{username}' to save default location.")
    except Exception as e:
        db.rollback()
        print(f"[set_location] ERROR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

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
        prefs = db.query(UiPref).filter_by(user_id=u.id).first()
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
        db_objects = db.query(AstroObject).filter_by(user_id=u.id).order_by(AstroObject.object_name).all()
        config_doc["objects"] = [o.to_dict() for o in db_objects]

        # --- 4. Load Saved Framings (NEW) ---
        saved_framings_db = db.query(SavedFraming).filter_by(user_id=u.id).all()
        saved_framings_list = []
        for sf in saved_framings_db:
            # Resolve rig name for portability (ID is local to DB)
            r_name = None
            if sf.rig_id:
                rig_obj = db.get(Rig, sf.rig_id)
                if rig_obj: r_name = rig_obj.rig_name

            saved_framings_list.append({
                "object_name": sf.object_name,
                "rig_name": r_name,
                "ra": sf.ra,
                "dec": sf.dec,
                "rotation": sf.rotation,
                "survey": sf.survey,
                "blend_survey": sf.blend_survey,
                "blend_opacity": sf.blend_opacity
            })
        config_doc["saved_framings"] = saved_framings_list

        # --- 5. Load Saved Views ---
        db_views = db.query(SavedView).filter_by(user_id=u.id).order_by(SavedView.name).all()
        config_doc["saved_views"] = [
            {
                "name": v.name,
                "description": v.description,
                "is_shared": v.is_shared,
                "settings": json.loads(v.settings_json)
            } for v in db_views
        ]

        # --- 6. Create in-memory file ---
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

        # --- 1. Load Projects (Including new fields) ---
        projects = db.query(Project).filter_by(user_id=u.id).order_by(Project.name).all()
        projects_list = [
            {
                "project_id": p.id,
                "project_name": p.name,
                "target_object_id": p.target_object_name,
                "description_notes": p.description_notes,
                "framing_notes": p.framing_notes,
                "processing_notes": p.processing_notes,
                "final_image_file": p.final_image_file,
                "goals": p.goals,
                "status": p.status,
            } for p in projects
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
                "calculated_integration_time_minutes": s.calculated_integration_time_minutes,
                "rig_id_snapshot": s.rig_id_snapshot,  # <-- ADDED
                "rig_name_snapshot": s.rig_name_snapshot,
                "rig_efl_snapshot": s.rig_efl_snapshot,
                "rig_fr_snapshot": s.rig_fr_snapshot,
                "rig_scale_snapshot": s.rig_scale_snapshot,
                "rig_fov_w_snapshot": s.rig_fov_w_snapshot,
                "rig_fov_h_snapshot": s.rig_fov_h_snapshot
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
                new_journal_data = {"projects": [], "sessions": []}  # Handle empty file

            # Basic validation
            is_valid, message = validate_journal_data(new_journal_data)
            if not is_valid:
                flash(f"Invalid journal file structure: {message}", "error")
                return redirect(url_for('config_form'))

            username = "default" if SINGLE_USER_MODE else current_user.username

            # === START REFACTOR: WIPE & REPLACE ===
            db = get_db()
            try:
                user = _upsert_user(db, username)

                # 1. Wipe existing Journal Data
                # We must delete Sessions first (they depend on Projects), then Projects.
                print(f"[IMPORT_JOURNAL] Wiping existing sessions and projects for user '{username}'...")

                db.query(JournalSession).filter_by(user_id=user.id).delete()
                # Projects are safe to delete after sessions are gone
                db.query(Project).filter_by(user_id=user.id).delete()

                db.flush()  # Ensure deletion happens before insertion

                # 2. Import New Data
                _migrate_journal(db, user, new_journal_data)

                db.commit()
                flash("Journal imported successfully! (Previous journal data was replaced)", "success")
            except Exception as e:
                db.rollback()
                print(f"[IMPORT_JOURNAL] DB Error: {e}")
                # Re-raise to hit the outer exception handler for detailed logging
                raise e
            finally:
                db.close()
            # === END REFACTOR ===

            return redirect(url_for('config_form'))

        except yaml.YAMLError as ye:
            print(f"[IMPORT JOURNAL ERROR] Invalid YAML format: {ye}")
            flash(f"Import failed: Invalid YAML format in the journal file. {ye}", "error")
            return redirect(url_for('config_form'))
        except Exception as e:
            print(f"[IMPORT JOURNAL ERROR] {e}")
            # Clean up the error message for display
            err_msg = str(e)
            if "UNIQUE constraint failed" in err_msg:
                err_msg = "Data conflict detected. Please try again (the wipe logic should prevent this)."
            flash(f"Import failed: {err_msg}", "error")
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

        # === START REFACTOR: FULL WIPE & REPLACE ===
        db = get_db()
        try:
            user = _upsert_user(db, username)

            print(f"[IMPORT_CONFIG] Wiping all existing config data for user '{username}'...")

            # 1. Delete existing locations
            db.query(Location).filter_by(user_id=user.id).delete()

            # 2. Delete existing objects
            db.query(AstroObject).filter_by(user_id=user.id).delete()

            # 3. Delete existing saved views
            db.query(SavedView).filter_by(user_id=user.id).delete()

            # 4. Delete existing saved framings (ADDED THIS)
            db.query(SavedFraming).filter_by(user_id=user.id).delete()

            # 5. Flush deletions
            db.flush()

            # 6. Import New Data
            _migrate_locations(db, user, new_config)
            _migrate_objects(db, user, new_config)
            _migrate_ui_prefs(db, user, new_config)
            _migrate_saved_views(db, user, new_config)

            # 7. Import Saved Framings
            _migrate_saved_framings(db, user, new_config)

            # Capture ID for thread
            user_id_for_thread = user.id

            db.commit()
            flash("Config imported successfully! (Previous config was replaced)", "success")
        except Exception as e:
            db.rollback()
            print(f"[IMPORT_CONFIG] DB Error: {e}")
            raise e
        finally:
            db.close()
        # === END REFACTOR ===

        # Trigger background cache update for any new locations
        user_config_for_thread = new_config.copy()
        for loc_name in user_config_for_thread.get('locations', {}).keys():
            cache_filename = f"outlook_cache_{username}_{loc_name.lower().replace(' ', '_')}.json"
            cache_filepath = os.path.join(CACHE_DIR, cache_filename)
            if not os.path.exists(cache_filepath):
                user_log_key = f"({user_id_for_thread} | {username})"
                safe_log_key = f"{user_id_for_thread}_{username}"
                status_key = f"{user_log_key}_{loc_name}"

                thread = threading.Thread(target=update_outlook_cache,
                                          args=(user_id_for_thread, status_key, cache_filepath, loc_name,
                                                user_config_for_thread,
                                                g.sampling_interval))
                thread.start()

        return redirect(url_for('config_form'))

    except yaml.YAMLError as ye:
        flash(f"Import failed: Invalid YAML. ({ye})", "error")
        return redirect(url_for('config_form'))
    except Exception as e:
        flash(f"Import failed: {str(e)}", "error")
        return redirect(url_for('config_form'))

@app.route('/import_catalog/<pack_id>', methods=['POST'])
@login_required
def import_catalog(pack_id):
    """Import a server-side catalog pack into the current user's object library.

    This is non-destructive: existing objects are never overwritten. If an
    object with the same name already exists, it is skipped (aside from
    catalog_sources bookkeeping handled in the helper).
    """
    # Determine which username to use for DB lookups
    try:
        single_user_mode = bool(globals().get('SINGLE_USER_MODE', True))
    except Exception:
        single_user_mode = True

    if single_user_mode:
        username = "default"
    else:
        if not current_user.is_authenticated:
            flash("Authentication error during catalog import.", "error")
            return redirect(url_for('login'))
        username = current_user.username

    db = get_db()
    try:
        user = _upsert_user(db, username)

        catalog_data, meta = load_catalog_pack(pack_id)
        if not catalog_data or not isinstance(catalog_data, dict):
            flash("Catalog pack not found or invalid.", "error")
            return redirect(url_for('config_form'))

        created, skipped = import_catalog_pack_for_user(db, user, catalog_data, pack_id)
        db.commit()

        pack_name = (meta or {}).get("name") or pack_id
        msg = f"Catalog '{pack_name}' imported: {created} new object(s), {skipped} skipped."
        flash(msg, "success")
    except Exception as e:
        db.rollback()
        print(f"[CATALOG IMPORT] Error importing catalog pack '{pack_id}': {e}")
        flash("Catalog import failed due to an internal error.", "error")
    finally:
        db.close()

    return redirect(url_for('config_form'))

# =============================================================================
# Astronomical Calculations
# =============================================================================

def get_ra_dec(object_name, objects_map=None): # <-- ADD objects_map=None parameter
    """
    Looks up RA/DEC and other details for an object.
    Prioritizes the provided objects_map (if given), then falls back to g.objects_map (in request context),
    then queries SIMBAD.
    """
    obj_key = object_name.lower()

    # --- Use the provided map first, then g, then None ---
    obj_map_to_use = objects_map # Use the one passed in (e.g., from the worker thread)
    if obj_map_to_use is None and has_request_context():
        # Fallback to g ONLY if in a request context and no map was passed
        obj_map_to_use = getattr(g, 'objects_map', None)

    obj_entry = obj_map_to_use.get(obj_key) if obj_map_to_use else None # Use the determined map

    # --- Define defaults ---
    # (Defaults remain the same)
    default_type = "N/A"; default_magnitude = "N/A"; default_size = "N/A"; default_sb = "N/A";
    default_project = "none"; default_constellation = "N/A"; default_active_project = False

    # --- Path 1: Object found in config (using obj_map_to_use) ---
    if obj_entry:
        # (Logic inside Path 1 remains the same, using obj_entry)
        ra_str = obj_entry.get("RA"); dec_str = obj_entry.get("DEC")
        constellation_val = obj_entry.get("Constellation", default_constellation)
        type_val = obj_entry.get("Type", default_type)
        magnitude_val = obj_entry.get("Magnitude", default_magnitude)
        size_val = obj_entry.get("Size", default_size)
        sb_val = obj_entry.get("SB", default_sb)
        project_val = obj_entry.get("Project", default_project)
        common_name_val = obj_entry.get("Name", object_name) # Uses "Name" for config form compatibility
        active_project_val = obj_entry.get("ActiveProject", default_active_project)

        if ra_str is not None and dec_str is not None:
            try:
                ra_hours_float = float(ra_str); dec_degrees_float = float(dec_str)
                if constellation_val in [None, "N/A", ""]:
                    try:
                        coords = SkyCoord(ra=ra_hours_float * u.hourangle, dec=dec_degrees_float * u.deg)
                        constellation_val = get_constellation(coords, short_name=True)
                    except Exception: constellation_val = "N/A"
                return {
                    "Object": object_name, "Constellation": constellation_val, "Common Name": common_name_val,
                    "RA (hours)": ra_hours_float, "DEC (degrees)": dec_degrees_float, "Project": project_val,
                    "Type": type_val or default_type, "Magnitude": magnitude_val or default_magnitude,
                    "Size": size_val or default_size, "SB": sb_val or default_sb, "ActiveProject": active_project_val
                }
            except ValueError:
                return { # Return error but keep other config data
                    "Object": object_name, "Constellation": "N/A", "Common Name": "Error: Invalid RA/DEC in config",
                    "RA (hours)": None, "DEC (degrees)": None, "Project": project_val, "Type": type_val,
                    "Magnitude": magnitude_val, "Size": size_val, "SB": sb_val, "ActiveProject": active_project_val
                }
        # Fall through to SIMBAD if coordinates missing in config

    # --- Path 2: Object not in config OR missing coords -> Query SIMBAD ---
    # (SIMBAD lookup logic remains the same)
    project_to_use = obj_entry.get("Project", default_project) if obj_entry else default_project
    active_project_to_use = obj_entry.get("ActiveProject", default_active_project) if obj_entry else default_active_project
    try:
        custom_simbad = Simbad();
        custom_simbad.ROW_LIMIT = 1;
        custom_simbad.TIMEOUT = 60
        # We explicitly ask for decimal degrees.
        # Even if Simbad renames the column to 'ra', the VALUE will be in degrees.
        custom_simbad.add_votable_fields('main_id', 'ra(d)', 'dec(d)', 'otype')

        result = custom_simbad.query_object(object_name)
        if result is None or len(result) == 0: raise ValueError(f"No results for '{object_name}' in SIMBAD.")

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

            # print(f"[SIMBAD FIX] Converted {ra_float}° RA to {ra_hours_simbad:.4f} hours")
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

        try:
            coords = SkyCoord(ra=ra_hours_simbad * u.hourangle, dec=dec_degrees_simbad * u.deg)
            constellation_simbad = get_constellation(coords, short_name=True)
        except Exception: constellation_simbad = "N/A"
        return {
            "Object": object_name, "Constellation": constellation_simbad, "Common Name": simbad_main_id,
            "RA (hours)": ra_hours_simbad, "DEC (degrees)": dec_degrees_simbad, "Project": project_to_use,
            "Type": str(result['OTYPE'][0]) if 'OTYPE' in result.colnames else "N/A",
            "Magnitude": "N/A", "Size": "N/A", "SB": "N/A", "ActiveProject": active_project_to_use
        }
    except Exception as ex:
        return { # Return error structure
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


# This helper function (which I sent before) is still needed.
def _parse_float_from_request(value, field_name="field"):
    """Helper to convert request values to float, raising a clear ValueError."""
    if value is None:
        raise ValueError(f"{field_name} is required and cannot be empty.")
    try:
        return float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid non-numeric value '{value}' received for {field_name}.")

@app.route('/confirm_object', methods=['POST'])
@login_required
def confirm_object():
    req = request.get_json()
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        app_db_user = db.query(DbUser).filter_by(username=username).one()

        raw_object_name = req.get('object')
        if not raw_object_name or not raw_object_name.strip():
            raise ValueError("Object ID is required and cannot be empty.")

        # --- NEW: Normalize the name ---
        object_name = normalize_object_name(raw_object_name)

        common_name = req.get('name')
        if not common_name or not common_name.strip():
            # If common name is blank, use the raw (pretty) object name as a fallback
            common_name = raw_object_name.strip()

        ra_float = _parse_float_from_request(req.get('ra'), "RA")
        dec_float = _parse_float_from_request(req.get('dec'), "DEC")

        # --- START: Rich Text Logic for Notes ---
        # Get the raw HTML directly from the JS payload
        private_notes_html = req.get('project', '') or ""
        shared_notes_html = req.get('shared_notes', '') or ""
        # --- END: Rich Text Logic ---

        existing = db.query(AstroObject).filter_by(user_id=app_db_user.id, object_name=object_name).one_or_none()

        # Get other fields
        constellation = req.get('constellation')
        obj_type = convert_to_native_python(req.get('type'))
        magnitude = str(convert_to_native_python(req.get('magnitude')) or '')
        size = str(convert_to_native_python(req.get('size')) or '')
        sb = str(convert_to_native_python(req.get('sb')) or '')
        is_shared = req.get('is_shared') == True
        active_project = req.get('is_active') == True

        if existing:
            existing.common_name = common_name
            existing.ra_hours = ra_float
            existing.dec_deg = dec_float
            existing.project_name = private_notes_html
            existing.constellation = constellation
            existing.type = obj_type
            existing.magnitude = magnitude
            existing.size = size
            existing.sb = sb
            existing.shared_notes = shared_notes_html
            existing.is_shared = is_shared
            existing.active_project = active_project
        else:
            new_obj = AstroObject(
                user_id=app_db_user.id,
                object_name=object_name,
                common_name=common_name,
                ra_hours=ra_float,
                dec_deg=dec_float,
                project_name=private_notes_html,
                constellation=constellation,
                type=obj_type,
                magnitude=magnitude,
                size=size,
                sb=sb,
                shared_notes=shared_notes_html,
                is_shared=is_shared,
                active_project = active_project
            )
            db.add(new_obj)

        db.commit()
        return jsonify({"status": "success"})

    except ValueError as ve:
        db.rollback()
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()


@app.route('/api/update_object', methods=['POST'])
@login_required
def update_object():
    """
    API endpoint to update a single AstroObject from the config form.
    Expects a JSON payload with all object fields.
    """
    db = get_db()
    try:
        data = request.get_json()
        object_name = data.get('object_id')
        username = "default" if SINGLE_USER_MODE else current_user.username

        user = db.query(DbUser).filter_by(username=username).one()
        obj = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if not obj:
            return jsonify({"status": "error", "message": "Object not found"}), 404

        # Update all fields from the payload
        obj.common_name = data.get('name')
        obj.ra_hours = float(data.get('ra'))
        obj.dec_deg = float(data.get('dec'))
        obj.constellation = data.get('constellation')
        obj.type = data.get('type')
        obj.magnitude = data.get('magnitude')
        obj.size = data.get('size')
        obj.sb = data.get('sb')
        obj.active_project = data.get('is_active')
        # Update notes (JS sends the raw HTML from Trix)
        obj.project_name = data.get('project_notes')

        if not SINGLE_USER_MODE:
            # Only update sharing if it's not an imported item
            if not obj.original_user_id:
                obj.is_shared = data.get('is_shared')
                obj.shared_notes = data.get('shared_notes')

        db.commit()
        return jsonify({"status": "success", "message": f"Object '{object_name}' updated."})

    except Exception as e:
        db.rollback()
        print(f"--- ERROR in /api/update_object ---")
        traceback.print_exc()
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
    load_full_astro_context()
    """
    A new, very fast endpoint that just returns the list of object names.
    """
    # g.objects is already loaded by the @app.before_request
    return jsonify({"objects": g.objects})


@app.route('/api/get_object_data/<path:object_name>')
def get_object_data(object_name):
    # --- 1. Determine User (No change needed here) ---
    if SINGLE_USER_MODE:
        username = "default"
    elif current_user.is_authenticated:
        username = current_user.username
    elif request.args.get('location'):  # Allow guest if location provided
        username = "guest_user"
    else:
        # Deny guest if no location specified (cannot determine defaults)
        return jsonify({
            'Object': object_name, 'Common Name': "Error: Authentication required.", 'error': True
        }), 401

    db = get_db()
    try:
        # --- 2. Get User Record (No change needed here) ---
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            return jsonify({
                'Object': object_name, 'Common Name': "Error: User not found.", 'error': True
            }), 404

        # --- 3. Determine Location to Use (Modified: Query DB directly) ---
        requested_location_name = request.args.get('location')
        selected_location = None
        current_location_config = {}

        if requested_location_name:
            # Try to load the specific location requested
            selected_location = db.query(Location).filter_by(user_id=user.id, name=requested_location_name).options(
                selectinload(Location.horizon_points)).one_or_none()
            if not selected_location:
                return jsonify(
                    {'Object': object_name, 'Common Name': "Error: Requested location not found.", 'error': True}), 404
        else:
            # Fallback to the user's default location
            selected_location = db.query(Location).filter_by(user_id=user.id, is_default=True).options(
                selectinload(Location.horizon_points)).one_or_none()
            # If no default, try the first active one
            if not selected_location:
                selected_location = db.query(Location).filter_by(user_id=user.id, active=True).options(
                    selectinload(Location.horizon_points)).order_by(Location.id).first()

        if not selected_location:
            return jsonify({'Object': object_name, 'Common Name': "Error: No valid location configured or selected.",
                            'error': True}), 400

        # Extract details from the selected location object
        lat = selected_location.lat
        lon = selected_location.lon
        tz_name = selected_location.timezone
        selected_location_name = selected_location.name
        # Build the config-like dict for horizon mask etc.
        horizon_mask = [[hp.az_deg, hp.alt_min_deg] for hp in
                        sorted(selected_location.horizon_points, key=lambda p: p.az_deg)]
        current_location_config = {
            "lat": lat, "lon": lon, "timezone": tz_name,
            "altitude_threshold": selected_location.altitude_threshold,
            "horizon_mask": horizon_mask
            # Add other fields if needed by calculations below
        }
        # --- End Location Determination ---

        # --- 4. Query ONLY the specific object ---
        obj_record = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        # Handle case where object isn't found for this user
        if not obj_record:
            # Optionally try SIMBAD as a fallback *here* if desired,
            # or just return not found. Let's return not found for now.
            return jsonify({
                'Object': object_name, 'Common Name': f"Error: Object '{object_name}' not found in your config.",
                'error': True
            }), 404

        # Extract necessary details
        ra = obj_record.ra_hours
        dec = obj_record.dec_deg
        if ra is None or dec is None:
            return jsonify({
                'Object': object_name, 'Common Name': f"Error: RA/DEC missing for '{object_name}'.",
                'error': True
            }), 400

        # --- 5. Perform Calculations (Largely unchanged, but uses specific object/location) ---
        local_tz = pytz.timezone(tz_name)
        current_datetime_local = datetime.now(local_tz)
        today_str = current_datetime_local.strftime('%Y-%m-%d')
        dawn_today_str = calculate_sun_events_cached(today_str, tz_name, lat, lon).get("astronomical_dawn")
        local_date = today_str
        if dawn_today_str:
            try:
                dawn_today_dt = local_tz.localize(
                    datetime.combine(current_datetime_local.date(), datetime.strptime(dawn_today_str, "%H:%M").time()))
                if current_datetime_local < dawn_today_dt:
                    local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        # Load UI Prefs specifically for altitude_threshold and sampling_interval
        prefs_record = db.query(UiPref).filter_by(user_id=user.id).first()
        user_prefs_dict = {}
        if prefs_record and prefs_record.json_blob:
            try:
                user_prefs_dict = json.loads(prefs_record.json_blob)
            except:
                pass
        altitude_threshold = user_prefs_dict.get("altitude_threshold", 20)
        # Use location-specific threshold if available
        if selected_location.altitude_threshold is not None:
            altitude_threshold = selected_location.altitude_threshold

        # Determine sampling interval based on mode
        sampling_interval = 15  # Default
        if SINGLE_USER_MODE:
            sampling_interval = user_prefs_dict.get('sampling_interval_minutes') or 15
        else:
            sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))

        cache_key = f"{object_name.lower()}_{local_date}_{selected_location_name.lower().replace(' ', '_')}"

        # Calculate or retrieve cached nightly data (logic remains similar)
        if cache_key not in nightly_curves_cache:
            times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)
            location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
            altaz_frame = AltAz(obstime=times_utc, location=location)
            altitudes = sky_coord.transform_to(altaz_frame).alt.deg
            azimuths = sky_coord.transform_to(altaz_frame).az.deg
            transit_time = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
            obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
                ra, dec, lat, lon, local_date, tz_name, altitude_threshold, sampling_interval,
                horizon_mask=horizon_mask  # Pass the specific mask
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
                "times_local": times_local, "altitudes": altitudes, "azimuths": azimuths, "transit_time": transit_time,
                "obs_duration_minutes": int(obs_duration.total_seconds() / 60) if obs_duration else 0,
                "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
                "alt_11pm": f"{alt_11pm:.2f}", "az_11pm": f"{az_11pm:.2f}",
                "is_obstructed_at_11pm": is_obstructed_at_11pm
            }

        cached_night_data = nightly_curves_cache[cache_key]

        # Calculate current position and trend (logic remains similar)
        now_utc = datetime.now(pytz.utc)
        time_diffs = [abs((t - now_utc).total_seconds()) for t in cached_night_data["times_local"]]
        current_index = np.argmin(time_diffs)
        current_alt = cached_night_data["altitudes"][current_index]
        current_az = cached_night_data["azimuths"][current_index]
        next_index = min(current_index + 1, len(cached_night_data["altitudes"]) - 1)
        next_alt = cached_night_data["altitudes"][next_index]
        trend = '–'
        if abs(next_alt - current_alt) > 0.01: trend = '↑' if next_alt > current_alt else '↓'

        # Check obstruction now
        is_obstructed_now = False
        if horizon_mask and len(horizon_mask) > 1:
            sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
            required_altitude_now = interpolate_horizon(current_az, sorted_mask, altitude_threshold)
            if current_alt >= altitude_threshold and current_alt < required_altitude_now:
                is_obstructed_now = True

        # Calculate Moon separation (logic remains similar)
        time_obj = Time(datetime.now(pytz.utc))
        location_for_moon = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        moon_coord = get_body('moon', time_obj, location_for_moon)
        obj_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        frame = AltAz(obstime=time_obj, location=location_for_moon)
        angular_sep = obj_coord_sky.transform_to(frame).separation(moon_coord.transform_to(frame)).deg

        is_obstructed_at_11pm = cached_night_data.get('is_obstructed_at_11pm', False)

        # --- START OF NEW CALCULATIONS ---
        # 1. Calculate Best Month from RA
        # (RA 0h -> Oct, RA 2h -> Nov, ... RA 22h -> Sep)
        RA_to_Month_Opposition = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"]
        best_month_idx = int(ra / 2) % 12  # Simple floor(ra/2)
        best_month_str = RA_to_Month_Opposition[best_month_idx]

        # 2. Calculate Max Culmination Altitude from Dec and Lat
        max_culmination_alt = 90.0 - abs(lat - dec)
        # --- END OF NEW CALCULATIONS ---

        # --- 6. Assemble JSON using the single object record ---

        # 1. Get all static data from the model's to_dict() method
        single_object_data = obj_record.to_dict()

        # 2. Add all dynamic (calculated) data to that dictionary
        single_object_data.update({
            'Altitude Current': f"{current_alt:.2f}",
            'Azimuth Current': f"{current_az:.2f}",
            'Trend': trend,
            'Altitude 11PM': cached_night_data['alt_11pm'],
            'Azimuth 11PM': cached_night_data['az_11pm'],
            'Transit Time': cached_night_data['transit_time'],
            'Observable Duration (min)': cached_night_data['obs_duration_minutes'],
            'Max Altitude (°)': cached_night_data['max_altitude'],
            'Angular Separation (°)': round(angular_sep),
            'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
            'is_obstructed_now': is_obstructed_now,
            'is_obstructed_at_11pm': is_obstructed_at_11pm,
            'best_month_ra': best_month_str,
            'max_culmination_alt': max_culmination_alt,
            'error': False
        })

        # 3. Ensure 'Project' key has a fallback for the UI
        single_object_data.setdefault('Project', 'none')

        return jsonify(single_object_data)

    except Exception as e:
        print(f"ERROR in get_object_data for '{object_name}': {e}")
        traceback.print_exc()
        # Return a generic error structure
        return jsonify({
            'Object': object_name, 'Common Name': "Error processing request.", 'error': True
        }), 500
    finally:
        db.close()


@app.route('/api/get_desktop_data_batch')
@login_required
def get_desktop_data_batch():
    """
    Batch processor for the desktop dashboard.
    Calculates data for 50 objects internally to prevent HTTP request flooding.
    """
    load_full_astro_context()

    # 1. Get Pagination Params
    try:
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 50))
    except ValueError:
        offset = 0
        limit = 50

    user = g.db_user
    if not user: return jsonify({"error": "User not found", "results": []}), 404

    # 2. Determine Location
    requested_loc_name = request.args.get('location')
    # Fallback logic: Request Param -> Session (g) -> Default
    if not requested_loc_name:
        requested_loc_name = g.selected_location

    db = get_db()
    try:
        location_obj = db.query(Location).options(
            selectinload(Location.horizon_points)
        ).filter_by(user_id=user.id, name=requested_loc_name).one_or_none()

        if not location_obj: return jsonify({"error": "Location not found", "results": []}), 404

        # 3. Get Object Slice
        objects_query = db.query(AstroObject).filter_by(user_id=user.id).order_by(AstroObject.object_name)
        total_count = objects_query.count()
        batch_objects = objects_query.offset(offset).limit(limit).all()

        # 4. Prepare Calculation Variables
        results = []
        lat, lon, tz_name = location_obj.lat, location_obj.lon, location_obj.timezone
        horizon_mask = [[hp.az_deg, hp.alt_min_deg] for hp in
                        sorted(location_obj.horizon_points, key=lambda p: p.az_deg)]
        altitude_threshold = location_obj.altitude_threshold if location_obj.altitude_threshold is not None else g.user_config.get(
            "altitude_threshold", 20)

        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.utc

        current_datetime_local = datetime.now(local_tz)
        local_date = current_datetime_local.strftime('%Y-%m-%d')

        # Adjust date if "night of" (past midnight logic)
        dawn_today = calculate_sun_events_cached(local_date, tz_name, lat, lon).get("astronomical_dawn")
        if dawn_today:
            try:
                dawn_dt = local_tz.localize(
                    datetime.combine(current_datetime_local.date(), datetime.strptime(dawn_today, "%H:%M").time()))
                if current_datetime_local < dawn_dt:
                    local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
            except:
                pass

        sampling_interval = 15 if SINGLE_USER_MODE else int(os.environ.get('CALCULATION_PRECISION', 15))
        fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)

        # Moon / Ephem Prep
        time_obj_now = Time(datetime.now(pytz.utc))
        loc_earth = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        moon_coord = get_body('moon', time_obj_now, loc_earth)
        frame_now = AltAz(obstime=time_obj_now, location=loc_earth)
        moon_in_frame = moon_coord.transform_to(frame_now)
        location_key = location_obj.name.lower().replace(' ', '_')

        # 5. Process Batch
        for obj in batch_objects:
            try:
                item = obj.to_dict()
                ra, dec = obj.ra_hours, obj.dec_deg

                if ra is None or dec is None:
                    item.update({'error': True, 'Common Name': 'Error: Missing RA/DEC'})
                    results.append(item)
                    continue

                # Calculate / Cache
                cache_key = f"{obj.object_name.lower()}_{local_date}_{location_key}"

                cached = None
                if cache_key in nightly_curves_cache:
                    cached = nightly_curves_cache[cache_key]
                else:
                    obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
                        ra, dec, lat, lon, local_date, tz_name, altitude_threshold, sampling_interval, horizon_mask
                    )
                    transit = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
                    alt_11, az_11 = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)

                    is_obst_11 = False
                    if horizon_mask:
                        req_alt = interpolate_horizon(az_11, sorted(horizon_mask, key=lambda p: p[0]),
                                                      altitude_threshold)
                        if alt_11 >= altitude_threshold and alt_11 < req_alt: is_obst_11 = True

                    times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)
                    sky_c = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    aa_frame = AltAz(obstime=times_utc, location=loc_earth)
                    alts = sky_c.transform_to(aa_frame).alt.deg
                    azs = sky_c.transform_to(aa_frame).az.deg

                    cached = {
                        "times_local": times_local, "altitudes": alts, "azimuths": azs,
                        "transit_time": transit,
                        "obs_duration_minutes": int(obs_duration.total_seconds() / 60) if obs_duration else 0,
                        "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
                        "alt_11pm": f"{alt_11:.2f}", "az_11pm": f"{az_11:.2f}",
                        "is_obstructed_at_11pm": is_obst_11
                    }
                    nightly_curves_cache[cache_key] = cached

                # Current Position (Fast Interpolation)
                now_utc = datetime.now(pytz.utc)
                idx = np.argmin([abs((t - now_utc).total_seconds()) for t in cached["times_local"]])
                cur_alt = cached["altitudes"][idx]
                cur_az = cached["azimuths"][idx]

                next_idx = min(idx + 1, len(cached["altitudes"]) - 1)
                trend = '–'
                if abs(cached["altitudes"][next_idx] - cur_alt) > 0.01:
                    trend = '↑' if cached["altitudes"][next_idx] > cur_alt else '↓'

                is_obst_now = False
                if horizon_mask:
                    req = interpolate_horizon(cur_az, sorted(horizon_mask, key=lambda p: p[0]), altitude_threshold)
                    if cur_alt >= altitude_threshold and cur_alt < req: is_obst_now = True

                sep = "N/A"
                try:
                    sky_c = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    sep = round(sky_c.transform_to(frame_now).separation(moon_in_frame).deg)
                except:
                    pass

                best_m = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"][
                    int(ra / 2) % 12]
                max_culm = 90.0 - abs(lat - dec)

                item.update({
                    'Altitude Current': f"{cur_alt:.2f}",
                    'Azimuth Current': f"{cur_az:.2f}",
                    'Trend': trend,
                    'Altitude 11PM': cached['alt_11pm'],
                    'Azimuth 11PM': cached['az_11pm'],
                    'Transit Time': cached['transit_time'],
                    'Observable Duration (min)': cached['obs_duration_minutes'],
                    'Max Altitude (°)': cached['max_altitude'],
                    'Angular Separation (°)': sep,
                    'is_obstructed_now': is_obst_now,
                    'is_obstructed_at_11pm': cached['is_obstructed_at_11pm'],
                    'best_month_ra': best_m,
                    'max_culmination_alt': max_culm,
                    'error': False
                })
                results.append(item)

            except Exception as e:
                print(f"Batch Error {obj.object_name}: {e}")
                results.append({'Object': obj.object_name, 'Common Name': 'Error: Calc failed', 'error': True})

        response_data = {
            "results": results,
            "total": total_count,
            "offset": offset,
            "limit": limit
        }

        # Ensure no NumPy types exist in the response
        response_data = recursively_clean_numpy_types(response_data)

        return jsonify(response_data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@app.route('/')
def index():
    load_full_astro_context()
    if not (current_user.is_authenticated or SINGLE_USER_MODE or getattr(g, 'is_guest', False)):
        return redirect(url_for('login'))

    username = "default" if SINGLE_USER_MODE else current_user.username if current_user.is_authenticated else "guest_user"
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            # Handle case where user is authenticated but not yet in app.db
            return render_template('index.html', journal_sessions=[])

        sessions = db.query(JournalSession).filter_by(user_id=user.id).order_by(JournalSession.date_utc.desc()).all()
        all_projects = db.query(Project).filter_by(user_id=user.id).all()
        project_map = {p.id: p.name for p in all_projects}
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

            if session.project_id:
                session_dict['project_name'] = project_map.get(session.project_id, "Unknown Project")
            else:
                session_dict['project_name'] = "-"  # Or "Standalone"

            sessions_for_template.append(session_dict)
        # --- END OF FIX ---

        local_tz = pytz.timezone(g.tz_name or 'UTC')
        now_local = datetime.now(local_tz)

        # --- START FIX: Determine "Observing Night" Date ---
        # If it's before noon, we're still on the "night of" the previous day.
        if now_local.hour < 12:
            observing_date_for_calcs = now_local.date() - timedelta(days=1)
        else:
            observing_date_for_calcs = now_local.date()
        # --- END FIX ---

        return render_template('index.html',
                               journal_sessions=sessions_for_template,  # Pass the new list of dictionaries
                               selected_day=observing_date_for_calcs.day,
                               selected_month=observing_date_for_calcs.month,
                               selected_year=observing_date_for_calcs.year)
    finally:
        db.close()


# =============================================================================
# MOBILE COMPANION ROUTES
# =============================================================================
@app.route('/m/up_now')
@login_required
def mobile_up_now():
    """Renders the mobile 'Up Now' dashboard skeleton (data fetched via API)."""
    load_full_astro_context()
    # Render template immediately with empty data; JS will fetch it.
    return render_template('mobile_up_now.html')

@app.route('/api/mobile_data_chunk')
@login_required
def api_mobile_data_chunk():
    """Fetches a specific slice of object data for the mobile progress bar."""
    load_full_astro_context()

    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 10))

    user = g.db_user
    location_name = g.selected_location
    user_prefs = g.user_config or {}

    results = []
    total_count = 0

    if user and location_name:
        db = get_db()
        try:
            # 1. Get Location
            location_db_obj = db.query(Location).options(
                selectinload(Location.horizon_points)
            ).filter_by(user_id=user.id, name=location_name).one_or_none()

            if location_db_obj:
                # 2. Get All Objects (to count and slice)
                all_objects_query = db.query(AstroObject).filter_by(user_id=user.id).order_by(AstroObject.id)
                total_count = all_objects_query.count()

                # 3. Get Slice
                sliced_objects = all_objects_query.offset(offset).limit(limit).all()

                # 4. Calculate Data for this slice
                results = get_all_mobile_up_now_data(
                    user,
                    location_db_obj,
                    user_prefs,
                    sliced_objects,
                    db  # Pass DB session
                )
        finally:
            db.close()

    return jsonify({
        "data": results,
        "total": total_count,
        "offset": offset,
        "limit": limit
    })

@app.route('/m/location')
@login_required
def mobile_location():
    """Renders the mobile location selector."""
    load_full_astro_context()  # <-- ADD THIS LINE
    return render_template('mobile_location.html',
                           locations=g.active_locations,
                           selected_location_name=g.selected_location)

@app.route('/m')
@app.route('/m/add_object')
@login_required
def mobile_add_object():
    """Renders the mobile 'Add Object' page."""
    load_full_astro_context()  # <-- ADD THIS LINE
    return render_template('mobile_add_object.html')

@app.route('/m/outlook')
@login_required
def mobile_outlook():
    """Renders the mobile 'Outlook' page."""
    load_full_astro_context()  # <-- ADD THIS LINE
    return render_template('mobile_outlook.html')


@app.route('/m/edit_notes/<path:object_name>')
@login_required
def mobile_edit_notes(object_name):
    """Renders the mobile 'Edit Notes' page for a specific object."""
    load_full_astro_context()  # Ensures g.db_user is loaded

    # Get the current user
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            flash("User not found.", "error")
            return redirect(url_for('mobile_up_now'))

        # Get the specific object
        obj_record = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if not obj_record:
            flash(f"Object '{object_name}' not found.", "error")
            return redirect(url_for('mobile_up_now'))

        # Handle Trix/HTML conversion for old plain text notes
        raw_project_notes = obj_record.project_name or ""
        if not raw_project_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>")):
            escaped_text = bleach.clean(raw_project_notes, tags=[], strip=True)
            project_notes_for_editor = escaped_text.replace("\n", "<br>")
        else:
            project_notes_for_editor = raw_project_notes

        return render_template(
            'mobile_edit_notes.html',
            object_name=obj_record.object_name,
            common_name=obj_record.common_name,
            project_notes_html=project_notes_for_editor,
            is_project_active=obj_record.active_project
        )
    finally:
        db.close()

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
    today_str = current_datetime_local.strftime('%Y-%m-%d')
    dawn_today_str = calculate_sun_events_cached(today_str, tz_name, lat, lon).get("astronomical_dawn")
    local_date = today_str
    if dawn_today_str:
        try:
            dawn_today_dt = local_tz.localize(
                datetime.combine(current_datetime_local.date(), datetime.strptime(dawn_today_str, "%H:%M").time()))
            if current_datetime_local < dawn_today_dt:
                local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            pass

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
            cache_key = f"{object_name.lower()}_{local_date}_{location_name_key}"
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
            })
        except Exception as e:
            print(f"[Mobile Helper] Failed to process object {obj_record.object_name}: {e}")
            continue  # Skip this object

    return all_objects_data



@app.route('/sun_events')
def sun_events():
    """
    API endpoint to calculate and return sun event times (dusk, dawn, etc.)
    and the current moon phase for a specific location. Uses the location
    specified in the 'location' query parameter or falls back to the
    user's default location.
    """
    load_full_astro_context()
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
        # === START REFACTOR ===
        # Use the g.user_config, which is already loaded from the DB
        cfg = g.user_config if hasattr(g, "user_config") else {}
        # === END REFACTOR ===
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
        state_dir = Path(os.environ.get('NOVA_STATE_DIR', CACHE_DIR))
        if telemetry_should_send(state_dir):
            send_telemetry_async(cfg, browser_user_agent=ua_final, force=False)
        # else: silently skip; scheduler or next allowed window will send
    except Exception:
        pass

    return jsonify({"status": "ok"}), 200


@app.route('/config_form', methods=['GET', 'POST'])
@login_required
def config_form():
    load_full_astro_context()
    error = None
    message = None
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        app_db_user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not app_db_user:
            flash(f"Could not find user '{username}' in the database.", "error")
            return redirect(url_for('index'))

        if request.method == 'POST':
            # --- General Settings Tab ---
            if 'submit_general' in request.form:
                prefs = db.query(UiPref).filter_by(user_id=app_db_user.id).first()
                if not prefs:
                    prefs = UiPref(user_id=app_db_user.id, json_blob='{}')
                    db.add(prefs)
                try:
                    settings = json.loads(prefs.json_blob or '{}')
                except json.JSONDecodeError:
                    settings = {}
                settings['altitude_threshold'] = int(request.form.get('altitude_threshold', 20))
                settings['default_location'] = request.form.get('default_location', settings.get('default_location'))
                if SINGLE_USER_MODE:
                    settings['sampling_interval_minutes'] = int(request.form.get("sampling_interval", 15))
                    settings.setdefault('telemetry', {})['enabled'] = bool(request.form.get('telemetry_enabled'))
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
                new_tz = request.form.get("new_timezone")  # Get the timezone

                existing = db.query(Location).filter_by(user_id=app_db_user.id, name=new_name).first()
                if existing:
                    error = f"A location named '{new_name}' already exists."
                elif not all([new_name, request.form.get("new_lat"), request.form.get("new_lon"), new_tz]):
                    error = "Name, Latitude, Longitude, and Timezone are required."

                elif new_tz not in pytz.all_timezones:
                    error = f"Invalid timezone: '{new_tz}'. Please select a valid option from the list."

                else:
                    new_loc = Location(
                        user_id=app_db_user.id, name=new_name,
                        lat=float(request.form.get("new_lat")), lon=float(request.form.get("new_lon")),
                        timezone=request.form.get("new_timezone"), active=request.form.get("new_active") == "on",
                        comments=request.form.get("new_comments", "").strip()[:500]
                    )
                    db.add(new_loc);
                    db.flush()
                    mask_str = request.form.get("new_horizon_mask", "").strip()
                    if mask_str:
                        try:
                            mask_data = yaml.safe_load(mask_str)
                            if isinstance(mask_data, list):
                                for point in mask_data:
                                    db.add(HorizonPoint(location_id=new_loc.id, az_deg=float(point[0]),
                                                        alt_min_deg=float(point[1])))
                        except (yaml.YAMLError, ValueError, TypeError):
                            flash("Warning: Horizon Mask was invalid and was ignored.", "warning")
                    message = "New location added."

            # --- Update Existing Locations ---
            elif 'submit_locations' in request.form:
                locs_to_update = db.query(Location).filter_by(user_id=app_db_user.id).all()
                for loc in locs_to_update:
                    if request.form.get(f"delete_loc_{loc.name}") == "on":
                        db.delete(loc);
                        continue

                    tz_name_from_form = request.form.get(f"timezone_{loc.name}")
                    if tz_name_from_form not in pytz.all_timezones:
                        error = f"Invalid timezone for {loc.name}: '{tz_name_from_form}'. Please select a valid option."
                        break  # Stop processing immediately on the first error

                    loc.lat = float(request.form.get(f"lat_{loc.name}"))
                    loc.lon = float(request.form.get(f"lon_{loc.name}"))
                    loc.timezone = request.form.get(f"timezone_{loc.name}")
                    loc.active = request.form.get(f"active_{loc.name}") == "on"
                    loc.comments = request.form.get(f"comments_{loc.name}", "").strip()[:500]

                    # --- START FIX: Use relationship assignment for cascade ---
                    # 1. Create a new, empty list for this location's points.
                    new_horizon_points = []
                    mask_str = request.form.get(f"horizon_mask_{loc.name}", "").strip()

                    if mask_str:
                        try:
                            mask_data = yaml.safe_load(mask_str)
                            if isinstance(mask_data, list):
                                # 2. Create new HorizonPoint objects and add them to the new list.
                                for point in mask_data:
                                    new_horizon_points.append(
                                        HorizonPoint(location_id=loc.id, az_deg=float(point[0]),
                                                     alt_min_deg=float(point[1]))
                                    )
                        except Exception:
                            flash(f"Warning: Horizon Mask for '{loc.name}' was invalid and ignored.", "warning")

                    # 3. Assign the new list directly to the relationship.
                    # SQLAlchemy will now compare the old list with the new one.
                    # It will automatically delete any points not in the new list (due to 'delete-orphan')
                    # and add any new points. This avoids the bulk-delete conflict.
                    loc.horizon_points = new_horizon_points
                    # --- END FIX ---

                message = "Locations"

            # --- Update Existing Objects ---
            elif 'submit_objects' in request.form:
                # 1. Fetch all objects for the current user
                objs_to_update = db.query(AstroObject).filter_by(user_id=app_db_user.id).all()

                # 2. Loop through each object and process its form data
                for obj in objs_to_update:
                    # Handle deletion first
                    if request.form.get(f"delete_{obj.object_name}") == "on":
                        db.delete(obj);
                        continue

                    # Update standard fields
                    obj.common_name = request.form.get(f"name_{obj.object_name}")
                    obj.ra_hours = float(request.form.get(f"ra_{obj.object_name}"))
                    obj.dec_deg = float(request.form.get(f"dec_{obj.object_name}"))
                    obj.constellation = request.form.get(f"constellation_{obj.object_name}")
                    obj.project_name = request.form.get(f"project_{obj.object_name}")  # Private notes
                    obj.type = request.form.get(f"type_{obj.object_name}")
                    obj.magnitude = request.form.get(f"magnitude_{obj.object_name}")
                    obj.size = request.form.get(f"size_{obj.object_name}")
                    obj.sb = request.form.get(f"sb_{obj.object_name}")

                    # --- START NEW LOGIC ---
                    # Update the 'ActiveProject' status based on the checkbox being 'on'
                    obj.active_project = request.form.get(f"active_project_{obj.object_name}") == "on"
                    # --- END NEW LOGIC ---

                    if not obj.original_user_id:
                        obj.is_shared = request.form.get(f"is_shared_{obj.object_name}") == "on"
                        obj.shared_notes = request.form.get(f"shared_notes_{obj.object_name}")

                message = "Objects updated."

            if not error:
                db.commit()
                flash(f"{message or 'Configuration'} updated successfully.", "success")
                return redirect(url_for('config_form'))
            else:
                db.rollback()
                flash(error, "error")

        # --- GET Request: Populate Template Context from DB ---
        config_for_template = {}
        prefs = db.query(UiPref).filter_by(user_id=app_db_user.id).first()
        if prefs and prefs.json_blob:
            try:
                config_for_template = json.loads(prefs.json_blob)
            except json.JSONDecodeError:
                pass

        # --- START FIX ---
        # Ensure nested dicts are not None, so template .get() calls don't fail
        if config_for_template.get('telemetry') is None:
            config_for_template['telemetry'] = {}
        if config_for_template.get('imaging_criteria') is None:
            config_for_template['imaging_criteria'] = {}
        # --- END FIX ---

        locations_for_template = {}
        db_locations = db.query(Location).filter_by(user_id=app_db_user.id).order_by(Location.name).all()
        for loc in db_locations:
            locations_for_template[loc.name] = {
                "lat": loc.lat, "lon": loc.lon, "timezone": loc.timezone,
                "active": loc.active, "comments": loc.comments,
                "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in
                                 sorted(loc.horizon_points, key=lambda p: p.az_deg)]
            }
            if loc.is_default:
                config_for_template['default_location'] = loc.name

        db_objects = db.query(AstroObject).filter_by(user_id=app_db_user.id).order_by(AstroObject.object_name).all()
        config_for_template['objects'] = []
        for o in db_objects:
            # --- START: Rich Text Upgrade for Private Notes ---
            raw_private_notes = o.project_name or ""
            if not raw_private_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>")):
                escaped_text = bleach.clean(raw_private_notes, tags=[], strip=True)
                private_notes_html = escaped_text.replace("\n", "<br>")
            else:
                private_notes_html = raw_private_notes
            # --- END: Rich Text Upgrade ---

            # --- START: Rich Text Upgrade for SHARED Notes ---
            raw_shared_notes = o.shared_notes or ""
            if not raw_shared_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>")):
                escaped_text = bleach.clean(raw_shared_notes, tags=[], strip=True)
                shared_notes_html = escaped_text.replace("\n", "<br>")
            else:
                shared_notes_html = raw_shared_notes
            # --- END: Rich Text Upgrade for SHARED Notes ---

            # 1. Get all standard fields from the new method
            obj_data_dict = o.to_dict()

            # 2. Overwrite the note fields with our editor-safe HTML
            obj_data_dict["Project"] = private_notes_html
            obj_data_dict["shared_notes"] = shared_notes_html

            # 3. Append the final dictionary
            config_for_template['objects'].append(obj_data_dict)

        catalog_packs = discover_catalog_packs()
        return render_template('config_form.html', config=config_for_template, locations=locations_for_template, all_timezones=pytz.all_timezones, catalog_packs=catalog_packs)

    except Exception as e:
        db.rollback()
        flash(f"A database error occurred: {e}", "error")
        traceback.print_exc()
        return redirect(url_for('index'))
    finally:
        db.close()

@app.route('/update_project', methods=['POST'])
@login_required
def update_project():
    data = request.get_json()
    object_name = data.get('object')

    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        obj_to_update = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if obj_to_update:

            did_change_active_status = False

            # --- START OF FIX ---
            # 1. Update notes if the 'project' key was sent
            if 'project' in data:
                new_project_notes_html = data.get('project')
                obj_to_update.project_name = new_project_notes_html

            # 2. RESTORED: Update Active Status if 'is_active' key was sent
            # This is required for the 'Save Project' button in the graph dashboard
            if 'is_active' in data:
                new_active_status = bool(data.get('is_active'))
                if obj_to_update.active_project != new_active_status:
                    obj_to_update.active_project = new_active_status
                    did_change_active_status = True
            # --- END OF FIX ---

            db.commit()

            # Only trigger the expensive outlook update if the status actually changed
            if did_change_active_status:
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
        tz_name = request.args.get('tz')

        # Use safe_float to handle potential empty strings or invalid values
        lat = safe_float(request.args.get('lat'))
        lon = safe_float(request.args.get('lon'))
        ra = safe_float(request.args.get('ra'))
        dec = safe_float(request.args.get('dec'))

        if not all([date_str, tz_name]):
            raise ValueError("Missing date or timezone.")

        # Check if critical values are None after safe_float
        if lat is None or lon is None:
            raise ValueError("Invalid or missing latitude/longitude.")

        # --- Calculate Moon Phase (always possible) ---
        local_tz = pytz.timezone(tz_name)
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        # Use a consistent time (e.g., local noon) for phase calculation
        time_for_phase_local = local_tz.localize(
            datetime.combine(date_obj.date(), datetime.min.time().replace(hour=12)))
        moon_phase = round(ephem.Moon(time_for_phase_local.astimezone(pytz.utc)).phase, 1)

        # --- Calculate Separation (only if RA/DEC are present) ---
        angular_sep_value = None
        if ra is not None and dec is not None:
            # All values present, calculate separation
            sun_events = calculate_sun_events_cached(date_str, tz_name, lat, lon)
            dusk_str = sun_events.get("astronomical_dusk", "21:00")
            dusk_time_obj = datetime.strptime(dusk_str, "%H:%M").time()
            time_for_calc_local = local_tz.localize(datetime.combine(date_obj.date(), dusk_time_obj))
            time_obj = Time(time_for_calc_local.astimezone(pytz.utc))

            location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            frame = AltAz(obstime=time_obj, location=location_obj)
            obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
            moon_coord = get_body('moon', time_obj, location=location_obj)
            separation = obj_coord.transform_to(frame).separation(moon_coord.transform_to(frame)).deg
            angular_sep_value = round(separation, 1)
        else:
            print("[API Moon Data] RA/DEC not provided, calculating phase only.")

        return jsonify({
            "status": "success",
            "moon_illumination": moon_phase,
            "angular_separation": angular_sep_value  # This will be null if RA/DEC were missing
        })

    except Exception as e:
        print(f"ERROR in /api/get_moon_data: {e}")
        traceback.print_exc()  # Add traceback for better debugging
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/graph_dashboard/<path:object_name>')
def graph_dashboard(object_name):
    # --- 1. Determine User (No change) ---
    if not (SINGLE_USER_MODE or current_user.is_authenticated or getattr(g, 'is_guest', False)):
        flash("Please log in to view object details.", "info")
        return redirect(url_for('login'))

    if SINGLE_USER_MODE:
        username = "default"
    elif current_user.is_authenticated:
        username = current_user.username
    else:  # Must be a guest if we reached here
        username = "guest_user"

    db = get_db()
    try:
        # --- 2. Get User Record (No change) ---
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            flash(f"User '{username}' not found.", "error")
            return redirect(url_for('index'))

        # --- 3. Determine Effective Location (No change) ---
        effective_location_name = "Unknown"
        effective_lat, effective_lon, effective_tz_name = None, None, 'UTC'
        requested_location_name_from_url = request.args.get('location')
        selected_location_db = None

        if requested_location_name_from_url:
            selected_location_db = db.query(Location).filter_by(user_id=user.id,
                                                                name=requested_location_name_from_url).one_or_none()
            if selected_location_db:
                print(f"[Graph View] Using location from URL: {requested_location_name_from_url}")
            else:
                flash(f"Requested location '{requested_location_name_from_url}' not found, using default.", "warning")

        if not selected_location_db:
            selected_location_db = db.query(Location).filter_by(user_id=user.id, is_default=True).one_or_none()
            if not selected_location_db:
                selected_location_db = db.query(Location).filter_by(user_id=user.id, active=True).order_by(
                    Location.id).first()

        if selected_location_db:
            effective_location_name = selected_location_db.name
            effective_lat = selected_location_db.lat
            effective_lon = selected_location_db.lon
            effective_tz_name = selected_location_db.timezone
        else:
            flash("Error: No valid location configured or selected for this user.", "error")
            return redirect(url_for('index'))

        try:
            now_at_effective_location = datetime.now(pytz.timezone(effective_tz_name))
        except pytz.UnknownTimeZoneError:
            effective_tz_name = 'UTC'
            now_at_effective_location = datetime.now(pytz.utc)

        if now_at_effective_location.hour < 12:
            observing_date_for_calcs = now_at_effective_location.date() - timedelta(days=1)
        else:
            observing_date_for_calcs = now_at_effective_location.date()

        # --- 4. Query ONLY the specific object (No change) ---
        obj_record = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if not obj_record:
            flash(f"Object '{object_name}' not found in your configuration.", "error")
            return redirect(url_for('index'))

        # --- Framing Tab Data Preparation ---
        project_record = db.query(Project).filter_by(
            user_id=user.id, target_object_name=object_name
        ).order_by(Project.status).first()

        project_id_for_this_object = None
        project_name_for_this_object = "N/A"
        project_data_for_template = {}
        sessions_for_project = []  # This is for the Framing Tab only
        total_integration_str = "N/A"

        goals_html = ""
        description_notes_html = ""
        framing_notes_html = ""
        processing_notes_html = ""

        def _sanitize_for_display(html_content):
            if not html_content: return ""
            SAFE_TAGS = ['p', 'strong', 'em', 'ul', 'ol', 'li', 'br', 'div', 'img', 'a', 'figure', 'figcaption']
            SAFE_ATTRS = {'img': ['src', 'alt', 'width', 'height', 'style'], 'a': ['href'], '*': ['style']}
            SAFE_CSS = ['text-align', 'width', 'height', 'max-width', 'float', 'margin', 'margin-left',
                        'margin-right']
            css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS)
            return bleach.clean(html_content, tags=SAFE_TAGS, attributes=SAFE_ATTRS, css_sanitizer=css_sanitizer)

        if project_record:
            project_id_for_this_object = project_record.id
            project_name_for_this_object = project_record.name
            project_data_for_template = {c.name: getattr(project_record, c.name)
                                         for c in project_record.__table__.columns}
            # (We skip calculating stats here since we removed the Project Details sub-tab from Framing)

        # Notes Upgrader
        raw_project_notes = obj_record.project_name or ""
        if not raw_project_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>")):
            escaped_text = bleach.clean(raw_project_notes, tags=[], strip=True)
            project_notes_for_editor = escaped_text.replace("\n", "<br>")
        else:
            project_notes_for_editor = raw_project_notes

        object_main_details = {
            "Object": obj_record.object_name, "Common Name": obj_record.common_name,
            "RA (hours)": obj_record.ra_hours, "DEC (degrees)": obj_record.dec_deg,
            "Type": obj_record.type, "Constellation": obj_record.constellation,
            "Magnitude": obj_record.magnitude, "Size": obj_record.size, "SB": obj_record.sb,
            "ActiveProject": obj_record.active_project, "Project": obj_record.project_name,
            "Name": obj_record.common_name, "RA": obj_record.ra_hours, "DEC": obj_record.dec_deg
        }

        # --- 5. Handle Journal Data ---
        requested_session_id = request.args.get('session_id')
        requested_project_id_journal = request.args.get('project_id')

        # Auto-select the primary project if no specific session/project is requested
        if not requested_session_id and not requested_project_id_journal and project_record:
            requested_project_id_journal = project_record.id

        selected_session_data = None
        selected_session_data_dict = None

        # Variables for the Journal Project Detail View
        selected_project_data_journal = None
        project_journal_html_fields = {}
        total_integration_str_journal = "0h 0m"
        project_sessions_list_journal = []  # <--- NEW LIST

        # A: Handle Session Selection
        if requested_session_id:
            selected_session_data = db.query(JournalSession).filter_by(id=requested_session_id,
                                                                       user_id=user.id).one_or_none()
            if selected_session_data:
                selected_session_data_dict = {c.name: getattr(selected_session_data, c.name) for c in
                                              selected_session_data.__table__.columns}
                # ... (Session Note Sanitization Logic - same as before) ...
                raw_journal_notes = selected_session_data_dict.get('notes') or ""
                if not raw_journal_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>")):
                    escaped_text = bleach.clean(raw_journal_notes, tags=[], strip=True)
                    sanitized_notes = escaped_text.replace("\n", "<br>")
                else:
                    SAFE_TAGS = ['p', 'strong', 'em', 'ul', 'ol', 'li', 'br', 'div', 'img', 'a', 'figure', 'figcaption']
                    SAFE_ATTRS = {'img': ['src', 'alt', 'width', 'height', 'style'], 'a': ['href'], '*': ['style']}
                    SAFE_CSS = ['text-align', 'width', 'height', 'max-width', 'float', 'margin', 'margin-left',
                                'margin-right']
                    css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS)
                    sanitized_notes = bleach.clean(raw_journal_notes, tags=SAFE_TAGS, attributes=SAFE_ATTRS,
                                                   css_sanitizer=css_sanitizer)
                selected_session_data_dict['notes'] = sanitized_notes

                if isinstance(selected_session_data_dict.get('date_utc'), (datetime, date)):
                    selected_session_data_dict['date_utc'] = selected_session_data_dict['date_utc'].isoformat()
                    if selected_session_data.date_utc:
                        effective_day, effective_month, effective_year = selected_session_data.date_utc.day, selected_session_data.date_utc.month, selected_session_data.date_utc.year

        # B: Handle Project Selection
        elif requested_project_id_journal:
            selected_project_data_journal = db.query(Project).filter_by(
                id=requested_project_id_journal, user_id=user.id
            ).one_or_none()

            if selected_project_data_journal:
                # Calculate total integration
                total_int_min = db.query(
                    func.sum(JournalSession.calculated_integration_time_minutes)
                ).filter_by(project_id=selected_project_data_journal.id, user_id=user.id).scalar() or 0
                total_minutes = int(total_int_min)
                total_integration_str_journal = f"{total_minutes // 60}h {total_minutes % 60}m"

                # Prepare sanitized rich text fields
                project_journal_html_fields = {
                    'goals': _sanitize_for_display(selected_project_data_journal.goals),
                    'description': _sanitize_for_display(selected_project_data_journal.description_notes),
                    'framing': _sanitize_for_display(selected_project_data_journal.framing_notes),
                    'processing': _sanitize_for_display(selected_project_data_journal.processing_notes)
                }

                # --- NEW: Fetch all sessions for this project to explain the integration time ---
                project_sessions_db = db.query(JournalSession).filter_by(
                    project_id=selected_project_data_journal.id, user_id=user.id
                ).order_by(JournalSession.date_utc.desc()).all()

                project_sessions_list_journal = []
                for s in project_sessions_db:
                    s_dict = {c.name: getattr(s, c.name) for c in s.__table__.columns}
                    if s.date_utc: s_dict['date_utc'] = s.date_utc.isoformat()
                    project_sessions_list_journal.append(s_dict)

        # (The rest of grouping logic is unchanged)
        all_projects_for_user = db.query(Project).filter_by(user_id=user.id).order_by(Project.name).all()
        object_specific_sessions_db = db.query(JournalSession).filter_by(user_id=user.id,
                                                                         object_name=object_name).order_by(
            JournalSession.date_utc.desc()).all()

        object_specific_sessions_list = []
        for s in object_specific_sessions_db:
            session_dict = {c.name: getattr(s, c.name) for c in s.__table__.columns}
            object_specific_sessions_list.append(session_dict)

        projects_map = {p.id: p.name for p in all_projects_for_user}
        grouped_sessions_dict = {}

        # 1. Add sessions to their respective projects
        for session in object_specific_sessions_list:
            project_id = session.get('project_id')
            grouped_sessions_dict.setdefault(project_id, []).append(session)

        # 2. Explicitly ensure empty projects targeting this object appear in the list
        # This fixes the issue where a newly created project (with no sessions yet) remains invisible
        target_projects = [p for p in all_projects_for_user if p.target_object_name == object_name]
        for tp in target_projects:
            if tp.id not in grouped_sessions_dict:
                grouped_sessions_dict[tp.id] = []

        grouped_sessions_for_template = []
        sorted_project_ids = sorted([pid for pid in grouped_sessions_dict if pid],
                                    key=lambda pid: projects_map.get(pid, ''))
        for project_id in sorted_project_ids:
            sessions_in_group = grouped_sessions_dict[project_id]
            total_minutes = sum(s.get('calculated_integration_time_minutes') or 0 for s in sessions_in_group)
            grouped_sessions_for_template.append({
                'is_project': True,
                'project_name': projects_map.get(project_id, 'Unknown Project'),
                'project_id': project_id,
                'sessions': sessions_in_group,
                'total_integration_time': total_minutes
            })
        if None in grouped_sessions_dict:
            sessions_none_project = grouped_sessions_dict[None]
            total_minutes_none = sum(s.get('calculated_integration_time_minutes') or 0 for s in sessions_none_project)
            grouped_sessions_for_template.append({
                'is_project': False,
                'project_name': 'Standalone Sessions',
                'project_id': None,
                'sessions': sessions_none_project,
                'total_integration_time': total_minutes_none
            })

        # --- 6. Calculate Effective Date (No change) ---
        effective_day_req = request.args.get('day')
        effective_month_req = request.args.get('month')
        effective_year_req = request.args.get('year')

        if effective_day_req and not selected_session_data_dict:
            effective_day = int(effective_day_req)
        elif not selected_session_data_dict:
            effective_day = observing_date_for_calcs.day

        if effective_month_req and not selected_session_data_dict:
            effective_month = int(effective_month_req)
        elif not selected_session_data_dict:
            effective_month = observing_date_for_calcs.month

        if effective_year_req and not selected_session_data_dict:
            effective_year = int(effective_year_req)
        elif not selected_session_data_dict:
            effective_year = observing_date_for_calcs.year

        try:
            effective_date_obj = datetime(effective_year, effective_month, effective_day)
            effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
        except ValueError:
            effective_date_obj = now_at_effective_location.date()
            effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
            flash("Invalid date components provided, defaulting to current date.", "warning")

        next_day_obj = effective_date_obj + timedelta(days=1)

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

        # --- 7. Load Rigs (No change) ---
        rigs_from_db = db.query(Rig).options(
            selectinload(Rig.telescope), selectinload(Rig.camera), selectinload(Rig.reducer_extender)
        ).filter_by(user_id=user.id).all()
        final_rigs_for_template = []
        for rig in rigs_from_db:
            efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(rig.telescope, rig.camera,
                                                                              rig.reducer_extender)
            fov_h = None
            if rig.camera and rig.camera.sensor_height_mm and efl:
                try:
                    fov_h = (degrees(2 * atan((rig.camera.sensor_height_mm / 2.0) / efl)) * 60.0)
                except:
                    pass
            final_rigs_for_template.append({
                "rig_id": rig.id, "rig_name": rig.rig_name,
                "effective_focal_length": efl, "f_ratio": f_ratio,
                "image_scale": scale, "fov_w_arcmin": fov_w, "fov_h_arcmin": fov_h
            })

        prefs_record = db.query(UiPref).filter_by(user_id=user.id).first()
        sort_preference = 'name-asc'
        if prefs_record and prefs_record.json_blob:
            try:
                sort_preference = json.loads(prefs_record.json_blob).get('rig_sort', 'name-asc')
            except:
                pass
        sorted_rigs = sort_rigs(final_rigs_for_template, sort_preference)

        # --- 8. Load Other Template Data (No change) ---
        all_projects = db.query(Project).filter_by(user_id=user.id).order_by(Project.name).all()
        available_locations = db.query(Location).filter_by(user_id=user.id).order_by(Location.name).all()
        default_location_name = effective_location_name if selected_location_db and selected_location_db.is_default else None
        if not default_location_name:
            default_loc_obj = db.query(Location).filter_by(user_id=user.id, is_default=True).first()
            if default_loc_obj: default_location_name = default_loc_obj.name

        all_objects_for_framing = []
        try:
            all_objs = db.query(AstroObject).filter_by(user_id=user.id).filter(AstroObject.ra_hours != None,
                                                                               AstroObject.dec_deg != None).all()
            for o in all_objs:
                try:
                    all_objects_for_framing.append({
                        "id": o.id, "object_name": o.object_name, "common_name": o.common_name,
                        "ra_deg": float(o.ra_hours) * 15.0, "dec_deg": float(o.dec_deg),
                    })
                except Exception:
                    continue
        except Exception:
            all_objects_for_framing = []

        # --- 9. Render Template ---
        return render_template('graph_view.html',
                               object_name=object_name,
                               alt_name=object_main_details.get("Common Name", object_name),
                               object_main_details=object_main_details,
                               available_rigs=sorted_rigs,
                               selected_day=effective_date_obj.day,
                               selected_month=effective_date_obj.month,
                               selected_year=effective_date_obj.year,
                               header_location_name=effective_location_name,
                               header_date_display=f"{effective_date_obj.strftime('%d.%m.%Y')} - {next_day_obj.strftime('%d.%m.%Y')}",
                               header_moon_phase=moon_phase_for_effective_date,
                               header_astro_dusk=sun_events_for_effective_date.get("astronomical_dusk", "N/A"),
                               header_astro_dawn=sun_events_for_effective_date.get("astronomical_dawn", "N/A"),
                               project_notes_from_config=project_notes_for_editor,

                               # --- PASS THESE VARIABLES TO TEMPLATE ---
                               selected_project_data_journal=selected_project_data_journal,
                               project_journal_html_fields=project_journal_html_fields,
                               total_integration_str_journal=total_integration_str_journal,
                               current_project_id=requested_project_id_journal,
                               project_sessions_list_journal=project_sessions_list_journal,  # <--- NEW Variable

                               all_objects=db.query(AstroObject).filter_by(user_id=user.id).order_by(
                                   AstroObject.object_name).all(),
                               project_statuses=["In Progress", "Completed", "On Hold", "Abandoned"],
                               is_project_active=object_main_details.get('ActiveProject', False),
                               grouped_sessions=grouped_sessions_for_template,
                               object_specific_sessions=object_specific_sessions_list,
                               selected_session_data=selected_session_data,
                               selected_session_data_dict=selected_session_data_dict,
                               current_session_id=requested_session_id if selected_session_data else None,
                               graph_lat_param=effective_lat,
                               graph_lon_param=effective_lon,
                               graph_tz_name_param=effective_tz_name,
                               all_projects=all_projects,
                               available_locations=available_locations,
                               default_location=default_location_name,
                               framing_objects=all_objects_for_framing,
                               stellarium_api_url_base=STELLARIUM_API_URL_BASE,
                               today_date=datetime.now().strftime('%Y-%m-%d'),
                               is_guest=g.is_guest
                               )

    except Exception as e:
        db.rollback()
        print(f"ERROR rendering graph dashboard for '{object_name}': {e}")
        traceback.print_exc()
        flash(f"An error occurred while loading the details for {object_name}.", "error")
        return redirect(url_for('index'))
    finally:
        db.close()

@app.route('/get_date_info/<path:object_name>')
def get_date_info(object_name):
    load_full_astro_context()
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
    load_full_astro_context()
    # Load object RA/DEC (this uses 'g' context correctly)
    data = get_ra_dec(object_name)
    if not data or data.get("RA (hours)") is None or data.get("DEC (degrees)") is None:
        error_msg = data.get("Common Name", "Object data not found or invalid RA/DEC.")
        return jsonify({"status": "error", "message": error_msg}), 400

    ra = data["RA (hours)"]
    dec = data["DEC (degrees)"]
    alt_name = data.get("Common Name", object_name)

    # --- START FIX: Read location from query parameters ---
    # Try to get lat, lon, tz from the URL query string.
    # Fall back to the values in the global 'g' object if parameters are missing.
    try:
        lat_str = request.args.get('plot_lat')
        lon_str = request.args.get('plot_lon')
        tz_name_req = request.args.get('plot_tz')

        # Use request args if provided and valid, otherwise fallback to g
        lat = float(lat_str) if lat_str else g.lat
        lon = float(lon_str) if lon_str else g.lon
        tz_name = tz_name_req if tz_name_req else g.tz_name

        # Validate that we ended up with valid numeric lat/lon
        if lat is None or lon is None:
             raise ValueError("Latitude or Longitude could not be determined.")
        # Validate timezone
        if not tz_name:
             raise ValueError("Timezone could not be determined.")
        local_tz = pytz.timezone(tz_name) # This will raise UnknownTimeZoneError if invalid

    except (ValueError, TypeError, pytz.UnknownTimeZoneError) as e:
        # Handle errors if lat/lon aren't numbers or tz is invalid
        print(f"❌ ERROR: Invalid location data in get_imaging_opportunities: {e}")
        return jsonify({"status": "error", "message": f"Invalid location data provided: {e}"}), 400
    # --- END FIX ---

    # --- Use the determined lat, lon, tz_name, local_tz variables below ---

    # Get imaging criteria (this uses 'g' context correctly)
    criteria = get_imaging_criteria()
    min_obs = criteria["min_observable_minutes"]
    min_alt = criteria["min_max_altitude"]
    max_moon = criteria["max_moon_illumination"]
    min_sep = criteria["min_angular_separation"]
    months = criteria.get("search_horizon_months", 6)

    # Use the determined local_tz for date calculations
    today = datetime.now(local_tz).date()
    end_date = today + timedelta(days=months * 30)
    dates = [today + timedelta(days=i) for i in range((end_date - today).days)]

    sun_events_cache = {}
    final_results = []

    # Get altitude threshold and sampling interval (from 'g')
    altitude_threshold = g.user_config.get("altitude_threshold", 20)
    sampling_interval = (g.user_config.get('sampling_interval_minutes') or 15) if SINGLE_USER_MODE else int(
        os.environ.get('CALCULATION_PRECISION', 15))

    # --- Get Horizon Mask for the specific location ---
    # We need the location *name* to look up the mask.
    # It's tricky because the graph view only sends lat/lon/tz.
    # We'll approximate by finding a location in g.locations that matches lat/lon/tz.
    # This isn't perfect if multiple locations share coords but is the best we can do without passing the name.
    horizon_mask = None
    try:
        if hasattr(g, 'locations') and isinstance(g.locations, dict):
            for loc_name, loc_details in g.locations.items():
                # Compare floats with a small tolerance
                if (abs(loc_details.get('lat', 999) - lat) < 0.001 and
                    abs(loc_details.get('lon', 999) - lon) < 0.001 and
                    loc_details.get('timezone') == tz_name):
                    horizon_mask = loc_details.get('horizon_mask')
                    # print(f"[Opportunities] Found matching location '{loc_name}' for horizon mask.") # Debug
                    break # Use the first match
            # if not horizon_mask: print("[Opportunities] No matching location found for horizon mask.") # Debug
    except Exception as e:
        print(f"Warning: Error accessing horizon mask - {e}")
    # --- End Horizon Mask Lookup ---


    for d in dates:
        date_str = d.strftime('%Y-%m-%d')
        # Check cache or compute sun events using determined lat, lon, tz_name
        if date_str not in sun_events_cache:
            sun_events_cache[date_str] = calculate_sun_events_cached(date_str, tz_name, lat, lon)
        sun_events = sun_events_cache[date_str]
        dusk = sun_events.get("astronomical_dusk", "20:00") # Default dusk if not found

        # Calculate observable duration using determined lat, lon, tz_name, threshold, interval, AND horizon_mask
        obs_duration, max_altitude, obs_from, obs_to = calculate_observable_duration_vectorized(
            ra, dec, lat, lon, date_str, tz_name,
            altitude_threshold, sampling_interval,
            horizon_mask=horizon_mask # Pass the looked-up mask
        )

        # Apply basic thresholds
        if obs_duration.total_seconds() / 60 < min_obs or max_altitude < min_alt:
            continue

        # Calculate Moon phase using determined local_tz
        time_for_moon_phase = local_tz.localize(datetime.combine(d, datetime.min.time().replace(hour=12))) # Use local noon
        moon_phase = ephem.Moon(time_for_moon_phase.astimezone(pytz.utc)).phase
        if moon_phase > max_moon:
            continue

        # Calculate separation using determined local_tz, lat, lon
        try:
            dusk_time_obj = datetime.strptime(dusk, "%H:%M").time()
        except ValueError:
            dusk_time_obj = datetime.strptime("20:00", "%H:%M").time() # Fallback dusk time
        dusk_dt_local = local_tz.localize(datetime.combine(d, dusk_time_obj))
        dusk_utc = dusk_dt_local.astimezone(pytz.utc)

        location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg) # Use determined lat, lon
        time_for_sep = Time(dusk_utc)
        frame = AltAz(obstime=time_for_sep, location=location_obj)
        obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        moon_coord = get_body('moon', time_for_sep, location=location_obj)
        try:
            separation = obj_coord.transform_to(frame).separation(moon_coord.transform_to(frame)).deg
            if separation < min_sep:
                continue
        except Exception as sep_err:
             print(f"Warning: Could not calculate separation for {object_name} on {date_str}: {sep_err}")
             continue # Skip if separation calc fails

        # Scoring logic (remains the same)
        MIN_ALTITUDE = 20
        score_alt = max(0, min((max_altitude - MIN_ALTITUDE) / (90 - MIN_ALTITUDE), 1))
        score_duration = min(obs_duration.total_seconds() / (3600 * 12), 1)
        score_moon_illum = 1 - min(moon_phase / 100, 1)
        score_moon_sep_dynamic = (1 - (moon_phase / 100)) + (moon_phase / 100) * min(separation / 180, 1)
        composite_score = 100 * (0.20 * score_alt + 0.15 * score_duration + 0.45 * score_moon_illum + 0.20 * score_moon_sep_dynamic)
        stars = int(round((composite_score / 100) * 4)) + 1
        star_string = "★" * stars + "☆" * (5 - stars)

        # Append results
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

    # Return success response
    return jsonify({"status": "success", "object": object_name, "alt_name": alt_name, "results": final_results})


@app.route('/project/report_page/<string:project_id>')
@login_required
def show_project_report_page(project_id):
    db = get_db()
    try:
        # 1. Fetch Project
        project = db.query(Project).filter_by(id=project_id, user_id=g.db_user.id).one_or_none()
        if not project:
            return "Project not found", 404

        # 2. Fetch Sessions
        sessions = db.query(JournalSession).filter_by(project_id=project.id, user_id=g.db_user.id).order_by(
            JournalSession.date_utc.asc()).all()

        # 3. Calculate Stats
        total_min = sum(s.calculated_integration_time_minutes or 0 for s in sessions)
        hours = int(total_min) // 60
        mins = int(total_min) % 60
        total_integration = f"{hours}h {mins}m"

        first_date = sessions[0].date_utc.strftime('%d.%m.%Y') if sessions else "-"
        last_date = sessions[-1].date_utc.strftime('%d.%m.%Y') if sessions else "-"

        # 4. Prepare Image
        project_image_url = None
        if project.final_image_file:
            username = "default" if SINGLE_USER_MODE else current_user.username
            project_image_url = url_for('get_uploaded_image', username=username, filename=project.final_image_file,
                                        _external=True)

        return render_template(
            'project_report.html',
            project=project,
            sessions=sessions,
            total_integration=total_integration,
            session_count=len(sessions),
            first_date=first_date,
            last_date=last_date,
            project_image_url=project_image_url,
            today_date=datetime.now().strftime('%d.%m.%Y')
        )
    finally:
        db.close()

@app.route('/project/delete/<string:project_id>', methods=['POST'])
@login_required
def delete_project(project_id):
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        project = db.query(Project).filter_by(id=project_id, user_id=user.id).one_or_none()

        if not project:
            flash("Project not found.", "error")
            return redirect(url_for('index'))

        # Optional: Unset 'active_project' flag on the associated object if it exists
        if project.target_object_name:
            obj = db.query(AstroObject).filter_by(user_id=user.id, object_name=project.target_object_name).one_or_none()
            if obj:
                obj.active_project = False

        # Delete the project.
        # Note: Sessions will NOT be deleted. Their project_id will automatically set to NULL
        # because of the ForeignKey(ondelete="SET NULL") definition in your model.
        db.delete(project)
        db.commit()

        flash(f"Project '{project.name}' deleted. Sessions are now standalone.", "success")

        # Redirect back to the object's journal tab
        return redirect(
            url_for('graph_dashboard', object_name=request.form.get('redirect_object', 'M31'), tab='journal'))

    except Exception as e:
        db.rollback()
        flash(f"Error deleting project: {e}", "error")
        return redirect(url_for('index'))
    finally:
        db.close()


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
                    {"id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm,
                     # --- ADD THESE 3 LINES ---
                     "is_shared": c.is_shared, "original_user_id": c.original_user_id,
                     "original_item_id": c.original_item_id
                     }
                    for c in bykind("telescope")
                ],
                "cameras": [
                    {"id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                     "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um,
                     # --- ADD THESE 3 LINES ---
                     "is_shared": c.is_shared, "original_user_id": c.original_user_id,
                     "original_item_id": c.original_item_id
                     }
                    for c in bykind("camera")
                ],
                "reducers_extenders": [
                    {"id": c.id, "name": c.name, "factor": c.factor,
                     # --- ADD THESE 3 LINES ---
                     "is_shared": c.is_shared, "original_user_id": c.original_user_id,
                     "original_item_id": c.original_item_id
                     }
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

    V2 FIX: This version strips all directory structures from the ZIP,
    placing all files flatly into the user's root upload directory.
    This correctly handles migrating from a multi-user (e.g., /uploads/mrantonSG/)
    to a single-user (/uploads/default/) instance.
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
        if not zipfile.is_zipfile(file):
            flash("Import failed: The uploaded file is not a valid ZIP archive.", "error")
            return redirect(url_for('config_form'))

        file.seek(0)

        extracted_count = 0
        with zipfile.ZipFile(file, 'r') as zf:
            for member in zf.infolist():
                # Skip directories
                if member.is_dir():
                    continue

                # Get just the filename, stripping all parent directories
                filename = os.path.basename(member.filename)

                # Skip empty filenames (like .DS_Store or empty dir entries)
                if not filename:
                    continue

                # 🔒 Security Check: Prevent path traversal
                # (os.path.basename already helps, but we double-check)
                if ".." in filename or filename.startswith(("/", "\\")):
                    print(f"Skipping potentially malicious file: {member.filename}")
                    continue

                # Build the final, correct, flat target path
                target_path = os.path.join(user_upload_dir, filename)

                # Extract the file data
                with zf.open(member) as source, open(target_path, "wb") as target:
                    target.write(source.read())

                extracted_count += 1

        flash(f"Journal photos imported successfully! Extracted {extracted_count} files.", "success")

    except zipfile.BadZipFile:
        flash("Import failed: The ZIP file appears to be corrupted.", "error")
    except Exception as e:
        flash(f"An unexpected error occurred during import: {e}", "error")

    return redirect(url_for('config_form'))


@app.route('/api/get_saved_views')
@login_required
def get_saved_views():
    db = get_db()
    try:
        views = db.query(SavedView).filter_by(user_id=g.db_user.id).order_by(SavedView.name).all()
        views_dict = {
            v.name: {
                "id": v.id,
                "name": v.name,
                "settings": json.loads(v.settings_json)
            } for v in views
        }
        return jsonify(views_dict)
    except Exception as e:
        print(f"Error fetching saved views: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@app.route('/api/save_saved_view', methods=['POST'])
@login_required
def save_saved_view():
    db = get_db()
    try:
        data = request.get_json()
        view_name = data.get('name')
        settings = data.get('settings')

        # --- NEW: Capture description and sharing status ---
        description = data.get('description', '')
        is_shared = bool(data.get('is_shared', False))

        if not view_name or not settings:
            return jsonify({"status": "error", "message": "Missing view name or settings."}), 400

        settings_str = json.dumps(settings)

        # Check for existing view by name (upsert logic)
        existing_view = db.query(SavedView).filter_by(user_id=g.db_user.id, name=view_name).one_or_none()

        if existing_view:
            existing_view.settings_json = settings_str

            # Only update description/sharing if it is NOT an imported view
            # (Imported views preserve their original metadata)
            if not existing_view.original_user_id:
                existing_view.description = description
                existing_view.is_shared = is_shared

            message = "View updated"
        else:
            new_view = SavedView(
                user_id=g.db_user.id,
                name=view_name,
                description=description,  # <-- New field
                settings_json=settings_str,
                is_shared=is_shared  # <-- New field
            )
            db.add(new_view)
            message = "View saved"

        db.commit()
        return jsonify({"status": "success", "message": message})

    except Exception as e:
        db.rollback()
        print(f"Error saving view: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()


@app.route('/api/delete_saved_view', methods=['POST'])
@login_required
def delete_saved_view():
    db = get_db()
    try:
        data = request.get_json()
        view_name = data.get('name')
        if not view_name:
            return jsonify({"status": "error", "message": "Missing view name."}), 400

        view_to_delete = db.query(SavedView).filter_by(user_id=g.db_user.id, name=view_name).one_or_none()

        if view_to_delete:
            db.delete(view_to_delete)
            db.commit()
            return jsonify({"status": "success", "message": "View deleted."})
        else:
            return jsonify({"status": "error", "message": "View not found."}), 404
    except Exception as e:
        db.rollback()
        print(f"Error deleting view: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()


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

            # === START REFACTOR ===
            # Import directly into the database
            db = get_db()
            try:
                user = _upsert_user(db, username)  # Get or create the user in app.db
                print(f"[IMPORT_RIGS] Deleting all existing rigs and components for user '{username}' before import...")

                # 1. Delete existing Rigs (must be done first due to foreign keys)
                db.query(Rig).filter_by(user_id=user.id).delete()

                # 2. Delete existing Components
                db.query(Component).filter_by(user_id=user.id).delete()

                # 3. Flush the deletions
                db.flush()
                # Use the migration helper to load data directly into the DB
                _migrate_components_and_rigs(db, user, new_rigs_data, username)

                db.commit()
                flash("Rigs configuration imported and synced to database successfully!", "success")
            except Exception as e:
                db.rollback()
                print(f"[IMPORT_RIGS] DB Error: {e}")
                raise e  # Re-throw to be caught by the outer block
            finally:
                db.close()
            # === END REFACTOR ===

        except (yaml.YAMLError, Exception) as e:
            flash(f"Error importing rigs file: {e}", "error")

    else:
        flash("Invalid file type. Please upload a .yaml or .yml file.", "error")

    return redirect(url_for('config_form'))

@app.route('/api/get_monthly_plot_data/<path:object_name>')
def get_monthly_plot_data(object_name):
    load_full_astro_context()
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


@app.route('/api/get_yearly_heatmap_chunk')
@login_required
def get_yearly_heatmap_chunk():
    load_full_astro_context()

    try:
        # 1. Parse Request Parameters
        chunk_idx = int(request.args.get('chunk_index', 0))  # 0 to 11 (Month index)
        total_chunks = 12

        # Prefer explicit location name from request
        req_loc_name = request.args.get('location_name')
        if req_loc_name and req_loc_name in g.locations:
            loc_data = g.locations[req_loc_name]
            lat = loc_data['lat']
            lon = loc_data['lon']
            tz_name = loc_data['timezone']
            horizon_mask = loc_data.get('horizon_mask')
            selected_loc_key = req_loc_name
        else:
            lat = float(request.args.get('lat', g.lat))
            lon = float(request.args.get('lon', g.lon))
            tz_name = request.args.get('tz', g.tz_name)
            horizon_mask = None
            if g.selected_location and g.selected_location in g.locations:
                horizon_mask = g.locations[g.selected_location].get('horizon_mask')
            selected_loc_key = g.selected_location or "default"

        local_tz = pytz.timezone(tz_name)

        # 2. GENERATE CACHE KEY (Per Chunk)
        db = get_db()
        user_id = g.db_user.id
        obj_count = db.query(AstroObject).filter_by(user_id=user_id).count()
        loc_safe = selected_loc_key.lower().replace(' ', '_')

        # Base filename
        base_cache_name = f"heatmap_v5_{user_id}_{loc_safe}_{obj_count}"
        # Specific chunk filename
        chunk_cache_filename = os.path.join(CACHE_DIR, f"{base_cache_name}.part{chunk_idx}.json")

        # 3. FAST PATH: Read existing chunk from disk
        if os.path.exists(chunk_cache_filename):
            mtime = os.path.getmtime(chunk_cache_filename)
            if (time.time() - mtime) < 86400:  # 24 hours
                try:
                    with open(chunk_cache_filename, 'r') as f:
                        # print(f"[HEATMAP] Serving chunk {chunk_idx} from cache: {chunk_cache_filename}")
                        return jsonify(json.load(f))
                except Exception as e:
                    print(f"[HEATMAP] Error reading chunk cache: {e}")

        # 4. SLOW PATH: Live Calculation
        now = datetime.now(local_tz)
        start_date_year = now.date() - timedelta(days=now.weekday())

        weeks_per_chunk = 52 // total_chunks
        remainder = 52 % total_chunks
        start_week = chunk_idx * weeks_per_chunk + min(chunk_idx, remainder)
        end_week = start_week + weeks_per_chunk + (1 if chunk_idx < remainder else 0)

        weeks_x = []
        target_dates = []
        moon_phases = []

        for i in range(start_week, end_week):
            d = start_date_year + timedelta(weeks=i)
            weeks_x.append(d.strftime('%b %d'))
            target_dates.append(d.strftime('%Y-%m-%d'))
            try:
                dt_moon = local_tz.localize(datetime.combine(d, datetime.min.time())).astimezone(pytz.utc)
                m = ephem.Moon(dt_moon)
                moon_phases.append(round(m.phase, 1))
            except:
                moon_phases.append(0)

        # --- Object Selection ---
        altitude_threshold = g.user_config.get("altitude_threshold", 20)
        sampling_interval = 60

        all_objects = db.query(AstroObject).filter_by(user_id=user_id).all()

        # Validity Check
        valid_objects = [o for o in all_objects if o.ra_hours is not None and o.dec_deg is not None]

        # Geometric Visibility Filter (Consistent across all chunks)
        visible_objects = []
        for obj in valid_objects:
            dec = float(obj.dec_deg)
            # Max theoretical altitude = 90 - |Lat - Dec|
            max_theoretical_alt = 90 - abs(lat - dec)
            if max_theoretical_alt >= altitude_threshold:
                visible_objects.append(obj)

        visible_objects.sort(key=lambda x: float(x.ra_hours))

        # --- Data Generation ---
        z_scores_chunk = []
        y_names = []
        meta_ids = []
        meta_active = []
        meta_types = []
        meta_cons = []
        meta_mags = []
        meta_sizes = []
        meta_sbs = []

        for obj in visible_objects:
            ra = float(obj.ra_hours)
            dec = float(obj.dec_deg)
            obj_scores = []

            for i, date_str in enumerate(target_dates):
                obs_dur, max_alt, _, _ = calculate_observable_duration_vectorized(
                    ra, dec, lat, lon, date_str, tz_name,
                    altitude_threshold, sampling_interval, horizon_mask
                )

                duration_mins = obs_dur.total_seconds() / 60 if obs_dur else 0

                if max_alt is None or max_alt < altitude_threshold or duration_mins < 45:
                    score = 0
                else:
                    norm_alt = min((max_alt - altitude_threshold) / (90 - altitude_threshold), 1.0)
                    norm_dur = min(duration_mins / 480, 1.0)
                    score = (0.4 * norm_alt + 0.6 * norm_dur) * 100

                    current_moon = moon_phases[i]
                    if current_moon > 60:
                        penalty_factor = ((current_moon - 60) / 40) * 0.9
                        score *= (1 - penalty_factor)

                obj_scores.append(round(score, 1))

            z_scores_chunk.append(obj_scores)

            # Metadata
            display_name = obj.common_name or obj.object_name
            if obj.type: display_name += f" [{obj.type}]"
            y_names.append(display_name)
            meta_ids.append(obj.object_name)
            meta_active.append(1 if obj.active_project else 0)
            meta_types.append(str(obj.type or ""))
            meta_cons.append(str(obj.constellation or ""))
            try:
                meta_mags.append(float(obj.magnitude))
            except:
                meta_mags.append(999.0)
            try:
                meta_sizes.append(float(obj.size))
            except:
                meta_sizes.append(0.0)
            try:
                meta_sbs.append(float(obj.sb))
            except:
                meta_sbs.append(999.0)

        result_data = {
            "chunk_index": chunk_idx,
            "x": weeks_x,
            "z_chunk": z_scores_chunk,
            "y": y_names,
            "moon_phases": moon_phases,
            "ids": meta_ids,
            "active": meta_active,
            "dates": target_dates,
            "types": meta_types,
            "cons": meta_cons,
            "mags": meta_mags,
            "sizes": meta_sizes,
            "sbs": meta_sbs
        }

        # 5. SAVE CHUNK TO DISK
        try:
            with open(chunk_cache_filename, 'w') as f:
                json.dump(result_data, f)
            print(f"[HEATMAP] Saved chunk {chunk_idx} to {chunk_cache_filename}")
        except Exception as e:
            print(f"[HEATMAP] Failed to write cache file: {e}")

        return jsonify(result_data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if 'db' in locals(): db.close()



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

    @app.cli.command("migrate-yaml-to-db")
    def migrate_yaml_command():
        """
        Initializes instance directories and runs the one-time migration
        from all YAML files to the app.db database.
        """
        print("--- [MIGRATION COMMAND] ---")
        print("Step 1: Initializing instance directory...")
        initialize_instance_directory()
        print("Step 2: Running YAML to Database migration...")
        run_one_time_yaml_migration()
        print("--- [MIGRATION COMMAND] ---")
        print("✅ Migration task complete.")


# =============================================================================
# One-Time Data Migration for Object Name Normalization
# =============================================================================


@app.cli.command("seed-empty-users")
def seed_empty_users_command():
    """
    Finds all existing users with no data (no locations) and seeds
    their accounts from the 'guest_user' template.
    """
    print("--- [BACKFILL SEEDING EMPTY USERS] ---")
    db = get_db()
    try:
        # Find all users *except* the system/template users
        users_to_check = db.query(DbUser).filter(
            DbUser.username != "default",
            DbUser.username != "guest_user"
        ).all()

        if not users_to_check:
            print("No user accounts found to check.")
            return

        print(f"Found {len(users_to_check)} user account(s) to check...")
        seeded_count = 0

        for user in users_to_check:
            # We will check and seed each user in their OWN transaction
            # This is safer for a live system.
            try:
                # The seeding function already contains the safety check
                # to see if the user is empty.
                print(f"Checking user: '{user.username}' (ID: {user.id})...")
                _seed_user_from_guest_data(db, user)

                # If the function added data, the session will be "dirty"
                if db.is_modified(user) or db.new or db.dirty:
                    db.commit()
                    print(f"   -> Successfully seeded '{user.username}'.")
                    seeded_count += 1
                else:
                    # This happens if the safety check was triggered
                    db.rollback()  # Rollback any potential flushes

            except Exception as e:
                db.rollback()
                print(f"   -> FAILED to seed '{user.username}'. Rolled back. Error: {e}")

        print("--- [BACKFILL COMPLETE] ---")
        print(f"✅ Successfully seeded {seeded_count} empty user account(s).")

    except Exception as e:
        db.rollback()
        print(f"❌ An unexpected error occurred: {e}")
        traceback.print_exc()
    finally:
        db.close()

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
    """
    Serve uploaded images.

    Compatible with:
    - Legacy notes where Trix stored /uploads/<old_username>/...
    - Photo ZIP imports that always extract into the *current* user's directory
    - Single-user installs that store everything under 'default'
    """

    candidate_dirs = []

    # 1) Directory that matches the URL segment (legacy behaviour)
    candidate_dirs.append(os.path.join(UPLOAD_FOLDER, username))

    # 2) In multi-user mode, also try the current user's directory.
    #    This fixes MU→MU migrations where the username changed:
    #    old HTML: /uploads/mrantonsG/..., new files: uploads/anton/...
    if not SINGLE_USER_MODE:
        current_name = getattr(current_user, "username", None)
        if current_name and current_name != username:
            candidate_dirs.append(os.path.join(UPLOAD_FOLDER, current_name))

    # 3) In single-user mode, fall back to "default" for legacy paths.
    if SINGLE_USER_MODE and username != "default":
        candidate_dirs.append(os.path.join(UPLOAD_FOLDER, "default"))

    for user_upload_dir in candidate_dirs:
        base_dir = os.path.abspath(user_upload_dir)
        target_path = os.path.abspath(os.path.join(user_upload_dir, filename))

        # Prevent path traversal
        if not target_path.startswith(base_dir + os.sep):
            continue

        if os.path.exists(target_path):
            return send_from_directory(user_upload_dir, filename)

    return "Not Found", 404

@app.route('/api/get_shared_items')
@login_required
def get_shared_items():
    if SINGLE_USER_MODE:
        return jsonify({"objects": [], "components": [], "views": [], "imported_object_ids": [], "imported_component_ids": [], "imported_view_ids": []})

    db = get_db()
    try:
        current_user_id = g.db_user.id

        # --- 1. Get ALL Shared Objects (Including own) ---
        shared_objects_db = db.query(AstroObject, DbUser.username).join(
            DbUser, AstroObject.user_id == DbUser.id
        ).filter(
            AstroObject.is_shared == True
            # Removed: AstroObject.user_id != current_user_id
        ).all()

        shared_objects_list = []
        for obj, username in shared_objects_db:
            shared_objects_list.append({
                "id": obj.id,
                "object_name": obj.object_name,
                "common_name": obj.common_name,
                "type": obj.type,
                "constellation": obj.constellation,
                "ra": obj.ra_hours,
                "dec": obj.dec_deg,
                "shared_by_user": username,
                "shared_notes": obj.shared_notes or ""
            })

        # --- 2. Get ALL Shared Components (Including own) ---
        shared_components_db = db.query(Component, DbUser.username).join(
            DbUser, Component.user_id == DbUser.id
        ).filter(
            Component.is_shared == True
            # Removed: Component.user_id != current_user_id
        ).all()

        shared_components_list = []
        for comp, username in shared_components_db:
            shared_components_list.append({
                "id": comp.id,
                "name": comp.name,
                "kind": comp.kind,
                "shared_by_user": username
            })

        # --- 3. Get ALL Shared Views (Including own) ---
        shared_views_db = db.query(SavedView, DbUser.username).join(
            DbUser, SavedView.user_id == DbUser.id
        ).filter(
            SavedView.is_shared == True
            # Removed: SavedView.user_id != current_user_id
        ).all()

        shared_views_list = []
        for view, username in shared_views_db:
            shared_views_list.append({
                "id": view.id,
                "name": view.name,
                "description": view.description,
                "shared_by_user": username
            })

        # --- 4. Get IDs of items ALREADY imported by CURRENT user ---
        imported_objects = db.query(AstroObject.original_item_id).filter(
            AstroObject.user_id == current_user_id,
            AstroObject.original_item_id != None
        ).all()
        imported_object_ids = {item_id for (item_id,) in imported_objects}

        imported_components = db.query(Component.original_item_id).filter(
            Component.user_id == current_user_id,
            Component.original_item_id != None
        ).all()
        imported_component_ids = {item_id for (item_id,) in imported_components}

        imported_views = db.query(SavedView.original_item_id).filter(
            SavedView.user_id == current_user_id,
            SavedView.original_item_id != None
        ).all()
        imported_view_ids = {item_id for (item_id,) in imported_views}

        return jsonify({
            "objects": shared_objects_list,
            "components": shared_components_list,
            "views": shared_views_list,
            "imported_object_ids": list(imported_object_ids),
            "imported_component_ids": list(imported_component_ids),
            "imported_view_ids": list(imported_view_ids)
        })
    except Exception as e:
        print(f"ERROR in get_shared_items: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@app.route('/api/import_item', methods=['POST'])
@login_required
def import_item():
    if SINGLE_USER_MODE:
        return jsonify({"status": "error", "message": "Sharing is disabled in single-user mode"}), 400

    db = get_db()
    try:
        data = request.get_json()
        item_id = data.get('id')
        item_type = data.get('type')  # 'object' or 'component'

        if not item_id or not item_type:
            return jsonify({"status": "error", "message": "Missing item ID or type"}), 400

        current_user_id = g.db_user.id

        if item_type == 'object':
            # --- Import an Object ---
            original_obj = db.query(AstroObject).filter_by(id=item_id, is_shared=True).one_or_none()
            if not original_obj or original_obj.user_id == current_user_id:
                return jsonify({"status": "error", "message": "Object not found or cannot import your own item"}), 404

            existing = db.query(AstroObject).filter_by(user_id=current_user_id,
                                                       object_name=original_obj.object_name).one_or_none()
            if existing:
                return jsonify({"status": "error",
                                "message": f"You already have an object named '{original_obj.object_name}'"}), 409

            new_obj = AstroObject(
                user_id=current_user_id,
                object_name=original_obj.object_name,
                common_name=original_obj.common_name,
                ra_hours=original_obj.ra_hours,
                dec_deg=original_obj.dec_deg,
                type=original_obj.type,
                constellation=original_obj.constellation,
                magnitude=original_obj.magnitude,
                size=original_obj.size,
                sb=original_obj.sb,
                shared_notes=original_obj.shared_notes,
                original_user_id=original_obj.user_id,
                original_item_id=original_obj.id,  # <-- THE FIX
                is_shared=False,
                project_name=""
            )
            db.add(new_obj)

        elif item_type == 'component':
            # --- Import a Component ---
            original_comp = db.query(Component).filter_by(id=item_id, is_shared=True).one_or_none()
            if not original_comp or original_comp.user_id == current_user_id:
                return jsonify(
                    {"status": "error", "message": "Component not found or cannot import your own item"}), 404

            existing = db.query(Component).filter_by(user_id=current_user_id, kind=original_comp.kind,
                                                     name=original_comp.name).one_or_none()
            if existing:
                return jsonify({"status": "error",
                                "message": f"You already have a {original_comp.kind} named '{original_comp.name}'"}), 409

            new_comp = Component(
                user_id=current_user_id,
                kind=original_comp.kind,
                name=original_comp.name,
                aperture_mm=original_comp.aperture_mm,
                focal_length_mm=original_comp.focal_length_mm,
                sensor_width_mm=original_comp.sensor_width_mm,
                sensor_height_mm=original_comp.sensor_height_mm,
                pixel_size_um=original_comp.pixel_size_um,
                factor=original_comp.factor,
                is_shared=False,
                original_user_id=original_comp.user_id,
                original_item_id=original_comp.id  # <-- THE FIX
            )
            db.add(new_comp)

        elif item_type == 'view':
            # --- Import a View ---
            original_view = db.query(SavedView).filter_by(id=item_id, is_shared=True).one_or_none()
            if not original_view:
                return jsonify({"status": "error", "message": "View not found"}), 404

            existing = db.query(SavedView).filter_by(user_id=current_user_id, name=original_view.name).one_or_none()
            if existing:
                return jsonify(
                    {"status": "error", "message": f"You already have a view named '{original_view.name}'"}), 409

            new_view = SavedView(
                user_id=current_user_id,
                name=original_view.name,
                description=original_view.description,
                settings_json=original_view.settings_json,
                is_shared=False,
                original_user_id=original_view.user_id,
                original_item_id=original_view.id
            )
            db.add(new_view)

        else:
            return jsonify({"status": "error", "message": "Invalid item type"}), 400

        db.commit()
        return jsonify({"status": "success", "message": f"{item_type.capitalize()} imported successfully!"})

    except Exception as e:
        db.rollback()
        print(f"ERROR in import_item: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": "An internal error occurred."}), 500
    finally:
        db.close()

@app.route('/upload_editor_image', methods=['POST'])
@login_required
def upload_editor_image():
    """
    Handles file uploads from the Trix editor.
    Saves the file to the user's upload directory and returns
    a JSON response with the file's public URL.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file part in request."}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400

    # Determine username
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    if file and allowed_file(file.filename):
        try:
            # Get the file extension
            file_extension = file.filename.rsplit('.', 1)[1].lower()

            # Generate a new, unique filename
            # e.g., "note_img_a1b2c3d4.jpg"
            new_filename = f"note_img_{uuid.uuid4().hex[:12]}.{file_extension}"

            # Create the user's upload directory if it doesn't exist
            user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
            os.makedirs(user_upload_dir, exist_ok=True)

            # Save the file
            save_path = os.path.join(user_upload_dir, new_filename)
            file.save(save_path)

            # Create the public URL for the image
            # This must match the `get_uploaded_image` route
            public_url = url_for('get_uploaded_image', username=username, filename=new_filename)

            # Trix expects a JSON response with a 'url' key
            return jsonify({"url": public_url})

        except Exception as e:
            print(f"Error uploading editor image: {e}")
            return jsonify({"error": f"Server error during upload: {e}"}), 500

    return jsonify({"error": "File type not allowed."}), 400


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


@app.cli.command("repair-image-links")
def repair_image_links_command():
    """
    Finds and repairs image URLs in Trix content.
    1. Converts absolute URLs (e.g., 'http://localhost/...') to
       portable relative URLs (e.g., '/uploads/...').
    2. If in SINGLE_USER_MODE, also rewrites all user-specific paths
       (e.g., '/uploads/mrantonSG/...') to the 'default' user path
       (e.g., '/uploads/default/...').
    """
    print("--- [REPAIRING BROKEN IMAGE LINKS (v2)] ---")
    db = get_db()

    # Regex 1: Fixes absolute URLs (e.g., http://.../uploads/...)
    # Groups: (1: http://host) (2: /uploads/user/img.jpg) (3: quote)
    abs_url_pattern = re.compile(r'(http[s]?://[^/"\']+)(/uploads/.*?)(["\'])')
    abs_replacement = r'\2\3'  # Replace with: /uploads/user/img.jpg"

    # Regex 2: Fixes user paths *only* if in Single-User Mode
    su_url_pattern = None
    su_replacement = None
    if SINGLE_USER_MODE:
        print("--- Running in Single-User Mode: Will also fix user paths to 'default' ---")
        # Groups: (1: /uploads/) (2: !default) (3: /img.jpg) (4: quote)
        # This regex finds any path in /uploads/ that is NOT 'default'
        su_url_pattern = re.compile(r'(/uploads/)(?!default/)([^/]+)(/.*?)(["\'])')
        su_replacement = r'\1default\3\4'  # Replace with: /uploads/default/img.jpg"

    total_objects_fixed = 0
    total_journals_fixed = 0

    try:
        all_users = db.query(DbUser).all()
        print(f"Found {len(all_users)} user(s) to check...")

        for user in all_users:
            print(f"--- Processing user: {user.username} ---")

            # 1. Fix AstroObject Notes (Private and Shared)
            objects_to_fix = db.query(AstroObject).filter_by(user_id=user.id).all()
            objects_fixed_count = 0
            for obj in objects_to_fix:
                fixed = False

                # --- Fix Private Notes ---
                notes = obj.project_name
                if notes and "/uploads/" in notes:
                    # Step 1: Fix absolute URLs
                    new_notes, count_abs = abs_url_pattern.subn(abs_replacement, notes)
                    # Step 2: Fix user paths (if in SU mode and pattern exists)
                    count_su = 0
                    if su_url_pattern and "/uploads/" in new_notes:
                        new_notes, count_su = su_url_pattern.subn(su_replacement, new_notes)

                    if count_abs > 0 or count_su > 0:
                        obj.project_name = new_notes
                        fixed = True

                # --- Fix Shared Notes ---
                shared_notes = obj.shared_notes
                if shared_notes and "/uploads/" in shared_notes:
                    # Step 1: Fix absolute URLs
                    new_shared_notes, count_abs = abs_url_pattern.subn(abs_replacement, shared_notes)
                    # Step 2: Fix user paths (if in SU mode)
                    count_su = 0
                    if su_url_pattern and "/uploads/" in new_shared_notes:
                        new_shared_notes, count_su = su_url_pattern.subn(su_replacement, new_shared_notes)

                    if count_abs > 0 or count_su > 0:
                        obj.shared_notes = new_shared_notes
                        fixed = True

                if fixed:
                    objects_fixed_count += 1

            if objects_fixed_count > 0:
                print(f"    Fixed links in {objects_fixed_count} AstroObject note(s).")
                total_objects_fixed += objects_fixed_count

            # 2. Fix JournalSession Notes
            sessions_to_fix = db.query(JournalSession).filter_by(user_id=user.id).all()
            sessions_fixed_count = 0
            for session in sessions_to_fix:
                notes = session.notes
                if notes and "/uploads/" in notes:
                    # Step 1: Fix absolute URLs
                    new_journal_notes, count_abs = abs_url_pattern.subn(abs_replacement, notes)
                    # Step 2: Fix user paths (if in SU mode)
                    count_su = 0
                    if su_url_pattern and "/uploads/" in new_journal_notes:
                        new_journal_notes, count_su = su_url_pattern.subn(su_replacement, new_journal_notes)

                    if count_abs > 0 or count_su > 0:
                        session.notes = new_journal_notes
                        sessions_fixed_count += 1

            if sessions_fixed_count > 0:
                print(f"    Fixed links in {sessions_fixed_count} JournalSession note(s).")
                total_journals_fixed += sessions_fixed_count

            if objects_fixed_count == 0 and sessions_fixed_count == 0:
                print("    No broken image links found for this user.")

        # Commit all changes for all users at the end
        db.commit()
        print("--- [REPAIR COMPLETE] ---")
        print(f"✅ Repaired links in {total_objects_fixed} object notes and {total_journals_fixed} journal notes.")
        print("Database has been updated with relative image paths.")

    except Exception as e:
        db.rollback()
        print(f"❌ FATAL ERROR: {e}")
        print("Database has been rolled back. No changes were saved.")
        traceback.print_exc()
    finally:
        db.close()

@app.cli.command("repair-corrupt-ids")
def repair_corrupt_ids_command():
    """
    Finds and repairs object IDs that were corrupted by the old
    over-aggressive normalization script (e.g., 'SH2129' -> 'SH 2-129').
    This script is RULE-BASED and fixes all matching corrupt patterns.
    It runs IN-PLACE on the database to fix the names
    and re-link all associated journal entries.
    """
    print("--- [EMERGENCY OBJECT ID REPAIR SCRIPT] ---")
    db = get_db()

    # --- THIS LIST IS NOW FIXED TO MATCH normalize_object_name ---
    repair_rules = [
        # IC 405 -> IC405
        (re.compile(r'^(IC)(\d+)$'), r'IC \2'),

        # SNR G180.0-01.7 -> SNRG180.001.7
        (re.compile(r'^(SNRG)(\d+\.\d+?)(\d+\.\d+)$'), r'SNR G\2-\3'), # (non-greedy)

        # LHA 120-N 70 -> LHA120N70
        (re.compile(r'^(LHA)(\d+)(N)(\d+)$'), r'LHA \2-\3 \4'), # (FIXED regex and replacement)

        # SH 2-129 -> SH2129
        (re.compile(r'^(SH2)(\d+)$'), r'SH 2-\2'),

        # TGU H1867 -> TGUH1867
        (re.compile(r'^(TGUH)(\d+)$'), r'TGU H\2'),

        # VDB 1 -> VDB1
        (re.compile(r'^(VDB)(\d+)$'), r'VDB \2'),

        # NGC 1976 -> NGC1976
        (re.compile(r'^(NGC)(\d+)$'), r'NGC \2'),

        # GUM 16 -> GUM16
        (re.compile(r'^(GUM)(\d+)$'), r'GUM \2'),

        # CTA 1 -> CTA1
        (re.compile(r'^(CTA)(\d+)$'), r'CTA \2'),

        # HB 3 -> HB3
        (re.compile(r'^(HB)(\d+)$'), r'HB \2'),

        # PN ARO 121 -> PNARO121
        (re.compile(r'^(PNARO)(\d+)$'), r'PN ARO \2'),

        # LIESTO 1 -> LIESTO1
        (re.compile(r'^(LIESTO)(\d+)$'), r'LIESTO \2'),

        # PK 081-14.1 -> PK08114.1
        (re.compile(r'^(PK)(\d+)(\d{2}\.\d+)$'), r'PK \2-\3'),

        # PN G093.3-02.4 -> PNG093.302.4
        (re.compile(r'^(PNG)(\d+\.\d+?)(\d+\.\d+)$'), r'PN G\2-\3'), # (non-greedy)

        # WR 134 -> WR134
        (re.compile(r'^(WR)(\d+)$'), r'WR \2'),

        # ABELL 21 -> ABELL21
        (re.compile(r'^(ABELL)(\d+)$'), r'ABELL \2'),

        # BARNARD 33 -> BARNARD33
        (re.compile(r'^(BARNARD)(\d+)$'), r'BARNARD \2'),
    ]

    try:
        all_users = db.query(DbUser).all()
        print(f"Found {len(all_users)} users to check...")
        total_repaired = 0

        for user in all_users:
            print(f"--- Processing user: {user.username} ---")

            # Get all objects for this user
            user_objects = db.query(AstroObject).filter_by(user_id=user.id).all()

            # Create a lookup of objects by their name for collision detection
            objects_by_name = {obj.object_name: obj for obj in user_objects}

            repaired_in_this_user = 0

            # Iterate over a copy of the list, as we may be modifying objects
            for obj_to_fix in list(user_objects):
                corrupt_name = obj_to_fix.object_name
                repaired_name = None

                # Apply rules to find a match
                for pattern, replacement in repair_rules:
                    if pattern.match(corrupt_name):
                        repaired_name = pattern.sub(replacement, corrupt_name)
                        break  # Stop on the first rule that matches

                # If we found a repair and it's different, apply it
                if repaired_name and repaired_name != corrupt_name:

                    # Check if the "repaired" name *already* exists (collision)
                    existing_correct_obj = objects_by_name.get(repaired_name)

                    if existing_correct_obj and existing_correct_obj.id != obj_to_fix.id:
                        # --- MERGE PATH ---
                        print(
                            f"    WARNING: Found '{corrupt_name}' and '{repaired_name}'. Merging corrupt into correct...")

                        # 1. Merge notes
                        if obj_to_fix.project_name:
                            notes_to_merge = obj_to_fix.project_name or ""
                            if not (not notes_to_merge or notes_to_merge.lower().strip() in ('none', '<div>none</div>',
                                                                                             'null')):
                                existing_correct_obj.project_name = (
                                                                                existing_correct_obj.project_name or "") + f"<br>---<br><em>(Merged from corrupt: {corrupt_name})</em><br>{notes_to_merge}"

                        # 2. Re-link journals that point to the corrupt name
                        db.query(JournalSession).filter_by(user_id=user.id, object_name=corrupt_name).update(
                            {'object_name': repaired_name})

                        # 3. Delete the corrupt object
                        db.delete(obj_to_fix)
                        print(f"      -> Merged and deleted '{corrupt_name}'.")

                    else:
                        # --- RENAME PATH ---
                        print(f"    Repairing: '{corrupt_name}' -> '{repaired_name}'")

                        # 1. Rename the object
                        obj_to_fix.object_name = repaired_name

                        # 2. Update all journal entries that pointed to the corrupt name
                        db.query(JournalSession).filter_by(user_id=user.id, object_name=corrupt_name).update(
                            {'object_name': repaired_name})

                        # 3. Update the lookup map for this user
                        objects_by_name[repaired_name] = obj_to_fix
                        if corrupt_name in objects_by_name:
                            del objects_by_name[corrupt_name]

                    total_repaired += 1
                    repaired_in_this_user += 1

            if repaired_in_this_user > 0:
                print(f"  Repaired {repaired_in_this_user} objects for this user.")
            else:
                print("  No corrupt IDs matching the repair rules were found for this user.")

        # Commit all changes for all users at the very end
        db.commit()
        print("--- [REPAIR COMPLETE] ---")
        print(f"✅ Repaired and re-linked {total_repaired} objects across all users.")
        print("Database corruption has been fixed.")

    except Exception as e:
        db.rollback()
        print(f"❌ FATAL ERROR: {e}")
        print("Database has been rolled back. No changes were saved.")
        traceback.print_exc()
    finally:
        db.close()


@app.cli.command("seed-guest-account")
def seed_guest_account_command():
    """
    Safely adds default rigs AND journal entries to the 'guest_user' account.
    This is for live systems to populate the demo account.
    V2: Cleans up guest account first and reads from TEMPLATE_DIR.
    """
    print("--- [SEEDING GUEST ACCOUNT (v2 - FIX)] ---")
    db = get_db()
    try:
        # 1. Find the guest_user
        guest_user = db.query(DbUser).filter_by(username="guest_user").one_or_none()
        if not guest_user:
            print("ERROR: 'guest_user' account not found. Cannot seed.")
            return
        print(f"Found 'guest_user' (ID: {guest_user.id}).")

        # 2. --- CLEAN UP FIRST ---
        # This is critical to remove your personal data from the guest account.
        print("Cleaning up any existing data from guest_user account...")
        db.query(Rig).filter_by(user_id=guest_user.id).delete()
        db.query(Component).filter_by(user_id=guest_user.id).delete()
        db.query(JournalSession).filter_by(user_id=guest_user.id).delete()
        db.commit()  # Commit the deletions
        print("...Cleanup complete.")

        # 3. --- Seed Rigs from TEMPLATES ---
        # Use TEMPLATE_DIR (config_templates), not CONFIG_DIR (instance/configs)
        rigs_template_path = os.path.join(TEMPLATE_DIR, "rigs_default.yaml")
        if os.path.exists(rigs_template_path):
            rigs_yaml, error = _read_yaml(rigs_template_path)
            if error:
                print(f"ERROR (Rigs): Could not read 'config_templates/rigs_default.yaml': {error}")
            else:
                print("Migrating components and rigs from template...")
                _migrate_components_and_rigs(db, guest_user, rigs_yaml, "guest_user")
                print("...Rigs seeded.")
        else:
            print("WARNING: 'config_templates/rigs_default.yaml' not found.")

        # 4. --- Seed Journal from TEMPLATES ---
        # Use TEMPLATE_DIR, not CONFIG_DIR
        journal_template_path = os.path.join(TEMPLATE_DIR, "journal_default.yaml")
        if os.path.exists(journal_template_path):
            journal_yaml, error = _read_yaml(journal_template_path)
            if error:
                print(f"ERROR (Journal): Could not read 'config_templates/journal_default.yaml': {error}")
            else:
                print("Migrating journal entries from template...")
                _migrate_journal(db, guest_user, journal_yaml)
                print("...Journal seeded.")
        else:
            print("WARNING: 'config_templates/journal_default.yaml' not found.")

        db.commit()  # Commit the additions
        print("--- [SEEDING COMPLETE] ---")
        print("✅ Successfully cleaned and populated the 'guest_user' account with demo data.")

    except Exception as e:
        db.rollback()
        print(f"❌ FATAL ERROR: {e}")
        print("Database has been rolled back. No changes were saved.")
        traceback.print_exc()
    finally:
        db.close()


@app.route('/api/save_framing', methods=['POST'])
@login_required
def save_framing():
    db = get_db()
    try:
        data = request.get_json()
        object_name = data.get('object_name')

        # Find existing framing or create new
        framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id,
            object_name=object_name
        ).one_or_none()

        if not framing:
            framing = SavedFraming(user_id=g.db_user.id, object_name=object_name)
            db.add(framing)

        # Update fields
        rig_id_val = int(data.get('rig')) if data.get('rig') else None

        # Lookup rig_name so we save it for portability (Backup/Restore)
        rig_name_val = None
        if rig_id_val:
            r = db.get(Rig, rig_id_val)
            if r:
                rig_name_val = r.rig_name

        framing.rig_id = rig_id_val
        framing.rig_name = rig_name_val  # <-- Important: Saves name for portability

        framing.ra = float(data.get('ra')) if data.get('ra') else None
        framing.dec = float(data.get('dec')) if data.get('dec') else None
        framing.rotation = float(data.get('rotation')) if data.get('rotation') else 0.0
        framing.survey = data.get('survey')
        framing.blend_survey = data.get('blend')
        framing.blend_opacity = float(data.get('blend_op')) if data.get('blend_op') else 0.0

        # Mosaic fields
        framing.mosaic_cols = int(data.get('mosaic_cols')) if data.get('mosaic_cols') else 1
        framing.mosaic_rows = int(data.get('mosaic_rows')) if data.get('mosaic_rows') else 1
        framing.mosaic_overlap = float(data.get('mosaic_overlap')) if data.get('mosaic_overlap') is not None else 10.0

        framing.updated_at = datetime.now(UTC)

        db.commit()
        return jsonify({"status": "success", "message": "Framing saved."})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()

@app.route('/api/get_framing/<path:object_name>')
@login_required
def get_framing(object_name):
    db = get_db()
    try:
        framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id,
            object_name=object_name
        ).one_or_none()

        if framing:
            # Helper: format mosaic columns if present
            return jsonify({
                "status": "found",
                "rig": framing.rig_id,
                "ra": framing.ra,
                "dec": framing.dec,
                "rotation": framing.rotation,
                "survey": framing.survey,
                "blend": framing.blend_survey,
                "blend_op": framing.blend_opacity,
                "mosaic_cols": framing.mosaic_cols or 1,
                "mosaic_rows": framing.mosaic_rows or 1,
                "mosaic_overlap": framing.mosaic_overlap if framing.mosaic_overlap is not None else 10
            })
        else:
            return jsonify({"status": "empty"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()


@app.route('/api/delete_framing', methods=['POST'])
@login_required
def delete_framing():
    db = get_db()
    try:
        data = request.get_json()
        object_name = data.get('object_name')

        framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id,
            object_name=object_name
        ).one_or_none()

        if framing:
            db.delete(framing)
            db.commit()
            return jsonify({"status": "success", "message": "Framing deleted."})
        else:
            return jsonify({"status": "error", "message": "No saved framing found."}), 404
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        db.close()


def heatmap_background_worker():
    """
    Background thread that gently checks for stale heatmap caches (older than 24h)
    and regenerates them chunk-by-chunk without blocking the CPU.
    """
    # Initial startup delay to let the app boot
    time.sleep(30)

    while True:
        print("[HEATMAP WORKER] Starting maintenance cycle...")

        try:
            # 1. Gather Tasks (Users & Locations)
            tasks = []
            with app.app_context():
                db = get_db()
                try:
                    users = db.query(DbUser).filter_by(active=True).all()
                    for u in users:
                        # Get User's Config for preferences
                        prefs = db.query(UiPref).filter_by(user_id=u.id).first()
                        user_cfg = {}
                        if prefs and prefs.json_blob:
                            try:
                                user_cfg = json.loads(prefs.json_blob)
                            except:
                                pass

                        # Get Active Locations
                        locs = db.query(Location).filter_by(user_id=u.id, active=True).all()

                        # Get Object Count (for cache key)
                        obj_count = db.query(AstroObject).filter_by(user_id=u.id).count()

                        for loc in locs:
                            tasks.append({
                                'user_id': u.id,
                                'obj_count': obj_count,
                                'loc_name': loc.name,
                                'lat': loc.lat,
                                'lon': loc.lon,
                                'tz': loc.timezone,
                                'mask': [[hp.az_deg, hp.alt_min_deg] for hp in loc.horizon_points],
                                'alt_threshold': user_cfg.get("altitude_threshold", 20)
                            })
                finally:
                    db.close()

            # 2. Process Tasks
            for task in tasks:
                user_id = task['user_id']
                loc_safe = task['loc_name'].lower().replace(' ', '_')
                obj_count = task['obj_count']

                # Check the timestamp of the LAST chunk (part11) as a proxy for the whole set
                # We use v5 cache naming
                base_filename = f"heatmap_v5_{user_id}_{loc_safe}_{obj_count}"
                last_chunk_path = os.path.join(CACHE_DIR, f"{base_filename}.part11.json")

                should_update = True
                if os.path.exists(last_chunk_path):
                    age = time.time() - os.path.getmtime(last_chunk_path)
                    if age < 86400:  # 24 Hours
                        should_update = False

                if should_update:
                    print(f"[HEATMAP WORKER] Updating stale cache for User {user_id} @ {task['loc_name']}...")

                    # --- REGENERATE ALL 12 CHUNKS ---
                    # We re-query objects inside the loop to be safe with DB contexts
                    with app.app_context():
                        db = get_db()
                        try:
                            all_objects = db.query(AstroObject).filter_by(user_id=user_id).all()
                            valid_objects = [o for o in all_objects if o.ra_hours is not None and o.dec_deg is not None]

                            # Filter Invisible (Geometric) - Critical for consistency
                            visible_objects = []
                            for obj in valid_objects:
                                dec = float(obj.dec_deg)
                                if (90 - abs(task['lat'] - dec)) >= task['alt_threshold']:
                                    visible_objects.append(obj)
                            visible_objects.sort(key=lambda x: float(x.ra_hours))

                            # Validate Timezone (Fix for 'Greenland/Sermersooq' crash)
                            try:
                                local_tz = pytz.timezone(task['tz'])
                                valid_tz = task['tz']
                            except Exception:
                                print(
                                    f"[HEATMAP WORKER] WARN: Invalid timezone '{task['tz']}' for '{task['loc_name']}'. Using UTC.")
                                local_tz = pytz.utc
                                valid_tz = 'UTC'

                            now = datetime.now(local_tz)
                            start_date_year = now.date() - timedelta(days=now.weekday())

                            # Loop 12 chunks
                            for chunk_idx in range(12):
                                weeks_per_chunk = 52 // 12
                                remainder = 52 % 12
                                start_week = chunk_idx * weeks_per_chunk + min(chunk_idx, remainder)
                                end_week = start_week + weeks_per_chunk + (1 if chunk_idx < remainder else 0)

                                weeks_x = []
                                target_dates = []
                                moon_phases = []

                                for i in range(start_week, end_week):
                                    d = start_date_year + timedelta(weeks=i)
                                    weeks_x.append(d.strftime('%b %d'))
                                    target_dates.append(d.strftime('%Y-%m-%d'))
                                    try:
                                        dt_moon = local_tz.localize(
                                            datetime.combine(d, datetime.min.time())).astimezone(pytz.utc)
                                        moon_phases.append(round(ephem.Moon(dt_moon).phase, 1))
                                    except:
                                        moon_phases.append(0)

                                z_scores_chunk = []
                                y_names, meta_ids, meta_active = [], [], []
                                meta_types, meta_cons, meta_mags, meta_sizes, meta_sbs = [], [], [], [], []

                                for obj in visible_objects:
                                    ra, dec = float(obj.ra_hours), float(obj.dec_deg)
                                    obj_scores = []
                                    for i, date_str in enumerate(target_dates):
                                        obs_dur, max_alt, _, _ = calculate_observable_duration_vectorized(
                                            ra, dec, task['lat'], task['lon'], date_str, valid_tz,
                                            task['alt_threshold'], 60, horizon_mask=task['mask']
                                        )
                                        score = 0
                                        duration_mins = obs_dur.total_seconds() / 60 if obs_dur else 0
                                        if max_alt is not None and max_alt >= task[
                                            'alt_threshold'] and duration_mins >= 45:
                                            norm_alt = min(
                                                (max_alt - task['alt_threshold']) / (90 - task['alt_threshold']), 1.0)
                                            norm_dur = min(duration_mins / 480, 1.0)
                                            score = (0.4 * norm_alt + 0.6 * norm_dur) * 100
                                            if moon_phases[i] > 60:
                                                score *= (1 - ((moon_phases[i] - 60) / 40) * 0.9)
                                        obj_scores.append(round(score, 1))

                                    z_scores_chunk.append(obj_scores)

                                    # Metadata
                                    dname = obj.common_name or obj.object_name
                                    if obj.type: dname += f" [{obj.type}]"
                                    y_names.append(dname)
                                    meta_ids.append(obj.object_name)
                                    meta_active.append(1 if obj.active_project else 0)
                                    meta_types.append(str(obj.type or ""))
                                    meta_cons.append(str(obj.constellation or ""))
                                    try:
                                        meta_mags.append(float(obj.magnitude))
                                    except:
                                        meta_mags.append(999.0)
                                    try:
                                        meta_sizes.append(float(obj.size))
                                    except:
                                        meta_sizes.append(0.0)
                                    try:
                                        meta_sbs.append(float(obj.sb))
                                    except:
                                        meta_sbs.append(999.0)

                                chunk_data = {
                                    "chunk_index": chunk_idx, "x": weeks_x, "z_chunk": z_scores_chunk,
                                    "y": y_names, "moon_phases": moon_phases, "ids": meta_ids, "active": meta_active,
                                    "dates": target_dates, "types": meta_types, "cons": meta_cons,
                                    "mags": meta_mags, "sizes": meta_sizes, "sbs": meta_sbs
                                }

                                # Save Chunk
                                chunk_filename = os.path.join(CACHE_DIR, f"{base_filename}.part{chunk_idx}.json")
                                with open(chunk_filename, 'w') as f:
                                    json.dump(chunk_data, f)

                                # Sleep briefly between chunks to yield CPU
                                time.sleep(2)

                        finally:
                            db.close()

                    print(f"[HEATMAP WORKER] Finished updating {task['loc_name']}.")
                    # Sleep between locations
                    time.sleep(30)

        except Exception as e:
            print(f"[HEATMAP WORKER] Error in maintenance cycle: {e}")
            # traceback.print_exc() # Uncomment to debug

        # Sleep 4 hours before next check
        print("[HEATMAP WORKER] Cycle done. Sleeping 4 hours.")
        time.sleep(4 * 60 * 60)


# --- Mobile Mosaic Helper ---
def _format_ra_asiair(ra_deg):
    total_sec = (ra_deg / 15.0) * 3600
    h = int(total_sec // 3600)
    m = int((total_sec % 3600) // 60)
    s = total_sec % 60
    return f"{h}h {m}m {s:.2f}s"


def _format_dec_asiair(dec_deg):
    sign = '+' if dec_deg >= 0 else '-'
    abs_dec = abs(dec_deg)
    d = int(abs_dec)
    m = int((abs_dec - d) * 60)
    s = ((abs_dec - d) * 60 - m) * 60
    return f"{sign}{d}° {m}' {s:.2f}\""


import math


@app.route('/m/mosaic/<path:object_name>')
@login_required
def mobile_mosaic_view(object_name):
    """Mobile-optimized page to copy the ASIAIR mosaic plan string."""
    db = get_db()
    try:
        framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id, object_name=object_name
        ).one_or_none()

        if not framing:
            return f"<h3>No saved framing found for {object_name}</h3><p>Please save a framing on the desktop first.</p>"

        # Get Rig Data
        rig = db.get(Rig, framing.rig_id) if framing.rig_id else None
        if not rig or not rig.fov_w_arcmin:
            return "<h3>Error: Rig data missing in saved framing.</h3>"

        # Math Setup
        fov_w_deg = rig.fov_w_arcmin / 60.0
        # If height is missing (older DBs), estimate based on sensor ratio or square
        fov_h_deg = (rig.fov_w_arcmin / 60.0)  # Default square if missing

        # Try to be precise if components exist
        if rig.camera and rig.camera.sensor_height_mm and rig.effective_focal_length:
            fov_h_deg = math.degrees(2 * math.atan((rig.camera.sensor_height_mm / 2.0) / rig.effective_focal_length))

        cols = framing.mosaic_cols or 1
        rows = framing.mosaic_rows or 1
        overlap = (framing.mosaic_overlap or 10.0) / 100.0

        w_step = fov_w_deg * (1 - overlap)
        h_step = fov_h_deg * (1 - overlap)

        # Invert angle for CW rotation to match frontend
        rot_rad = math.radians(-(framing.rotation or 0))
        center_ra_rad = math.radians(framing.ra)
        center_dec_rad = math.radians(framing.dec)

        # Tangent Plane Projection (Matches JS logic)
        cX = math.cos(center_dec_rad) * math.cos(center_ra_rad)
        cY = math.cos(center_dec_rad) * math.sin(center_ra_rad)
        cZ = math.sin(center_dec_rad)
        eX = -math.sin(center_ra_rad);
        eY = math.cos(center_ra_rad);
        eZ = 0
        nX = -math.sin(center_dec_rad) * math.cos(center_ra_rad)
        nY = -math.sin(center_dec_rad) * math.sin(center_ra_rad)
        nZ = math.cos(center_dec_rad)

        output_lines = []
        base_name = object_name.replace(" ", "_")
        pane_count = 1

        for r in range(rows):
            for c in range(cols):
                cx_off = (c - (cols - 1) / 2.0) * w_step
                cy_off = (r - (rows - 1) / 2.0) * h_step

                # Rotation
                rx = cx_off * math.cos(rot_rad) - cy_off * math.sin(rot_rad)
                ry = cx_off * math.sin(rot_rad) + cy_off * math.cos(rot_rad)

                # De-projection
                dx = math.radians(-rx)  # Negate X for RA
                dy = math.radians(ry)
                rad = math.hypot(dx, dy)

                if rad < 1e-9:
                    p_ra = framing.ra
                    p_dec = framing.dec
                else:
                    sinC = math.sin(rad);
                    cosC = math.cos(rad)
                    dirX = (dx * eX + dy * nX) / rad
                    dirY = (dx * eY + dy * nY) / rad
                    dirZ = (dx * eZ + dy * nZ) / rad

                    pX = cosC * cX + sinC * dirX
                    pY = cosC * cY + sinC * dirY
                    pZ = cosC * cZ + sinC * dirZ

                    ra_rad_res = math.atan2(pY, pX)
                    if ra_rad_res < 0: ra_rad_res += 2 * math.pi
                    p_ra = math.degrees(ra_rad_res)
                    p_dec = math.degrees(math.asin(pZ))

                output_lines.append(f"{base_name}_P{pane_count}")
                output_lines.append(f"RA: {_format_ra_asiair(p_ra)} DEC: {_format_dec_asiair(p_dec)}")
                pane_count += 1

        full_text = "\n".join(output_lines)

        # Determine back link based on source
        source = request.args.get('from')
        if source == 'outlook':
            back_url = url_for('mobile_outlook')
        else:
            # Default to up_now for direct access or 'up_now' source
            back_url = url_for('mobile_up_now')

        return render_template('mobile_mosaic_copy.html',
                               object_name=object_name,
                               mosaic_text=full_text,
                               info=f"{cols}x{rows} Mosaic @ {framing.rotation}°",
                               back_url=back_url)

    finally:
        db.close()

# =============================================================================
# Main Entry Point
# =============================================================================
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
    # Use a lock to protect a "flag file" check
    startup_lock_path = os.path.join(INSTANCE_PATH, "startup.lock")
    tasks_ran_flag_path = os.path.join(INSTANCE_PATH, "startup.done")

    # --- ONE-TIME INITIALIZATION (Runs only once per install/wipe) ---
    with _FileLock(startup_lock_path):
        if not os.path.exists(tasks_ran_flag_path):
            print("[STARTUP] Acquired lock, startup flag not found. Running one-time init tasks...")

            print("[STARTUP] Pre-fetching Earth rotation data (finals2000A.all)...")
            try:
                load_root = Loader('.')
                load_root.download('finals2000A.all')
                load_root.download('Leap_Second.dat')
                if os.path.exists(CACHE_DIR):
                    load_cache = Loader(CACHE_DIR)
                    load_cache.download('finals2000A.all')
                    load_cache.download('Leap_Second.dat')
            except Exception as e:
                print(f"[STARTUP] WARN: Skyfield pre-fetch failed: {e}")

            migrate_journal_data()

            # Note: Cache warming should ideally run per-worker or be managed differently,
            # but we keep it here to avoid load spikes on restart.
            trigger_startup_cache_workers()

            try:
                with open(tasks_ran_flag_path, 'w') as f:
                    f.write(datetime.now(timezone.utc).isoformat())
            except Exception as e:
                print(f"[STARTUP] CRITICAL: Could not write startup flag file! Error: {e}")

            print("[STARTUP] One-time init complete. Created flag. Releasing lock.")
        else:
            print("[STARTUP] Startup flag file found. Skipping init tasks.")

    # --- BACKGROUND THREADS (Single-Worker Locking) ---
    # We use a non-blocking file lock to ensure only ONE gunicorn worker
    # spawns the background threads, preventing duplicate logs/tasks.

    scheduler_lock_path = os.path.join(INSTANCE_PATH, "scheduler.lock")
    scheduler_lock_fh = None
    should_start_threads = False

    if _HAS_FCNTL:
        try:
            # Open file for locking. We keep this file handle OPEN to hold the lock.
            # If the worker dies, the OS releases the lock automatically.
            scheduler_lock_fh = open(scheduler_lock_path, "a+")
            fcntl.flock(scheduler_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            should_start_threads = True
            print("[STARTUP] Acquired scheduler lock. This worker will run background tasks.")
        except (IOError, BlockingIOError):
            print("[STARTUP] Scheduler lock held by another worker. Skipping background threads.")
            # Close the handle since we didn't get the lock
            if scheduler_lock_fh:
                try:
                    scheduler_lock_fh.close()
                except:
                    pass
    else:
        # Fallback for Windows or non-POSIX systems (Always start to ensure functionality)
        should_start_threads = True

    if should_start_threads:
        print("[STARTUP] Starting background update check thread...")
        update_thread = threading.Thread(target=check_for_updates)
        update_thread.daemon = True
        update_thread.start()

        print("[STARTUP] Starting background weather worker thread...")
        weather_thread = threading.Thread(target=weather_cache_worker)
        weather_thread.daemon = True
        weather_thread.start()

        print("[STARTUP] Starting background heatmap maintenance thread...")
        heatmap_thread = threading.Thread(target=heatmap_background_worker)
        heatmap_thread.daemon = True
        heatmap_thread.start()
if __name__ == '__main__':
    # Automatically disable debugger and reloader if set by the updater
    disable_debug = os.environ.get("NOVA_NO_DEBUG") == "1"

    app.run(
        debug=not disable_debug,
        use_reloader=False,
        # use_reloader=not disable_debug,
        host='0.0.0.0',
        port=5001
    )
