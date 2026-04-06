"""
Nova DSO Tracker
------------------------
This application provides endpoints to fetch and plot astronomical data
based on user-specific configuration details (e.g., locations and objects).
It uses Astroquery, Astropy, Ephem, and Matplotlib to calculate object altitudes,
transit times, and generate altitude curves for both celestial objects and the Moon.
It also integrates Flask-Login for user authentication.

March 2025, Anton Gutscher/cle

"""

# =============================================================================
# Imports
# =============================================================================
import os
from decouple import config as decouple_config
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
import warnings
from modules.config_validation import validate_config
import uuid
from pathlib import Path
import platform
import markdown
import csv
from math import atan, degrees
from flask import render_template, jsonify, request, send_file, redirect, url_for, flash, g, current_app, make_response, Response, stream_with_context
from flask_login import login_user, login_required, current_user, logout_user
from flask import session
from flask import Flask, send_from_directory, has_request_context
from flask_babel import Babel, gettext as _
import math
from astroquery.simbad import Simbad
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body, get_constellation, FK5, search_around_sky
from astropy.time import Time
from astropy.utils import iers
import astropy.units as u

# Disable IERS auto-download to speed up startup (uses bundled data instead)
# Precision loss is negligible for amateur astronomy (~milliseconds)
iers.conf.auto_download = False
iers.conf.auto_max_age = None  # Allow using old IERS data without errors

from flask_wtf.csrf import CSRFProtect
from sqlalchemy import text, func
from sqlalchemy.orm import selectinload
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

# === DB: Unified SQLAlchemy setup ============================================
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, Date,
    ForeignKey, Text, UniqueConstraint, and_, or_
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session
import bleach
from bleach.css_sanitizer import CSSSanitizer

try:
    import fcntl
except ImportError:
    fcntl = None

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

import re

from nova.models import (
    INSTANCE_PATH, DB_PATH, DB_URI, engine, SessionLocal, Base,
    DbUser, Project, SavedView, Location, SavedFraming, HorizonPoint,
    AstroObject, Component, Rig, JournalSession, UiPref, UserCustomFilter
)
from nova.config import (
    APP_VERSION, TEMPLATE_DIR, CACHE_DIR, CONFIG_DIR, BACKUP_DIR,
    UPLOAD_FOLDER, ENV_FILE, FIRST_RUN_ENV_CREATED, SINGLE_USER_MODE,
    SECRET_KEY, STELLARIUM_ERROR_MESSAGE, NOVA_CATALOG_URL,
    ALLOWED_EXTENSIONS, MAX_ACTIVE_LOCATIONS, SENTRY_DSN,
    static_cache, moon_separation_cache, nightly_curves_cache,
    cache_worker_status, monthly_top_targets_cache, config_cache,
    config_mtime, journal_cache, journal_mtime, LATEST_VERSION_INFO,
    rig_data_cache, weather_cache, CATALOG_MANIFEST_CACHE,
    _telemetry_startup_once, TELEMETRY_DEBUG_STATE, TRANSLATION_STATUS,
    AI_PROVIDER, AI_API_KEY, AI_MODEL, AI_BASE_URL, AI_ALLOWED_USERS
)
from nova.helpers import (
    get_db, get_user_log_string, allowed_file, _yaml_dump_pretty,
    _mkdirp, _backup_with_rotation, _atomic_write_yaml, _FileLock,
    to_yaml_filter, safe_float, safe_int, convert_to_native_python,
    load_effective_settings,
    get_imaging_criteria, _HAS_FCNTL,
    save_log_to_filesystem, read_log_content,
    calculate_dither_recommendation, dither_display,
    # Moved from __init__.py for blueprint migration
    generate_session_id, _compute_rig_metrics_from_components,
    load_full_astro_context, get_ra_dec,
    # Additional helpers extracted
    normalize_object_name, _parse_float_from_request, sort_rigs
)
from nova.config import DEFAULT_DITHER_MAIN_SHIFT_PX
from nova.report_graphs import generate_session_charts
from nova.workers.weather import weather_cache_worker
from nova.workers.updates import check_for_updates
from nova.workers.heatmap import heatmap_background_worker
from nova.workers.iers import iers_refresh_worker
from nova.analytics import record_event, record_login
from nova.auth import db, User, login_manager, init_auth, UserMixin  # noqa: F401

from nova.blueprints.core import core_bp
from nova.blueprints.api import api_bp
from nova.blueprints.journal import journal_bp
from nova.blueprints.mobile import mobile_bp
from nova.blueprints.projects import projects_bp
from nova.blueprints.tools import tools_bp
from nova.ai.routes import register_ai_blueprint


# =============================================================================
# GLOBAL CONSTANTS
# =============================================================================
# HTTP Request Timeouts (seconds)
DEFAULT_HTTP_TIMEOUT = 10       # Standard timeout for most HTTP requests
TELEMETRY_TIMEOUT = 5           # Shorter timeout for telemetry pings
from nova.config import SIMBAD_TIMEOUT  # Timeout for SIMBAD queries

# Scoring Constants
SCORING_WINDOW_SECONDS = 43200  # 12 hours in seconds - max observable duration for scoring


def get_weather_data_single_attempt(url: str, lat: float, lon: float) -> dict | None:
    """
    Fetches weather data from a single URL with robust error handling.
    Returns a dictionary on success, None on any failure.
    """
    try:
        r = None
        # Use a reasonable timeout (e.g., 10 seconds)
        r = requests.get(url, timeout=DEFAULT_HTTP_TIMEOUT)

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
        response_text = getattr(r, 'text', '<no response object>')
        print(f"[Weather Func] Response text (first 200 chars): {response_text[:200]}")
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


