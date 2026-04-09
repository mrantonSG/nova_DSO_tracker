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
    static_cache, moon_separation_cache, nightly_curves_cache, observable_objects_cache,
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


from nova.helpers import _read_yaml, discover_catalog_packs  # noqa: F401 — moved to helpers


from nova.migration import (
    _try_float, _as_int, _norm_name,
    _heal_saved_framings, _upsert_user,
    _migrate_locations, _migrate_objects,
    _migrate_saved_framings, _migrate_ui_prefs,
    _migrate_journal, _migrate_components_and_rigs,
    _migrate_saved_views,
    validate_journal_data, repair_journals,
    load_catalog_pack, import_catalog_pack_for_user,
    export_user_to_yaml, import_user_from_yaml,
)
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

from nova.helpers import get_locale

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



from nova.helpers import get_all_mobile_up_now_data
from nova.blueprints.api import get_hybrid_weather_forecast




def get_object_list_from_config():
    """Helper function to get the list of objects from the current user's config."""
    if hasattr(g, 'user_config') and g.user_config and "objects" in g.user_config:
        return g.user_config.get("objects", [])
    return []





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

from nova.helpers import enable_user, disable_user, delete_user





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
from nova.blueprints.admin import admin_bp
app.register_blueprint(admin_bp)

# Register AI blueprint (conditional on AI_API_KEY being set)
register_ai_blueprint(app)