# --- MODELS (imported from nova.models) ---


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
                is_shared=False, shared_notes=None, original_user_id=None, original_item_id=None,
                # Ensure inspiration metadata is carried over during provisioning
                image_url=g_obj.image_url,
                image_credit=g_obj.image_credit,
                image_source_link=g_obj.image_source_link,
                description_text=g_obj.description_text,
                description_credit=g_obj.description_credit,
                description_source_link=g_obj.description_source_link
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
    For 'guest_user' specifically, it seeds from the YAML files to ensure the template source exists.
    """
    if not username:
        return None

    # Try to find the user in our application database
    user = db_session.query(DbUser).filter_by(username=username).one_or_none()

    # --- HELPER: Logic to seed guest_user from YAML ---
    def _seed_guest_from_yaml(target_user):
        try:
            print(f"[PROVISIONING] Seeding '{target_user.username}' directly from YAML files...")
            cfg_path = os.path.join(CONFIG_DIR, "config_guest_user.yaml")
            rigs_path = os.path.join(CONFIG_DIR, "rigs_guest_user.yaml")
            jrn_path = os.path.join(CONFIG_DIR, "journal_guest_user.yaml")

            cfg_data, _ = _read_yaml(cfg_path)
            rigs_data, _ = _read_yaml(rigs_path)
            jrn_data, _ = _read_yaml(jrn_path)

            if cfg_data:
                _migrate_locations(db_session, target_user, cfg_data)
                _migrate_objects(db_session, target_user, cfg_data)
                _migrate_components_and_rigs(db_session, target_user, rigs_data, target_user.username)
                _migrate_saved_framings(db_session, target_user, cfg_data)
                _migrate_journal(db_session, target_user, jrn_data)
                _migrate_ui_prefs(db_session, target_user, cfg_data)
                print(f"   -> [SEEDING] YAML import complete.")
            else:
                print(f"   -> [SEEDING] WARNING: config_guest_user.yaml empty or missing.")
        except Exception as e:
            print(f"   -> [SEEDING] ERROR importing from YAML: {e}")
            raise e

    if user:
        # REPAIR: If this is the guest_user but they have NO locations (broken state), re-seed from YAML.
        if username == "guest_user":
            loc_count = db_session.query(Location).filter_by(user_id=user.id).count()
            if loc_count == 0:
                print(f"[PROVISIONING] Detected empty guest_user. Attempting repair from YAML...")
                _seed_guest_from_yaml(user)
                db_session.commit()
        return user

    # --- New User Provisioning Path ---
    try:
        print(f"[PROVISIONING] User '{username}' not found in app.db. Creating new record.")
        new_user = DbUser(username=username)
        db_session.add(new_user)
        db_session.flush()  # Flush to get the new_user.id before seeding

        print(f"   -> User record created with ID {new_user.id}. Now seeding data...")

        if username == "guest_user":
            # Guest user MUST be seeded from disk (YAML) to act as the template for others
            _seed_guest_from_yaml(new_user)
        else:
            # Normal users are seeded from the database template (the guest_user record)
            _seed_user_from_guest_data(db_session, new_user)

        db_session.commit()

        print(f"   -> Successfully provisioned and seeded '{username}'.")
        return db_session.query(DbUser).filter_by(username=username).one()

    except Exception as e:
        db_session.rollback()
        print(f"   -> FAILED to provision '{username}'. Rolled back. Error: {e}")
        traceback.print_exc()
        return None

def _run_schema_patches(conn):
    """
    Run all schema patches against an existing database connection.
    This function is extracted for testability - tests can pass a connection
    to a minimal baseline database and verify all patches apply correctly.

    Args:
        conn: An active SQLAlchemy connection with an open transaction
    """
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
    if "rig_stable_uid_snapshot" not in colnames_journal:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN rig_stable_uid_snapshot VARCHAR(36);")
        print("[DB PATCH] Added missing column journal_sessions.rig_stable_uid_snapshot")

    # --- Log content columns for ASIAIR, PHD2, and NINA log analysis ---
    if "asiair_log_content" not in colnames_journal:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN asiair_log_content TEXT;")
        print("[DB PATCH] Added missing column journal_sessions.asiair_log_content")
    if "phd2_log_content" not in colnames_journal:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN phd2_log_content TEXT;")
        print("[DB PATCH] Added missing column journal_sessions.phd2_log_content")
    if "nina_log_content" not in colnames_journal:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN nina_log_content TEXT;")
        print("[DB PATCH] Added missing column journal_sessions.nina_log_content")
    if "log_analysis_cache" not in colnames_journal:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN log_analysis_cache TEXT;")
        print("[DB PATCH] Added missing column journal_sessions.log_analysis_cache")

    # --- Draft session support ---
    if "draft" not in colnames_journal:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN draft BOOLEAN DEFAULT 0;")
        print("[DB PATCH] Added missing column journal_sessions.draft")

    # --- Drop old global unique index on external_id ---
    # This index made external_id globally unique, but it should be unique per user
    try:
        conn.exec_driver_sql("DROP INDEX IF EXISTS uq_journal_external_id;")
        print("[DB PATCH] Dropped old global unique index uq_journal_external_id")
    except Exception as idx_err:
        print(f"[DB PATCH] Could not drop old index (may not exist): {idx_err}")

    # --- Deduplicate external_id values per user before creating composite unique index ---
    # Only deduplicate within each user, since external_id should be unique per user
    dup_check = conn.exec_driver_sql("""
        SELECT user_id, external_id, COUNT(*) as cnt
        FROM journal_sessions
        WHERE external_id IS NOT NULL
        GROUP BY user_id, external_id
        HAVING COUNT(*) > 1
    """).fetchall()

    if dup_check:
        print(f"[DB PATCH] Found {len(dup_check)} duplicate external_id value(s) per user. Resolving...")
        for (user_id, ext_id, count) in dup_check:
            # Get all rows with this duplicate external_id for this user (keep the one with lowest id)
            rows = conn.exec_driver_sql(
                "SELECT id FROM journal_sessions WHERE user_id = ? AND external_id = ? ORDER BY id",
                (user_id, ext_id)
            ).fetchall()
            # Regenerate external_id for all but the first (oldest) row
            for row in rows[1:]:
                new_id = uuid.uuid4().hex
                conn.exec_driver_sql(
                    "UPDATE journal_sessions SET external_id = ? WHERE id = ?",
                    (new_id, row[0])
                )
                print(f"[DB PATCH] Regenerated external_id for journal_sessions.id={row[0]} (was '{ext_id}' -> '{new_id}')")

    # --- Create composite unique index on (user_id, external_id) ---
    # This ensures external_id is unique per user, allowing different users to have the same external_id
    try:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_user_external_id ON journal_sessions(user_id, external_id) WHERE external_id IS NOT NULL;"
        )
        print("[DB PATCH] Created composite unique index uq_journal_user_external_id on journal_sessions(user_id, external_id)")
    except Exception as idx_err:
        print(f"[DB PATCH] Could not create journal user_external_id index: {idx_err}")

    # --- PERFORMANCE: Add Composite Index for Journal Sessions ---
    try:
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_journal_user_date ON journal_sessions(user_id, date_utc DESC);"
        )
        print("[DB PATCH] Created performance index idx_journal_user_date on journal_sessions")
    except Exception as idx_err:
        print(f"[DB PATCH] Could not create journal user_date index: {idx_err}")

    # --- PERFORMANCE: Add Index for Object Name Lookups ---
    try:
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_journal_object_name ON journal_sessions(object_name);"
        )
        print("[DB PATCH] Created performance index idx_journal_object_name on journal_sessions")
    except Exception as idx_err:
        print(f"[DB PATCH] Could not create journal object_name index: {idx_err}")

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

    # --- PERFORMANCE: Add Index for Saved Views Name Lookups ---
    try:
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_saved_views_name ON saved_views(name);"
        )
        print("[DB PATCH] Created performance index idx_saved_views_name on saved_views")
    except Exception as idx_err:
        print(f"[DB PATCH] Could not create saved_views name index: {idx_err}")

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
        # Image Adjustment columns
        if "img_brightness" not in colnames_framing:
            conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN img_brightness FLOAT DEFAULT 0.0;")
            print("[DB PATCH] Added missing column saved_framings.img_brightness")
        if "img_contrast" not in colnames_framing:
            conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN img_contrast FLOAT DEFAULT 0.0;")
            print("[DB PATCH] Added missing column saved_framings.img_contrast")
        if "img_gamma" not in colnames_framing:
            conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN img_gamma FLOAT DEFAULT 1.0;")
            print("[DB PATCH] Added missing column saved_framings.img_gamma")
        if "img_saturation" not in colnames_framing:
            conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN img_saturation FLOAT DEFAULT 0.0;")
            print("[DB PATCH] Added missing column saved_framings.img_saturation")
        # Overlay Preference columns
        if "geo_belt_enabled" not in colnames_framing:
            conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN geo_belt_enabled BOOLEAN DEFAULT 1;")
            print("[DB PATCH] Added missing column saved_framings.geo_belt_enabled")
        # Stable UID for cross-boundary rig resolution
        if "rig_stable_uid" not in colnames_framing:
            conn.exec_driver_sql("ALTER TABLE saved_framings ADD COLUMN rig_stable_uid VARCHAR(36);")
            print("[DB PATCH] Added missing column saved_framings.rig_stable_uid")
    except Exception as e:
        # Table might not exist yet if it's a fresh install, which is fine
        print(f"[DB PATCH] SavedFraming table patch skipped (may not exist yet): {e}")

    # --- Add stable_uid column to 'locations' table ---
    cols_locations = conn.exec_driver_sql("PRAGMA table_info(locations);").fetchall()
    colnames_locations = {row[1] for row in cols_locations}
    if "stable_uid" not in colnames_locations:
        conn.exec_driver_sql("ALTER TABLE locations ADD COLUMN stable_uid VARCHAR(36);")
        print("[DB PATCH] Added missing column locations.stable_uid")
    if "bortle_scale" not in colnames_locations:
        conn.exec_driver_sql("ALTER TABLE locations ADD COLUMN bortle_scale INTEGER;")
        print("[DB PATCH] Added missing column locations.bortle_scale")

    # --- Add new columns to 'components' table ---
    cols_components = conn.exec_driver_sql("PRAGMA table_info(components);").fetchall()
    colnames_components = {row[1] for row in cols_components}

    if "stable_uid" not in colnames_components:
        conn.exec_driver_sql("ALTER TABLE components ADD COLUMN stable_uid VARCHAR(36);")
        print("[DB PATCH] Added missing column components.stable_uid")

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

    # --- Add guide optics columns to 'rigs' table for dither recommendations ---
    cols_rigs = conn.exec_driver_sql("PRAGMA table_info(rigs);").fetchall()
    colnames_rigs = {row[1] for row in cols_rigs}

    if "stable_uid" not in colnames_rigs:
        conn.exec_driver_sql("ALTER TABLE rigs ADD COLUMN stable_uid VARCHAR(36);")
        print("[DB PATCH] Added missing column rigs.stable_uid")

    # Legacy guide optics columns (kept for backwards compatibility, but no longer used)
    if "guide_scope_name" not in colnames_rigs:
        conn.exec_driver_sql("ALTER TABLE rigs ADD COLUMN guide_scope_name VARCHAR(256);")
        print("[DB PATCH] Added missing column rigs.guide_scope_name")
    if "guide_focal_length_mm" not in colnames_rigs:
        conn.exec_driver_sql("ALTER TABLE rigs ADD COLUMN guide_focal_length_mm FLOAT;")
        print("[DB PATCH] Added missing column rigs.guide_focal_length_mm")
    if "guide_camera_name" not in colnames_rigs:
        conn.exec_driver_sql("ALTER TABLE rigs ADD COLUMN guide_camera_name VARCHAR(256);")
        print("[DB PATCH] Added missing column rigs.guide_camera_name")
    if "guide_pixel_size_um" not in colnames_rigs:
        conn.exec_driver_sql("ALTER TABLE rigs ADD COLUMN guide_pixel_size_um FLOAT;")
        print("[DB PATCH] Added missing column rigs.guide_pixel_size_um")

    # New FK-based guide optics columns
    if "guide_telescope_id" not in colnames_rigs:
        conn.exec_driver_sql("ALTER TABLE rigs ADD COLUMN guide_telescope_id INTEGER;")
        print("[DB PATCH] Added missing column rigs.guide_telescope_id")
    if "guide_camera_id" not in colnames_rigs:
        conn.exec_driver_sql("ALTER TABLE rigs ADD COLUMN guide_camera_id INTEGER;")
        print("[DB PATCH] Added missing column rigs.guide_camera_id")
    if "guide_is_oag" not in colnames_rigs:
        conn.exec_driver_sql("ALTER TABLE rigs ADD COLUMN guide_is_oag BOOLEAN DEFAULT 0 NOT NULL;")
        print("[DB PATCH] Added missing column rigs.guide_is_oag")

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

    if "enabled" not in colnames_objects:
        conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN enabled BOOLEAN DEFAULT 1;")
        print("[DB PATCH] Added missing column astro_objects.enabled")

        # Curation Fields Patch
    if "image_url" not in colnames_objects:
        conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN image_url VARCHAR(500);")
        conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN image_credit VARCHAR(256);")
        conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN image_source_link VARCHAR(500);")
        print("[DB PATCH] Added image curation columns")

    if "description_text" not in colnames_objects:
        conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN description_text TEXT;")
        conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN description_credit VARCHAR(256);")
        conn.exec_driver_sql("ALTER TABLE astro_objects ADD COLUMN description_source_link VARCHAR(500);")
        print("[DB PATCH] Added description curation columns")

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

    # --- Structured Dither Fields Patches ---
    cols_journal_sessions = conn.exec_driver_sql("PRAGMA table_info(journal_sessions);").fetchall()
    colnames_journal_sessions = {row[1] for row in cols_journal_sessions}

    if "dither_pixels" not in colnames_journal_sessions:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN dither_pixels INTEGER;")
        print("[DB PATCH] Added missing column journal_sessions.dither_pixels")
    if "dither_every_n" not in colnames_journal_sessions:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN dither_every_n INTEGER;")
        print("[DB PATCH] Added missing column journal_sessions.dither_every_n")
    if "dither_notes" not in colnames_journal_sessions:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN dither_notes VARCHAR(256);")
        print("[DB PATCH] Added missing column journal_sessions.dither_notes")
    # --- End Structured Dither Fields Patches ---

    # Indexes for frequently filtered columns
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_locations_active ON locations(active);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_locations_is_default ON locations(is_default);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_astro_objects_object_name ON astro_objects(object_name);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_rigs_rig_name ON rigs(rig_name);")

    # --- User Custom Filters Table ---
    user_custom_filters_exists = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='user_custom_filters';"
    ).fetchone()
    if not user_custom_filters_exists:
        conn.exec_driver_sql("""
            CREATE TABLE user_custom_filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                filter_key TEXT NOT NULL,
                filter_label TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, filter_key)
            );
        """)
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_user_custom_filters_user_id ON user_custom_filters(user_id);")
        print("[DB PATCH] Created user_custom_filters table")

    # --- Custom Filter Data column on journal_sessions ---
    if "custom_filter_data" not in colnames_journal:
        conn.exec_driver_sql("ALTER TABLE journal_sessions ADD COLUMN custom_filter_data TEXT;")
        print("[DB PATCH] Added missing column journal_sessions.custom_filter_data")

    # --- Analytics Tables (GDPR-compliant, no PII) ---
    # Create analytics_event table if it doesn't exist
    analytics_event_exists = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_event';"
    ).fetchone()
    if not analytics_event_exists:
        conn.exec_driver_sql("""
            CREATE TABLE analytics_event (
                event_name VARCHAR(64) NOT NULL,
                date DATE NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (event_name, date)
            );
        """)
        print("[DB PATCH] Created analytics_event table")

    # Create analytics_login table if it doesn't exist
    analytics_login_exists = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_login';"
    ).fetchone()
    if not analytics_login_exists:
        conn.exec_driver_sql("""
            CREATE TABLE analytics_login (
                date DATE PRIMARY KEY,
                login_count INTEGER NOT NULL DEFAULT 0
            );
        """)
        print("[DB PATCH] Created analytics_login table")


def ensure_db_initialized_unified():
    """
    Create tables if missing, ensure schema patches (external_id column),
    and set SQLite pragmas before any queries or migrations run.
    """
    lock_path = os.path.join(INSTANCE_PATH, "schema_patch.lock")
    with _FileLock(lock_path):
        Base.metadata.create_all(bind=engine, checkfirst=True)

        # Set pragmas BEFORE any transaction (SQLite restriction)
        with engine.connect() as pragma_conn:
            pragma_conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            pragma_conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
            pragma_conn.exec_driver_sql("PRAGMA cache_size = -20000;")    # 20MB page cache
            pragma_conn.exec_driver_sql("PRAGMA temp_store = MEMORY;")    # temp tables in RAM
            pragma_conn.exec_driver_sql("PRAGMA mmap_size = 30000000;")   # 30MB memory-mapped I/O

        with engine.begin() as conn:
            _run_schema_patches(conn)


# --- Ensure DB schema and patches are applied before any migration/backfill ---
ensure_db_initialized_unified()

# Ensure config/backup directories exist before migration runs
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

            # Bortle scale: validate 1-9 if present, ignore silently if absent
            raw_bortle = loc.get("bortle_scale")
            bortle_val = None
            if raw_bortle is not None:
                try:
                    bortle_int = int(raw_bortle)
                    if 1 <= bortle_int <= 9:
                        bortle_val = bortle_int
                except (ValueError, TypeError):
                    pass

            existing = db.query(Location).filter_by(user_id=user.id, name=name).one_or_none()
            if existing:
                # --- UPDATE existing row
                existing.lat = lat
                existing.lon = lon
                existing.timezone = tz
                existing.altitude_threshold = alt_thr
                existing.is_default = new_is_default
                existing.active = loc.get("active", True)
                existing.bortle_scale = bortle_val

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
                        except (ValueError, TypeError, IndexError) as hp_err:
                            app.logger.warning(f"[MIGRATION] Invalid horizon point skipped for location '{name}': {pair} - {hp_err}")

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
                    active=loc.get("active", True),
                    bortle_scale=bortle_val
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
                        except (ValueError, TypeError, IndexError) as hp_err:
                            app.logger.warning(f"[MIGRATION] Invalid horizon point skipped for new location '{name}': {pair} - {hp_err}")

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
        db.rollback()
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
                # Mosaic Data (legacy safe with .get() and defaults)
                existing.mosaic_cols = f.get("mosaic_cols", 1)
                existing.mosaic_rows = f.get("mosaic_rows", 1)
                existing.mosaic_overlap = f.get("mosaic_overlap", 10.0)
                # Image Adjustment Data (legacy safe with .get() and defaults)
                existing.img_brightness = f.get("img_brightness", 0.0)
                existing.img_contrast = f.get("img_contrast", 0.0)
                existing.img_gamma = f.get("img_gamma", 1.0)
                existing.img_saturation = f.get("img_saturation", 0.0)
                # Overlay Preferences (legacy safe with .get() and default)
                existing.geo_belt_enabled = f.get("geo_belt_enabled", True)
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
                    blend_opacity=f.get("blend_opacity"),
                    # Mosaic Data (legacy safe with .get() and defaults)
                    mosaic_cols=f.get("mosaic_cols", 1),
                    mosaic_rows=f.get("mosaic_rows", 1),
                    mosaic_overlap=f.get("mosaic_overlap", 10.0),
                    # Image Adjustment Data (legacy safe with .get() and defaults)
                    img_brightness=f.get("img_brightness", 0.0),
                    img_contrast=f.get("img_contrast", 0.0),
                    img_gamma=f.get("img_gamma", 1.0),
                    img_saturation=f.get("img_saturation", 0.0),
                    # Overlay Preferences (legacy safe with .get() and default)
                    geo_belt_enabled=f.get("geo_belt_enabled", True)
                )
                db.add(new_sf)

        except Exception as e:
            db.rollback()
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

            # Default to True for backward compatibility with old backups
            enabled = bool(o.get("enabled", True))
            is_shared = bool(o.get("is_shared", False))
            original_user_id = _as_int(o.get("original_user_id"))
            original_item_id = _as_int(o.get("original_item_id"))
            catalog_sources = o.get("catalog_sources")
            catalog_info = o.get("catalog_info")

            # --- Curation Fields (Backup/Restore Support) ---
            image_url = o.get("image_url")
            image_credit = o.get("image_credit")
            image_source_link = o.get("image_source_link")
            description_text = o.get("description_text")
            description_credit = o.get("description_credit")
            description_source_link = o.get("description_source_link")

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
                existing.enabled = enabled

                # Restore Curation
                existing.image_url = image_url
                existing.image_credit = image_credit
                existing.image_source_link = image_source_link
                existing.description_text = description_text
                existing.description_credit = description_credit
                existing.description_source_link = description_source_link
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
                    enabled=enabled,
                    # Restore Curation
                    image_url=image_url,
                    image_credit=image_credit,
                    image_source_link=image_source_link,
                    description_text=description_text,
                    description_credit=description_credit,
                    description_source_link=description_source_link,
                )
                db.add(new_object)
                db.flush()

        except Exception as e:
            # If one object entry is malformed, log the error and continue with the rest.
            db.rollback()
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

            # Guide optics fields
            guide_tel_name = r.get("guide_telescope_name")
            guide_cam_name = r.get("guide_camera_name")
            guide_tel_id = _resolve_component_id("telescope", r.get("guide_telescope_id"), guide_tel_name)
            guide_cam_id = _resolve_component_id("camera", r.get("guide_camera_id"), guide_cam_name)
            guide_is_oag = bool(r.get("guide_is_oag", False))

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
                existing_rig.guide_telescope_id, existing_rig.guide_camera_id, existing_rig.guide_is_oag = guide_tel_id, guide_cam_id, guide_is_oag
            else:
                db.add(Rig(user_id=user.id, rig_name=rig_name, telescope_id=tel_id, camera_id=cam_id,
                           reducer_extender_id=red_id, effective_focal_length=eff_fl, f_ratio=f_ratio,
                           image_scale=scale, fov_w_arcmin=fov_w, guide_telescope_id=guide_tel_id,
                           guide_camera_id=guide_cam_id, guide_is_oag=guide_is_oag))
            db.flush()

        except Exception as e:
            db.rollback()
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
            db.rollback()
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
            "dither_pixels": _as_int(s.get("dither_pixels")),  # None for old backups
            "dither_every_n": _as_int(s.get("dither_every_n")),  # None for old backups
            "dither_notes": s.get("dither_notes"),  # None for old backups
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
            "external_id": str(ext_id) if ext_id else None,
            # Custom filter data (JSON string for user-defined filters)
            "custom_filter_data": s.get("custom_filter_data"),
            "asiair_log_content": s.get("asiair_log_content"),
            "phd2_log_content": s.get("phd2_log_content"),
            "log_analysis_cache": s.get("log_analysis_cache"),
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

        # *** START: Legacy dither migration ***
        # If new structured fields are absent but old dither_details is present,
        # migrate the old text into dither_notes
        if row_values.get("dither_pixels") is None and row_values.get("dither_details"):
            # Get the session object (either existing_session or new_session)
            session_obj = existing_session if existing_session else new_session
            session_obj.dither_notes = row_values.get("dither_details")
        # *** END: Legacy dither migration ***
        # *** END: Simplified Upsert Logic ***

    # --- Import custom filter definitions ---
    for cf_def in data.get('custom_mono_filters', []):
        key = (cf_def.get('key') or '').strip()
        label = (cf_def.get('label') or '').strip()
        if not key or not label:
            continue
        if not db.query(UserCustomFilter).filter_by(user_id=user.id, filter_key=key).first():
            db.add(UserCustomFilter(user_id=user.id, filter_key=key, filter_label=label))
    db.flush()


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
    loc_rows = db.query(Location).options(selectinload(Location.horizon_points)).filter_by(user_id=u.id).all()
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

# === YAML Portability: Export / Import ======================================
def export_user_to_yaml(username: str, out_dir: str = None) -> bool:
    """
    Write three YAML files (config_*.yaml, rigs_default.yaml, journal_*.yaml) in out_dir.
    """
    db = get_db()
    out_dir = out_dir or CONFIG_DIR
    os.makedirs(out_dir, exist_ok=True)

    u = db.query(DbUser).filter_by(username=username).one_or_none()
    if not u:
        return False

    # CONFIG (locations + objects + defaults)
    locs = db.query(Location).options(selectinload(Location.horizon_points)).filter_by(user_id=u.id).all()
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
            "blend_opacity": sf.blend_opacity,
            # Mosaic Data
            "mosaic_cols": sf.mosaic_cols,
            "mosaic_rows": sf.mosaic_rows,
            "mosaic_overlap": sf.mosaic_overlap,
            # Image Adjustment Data
            "img_brightness": sf.img_brightness,
            "img_contrast": sf.img_contrast,
            "img_gamma": sf.img_gamma,
            "img_saturation": sf.img_saturation,
            # Overlay Preferences
            "geo_belt_enabled": sf.geo_belt_enabled
        })
    cfg = {
        "default_location": default_loc,
        "locations": {
            l.name: {
                **{
                    "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                    "altitude_threshold": l.altitude_threshold,
                    "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
                },
                **({"bortle_scale": l.bortle_scale} if l.bortle_scale is not None else {})
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

    # Create a lookup map for component names by ID to ensure portable exports
    comp_map = {c.id: c.name for c in comps}

    def bykind(k):
        return [c for c in comps if c.kind == k]

    rigs_doc = {
        "components": {
            "telescopes": [
                {"id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm,
                 "is_shared": c.is_shared, "original_user_id": c.original_user_id,
                 "original_item_id": c.original_item_id}
                for c in bykind("telescope")
            ],
            "cameras": [
                {"id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                 "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um, "is_shared": c.is_shared,
                 "original_user_id": c.original_user_id, "original_item_id": c.original_item_id}
                for c in bykind("camera")
            ],
            "reducers_extenders": [
                {"id": c.id, "name": c.name, "factor": c.factor, "is_shared": c.is_shared,
                 "original_user_id": c.original_user_id, "original_item_id": c.original_item_id}
                for c in bykind("reducer_extender")
            ],
        },
        "rigs": [
            {
                "rig_name": r.rig_name,
                "telescope_id": r.telescope_id,
                "telescope_name": comp_map.get(r.telescope_id),  # Export name for portability
                "camera_id": r.camera_id,
                "camera_name": comp_map.get(r.camera_id),  # Export name for portability
                "reducer_extender_id": r.reducer_extender_id,
                "reducer_extender_name": comp_map.get(r.reducer_extender_id),  # Export name
                "effective_focal_length": r.effective_focal_length,
                "f_ratio": r.f_ratio,
                "image_scale": r.image_scale,
                "fov_w_arcmin": r.fov_w_arcmin,
                # Guide optics fields
                "guide_telescope_id": r.guide_telescope_id,
                "guide_telescope_name": comp_map.get(r.guide_telescope_id),
                "guide_camera_id": r.guide_camera_id,
                "guide_camera_name": comp_map.get(r.guide_camera_id),
                "guide_is_oag": r.guide_is_oag or False
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
    # FIX: Build project lookup dict for session export (natural key resolution)
    project_lookup = {p.id: p.name for p in db_projects}
    
    for p in db_projects:
        projects_list.append({
            "project_id": p.id,  # Legacy: kept for backward compatibility
            "project_name": p.name,
            "target_object_id": p.target_object_name,
            "status": p.status,
            "goals": p.goals,
            "description_notes": p.description_notes,
            "framing_notes": p.framing_notes,
            "processing_notes": p.processing_notes,
            "final_image_file": p.final_image_file
        })

    # Custom filter definitions for this user
    custom_filters_db = db.query(UserCustomFilter).filter_by(user_id=u.id).order_by(UserCustomFilter.created_at).all()
    custom_filters_list = [
        {'key': cf.filter_key, 'label': cf.filter_label}
        for cf in custom_filters_db
    ]

    jdoc = {
        "projects": projects_list,
        "custom_mono_filters": custom_filters_list,
        "sessions": [
            {
                "date": s.date_utc.isoformat(),
                "object_name": s.object_name,
                "notes": s.notes,
                "session_id": s.external_id or s.id,
                "project_id": s.project_id,  # Legacy: kept for backward compatibility
                "project_name": project_lookup.get(s.project_id) if s.project_id else None,

                # Capture Details
                "number_of_subs_light": s.number_of_subs_light,
                "exposure_time_per_sub_sec": s.exposure_time_per_sub_sec,
                "filter_used_session": s.filter_used_session,
                "gain_setting": s.gain_setting,
                "offset_setting": s.offset_setting,
                "binning_session": s.binning_session,
                "camera_temp_setpoint_c": s.camera_temp_setpoint_c,
                "camera_temp_actual_avg_c": s.camera_temp_actual_avg_c,
                "calculated_integration_time_minutes": s.calculated_integration_time_minutes,

                # Environmental & Location
                "location_name": s.location_name,
                "seeing_observed_fwhm": s.seeing_observed_fwhm,
                "sky_sqm_observed": s.sky_sqm_observed,
                "transparency_observed_scale": s.transparency_observed_scale,
                "moon_illumination_session": s.moon_illumination_session,
                "moon_angular_separation_session": s.moon_angular_separation_session,
                "weather_notes": s.weather_notes,

                # Gear & Guiding
                "telescope_setup_notes": s.telescope_setup_notes,
                "guiding_rms_avg_arcsec": s.guiding_rms_avg_arcsec,
                "guiding_equipment": s.guiding_equipment,
                "dither_details": s.dither_details,
                "dither_pixels": s.dither_pixels,
                "dither_every_n": s.dither_every_n,
                "dither_notes": s.dither_notes,
                "dither_display": dither_display(s),
                "acquisition_software": s.acquisition_software,

                # Calibration Strategy
                "darks_strategy": s.darks_strategy,
                "flats_strategy": s.flats_strategy,
                "bias_darkflats_strategy": s.bias_darkflats_strategy,
                "session_rating_subjective": s.session_rating_subjective,

                # Mono Filters
                "filter_L_subs": s.filter_L_subs, "filter_L_exposure_sec": s.filter_L_exposure_sec,
                "filter_R_subs": s.filter_R_subs, "filter_R_exposure_sec": s.filter_R_exposure_sec,
                "filter_G_subs": s.filter_G_subs, "filter_G_exposure_sec": s.filter_G_exposure_sec,
                "filter_B_subs": s.filter_B_subs, "filter_B_exposure_sec": s.filter_B_exposure_sec,
                "filter_Ha_subs": s.filter_Ha_subs, "filter_Ha_exposure_sec": s.filter_Ha_exposure_sec,
                "filter_OIII_subs": s.filter_OIII_subs, "filter_OIII_exposure_sec": s.filter_OIII_exposure_sec,
                "filter_SII_subs": s.filter_SII_subs, "filter_SII_exposure_sec": s.filter_SII_exposure_sec,

                # Rig Snapshots
                "rig_id_snapshot": s.rig_id_snapshot,
                "rig_name_snapshot": s.rig_name_snapshot,
                "rig_efl_snapshot": s.rig_efl_snapshot,
                "rig_fr_snapshot": s.rig_fr_snapshot,
                "rig_scale_snapshot": s.rig_scale_snapshot,
                "rig_fov_w_snapshot": s.rig_fov_w_snapshot,
                "rig_fov_h_snapshot": s.rig_fov_h_snapshot,
                "telescope_name_snapshot": s.telescope_name_snapshot,
                "reducer_name_snapshot": s.reducer_name_snapshot,
                "camera_name_snapshot": s.camera_name_snapshot,

                # Custom filter data (JSON string for user-defined filters)
                "custom_filter_data": s.custom_filter_data,
            } for s in sessions
        ]
    }
    jfile = "journal_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"journal_{username}.yaml"
    _atomic_write_yaml(os.path.join(out_dir, jfile), jdoc)
    return True

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
    except Exception as import_err:
        db.rollback()
        app.logger.error(f"[YAML IMPORT] Failed to import config for user '{username}': {import_err}")
        traceback.print_exc()
        return False

def import_catalog_pack_for_user(db, user: DbUser, catalog_config: dict, pack_id: str) -> tuple[int, int, int]:
    """
    Import a catalog pack. Returns (created_count, enriched_count, skipped_count).
    Enrichment is NON-DESTRUCTIVE: it only fills missing (empty) fields.
    """
    created = 0
    enriched = 0
    skipped = 0

    objs = (catalog_config or {}).get("objects", []) or []

    def _merge_sources(current: str | None, new_id: str) -> str:
        if not new_id: return current or ""
        if not current: return new_id
        parts = {p.strip() for p in str(current).split(',') if p.strip()}
        parts.add(new_id)
        return ",".join(sorted(parts))

    for o in objs:
        try:
            # --- 1. Parse Common Data ---
            ra_val = o.get("RA") if o.get("RA") is not None else o.get("RA (hours)")
            dec_val = o.get("DEC") if o.get("DEC") is not None else o.get("DEC (degrees)")

            raw_obj_name = o.get("Object") or o.get("object") or o.get("object_name")
            if not raw_obj_name or not str(raw_obj_name).strip():
                skipped += 1
                continue

            object_name = normalize_object_name(raw_obj_name)

            # --- 2. Check for Existing Object ---
            existing = db.query(AstroObject).filter_by(
                user_id=user.id,
                object_name=object_name
            ).one_or_none()

            # --- 3. Extract Curation Data from Pack ---
            pack_img_url = o.get("image_url")
            pack_img_credit = o.get("image_credit")
            pack_img_link = o.get("image_source_link")
            pack_desc_text = o.get("description_text")
            pack_desc_credit = o.get("description_credit")
            pack_desc_link = o.get("description_source_link")

            if existing:
                # --- UPDATE LOGIC (Authoritative for Inspiration) ---
                was_enriched = False

                # Force update Inspiration fields if the pack provides them.
                # We assume the catalog is the master source for these fields,
                # while preserving user-specific data like Project Notes.
                if pack_img_url:
                    # Check if actually different to avoid unnecessary writes/counts
                    if existing.image_url != pack_img_url or existing.image_credit != pack_img_credit:
                        existing.image_url = pack_img_url
                        existing.image_credit = pack_img_credit
                        existing.image_source_link = pack_img_link
                        was_enriched = True

                if pack_desc_text:
                    if existing.description_text != pack_desc_text:
                        existing.description_text = pack_desc_text
                        existing.description_credit = pack_desc_credit
                        existing.description_source_link = pack_desc_link
                        was_enriched = True

                # Always update source tracking
                existing.catalog_sources = _merge_sources(existing.catalog_sources, pack_id)

                if was_enriched:
                    enriched += 1
                else:
                    skipped += 1
                continue

            # --- 4. Create New Object ---
            # (Only if RA/DEC exist)
            ra_f = float(ra_val) if ra_val is not None else None
            dec_f = float(dec_val) if dec_val is not None else None

            if (ra_f is None) or (dec_f is None):
                skipped += 1
                continue

            # Basic Fields
            common_name = o.get("Common Name") or o.get("Name") or o.get("common_name") or str(raw_obj_name).strip()
            obj_type = o.get("Type") or o.get("type")
            constellation = o.get("Constellation") or o.get("constellation")
            magnitude = str(o.get("Magnitude") if o.get("Magnitude") is not None else o.get("magnitude") or "")
            size = str(o.get("Size") if o.get("Size") is not None else o.get("size") or "")
            sb = str(o.get("SB") if o.get("SB") is not None else o.get("sb") or "")

            # Constellation Calc
            if (not constellation) and (ra_f is not None) and (dec_f is not None):
                try:
                    coords = SkyCoord(ra=ra_f * u.hourangle, dec=dec_f * u.deg)
                    constellation = get_constellation(coords)
                except Exception:
                    constellation = None

            new_object = AstroObject(
                user_id=user.id,
                object_name=object_name,
                common_name=common_name,
                ra_hours=ra_f,
                dec_deg=dec_f,
                type=obj_type,
                constellation=constellation,
                magnitude=magnitude if magnitude else None,
                size=size if size else None,
                sb=sb if sb else None,
                active_project=False,
                project_name=None,
                is_shared=False,
                shared_notes=None,
                original_user_id=None,
                original_item_id=None,
                catalog_sources=pack_id,
                catalog_info=o.get("catalog_info"),
                # Curation
                image_url=pack_img_url,
                image_credit=pack_img_credit,
                image_source_link=pack_img_link,
                description_text=pack_desc_text,
                description_credit=pack_desc_credit,
                description_source_link=pack_desc_link,
            )
            db.add(new_object)
            created += 1

        except Exception as e:
            print(f"[CATALOG IMPORT] Error processing '{o}': {e}")
            skipped += 1

    return (created, enriched, skipped)

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
        is_single_user_mode_for_migration = decouple_config('SINGLE_USER_MODE', default='True') == 'True'

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


# =============================================================================
# Flask and Flask-Login Setup
# =============================================================================

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

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(
    __name__,
    template_folder=os.path.join(_project_root, 'templates'),
    static_folder=os.path.join(_project_root, 'static'),
)
app.jinja_env.filters['toyaml'] = to_yaml_filter
app.jinja_env.globals['translation_status'] = TRANSLATION_STATUS
app.secret_key = SECRET_KEY
csrf = CSRFProtect()
csrf.init_app(app)
app.config['WTF_CSRF_CHECK_DEFAULT'] = False  # Don't enforce globally; protect routes explicitly

# --- AI Configuration (loaded from .env via nova.config) ---
app.config['AI_PROVIDER'] = AI_PROVIDER
app.config['AI_API_KEY'] = AI_API_KEY
app.config['AI_MODEL'] = AI_MODEL
app.config['AI_BASE_URL'] = AI_BASE_URL
app.config['AI_ALLOWED_USERS'] = AI_ALLOWED_USERS

# --- Internationalization (i18n) with Flask-Babel ---
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.config['BABEL_SUPPORTED_LOCALES'] = ['en', 'de', 'fr', 'es', 'ja', 'zh']
# Translations are at project root (../translations relative to nova/ package)
app.config['BABEL_TRANSLATION_DIRECTORIES'] = os.path.join(os.path.dirname(__file__), '..', 'translations')
babel = Babel()  # Create without app - will init with locale_selector below

def get_locale():
    """
    Locale selector for Flask-Babel.
    Reads language preference from user config or session, falls back to browser preference or 'en'.
    """
    # Try user preference first (set by load_global_request_context)
    if hasattr(g, 'user_config') and g.user_config:
        user_lang = g.user_config.get('language')
        if user_lang and user_lang in app.config['BABEL_SUPPORTED_LOCALES']:
            return user_lang
    # Try session (for guest users)
    session_lang = session.get('language')
    if session_lang and session_lang in app.config['BABEL_SUPPORTED_LOCALES']:
        return session_lang
    # Fall back to browser preference
    browser_locale = request.accept_languages.best_match(app.config['BABEL_SUPPORTED_LOCALES'])
    if browser_locale:
        return browser_locale
    # Default
    return 'en'

# Initialize Babel with the app and locale_selector in one call
babel.init_app(app, locale_selector=get_locale)
app.jinja_env.globals['get_locale'] = get_locale

# --- Performance: gzip response compression ---
from flask_compress import Compress
Compress(app)

# --- Performance: custom JSON provider for numpy types ---
from flask.json.provider import DefaultJSONProvider

class NumpyJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)

app.json_provider_class = NumpyJSONProvider
app.json = NumpyJSONProvider(app)


# --- Performance: browser cache headers ---
@app.after_request
def set_cache_headers(response):
    path = request.path
    if path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=604800'  # 1 week
    elif path.startswith('/uploads/'):
        response.headers['Cache-Control'] = 'public, max-age=86400'  # 1 day
    elif path == '/favicon.ico':
        response.headers['Cache-Control'] = 'public, max-age=2592000'  # 30 days
    elif response.content_type and 'application/json' in response.content_type:
        response.headers['Cache-Control'] = 'no-store'
    return response


# --- Database Session Lifecycle Management ---
@app.teardown_appcontext
def cleanup_db_session(exception=None):
    """
    Cleanup database session at the end of each request context.
    Uses SessionLocal.remove() to properly clean up the scoped session.
    """
    SessionLocal.remove()


if not SINGLE_USER_MODE:
    # --- Sentry Error Reporting (multi-user mode only) ---
    if SENTRY_DSN:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[
                FlaskIntegration(),
                LoggingIntegration(
                    level=logging.WARNING,    # Capture WARNING+ as breadcrumbs
                    event_level=logging.ERROR  # Send events on ERROR+
                ),
            ],
            release=APP_VERSION,
        )

# --- Auth setup (db, User, login_manager live in nova.auth) ---
init_auth(app)


@app.before_request
def load_global_request_context():
    # 1. Skip all expensive logic for static files
    if request.endpoint in ('static', 'core.favicon'):
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
    elif not SINGLE_USER_MODE and request.path.startswith('/sso/login'):
        # Do not allow provisioning during the SSO login redirect itself,
        # as it happens *before* current_user is set for the *next* request.
        g.db_user = None
        return
    else:
        # Fallback for unauthenticated multi-user or authenticated single-user.
        username = "guest_user"
        g.is_guest = True

    if not username:
        g.db_user = None
        return

    # 4. Get DB user and UI preferences (FAST queries)
    db = get_db()
    try:
        # Get or create the user in app.db. This is the crucial line.
        # It handles provisioning/seeding if the user doesn't exist.
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


# --- CONFIG: Log Parsing Patterns ---
ASIAIR_PATTERNS = {
    'master': re.compile(r'^(\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}:\d{2})\s+\[(.*?)\]\s+(.*)$'),
    'temp': re.compile(r'temperature\s+([-\d\.]+)'),
    'exposure': re.compile(r'Exposure\s+([\d\.]+)s'),
    'stars': re.compile(r'Star number =\s*(\d+)'),
    'focus_pos': re.compile(r'position is (\d+)'),
    'fallback_ts': re.compile(r'^(\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}:\d{2})')
}


def _parse_phd2_log_content(content):
    """Parses PHD2 Guide Log into a formatted HTML report."""
    lines = content.splitlines()

    sessions = []
    # Added fields for SNR and Event tracking
    current_session = {'data': [], 'start': None, 'end': None, 'snr': [], 'dither_count': 0, 'settle_count': 0}
    col_map = {}

    pixel_scale = 1.0
    profile_name = "Unknown Profile"
    phd_version = "Unknown"
    # Metadata placeholders
    camera_name = "Unknown"
    mount_name = "Unknown"
    focal_length = "Unknown"
    x_algo = "Unknown"
    y_algo = "Unknown"

    # Helper: Robust date parser
    def parse_ts(s):
        s = s.strip()
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%d-%m-%Y %H:%M:%S'):
            try:
                return datetime.strptime(s, fmt)
            except:
                continue
        return None

    for line in lines:
        line = line.strip()
        if not line: continue

        # --- Headers ---
        if line.startswith("PHD2_v"):
            phd_version = line.split(",")[0]

        if "Pixel scale =" in line:
            try:
                # Regex to find float in string like "Pixel scale = 1.03 arc-sec/px"
                match = re.search(r'Pixel scale = ([\d\.]+)', line)
                if match: pixel_scale = float(match.group(1))
                # Also look for Focal Length on this line
                fl_match = re.search(r'Focal length = ([\d\.]+)', line)
                if fl_match: focal_length = f"{fl_match.group(1)} mm"
            except:
                pass

        if "Equipment Profile =" in line:
            profile_name = line.split("=")[1].strip()

            # Track Events within active session
        if current_session.get('start') and not current_session.get('end'):
            if "DITHER" in line:
                current_session['dither_count'] += 1
            if "SETTLING STATE CHANGE" in line:
                current_session['settle_count'] += 1

        if line.startswith("Camera ="):
            # Extract name before the first comma (e.g., "Camera = ZWO ASI174MM Mini, gain = ...")
            camera_name = line.split("=")[1].split(",")[0].strip()

        if line.startswith("Mount ="):
            mount_name = line.split("=")[1].split(",")[0].strip()

        if "X guide algorithm =" in line:
            x_algo = line.split("=")[1].split(",")[0].strip()

        if "Y guide algorithm =" in line:
            y_algo = line.split("=")[1].split(",")[0].strip()

        # --- Session Markers ---
        # Match "Guiding Begins at ..." broadly
        if "Guiding Begins" in line:
            parts = line.split(" at ")
            if len(parts) > 1:
                dt = parse_ts(parts[1])
                if dt:
                    current_session = {'data': [], 'start': dt, 'end': None, 'snr': [], 'dither_count': 0,
                                       'settle_count': 0}
            continue

        if "Guiding Ends" in line:
            parts = line.split(" at ")
            if len(parts) > 1:
                current_session['end'] = parse_ts(parts[1])

            # Archive session if it has data
            if current_session['data'] and current_session['start']:
                sessions.append(current_session)
            # Reset
            current_session = {'data': [], 'start': None, 'end': None, 'snr': [], 'dither_count': 0, 'settle_count': 0}
            continue

        # --- Column Definitions ---
        # Robust check for header line (handles "Frame" or Frame)
        if ("Frame" in line and "Time" in line and "," in line) and (
                line.startswith("Frame") or line.startswith('"Frame"')):
            cols = [c.strip().replace('"', '') for c in line.split(",")]
            col_map = {name: i for i, name in enumerate(cols)}
            continue

        # --- Data Rows ---
        if current_session.get('start') and line[0].isdigit() and ',' in line:
            parts = line.split(',')

            # Flexible Column Lookup (RAErr OR RA)
            def get_col(candidates):
                for c in candidates:
                    if c in col_map and len(parts) > col_map[c]:
                        val = parts[col_map[c]]
                        if val and val.strip():
                            try:
                                return float(val)
                            except:
                                pass
                return None

            # Expanded candidates to support log versions using "RawDistance" (e.g., v2.5)
            ra = get_col(['RAErr', 'RA', 'RARawDistance'])
            dec = get_col(['DecErr', 'Dec', 'DECRawDistance'])

            if ra is not None and dec is not None:
                current_session['data'].append((ra, dec))

                # Extract SNR
            if 'SNR' in col_map and len(parts) > col_map['SNR']:
                try:
                    current_session['snr'].append(float(parts[col_map['SNR']]))
                except:
                    pass

    # Handle unterminated session at EOF
    if current_session['data'] and current_session['start']:
        if not current_session['end']:
            # Fallback end time: start + frames * 2s (approx)
            est_seconds = len(current_session['data']) * 2
            current_session['end'] = current_session['start'] + timedelta(seconds=est_seconds)
        sessions.append(current_session)

    if not sessions:
        return "<p style='color: var(--text-tertiary); font-style: italic;'>No guiding sessions found in this log. Ensure the log contains 'Guiding Begins' and data rows.</p>"

    # --- Select Main Session (Longest) ---
    main_session = max(sessions, key=lambda s: len(s['data']))
    data_points = main_session['data']
    start_dt = main_session['start']
    end_dt = main_session['end']
    duration = end_dt - start_dt

    # --- Statistics ---
    import math

    n = len(data_points)
    sum_ra_sq = sum(d[0] ** 2 for d in data_points)
    sum_dec_sq = sum(d[1] ** 2 for d in data_points)

    # RMS in pixels
    rms_ra_px = math.sqrt(sum_ra_sq / n)
    rms_dec_px = math.sqrt(sum_dec_sq / n)
    rms_tot_px = math.sqrt((sum_ra_sq + sum_dec_sq) / n)

    # Convert to Arcsec
    rms_ra = rms_ra_px * pixel_scale
    rms_dec = rms_dec_px * pixel_scale
    rms_tot = rms_tot_px * pixel_scale

    # Class Grading (<0.5 Excellent, <0.8 Good, <1.0 Ok, >1.0 Poor)
    rms_class = "status-success" if rms_tot < 0.5 else "status-info" if rms_tot < 0.8 else "status-warning" if rms_tot < 1.0 else "status-danger"

    # --- Derived Metrics for HTML ---
    dither_c = main_session.get('dither_count', 0)
    settle_c = main_session.get('settle_count', 0)

    snr_list = main_session.get('snr', [])
    if snr_list:
        avg_snr = sum(snr_list) / len(snr_list)
        min_snr = min(snr_list)
        max_snr = max(snr_list)
    else:
        avg_snr, min_snr, max_snr = 0, 0, 0

    dither_html = ""
    if dither_c > 0:
        dither_html = f"""
            <li><strong>Dithering is Active:</strong> The log shows frequent "DITHER" commands ({dither_c} detected) followed by "SETTLING STATE CHANGE". This confirms your capture sequence is correctly instructing PHD2 to shift the frame between exposures to reduce noise.</li>
            <li><strong>Settling Performance:</strong> There are distinct periods where the mount is "Settling" after a dither ({settle_c} events detected).</li>
            """
    else:
        dither_html = "<li><strong>Dithering:</strong> No dither commands detected in this session.</li>"

    snr_class = "status-success" if avg_snr >= 20 else "status-warning" if avg_snr >= 10 else "status-danger"
    snr_html = f"""<li><strong>Signal Strength (SNR):</strong> <span class="{snr_class}">Avg {avg_snr:.1f}</span> (Range: {min_snr:.1f}-{max_snr:.1f}). Values consistently around 20–30 indicate a very healthy/strong signal, likely helped by the 2x binning.</li>"""

    html = f"""
    <h3>PHD2 Guiding Analysis</h3>
    <p style="margin-bottom: 10px;"><strong>Profile:</strong> {profile_name} &nbsp;|&nbsp; <strong>Date:</strong> {start_dt.strftime('%b %d, %Y')}</p>

    <hr style="margin: 10px 0; border: 0; border-top: 1px solid var(--border-light);">

    <div style="margin-bottom: 2px;"><strong>Performance (RMS)</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Total RMS:</strong> <strong class="{rms_class}">{rms_tot:.2f}"</strong> (RA: {rms_ra:.2f}", Dec: {rms_dec:.2f}")</li>
        <li><strong>Pixel Scale:</strong> {pixel_scale}"/px</li>
        <li><strong>Duration:</strong> {int(duration.total_seconds() // 60)} min ({n} frames)</li>
    </ul>

    <div style="margin-bottom: 2px;"><strong>Equipment & Config</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Camera:</strong> {camera_name}</li>
        <li><strong>Mount:</strong> {mount_name}</li>
        <li><strong>Optics:</strong> {focal_length}</li>
        <li><strong>Algorithms:</strong> RA: {x_algo} / Dec: {y_algo}</li>
    </ul>

    <div style="margin-bottom: 2px;"><strong>Guide Performance & Stability</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        {dither_html}
        {snr_html}
    </ul>

    <div style="margin-bottom: 2px;"><strong>Session Details</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Start:</strong> {start_dt.strftime('%H:%M:%S')}</li>
        <li><strong>End:</strong> {end_dt.strftime('%H:%M:%S')}</li>
        <li><strong>Version:</strong> {phd_version}</li>
    </ul>
    """

    if len(sessions) > 1:
        html += f"<div style='margin-top:10px; font-size:0.85em; color:var(--text-muted);'>* Analyzed longest session ({n} frames). Log contained {len(sessions)} runs.</div>"

    return html


# --- ASIAIR Log Parsing Logic ---
def _parse_asiair_log_content(content):
    """Parses ASIAIR log content into a formatted HTML report."""
    lines = content.splitlines()

    # 1. Collect all raw data points from the log
    raw_data_points = []

    for i, line in enumerate(lines):
        line = line.strip()
        if not line: continue

        # --- Regex Parsing ---
        master_match = ASIAIR_PATTERNS['master'].match(line)
        if not master_match:
            ts_match = ASIAIR_PATTERNS['fallback_ts'].match(line)
            if not ts_match: continue
            dt_str = ts_match.group(1)
            category = "Unknown"
            msg = line[len(dt_str):].strip()
        else:
            dt_str = master_match.group(1)
            category = master_match.group(2)
            msg = master_match.group(3)

        try:
            dt = datetime.strptime(dt_str, '%Y/%m/%d %H:%M:%S')
        except ValueError:
            continue

        # --- Extract Data Points ---

        # Exposure
        if "Exposure" in category or msg.startswith("Exposure"):
            if msg.startswith("Exposure"):
                dur_match = ASIAIR_PATTERNS['exposure'].search(msg)
                seconds = float(dur_match.group(1)) if dur_match else 0
                raw_data_points.append({'dt': dt, 'type': 'exposure', 'seconds': seconds})
                continue

        # Autorun (Target Name & Start/End)
        if "Autorun|Begin" in category or "[Autorun|Begin]" in msg:
            clean_msg = msg.replace("[Autorun|Begin]", "").strip()
            parts = clean_msg.split(" Start")
            tgt = parts[0] if parts else "Unknown"
            raw_data_points.append({'dt': dt, 'type': 'target', 'name': tgt})
            raw_data_points.append({'dt': dt, 'type': 'event', 'text': "Session Start", 'color_class': "status-info"})
            continue
        elif "Autorun|End" in category or "[Autorun|End]" in msg:
            raw_data_points.append({'dt': dt, 'type': 'event', 'text': "Session Ended", 'color_class': "status-info"})
            continue

        # Guiding / Dither
        if "Guide" in category or "[Guide]" in msg:
            if "Dither" in msg and "Settle" not in msg:
                raw_data_points.append({'dt': dt, 'type': 'dither_start'})
            elif "Settle Done" in msg:
                raw_data_points.append({'dt': dt, 'type': 'dither_end'})
            elif "Settle Timeout" in msg:
                raw_data_points.append(
                    {'dt': dt, 'type': 'event', 'text': "Guiding: Dither Settle Timeout", 'color_class': "status-danger"})
            continue

        # Meridian Flip
        if "Meridian Flip" in category or "Meridian Flip" in msg:
            if "Start" in msg:
                raw_data_points.append(
                    {'dt': dt, 'type': 'event', 'text': "Meridian Flip: Sequence started", 'color_class': "status-info"})
            elif "failed" in msg:
                # Store raw error message
                raw_data_points.append({'dt': dt, 'type': 'event', 'text': f"ERROR: {msg}", 'color_class': "status-danger"})
            elif "succeeded" in msg:
                raw_data_points.append(
                    {'dt': dt, 'type': 'event', 'text': "Meridian Flip: Success", 'color_class': "status-success"})
            continue

        # Focus Events
        if ("AutoFocus" in category or "Auto Focus" in msg) and "Begin" in msg:
            raw_data_points.append({'dt': dt, 'type': 'event', 'text': "Auto Focus Run", 'color_class': "status-info"})

        # Environmental Data Extraction
        temp_match = ASIAIR_PATTERNS['temp'].search(msg)
        if temp_match:
            raw_data_points.append({'dt': dt, 'type': 'temp', 'val': float(temp_match.group(1))})

        if "Auto focus succeeded" in msg:
            pos_match = ASIAIR_PATTERNS['focus_pos'].search(msg)
            if pos_match:
                raw_data_points.append({'dt': dt, 'type': 'focus', 'val': int(pos_match.group(1))})

        if "Solve succeeded" in msg:
            star_match = ASIAIR_PATTERNS['stars'].search(msg)
            if star_match:
                raw_data_points.append({'dt': dt, 'type': 'stars', 'val': int(star_match.group(1))})

        if "Mount GoTo Home" in msg:
            raw_data_points.append({'dt': dt, 'type': 'event', 'text': "GoTo Home (Session End)", 'color_class': "status-secondary"})

    # --- 2. Session Grouping Logic ---
    if not raw_data_points:
        return "<p>No parsable data found.</p>"

    # Sort all points by time
    raw_data_points.sort(key=lambda x: x['dt'])

    # Group into separate sessions based on time gaps > 90 minutes
    # This separates "Night Imaging" from "Morning Flats"
    sessions = []
    current_session = []
    GAP_THRESHOLD_MINUTES = 90

    for point in raw_data_points:
        if not current_session:
            current_session.append(point)
        else:
            last_dt = current_session[-1]['dt']
            diff = (point['dt'] - last_dt).total_seconds() / 60
            if diff > GAP_THRESHOLD_MINUTES:
                sessions.append(current_session)
                current_session = [point]
            else:
                current_session.append(point)
    if current_session:
        sessions.append(current_session)

    # Pick the MAIN session (longest duration) for analysis
    main_session = max(sessions, key=lambda s: (s[-1]['dt'] - s[0]['dt']).total_seconds())

    # --- 3. Calculate Stats on Main Session Only ---
    start_dt = main_session[0]['dt']
    end_dt = main_session[-1]['dt']
    total_duration = (end_dt - start_dt).total_seconds()

    target_name = "Unknown Target"
    subs_count = 0
    total_exposure_sec = 0
    dither_durations = []
    last_dither_start = None
    temps = []
    focus_moves = []
    star_counts = []
    timeline_events = []  # List of (dt, color, text)

    for p in main_session:
        ptype = p['type']

        if ptype == 'target':
            target_name = p['name']
        elif ptype == 'exposure':
            subs_count += 1
            total_exposure_sec += p['seconds']
        elif ptype == 'dither_start':
            last_dither_start = p['dt']
        elif ptype == 'dither_end' and last_dither_start:
            dither_durations.append((p['dt'] - last_dither_start).total_seconds())
            last_dither_start = None
        elif ptype == 'temp':
            temps.append(p['val'])
        elif ptype == 'stars':
            star_counts.append(p['val'])
        elif ptype == 'focus':
            last_temp = temps[-1] if temps else None
            if last_temp is not None: focus_moves.append((last_temp, p['val']))
        elif ptype == 'event':
            timeline_events.append((p['dt'], p['color_class'], p['text']))

    # Metrics
    imaging_duration = total_exposure_sec
    duty_cycle = (imaging_duration / total_duration * 100) if total_duration > 0 else 0

    total_h = int(total_duration // 3600)
    total_m = int((total_duration % 3600) // 60)
    img_h = int(imaging_duration // 3600)
    img_m = int((imaging_duration % 3600) // 60)

    # Dithering
    total_dither_time = sum(dither_durations)
    avg_dither = (total_dither_time / len(dither_durations)) if dither_durations else 0
    dither_m = int(total_dither_time // 60)

    # Environment
    start_temp = temps[0] if temps else 0
    end_temp = temps[-1] if temps else 0
    avg_stars = int(sum(star_counts) / len(star_counts)) if star_counts else 0

    focus_summary = "N/A"
    if len(focus_moves) >= 2:
        steps_delta = abs(focus_moves[-1][1] - focus_moves[0][1])
        temp_delta = abs(focus_moves[-1][0] - focus_moves[0][0])
        steps_per_c = int(steps_delta / temp_delta) if temp_delta > 0 else 0
        focus_summary = f"Moved {steps_delta} steps over {temp_delta:.1f}°C (~{steps_per_c} steps/°C)"

    # --- HTML Generation ---
    html = f"""
    <h3>ASIAIR Session Analysis</h3>
    <p style="margin-bottom: 10px;"><strong>Target:</strong> {target_name} &nbsp;|&nbsp; <strong>Date:</strong> {start_dt.strftime('%b %d, %Y')}</p>

    <hr style="margin: 10px 0; border: 0; border-top: 1px solid var(--border-light);">

    <div style="margin-bottom: 2px;"><strong>Efficiency Metrics</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Duty Cycle:</strong> {duty_cycle:.1f}% ({img_h}h {img_m}m Imaging / {total_h}h {total_m}m Total)</li>
        <li><strong>Total Subs:</strong> {subs_count}</li>
        <li><strong>Dithering:</strong> Average settle: {avg_dither:.1f}s. Total wasted: {dither_m} min.</li>
    </ul>

    <div style="margin-bottom: 2px;"><strong>Environmental Data</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Temp Drift:</strong> {start_temp}°C &rarr; {end_temp}°C</li>
        <li><strong>Focus:</strong> {focus_summary}</li>
        <li><strong>Star Count (Avg):</strong> ~{avg_stars}</li>
    </ul>

    <div style="margin-bottom: 2px;"><strong>Key Events Timeline</strong></div>
    <div style="padding-left: 0; margin-top: 0;">
    """

    # --- 4. Timeline Rendering with Error Merging ---
    timeline_html_lines = []

    for dt, color_class, text in timeline_events:
        time_str = dt.strftime('%H:%M') if dt else "--:--"

        # Prepare content string
        if "ERROR" in text:
            # Use class for errors with bold styling
            content_html = f"<span class='{color_class}' style='font-weight: bold;'>{text}</span>"
        else:
            content_html = f"<span class='{color_class}'>{text}</span>"

        # Check for merge condition:
        # If current is an ERROR, and we have a previous line, append it there.
        if "ERROR" in text and timeline_html_lines:
            last_line = timeline_html_lines.pop()
            # Remove the closing div, append separator + error, re-add closing div
            merged_line = last_line.replace("</div>", "") + f" &nbsp; {content_html}</div>"
            timeline_html_lines.append(merged_line)
        else:
            # Standard Line
            row_html = f"<div style='margin-bottom: 2px;'><span style='color: var(--text-muted); font-family: monospace; margin-right: 8px;'>{time_str}</span> {content_html}</div>"
            timeline_html_lines.append(row_html)

    # Join and append timeline
    html += "".join(timeline_html_lines)
    html += "</div>"

    # Warning if sessions were dropped
    if len(sessions) > 1:
        dropped_count = len(sessions) - 1
        html += f"<div style='margin-top:10px; font-size:0.85em; color:var(--text-muted);'>* Analyzed main imaging session only. Excluded {dropped_count} disconnected segment(s).</div>"

    return html


@api_bp.route('/api/parse_asiair_log', methods=['POST'])
@login_required
def api_parse_asiair_log():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": _("No file uploaded")}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": _("Empty filename")}), 400

    try:
        content = file.read().decode('utf-8', errors='ignore')

        # Basic sniffing to determine log type
        first_lines = content[:200]

        if "PHD2" in first_lines or "Guiding Begins" in content:
            # Route to PHD2 Parser
            report_html = _parse_phd2_log_content(content)
        else:
            # Route to ASIAIR Parser (Default)
            report_html = _parse_asiair_log_content(content)

        record_event('log_file_imported')
        return jsonify({"status": "success", "html": report_html})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/session/<int:session_id>/log-analysis')
@login_required
def get_session_log_analysis(session_id):
    """
    Return structured log data for Chart.js visualization with parse-once caching.

    Returns:
    {
        'has_logs': bool,
        'asiair': {...} or null,
        'phd2': {...} or null,
        'nina': {...} or null
    }
    """
    import json
    from nova.log_parser import parse_asiair_log, parse_phd2_log, parse_nina_log

    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one_or_none()

    if not user:
        return jsonify({'error': _('User not found')}), 404

    session = db.query(JournalSession).filter_by(id=session_id, user_id=user.id).one_or_none()
    if not session:
        return jsonify({'error': _('Session not found')}), 404

    # 1. Return cached result if available and valid (has session_start for clock time)
    if session.log_analysis_cache:
        try:
            cached = json.loads(session.log_analysis_cache)
            # Invalidate old cache that doesn't have session_start
            asiair = cached.get('asiair')
            phd2 = cached.get('phd2')
            nina = cached.get('nina')
            has_session_start = (asiair and asiair.get('session_start')) or (phd2 and phd2.get('session_start')) or (nina and nina.get('session_start'))
            if has_session_start:
                return jsonify(cached)
            # Old cache without session_start - fall through to re-parse
        except json.JSONDecodeError:
            pass  # Fall through to re-parse if cache is corrupt

    # 2. Parse logs if any exist
    result = {
        'has_logs': False,
        'asiair': None,
        'phd2': None,
        'nina': None
    }

    # Parse logs - use read_log_content to handle both filesystem paths and legacy raw content
    asiair_content = read_log_content(session.asiair_log_content)
    if asiair_content:
        result['asiair'] = parse_asiair_log(asiair_content)
        result['has_logs'] = True

    phd2_content = read_log_content(session.phd2_log_content)
    if phd2_content:
        result['phd2'] = parse_phd2_log(phd2_content)
        result['has_logs'] = True

    nina_content = read_log_content(session.nina_log_content)
    if nina_content:
        result['nina'] = parse_nina_log(nina_content)
        result['has_logs'] = True

    # 3. Cache the result if parsing happened
    if result['has_logs']:
        session.log_analysis_cache = json.dumps(result)
        db.commit()

    return jsonify(result)


@api_bp.route('/api/get_plot_data/<path:object_name>')
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
        return jsonify({"error": _("Object data not found or invalid.")}), 404
    ra = data.get('RA (hours)')
    dec = data.get('DEC (deg)', data.get('DEC (degrees)'))
    if ra is None or dec is None:
        return jsonify({"error": _("RA/DEC missing for object.")}), 404
    try:
        ra = float(ra);
        dec = float(dec)
    except (ValueError, TypeError):
        return jsonify({"error": _("Invalid RA/DEC format for object.")}), 400

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
        # Determine the effective location name
        loc_name = plot_loc_name if plot_loc_name else default_loc_name

        # --- SIMULATION MODE SUPPORT ---
        # If sim_date is provided, use it to override the calculation anchor
        sim_date_str = request.args.get('sim_date')

        # Retrieve config for this location from the loaded context
        target_loc_conf = g.locations.get(loc_name, {}) if hasattr(g, 'locations') else {}

        # Resolve Lat/Lon/Tz:
        # 1. Use explicit query params if present (e.g. custom link)
        # 2. Fallback to the named location's config (e.g. from inspiration modal)
        # 3. Fallback to the session defaults
        lat = float(plot_lat_str) if plot_lat_str else target_loc_conf.get('lat', default_lat)
        lon = float(plot_lon_str) if plot_lon_str else target_loc_conf.get('lon', default_lon)
        tz_name = plot_tz_name if plot_tz_name else target_loc_conf.get('timezone', default_tz)

        if lat is None or lon is None:
            raise ValueError("Could not determine latitude or longitude.")
        if not tz_name:
            tz_name = "UTC"

        local_tz = pytz.timezone(tz_name)
    except Exception as e:
        print(f"[API Plot Data] Error parsing location parameters: {e}")
        return jsonify({"error": f"Invalid location or timezone data: {e}"}), 400

    if sim_date_str:
        try:
            now_local = local_tz.localize(datetime.strptime(sim_date_str, '%Y-%m-%d'))
        except ValueError:
            now_local = datetime.now(local_tz)
    else:
        now_local = datetime.now(local_tz)

        # Determine default date based on Noon-to-Noon logic (consistent with dashboard)
    if not sim_date_str and now_local.hour < 12:
        default_date = now_local.date() - timedelta(days=1)
    else:
        default_date = now_local.date()

    day = int(request.args.get('day', default_date.day))
    month = int(request.args.get('month', default_date.month))
    year = int(request.args.get('year', default_date.year))
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
                                                    sampling_interval_minutes=sampling_interval)
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
            # Enforce the active threshold floor on the mask before processing
            clamped_mask = [[p[0], max(p[1], altitude_threshold)] for p in horizon_mask]
            sorted_mask = sorted(clamped_mask, key=lambda p: p[0])
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

        # Vectorized calculation (1 call instead of ~288 calls)
        t_ast_array = Time(times_utc)
        # altaz_frame is already defined in step 3 using times_utc, so we can reuse it or rely on the Time array
        moon_icrs = get_body('moon', t_ast_array, location=location)
        moon_altaz = moon_icrs.transform_to(altaz_frame)

        moon_altitudes = moon_altaz.alt.deg.tolist()
        moon_azimuths = ((moon_altaz.az.deg + 360.0) % 360.0).tolist()

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
        return jsonify({"error": _("Could not generate time series for plot.")}), 500
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

    return jsonify(plot_data)


@api_bp.route('/api/get_observable_objects')
def get_observable_objects():
    """
    Returns active objects observable tonight from the current location.
    Used by the secondary object comparison dropdown in graph view.

    Query params:
        exclude: Object name to exclude (the primary object being viewed)
        lat: Override latitude (from dashboard location)
        lon: Override longitude (from dashboard location)
        tz: Override timezone

    Returns:
        {
            "objects": [
                {
                    "object_name": str,
                    "common_name": str,
                    "observable_minutes": int,
                    "max_altitude": float
                },
                ...
            ]
        }

    Objects are:
        - Active projects only (active_project=True)
        - Have valid RA/DEC coordinates
        - Observable tonight (duration > 0)
        - Excludes the primary object if specified
        - Sorted by observable_minutes descending
        - Limited to top 20
    """
    from modules.astro_calculations import calculate_observable_duration_vectorized, calculate_sun_events_cached

    load_global_request_context()
    load_full_astro_context()

    # Get exclusion parameter
    exclude_name = request.args.get('exclude', '').strip()

    # Get location context - prefer query params (dashboard location) over defaults
    lat_override = request.args.get('lat')
    lon_override = request.args.get('lon')
    tz_override = request.args.get('tz')
    location_name_param = request.args.get('location')
    # Get graph date parameters (day/month/year from user selection)
    day_param = request.args.get('day')
    month_param = request.args.get('month')
    year_param = request.args.get('year')

    try:
        if lat_override is not None:
            lat = float(lat_override)
        else:
            lat = getattr(g, 'lat', None)

        if lon_override is not None:
            lon = float(lon_override)
        else:
            lon = getattr(g, 'lon', None)

        if tz_override:
            tz_name = tz_override
        else:
            tz_name = getattr(g, 'tz_name', 'UTC')
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid location parameters: {e}", "objects": []}), 400

    altitude_threshold = 20

    # Get user's altitude threshold from config
    try:
        user_cfg = getattr(g, 'user_config', {}) or {}
        altitude_threshold = user_cfg.get("altitude_threshold", 20)
    except Exception:
        pass

    if lat is None or lon is None:
        return jsonify({"error": _("Location not configured"), "objects": []}), 400

    # Determine date for calculations - use passed-in graph date, not server's current time
    local_tz = pytz.timezone(tz_name)

    # Priority 1: Use the graph date passed from frontend (day/month/year)
    if day_param and month_param and year_param:
        try:
            # Parse the date components from strings
            day = int(day_param)
            month = int(month_param)
            year = int(year_param)
            calc_date = datetime(year, month, day).strftime('%Y-%m-%d')
        except (ValueError, TypeError) as e:
            print(f"[API Observable Objects] Invalid date parameters: {e}")
            return jsonify({"error": f"Invalid date parameters: {e}", "objects": []}), 400
    else:
        # Priority 2: Fallback to noon-to-noon logic with current server time (if no date passed)
        now_local = datetime.now(local_tz)
        if now_local.hour < 12:
            calc_date = (now_local.date() - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            calc_date = now_local.date().strftime('%Y-%m-%d')

    db = get_db()
    try:
        # Get current user
        if SINGLE_USER_MODE:
            username = "default"
        elif current_user.is_authenticated:
            username = current_user.username
        else:
            username = "guest_user"

        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            return jsonify({"error": _("User not found"), "objects": []}), 401

        # Query active objects with valid coordinates
        active_objects = db.query(AstroObject).filter_by(
            user_id=user.id,
            active_project=True
        ).filter(
            AstroObject.ra_hours != None,
            AstroObject.dec_deg != None
        ).all()

        # Get horizon mask from database with proper Location lookup
        horizon_mask = None
        location_key_for_cache = "default"

        # Priority 1: Use location name if provided (most accurate)
        if location_name_param:
            try:
                location_obj = db.query(Location).options(
                    selectinload(Location.horizon_points)
                ).filter_by(user_id=user.id, name=location_name_param).one_or_none()
                if location_obj:
                    horizon_mask = [[hp.az_deg, hp.alt_min_deg] for hp in
                                    sorted(location_obj.horizon_points, key=lambda p: p.az_deg)]
                    location_key_for_cache = location_obj.name.lower().replace(' ', '_')
                    # Ensure lat/lon/tz match the location's configured values
                    lat = location_obj.lat
                    lon = location_obj.lon
                    tz_name = location_obj.timezone
                    if location_obj.altitude_threshold is not None:
                        altitude_threshold = location_obj.altitude_threshold
            except Exception as e:
                print(f"[API Observable Objects] Error fetching location from DB: {e}")

        # Priority 2: Fallback to finding location by lat/lon/tz (if no location name provided)
        if horizon_mask is None and hasattr(g, 'locations') and isinstance(g.locations, dict):
            for loc_name, loc_details in g.locations.items():
                if (abs(loc_details.get('lat', 999) - lat) < 0.001 and
                    abs(loc_details.get('lon', 999) - lon) < 0.001 and
                    loc_details.get('timezone') == tz_name):
                    horizon_mask = loc_details.get('horizon_mask')
                    location_key_for_cache = loc_name.lower().replace(' ', '_')
                    break

        # Cache key: username + date + location + threshold uniquely identify the computation
        obs_cache_key = (
            f"obs_objects:{username}:{calc_date}:{lat:.4f}:{lon:.4f}"
            f":{altitude_threshold}:{location_key_for_cache}"
        )

        if obs_cache_key in nightly_curves_cache:
            full_list = nightly_curves_cache[obs_cache_key]
        else:
            full_list = []
            for obj in active_objects:
                try:
                    duration_td, max_alt, _, _ = calculate_observable_duration_vectorized(
                        ra=obj.ra_hours,
                        dec=obj.dec_deg,
                        lat=lat,
                        lon=lon,
                        local_date=calc_date,
                        tz_name=tz_name,
                        altitude_threshold=altitude_threshold,
                        horizon_mask=horizon_mask
                    )

                    observable_minutes = int(duration_td.total_seconds() / 60)

                    if observable_minutes > 0:
                        full_list.append({
                            "object_name": obj.object_name,
                            "common_name": obj.common_name or obj.object_name,
                            "observable_minutes": observable_minutes,
                            "max_altitude": round(max_alt, 1)
                        })
                except Exception as e:
                    print(f"[API Observable Objects] Error calculating "
                          f"for {obj.object_name}: {e}")
                    continue
            nightly_curves_cache[obs_cache_key] = full_list

        # Post-filter: exclude primary object, sort, limit
        observable_list = [
            o for o in full_list
            if o["object_name"] != exclude_name
        ]
        observable_list.sort(
            key=lambda x: x['observable_minutes'], reverse=True
        )
        observable_list = observable_list[:20]

        return jsonify({"objects": observable_list})

    except Exception as e:
        print(f"[API Observable Objects] Error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e), "objects": []}), 500


@api_bp.route('/api/get_weather_forecast')
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

        r = requests.get(base_url, params=params, timeout=DEFAULT_HTTP_TIMEOUT)  # 10-second timeout

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

        # Define a sequential wrapper to prevent CPU spikes from parallel location processing
        def _process_locations_sequentially(uid, uname, loc_list, cfg, interval):
            for loc_name in loc_list:
                user_log_key = get_user_log_string(uid, uname)
                safe_log_key = user_log_key.replace(" | ", "_").replace(".", "").replace(" ", "_")
                status_key = f"({user_log_key})_{loc_name}"
                cache_filename = os.path.join(CACHE_DIR,
                                              f"outlook_cache_{safe_log_key}_{loc_name.lower().replace(' ', '_')}.json")

                cache_worker_status[status_key] = "starting"
                # Call update_outlook_cache directly (blocking) to enforce sequential execution
                try:
                    update_outlook_cache(uid, status_key, cache_filename, loc_name, cfg, interval, None)
                except Exception as e:
                    print(f"Error in sequential update for {loc_name}: {e}")

        # Filter: Only process ACTIVE locations
        active_loc_names = [name for name, data in locations.items() if data.get('active', True)]

        # Start a SINGLE thread that processes all active locations one by one
        thread = threading.Thread(target=_process_locations_sequentially, args=(
            g.db_user.id,
            g.db_user.username,
            active_loc_names,  # Only process active locations
            user_cfg.copy(),
            sampling_interval
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
                # Query only active users from the DbUser table
                all_db_users = _db.query(DbUser).filter(DbUser.active == True).all()
                usernames_to_check = [u.username for u in all_db_users]
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
def update_outlook_cache(user_id, status_key, cache_filename, location_name, user_config, sampling_interval,
                         sim_date_str=None):
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
            except Exception as e:
                # Fail gracefully if table is missing (e.g. during tests/migrations)
                print(f"[OUTLOOK WORKER {status_key}] WARN: Could not fetch framings: {e}")

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

            # Use simulated date if provided, otherwise 'now'
            if sim_date_str:
                try:
                    start_date = datetime.strptime(sim_date_str, '%Y-%m-%d').date()
                except ValueError:
                    start_date = datetime.now(local_tz).date()
            else:
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
                        score_duration = min(obs_duration.total_seconds() / SCORING_WINDOW_SECONDS, 1)
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
    Refactored to use Vectorized Astropy operations for massive speedup.
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
            tz_name = "UTC"

        # --- 2. GET LOCATION & DATE VARS ---
        observing_date_for_calcs = datetime.now(local_tz) - timedelta(hours=12)
        local_date = observing_date_for_calcs.strftime('%Y-%m-%d')
        lat = float(user_config["locations"][location_name]["lat"])
        lon = float(user_config["locations"][location_name]["lon"])
        altitude_threshold = user_config.get("altitude_threshold", 20)
        try:
            horizon_mask = user_config.get("locations", {}).get(location_name, {}).get("horizon_mask")
        except Exception:
            horizon_mask = None

            # --- 3. PREPARE VECTORS ---
        enabled_objects = [o for o in user_config.get("objects", []) if o.get("enabled", True) and o.get("Object")]

        if not enabled_objects:
            return

        # Extract Arrays
        ra_list = []
        dec_list = []
        obj_names = []

        # Check user preference for optimization
        calc_invisible = user_config.get("calc_invisible", False)

        for obj in enabled_objects:
            try:
                r = float(obj.get("RA", 0))
                d = float(obj.get("DEC", 0))
                obj_name = obj.get("Object")

                # --- GEOMETRIC PRE-FILTER (Smart Skip) ---
                if not calc_invisible:
                    max_culm = 90.0 - abs(lat - d)

                    if max_culm < altitude_threshold:
                        # Object never rises above threshold. Cache immediately as impossible.
                        cache_key = f"{username}_{obj_name.lower().replace(' ', '_')}_{local_date}_{lat:.4f}_{lon:.4f}_{altitude_threshold}_{sampling_interval}"
                        nightly_curves_cache[cache_key] = {
                            "times_local": [],
                            "altitudes": [],
                            "azimuths": [],
                            "transit_time": "N/A",
                            "obs_duration_minutes": 0,
                            "max_altitude": round(max_culm, 1),
                            "alt_11pm": "N/A",
                            "az_11pm": "N/A",
                            "is_obstructed_at_11pm": False,
                            "is_geometrically_impossible": True
                        }
                        continue  # Skip adding to vectors

                # If visible, add to lists for heavy calculation
                ra_list.append(r)
                dec_list.append(d)
                obj_names.append(obj_name)
            except (ValueError, TypeError):
                continue

        if not ra_list:
            return

        # --- 4. VECTORIZED CALCULATION ---
        # A. Time Grid (Once)
        times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)
        location_earth = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)

        # B. Coordinate Frame (Broadcasting Time)
        # Create AltAz frame with the time array. Astropy will broadcast this against the object array.
        altaz_frame = AltAz(obstime=times_utc, location=location_earth)

        # C. SkyCoords (Vectorized)
        # Create one SkyCoord containing ALL objects
        sky_coords = SkyCoord(ra=ra_list * u.hourangle, dec=dec_list * u.deg)

        # D. Transform (The Heavy Lift)
        # Result shape: (N_times, N_objects). We use newaxis to align the time dimension for broadcasting.
        transformed = sky_coords.transform_to(altaz_frame[:, np.newaxis])

        # Extract Data (Result is [Time, Object])
        all_alts = transformed.alt.deg  # Shape: (T, N)
        all_azs = transformed.az.deg  # Shape: (T, N)

        # Transpose to (N, T) for easier processing per object
        all_alts = all_alts.T
        all_azs = all_azs.T

        # --- 5. PROCESS RESULTS & CACHE ---
        fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)

        # Pre-calculate Vectorized Horizon Mask (xp, fp) to avoid Python looping
        mask_xp, mask_fp = None, None

        if horizon_mask and len(horizon_mask) > 1:
            # 1. Sort and Clamp: Ensure floors are met
            sorted_clamped = sorted([[p[0], max(p[1], altitude_threshold)] for p in horizon_mask], key=lambda x: x[0])

            # 2. Build Profile Arrays (Replicating the 'Wall' logic from interpolate_horizon)
            # We construct the x (azimuth) and y (altitude) arrays once
            xp = [0.0]
            fp = [float(altitude_threshold)]

            # Wall UP at start of mask
            xp.append(sorted_clamped[0][0] - 0.001)
            fp.append(float(altitude_threshold))

            # Mask Points
            for az, alt in sorted_clamped:
                xp.append(az)
                fp.append(alt)

            # Wall DOWN at end of mask
            xp.append(sorted_clamped[-1][0] + 0.001)
            fp.append(float(altitude_threshold))

            # End at 360
            xp.append(360.0)
            fp.append(float(altitude_threshold))

            mask_xp = np.array(xp)
            mask_fp = np.array(fp)

        for i, obj_name in enumerate(obj_names):
            ra = ra_list[i]
            dec = dec_list[i]

            cache_key = f"{username}_{obj_name.lower().replace(' ', '_')}_{local_date}_{lat:.4f}_{lon:.4f}_{altitude_threshold}_{sampling_interval}"
            if cache_key in nightly_curves_cache:
                continue

            # Extract specific arrays
            altitudes = all_alts[i]
            azimuths = all_azs[i]

            # Calculate Visibility Mask using NumPy Interp (C-Speed)
            if mask_xp is not None:
                # This replaces the slow list comprehension
                min_alts = np.interp(azimuths, mask_xp, mask_fp)
                visible_mask = altitudes >= min_alts
            else:
                visible_mask = altitudes >= altitude_threshold

            obs_duration_minutes = np.sum(visible_mask) * sampling_interval
            max_alt = np.max(altitudes)

            # Transit
            transit_time = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)

            # 11 PM Logic
            alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)

            is_obstructed_at_11pm = False
            if mask_xp is not None:
                # Also optimize the single-point check
                required_altitude_11pm = np.interp(az_11pm, mask_xp, mask_fp)
                if alt_11pm >= altitude_threshold and alt_11pm < required_altitude_11pm:
                    is_obstructed_at_11pm = True

            nightly_curves_cache[cache_key] = {
                "times_local": times_local,
                "altitudes": altitudes,
                "azimuths": azimuths,
                "transit_time": transit_time,
                "obs_duration_minutes": int(obs_duration_minutes),
                "max_altitude": round(float(max_alt), 1),
                "alt_11pm": f"{alt_11pm:.2f}",
                "az_11pm": f"{az_11pm:.2f}",
                "is_obstructed_at_11pm": is_obstructed_at_11pm
            }

        # --- 4. TRIGGER OUTLOOK CACHE (Unchanged) ---
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
            # Skip disabled objects to save CPU
            if not obj_entry.get("enabled", True):
                continue

            time.sleep(0.01)
            obj_name = obj_entry.get("Object")
            if not obj_name: continue

            cache_key = f"{username}_{obj_name.lower().replace(' ', '_')}_{local_date}_{lat:.4f}_{lon:.4f}_{altitude_threshold}_{sampling_interval}"
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
        # Generate standard filename keys
        # We need the user ID here. In warm_main_cache, we only have 'username'.
        # We must re-fetch the ID or pass it in.
        # Since warm_main_cache is called from trigger_startup_cache_workers,
        # let's look up the ID inside the function if we don't have it,
        # OR rely on the fix below which fetches it before the thread starts.

        db = get_db()
        u_obj = db.query(DbUser).filter_by(username=username).first()
        u_id = u_obj.id if u_obj else 0

        user_log_key = get_user_log_string(u_id, username)
        safe_log_key = user_log_key.replace(" | ", "_").replace(".", "").replace(" ", "_")
        loc_safe = location_name.lower().replace(' ', '_')

        cache_filename = os.path.join(CACHE_DIR, f"outlook_cache_{safe_log_key}_{loc_safe}.json")

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
            u_obj = db.query(DbUser).filter_by(username=username).first()
            u_id = u_obj.id if u_obj else 0

            user_log_key = f"({u_id} | {username})"
            safe_log_key = f"{u_id}_{username}"
            status_key = f"{user_log_key}_{location_name}"
            cache_filename = os.path.join(CACHE_DIR,
                                          f"outlook_cache_{safe_log_key}_{location_name.lower().replace(' ', '_')}.json")

            thread = threading.Thread(target=update_outlook_cache,
                                      args=(u_id, status_key, cache_filename, location_name, user_config.copy(),
                                            sampling_interval, None))  # Pass None for sim_date
            # --- FIX END ---
            thread.start()

    except Exception as e:
        import traceback
        print(f"❌ [CACHE WARMER] FATAL ERROR during cache warming for '{location_name}': {e}")
        traceback.print_exc()

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
                resp = requests.post(endpoint, json=payload, timeout=TELEMETRY_TIMEOUT)
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

# --- Telemetry diagnostics route ---
@api_bp.route('/telemetry/debug', methods=['GET'])
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


@api_bp.route('/api/latest_version')
def get_latest_version():
    """An API endpoint for the frontend to check for updates."""
    return jsonify(LATEST_VERSION_INFO)


@tools_bp.route('/add_component', methods=['POST'])
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
            flash(_("Error: Unknown component type '%(component_type)s'.", component_type=form_kind), "error")
            return redirect(url_for('core.config_form'))

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
        flash(_("Component '%(component_name)s' added successfully.", component_name=new_comp.name), "success")
    except Exception as e:
        db.rollback()
        flash(_("Error adding component: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))


@tools_bp.route('/update_component', methods=['POST'])
@login_required
def update_component():
    db = get_db()
    try:
        form = request.form
        comp_id = int(form.get('component_id'))
        comp = db.get(Component, comp_id)

        # Security check: ensure component belongs to the current user
        if comp.user.username != ("default" if SINGLE_USER_MODE else current_user.username):
            flash(_("Authorization error."), "error")
            return redirect(url_for('core.config_form'))

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
        flash(_("Component '%(component_name)s' updated successfully.", component_name=comp.name), "success")
    except Exception as e:
        db.rollback()
        flash(_("Error updating component: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))


@tools_bp.route('/add_rig', methods=['POST'])
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
            # Guide optics FK fields and OAG flag
            rig.guide_telescope_id = safe_int(form.get('guide_telescope_id'))
            rig.guide_camera_id = safe_int(form.get('guide_camera_id'))
            rig.guide_is_oag = form.get('guide_is_oag') == 'on'
            flash(_("Rig '%(rig_name)s' updated successfully.", rig_name=rig.rig_name), "success")
        else:  # Add
            new_rig = Rig(
                user_id=user.id, rig_name=form.get('rig_name'),
                telescope_id=tel_id, camera_id=cam_id, reducer_extender_id=red_id,
                # Guide optics FK fields and OAG flag
                guide_telescope_id=safe_int(form.get('guide_telescope_id')),
                guide_camera_id=safe_int(form.get('guide_camera_id')),
                guide_is_oag=form.get('guide_is_oag') == 'on'
            )
            db.add(new_rig)
            rig = new_rig  # Reference the new object for update below
            flash(_("Rig '%(rig_name)s' created successfully.", rig_name=new_rig.rig_name), "success")

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
        flash(_("Error saving rig: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))

@tools_bp.route('/delete_component', methods=['POST'])
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
            flash(_("Cannot delete component: It is used in at least one rig."), "error")
        else:
            comp_to_delete = db.get(Component, comp_id)
            db.delete(comp_to_delete)
            db.commit()
            flash(_("Component deleted successfully."), "success")
    except Exception as e:
        db.rollback()
        flash(_("Error deleting component: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))

@tools_bp.route('/delete_rig', methods=['POST'])
@login_required
def delete_rig():
    db = get_db()
    try:
        rig_id = int(request.form.get('rig_id'))
        rig_to_delete = db.get(Rig, rig_id)
        db.delete(rig_to_delete)
        db.commit()
        flash(_("Rig deleted successfully."), "success")
    except Exception as e:
        db.rollback()
        flash(_("Error deleting rig: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))


@tools_bp.route('/set_rig_sort_preference', methods=['POST'])
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

@tools_bp.route('/get_rig_data')
@login_required
def get_rig_data():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
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

        # Resolve guide optics FK references
        guide_tel_obj = next((c for c in telescopes if c.id == r.guide_telescope_id), None) if r.guide_telescope_id else None
        guide_cam_obj = next((c for c in cameras if c.id == r.guide_camera_id), None) if r.guide_camera_id else None

        # Calculate dither recommendation if guide optics are configured
        dither_rec = None
        guide_fl = None
        guide_pixel_size = None

        # Determine guide focal length based on OAG setting
        if r.guide_is_oag:
            # OAG uses main scope focal length
            guide_fl = efl  # effective focal length already accounts for reducer/extender
        elif guide_tel_obj and guide_tel_obj.focal_length_mm:
            guide_fl = guide_tel_obj.focal_length_mm

        # Get guide camera pixel size
        if guide_cam_obj and guide_cam_obj.pixel_size_um:
            guide_pixel_size = guide_cam_obj.pixel_size_um

        # Calculate dither if we have all required values
        if (cam_obj and cam_obj.pixel_size_um and efl and
            guide_pixel_size and guide_fl):
            dither_rec = calculate_dither_recommendation(
                main_pixel_size_um=cam_obj.pixel_size_um,
                main_focal_length_mm=efl,
                guide_pixel_size_um=guide_pixel_size,
                guide_focal_length_mm=guide_fl,
                desired_main_shift_px=DEFAULT_DITHER_MAIN_SHIFT_PX
            )

        rigs_list.append({
            "rig_id": r.id, "rig_name": r.rig_name,
            "telescope_id": r.telescope_id, "camera_id": r.camera_id, "reducer_extender_id": r.reducer_extender_id,
            "effective_focal_length": efl, "f_ratio": f_ratio,
            "image_scale": scale, "fov_w_arcmin": fov_w, "fov_h_arcmin": fov_h,
            # Main equipment names for display
            "telescope_name": tel_obj.name if tel_obj else None,
            "camera_name": cam_obj.name if cam_obj else None,
            "reducer_name": red_obj.name if red_obj else None,
            # Guide optics FK fields and OAG flag
            "guide_telescope_id": r.guide_telescope_id,
            "guide_camera_id": r.guide_camera_id,
            "guide_is_oag": r.guide_is_oag,
            # Resolved guide equipment names for display
            "guide_telescope_name": guide_tel_obj.name if guide_tel_obj else None,
            "guide_camera_name": guide_cam_obj.name if guide_cam_obj else None,
            # Dither recommendation (None if guide optics not configured)
            "dither_recommendation": dither_rec
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


def sanitize_object_name(object_name):
    return object_name.replace("/", "-")

@app.template_filter('sanitize_html')
def sanitize_html_filter(html_content):
    """
    Jinja2 template filter to sanitize user-provided HTML content.
    Allows safe HTML tags and attributes while stripping potentially dangerous content.
    Use this with | safe when rendering user content: {{ user_content | sanitize_html | safe }}
    """
    if not html_content:
        return ""

    from bleach.css_sanitizer import CSSSanitizer

    SAFE_TAGS = ['p', 'strong', 'em', 'b', 'i', 'u', 'del', 'strike', 'sub', 'sup',
                 'ul', 'ol', 'li', 'br', 'div', 'img', 'a', 'figure', 'figcaption', 'span',
                 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre', 'code']
    SAFE_ATTRS = {
        'img': ['src', 'alt', 'width', 'height', 'style'],
        'a': ['href', 'title'],
        '*': ['style', 'class']
    }
    SAFE_CSS = ['text-align', 'width', 'height', 'max-width', 'float', 'margin', 'margin-left', 'margin-right']
    css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS)

    return bleach.clean(html_content, tags=SAFE_TAGS, attributes=SAFE_ATTRS, css_sanitizer=css_sanitizer)

@app.context_processor
def inject_user_mode():
    from flask_login import current_user
    user_config = getattr(g, "user_config", {})
    theme_preference = user_config.get("theme_preference", "follow_system") if user_config else "follow_system"
    # Get current language from user config or session
    current_language = user_config.get("language") if user_config else None
    if not current_language:
        current_language = session.get("language", "en")
    return {
        "SINGLE_USER_MODE": SINGLE_USER_MODE,
        "current_user": current_user,
        "is_guest": getattr(g, "is_guest", False),
        "user_theme_preference": theme_preference,
        "current_language": current_language,
        "supported_languages": app.config.get("BABEL_SUPPORTED_LOCALES", ["en"]),
        "ai_enabled": bool(app.config.get("AI_API_KEY"))
    }




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


@app.context_processor
def inject_version():
    return dict(version=APP_VERSION)


@tools_bp.route('/download_config')
@login_required
def download_config():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash(_("User not found."), "error")
            return redirect(url_for('core.config_form'))

        # --- 1. Load base settings from UiPref ---
        config_doc = {}
        prefs = db.query(UiPref).filter_by(user_id=u.id).first()
        if prefs and prefs.json_blob:
            try:
                config_doc = json.loads(prefs.json_blob)
            except json.JSONDecodeError:
                pass  # Start with empty doc if JSON is corrupt

        # --- 2. Load Locations ---
        locs = db.query(Location).options(selectinload(Location.horizon_points)).filter_by(user_id=u.id).all()
        default_loc_name = next((l.name for l in locs if l.is_default), None)
        config_doc["default_location"] = default_loc_name
        config_doc["locations"] = {
            l.name: {
                **{
                    "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                    "altitude_threshold": l.altitude_threshold,
                    "active": l.active,
                    "comments": l.comments,
                    "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
                },
                **({"bortle_scale": l.bortle_scale} if l.bortle_scale is not None else {})
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
        flash(_("Error generating config file: %(error)s", error=e), "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('core.config_form'))


@tools_bp.route('/download_journal')
@login_required
def download_journal():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash(_("User not found."), "error")
            return redirect(url_for('core.config_form'))

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
                "rig_fov_h_snapshot": s.rig_fov_h_snapshot,
                "telescope_name_snapshot": s.telescope_name_snapshot,
                "reducer_name_snapshot": s.reducer_name_snapshot,
                "camera_name_snapshot": s.camera_name_snapshot,
                "custom_filter_data": s.custom_filter_data,
                "asiair_log_content": s.asiair_log_content,
                "phd2_log_content": s.phd2_log_content,
                "log_analysis_cache": s.log_analysis_cache,
            })

        # --- 3. Load Custom Filter Definitions ---
        custom_filters_db = db.query(UserCustomFilter).filter_by(user_id=u.id).order_by(UserCustomFilter.created_at).all()
        custom_filters_list = [
            {'key': cf.filter_key, 'label': cf.filter_label}
            for cf in custom_filters_db
        ]

        journal_doc = {
            "projects": projects_list,
            "custom_mono_filters": custom_filters_list,
            "sessions": sessions_list
        }

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
        flash(_("Error generating journal file: %(error)s", error=e), "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('core.config_form'))

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


@tools_bp.route('/import_journal', methods=['POST'])
@login_required
def import_journal():
    if 'file' not in request.files:
        flash(_("No file selected for journal import."), "error")
        return redirect(url_for('core.config_form'))

    file = request.files['file']
    if file.filename == '':
        flash(_("No file selected for journal import."), "error")
        return redirect(url_for('core.config_form'))

    if file and file.filename.endswith('.yaml'):
        try:
            contents = file.read().decode('utf-8')
            new_journal_data = yaml.safe_load(contents)

            if new_journal_data is None:
                new_journal_data = {"projects": [], "sessions": []}  # Handle empty file

            # Basic validation
            is_valid, message = validate_journal_data(new_journal_data)
            if not is_valid:
                flash(_("Invalid journal file structure: %(message)s", message=message), "error")
                return redirect(url_for('core.config_form'))

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
                flash(_("Journal imported successfully! (Previous journal data was replaced)"), "success")
            except Exception as e:
                db.rollback()
                print(f"[IMPORT_JOURNAL] DB Error: {e}")
                # Re-raise to hit the outer exception handler for detailed logging
                raise e
            # === END REFACTOR ===

            return redirect(url_for('core.config_form'))

        except yaml.YAMLError as ye:
            print(f"[IMPORT JOURNAL ERROR] Invalid YAML format: {ye}")
            flash(_("Import failed: Invalid YAML format in the journal file. %(error)s", error=ye), "error")
            return redirect(url_for('core.config_form'))
        except Exception as e:
            print(f"[IMPORT JOURNAL ERROR] {e}")
            # Clean up the error message for display
            err_msg = str(e)
            if "UNIQUE constraint failed" in err_msg:
                err_msg = "Data conflict detected. Please try again (the wipe logic should prevent this)."
            flash(_("Import failed: %(error)s", error=err_msg), "error")
            return redirect(url_for('core.config_form'))
    else:
        flash(_("Invalid file type. Please upload a .yaml journal file."), "error")
        return redirect(url_for('core.config_form'))


@tools_bp.route('/import_config', methods=['POST'])
@login_required
def import_config():
    if SINGLE_USER_MODE:
        username = "default"
    elif current_user.is_authenticated:
        username = current_user.username
    else:
        flash(_("Authentication error during import."), "error")
        return redirect(url_for('core.login'))

    try:
        if 'file' not in request.files:
            flash(_("No file selected for import."), "error")
            return redirect(url_for('core.config_form'))

        file = request.files['file']
        if file.filename == '':
            flash(_("No file selected for import."), "error")
            return redirect(url_for('core.config_form'))

        contents = file.read().decode('utf-8')
        new_config = yaml.safe_load(contents)

        valid, errors = validate_config(new_config)
        if not valid:
            error_message = f"Configuration validation failed: {json.dumps(errors, indent=2)}"
            flash(error_message, "error")
            return redirect(url_for('core.config_form'))

        # === START REFACTOR: FULL WIPE & REPLACE ===
        db = get_db()
        try:
            user = _upsert_user(db, username)

            print(f"[IMPORT_CONFIG] Wiping all existing config data for user '{username}'...")

            # Guard: Ensure import has at least one location before wiping existing ones
            imported_locations = new_config.get("locations", {})
            if not imported_locations or len(imported_locations) == 0:
                flash(_("Cannot import: Configuration must contain at least one location."), "error")
                return redirect(url_for('core.config_form'))

            # Guard: Ensure at least one imported location is active
            has_active_location = any(
                loc_data.get('active', True) for loc_data in imported_locations.values()
            )
            if not has_active_location:
                flash(_("Cannot import: Configuration must contain at least one active location."), "error")
                return redirect(url_for('core.config_form'))

            # 1. Delete existing locations (only if import has locations)
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
            flash(_("Config imported successfully! (Previous config was replaced)"), "success")
        except Exception as e:
            db.rollback()
            print(f"[IMPORT_CONFIG] DB Error: {e}")
            raise e
        # === END REFACTOR ===

        # Trigger background cache update for ACTIVE locations in the import
        # (Force refresh to ensure active projects match the new config)
        user_config_for_thread = new_config.copy()

        # Determine sampling interval from the imported config if possible, else fallback
        import_interval = 15
        if SINGLE_USER_MODE:
            import_interval = user_config_for_thread.get('sampling_interval_minutes') or 15

        locations_in_import = user_config_for_thread.get('locations', {})

        for loc_name, loc_data in locations_in_import.items():
            # FILTER: Skip inactive locations to prevent CPU spikes
            if not loc_data.get('active', True):
                continue

            # 1. Standardize filename construction (Matches Master Cache)
            user_log_key = get_user_log_string(user_id_for_thread, username)
            safe_log_key = user_log_key.replace(" | ", "_").replace(".", "").replace(" ", "_")
            loc_safe = loc_name.lower().replace(' ', '_')

            status_key = f"({user_log_key})_{loc_name}"
            cache_filename = os.path.join(CACHE_DIR, f"outlook_cache_{safe_log_key}_{loc_safe}.json")

            # 2. REMOVED 'if not os.path.exists': Always force update on import

            thread = threading.Thread(target=update_outlook_cache,
                                      args=(user_id_for_thread, status_key, cache_filename, loc_name,
                                            user_config_for_thread,
                                            import_interval, None))
            thread.start()

        return redirect(url_for('core.config_form'))

    except yaml.YAMLError as ye:
        flash(_("Import failed: Invalid YAML. (%(error)s)", error=ye), "error")
        return redirect(url_for('core.config_form'))
    except Exception as e:
        flash(_("Import failed: %(error)s", error=str(e)), "error")
        return redirect(url_for('core.config_form'))

@tools_bp.route('/import_catalog/<pack_id>', methods=['POST'])
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
            flash(_("Authentication error during catalog import."), "error")
            return redirect(url_for('core.login'))
        username = current_user.username

    db = get_db()
    try:
        user = _upsert_user(db, username)

        catalog_data, meta = load_catalog_pack(pack_id)
        if not catalog_data or not isinstance(catalog_data, dict):
            flash(_("Catalog pack not found or invalid."), "error")
            return redirect(url_for('core.config_form'))

        created, enriched, skipped = import_catalog_pack_for_user(db, user, catalog_data, pack_id)
        db.commit()

        pack_name = (meta or {}).get("name") or pack_id
        msg = f"Catalog '{pack_name}': {created} new, {enriched} enriched (updated), {skipped} skipped."
        flash(msg, "success")
    except Exception as e:
        db.rollback()
        print(f"[CATALOG IMPORT] Error importing catalog pack '{pack_id}': {e}")
        flash(_("Catalog import failed due to an internal error."), "error")

    return redirect(url_for('core.config_form'))

# =============================================================================
# Protected Routes
# =============================================================================




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






@api_bp.route('/api/update_object', methods=['POST'])
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
            return jsonify({"status": "error", "message": _("Object not found")}), 404

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

        # --- Curation Fields ---
        obj.image_url = data.get('image_url')
        obj.image_credit = data.get('image_credit')
        obj.image_source_link = data.get('image_source_link')
        obj.description_text = data.get('description_text')
        obj.description_credit = data.get('description_credit')
        obj.description_source_link = data.get('description_source_link')
        # -----------------------

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




@api_bp.route('/api/get_object_list')
def get_object_list():
    load_full_astro_context()
    """
    A new, very fast endpoint that just returns the list of object names.
    """
    # Filter g.objects_list to return only enabled object names
    enabled_names = [o['Object'] for o in g.objects_list if o.get('enabled', True)]
    return jsonify({"objects": enabled_names})


@api_bp.route('/api/journal/objects')
@login_required
def get_journal_objects():
    """
    Returns a list of all objects that have journal sessions with any imaging data,
    sorted by most recent session date. Used by the journal object switcher.
    Includes first_session_date, first_session_id, and first_session_location for navigation.
    """
    db = get_db()
    user_id = g.db_user.id

    # Query all sessions with any imaging data (light subs > 0 OR calculated integration > 0)
    # This captures sessions with mono filter data that don't use number_of_subs_light
    sessions_with_data = db.query(
        JournalSession.id,
        JournalSession.object_name,
        JournalSession.date_utc,
        JournalSession.calculated_integration_time_minutes,
        JournalSession.location_name,
        AstroObject.common_name,
        AstroObject.id.label('astro_id')
    ).outerjoin(
        AstroObject,
        and_(AstroObject.user_id == user_id, AstroObject.object_name == JournalSession.object_name)
    ).filter(
        JournalSession.user_id == user_id,
        or_(
            JournalSession.number_of_subs_light > 0,
            JournalSession.calculated_integration_time_minutes > 0
        )
    ).order_by(
        JournalSession.object_name,
        JournalSession.date_utc.desc()
    ).all()

    # Aggregate by object_name
    objects_map = {}
    for session in sessions_with_data:
        object_name = session.object_name
        if not object_name:
            continue

        if object_name not in objects_map:
            objects_map[object_name] = {
                'id': session.astro_id,
                'name': session.common_name or object_name,
                'catalog_id': object_name,
                'total_minutes': 0,
                'last_session': None,
                'first_session_date': None,
                'first_session_id': None,
                'first_session_location': None
            }

        # Accumulate integration time
        if session.calculated_integration_time_minutes:
            objects_map[object_name]['total_minutes'] += session.calculated_integration_time_minutes

        # Track most recent session date (for sorting)
        if session.date_utc:
            if objects_map[object_name]['last_session'] is None or session.date_utc > objects_map[object_name]['last_session']:
                objects_map[object_name]['last_session'] = session.date_utc

        # Track first (oldest) session date, id, and location (for navigation)
        if session.date_utc:
            if objects_map[object_name]['first_session_date'] is None or session.date_utc < objects_map[object_name]['first_session_date']:
                objects_map[object_name]['first_session_date'] = session.date_utc
                objects_map[object_name]['first_session_id'] = session.id
                objects_map[object_name]['first_session_location'] = session.location_name

    # Convert to list and sort by last_session DESC
    result = []
    for obj in objects_map.values():
        total_hours = round(obj['total_minutes'] / 60.0, 1) if obj['total_minutes'] else 0.0
        first_session_id = obj['first_session_id']
        first_session_location = obj['first_session_location']

        # Only include location if it's a non-empty string
        first_session_location = first_session_location.strip() if first_session_location else None

        # Build URL with location parameter if available
        url_params = {'object_name': obj['catalog_id'], 'tab': 'journal'}
        if first_session_id:
            url_params['session_id'] = first_session_id
        if first_session_location:
            url_params['location'] = first_session_location

        result.append({
            'id': obj['id'],
            'name': obj['name'],
            'catalog_id': obj['catalog_id'],
            'total_hours': total_hours,
            'last_session': obj['last_session'].strftime('%Y-%m-%d') if obj['last_session'] else None,
            'first_session_date': obj['first_session_date'].strftime('%Y-%m-%d') if obj['first_session_date'] else None,
            'first_session_location': first_session_location,
            'url': url_for('core.graph_dashboard', **url_params, _external=False)
        })

    # Sort by last_session descending (most recent first)
    result.sort(key=lambda x: x['last_session'] or '0000-00-00', reverse=True)

    return jsonify(result)


@api_bp.route('/api/bulk_update_objects', methods=['POST'])
@login_required
def bulk_update_objects():
    data = request.get_json()
    action = data.get('action')  # 'enable', 'disable', 'delete'
    object_ids = data.get('object_ids', [])

    if not action or not object_ids:
        return jsonify({"status": "error", "message": "Missing action or object_ids"}), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        query = db.query(AstroObject).filter(
            AstroObject.user_id == user_id,
            AstroObject.object_name.in_(object_ids)
        )

        if action == 'delete':
            # Check for dependencies (Journals or Projects) before deleting
            safe_to_delete = []
            skipped_count = 0

            # We need to iterate to check relationships since bulk delete bypasses Python-level checks
            objects_to_check = query.all()

            for obj in objects_to_check:
                # Check for journal sessions using this object name
                has_journals = db.query(JournalSession).filter_by(
                    user_id=user_id, object_name=obj.object_name
                ).first()

                # Check for projects targeting this object
                has_projects = db.query(Project).filter_by(
                    user_id=user_id, target_object_name=obj.object_name
                ).first()

                if has_journals or has_projects:
                    skipped_count += 1
                else:
                    safe_to_delete.append(obj.object_name)

            if safe_to_delete:
                # Perform the delete only on safe IDs
                delete_q = db.query(AstroObject).filter(
                    AstroObject.user_id == user_id,
                    AstroObject.object_name.in_(safe_to_delete)
                )
                count = delete_q.delete(synchronize_session=False)
            else:
                count = 0

            msg = f"Deleted {count} objects."
            if skipped_count > 0:
                msg += f" (Skipped {skipped_count} objects used in Journals/Projects)"
        elif action == 'enable':
            count = query.update({AstroObject.enabled: True}, synchronize_session=False)
            msg = f"Enabled {count} objects."
        elif action == 'disable':
            count = query.update({AstroObject.enabled: False}, synchronize_session=False)
            msg = f"Disabled {count} objects."
        else:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        db.commit()
        return jsonify({"status": "success", "message": msg})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/bulk_fetch_details', methods=['POST'])
@login_required
def bulk_fetch_details():
    """Fetch missing details (type, magnitude, size, SB, constellation) for selected objects."""
    data = request.get_json()
    object_ids = data.get('object_ids', [])

    if not object_ids:
        return jsonify({"status": "error", "message": "No objects selected"}), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        # Query only the selected objects for this user
        objects_to_check = db.query(AstroObject).filter(
            AstroObject.user_id == user_id,
            AstroObject.object_name.in_(object_ids)
        ).all()

        if not objects_to_check:
            return jsonify({"status": "error", "message": "No valid objects found"}), 400

        updated_count = 0
        error_count = 0
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

                    # Fetch other details from external API
                    fetched_data = nova_data_fetcher.get_astronomical_data(obj.object_name)
                    if fetched_data.get("object_type"): obj.type = fetched_data["object_type"]
                    if fetched_data.get("magnitude"): obj.magnitude = str(fetched_data["magnitude"])
                    if fetched_data.get("size_arcmin"): obj.size = str(fetched_data["size_arcmin"])
                    if fetched_data.get("surface_brightness"): obj.sb = str(fetched_data["surface_brightness"])
                    updated_count += 1
                    time.sleep(0.5)  # Be kind to external APIs
                except Exception as e:
                    print(f"Failed to fetch details for {obj.object_name}: {e}")
                    error_count += 1

        db.commit()

        msg = f"Updated details for {updated_count} object(s)."
        if error_count > 0:
            msg += f" ({error_count} failed)"
        if updated_count == 0 and error_count == 0:
            msg = "No missing data found - all selected objects already have complete details."

        return jsonify({"status": "success", "message": msg, "updated": updated_count, "errors": error_count})

    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/find_duplicates')
@login_required
def find_duplicates():
    load_full_astro_context()
    user_id = g.db_user.id

    # 1. Get all objects with valid coordinates
    all_objects = [o for o in g.objects_list if o.get('RA (hours)') is not None and o.get('DEC (degrees)') is not None]

    if len(all_objects) < 2:
        return jsonify({"status": "success", "duplicates": []})

    # 2. Create SkyCoord objects
    ra_vals = [o['RA (hours)'] * 15.0 for o in all_objects]  # Convert to degrees
    dec_vals = [o['DEC (degrees)'] for o in all_objects]

    coords = SkyCoord(ra=ra_vals * u.deg, dec=dec_vals * u.deg)

    # 3. Find matches within 2.5 arcminutes
    # search_around_sky finds all pairs (i, j) where distance < limit
    # This includes (i, i) self-matches and (i, j) + (j, i) duplicates
    idx1, idx2, d2d, d3d = search_around_sky(coords, coords, seplimit=2.5 * u.arcmin)

    potential_duplicates = []
    seen_pairs = set()

    for i, j, dist in zip(idx1, idx2, d2d):
        if i >= j: continue  # Skip self-matches and reverse duplicates

        obj_a = all_objects[i]
        obj_b = all_objects[j]

        # Create a unique key for this pair
        pair_key = tuple(sorted([obj_a['Object'], obj_b['Object']]))
        if pair_key in seen_pairs: continue
        seen_pairs.add(pair_key)

        potential_duplicates.append({
            "object_a": obj_a,
            "object_b": obj_b,
            "separation_arcmin": round(dist.to(u.arcmin).value, 2)
        })

    return jsonify({"status": "success", "duplicates": potential_duplicates})


@api_bp.route('/api/merge_objects', methods=['POST'])
@login_required
def merge_objects():
    data = request.get_json()
    keep_id = data.get('keep_id')
    merge_id = data.get('merge_id')

    if not keep_id or not merge_id:
        return jsonify({"status": "error", "message": "Missing object IDs"}), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        # 1. Fetch Objects
        obj_keep = db.query(AstroObject).filter_by(user_id=user_id, object_name=keep_id).one_or_none()
        obj_merge = db.query(AstroObject).filter_by(user_id=user_id, object_name=merge_id).one_or_none()

        if not obj_keep or not obj_merge:
            return jsonify({"status": "error", "message": "One or both objects not found."}), 404

        print(f"[MERGE] Merging '{merge_id}' INTO '{keep_id}'...")

        # 2. Re-link Journals
        journals = db.query(JournalSession).filter_by(user_id=user_id, object_name=merge_id).all()
        for j in journals:
            j.object_name = keep_id
        print(f"   -> Moved {len(journals)} journal sessions.")

        # 3. Re-link Projects
        projects = db.query(Project).filter_by(user_id=user_id, target_object_name=merge_id).all()
        for p in projects:
            p.target_object_name = keep_id
        print(f"   -> Updated {len(projects)} projects.")

        # 4. Handle Framings
        framing_keep = db.query(SavedFraming).filter_by(user_id=user_id, object_name=keep_id).one_or_none()
        framing_merge = db.query(SavedFraming).filter_by(user_id=user_id, object_name=merge_id).one_or_none()

        if framing_merge:
            if not framing_keep:
                # Move framing to the kept object
                framing_merge.object_name = keep_id
                print(f"   -> Moved framing from {merge_id} to {keep_id}.")
            else:
                # Conflict: Keep existing framing on target, delete merged one
                db.delete(framing_merge)
                print(f"   -> Deleted conflicting framing from {merge_id}.")

        # 5. Merge Notes (Append if different)
        if obj_merge.project_name:
            if not obj_keep.project_name:
                obj_keep.project_name = obj_merge.project_name
            elif obj_merge.project_name not in obj_keep.project_name:
                obj_keep.project_name += f"<br><hr><strong>Merged Notes ({merge_id}):</strong><br>{obj_merge.project_name}"

        # 6. Delete the Merged Object
        db.delete(obj_merge)

        db.commit()
        return jsonify({"status": "success", "message": f"Successfully merged '{merge_id}' into '{keep_id}'."})

    except Exception as e:
        db.rollback()
        print(f"[MERGE ERROR] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@api_bp.route('/api/help/img/<path:filename>')
def get_help_image(filename):
    """Serves images located in the help_docs directory."""
    return send_from_directory(os.path.join(_project_root, 'help_docs'), filename)

@api_bp.route('/api/help/<topic_id>')
def get_help_content(topic_id):
    """
    Reads a markdown file from help_docs/{locale}/, converts it to HTML, and returns it.
    Falls back to English if the localized file doesn't exist.
    """
    # 1. Sanitize input to prevent directory traversal
    safe_topic = "".join([c for c in topic_id if c.isalnum() or c in "_-"])

    # 2. Determine locale and build file path with fallback
    locale = get_locale()
    lang = str(locale).split('_')[0] if locale else 'en'
    file_path = os.path.join(_project_root, 'help_docs', lang, f'{safe_topic}.md')

    # 3. Fallback to English if localized file doesn't exist
    if not os.path.exists(file_path):
        file_path = os.path.join(_project_root, 'help_docs', 'en', f'{safe_topic}.md')

    # 4. Check if file exists (even after fallback)
    if not os.path.exists(file_path):
        return jsonify({
            "error": True,
            "html": f"<h3>Topic Not Found</h3><p>No help file found for ID: <code>{safe_topic}</code></p>"
        }), 404

    # 5. Read and convert
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
            # Extensions: 'fenced_code' adds support for ```code blocks```
            html_content = markdown.markdown(text, extensions=['fenced_code', 'tables'])
            return jsonify({"status": "success", "html": html_content})
    except Exception as e:
        return jsonify({"error": True, "html": _("<p>Error reading help file: %(error)s</p>", error=str(e))}), 500

@api_bp.route('/api/get_object_data/<path:object_name>')
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

        # --- 5. Perform Calculations (FIXED DATE LOGIC) ---
        local_tz = pytz.timezone(tz_name)
        current_datetime_local = datetime.now(local_tz)

        # Determine "Observing Night" Date
        # If it's before noon, we associate this time with the previous night's session
        # to ensure the time array (which starts at noon) covers the current moment.
        if current_datetime_local.hour < 12:
            local_date = (current_datetime_local.date() - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            local_date = current_datetime_local.strftime('%Y-%m-%d')

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

        cache_key = f"{username}_{object_name.lower().replace(' ', '_')}_{local_date}_{lat:.4f}_{lon:.4f}_{altitude_threshold}_{sampling_interval}"

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

@api_bp.route('/api/get_desktop_data_batch')
def get_desktop_data_batch():
    # --- Manual Auth Check for Guest Support ---
    if not (current_user.is_authenticated or SINGLE_USER_MODE or getattr(g, 'is_guest', False)):
        return jsonify({"error": "Unauthorized"}), 401
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

        # 3. Get Object Slice (Only Enabled Objects)
        # OPTIMIZATION: Fetch batch first to determine if there are more results
        batch_objects = db.query(AstroObject).filter_by(user_id=user.id, enabled=True)\
            .order_by(AstroObject.object_name)\
            .offset(offset)\
            .limit(limit)\
            .all()

        # Determine has_more flag based on whether we got a full page
        has_more = len(batch_objects) == limit

        # Only fetch total_count if offset is 0 (first page) to avoid double query on pagination
        # For subsequent pages, client can rely on has_more flag
        total_count = None
        if offset == 0:
            total_count = db.query(func.count(AstroObject.id))\
                .filter_by(user_id=user.id, enabled=True)\
                .scalar() or 0

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

            # --- SIMULATION MODE ---
        sim_date_str = request.args.get('sim_date')
        if sim_date_str:
            try:
                # Use current wall-clock time combined with simulated date
                sim_date = datetime.strptime(sim_date_str, '%Y-%m-%d').date()
                now_time = datetime.now(local_tz).time()
                current_datetime_local = local_tz.localize(datetime.combine(sim_date, now_time))
            except ValueError:
                current_datetime_local = datetime.now(local_tz)
        else:
            current_datetime_local = datetime.now(local_tz)

        # Determine "Observing Night" Date
        # If it's before noon, we associate this time with the previous night's session
        # to ensure the time array (which starts at noon) covers the current moment.
        if current_datetime_local.hour < 12:
            local_date = (current_datetime_local.date() - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            local_date = current_datetime_local.strftime('%Y-%m-%d')

        sampling_interval = 15 if SINGLE_USER_MODE else int(os.environ.get('CALCULATION_PRECISION', 15))
        fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)

        # Moon / Ephem Prep
        time_obj_now = Time(current_datetime_local.astimezone(pytz.utc))
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

                    # --- GEOMETRIC PRE-FILTER (Live Request) ---
                calc_invisible = g.user_config.get("calc_invisible", False)

                if not calc_invisible:
                    max_culm_geo = 90.0 - abs(lat - dec)
                    if max_culm_geo < altitude_threshold:
                        # Skip heavy math, return "greyed out" state immediately
                        item.update({
                            'Altitude Current': 'N/A', 'Azimuth Current': 'N/A', 'Trend': '–',
                            'Altitude 11PM': 'N/A', 'Azimuth 11PM': 'N/A', 'Transit Time': 'N/A',
                            'Observable Duration (min)': 0,
                            'Max Altitude (°)': round(max_culm_geo, 1),
                            'Angular Separation (°)': 'N/A',
                            'is_obstructed_now': False,
                            'is_geometrically_impossible': True,
                            'best_month_ra':
                                ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"][
                                    int(ra / 2) % 12],
                            'max_culmination_alt': round(max_culm_geo, 1),
                            'error': False
                        })
                        results.append(item)
                        continue

                # Calculate / Cache
                cache_key = f"{user.username}_{obj.object_name.lower().replace(' ', '_')}_{local_date}_{lat:.4f}_{lon:.4f}_{altitude_threshold}_{sampling_interval}"

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
                # Use the effective simulation time converted to UTC
                now_utc = current_datetime_local.astimezone(pytz.utc)
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
            "has_more": has_more,
            "offset": offset,
            "limit": limit
        }

        return jsonify(response_data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500




# =============================================================================
# MOBILE COMPANION ROUTES
# =============================================================================
@api_bp.route('/api/mobile_data_chunk')
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

    return jsonify({
        "data": results,
        "total": total_count,
        "offset": offset,
        "limit": limit
    })


@api_bp.route('/api/mobile_status')
@login_required
def api_mobile_status():
    """Returns moon phase and sun events for the mobile status strip."""
    load_full_astro_context()

    user = g.db_user
    location_name = g.selected_location

    if not user or not location_name:
        return jsonify({"error": "No location selected"}), 400

    db = get_db()
    location_db_obj = db.query(Location).filter_by(user_id=user.id, name=location_name).one_or_none()

    if not location_db_obj:
        return jsonify({"error": "Location not found"}), 404

    try:
        lat = location_db_obj.lat
        lon = location_db_obj.lon
        tz_name = location_db_obj.timezone
        local_tz = pytz.timezone(tz_name)
    except Exception as e:
        return jsonify({"error": f"Invalid location data: {e}"}), 400

    current_datetime_local = datetime.now(local_tz)

    # Determine "Observing Night" Date (Noon-to-Noon Logic)
    if current_datetime_local.hour < 12:
        local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        local_date = current_datetime_local.strftime('%Y-%m-%d')

    # Calculate moon phase
    try:
        time_for_phase_local = local_tz.localize(
            datetime.combine(datetime.now().date(), datetime.min.time().replace(hour=12)))
        moon_phase = round(ephem.Moon(time_for_phase_local.astimezone(pytz.utc)).phase, 0)
    except Exception:
        moon_phase = None

    # Calculate sun events
    try:
        sun_events = calculate_sun_events_cached(local_date, tz_name, lat, lon)
        dusk = sun_events.get("astronomical_dusk", "—")
        dawn = sun_events.get("astronomical_dawn", "—")
        dusk_dawn = f"{dusk}–{dawn}" if dusk != "N/A" and dawn != "N/A" else "—"
    except Exception:
        dusk_dawn = "—"

    return jsonify({
        "moon_phase": moon_phase,
        "dusk_dawn": dusk_dawn
    })

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




@api_bp.route("/telemetry/ping", methods=["POST"])
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



def get_object_list_from_config():
    """Helper function to get the list of objects from the current user's config."""
    if hasattr(g, 'user_config') and g.user_config and "objects" in g.user_config:
        return g.user_config.get("objects", [])
    return []

@api_bp.route('/api/get_moon_data')
def get_moon_data_for_session():
    # --- Manual Auth Check for Guest Support ---
    if not (current_user.is_authenticated or SINGLE_USER_MODE or getattr(g, 'is_guest', False)):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
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

            # --- Calculate Observable Duration (for Max Subs estimate) ---
        obs_duration_min = 0
        if ra is not None and dec is not None:
            try:
                # Attempt to resolve horizon mask and threshold from global context if location matches
                alt_thresh = g.user_config.get("altitude_threshold", 20)
                mask = None

                # Heuristic lookup for location-specific settings
                if hasattr(g, 'locations') and isinstance(g.locations, dict):
                    for loc_details in g.locations.values():
                        # Fuzzy match coordinates to find correct location settings
                        if (abs(loc_details.get('lat', 999) - lat) < 0.001 and
                                abs(loc_details.get('lon', 999) - lon) < 0.001):
                            mask = loc_details.get('horizon_mask')
                            if loc_details.get('altitude_threshold') is not None:
                                alt_thresh = loc_details.get('altitude_threshold')
                            break

                # Use a standard sampling interval for this quick check
                sampling = 15

                obs_dur_td, _, _, _ = calculate_observable_duration_vectorized(
                    ra, dec, lat, lon, date_str, tz_name, alt_thresh, sampling, horizon_mask=mask
                )
                if obs_dur_td:
                    obs_duration_min = int(obs_dur_td.total_seconds() / 60)
            except Exception as e:
                print(f"[API Moon] Duration calc error: {e}")

        return jsonify({
            "status": "success",
            "moon_illumination": moon_phase,
            "angular_separation": angular_sep_value,
            "observable_duration_min": obs_duration_min
        })

    except Exception as e:
        print(f"ERROR in /api/get_moon_data: {e}")
        traceback.print_exc()  # Add traceback for better debugging
        return jsonify({"status": "error", "message": str(e)}), 500




@tools_bp.route('/download_rig_config')
@login_required
def download_rig_config():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash(_("User not found."), "error")
            return redirect(url_for('core.config_form'))

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
            guide_tel_obj = next((c for c in comps if c.id == r.guide_telescope_id), None)
            guide_cam_obj = next((c for c in comps if c.id == r.guide_camera_id), None)

            efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)

            final_rigs_list.append({
                "rig_id": r.id,  # Legacy: kept for backward compatibility
                "rig_name": r.rig_name,
                "telescope_name": tel_obj.name if tel_obj else None,  # Natural key
                "camera_name": cam_obj.name if cam_obj else None,  # Natural key
                "reducer_extender_name": red_obj.name if red_obj else None,  # Natural key
                "telescope_id": r.telescope_id,  # Legacy: kept for backward compatibility
                "camera_id": r.camera_id,  # Legacy: kept for backward compatibility
                "reducer_extender_id": r.reducer_extender_id,  # Legacy: kept for backward compatibility
                "effective_focal_length": efl,
                "f_ratio": f_ratio,
                "image_scale": scale,
                "fov_w_arcmin": fov_w,
                # Guiding equipment
                "guide_telescope_name": guide_tel_obj.name if guide_tel_obj else None,
                "guide_camera_name": guide_cam_obj.name if guide_cam_obj else None,
                "guide_telescope_id": r.guide_telescope_id,
                "guide_camera_id": r.guide_camera_id,
                "guide_is_oag": r.guide_is_oag
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
        flash(_("Error generating rig config: %(error)s", error=e), "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('core.config_form'))

@tools_bp.route('/download_journal_photos')
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
        flash(_("No journal photos found to download."), "info")
        return redirect(url_for('core.config_form'))

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


@tools_bp.route('/import_journal_photos', methods=['POST'])
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
        flash(_("No file selected for photo import."), "error")
        return redirect(url_for('core.config_form'))

    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith('.zip'):
        flash(_("Please select a valid .zip file to import."), "error")
        return redirect(url_for('core.config_form'))

    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
    os.makedirs(user_upload_dir, exist_ok=True)  # Ensure the destination exists

    try:
        if not zipfile.is_zipfile(file):
            flash(_("Import failed: The uploaded file is not a valid ZIP archive."), "error")
            return redirect(url_for('core.config_form'))

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

        flash(_("Journal photos imported successfully! Extracted %(count)d files.", count=extracted_count), "success")

    except zipfile.BadZipFile:
        flash(_("Import failed: The ZIP file appears to be corrupted."), "error")
    except Exception as e:
        flash(_("An unexpected error occurred during import: %(error)s", error=e), "error")

    return redirect(url_for('core.config_form'))


@api_bp.route('/api/get_saved_views')
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


@api_bp.route('/api/save_saved_view', methods=['POST'])
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


@api_bp.route('/api/delete_saved_view', methods=['POST'])
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


@tools_bp.route('/import_rig_config', methods=['POST'])
@login_required
def import_rig_config():
    if 'file' not in request.files:
        flash(_("No file selected for rigs import."), "error")
        return redirect(url_for('core.config_form'))

    file = request.files['file']
    if not file or file.filename == '':
        flash(_("No file selected for rigs import."), "error")
        return redirect(url_for('core.config_form'))

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
                flash(_("Rigs configuration imported and synced to database successfully!"), "success")
            except Exception as e:
                db.rollback()
                print(f"[IMPORT_RIGS] DB Error: {e}")
                raise e  # Re-throw to be caught by the outer block
            # === END REFACTOR ===

        except (yaml.YAMLError, Exception) as e:
            flash(_("Error importing rigs file: %(error)s", error=e), "error")

    else:
        flash(_("Invalid file type. Please upload a .yaml or .yml file."), "error")

    return redirect(url_for('core.config_form'))

@api_bp.route('/api/get_monthly_plot_data/<path:object_name>')
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

@api_bp.route('/api/get_yearly_heatmap_chunk')
def get_yearly_heatmap_chunk():
    # --- Manual Auth Check for Guest Support ---
    if not (current_user.is_authenticated or SINGLE_USER_MODE or getattr(g, 'is_guest', False)):
        return jsonify({"error": "Unauthorized"}), 401
    load_full_astro_context()

    # NOTE: heatmap_viewed analytics removed - this is a chunk API called multiple times per page
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
        obj_count = db.query(AstroObject).filter_by(user_id=user_id, enabled=True).count()
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

        all_objects = db.query(AstroObject).filter_by(user_id=user_id, enabled=True).all()

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

    @app.cli.command("add-user")
    def add_user_command():
        """Creates a new user account."""
        print("--- Create New User ---")
        username = input("Enter username: ")

        # Check if username already exists
        existing = db.session.scalar(db.select(User).where(User.username == username))
        if existing:
            print(f"❌ User '{username}' already exists.")
            return

        password = getpass.getpass("Enter password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("❌ Passwords do not match.")
            return

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"✅ User '{username}' created successfully!")

    @app.cli.command("rename-user")
    def rename_user_command():
        """Renames an existing user account."""
        old_name = input("Current username: ")
        user = db.session.scalar(db.select(User).where(User.username == old_name))
        if not user:
            print(f"❌ User '{old_name}' not found.")
            return

        new_name = input("New username: ").strip()
        if not new_name:
            print("❌ Username cannot be empty.")
            return

        if db.session.scalar(db.select(User).where(User.username == new_name)):
            print(f"❌ Username '{new_name}' is already taken.")
            return

        user.username = new_name
        db.session.commit()
        print(f"✅ User renamed from '{old_name}' to '{new_name}'.")

    @app.cli.command("change-password")
    def change_password_command():
        """Changes the password for an existing user."""
        username = input("Username: ")
        user = db.session.scalar(db.select(User).where(User.username == username))
        if not user:
            print(f"❌ User '{username}' not found.")
            return

        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm new password: ")
        if password != confirm:
            print("❌ Passwords do not match.")
            return

        user.set_password(password)
        db.session.commit()
        print(f"✅ Password changed for '{username}'.")

    @app.cli.command("delete-user")
    def delete_user_command():
        """Deletes a user account from the credentials database."""
        username = input("Username to delete: ")
        if username == "admin":
            print("❌ Cannot delete the admin account.")
            return

        user = db.session.scalar(db.select(User).where(User.username == username))
        if not user:
            print(f"❌ User '{username}' not found.")
            return

        confirm = input(f"Are you sure you want to delete '{username}'? (yes/no): ")
        if confirm.lower() != "yes":
            print("Cancelled.")
            return

        db.session.delete(user)
        db.session.commit()
        print(f"✅ User '{username}' deleted from credentials database.")

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

@api_bp.route('/api/internal/provision_user', methods=['POST'])
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

@api_bp.route('/api/internal/deprovision_user', methods=['POST'])
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


@api_bp.route('/api/get_shared_items')
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
                "shared_notes": obj.shared_notes or "",
                # --- Inspiration Metadata ---
                "image_url": obj.image_url,
                "image_credit": obj.image_credit,
                "image_source_link": obj.image_source_link,
                "description_text": obj.description_text,
                "description_credit": obj.description_credit,
                "description_source_link": obj.description_source_link
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


@api_bp.route('/api/import_item', methods=['POST'])
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
                project_name="",
                # --- Inspiration Fields Transfer ---
                image_url=original_obj.image_url,
                image_credit=original_obj.image_credit,
                image_source_link=original_obj.image_source_link,
                description_text=original_obj.description_text,
                description_credit=original_obj.description_credit,
                description_source_link=original_obj.description_source_link
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

@tools_bp.route('/upload_editor_image', methods=['POST'])
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
            public_url = url_for('core.get_uploaded_image', username=username, filename=new_filename)

            # Trix expects a JSON response with a 'url' key
            return jsonify({"url": public_url})

        except Exception as e:
            print(f"Error uploading editor image: {e}")
            return jsonify({"error": f"Server error during upload: {e}"}), 500

    return jsonify({"error": "File type not allowed."}), 400


# --- YAML Portability Routes -----------------------------------------------
@tools_bp.route("/tools/export/<username>", methods=["GET"])
@login_required
def export_yaml_for_user(username):
    # Only allow exporting self in multi-user; admin can export anyone (basic guard, adjust as needed)
    if not SINGLE_USER_MODE and current_user.username != username and current_user.username != "admin":
        flash(_("Not authorized to export another user's data."), "error")
        return redirect(url_for("core.index"))
    ok = export_user_to_yaml(username, out_dir=CONFIG_DIR)
    if not ok:
        flash(_("Export failed (no such user or empty data)."), "error")
        return redirect(url_for("core.index"))
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

@tools_bp.route("/tools/import", methods=["POST"])
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
        flash(_("Username is required in multi-user mode."), "error")
        return redirect(url_for("core.index"))

    # Basic guard: only allow importing for self unless admin
    if not SINGLE_USER_MODE and current_user.username != username and current_user.username != "admin":
        flash(_("Not authorized to import for another user."), "error")
        return redirect(url_for("core.index"))

    try:
        cfg = request.files.get("config_file")
        rigs = request.files.get("rigs_file")
        jrn = request.files.get("journal_file")
        if not (cfg and rigs and jrn):
            flash(_("Please provide config, rigs, and journal YAML files."), "error")
            return redirect(url_for("core.index"))

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
            flash(_("Import completed successfully!"), "success")
        else:
            flash(_("Import failed. See server logs for details."), "error")
    except Exception as e:
        print(f"[IMPORT] ERROR: {e}")
        flash(_("Import crashed. Check logs."), "error")
    return redirect(url_for("core.index"))
# Admin-only repair route for deduplication and backfill
@tools_bp.route("/tools/repair_db", methods=["POST"])
@login_required
def repair_db_now():
    if not SINGLE_USER_MODE and current_user.username != "admin":
        flash(_("Not authorized."), "error")
        return redirect(url_for("core.index"))
    try:
        repair_journals(dry_run=False)
        flash(_("Database repair completed."), "success")
    except Exception as e:
        flash(_("Repair failed: %(error)s", error=e), "error")
    return redirect(url_for("core.index"))

# =============================================================================
# Admin User Management Panel
# =============================================================================
if not SINGLE_USER_MODE:

    @tools_bp.before_request
    def csrf_protect_admin():
        """Enforce CSRF on admin POST routes."""
        if request.method == "POST" and request.path.startswith("/admin/"):
            csrf.protect()

    @tools_bp.route("/admin/users")
    @login_required
    def admin_users():
        if current_user.username != "admin":
            flash(_("Not authorized."), "error")
            return redirect(url_for("core.index"))
        users = db.session.scalars(db.select(User).order_by(User.id)).all()
        return render_template("admin_users.html", users=users)

    @tools_bp.route("/admin/users/create", methods=["POST"])
    @login_required
    def admin_create_user():
        if current_user.username != "admin":
            flash(_("Not authorized."), "error")
            return redirect(url_for("core.index"))
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash(_("Username and password are required."), "error")
            return redirect(url_for("tools.admin_users"))
        if db.session.scalar(db.select(User).where(User.username == username)):
            flash(_("User '%(username)s' already exists.", username=username), "error")
            return redirect(url_for("tools.admin_users"))
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(_("User '%(username)s' created successfully.", username=username), "success")
        return redirect(url_for("tools.admin_users"))

    @tools_bp.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
    @login_required
    def admin_toggle_user(user_id):
        if current_user.username != "admin":
            flash(_("Not authorized."), "error")
            return redirect(url_for("core.index"))
        user = db.session.get(User, user_id)
        if not user:
            flash(_("User not found."), "error")
            return redirect(url_for("tools.admin_users"))
        if user.username == "admin":
            flash(_("Cannot deactivate the admin account."), "error")
            return redirect(url_for("tools.admin_users"))
        user.active = not user.active
        db.session.commit()
        status = "activated" if user.active else "deactivated"
        flash(_("User '%(username)s' %(status)s.", username=user.username, status=status), "success")
        return redirect(url_for("tools.admin_users"))

    @tools_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
    @login_required
    def admin_reset_password(user_id):
        if current_user.username != "admin":
            flash(_("Not authorized."), "error")
            return redirect(url_for("core.index"))
        user = db.session.get(User, user_id)
        if not user:
            flash(_("User not found."), "error")
            return redirect(url_for("tools.admin_users"))
        new_password = request.form.get("new_password", "")
        if not new_password:
            flash(_("Password cannot be empty."), "error")
            return redirect(url_for("tools.admin_users"))
        user.set_password(new_password)
        db.session.commit()
        flash(_("Password reset for '%(username)s'.", username=user.username), "success")
        return redirect(url_for("tools.admin_users"))

    @tools_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    @login_required
    def admin_delete_user(user_id):
        if current_user.username != "admin":
            flash(_("Not authorized."), "error")
            return redirect(url_for("core.index"))
        user = db.session.get(User, user_id)
        if not user:
            flash(_("User not found."), "error")
            return redirect(url_for("tools.admin_users"))
        if user.username == "admin":
            flash(_("Cannot delete the admin account."), "error")
            return redirect(url_for("tools.admin_users"))
        uname = user.username
        db.session.delete(user)
        db.session.commit()
        flash(_("User '%(username)s' deleted.", username=uname), "success")
        return redirect(url_for("tools.admin_users"))


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

        # IC 1805 -> IC1805
        (re.compile(r'^(IC)(\d+)$'), r'IC \2'),

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


@api_bp.route('/api/save_framing', methods=['POST'])
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

        framing.ra = float(data['ra']) if data.get('ra') is not None else None
        framing.dec = float(data['dec']) if data.get('dec') is not None else None
        framing.rotation = float(data['rotation']) if data.get('rotation') is not None else 0.0
        framing.survey = data.get('survey')
        framing.blend_survey = data.get('blend')
        framing.blend_opacity = float(data['blend_op']) if data.get('blend_op') is not None else 0.0

        # Mosaic fields
        framing.mosaic_cols = int(data['mosaic_cols']) if data.get('mosaic_cols') is not None else 1
        framing.mosaic_rows = int(data['mosaic_rows']) if data.get('mosaic_rows') is not None else 1
        framing.mosaic_overlap = float(data['mosaic_overlap']) if data.get('mosaic_overlap') is not None else 10.0

        # Image Adjustment fields
        framing.img_brightness = float(data['img_brightness']) if data.get('img_brightness') is not None else 0.0
        framing.img_contrast = float(data['img_contrast']) if data.get('img_contrast') is not None else 0.0
        framing.img_gamma = float(data['img_gamma']) if data.get('img_gamma') is not None else 1.0
        framing.img_saturation = float(data['img_saturation']) if data.get('img_saturation') is not None else 0.0

        # Overlay Preference fields
        framing.geo_belt_enabled = bool(data.get('geo_belt_enabled', True))

        framing.updated_at = datetime.now(UTC)

        db.commit()
        return jsonify({"status": "success", "message": "Framing saved."})
    except Exception as e:
        db.rollback()
        app.logger.error(f"[FRAMING API] Failed to save framing for '{object_name}': {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@api_bp.route('/api/get_framing/<path:object_name>')
@login_required
def get_framing(object_name):
    db = get_db()
    try:
        framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id,
            object_name=object_name
        ).one_or_none()

        record_event('framing_opened')
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
                "mosaic_overlap": framing.mosaic_overlap if framing.mosaic_overlap is not None else 10,
                "img_brightness": framing.img_brightness if framing.img_brightness is not None else 0.0,
                "img_contrast": framing.img_contrast if framing.img_contrast is not None else 0.0,
                "img_gamma": framing.img_gamma if framing.img_gamma is not None else 1.0,
                "img_saturation": framing.img_saturation if framing.img_saturation is not None else 0.0,
                "geo_belt_enabled": framing.geo_belt_enabled if framing.geo_belt_enabled is not None else True
            })
        else:
            return jsonify({"status": "empty"})
    except Exception as e:
        app.logger.error(f"[FRAMING API] Failed to get framing for '{object_name}': {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/delete_framing', methods=['POST'])
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
        app.logger.error(f"[FRAMING API] Failed to delete framing for '{object_name}': {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


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

    # Skip background workers during testing
    if should_start_threads and not app.config.get('TESTING'):
        print("[STARTUP] Starting background update check thread...")
        update_thread = threading.Thread(target=check_for_updates, args=(app,))
        update_thread.daemon = True
        update_thread.start()

        print("[STARTUP] Starting background weather worker thread...")
        weather_thread = threading.Thread(target=weather_cache_worker, args=(app,))
        weather_thread.daemon = True
        weather_thread.start()

        print("[STARTUP] Starting background heatmap maintenance thread...")
        heatmap_thread = threading.Thread(target=heatmap_background_worker, args=(app,))
        heatmap_thread.daemon = True
        heatmap_thread.start()

        print("[STARTUP] Starting background IERS data refresh thread...")
        iers_thread = threading.Thread(target=iers_refresh_worker, args=(app,))
        iers_thread.daemon = True
        iers_thread.start()


@app.cli.command("reset-guest-from-template")
def reset_guest_from_template_command():
    """
    COMPLETELY WIPES the 'guest_user' and re-seeds it strictly from the
    source code's 'config_templates' directory.
    Use this to force the guest view to match the shipped defaults.
    """
    print("--- [RESETTING GUEST FROM TEMPLATES] ---")
    db = get_db()
    try:
        # 1. Ensure guest_user exists
        guest_user = db.query(DbUser).filter_by(username="guest_user").one_or_none()
        if not guest_user:
            guest_user = DbUser(username="guest_user", active=True)
            db.add(guest_user)
            db.flush()
            print(f"Created 'guest_user' (ID: {guest_user.id}).")
        else:
            print(f"Found 'guest_user' (ID: {guest_user.id}).")

        # 2. WIPE ALL DATA
        print("Wiping all existing data for guest_user...")
        # Order matters for foreign keys
        db.query(JournalSession).filter_by(user_id=guest_user.id).delete()
        db.query(Project).filter_by(user_id=guest_user.id).delete()
        db.query(SavedFraming).filter_by(user_id=guest_user.id).delete()
        db.query(SavedView).filter_by(user_id=guest_user.id).delete()
        db.query(Rig).filter_by(user_id=guest_user.id).delete()
        db.query(Component).filter_by(user_id=guest_user.id).delete()
        db.query(AstroObject).filter_by(user_id=guest_user.id).delete()
        db.query(Location).filter_by(user_id=guest_user.id).delete()
        db.query(UiPref).filter_by(user_id=guest_user.id).delete()
        db.flush()
        print("...Wipe complete.")

        # 3. LOAD TEMPLATES
        # We look for 'config_guest_user.yaml' first, fall back to 'config_default.yaml'
        cfg_path = os.path.join(TEMPLATE_DIR, "config_guest_user.yaml")
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(TEMPLATE_DIR, "config_default.yaml")

        rigs_path = os.path.join(TEMPLATE_DIR, "rigs_default.yaml")
        jrn_path = os.path.join(TEMPLATE_DIR, "journal_default.yaml")

        print(f"Loading Config from: {os.path.basename(cfg_path)}")
        cfg_data, _ = _read_yaml(cfg_path)

        print(f"Loading Rigs from: {os.path.basename(rigs_path)}")
        rigs_data, _ = _read_yaml(rigs_path)

        print(f"Loading Journal from: {os.path.basename(jrn_path)}")
        jrn_data, _ = _read_yaml(jrn_path)

        # 4. RE-SEED
        if cfg_data:
            print("Seeding Locations, Objects, Prefs...")
            _migrate_locations(db, guest_user, cfg_data)
            _migrate_objects(db, guest_user, cfg_data)
            _migrate_ui_prefs(db, guest_user, cfg_data)
            _migrate_saved_framings(db, guest_user, cfg_data)
            _migrate_saved_views(db, guest_user, cfg_data)

        if rigs_data:
            print("Seeding Rigs...")
            _migrate_components_and_rigs(db, guest_user, rigs_data, "guest_user")

        if jrn_data:
            print("Seeding Journal...")
            _migrate_journal(db, guest_user, jrn_data)

        db.commit()
        print("✅ Guest user fully reset to template defaults.")

    except Exception as e:
        db.rollback()
        print(f"❌ FATAL ERROR: {e}")
        traceback.print_exc()


# =============================================================================
# Register Blueprints (must be after all route definitions)
# =============================================================================
app.register_blueprint(core_bp)
app.register_blueprint(api_bp)
app.register_blueprint(journal_bp)
app.register_blueprint(mobile_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(tools_bp)

# Register AI blueprint (conditional on AI_API_KEY being set)
register_ai_blueprint(app)


