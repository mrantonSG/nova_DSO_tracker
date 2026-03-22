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
    INSTANCE_PATH,
    DB_PATH,
    DB_URI,
    engine,
    SessionLocal,
    Base,
    DbUser,
    Project,
    SavedView,
    Location,
    SavedFraming,
    HorizonPoint,
    AstroObject,
    Component,
    Rig,
    JournalSession,
    UiPref,
    UserCustomFilter,
    ApiKey,
    Role,
    Permission,
    roles_users,
    roles_permissions,
    BlogPost,
    BlogImage,
    BlogComment,
)
from nova.config import (
    APP_VERSION,
    TEMPLATE_DIR,
    CACHE_DIR,
    CONFIG_DIR,
    BACKUP_DIR,
    UPLOAD_FOLDER,
    BLOG_UPLOAD_FOLDER,
    ENV_FILE,
    FIRST_RUN_ENV_CREATED,
    SINGLE_USER_MODE,
    SECRET_KEY,
    STELLARIUM_ERROR_MESSAGE,
    NOVA_CATALOG_URL,
    ALLOWED_EXTENSIONS,
    MAX_ACTIVE_LOCATIONS,
    SENTRY_DSN,
    static_cache,
    moon_separation_cache,
    nightly_curves_cache,
    cache_worker_status,
    monthly_top_targets_cache,
    config_cache,
    config_mtime,
    journal_cache,
    journal_mtime,
    LATEST_VERSION_INFO,
    rig_data_cache,
    weather_cache,
    CATALOG_MANIFEST_CACHE,
    _telemetry_startup_once,
    TELEMETRY_DEBUG_STATE,
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
from nova.blueprints.rest_api import rest_api_bp
from nova.blueprints.weather import weather_bp
from nova.blueprints.blog import blog_bp
from nova.api_auth import (
    ensure_single_user_api_key,
    api_key_or_login_required,
    create_api_key,
)


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
            # --- Get table info for journal_sessions ---
            cols_journal = conn.exec_driver_sql(
                "PRAGMA table_info(journal_sessions);"
            ).fetchall()
            colnames_journal = {
                row[1] for row in cols_journal
            }  # (cid, name, type, notnull, dflt_value, pk)
            if "external_id" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN external_id TEXT;"
                )
                print("[DB PATCH] Added missing column journal_sessions.external_id")

            # --- ADD THIS BLOCK for Rig Snapshot ---
            if "rig_id_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN rig_id_snapshot INTEGER;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.rig_id_snapshot"
                )
            if "rig_name_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN rig_name_snapshot VARCHAR(256);"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.rig_name_snapshot"
                )
            if "rig_efl_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN rig_efl_snapshot FLOAT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.rig_efl_snapshot"
                )
            if "rig_fr_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN rig_fr_snapshot FLOAT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.rig_fr_snapshot"
                )
            if "rig_scale_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN rig_scale_snapshot FLOAT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.rig_scale_snapshot"
                )
            if "rig_fov_w_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN rig_fov_w_snapshot FLOAT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.rig_fov_w_snapshot"
                )
            if "rig_fov_h_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN rig_fov_h_snapshot FLOAT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.rig_fov_h_snapshot"
                )
            if "telescope_name_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN telescope_name_snapshot VARCHAR(256);"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.telescope_name_snapshot"
                )
            if "reducer_name_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN reducer_name_snapshot VARCHAR(256);"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.reducer_name_snapshot"
                )
            if "camera_name_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN camera_name_snapshot VARCHAR(256);"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.camera_name_snapshot"
                )
            if "rig_stable_uid_snapshot" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN rig_stable_uid_snapshot VARCHAR(36);"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.rig_stable_uid_snapshot"
                )

            # --- Log content columns for ASIAIR and PHD2 log analysis ---
            if "asiair_log_content" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN asiair_log_content TEXT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.asiair_log_content"
                )
            if "phd2_log_content" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN phd2_log_content TEXT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.phd2_log_content"
                )
            if "log_analysis_cache" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN log_analysis_cache TEXT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.log_analysis_cache"
                )

            # --- Drop old global unique index on external_id ---
            # This index made external_id globally unique, but it should be unique per user
            try:
                conn.exec_driver_sql("DROP INDEX IF EXISTS uq_journal_external_id;")
                print(
                    "[DB PATCH] Dropped old global unique index uq_journal_external_id"
                )
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
                print(
                    f"[DB PATCH] Found {len(dup_check)} duplicate external_id value(s) per user. Resolving..."
                )
                for user_id, ext_id, count in dup_check:
                    # Get all rows with this duplicate external_id for this user (keep the one with lowest id)
                    rows = conn.exec_driver_sql(
                        "SELECT id FROM journal_sessions WHERE user_id = ? AND external_id = ? ORDER BY id",
                        (user_id, ext_id),
                    ).fetchall()
                    # Regenerate external_id for all but the first (oldest) row
                    for row in rows[1:]:
                        new_id = uuid.uuid4().hex
                        conn.exec_driver_sql(
                            "UPDATE journal_sessions SET external_id = ? WHERE id = ?",
                            (new_id, row[0]),
                        )
                        print(
                            f"[DB PATCH] Regenerated external_id for journal_sessions.id={row[0]} (was '{ext_id}' -> '{new_id}')"
                        )

            # --- Create composite unique index on (user_id, external_id) ---
            # This ensures external_id is unique per user, allowing different users to have the same external_id
            try:
                conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_journal_user_external_id ON journal_sessions(user_id, external_id) WHERE external_id IS NOT NULL;"
                )
                print(
                    "[DB PATCH] Created composite unique index uq_journal_user_external_id on journal_sessions(user_id, external_id)"
                )
            except Exception as idx_err:
                print(
                    f"[DB PATCH] Could not create journal user_external_id index: {idx_err}"
                )

            # --- PERFORMANCE: Add Composite Index for Journal Sessions ---
            try:
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_journal_user_date ON journal_sessions(user_id, date_utc DESC);"
                )
                print(
                    "[DB PATCH] Created performance index idx_journal_user_date on journal_sessions"
                )
            except Exception as idx_err:
                print(f"[DB PATCH] Could not create journal user_date index: {idx_err}")

            # --- PERFORMANCE: Add Index for Object Name Lookups ---
            try:
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_journal_object_name ON journal_sessions(object_name);"
                )
                print(
                    "[DB PATCH] Created performance index idx_journal_object_name on journal_sessions"
                )
            except Exception as idx_err:
                print(
                    f"[DB PATCH] Could not create journal object_name index: {idx_err}"
                )

            # --- SavedView Patches ---
            cols_views = conn.exec_driver_sql(
                "PRAGMA table_info(saved_views);"
            ).fetchall()
            colnames_views = {row[1] for row in cols_views}
            if "description" not in colnames_views:
                conn.exec_driver_sql(
                    "ALTER TABLE saved_views ADD COLUMN description VARCHAR(500);"
                )
            if "is_shared" not in colnames_views:
                conn.exec_driver_sql(
                    "ALTER TABLE saved_views ADD COLUMN is_shared BOOLEAN DEFAULT 0 NOT NULL;"
                )
            if "original_user_id" not in colnames_views:
                conn.exec_driver_sql(
                    "ALTER TABLE saved_views ADD COLUMN original_user_id INTEGER;"
                )
            if "original_item_id" not in colnames_views:
                conn.exec_driver_sql(
                    "ALTER TABLE saved_views ADD COLUMN original_item_id INTEGER;"
                )

            # --- PERFORMANCE: Add Index for Saved Views Name Lookups ---
            try:
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS idx_saved_views_name ON saved_views(name);"
                )
                print(
                    "[DB PATCH] Created performance index idx_saved_views_name on saved_views"
                )
            except Exception as idx_err:
                print(f"[DB PATCH] Could not create saved_views name index: {idx_err}")

            try:
                cols_framing = conn.exec_driver_sql(
                    "PRAGMA table_info(saved_framings);"
                ).fetchall()
                colnames_framing = {row[1] for row in cols_framing}
                if "rig_name" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN rig_name VARCHAR(256);"
                    )
                    print("[DB PATCH] Added missing column saved_framings.rig_name")
                if "mosaic_cols" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN mosaic_cols INTEGER DEFAULT 1;"
                    )
                if "mosaic_rows" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN mosaic_rows INTEGER DEFAULT 1;"
                    )
                if "mosaic_overlap" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN mosaic_overlap FLOAT DEFAULT 10.0;"
                    )
                # Image Adjustment columns
                if "img_brightness" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN img_brightness FLOAT DEFAULT 0.0;"
                    )
                    print(
                        "[DB PATCH] Added missing column saved_framings.img_brightness"
                    )
                if "img_contrast" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN img_contrast FLOAT DEFAULT 0.0;"
                    )
                    print("[DB PATCH] Added missing column saved_framings.img_contrast")
                if "img_gamma" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN img_gamma FLOAT DEFAULT 1.0;"
                    )
                    print("[DB PATCH] Added missing column saved_framings.img_gamma")
                if "img_saturation" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN img_saturation FLOAT DEFAULT 0.0;"
                    )
                    print(
                        "[DB PATCH] Added missing column saved_framings.img_saturation"
                    )
                # Overlay Preference columns
                if "geo_belt_enabled" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN geo_belt_enabled BOOLEAN DEFAULT 1;"
                    )
                    print(
                        "[DB PATCH] Added missing column saved_framings.geo_belt_enabled"
                    )
                # Stable UID for cross-boundary rig resolution
                if "rig_stable_uid" not in colnames_framing:
                    conn.exec_driver_sql(
                        "ALTER TABLE saved_framings ADD COLUMN rig_stable_uid VARCHAR(36);"
                    )
                    print(
                        "[DB PATCH] Added missing column saved_framings.rig_stable_uid"
                    )
            except Exception as e:
                # Table might not exist yet if it's a fresh install, which is fine
                print(
                    f"[DB PATCH] SavedFraming table patch skipped (may not exist yet): {e}"
                )

            # --- Add stable_uid column to 'locations' table ---
            cols_locations = conn.exec_driver_sql(
                "PRAGMA table_info(locations);"
            ).fetchall()
            colnames_locations = {row[1] for row in cols_locations}
            if "stable_uid" not in colnames_locations:
                conn.exec_driver_sql(
                    "ALTER TABLE locations ADD COLUMN stable_uid VARCHAR(36);"
                )
                print("[DB PATCH] Added missing column locations.stable_uid")

            # --- Add new columns to 'components' table ---
            cols_components = conn.exec_driver_sql(
                "PRAGMA table_info(components);"
            ).fetchall()
            colnames_components = {row[1] for row in cols_components}

            if "stable_uid" not in colnames_components:
                conn.exec_driver_sql(
                    "ALTER TABLE components ADD COLUMN stable_uid VARCHAR(36);"
                )
                print("[DB PATCH] Added missing column components.stable_uid")

            if "is_shared" not in colnames_components:
                conn.exec_driver_sql(
                    "ALTER TABLE components ADD COLUMN is_shared BOOLEAN DEFAULT 0 NOT NULL;"
                )
                print("[DB PATCH] Added missing column components.is_shared")

            if "original_user_id" not in colnames_components:
                conn.exec_driver_sql(
                    "ALTER TABLE components ADD COLUMN original_user_id INTEGER;"
                )
                print("[DB PATCH] Added missing column components.original_user_id")

            # --- ADD THIS BLOCK ---
            if "original_item_id" not in colnames_components:
                conn.exec_driver_sql(
                    "ALTER TABLE components ADD COLUMN original_item_id INTEGER;"
                )
                print("[DB PATCH] Added missing column components.original_item_id")
            # --- END OF BLOCK ---

            # --- Add guide optics columns to 'rigs' table for dither recommendations ---
            cols_rigs = conn.exec_driver_sql("PRAGMA table_info(rigs);").fetchall()
            colnames_rigs = {row[1] for row in cols_rigs}

            if "stable_uid" not in colnames_rigs:
                conn.exec_driver_sql(
                    "ALTER TABLE rigs ADD COLUMN stable_uid VARCHAR(36);"
                )
                print("[DB PATCH] Added missing column rigs.stable_uid")

            # Legacy guide optics columns (kept for backwards compatibility, but no longer used)
            if "guide_scope_name" not in colnames_rigs:
                conn.exec_driver_sql(
                    "ALTER TABLE rigs ADD COLUMN guide_scope_name VARCHAR(256);"
                )
                print("[DB PATCH] Added missing column rigs.guide_scope_name")
            if "guide_focal_length_mm" not in colnames_rigs:
                conn.exec_driver_sql(
                    "ALTER TABLE rigs ADD COLUMN guide_focal_length_mm FLOAT;"
                )
                print("[DB PATCH] Added missing column rigs.guide_focal_length_mm")
            if "guide_camera_name" not in colnames_rigs:
                conn.exec_driver_sql(
                    "ALTER TABLE rigs ADD COLUMN guide_camera_name VARCHAR(256);"
                )
                print("[DB PATCH] Added missing column rigs.guide_camera_name")
            if "guide_pixel_size_um" not in colnames_rigs:
                conn.exec_driver_sql(
                    "ALTER TABLE rigs ADD COLUMN guide_pixel_size_um FLOAT;"
                )
                print("[DB PATCH] Added missing column rigs.guide_pixel_size_um")

            # New FK-based guide optics columns
            if "guide_telescope_id" not in colnames_rigs:
                conn.exec_driver_sql(
                    "ALTER TABLE rigs ADD COLUMN guide_telescope_id INTEGER;"
                )
                print("[DB PATCH] Added missing column rigs.guide_telescope_id")
            if "guide_camera_id" not in colnames_rigs:
                conn.exec_driver_sql(
                    "ALTER TABLE rigs ADD COLUMN guide_camera_id INTEGER;"
                )
                print("[DB PATCH] Added missing column rigs.guide_camera_id")
            if "guide_is_oag" not in colnames_rigs:
                conn.exec_driver_sql(
                    "ALTER TABLE rigs ADD COLUMN guide_is_oag BOOLEAN DEFAULT 0 NOT NULL;"
                )
                print("[DB PATCH] Added missing column rigs.guide_is_oag")

            # --- Add new columns to 'astro_objects' table ---
            cols_objects = conn.exec_driver_sql(
                "PRAGMA table_info(astro_objects);"
            ).fetchall()
            colnames_objects = {row[1] for row in cols_objects}

            project_name_col_info = next(
                (col for col in cols_objects if col[1] == "project_name"), None
            )
            if project_name_col_info and "TEXT" not in project_name_col_info[2].upper():
                print(
                    f"[DB PATCH] Note: astro_objects.project_name type is {project_name_col_info[2]}. Model updated to TEXT."
                )

            if "is_shared" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN is_shared BOOLEAN DEFAULT 0 NOT NULL;"
                )
                print("[DB PATCH] Added missing column astro_objects.is_shared")

            if "shared_notes" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN shared_notes TEXT;"
                )
                print("[DB PATCH] Added missing column astro_objects.shared_notes")

            if "original_user_id" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN original_user_id INTEGER;"
                )
                print("[DB PATCH] Added missing column astro_objects.original_user_id")

            # --- ADD THIS BLOCK ---
            if "original_item_id" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN original_item_id INTEGER;"
                )
                print("[DB PATCH] Added missing column astro_objects.original_item_id")
            # --- END OF BLOCK ---

            if "catalog_sources" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN catalog_sources TEXT;"
                )
                print("[DB PATCH] Added missing column astro_objects.catalog_sources")

            if "catalog_info" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN catalog_info TEXT;"
                )
                print("[DB PATCH] Added missing column astro_objects.catalog_info")

            if "enabled" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN enabled BOOLEAN DEFAULT 1;"
                )
                print("[DB PATCH] Added missing column astro_objects.enabled")

                # Curation Fields Patch
            if "image_url" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN image_url VARCHAR(500);"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN image_credit VARCHAR(256);"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN image_source_link VARCHAR(500);"
                )
                print("[DB PATCH] Added image curation columns")

            if "description_text" not in colnames_objects:
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN description_text TEXT;"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN description_credit VARCHAR(256);"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE astro_objects ADD COLUMN description_source_link VARCHAR(500);"
                )
                print("[DB PATCH] Added description curation columns")

            # --- Project Model Patches ---
            cols_projects = conn.exec_driver_sql(
                "PRAGMA table_info(projects);"
            ).fetchall()
            colnames_projects = {row[1] for row in cols_projects}

            if "target_object_name" not in colnames_projects:
                conn.exec_driver_sql(
                    "ALTER TABLE projects ADD COLUMN target_object_name VARCHAR(256);"
                )
                print("[DB PATCH] Added missing column projects.target_object_name")

            if "description_notes" not in colnames_projects:
                conn.exec_driver_sql(
                    "ALTER TABLE projects ADD COLUMN description_notes TEXT;"
                )
                print("[DB PATCH] Added missing column projects.description_notes")

            if "framing_notes" not in colnames_projects:
                conn.exec_driver_sql(
                    "ALTER TABLE projects ADD COLUMN framing_notes TEXT;"
                )
                print("[DB PATCH] Added missing column projects.framing_notes")

            if "processing_notes" not in colnames_projects:
                conn.exec_driver_sql(
                    "ALTER TABLE projects ADD COLUMN processing_notes TEXT;"
                )
                print("[DB PATCH] Added missing column projects.processing_notes")

            if "final_image_file" not in colnames_projects:
                conn.exec_driver_sql(
                    "ALTER TABLE projects ADD COLUMN final_image_file VARCHAR(256);"
                )
                print("[DB PATCH] Added missing column projects.final_image_file")

            if "goals" not in colnames_projects:
                conn.exec_driver_sql("ALTER TABLE projects ADD COLUMN goals TEXT;")
                print("[DB PATCH] Added missing column projects.goals")

            if "status" not in colnames_projects:
                conn.exec_driver_sql(
                    "ALTER TABLE projects ADD COLUMN status VARCHAR(32) DEFAULT 'In Progress';"
                )
                print("[DB PATCH] Added missing column projects.status")
            # --- End Project Model Patches ---

            # --- Structured Dither Fields Patches ---
            cols_journal_sessions = conn.exec_driver_sql(
                "PRAGMA table_info(journal_sessions);"
            ).fetchall()
            colnames_journal_sessions = {row[1] for row in cols_journal_sessions}

            if "dither_pixels" not in colnames_journal_sessions:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN dither_pixels INTEGER;"
                )
                print("[DB PATCH] Added missing column journal_sessions.dither_pixels")
            if "dither_every_n" not in colnames_journal_sessions:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN dither_every_n INTEGER;"
                )
                print("[DB PATCH] Added missing column journal_sessions.dither_every_n")
            if "dither_notes" not in colnames_journal_sessions:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN dither_notes VARCHAR(256);"
                )
                print("[DB PATCH] Added missing column journal_sessions.dither_notes")
            # --- End Structured Dither Fields Patches ---

            # Indexes for frequently filtered columns
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_locations_active ON locations(active);"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_locations_is_default ON locations(is_default);"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_astro_objects_object_name ON astro_objects(object_name);"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_rigs_rig_name ON rigs(rig_name);"
            )

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
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_user_custom_filters_user_id ON user_custom_filters(user_id);"
                )
                print("[DB PATCH] Created user_custom_filters table")

            # --- Custom Filter Data column on journal_sessions ---
            if "custom_filter_data" not in colnames_journal:
                conn.exec_driver_sql(
                    "ALTER TABLE journal_sessions ADD COLUMN custom_filter_data TEXT;"
                )
                print(
                    "[DB PATCH] Added missing column journal_sessions.custom_filter_data"
                )

            # --- Blog Tables (community astrophotography sharing) ---
            # Ensure blog_posts, blog_images, blog_comments tables exist
            # (normally created by Base.metadata.create_all, but check for safety)
            for blog_table in ("blog_posts", "blog_images", "blog_comments"):
                blog_table_exists = conn.exec_driver_sql(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name='{blog_table}';"
                ).fetchone()
                if not blog_table_exists:
                    # Tables should have been created by Base.metadata.create_all
                    # If not, the model wasn't imported yet - re-run create_all
                    Base.metadata.create_all(
                        bind=engine,
                        tables=[
                            t
                            for t in Base.metadata.tables.values()
                            if t.name == blog_table
                        ],
                        checkfirst=True,
                    )
                    print(f"[DB PATCH] Created {blog_table} table")

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

# --- Flask config ---
from nova.config import SECRET_KEY

app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
print(f"[STARTUP] SECRET_KEY hash: {hash(SECRET_KEY)}, length: {len(SECRET_KEY)}")
# --- Flask-Login setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "core.login"


@login_manager.user_loader
def load_user(user_id):
    print(f"[user_loader] Called with user_id={user_id}")
    db_sess = SessionLocal()
    try:
        # Eagerly load roles AND permissions to avoid DetachedInstanceError
        user = (
            db_sess.query(DbUser)
            .options(joinedload(DbUser.roles).joinedload(Role.permissions))
            .filter_by(id=int(user_id))
            .first()
        )
        if user:
            db_sess.expunge(user)
            print(f"[user_loader] Found user: {user.username}")
        else:
            print(f"[user_loader] No user found for id={user_id}")
        return user
    except Exception as e:
        print(f"[user_loader] Exception: {e}")
        return None
    finally:
        db_sess.close()


app.jinja_env.filters["toyaml"] = to_yaml_filter


# --- Blog Markdown rendering filter ---
import markdown as md_lib
from markupsafe import Markup

_BLOG_MD_EXTENSIONS = [
    "fenced_code",
    "tables",
    "nl2br",
    "attr_list",
    "footnotes",
]
_BLOG_ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "del",
    "code",
    "pre",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "blockquote",
    "hr",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "sup",
    "div",
    "span",
]
_BLOG_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title"],
    "*": ["class", "id"],
}


def render_markdown_filter(text: str) -> Markup:
    """Convert markdown text to safe HTML for blog posts and comments."""
    if not text:
        return Markup("")
    html = md_lib.markdown(text, extensions=_BLOG_MD_EXTENSIONS)
    clean = bleach.clean(
        html,
        tags=_BLOG_ALLOWED_TAGS,
        attributes=_BLOG_ALLOWED_ATTRS,
        strip=True,
    )
    return Markup(clean)


app.jinja_env.filters["render_markdown"] = render_markdown_filter

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
        return value_iso_str
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
        return value_iso_str

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


def _estimate_transparency(visibility_m, rh):
    if visibility_m is None:
        return None
    if visibility_m >= 50000:
        return 1
    elif visibility_m >= 30000:
        return 2
    elif visibility_m >= 20000:
        return 3
    elif visibility_m >= 15000:
        return 4
    elif visibility_m >= 10000:
        return 5
    elif visibility_m >= 5000:
        return 6
    elif visibility_m >= 2000:
        return 7
    else:
        return 8


def _estimate_seeing(temp, dew_point, wind_speed):
    if temp is None or dew_point is None:
        return None
    spread = abs(temp - dew_point)
    wind = wind_speed if wind_speed else 0
    if spread >= 15 and wind < 10:
        return 1
    elif spread >= 12 and wind < 15:
        return 2
    elif spread >= 10 and wind < 20:
        return 3
    elif spread >= 8 and wind < 25:
        return 4
    elif spread >= 6:
        return 5
    elif spread >= 4:
        return 6
    elif spread >= 2:
        return 7
    else:
        return 8


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
            "hourly": "temperature_2m,relative_humidity_2m,cloud_cover_low,cloud_cover_mid,cloud_cover_high,wind_speed_10m,visibility,dew_point_2m",
            "forecast_days": 7,
            "timezone": "UTC",  # Request data in UTC for easier processing
        }

        r = requests.get(
            base_url, params=params, timeout=DEFAULT_HTTP_TIMEOUT
        )  # 10-second timeout

        # --- Check for HTTP errors ---
        if r.status_code != 200:
            print(
                f"[Weather Fallback] ERROR (Open-Meteo): Received non-200 status code {r.status_code}"
            )
            print(f"[Weather Fallback] Response text (first 200 chars): {r.text[:200]}")
            return None

        # --- Try to parse the JSON ---
        data = r.json()

        # --- Basic validation ---
        if not data or "hourly" not in data or "time" not in data["hourly"]:
            print(
                f"[Weather Fallback] ERROR (Open-Meteo): Invalid data structure received."
            )
            return None

        # print(f"[Weather Fallback] Successfully fetched data from Open-Meteo.")
        return data

    except requests.exceptions.RequestException as e:
        # Handles timeouts, connection errors, etc.
        print(f"[Weather Fallback] ERROR (Open-Meteo): Request failed. Error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(
            f"[Weather Fallback] ERROR (Open-Meteo): Failed to decode JSON. Error: {e}"
        )
        print(f"[Weather Fallback] Response text (first 200 chars): {r.text[:200]}")
        return None
    except Exception as e:
        # Catch any other unexpected errors
        print(
            f"[Weather Fallback] ERROR (Open-Meteo): An unexpected error occurred. Error: {e}"
        )
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
    last_good = entry.get("data")
    last_err_ts = entry.get("last_err_ts")
    if entry and "expires" in entry:
        expires_dt = entry["expires"]
        is_expired = False
        try:
            if expires_dt.tzinfo is not None:
                if now >= expires_dt:
                    is_expired = True
            elif now.replace(tzinfo=None) >= expires_dt:
                is_expired = True
        except TypeError as te:
            print(
                f"[Weather Func] WARN: Timezone comparison error for key '{cache_key}': {te}"
            )
            is_expired = True
        if not is_expired:
            return entry["data"]

    # --- Helper Functions (No change) ---
    def _update_cache_ok(data, ttl_hours=3):
        expiry_time = datetime.now(UTC) + timedelta(hours=ttl_hours)
        weather_cache[cache_key] = {
            "data": data,
            "expires": expiry_time,
            "last_err_ts": None,
        }
        # print(f"[Weather Func] Cache UPDATED for '{cache_key}', expires {expiry_time.isoformat()}")

    def _rate_limited_error(msg):
        nonlocal last_err_ts
        now_aware = datetime.now(UTC)
        if not last_err_ts or (now_aware - last_err_ts) > timedelta(minutes=15):
            print(msg)
            weather_cache.setdefault(cache_key, {})["last_err_ts"] = now_aware

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

    if open_meteo_data and "hourly" in open_meteo_data:
        # print(f"[Weather Func] Open-Meteo succeeded. Translating data...")
        try:
            translated_dataseries = {}  # Use a dict for easier merging
            om_hourly = open_meteo_data["hourly"]
            times = om_hourly.get("time", [])

            # init_time is defined here (if successful)
            if times:
                # --- START FIX: Ensure parsed datetimes are offset-aware ---
                # 1. Parse the naive time (stripping 'Z' if it exists, fromisoformat handles T)
                first_time_naive = datetime.fromisoformat(times[0].split("Z")[0])
                # 2. Attach the UTC timezone to make it aware
                first_time_aware = first_time_naive.replace(tzinfo=UTC)
                # 3. Create the init_time (midnight), which is now guaranteed aware
                init_time = first_time_aware.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                init_str = init_time.strftime("%Y%m%d%H")
            else:
                init_time = datetime.now(UTC).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                init_str = init_time.strftime("%Y%m%d%H")
                print(
                    "[Weather Func] WARN: Open-Meteo returned no times. Using current day as init."
                )

            for i, time_str in enumerate(times):
                # --- FIX: Apply the same logic to the loop variable ---
                current_time_naive = datetime.fromisoformat(time_str.split("Z")[0])
                current_time = current_time_naive.replace(tzinfo=UTC)
                timepoint = int((current_time - init_time).total_seconds() / 3600)
                # --- END FIX ---

                cc_low = om_hourly.get("cloud_cover_low", [0] * len(times))[i]
                cc_mid = om_hourly.get("cloud_cover_mid", [0] * len(times))[i]
                cc_high = om_hourly.get("cloud_cover_high", [0] * len(times))[i]
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

                temp = om_hourly.get("temperature_2m", [None] * len(times))[i]
                rh = om_hourly.get("relative_humidity_2m", [None] * len(times))[i]
                wind = om_hourly.get("wind_speed_10m", [None] * len(times))[i]
                visibility = om_hourly.get("visibility", [None] * len(times))[i]
                dew_point = om_hourly.get("dew_point_2m", [None] * len(times))[i]

                est_trans = _estimate_transparency(visibility, rh)
                est_seeing = _estimate_seeing(temp, dew_point, wind)

                block = {
                    "timepoint": timepoint,
                    "cloudcover": cloudcover_1_9,
                    "temp2m": temp,
                    "rh2m": rh,
                    "wind_speed": wind,
                    "seeing": -est_seeing if est_seeing else -9999,
                    "transparency": -est_trans if est_trans else -9999,
                }
                translated_dataseries[timepoint] = block  # Store by timepoint

            base_dataseries = translated_dataseries
            # print(f"[Weather Func] Successfully translated {len(base_dataseries)} blocks from Open-Meteo.")

        except Exception as e:
            _rate_limited_error(
                f"[Weather Func] ERROR: Failed to translate Open-Meteo data: {e}"
            )
            # Continue with empty base_dataseries

    else:
        _rate_limited_error(
            f"[Weather Func] ERROR: Open-Meteo (base) fetch failed for key '{cache_key}'."
        )
        # base_dataseries is still {}

    # === 2. Attempt to fetch 7Timer! 'astro' for seeing/transparency ===
    # print(f"[Weather Func] Fetching enhancement data (seeing) from 7Timer! 'astro' for key '{cache_key}'")
    astro_data_7t = get_weather_data_with_retries(lat, lon, product="astro")

    if astro_data_7t and astro_data_7t.get("dataseries"):
        # print("[DEBUG] 7Timer! RAW DATA (first 5 blocks):")
        # print(astro_data_7t['dataseries'][:5])
        # print(f"[Weather Func] 7Timer! 'astro' succeeded. Merging data...")

        # --- START FIX: Calculate 7Timer! init time ---
        astro_init_str = astro_data_7t.get("init")
        try:
            # Get the 7Timer! init time as a datetime object
            astro_init_time = datetime.strptime(astro_init_str, "%Y%m%d%H").replace(
                tzinfo=UTC
            )
        except (ValueError, TypeError, AttributeError):
            _rate_limited_error(
                f"[Weather Func] ERROR: 7Timer! 'astro' gave invalid init string: '{astro_init_str}'. Cannot merge seeing data."
            )
            astro_data_7t = None  # Treat as failed
        # --- END FIX ---

        if astro_data_7t:  # Check if it's still valid after parsing init
            # --- FIX: If init_time is STILL None, Open-Meteo failed. Use 7Timer's init as the base.
            if init_time is None:
                init_time = astro_init_time
                init_str = astro_init_str
                print(
                    f"[Weather Func] WARN: Open-Meteo failed. Using 7Timer! init_time as base: {init_str}"
                )
            # --- END FIX ---

            for ablk in astro_data_7t.get("dataseries", []):
                tp_7timer = ablk.get("timepoint")
                if tp_7timer is None:
                    continue

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
                        if "seeing" in ablk:
                            base_dataseries[tp]["seeing"] = ablk["seeing"]
                        if "transparency" in ablk:
                            base_dataseries[tp]["transparency"] = ablk["transparency"]
                        # --- END FIX ---
                    else:
                        # Open-Meteo failed, use 7Timer! block as a fallback
                        # Add cloudcover placeholder if it doesn't exist
                        ablk.setdefault("cloudcover", 9)
                        base_dataseries[tp] = ablk

                except Exception as e:
                    print(
                        f"[Weather Func] WARN: Skipping 7Timer! block, could not align timepoints. Error: {e}"
                    )

        # print(f"[Weather Func] Merge complete.")
    else:
        _rate_limited_error(
            f"[Weather Func] WARN: 7Timer! 'astro' (enhancement) fetch failed for key '{cache_key}'. Seeing data will be unavailable."
        )

    # --- END: NEW HYBRID FETCH LOGIC ---

    # --- Cache Update or Return Stale ---
    if base_dataseries:  # If we have *any* data (even just Open-Meteo)
        final_data_to_cache = {
            "init": init_str,
            "dataseries": list(base_dataseries.values()),
        }
        _update_cache_ok(final_data_to_cache, ttl_hours=3)
        return final_data_to_cache
    else:
        # Both failed. Return last good data if available.
        print(
            f"[Weather Func] All sources failed. Returning stale data (if available) for key '{cache_key}'."
        )
        return last_good or None


def generate_session_id():
    """Generates a unique session ID."""
    return uuid.uuid4().hex


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
    theme_preference = (
        user_config.get("theme_preference", "follow_system")
        if user_config
        else "follow_system"
    )
    # Translation status from upstream i18n feature
    translation_status = {
        "en": "complete",
        "de": "complete",
        "fr": "complete",
        "es": "complete",
        "ja": "auto",
        "zh": "auto",
    }
    # Read from user_config first (authenticated users), then session (guests)
    current_language = (
        user_config.get("language") if user_config else None
    ) or session.get("language", "en")
    return {
        "SINGLE_USER_MODE": SINGLE_USER_MODE,
        "current_user": current_user,
        "is_guest": getattr(g, "is_guest", False),
        "user_theme_preference": theme_preference,
        "current_language": current_language,
        "supported_languages": app.config.get("BABEL_SUPPORTED_LOCALES", ["en"]),
        "ai_enabled": bool(app.config.get("AI_API_KEY"))
    }


@core_bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.clear()  # Optional: reset session if needed
    flash("Logged out successfully!", "success")
    return redirect(url_for("core.login"))


@core_bp.route("/set_language/<lang>")
def set_language(lang):
    """Set the user's preferred language and redirect back."""
    # Validate the language is supported
    supported_locales = app.config.get("BABEL_SUPPORTED_LOCALES", ["en"])
    if lang not in supported_locales:
        flash(_("Language '%(lang)s' is not supported.", lang=lang), "error")
        return redirect(request.referrer or url_for("core.index"))

    # Get the current user
    if not hasattr(g, "db_user") or not g.db_user:
        # For guest users, just set session and redirect
        session["language"] = lang
        return redirect(request.referrer or url_for("core.index"))

    # Save to UiPref.json_blob for authenticated users
    db = get_db()
    try:
        prefs = db.query(UiPref).filter_by(user_id=g.db_user.id).first()
        if not prefs:
            prefs = UiPref(user_id=g.db_user.id, json_blob="{}")
            db.add(prefs)

        # Load existing settings, add language, save back
        try:
            settings = json.loads(prefs.json_blob or "{}")
        except json.JSONDecodeError:
            settings = {}

        settings["language"] = lang
        prefs.json_blob = json.dumps(settings, ensure_ascii=False)
        db.commit()

        # Update g.user_config for current request
        if hasattr(g, "user_config"):
            g.user_config["language"] = lang

        # Also set session for consistency with context processor
        session["language"] = lang

    except Exception as e:
        db.rollback()
        print(f"[SET_LANGUAGE] Error saving language preference: {e}")

    # Redirect back to the previous page
    return redirect(request.referrer or url_for("core.index"))


def get_static_cache_key(obj_name, date_str, location):
    return f"{obj_name.lower()}_{date_str}_{location.lower()}"


def get_static_nightly_values(
    ra,
    dec,
    obj_name,
    local_date,
    fixed_time_utc_str,
    location,
    lat,
    lon,
    tz_name,
    alt_threshold,
):
    key = get_static_cache_key(obj_name, local_date, location)
    if key in static_cache:
        return static_cache[key]

    # Otherwise calculate and cache
    alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)
    transit_time = calculate_transit_time(ra, lat, lon, tz_name)
    altitude_threshold = g.user_config.get("altitude_threshold", 20)
    obs_duration, max_altitude, _obs_from, _obs_to = (
        calculate_observable_duration_vectorized(
            ra, dec, lat, lon, local_date, tz_name, altitude_threshold
        )
    )
    static_cache[key] = {
        "Altitude 11PM": alt_11pm,
        "Azimuth 11PM": az_11pm,
        "Transit Time": transit_time,
        "Observable Duration (min)": int(obs_duration.total_seconds() / 60),
        "Max Altitude (°)": round(max_altitude, 1)
        if max_altitude is not None
        else "N/A",
    }
    return static_cache[key]


@core_bp.route("/trigger_update", methods=["POST"])
@permission_required("settings.edit")
def trigger_update():
    try:
        script_path = os.path.join(os.path.dirname(__file__), "updater.py")
        subprocess.Popen([sys.executable, script_path])
        print("Exiting current app to allow updater to restart it...")
        sys.exit(0)  # Force exit without cleanup
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@core_bp.route("/login", methods=["GET", "POST"])
def login():
    if SINGLE_USER_MODE:
        # In single-user mode, the login page is not needed, just redirect.
        return redirect(url_for("core.index"))

    # --- MULTI-USER MODE LOGIC ---
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        db_sess = SessionLocal()
        try:
            user = db_sess.query(DbUser).filter_by(username=username).first()
            if (
                user
                and user.password_hash
                and check_password_hash(user.password_hash, password)
            ):
                if not user.active:
                    flash("Account is deactivated.", "error")
                    return render_template("login.html")
                db_sess.expunge(user)
                print(
                    f"[DEBUG login] Calling login_user for user.id={user.id}, username={user.username}"
                )
                login_user(user, remember=True)
                print(f"[DEBUG login] After login_user, session={dict(session)}")
                record_login()
                session.modified = True
                flash("Logged in successfully!", "success")

                next_page = request.form.get("next")
                if next_page and next_page.startswith("/"):
                    return redirect(next_page, code=303)
                return redirect(url_for("core.index"), code=303)
            else:
                flash("Invalid username or password.", "error")
        finally:
            db_sess.close()
    return render_template("login.html")


@core_bp.route("/account/change-password", methods=["POST"])
@login_required
def change_password():
    """Allow authenticated users to change their own password."""
    if SINGLE_USER_MODE:
        flash(_("Password change is not available in single-user mode."), "error")
        return redirect(url_for("core.config_form"))

    current_password = request.form.get("current_password", "").strip()
    new_password = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()

    if not current_password or not new_password or not confirm_password:
        flash(_("All fields are required."), "error")
        return redirect(url_for("core.config_form") + "#account")

    if new_password != confirm_password:
        flash(_("New passwords do not match."), "error")
        return redirect(url_for("core.config_form") + "#account")

    if len(new_password) < 8:
        flash(_("Password must be at least 8 characters."), "error")
        return redirect(url_for("core.config_form") + "#account")

    db_sess = SessionLocal()
    try:
        user = db_sess.query(DbUser).filter_by(id=current_user.id).first()
        if not user:
            flash(_("User not found."), "error")
            return redirect(url_for("core.config_form") + "#account")

        if not user.check_password(current_password):
            flash(_("Current password is incorrect."), "error")
            return redirect(url_for("core.config_form") + "#account")

        user.set_password(new_password)
        db_sess.commit()
        flash(_("Password changed successfully."), "success")
    except Exception as e:
        db_sess.rollback()
        logging.exception("Error changing password")
        flash(_("An error occurred while changing password."), "error")
    finally:
        db_sess.close()

    return redirect(url_for("core.config_form") + "#account")


@core_bp.route("/account/change-username", methods=["POST"])
@login_required
def change_username():
    """Allow authenticated users to change their own username."""
    if SINGLE_USER_MODE:
        flash(_("Username change is not available in single-user mode."), "error")
        return redirect(url_for("core.config_form"))

    new_username = request.form.get("new_username", "").strip()
    current_password = request.form.get("current_password", "").strip()

    if not new_username or not current_password:
        flash(_("All fields are required."), "error")
        return redirect(url_for("core.config_form") + "#account")

    # Validate username format
    import re

    if not re.match(r"^[a-zA-Z0-9_-]{3,80}$", new_username):
        flash(
            _(
                "Username must be 3-80 characters and contain only letters, numbers, underscores, and hyphens."
            ),
            "error",
        )
        return redirect(url_for("core.config_form") + "#account")

    # Check reserved usernames
    reserved = ["admin", "root", "system", "guest", "default", "guest_user"]
    if (
        new_username.lower() in reserved
        and new_username.lower() != current_user.username.lower()
    ):
        flash(_("This username is reserved."), "error")
        return redirect(url_for("core.config_form") + "#account")

    db_sess = SessionLocal()
    try:
        user = db_sess.query(DbUser).filter_by(id=current_user.id).first()
        if not user:
            flash(_("User not found."), "error")
            return redirect(url_for("core.config_form") + "#account")

        if not user.check_password(current_password):
            flash(_("Current password is incorrect."), "error")
            return redirect(url_for("core.config_form") + "#account")

        # Check if username is already taken
        existing = (
            db_sess.query(DbUser)
            .filter(DbUser.username == new_username, DbUser.id != current_user.id)
            .first()
        )
        if existing:
            flash(_("This username is already taken."), "error")
            return redirect(url_for("core.config_form") + "#account")

        user.username = new_username
        db_sess.commit()
        flash(_("Username changed successfully."), "success")
    except Exception as e:
        db_sess.rollback()
        logging.exception("Error changing username")
        flash(_("An error occurred while changing username."), "error")
    finally:
        db_sess.close()

    return redirect(url_for("core.config_form") + "#account")


# =============================================================================
# API KEY MANAGEMENT (WEB UI)
# =============================================================================


@core_bp.route("/account/api-keys/create", methods=["POST"])
@login_required
@permission_required("api_keys.manage")
def create_api_key_web():
    """Create a new API key from the web UI."""
    if SINGLE_USER_MODE:
        flash(_("API key management is not available in single-user mode."), "error")
        return redirect(url_for("core.config_form"))

    name = request.form.get("key_name", "").strip() or "unnamed"
    db_sess = SessionLocal()
    try:
        active_count = (
            db_sess.query(ApiKey)
            .filter(ApiKey.user_id == current_user.id, ApiKey.is_active.is_(True))
            .count()
        )
        if active_count >= 25:
            flash(
                _(
                    "Maximum 25 active API keys reached. Revoke one before creating a new key."
                ),
                "error",
            )
            return redirect(url_for("core.config_form") + "#account")

        raw_key = create_api_key(db_sess, current_user.id, name=name)
        session["new_api_key"] = raw_key
        flash(
            _("API key created. Copy it now — it will not be shown again."), "success"
        )
    except Exception:
        db_sess.rollback()
        logging.exception("Error creating API key")
        flash(_("An error occurred while creating the API key."), "error")
    finally:
        db_sess.close()

    return redirect(url_for("core.config_form") + "#account")


@core_bp.route("/account/api-keys/<int:key_id>/revoke", methods=["POST"])
@login_required
@permission_required("api_keys.manage")
def revoke_api_key_web(key_id):
    """Revoke (soft-delete) an API key from the web UI."""
    if SINGLE_USER_MODE:
        flash(_("API key management is not available in single-user mode."), "error")
        return redirect(url_for("core.config_form"))

    db_sess = SessionLocal()
    try:
        key = (
            db_sess.query(ApiKey)
            .filter(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
            .first()
        )
        if not key:
            flash(_("API key not found."), "error")
        elif not key.is_active:
            flash(_("This API key has already been revoked."), "error")
        else:
            key.is_active = False
            db_sess.commit()
            flash(_("API key revoked."), "success")
    except Exception:
        db_sess.rollback()
        logging.exception("Error revoking API key")
        flash(_("An error occurred while revoking the API key."), "error")
    finally:
        db_sess.close()

    return redirect(url_for("core.config_form") + "#account")


@core_bp.route("/account/api-keys/<int:key_id>/regenerate", methods=["POST"])
@login_required
@permission_required("api_keys.manage")
def regenerate_api_key_web(key_id):
    """Rotate an API key: revoke old one, create new one with the same name."""
    if SINGLE_USER_MODE:
        flash(_("API key management is not available in single-user mode."), "error")
        return redirect(url_for("core.config_form"))

    db_sess = SessionLocal()
    try:
        old_key = (
            db_sess.query(ApiKey)
            .filter(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
            .first()
        )
        if not old_key:
            flash(_("API key not found."), "error")
            return redirect(url_for("core.config_form") + "#account")

        if not old_key.is_active:
            flash(_("Cannot regenerate a revoked key."), "error")
            return redirect(url_for("core.config_form") + "#account")

        name = old_key.name
        old_key.is_active = False
        db_sess.commit()  # Revoke first — don't exceed active limit

        raw_key = create_api_key(db_sess, current_user.id, name=name)
        session["new_api_key"] = raw_key
        flash(
            _("API key regenerated. Copy the new key — it will not be shown again."),
            "success",
        )
    except Exception:
        db_sess.rollback()
        logging.exception("Error regenerating API key")
        flash(_("An error occurred while regenerating the API key."), "error")
    finally:
        db_sess.close()

    return redirect(url_for("core.config_form") + "#account")


@core_bp.route("/sso/login")
def sso_login():
    # First, check if the app is in single-user mode. SSO is not applicable here.
    if SINGLE_USER_MODE:
        flash("Single Sign-On is not applicable in single-user mode.", "error")
        return redirect(url_for("core.index"))

    # Get the token from the URL (e.g., ?token=...)
    token = request.args.get("token")
    if not token:
        flash("SSO Error: No token provided.", "error")
        return redirect(url_for("core.login"))

    # Get the shared secret key from the .env file
    secret_key = os.environ.get("JWT_SECRET_KEY")
    if not secret_key:
        flash("SSO Error: SSO is not configured on the server.", "error")
        return redirect(url_for("core.login"))

    try:
        # Decode the token. This automatically verifies the signature and expiration.
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        username = payload.get("username")

        if not username:
            raise jwt.InvalidTokenError("Token is missing username.")

        # Find the user in the Nova database (using DbUser from app.db)
        db_sess = SessionLocal()
        try:
            user = db_sess.query(DbUser).filter_by(username=username).first()
            if user and user.is_active:
                db_sess.expunge(user)
                login_user(user)  # Log the user in using Flask-Login
                record_login()
                session.modified = True  # Force session save before redirect
                flash(f"Welcome back, {user.username}!", "success")
                return redirect(url_for("core.index"), code=303)
            else:
                flash(
                    f"SSO Error: User '{username}' not found or is disabled in Nova.",
                    "error",
                )
                return redirect(url_for("core.login"))
        finally:
            db_sess.close()

    except jwt.ExpiredSignatureError:
        flash(
            "SSO Error: The login link has expired. Please try again from WordPress.",
            "error",
        )
        return redirect(url_for("core.login"))
    except jwt.InvalidTokenError:
        flash("SSO Error: Invalid login token.", "error")
        return redirect(url_for("core.login"))


@core_bp.route("/proxy_focus", methods=["POST"])
@permission_required("settings.stellarium")
def proxy_focus():
    payload = request.form
    try:
        # This line ensures the dynamically determined STELLARIUM_API_URL_BASE is used:
        stellarium_focus_url = f"{STELLARIUM_API_URL_BASE}/api/main/focus"

        # print(f"[PROXY FOCUS] Attempting to connect to Stellarium at: {stellarium_focus_url}")  # For debugging

        # Make the request to Stellarium
        r = requests.post(
            stellarium_focus_url, data=payload, timeout=DEFAULT_HTTP_TIMEOUT
        )  # Added timeout
        r.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        # print(f"[PROXY FOCUS] Stellarium response: {r.status_code}")  # For debugging
        return jsonify({"status": "success", "stellarium_response": r.text})

    except requests.exceptions.ConnectionError:
        # Specific error if Stellarium isn't running or reachable at the URL
        message = f"Could not connect to Stellarium at {STELLARIUM_API_URL_BASE}. Ensure Stellarium is running, Remote Control is enabled, and the URL is correct."
        if STELLARIUM_ERROR_MESSAGE:  # User-defined message overrides if present
            message = STELLARIUM_ERROR_MESSAGE
        print(f"[PROXY FOCUS ERROR] ConnectionError: {message}")
        return jsonify(
            {"status": "error", "message": message}
        ), 503  # 503 Service Unavailable

    except requests.exceptions.Timeout:
        # Specific error for timeouts
        message = f"Connection to Stellarium at {STELLARIUM_API_URL_BASE} timed out after 10 seconds."
        print(f"[PROXY FOCUS ERROR] Timeout: {message}")
        return jsonify(
            {"status": "error", "message": message}
        ), 504  # 504 Gateway Timeout

    except requests.exceptions.HTTPError as http_err:
        # Specific error for HTTP errors from Stellarium (e.g., API errors)
        error_details = (
            http_err.response.text
            if http_err.response is not None
            else "No response details"
        )
        message = f"Stellarium at {STELLARIUM_API_URL_BASE} returned an error: {http_err}. Details: {error_details}"
        status_code = (
            http_err.response.status_code if http_err.response is not None else 500
        )
        print(f"[PROXY FOCUS ERROR] HTTPError {status_code}: {message}")
        return jsonify({"status": "error", "message": message}), status_code

    except Exception as e:
        # Catch-all for other unexpected errors
        message = (
            STELLARIUM_ERROR_MESSAGE
            or f"An unexpected error occurred while attempting to contact Stellarium: {str(e)}"
        )
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


@app.context_processor
def inject_version():
    return dict(version=APP_VERSION)






# =============================================================================
# Protected Routes
# =============================================================================


@core_bp.route("/get_locations")
@permission_required("locations.view")
def get_locations():
    """Returns only ACTIVE locations for the main UI dropdown and the user's default."""
    # Determine username based on mode and authentication status
    username = (
        "default"
        if SINGLE_USER_MODE
        else (current_user.username if current_user.is_authenticated else "guest_user")
    )
    db = get_db()
    try:
        # Find the user record in the application database
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            # If the user doesn't exist in app.db, return empty lists
            return jsonify({"locations": [], "selected": None})

        # Query the database for locations belonging to this user that are marked as active
        active_locs = (
            db.query(Location)
            .filter_by(user_id=user.id, active=True)
            .order_by(Location.name)
            .all()
        )
        # Extract location data including coordinates for weather feature
        active_loc_data = [
            {"name": loc.name, "lat": loc.lat, "lon": loc.lon} for loc in active_locs
        ]

        # Determine which location should be pre-selected in the dropdown
        selected = None
        # Find if any of the active locations is also marked as the default
        default_loc = next((loc.name for loc in active_locs if loc.is_default), None)

        if default_loc:
            # If an active default location exists, use it
            selected = default_loc
        elif active_loc_data:
            # Otherwise, if there are any active locations, use the first one in the list
            selected = active_loc_data[0]["name"]
        # If there are no active locations, 'selected' remains None

        # Return the list of active locations with coordinates and the name of the location to be selected
        return jsonify({"locations": active_loc_data, "selected": selected})
    except Exception as e:
        # Log any unexpected errors during database access
        print(f"Error in get_locations for user '{username}': {e}")
        # Return an error response or an empty list in case of failure
        return jsonify({"locations": [], "selected": None, "error": str(e)}), 500


@core_bp.route("/search_object", methods=["POST"])
@login_required
@permission_required("objects.view")
def search_object():
    # Expect JSON input with the object identifier.
    object_name = request.json.get("object")
    if not object_name:
        return jsonify({"status": "error", "message": _("No object specified.")}), 400

    data = get_ra_dec(object_name)
    if data and data.get("RA (hours)") is not None:
        return jsonify({"status": "success", "data": data})
    else:
        # Return an error message from the lookup.
        return jsonify(
            {"status": "error", "message": data.get("Common Name", "Object not found.")}
        ), 404


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


        return jsonify(
            {
                "status": "success",
                "data": {
                    "Type": clean_data.get("Type") or "",
                    "Magnitude": clean_data.get("Magnitude") or "",
                    "Size": clean_data.get("Size") or "",
                    "SB": clean_data.get("SB") or "",
                },
            }
        )
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
        raise ValueError(
            f"Invalid non-numeric value '{value}' received for {field_name}."
        )


@core_bp.route("/confirm_object", methods=["POST"])
@login_required
@permission_required("objects.create")
def confirm_object():
    req = request.get_json()
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        app_db_user = db.query(DbUser).filter_by(username=username).one()

        raw_object_name = req.get("object")
        if not raw_object_name or not raw_object_name.strip():
            raise ValueError("Object ID is required and cannot be empty.")

        # --- NEW: Normalize the name ---
        object_name = normalize_object_name(raw_object_name)

        common_name = req.get("name")
        if not common_name or not common_name.strip():
            # If common name is blank, use the raw (pretty) object name as a fallback
            common_name = raw_object_name.strip()

        ra_float = _parse_float_from_request(req.get("ra"), "RA")
        dec_float = _parse_float_from_request(req.get("dec"), "DEC")

        # --- START: Rich Text Logic for Notes ---
        # Get the raw HTML directly from the JS payload
        private_notes_html = req.get("project", "") or ""
        shared_notes_html = req.get("shared_notes", "") or ""
        # --- END: Rich Text Logic ---

        existing = (
            db.query(AstroObject)
            .filter_by(user_id=app_db_user.id, object_name=object_name)
            .one_or_none()
        )

        # Get other fields
        constellation = req.get("constellation")
        obj_type = convert_to_native_python(req.get("type"))
        magnitude = str(convert_to_native_python(req.get("magnitude")) or "")
        size = str(convert_to_native_python(req.get("size")) or "")
        sb = str(convert_to_native_python(req.get("sb")) or "")
        is_shared = req.get("is_shared") == True
        active_project = req.get("is_active") == True

        # Inspiration Fields
        image_url = req.get("image_url")
        image_credit = req.get("image_credit")
        image_source_link = req.get("image_source_link")
        description_text = req.get("description_text")
        description_credit = req.get("description_credit")
        description_source_link = req.get("description_source_link")

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
            # Update inspiration fields if provided (or clear them if empty string passed)
            if image_url is not None:
                existing.image_url = image_url
            if image_credit is not None:
                existing.image_credit = image_credit
            if image_source_link is not None:
                existing.image_source_link = image_source_link
            if description_text is not None:
                existing.description_text = description_text
            if description_credit is not None:
                existing.description_credit = description_credit
            if description_source_link is not None:
                existing.description_source_link = description_source_link
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
                active_project=active_project,
                image_url=image_url,
                image_credit=image_credit,
                image_source_link=image_source_link,
                description_text=description_text,
                description_credit=description_credit,
                description_source_link=description_source_link,
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


@api_bp.route("/api/update_object", methods=["POST"])
@login_required
@permission_required("objects.edit")
def update_object():
    """
    API endpoint to update a single AstroObject from the config form.
    Expects a JSON payload with all object fields.
    """
    db = get_db()
    try:
        data = request.get_json()
        object_name = data.get("object_id")
        username = "default" if SINGLE_USER_MODE else current_user.username

        user = db.query(DbUser).filter_by(username=username).one()
        obj = (
            db.query(AstroObject)
            .filter_by(user_id=user.id, object_name=object_name)
            .one_or_none()
        )

        if not obj:
            return jsonify({"status": "error", "message": _("Object not found")}), 404

        # Update all fields from the payload
        obj.common_name = data.get("name")
        obj.ra_hours = float(data.get("ra"))
        obj.dec_deg = float(data.get("dec"))
        obj.constellation = data.get("constellation")
        obj.type = data.get("type")
        obj.magnitude = data.get("magnitude")
        obj.size = data.get("size")
        obj.sb = data.get("sb")
        obj.active_project = data.get("is_active")
        # Update notes (JS sends the raw HTML from Trix)
        obj.project_name = data.get("project_notes")

        # --- Curation Fields ---
        obj.image_url = data.get("image_url")
        obj.image_credit = data.get("image_credit")
        obj.image_source_link = data.get("image_source_link")
        obj.description_text = data.get("description_text")
        obj.description_credit = data.get("description_credit")
        obj.description_source_link = data.get("description_source_link")
        # -----------------------

        if not SINGLE_USER_MODE:
            # Only update sharing if it's not an imported item
            if not obj.original_user_id:
                obj.is_shared = data.get("is_shared")
                obj.shared_notes = data.get("shared_notes")

        db.commit()
        return jsonify(
            {"status": "success", "message": f"Object '{object_name}' updated."}
        )

    except Exception as e:
        db.rollback()
        print(f"--- ERROR in /api/update_object ---")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route("/stream_fetch_details")
@login_required
@permission_required("objects.view")
def stream_fetch_details():
    """
    Streams progress of fetching object details via Server-Sent Events (SSE).
    """

    @stream_with_context
    def generate():
        username = "default" if SINGLE_USER_MODE else current_user.username
        db = SessionLocal()  # Use a dedicated session for this generator
        try:
            app_db_user = db.query(DbUser).filter_by(username=username).one()
            objects_to_check = (
                db.query(AstroObject).filter_by(user_id=app_db_user.id).all()
            )

            total_count = len(objects_to_check)
            modified_count = 0

            # Send initial open event
            yield f"data: {json.dumps({'progress': 0, 'message': 'Starting analysis...'})}\n\n"

            refetch_triggers = [None, "", "N/A", "Not Found", "Fetch Error"]

            for i, obj in enumerate(objects_to_check):
                # Calculate percentage
                pct = int((i / total_count) * 100)
                yield f"data: {json.dumps({'progress': pct, 'message': f'Checking {obj.object_name}...'})}\n\n"

                needs_update = (
                    obj.type in refetch_triggers
                    or obj.magnitude in refetch_triggers
                    or obj.size in refetch_triggers
                    or obj.sb in refetch_triggers
                    or obj.constellation in refetch_triggers
                )

                if needs_update:
                    item_modified = False
                    try:
                        # 1. Constellation Auto-Calc
                        if (
                            obj.constellation in refetch_triggers
                            and obj.ra_hours is not None
                            and obj.dec_deg is not None
                        ):
                            coords = SkyCoord(
                                ra=obj.ra_hours * u.hourangle, dec=obj.dec_deg * u.deg
                            )
                            obj.constellation = get_constellation(
                                coords, short_name=True
                            )
                            item_modified = True

                        # 2. External API Fetch
                        yield f"data: {json.dumps({'progress': pct, 'message': f'Fetching data for {obj.object_name}...'})}\n\n"
                        fetched_data = nova_data_fetcher.get_astronomical_data(
                            obj.object_name
                        )

                        if fetched_data.get("object_type"):
                            obj.type = fetched_data["object_type"]
                            item_modified = True
                        if fetched_data.get("magnitude"):
                            obj.magnitude = str(fetched_data["magnitude"])
                            item_modified = True
                        if fetched_data.get("size_arcmin"):
                            obj.size = str(fetched_data["size_arcmin"])
                            item_modified = True
                        if fetched_data.get("surface_brightness"):
                            obj.sb = str(fetched_data["surface_brightness"])
                            item_modified = True

                        if item_modified:
                            modified_count += 1
                            time.sleep(0.5)  # Polite delay

                    except Exception as e:
                        print(f"Failed details fetch for {obj.object_name}: {e}")
                        # Continue stream despite individual object error

            if modified_count > 0:
                yield f"data: {json.dumps({'progress': 99, 'message': 'Saving changes...'})}\n\n"
                db.commit()

            # Send final done signal
            yield f"data: {json.dumps({'progress': 100, 'message': 'Complete!', 'done': True, 'modified': modified_count})}\n\n"

        except Exception as e:
            print(f"Stream Fetch Error: {e}")
            db.rollback()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            db.close()

    # --- FIX FOR NGINX BUFFERING ---
    response = Response(generate(), mimetype="text/event-stream")
    response.headers["X-Accel-Buffering"] = "no"  # Disable Nginx buffering
    response.headers["Cache-Control"] = "no-cache"  # Prevent browser caching
    response.headers["Connection"] = "keep-alive"  # Keep connection open
    return response


@core_bp.route("/fetch_all_details", methods=["POST"])
@login_required
@permission_required("objects.view")
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
                obj.type in refetch_triggers
                or obj.magnitude in refetch_triggers
                or obj.size in refetch_triggers
                or obj.sb in refetch_triggers
                or obj.constellation in refetch_triggers
            )
            if needs_update:
                try:
                    # Auto-calculate Constellation if missing
                    if (
                        obj.constellation in refetch_triggers
                        and obj.ra_hours is not None
                        and obj.dec_deg is not None
                    ):
                        coords = SkyCoord(
                            ra=obj.ra_hours * u.hourangle, dec=obj.dec_deg * u.deg
                        )
                        obj.constellation = get_constellation(coords, short_name=True)
                        modified = True

                    # Fetch other details from external API
                    fetched_data = nova_data_fetcher.get_astronomical_data(
                        obj.object_name
                    )
                    if fetched_data.get("object_type"):
                        obj.type = fetched_data["object_type"]
                    if fetched_data.get("magnitude"):
                        obj.magnitude = str(fetched_data["magnitude"])
                    if fetched_data.get("size_arcmin"):
                        obj.size = str(fetched_data["size_arcmin"])
                    if fetched_data.get("surface_brightness"):
                        obj.sb = str(fetched_data["surface_brightness"])
                    modified = True
                    time.sleep(0.5)  # Be kind to external APIs
                except Exception as e:
                    print(f"Failed to fetch details for {obj.object_name}: {e}")

        if modified:
            db.commit()
            flash(_("Fetched and saved missing object details."), "success")
        else:
            flash(_("No missing data found or no updates needed."), "info")

    except Exception as e:
        db.rollback()
        flash(_("An error occurred during data fetching: %(error)s", error=e), "error")

    return redirect(url_for("core.config_form"))


@api_bp.route("/api/get_object_list")
@permission_required("objects.view")
def get_object_list():
    load_full_astro_context()
    """
    A new, very fast endpoint that just returns the list of object names.
    """
    # Filter g.objects_list to return only enabled object names
    enabled_names = [o["Object"] for o in g.objects_list if o.get("enabled", True)]
    return jsonify({"objects": enabled_names})


@api_bp.route("/api/journal/objects")
@login_required
@permission_required("journal.view")
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
    sessions_with_data = (
        db.query(
            JournalSession.id,
            JournalSession.object_name,
            JournalSession.date_utc,
            JournalSession.calculated_integration_time_minutes,
            JournalSession.location_name,
            AstroObject.common_name,
            AstroObject.id.label("astro_id"),
        )
        .outerjoin(
            AstroObject,
            and_(
                AstroObject.user_id == user_id,
                AstroObject.object_name == JournalSession.object_name,
            ),
        )
        .filter(
            JournalSession.user_id == user_id,
            or_(
                JournalSession.number_of_subs_light > 0,
                JournalSession.calculated_integration_time_minutes > 0,
            ),
        )
        .order_by(JournalSession.object_name, JournalSession.date_utc.desc())
        .all()
    )

    # Aggregate by object_name
    objects_map = {}
    for session in sessions_with_data:
        object_name = session.object_name
        if not object_name:
            continue

        if object_name not in objects_map:
            objects_map[object_name] = {
                "id": session.astro_id,
                "name": session.common_name or object_name,
                "catalog_id": object_name,
                "total_minutes": 0,
                "last_session": None,
                "first_session_date": None,
                "first_session_id": None,
                "first_session_location": None,
            }

        # Accumulate integration time
        if session.calculated_integration_time_minutes:
            objects_map[object_name]["total_minutes"] += (
                session.calculated_integration_time_minutes
            )

        # Track most recent session date (for sorting)
        if session.date_utc:
            if (
                objects_map[object_name]["last_session"] is None
                or session.date_utc > objects_map[object_name]["last_session"]
            ):
                objects_map[object_name]["last_session"] = session.date_utc

        # Track first (oldest) session date, id, and location (for navigation)
        if session.date_utc:
            if (
                objects_map[object_name]["first_session_date"] is None
                or session.date_utc < objects_map[object_name]["first_session_date"]
            ):
                objects_map[object_name]["first_session_date"] = session.date_utc
                objects_map[object_name]["first_session_id"] = session.id
                objects_map[object_name]["first_session_location"] = (
                    session.location_name
                )

    # Convert to list and sort by last_session DESC
    result = []
    for obj in objects_map.values():
        total_hours = (
            round(obj["total_minutes"] / 60.0, 1) if obj["total_minutes"] else 0.0
        )
        first_session_id = obj["first_session_id"]
        first_session_location = obj["first_session_location"]

        # Only include location if it's a non-empty string
        first_session_location = (
            first_session_location.strip() if first_session_location else None
        )

        # Build URL with location parameter if available
        url_params = {"object_name": obj["catalog_id"], "tab": "journal"}
        if first_session_id:
            url_params["session_id"] = first_session_id
        if first_session_location:
            url_params["location"] = first_session_location

        result.append(
            {
                "id": obj["id"],
                "name": obj["name"],
                "catalog_id": obj["catalog_id"],
                "total_hours": total_hours,
                "last_session": obj["last_session"].strftime("%Y-%m-%d")
                if obj["last_session"]
                else None,
                "first_session_date": obj["first_session_date"].strftime("%Y-%m-%d")
                if obj["first_session_date"]
                else None,
                "first_session_location": first_session_location,
                "url": url_for("core.graph_dashboard", **url_params, _external=False),
            }
        )

    # Sort by last_session descending (most recent first)
    result.sort(key=lambda x: x["last_session"] or "0000-00-00", reverse=True)

    return jsonify(result)


@api_bp.route("/api/bulk_update_objects", methods=["POST"])
@login_required
@permission_required("objects.bulk_edit")
def bulk_update_objects():
    data = request.get_json()
    action = data.get("action")  # 'enable', 'disable', 'delete'
    object_ids = data.get("object_ids", [])

    if not action or not object_ids:
        return jsonify(
            {"status": "error", "message": "Missing action or object_ids"}
        ), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        query = db.query(AstroObject).filter(
            AstroObject.user_id == user_id, AstroObject.object_name.in_(object_ids)
        )

        if action == "delete":
            # Check for dependencies (Journals or Projects) before deleting
            safe_to_delete = []
            skipped_count = 0

            # We need to iterate to check relationships since bulk delete bypasses Python-level checks
            objects_to_check = query.all()

            for obj in objects_to_check:
                # Check for journal sessions using this object name
                has_journals = (
                    db.query(JournalSession)
                    .filter_by(user_id=user_id, object_name=obj.object_name)
                    .first()
                )

                # Check for projects targeting this object
                has_projects = (
                    db.query(Project)
                    .filter_by(user_id=user_id, target_object_name=obj.object_name)
                    .first()
                )

                if has_journals or has_projects:
                    skipped_count += 1
                else:
                    safe_to_delete.append(obj.object_name)

            if safe_to_delete:
                # Perform the delete only on safe IDs
                delete_q = db.query(AstroObject).filter(
                    AstroObject.user_id == user_id,
                    AstroObject.object_name.in_(safe_to_delete),
                )
                count = delete_q.delete(synchronize_session=False)
            else:
                count = 0

            msg = f"Deleted {count} objects."
            if skipped_count > 0:
                msg += f" (Skipped {skipped_count} objects used in Journals/Projects)"
        elif action == "enable":
            count = query.update({AstroObject.enabled: True}, synchronize_session=False)
            msg = f"Enabled {count} objects."
        elif action == "disable":
            count = query.update(
                {AstroObject.enabled: False}, synchronize_session=False
            )
            msg = f"Disabled {count} objects."
        else:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        db.commit()
        return jsonify({"status": "success", "message": msg})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route("/api/bulk_fetch_details", methods=["POST"])
@login_required
@permission_required("objects.view")
def bulk_fetch_details():
    """Fetch missing details (type, magnitude, size, SB, constellation) for selected objects."""
    data = request.get_json()
    object_ids = data.get("object_ids", [])

    if not object_ids:
        return jsonify({"status": "error", "message": "No objects selected"}), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        # Query only the selected objects for this user
        objects_to_check = (
            db.query(AstroObject)
            .filter(
                AstroObject.user_id == user_id, AstroObject.object_name.in_(object_ids)
            )
            .all()
        )

        if not objects_to_check:
            return jsonify(
                {"status": "error", "message": "No valid objects found"}
            ), 400

        updated_count = 0
        error_count = 0
        refetch_triggers = [None, "", "N/A", "Not Found", "Fetch Error"]

        for obj in objects_to_check:
            needs_update = (
                obj.type in refetch_triggers
                or obj.magnitude in refetch_triggers
                or obj.size in refetch_triggers
                or obj.sb in refetch_triggers
                or obj.constellation in refetch_triggers
            )
            if needs_update:
                try:
                    # Auto-calculate Constellation if missing
                    if (
                        obj.constellation in refetch_triggers
                        and obj.ra_hours is not None
                        and obj.dec_deg is not None
                    ):
                        coords = SkyCoord(
                            ra=obj.ra_hours * u.hourangle, dec=obj.dec_deg * u.deg
                        )
                        obj.constellation = get_constellation(coords, short_name=True)

                    # Fetch other details from external API
                    fetched_data = nova_data_fetcher.get_astronomical_data(
                        obj.object_name
                    )
                    if fetched_data.get("object_type"):
                        obj.type = fetched_data["object_type"]
                    if fetched_data.get("magnitude"):
                        obj.magnitude = str(fetched_data["magnitude"])
                    if fetched_data.get("size_arcmin"):
                        obj.size = str(fetched_data["size_arcmin"])
                    if fetched_data.get("surface_brightness"):
                        obj.sb = str(fetched_data["surface_brightness"])
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

        return jsonify(
            {
                "status": "success",
                "message": msg,
                "updated": updated_count,
                "errors": error_count,
            }
        )

    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route("/api/find_duplicates")
@login_required
@permission_required("objects.merge")
def find_duplicates():
    load_full_astro_context()
    user_id = g.db_user.id

    # 1. Get all objects with valid coordinates
    all_objects = [
        o
        for o in g.objects_list
        if o.get("RA (hours)") is not None and o.get("DEC (degrees)") is not None
    ]

    if len(all_objects) < 2:
        return jsonify({"status": "success", "duplicates": []})

    # 2. Create SkyCoord objects
    ra_vals = [o["RA (hours)"] * 15.0 for o in all_objects]  # Convert to degrees
    dec_vals = [o["DEC (degrees)"] for o in all_objects]

    coords = SkyCoord(ra=ra_vals * u.deg, dec=dec_vals * u.deg)

    # 3. Find matches within 2.5 arcminutes
    # search_around_sky finds all pairs (i, j) where distance < limit
    # This includes (i, i) self-matches and (i, j) + (j, i) duplicates
    idx1, idx2, d2d, d3d = search_around_sky(coords, coords, seplimit=2.5 * u.arcmin)

    potential_duplicates = []
    seen_pairs = set()

    for i, j, dist in zip(idx1, idx2, d2d):
        if i >= j:
            continue  # Skip self-matches and reverse duplicates

        obj_a = all_objects[i]
        obj_b = all_objects[j]

        # Create a unique key for this pair
        pair_key = tuple(sorted([obj_a["Object"], obj_b["Object"]]))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        potential_duplicates.append(
            {
                "object_a": obj_a,
                "object_b": obj_b,
                "separation_arcmin": round(dist.to(u.arcmin).value, 2),
            }
        )

    return jsonify({"status": "success", "duplicates": potential_duplicates})


@api_bp.route("/api/merge_objects", methods=["POST"])
@login_required
@permission_required("objects.merge")
def merge_objects():
    data = request.get_json()
    keep_id = data.get("keep_id")
    merge_id = data.get("merge_id")

    if not keep_id or not merge_id:
        return jsonify({"status": "error", "message": "Missing object IDs"}), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        # 1. Fetch Objects
        obj_keep = (
            db.query(AstroObject)
            .filter_by(user_id=user_id, object_name=keep_id)
            .one_or_none()
        )
        obj_merge = (
            db.query(AstroObject)
            .filter_by(user_id=user_id, object_name=merge_id)
            .one_or_none()
        )

        if not obj_keep or not obj_merge:
            return jsonify(
                {"status": "error", "message": "One or both objects not found."}
            ), 404

        print(f"[MERGE] Merging '{merge_id}' INTO '{keep_id}'...")

        # 2. Re-link Journals
        journals = (
            db.query(JournalSession)
            .filter_by(user_id=user_id, object_name=merge_id)
            .all()
        )
        for j in journals:
            j.object_name = keep_id
        print(f"   -> Moved {len(journals)} journal sessions.")

        # 3. Re-link Projects
        projects = (
            db.query(Project)
            .filter_by(user_id=user_id, target_object_name=merge_id)
            .all()
        )
        for p in projects:
            p.target_object_name = keep_id
        print(f"   -> Updated {len(projects)} projects.")

        # 4. Handle Framings
        framing_keep = (
            db.query(SavedFraming)
            .filter_by(user_id=user_id, object_name=keep_id)
            .one_or_none()
        )
        framing_merge = (
            db.query(SavedFraming)
            .filter_by(user_id=user_id, object_name=merge_id)
            .one_or_none()
        )

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
        return jsonify(
            {
                "status": "success",
                "message": f"Successfully merged '{merge_id}' into '{keep_id}'.",
            }
        )

    except Exception as e:
        db.rollback()
        print(f"[MERGE ERROR] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route("/api/help/img/<path:filename>")
@permission_required("settings.view")
def get_help_image(filename):
    """Serves images located in the help_docs directory."""
    return send_from_directory(os.path.join(_project_root, "help_docs"), filename)


@api_bp.route("/api/help/<topic_id>")
@permission_required("settings.view")
def get_help_content(topic_id):
    """
    Reads a markdown file from help_docs/{locale}/, converts it to HTML, and returns it.
    Falls back to English if the localized file doesn't exist.
    """
    # 1. Sanitize input to prevent directory traversal
    safe_topic = "".join([c for c in topic_id if c.isalnum() or c in "_-"])

    # 2. Build file path
    file_path = os.path.join(_project_root, "help_docs", f"{safe_topic}.md")

    # 3. Fallback to English if localized file doesn't exist
    if not os.path.exists(file_path):
        file_path = os.path.join(_project_root, "help_docs", "en", f"{safe_topic}.md")

    # 4. Check if file exists (even after fallback)
    if not os.path.exists(file_path):
        return jsonify(
            {
                "error": True,
                "html": f"<h3>Topic Not Found</h3><p>No help file found for ID: <code>{safe_topic}</code></p>",
            }
        ), 404

    # 5. Read and convert
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
            # Extensions: 'fenced_code' adds support for ```code blocks```
            html_content = markdown.markdown(text, extensions=["fenced_code", "tables"])
            return jsonify({"status": "success", "html": html_content})
    except Exception as e:
        return jsonify(
            {"error": True, "html": f"<p>Error reading help file: {str(e)}</p>"}
        ), 500


@api_bp.route("/api/get_object_data/<path:object_name>")
@permission_required("objects.view")
def get_object_data(object_name):
    # --- 1. Determine User (No change needed here) ---
    if SINGLE_USER_MODE:
        username = "default"
    elif current_user.is_authenticated:
        username = current_user.username
    elif request.args.get("location"):  # Allow guest if location provided
        username = "guest_user"
    else:
        # Deny guest if no location specified (cannot determine defaults)
        return jsonify(
            {
                "Object": object_name,
                "Common Name": "Error: Authentication required.",
                "error": True,
            }
        ), 401

    db = get_db()
    try:
        # --- 2. Get User Record (No change needed here) ---
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            return jsonify(
                {
                    "Object": object_name,
                    "Common Name": "Error: User not found.",
                    "error": True,
                }
            ), 404

        # --- 3. Determine Location to Use (Modified: Query DB directly) ---
        requested_location_name = request.args.get("location")
        selected_location = None
        current_location_config = {}

        if requested_location_name:
            # Try to load the specific location requested
            selected_location = (
                db.query(Location)
                .filter_by(user_id=user.id, name=requested_location_name)
                .options(selectinload(Location.horizon_points))
                .one_or_none()
            )
            if not selected_location:
                return jsonify(
                    {
                        "Object": object_name,
                        "Common Name": "Error: Requested location not found.",
                        "error": True,
                    }
                ), 404
        else:
            # Fallback to the user's default location
            selected_location = (
                db.query(Location)
                .filter_by(user_id=user.id, is_default=True)
                .options(selectinload(Location.horizon_points))
                .one_or_none()
            )
            # If no default, try the first active one
            if not selected_location:
                selected_location = (
                    db.query(Location)
                    .filter_by(user_id=user.id, active=True)
                    .options(selectinload(Location.horizon_points))
                    .order_by(Location.id)
                    .first()
                )

        if not selected_location:
            return jsonify(
                {
                    "Object": object_name,
                    "Common Name": "Error: No valid location configured or selected.",
                    "error": True,
                }
            ), 400

        # Extract details from the selected location object
        lat = selected_location.lat
        lon = selected_location.lon
        tz_name = selected_location.timezone
        selected_location_name = selected_location.name
        # Build the config-like dict for horizon mask etc.
        horizon_mask = [
            [hp.az_deg, hp.alt_min_deg]
            for hp in sorted(selected_location.horizon_points, key=lambda p: p.az_deg)
        ]
        current_location_config = {
            "lat": lat,
            "lon": lon,
            "timezone": tz_name,
            "altitude_threshold": selected_location.altitude_threshold,
            "horizon_mask": horizon_mask,
            # Add other fields if needed by calculations below
        }
        # --- End Location Determination ---

        # --- 4. Query ONLY the specific object ---
        obj_record = (
            db.query(AstroObject)
            .filter_by(user_id=user.id, object_name=object_name)
            .one_or_none()
        )

        # Handle case where object isn't found for this user
        if not obj_record:
            # Optionally try SIMBAD as a fallback *here* if desired,
            # or just return not found. Let's return not found for now.
            return jsonify(
                {
                    "Object": object_name,
                    "Common Name": f"Error: Object '{object_name}' not found in your config.",
                    "error": True,
                }
            ), 404

        # Extract necessary details
        ra = obj_record.ra_hours
        dec = obj_record.dec_deg
        if ra is None or dec is None:
            return jsonify(
                {
                    "Object": object_name,
                    "Common Name": f"Error: RA/DEC missing for '{object_name}'.",
                    "error": True,
                }
            ), 400

        # --- 5. Perform Calculations (FIXED DATE LOGIC) ---
        local_tz = pytz.timezone(tz_name)
        current_datetime_local = datetime.now(local_tz)

        # Determine "Observing Night" Date
        # If it's before noon, we associate this time with the previous night's session
        # to ensure the time array (which starts at noon) covers the current moment.
        if current_datetime_local.hour < 12:
            local_date = (current_datetime_local.date() - timedelta(days=1)).strftime(
                "%Y-%m-%d"
            )
        else:
            local_date = current_datetime_local.strftime("%Y-%m-%d")

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
            sampling_interval = user_prefs_dict.get("sampling_interval_minutes") or 15
        else:
            sampling_interval = int(os.environ.get("CALCULATION_PRECISION", 15))

        cache_key = f"{object_name.lower()}_{local_date}_{selected_location_name.lower().replace(' ', '_')}"

        # Calculate or retrieve cached nightly data (logic remains similar)
        if cache_key not in nightly_curves_cache:
            times_local, times_utc = get_common_time_arrays(
                tz_name, local_date, sampling_interval
            )
            location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
            altaz_frame = AltAz(obstime=times_utc, location=location)
            altitudes = sky_coord.transform_to(altaz_frame).alt.deg
            azimuths = sky_coord.transform_to(altaz_frame).az.deg
            transit_time = calculate_transit_time(
                ra, dec, lat, lon, tz_name, local_date
            )
            obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
                ra,
                dec,
                lat,
                lon,
                local_date,
                tz_name,
                altitude_threshold,
                sampling_interval,
                horizon_mask=horizon_mask,  # Pass the specific mask
            )
            fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)
            alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)
            is_obstructed_at_11pm = False
            if horizon_mask and len(horizon_mask) > 1:
                sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
                required_altitude_11pm = interpolate_horizon(
                    az_11pm, sorted_mask, altitude_threshold
                )
                if alt_11pm >= altitude_threshold and alt_11pm < required_altitude_11pm:
                    is_obstructed_at_11pm = True

            nightly_curves_cache[cache_key] = {
                "times_local": times_local,
                "altitudes": altitudes,
                "azimuths": azimuths,
                "transit_time": transit_time,
                "obs_duration_minutes": int(obs_duration.total_seconds() / 60)
                if obs_duration
                else 0,
                "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
                "alt_11pm": f"{alt_11pm:.2f}",
                "az_11pm": f"{az_11pm:.2f}",
                "is_obstructed_at_11pm": is_obstructed_at_11pm,
            }

        cached_night_data = nightly_curves_cache[cache_key]

        # Calculate current position and trend (logic remains similar)
        now_utc = datetime.now(pytz.utc)
        time_diffs = [
            abs((t - now_utc).total_seconds()) for t in cached_night_data["times_local"]
        ]
        current_index = np.argmin(time_diffs)
        current_alt = cached_night_data["altitudes"][current_index]
        current_az = cached_night_data["azimuths"][current_index]
        next_index = min(current_index + 1, len(cached_night_data["altitudes"]) - 1)
        next_alt = cached_night_data["altitudes"][next_index]
        trend = "–"
        if abs(next_alt - current_alt) > 0.01:
            trend = "↑" if next_alt > current_alt else "↓"

        # Check obstruction now
        is_obstructed_now = False
        if horizon_mask and len(horizon_mask) > 1:
            sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
            required_altitude_now = interpolate_horizon(
                current_az, sorted_mask, altitude_threshold
            )
            if (
                current_alt >= altitude_threshold
                and current_alt < required_altitude_now
            ):
                is_obstructed_now = True

        # Calculate Moon separation (logic remains similar)
        time_obj = Time(datetime.now(pytz.utc))
        location_for_moon = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        moon_coord = get_body("moon", time_obj, location_for_moon)
        obj_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        frame = AltAz(obstime=time_obj, location=location_for_moon)
        angular_sep = (
            obj_coord_sky.transform_to(frame)
            .separation(moon_coord.transform_to(frame))
            .deg
        )

        is_obstructed_at_11pm = cached_night_data.get("is_obstructed_at_11pm", False)

        # --- START OF NEW CALCULATIONS ---
        # 1. Calculate Best Month from RA
        # (RA 0h -> Oct, RA 2h -> Nov, ... RA 22h -> Sep)
        RA_to_Month_Opposition = [
            "Oct",
            "Nov",
            "Dec",
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
        ]
        best_month_idx = int(ra / 2) % 12  # Simple floor(ra/2)
        best_month_str = RA_to_Month_Opposition[best_month_idx]

        # 2. Calculate Max Culmination Altitude from Dec and Lat
        max_culmination_alt = 90.0 - abs(lat - dec)
        # --- END OF NEW CALCULATIONS ---

        # --- 6. Assemble JSON using the single object record ---

        # 1. Get all static data from the model's to_dict() method
        single_object_data = obj_record.to_dict()

        # 2. Add all dynamic (calculated) data to that dictionary
        single_object_data.update(
            {
                "Altitude Current": f"{current_alt:.2f}",
                "Azimuth Current": f"{current_az:.2f}",
                "Trend": trend,
                "Altitude 11PM": cached_night_data["alt_11pm"],
                "Azimuth 11PM": cached_night_data["az_11pm"],
                "Transit Time": cached_night_data["transit_time"],
                "Observable Duration (min)": cached_night_data["obs_duration_minutes"],
                "Max Altitude (°)": cached_night_data["max_altitude"],
                "Angular Separation (°)": round(angular_sep),
                "Time": current_datetime_local.strftime("%Y-%m-%d %H:%M:%S"),
                "is_obstructed_now": is_obstructed_now,
                "is_obstructed_at_11pm": is_obstructed_at_11pm,
                "best_month_ra": best_month_str,
                "max_culmination_alt": max_culmination_alt,
                "error": False,
            }
        )

        # 3. Ensure 'Project' key has a fallback for the UI
        single_object_data.setdefault("Project", "none")

        return jsonify(single_object_data)

    except Exception as e:
        print(f"ERROR in get_object_data for '{object_name}': {e}")
        traceback.print_exc()
        # Return a generic error structure
        return jsonify(
            {
                "Object": object_name,
                "Common Name": "Error processing request.",
                "error": True,
            }
        ), 500


@api_bp.route("/api/get_desktop_data_batch")
@permission_required("dashboard.view")
def get_desktop_data_batch():
    # --- Manual Auth Check for Guest Support ---
    if not (
        current_user.is_authenticated
        or SINGLE_USER_MODE
        or getattr(g, "is_guest", False)
    ):
        return jsonify({"error": "Unauthorized"}), 401
    """
    Batch processor for the desktop dashboard.
    Calculates data for 50 objects internally to prevent HTTP request flooding.
    """
    load_full_astro_context()

    # 1. Get Pagination Params
    try:
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 50))
    except ValueError:
        offset = 0
        limit = 50

    user = g.db_user
    if not user:
        return jsonify({"error": "User not found", "results": []}), 404

    # 2. Determine Location
    requested_loc_name = request.args.get("location")
    # Fallback logic: Request Param -> Session (g) -> Default
    if not requested_loc_name:
        requested_loc_name = g.selected_location

    db = get_db()
    try:
        location_obj = (
            db.query(Location)
            .options(selectinload(Location.horizon_points))
            .filter_by(user_id=user.id, name=requested_loc_name)
            .one_or_none()
        )

        if not location_obj:
            return jsonify({"error": "Location not found", "results": []}), 404

        # 3. Get Object Slice (Only Enabled Objects)
        # OPTIMIZATION: Fetch batch first to determine if there are more results
        batch_objects = (
            db.query(AstroObject)
            .filter_by(user_id=user.id, enabled=True)
            .order_by(AstroObject.object_name)
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Determine has_more flag based on whether we got a full page
        has_more = len(batch_objects) == limit

        # Only fetch total_count if offset is 0 (first page) to avoid double query on pagination
        # For subsequent pages, client can rely on has_more flag
        total_count = None
        if offset == 0:
            total_count = (
                db.query(func.count(AstroObject.id))
                .filter_by(user_id=user.id, enabled=True)
                .scalar()
                or 0
            )

        # 4. Prepare Calculation Variables
        results = []
        lat, lon, tz_name = location_obj.lat, location_obj.lon, location_obj.timezone
        horizon_mask = [
            [hp.az_deg, hp.alt_min_deg]
            for hp in sorted(location_obj.horizon_points, key=lambda p: p.az_deg)
        ]
        altitude_threshold = (
            location_obj.altitude_threshold
            if location_obj.altitude_threshold is not None
            else g.user_config.get("altitude_threshold", 20)
        )

        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.utc

            # --- SIMULATION MODE ---
        sim_date_str = request.args.get("sim_date")
        if sim_date_str:
            try:
                # Use current wall-clock time combined with simulated date
                sim_date = datetime.strptime(sim_date_str, "%Y-%m-%d").date()
                now_time = datetime.now(local_tz).time()
                current_datetime_local = local_tz.localize(
                    datetime.combine(sim_date, now_time)
                )
            except ValueError:
                current_datetime_local = datetime.now(local_tz)
        else:
            current_datetime_local = datetime.now(local_tz)

        # Determine "Observing Night" Date
        # If it's before noon, we associate this time with the previous night's session
        # to ensure the time array (which starts at noon) covers the current moment.
        if current_datetime_local.hour < 12:
            local_date = (current_datetime_local.date() - timedelta(days=1)).strftime(
                "%Y-%m-%d"
            )
        else:
            local_date = current_datetime_local.strftime("%Y-%m-%d")

        sampling_interval = (
            15 if SINGLE_USER_MODE else int(os.environ.get("CALCULATION_PRECISION", 15))
        )
        fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)

        # Moon / Ephem Prep
        time_obj_now = Time(current_datetime_local.astimezone(pytz.utc))
        loc_earth = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        moon_coord = get_body("moon", time_obj_now, loc_earth)
        frame_now = AltAz(obstime=time_obj_now, location=loc_earth)
        moon_in_frame = moon_coord.transform_to(frame_now)
        location_key = location_obj.name.lower().replace(" ", "_")

        # 5. Process Batch
        for obj in batch_objects:
            try:
                item = obj.to_dict()
                ra, dec = obj.ra_hours, obj.dec_deg

                if ra is None or dec is None:
                    item.update({"error": True, "Common Name": "Error: Missing RA/DEC"})
                    results.append(item)
                    continue

                    # --- GEOMETRIC PRE-FILTER (Live Request) ---
                calc_invisible = g.user_config.get("calc_invisible", False)

                if not calc_invisible:
                    max_culm_geo = 90.0 - abs(lat - dec)
                    if max_culm_geo < altitude_threshold:
                        # Skip heavy math, return "greyed out" state immediately
                        item.update(
                            {
                                "Altitude Current": "N/A",
                                "Azimuth Current": "N/A",
                                "Trend": "–",
                                "Altitude 11PM": "N/A",
                                "Azimuth 11PM": "N/A",
                                "Transit Time": "N/A",
                                "Observable Duration (min)": 0,
                                "Max Altitude (°)": round(max_culm_geo, 1),
                                "Angular Separation (°)": "N/A",
                                "is_obstructed_now": False,
                                "is_geometrically_impossible": True,
                                "best_month_ra": [
                                    "Oct",
                                    "Nov",
                                    "Dec",
                                    "Jan",
                                    "Feb",
                                    "Mar",
                                    "Apr",
                                    "May",
                                    "Jun",
                                    "Jul",
                                    "Aug",
                                    "Sep",
                                ][int(ra / 2) % 12],
                                "max_culmination_alt": round(max_culm_geo, 1),
                                "error": False,
                            }
                        )
                        results.append(item)
                        continue

                # Calculate / Cache
                cache_key = f"{obj.object_name.lower()}_{local_date}_{location_key}"

                cached = None
                if cache_key in nightly_curves_cache:
                    cached = nightly_curves_cache[cache_key]
                else:
                    obs_duration, max_alt, _, _ = (
                        calculate_observable_duration_vectorized(
                            ra,
                            dec,
                            lat,
                            lon,
                            local_date,
                            tz_name,
                            altitude_threshold,
                            sampling_interval,
                            horizon_mask,
                        )
                    )
                    transit = calculate_transit_time(
                        ra, dec, lat, lon, tz_name, local_date
                    )
                    alt_11, az_11 = ra_dec_to_alt_az(
                        ra, dec, lat, lon, fixed_time_utc_str
                    )

                    is_obst_11 = False
                    if horizon_mask:
                        req_alt = interpolate_horizon(
                            az_11,
                            sorted(horizon_mask, key=lambda p: p[0]),
                            altitude_threshold,
                        )
                        if alt_11 >= altitude_threshold and alt_11 < req_alt:
                            is_obst_11 = True

                    times_local, times_utc = get_common_time_arrays(
                        tz_name, local_date, sampling_interval
                    )
                    sky_c = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    aa_frame = AltAz(obstime=times_utc, location=loc_earth)
                    alts = sky_c.transform_to(aa_frame).alt.deg
                    azs = sky_c.transform_to(aa_frame).az.deg

                    cached = {
                        "times_local": times_local,
                        "altitudes": alts,
                        "azimuths": azs,
                        "transit_time": transit,
                        "obs_duration_minutes": int(obs_duration.total_seconds() / 60)
                        if obs_duration
                        else 0,
                        "max_altitude": round(max_alt, 1)
                        if max_alt is not None
                        else "N/A",
                        "alt_11pm": f"{alt_11:.2f}",
                        "az_11pm": f"{az_11:.2f}",
                        "is_obstructed_at_11pm": is_obst_11,
                    }
                    nightly_curves_cache[cache_key] = cached

                # Current Position (Fast Interpolation)
                # Use the effective simulation time converted to UTC
                now_utc = current_datetime_local.astimezone(pytz.utc)
                idx = np.argmin(
                    [abs((t - now_utc).total_seconds()) for t in cached["times_local"]]
                )
                cur_alt = cached["altitudes"][idx]
                cur_az = cached["azimuths"][idx]

                next_idx = min(idx + 1, len(cached["altitudes"]) - 1)
                trend = "–"
                if abs(cached["altitudes"][next_idx] - cur_alt) > 0.01:
                    trend = "↑" if cached["altitudes"][next_idx] > cur_alt else "↓"

                is_obst_now = False
                if horizon_mask:
                    req = interpolate_horizon(
                        cur_az,
                        sorted(horizon_mask, key=lambda p: p[0]),
                        altitude_threshold,
                    )
                    if cur_alt >= altitude_threshold and cur_alt < req:
                        is_obst_now = True

                sep = "N/A"
                try:
                    sky_c = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    sep = round(
                        sky_c.transform_to(frame_now).separation(moon_in_frame).deg
                    )
                except:
                    pass

                best_m = [
                    "Oct",
                    "Nov",
                    "Dec",
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                ][int(ra / 2) % 12]
                max_culm = 90.0 - abs(lat - dec)

                item.update(
                    {
                        "Altitude Current": f"{cur_alt:.2f}",
                        "Azimuth Current": f"{cur_az:.2f}",
                        "Trend": trend,
                        "Altitude 11PM": cached["alt_11pm"],
                        "Azimuth 11PM": cached["az_11pm"],
                        "Transit Time": cached["transit_time"],
                        "Observable Duration (min)": cached["obs_duration_minutes"],
                        "Max Altitude (°)": cached["max_altitude"],
                        "Angular Separation (°)": sep,
                        "is_obstructed_now": is_obst_now,
                        "is_obstructed_at_11pm": cached["is_obstructed_at_11pm"],
                        "best_month_ra": best_m,
                        "max_culmination_alt": max_culm,
                        "error": False,
                    }
                )
                results.append(item)

            except Exception as e:
                print(f"Batch Error {obj.object_name}: {e}")
                results.append(
                    {
                        "Object": obj.object_name,
                        "Common Name": "Error: Calc failed",
                        "error": True,
                    }
                )

        response_data = {
            "results": results,
            "total": total_count,
            "has_more": has_more,
            "offset": offset,
            "limit": limit,
        }

        return jsonify(response_data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@core_bp.route("/")
@permission_required("dashboard.view")
def index():
    load_full_astro_context()
    if not (
        current_user.is_authenticated
        or SINGLE_USER_MODE
        or getattr(g, "is_guest", False)
    ):
        return redirect(url_for("core.login"))

    username = (
        "default"
        if SINGLE_USER_MODE
        else current_user.username
        if current_user.is_authenticated
        else "guest_user"
    )
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one_or_none()
    if not user:
        # Handle case where user is authenticated but not yet in app.db
        return render_template("index.html", journal_sessions=[])

    sessions = (
        db.query(JournalSession)
        .filter_by(user_id=user.id)
        .order_by(JournalSession.date_utc.desc())
        .all()
    )
    all_projects = db.query(Project).filter_by(user_id=user.id).all()
    project_map = {p.id: p.name for p in all_projects}
    objects_from_db = db.query(AstroObject).filter_by(user_id=user.id).all()
    object_names_lookup = {o.object_name: o.common_name for o in objects_from_db}

    # --- THIS IS THE CRITICAL FIX ---
    # Convert the list of objects into a list of JSON-safe dictionaries
    sessions_for_template = []
    for session in sessions:
        # Create a dictionary from the database object's columns
        session_dict = {
            c.name: getattr(session, c.name) for c in session.__table__.columns
        }

        # Convert the date object to an ISO string for JavaScript
        if isinstance(session_dict.get("date_utc"), (datetime, date)):
            session_dict["date_utc"] = session_dict["date_utc"].isoformat()

        # Add the common name for convenience in the template
        session_dict["target_common_name"] = object_names_lookup.get(
            session.object_name, session.object_name
        )

        if session.project_id:
            session_dict["project_name"] = project_map.get(
                session.project_id, "Unknown Project"
            )
        else:
            session_dict["project_name"] = "-"  # Or "Standalone"

        sessions_for_template.append(session_dict)
    # --- END OF FIX ---

    local_tz = pytz.timezone(g.tz_name or "UTC")
    now_local = datetime.now(local_tz)

    # --- START FIX: Determine "Observing Night" Date ---
    # If it's before noon, we're still on the "night of" the previous day.
    if now_local.hour < 12:
        observing_date_for_calcs = now_local.date() - timedelta(days=1)
    else:
        observing_date_for_calcs = now_local.date()
    # --- END FIX ---

    # Get hiding preference (safe default False)
    hide_invisible_pref = g.user_config.get("hide_invisible", True)

    record_event("dashboard_load")
    return render_template(
        "index.html",
        journal_sessions=sessions_for_template,
        selected_day=observing_date_for_calcs.day,
        selected_month=observing_date_for_calcs.month,
        selected_year=observing_date_for_calcs.year,
        hide_invisible=hide_invisible_pref,
    )


# =============================================================================
# MOBILE COMPANION ROUTES
# =============================================================================
@mobile_bp.route("/m/up_now")
@login_required
@permission_required("mobile.access")
def mobile_up_now():
    """Renders the mobile 'Up Now' dashboard skeleton (data fetched via API)."""
    load_full_astro_context()
    # Render template immediately with empty data; JS will fetch it.
    return render_template("mobile_up_now.html")


@api_bp.route("/api/mobile_data_chunk")
@login_required
@permission_required("mobile.access")
def api_mobile_data_chunk():
    """Fetches a specific slice of object data for the mobile progress bar."""
    load_full_astro_context()

    offset = int(request.args.get("offset", 0))
    limit = int(request.args.get("limit", 10))

    user = g.db_user
    location_name = g.selected_location
    user_prefs = g.user_config or {}

    results = []
    total_count = 0

    if user and location_name:
        db = get_db()
        # 1. Get Location
        location_db_obj = (
            db.query(Location)
            .options(selectinload(Location.horizon_points))
            .filter_by(user_id=user.id, name=location_name)
            .one_or_none()
        )

        if location_db_obj:
            # 2. Get All Objects (to count and slice)
            all_objects_query = (
                db.query(AstroObject)
                .filter_by(user_id=user.id)
                .order_by(AstroObject.id)
            )
            total_count = all_objects_query.count()

            # 3. Get Slice
            sliced_objects = all_objects_query.offset(offset).limit(limit).all()

            # 4. Calculate Data for this slice
            results = get_all_mobile_up_now_data(
                user,
                location_db_obj,
                user_prefs,
                sliced_objects,
                db,  # Pass DB session
            )

    return jsonify(
        {"data": results, "total": total_count, "offset": offset, "limit": limit}
    )


@mobile_bp.route("/m/location")
@login_required
@permission_required("mobile.access")
def mobile_location():
    """Renders the mobile location selector."""
    load_full_astro_context()  # <-- ADD THIS LINE
    return render_template(
        "mobile_location.html",
        locations=g.active_locations,
        selected_location_name=g.selected_location,
    )


@mobile_bp.route("/m")
@mobile_bp.route("/m/add_object")
@login_required
@permission_required("mobile.access")
def mobile_add_object():
    """Renders the mobile 'Add Object' page."""
    load_full_astro_context()  # <-- ADD THIS LINE
    record_event("mobile_view_accessed")
    return render_template("mobile_add_object.html")


@mobile_bp.route("/m/outlook")
@login_required
@permission_required("mobile.access")
def mobile_outlook():
    """Renders the mobile 'Outlook' page."""
    load_full_astro_context()  # <-- ADD THIS LINE
    return render_template("mobile_outlook.html")


@mobile_bp.route("/m/edit_notes/<path:object_name>")
@login_required
@permission_required("mobile.access")
def mobile_edit_notes(object_name):
    """Renders the mobile 'Edit Notes' page for a specific object."""
    load_full_astro_context()  # Ensures g.db_user is loaded

    # Get the current user
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one_or_none()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("mobile.mobile_up_now"))

    # Get the specific object
    obj_record = (
        db.query(AstroObject)
        .filter_by(user_id=user.id, object_name=object_name)
        .one_or_none()
    )

    if not obj_record:
        flash(f"Object '{object_name}' not found.", "error")
        return redirect(url_for("mobile.mobile_up_now"))

    # Handle Trix/HTML conversion for old plain text notes
    raw_project_notes = obj_record.project_name or ""
    if not raw_project_notes.strip().startswith(
        (
            "<p>",
            "<div>",
            "<ul>",
            "<ol>",
            "<figure>",
            "<blockquote>",
            "<h1>",
            "<h2>",
            "<h3>",
            "<h4>",
            "<h5>",
            "<h6>",
        )
    ):
        escaped_text = bleach.clean(raw_project_notes, tags=[], strip=True)
        project_notes_for_editor = escaped_text.replace("\n", "<br>")
    else:
        project_notes_for_editor = raw_project_notes

    return render_template(
        "mobile_edit_notes.html",
        object_name=obj_record.object_name,
        common_name=obj_record.common_name,
        project_notes_html=project_notes_for_editor,
        is_project_active=obj_record.active_project,
    )


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
        local_date = (current_datetime_local - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        local_date = current_datetime_local.strftime("%Y-%m-%d")

    # --- 2. Get Calculation Settings ---
    altitude_threshold = user_prefs_dict.get("altitude_threshold", 20)
    if location.altitude_threshold is not None:
        altitude_threshold = location.altitude_threshold

    sampling_interval = 15  # Default
    if SINGLE_USER_MODE:
        sampling_interval = user_prefs_dict.get("sampling_interval_minutes") or 15
    else:
        sampling_interval = int(os.environ.get("CALCULATION_PRECISION", 15))

    horizon_mask = [
        [hp.az_deg, hp.alt_min_deg]
        for hp in sorted(location.horizon_points, key=lambda p: p.az_deg)
    ]
    location_name_key = location.name.lower().replace(" ", "_")

    # --- 3. Pre-calculate Moon Position ---
    try:
        time_obj_now = Time(datetime.now(pytz.utc))
        location_for_moon = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        moon_coord = get_body("moon", time_obj_now, location_for_moon)
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
                times_local, times_utc = get_common_time_arrays(
                    tz_name, local_date, sampling_interval
                )
                location_ephem = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
                sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                altaz_frame = AltAz(obstime=times_utc, location=location_ephem)
                altitudes = sky_coord.transform_to(altaz_frame).alt.deg
                azimuths = sky_coord.transform_to(altaz_frame).az.deg
                transit_time = calculate_transit_time(
                    ra, dec, lat, lon, tz_name, local_date
                )
                obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
                    ra,
                    dec,
                    lat,
                    lon,
                    local_date,
                    tz_name,
                    altitude_threshold,
                    sampling_interval,
                    horizon_mask=horizon_mask,
                )
                fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)
                alt_11pm, az_11pm = ra_dec_to_alt_az(
                    ra, dec, lat, lon, fixed_time_utc_str
                )
                is_obstructed_at_11pm = False
                if horizon_mask and len(horizon_mask) > 1:
                    sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
                    required_altitude_11pm = interpolate_horizon(
                        az_11pm, sorted_mask, altitude_threshold
                    )
                    if (
                        alt_11pm >= altitude_threshold
                        and alt_11pm < required_altitude_11pm
                    ):
                        is_obstructed_at_11pm = True

                nightly_curves_cache[cache_key] = {
                    "times_local": times_local,
                    "altitudes": altitudes,
                    "azimuths": azimuths,
                    "transit_time": transit_time,
                    "obs_duration_minutes": int(obs_duration.total_seconds() / 60)
                    if obs_duration
                    else 0,
                    "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
                    "alt_11pm": f"{alt_11pm:.2f}",
                    "az_11pm": f"{az_11pm:.2f}",
                    "is_obstructed_at_11pm": is_obstructed_at_11pm,
                }

            cached_night_data = nightly_curves_cache[cache_key]

            # --- 6. Calculate Current Position ---
            now_utc = datetime.now(pytz.utc)
            time_diffs = [
                abs((t - now_utc).total_seconds())
                for t in cached_night_data["times_local"]
            ]
            current_index = np.argmin(time_diffs)
            current_alt = cached_night_data["altitudes"][current_index]
            current_az = cached_night_data["azimuths"][current_index]
            next_index = min(current_index + 1, len(cached_night_data["altitudes"]) - 1)
            next_alt = cached_night_data["altitudes"][next_index]
            trend = "–"
            if abs(next_alt - current_alt) > 0.01:
                trend = "↑" if next_alt > current_alt else "↓"

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
            all_objects_data.append(
                {
                    # Static data from the object record
                    "Object": obj_record.object_name,
                    "Common Name": obj_record.common_name or obj_record.object_name,
                    "ActiveProject": obj_record.active_project,
                    "has_framing": obj_record.object_name in framed_objects,
                    # Calculated data
                    "Altitude Current": f"{current_alt:.2f}",
                    "Azimuth Current": f"{current_az:.2f}",
                    "Trend": trend,
                    "Observable Duration (min)": cached_night_data[
                        "obs_duration_minutes"
                    ],
                    "Max Altitude (°)": cached_night_data["max_altitude"],
                    "Angular Separation (°)": angular_sep,
                }
            )
        except Exception as e:
            print(
                f"[Mobile Helper] Failed to process object {obj_record.object_name}: {e}"
            )
            continue  # Skip this object

    return all_objects_data


@core_bp.route("/sun_events")
@permission_required("dashboard.sun_events")
def sun_events():
    """
    API endpoint to calculate and return sun event times (dusk, dawn, etc.)
    and the current moon phase for a specific location. Uses the location
    specified in the 'location' query parameter or falls back to the
    user's default location.
    """
    load_full_astro_context()
    # --- Determine location to use ---
    requested_location_name = request.args.get("location")
    lat, lon, tz_name = g.lat, g.lon, g.tz_name  # Defaults from flask global 'g'

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
        lat = loc_cfg.get(
            "lat", g.lat
        )  # Use default g value if key missing in specific config
        lon = loc_cfg.get("lon", g.lon)
        tz_name = loc_cfg.get(
            "timezone", g.tz_name or "UTC"
        )  # Use g.tz_name as fallback if timezone missing
        # print(f"[API Sun Events] Using default location: {g.selected_location}") # Optional debug print
    else:
        # print(f"[API Sun Events] Warning: No location specified or default found.") # Optional debug print
        # lat, lon, tz_name remain the initial g values (which might be None)
        pass  # Proceed, error handled below

    # If after checks, we don't have valid coordinates, return an error immediately
    if lat is None or lon is None:
        # print("[API Sun Events] Error: Invalid coordinates (lat or lon is None).") # Optional debug print
        return jsonify(
            {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M"),
                "phase": 0,  # Default phase
                "error": "No location set or location has invalid coordinates.",
            }
        ), 400  # Bad request status
    # --- END Location Determination ---

    # --- Use the determined (valid) lat, lon, tz_name variables below ---
    try:
        local_tz = pytz.timezone(tz_name)  # Use determined tz_name
    except pytz.UnknownTimeZoneError:
        # Handle invalid timezone string
        # print(f"[API Sun Events] Error: Invalid timezone '{tz_name}'. Falling back to UTC.") # Optional debug print
        tz_name = "UTC"
        local_tz = pytz.utc

        # --- SIMULATION MODE ---
    sim_date_str = request.args.get("sim_date")
    if sim_date_str:
        try:
            sim_date = datetime.strptime(sim_date_str, "%Y-%m-%d").date()
            now_time = datetime.now(local_tz).time()
            now_local = local_tz.localize(datetime.combine(sim_date, now_time))
        except ValueError:
            now_local = datetime.now(local_tz)
    else:
        now_local = datetime.now(local_tz)

    local_date = now_local.strftime("%Y-%m-%d")

    # Calculate sun events using determined variables
    events = calculate_sun_events_cached(local_date, tz_name, lat, lon)

    # Calculate moon phase using determined variables
    try:
        moon = ephem.Moon()
        observer = ephem.Observer()
        observer.lat = str(lat)  # Use determined lat (ephem needs string)
        observer.lon = str(lon)  # Use determined lon (ephem needs string)
        observer.date = now_local.astimezone(
            pytz.utc
        )  # Use current time converted to UTC
        moon.compute(observer)
        moon_phase = round(moon.phase, 1)
    except Exception as e:
        # Handle potential errors during ephem calculation
        print(f"[API Sun Events] Error calculating moon phase: {e}")  # Log error
        moon_phase = "N/A"  # Indicate error in response

    # Add all data to the response JSON
    events["date"] = local_date
    events["time"] = now_local.strftime("%H:%M")
    events["phase"] = moon_phase  # Use calculated (or N/A) phase
    # Add error field if moon phase calculation failed
    if moon_phase == "N/A":
        events["error"] = events.get("error", "") + " Moon phase calculation failed."

    return jsonify(events)


@api_bp.route("/telemetry/ping", methods=["POST"])
@permission_required("settings.edit")
def telemetry_ping():
    # Respect opt-out as usual
    try:
        username = (
            "default"
            if SINGLE_USER_MODE
            else (
                current_user.username
                if getattr(current_user, "is_authenticated", False)
                else "guest_user"
            )
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

    tcfg = cfg.get("telemetry") or {}
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
        state_dir = Path(os.environ.get("NOVA_STATE_DIR", CACHE_DIR))
        if telemetry_should_send(state_dir):
            send_telemetry_async(cfg, browser_user_agent=ua_final, force=False)
        # else: silently skip; scheduler or next allowed window will send
    except Exception:
        pass

    return jsonify({"status": "ok"}), 200


@core_bp.route("/config_form", methods=["GET", "POST"])
@login_required
@permission_required("settings.edit")
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
            return redirect(url_for("core.index"))

        if request.method == "POST":
            # --- General Settings Tab ---
            if "submit_general" in request.form:
                prefs = db.query(UiPref).filter_by(user_id=app_db_user.id).first()
                if not prefs:
                    prefs = UiPref(user_id=app_db_user.id, json_blob="{}")
                    db.add(prefs)
                try:
                    settings = json.loads(prefs.json_blob or "{}")
                except json.JSONDecodeError:
                    settings = {}
                settings["altitude_threshold"] = int(
                    request.form.get("altitude_threshold", 20)
                )
                settings["default_location"] = request.form.get(
                    "default_location", settings.get("default_location")
                )
                # Settings available to ALL users
                settings["calc_invisible"] = bool(request.form.get("calc_invisible"))
                settings["hide_invisible"] = bool(request.form.get("hide_invisible"))
                # Theme preference: 'follow_system', 'always_light', 'always_dark'
                theme_value = request.form.get("theme_preference", "follow_system")
                if theme_value in ("follow_system", "always_light", "always_dark"):
                    settings["theme_preference"] = theme_value

                if SINGLE_USER_MODE:
                    settings["sampling_interval_minutes"] = int(
                        request.form.get("sampling_interval", 15)
                    )
                    settings.setdefault("telemetry", {})["enabled"] = bool(
                        request.form.get("telemetry_enabled")
                    )

                imaging_criteria = settings.setdefault("imaging_criteria", {})
                imaging_criteria["min_observable_minutes"] = int(
                    request.form.get("min_observable_minutes", 60)
                )
                imaging_criteria["min_max_altitude"] = int(
                    request.form.get("min_max_altitude", 30)
                )
                imaging_criteria["max_moon_illumination"] = int(
                    request.form.get("max_moon_illumination", 20)
                )
                imaging_criteria["min_angular_separation"] = int(
                    request.form.get("min_angular_separation", 30)
                )
                imaging_criteria["search_horizon_months"] = int(
                    request.form.get("search_horizon_months", 6)
                )
                prefs.json_blob = json.dumps(settings)
                message = "General settings updated."

            # --- Add New Location ---
            elif "submit_new_location" in request.form:
                new_name = request.form.get("new_location").strip()
                new_tz = request.form.get("new_timezone")  # Get the timezone

                existing = (
                    db.query(Location)
                    .filter_by(user_id=app_db_user.id, name=new_name)
                    .first()
                )
                if existing:
                    error = f"A location named '{new_name}' already exists."
                elif not all(
                    [
                        new_name,
                        request.form.get("new_lat"),
                        request.form.get("new_lon"),
                        new_tz,
                    ]
                ):
                    error = "Name, Latitude, Longitude, and Timezone are required."

                elif new_tz not in pytz.all_timezones:
                    error = f"Invalid timezone: '{new_tz}'. Please select a valid option from the list."

                else:
                    new_loc = Location(
                        user_id=app_db_user.id,
                        name=new_name,
                        lat=float(request.form.get("new_lat")),
                        lon=float(request.form.get("new_lon")),
                        timezone=request.form.get("new_timezone"),
                        active=request.form.get("new_active") == "on",
                        comments=request.form.get("new_comments", "").strip()[:500],
                    )
                    db.add(new_loc)
                    db.flush()
                    mask_str = request.form.get("new_horizon_mask", "").strip()
                    if mask_str:
                        try:
                            mask_data = yaml.safe_load(mask_str)
                            if isinstance(mask_data, list):
                                for point in mask_data:
                                    db.add(
                                        HorizonPoint(
                                            location_id=new_loc.id,
                                            az_deg=float(point[0]),
                                            alt_min_deg=float(point[1]),
                                        )
                                    )
                        except (yaml.YAMLError, ValueError, TypeError):
                            flash(
                                "Warning: Horizon Mask was invalid and was ignored.",
                                "warning",
                            )
                    message = "New location added."

            # --- Update Existing Locations ---
            elif "submit_locations" in request.form:
                locs_to_update = (
                    db.query(Location).filter_by(user_id=app_db_user.id).all()
                )
                total_locs = len(locs_to_update)

                # Guard: Count locations marked for deletion and check active status after update
                locs_marked_for_deletion = 0
                active_locations_after_update = 0
                for loc in locs_to_update:
                    if request.form.get(f"delete_loc_{loc.name}") == "on":
                        locs_marked_for_deletion += 1
                    else:
                        # This location survives - check if it will be active
                        will_be_active = request.form.get(f"active_{loc.name}") == "on"
                        if will_be_active:
                            active_locations_after_update += 1

                # Prevent deleting the last location
                if locs_marked_for_deletion >= total_locs:
                    flash(
                        "Cannot delete your last location. You must have at least one location configured.",
                        "error",
                    )
                    return redirect(url_for("core.config_form"))

                # Prevent having zero active locations
                if active_locations_after_update == 0:
                    flash(
                        "Cannot deactivate your only active location. You must keep at least one active location.",
                        "error",
                    )
                    return redirect(url_for("core.config_form"))

                # Safe to proceed with deletions and updates
                for loc in locs_to_update:
                    if request.form.get(f"delete_loc_{loc.name}") == "on":
                        db.delete(loc)
                        continue

                    tz_name_from_form = request.form.get(f"timezone_{loc.name}")
                    if tz_name_from_form not in pytz.all_timezones:
                        error = f"Invalid timezone for {loc.name}: '{tz_name_from_form}'. Please select a valid option."
                        break  # Stop processing immediately on the first error

                    loc.lat = float(request.form.get(f"lat_{loc.name}"))
                    loc.lon = float(request.form.get(f"lon_{loc.name}"))
                    loc.timezone = request.form.get(f"timezone_{loc.name}")
                    loc.active = request.form.get(f"active_{loc.name}") == "on"
                    loc.comments = request.form.get(f"comments_{loc.name}", "").strip()[
                        :500
                    ]

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
                                        HorizonPoint(
                                            location_id=loc.id,
                                            az_deg=float(point[0]),
                                            alt_min_deg=float(point[1]),
                                        )
                                    )
                        except Exception:
                            flash(
                                f"Warning: Horizon Mask for '{loc.name}' was invalid and ignored.",
                                "warning",
                            )

                    # 3. Assign the new list directly to the relationship.
                    # SQLAlchemy will now compare the old list with the new one.
                    # It will automatically delete any points not in the new list (due to 'delete-orphan')
                    # and add any new points. This avoids the bulk-delete conflict.
                    loc.horizon_points = new_horizon_points
                    # --- END FIX ---

                message = "Locations"

            # --- Update Existing Objects ---
            elif "submit_objects" in request.form:
                # 1. Fetch all objects for the current user
                objs_to_update = (
                    db.query(AstroObject).filter_by(user_id=app_db_user.id).all()
                )

                # 2. Loop through each object and process its form data
                for obj in objs_to_update:
                    # Handle deletion first
                    if request.form.get(f"delete_{obj.object_name}") == "on":
                        db.delete(obj)
                        continue

                    # Update standard fields
                    obj.common_name = request.form.get(f"name_{obj.object_name}")
                    obj.ra_hours = float(request.form.get(f"ra_{obj.object_name}"))
                    obj.dec_deg = float(request.form.get(f"dec_{obj.object_name}"))
                    obj.constellation = request.form.get(
                        f"constellation_{obj.object_name}"
                    )
                    obj.project_name = request.form.get(
                        f"project_{obj.object_name}"
                    )  # Private notes
                    obj.type = request.form.get(f"type_{obj.object_name}")
                    obj.magnitude = request.form.get(f"magnitude_{obj.object_name}")
                    obj.size = request.form.get(f"size_{obj.object_name}")
                    obj.sb = request.form.get(f"sb_{obj.object_name}")

                    # --- START NEW LOGIC ---
                    # Update the 'ActiveProject' status based on the checkbox being 'on'
                    obj.active_project = (
                        request.form.get(f"active_project_{obj.object_name}") == "on"
                    )
                    # --- END NEW LOGIC ---

                    if not obj.original_user_id:
                        obj.is_shared = (
                            request.form.get(f"is_shared_{obj.object_name}") == "on"
                        )
                        obj.shared_notes = request.form.get(
                            f"shared_notes_{obj.object_name}"
                        )

                message = "Objects updated."

            if not error:
                db.commit()
                flash(f"{message or 'Configuration'} updated successfully.", "success")
                return redirect(url_for("core.config_form"))
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
        if config_for_template.get("telemetry") is None:
            config_for_template["telemetry"] = {}
        if config_for_template.get("imaging_criteria") is None:
            config_for_template["imaging_criteria"] = {}
        # --- END FIX ---

        locations_for_template = {}
        db_locations = (
            db.query(Location)
            .options(selectinload(Location.horizon_points))
            .filter_by(user_id=app_db_user.id)
            .order_by(Location.name)
            .all()
        )
        for loc in db_locations:
            locations_for_template[loc.name] = {
                "lat": loc.lat,
                "lon": loc.lon,
                "timezone": loc.timezone,
                "active": loc.active,
                "comments": loc.comments,
                "horizon_mask": [
                    [hp.az_deg, hp.alt_min_deg]
                    for hp in sorted(loc.horizon_points, key=lambda p: p.az_deg)
                ],
            }
            if loc.is_default:
                config_for_template["default_location"] = loc.name

        db_objects = (
            db.query(AstroObject)
            .filter_by(user_id=app_db_user.id)
            .order_by(AstroObject.object_name)
            .all()
        )
        config_for_template["objects"] = []
        for o in db_objects:
            # --- START: Rich Text Upgrade for Private Notes ---
            raw_private_notes = o.project_name or ""
            if not raw_private_notes.strip().startswith(
                (
                    "<p>",
                    "<div>",
                    "<ul>",
                    "<ol>",
                    "<figure>",
                    "<blockquote>",
                    "<h1>",
                    "<h2>",
                    "<h3>",
                    "<h4>",
                    "<h5>",
                    "<h6>",
                )
            ):
                escaped_text = bleach.clean(raw_private_notes, tags=[], strip=True)
                private_notes_html = escaped_text.replace("\n", "<br>")
            else:
                private_notes_html = raw_private_notes
            # --- END: Rich Text Upgrade ---

            # --- START: Rich Text Upgrade for SHARED Notes ---
            raw_shared_notes = o.shared_notes or ""
            if not raw_shared_notes.strip().startswith(
                ("<p>", "<div>", "<ul>", "<ol>")
            ):
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
            config_for_template["objects"].append(obj_data_dict)

        catalog_packs = discover_catalog_packs()

        # --- API Keys (multi-user only) ---
        new_api_key = session.pop("new_api_key", None)
        if not SINGLE_USER_MODE:
            user_api_keys = (
                db.query(ApiKey)
                .filter(ApiKey.user_id == app_db_user.id)
                .order_by(ApiKey.created_at.desc())
                .all()
            )
        else:
            user_api_keys = []

        return render_template(
            "config_form.html",
            config=config_for_template,
            locations=locations_for_template,
            all_timezones=pytz.all_timezones,
            catalog_packs=catalog_packs,
            api_keys=user_api_keys,
            new_api_key=new_api_key,
        )

    except Exception as e:
        db.rollback()
        flash(_("A database error occurred: %(error)s", error=e), "error")
        traceback.print_exc()
        return redirect(url_for("core.index"))


@core_bp.route("/update_project", methods=["POST"])
@login_required
@permission_required("projects.edit")
def update_project():
    data = request.get_json()
    object_name = data.get("object")

    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        obj_to_update = (
            db.query(AstroObject)
            .filter_by(user_id=user.id, object_name=object_name)
            .one_or_none()
        )

        if obj_to_update:
            did_change_active_status = False

            # --- START OF FIX ---
            # 1. Update notes if the 'project' key was sent
            if "project" in data:
                new_project_notes_html = data.get("project")
                obj_to_update.project_name = new_project_notes_html

            # 2. RESTORED: Update Active Status if 'is_active' key was sent
            # This is required for the 'Save Project' button in the graph dashboard
            if "is_active" in data:
                new_active_status = bool(data.get("is_active"))
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
            return jsonify({"status": "error", "error": _("Object not found.")}), 404

    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500


@core_bp.route("/update_project_active", methods=["POST"])
@login_required
@permission_required("projects.edit")
def update_project_active():
    data = request.get_json()
    object_name = data.get("object")
    is_active = data.get("active")
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        obj_to_update = (
            db.query(AstroObject)
            .filter_by(user_id=user.id, object_name=object_name)
            .one_or_none()
        )

        if obj_to_update:
            obj_to_update.active_project = bool(is_active)
            db.commit()
            trigger_outlook_update_for_user(username)
            return jsonify(
                {"status": "success", "active": obj_to_update.active_project}
            )
        else:
            return jsonify({"status": "error", "error": _("Object not found.")}), 404
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500


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
# Blog Image Helpers
# =============================================================================
from PIL import Image as _PILImage

try:
    _BLOG_LANCZOS = _PILImage.Resampling.LANCZOS
except AttributeError:
    _BLOG_LANCZOS = _PILImage.LANCZOS

BLOG_THUMB_MAX = (400, 400)
BLOG_THUMB_QUAL = 85
BLOG_PER_PAGE = 10
BLOG_COMMENT_MAX_LEN = 2000


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
    if not file or file.filename == "":
        return None
    if not allowed_file(file.filename):
        return None

    ext = file.filename.rsplit(".", 1)[1].lower()
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


# =============================================================================
# Blog Routes
# =============================================================================
from sqlalchemy.orm import joinedload, selectinload


@blog_bp.route("/")
def blog_list():
    """Public list of all blog posts, paginated."""
    db = get_db()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    total = db.query(BlogPost).count()
    posts = (
        db.query(BlogPost)
        .options(joinedload(BlogPost.user), selectinload(BlogPost.images))
        .order_by(BlogPost.created_at.desc())
        .offset((page - 1) * BLOG_PER_PAGE)
        .limit(BLOG_PER_PAGE)
        .all()
    )
    total_pages = (total + BLOG_PER_PAGE - 1) // BLOG_PER_PAGE if total > 0 else 1

    return render_template(
        "blog_list.html",
        posts=posts,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@blog_bp.route("/<int:post_id>")
def blog_detail(post_id):
    """Public view of a single blog post with comments."""
    db = get_db()
    post = (
        db.query(BlogPost)
        .options(
            joinedload(BlogPost.user),
            selectinload(BlogPost.images),
            selectinload(BlogPost.comments).joinedload(BlogComment.user),
        )
        .filter(BlogPost.id == post_id)
        .first()
    )
    if not post:
        abort(404)
    return render_template("blog_detail.html", post=post)


@blog_bp.route("/create", methods=["GET", "POST"])
@login_required
@permission_required("blog.create")
def blog_create():
    """Create a new blog post."""
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()

        if not title:
            flash("Title is required.", "error")
            return redirect(request.url)
        if not content:
            flash("Content is required.", "error")
            return redirect(request.url)

        user_id = 1 if SINGLE_USER_MODE else current_user.id

        db = get_db()
        post = BlogPost(title=title, content=content, user_id=user_id)
        db.add(post)
        db.flush()  # get post.id

        files = request.files.getlist("images")
        captions = request.form.getlist("captions")
        for order, file in enumerate(files):
            img = _save_blog_image(
                file,
                user_id,
                post.id,
                order,
                captions[order] if order < len(captions) else "",
            )
            if img:
                db.add(img)

        db.commit()
        flash("Post published!", "success")
        return redirect(url_for("blog.blog_detail", post_id=post.id))

    return render_template("blog_form.html", post=None, is_edit=False)


@blog_bp.route("/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("blog.edit")
def blog_edit(post_id):
    """Edit an existing blog post (owner or admin only)."""
    db = get_db()
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        abort(404)

    user_id = 1 if SINGLE_USER_MODE else current_user.id
    if not SINGLE_USER_MODE and post.user_id != user_id and not current_user.is_admin:
        abort(403)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()

        if not title or not content:
            flash("Title and content are required.", "error")
            return redirect(request.url)

        post.title = title
        post.content = content
        post.updated_at = datetime.utcnow()

        files = request.files.getlist("images")
        captions = request.form.getlist("new_captions")
        # Determine next display_order
        existing_max = max((img.display_order for img in post.images), default=-1)
        for i, file in enumerate(files):
            img = _save_blog_image(
                file,
                post.user_id,
                post.id,
                existing_max + 1 + i,
                captions[i] if i < len(captions) else "",
            )
            if img:
                db.add(img)

        db.commit()
        flash("Post updated!", "success")
        return redirect(url_for("blog.blog_detail", post_id=post.id))

    return render_template("blog_form.html", post=post, is_edit=True)


@blog_bp.route("/<int:post_id>/delete", methods=["POST"])
@login_required
@permission_required("blog.delete")
def blog_delete(post_id):
    """Delete a blog post (owner or admin only)."""
    db = get_db()
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        abort(404)

    user_id = 1 if SINGLE_USER_MODE else current_user.id
    if not SINGLE_USER_MODE and post.user_id != user_id and not current_user.is_admin:
        abort(403)

    # Delete image files from disk before deleting DB rows
    for img in post.images:
        _delete_blog_image_files(img, post.user_id)

    db.delete(post)  # cascade deletes BlogImage and BlogComment rows
    db.commit()
    flash("Post deleted.", "success")
    return redirect(url_for("blog.blog_list"))


@blog_bp.route("/<int:post_id>/delete-image/<int:image_id>", methods=["POST"])
@login_required
def blog_delete_image(post_id, image_id):
    """AJAX endpoint to delete a single image from a post."""
    db = get_db()
    image = (
        db.query(BlogImage)
        .filter(BlogImage.id == image_id, BlogImage.post_id == post_id)
        .first()
    )
    if not image:
        return jsonify({"error": "Image not found"}), 404

    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    user_id = 1 if SINGLE_USER_MODE else current_user.id
    if not SINGLE_USER_MODE and post.user_id != user_id and not current_user.is_admin:
        return jsonify({"error": "Forbidden"}), 403

    _delete_blog_image_files(image, post.user_id)
    db.delete(image)
    db.commit()
    return jsonify({"success": True})


@blog_bp.route("/<int:post_id>/comment", methods=["POST"])
@login_required
@permission_required("blog.comment")
def blog_add_comment(post_id):
    """Add a comment to a blog post."""
    db = get_db()
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        abort(404)

    content = request.form.get("comment_content", "").strip()
    if not content:
        flash("Comment cannot be empty.", "error")
        return redirect(url_for("blog.blog_detail", post_id=post_id))
    if len(content) > BLOG_COMMENT_MAX_LEN:
        flash(f"Comment exceeds {BLOG_COMMENT_MAX_LEN} characters.", "error")
        return redirect(url_for("blog.blog_detail", post_id=post_id))

    user_id = 1 if SINGLE_USER_MODE else current_user.id
    comment = BlogComment(post_id=post_id, user_id=user_id, content=content)
    db.add(comment)
    db.commit()
    return redirect(url_for("blog.blog_detail", post_id=post_id) + "#comments")


@blog_bp.route("/<int:post_id>/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def blog_delete_comment(post_id, comment_id):
    """Delete a comment (comment author, post author, or admin)."""
    db = get_db()
    comment = (
        db.query(BlogComment)
        .filter(BlogComment.id == comment_id, BlogComment.post_id == post_id)
        .first()
    )
    if not comment:
        abort(404)

    user_id = 1 if SINGLE_USER_MODE else current_user.id
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()

    # Allow: comment author, post author, admin
    is_comment_owner = comment.user_id == user_id
    is_post_owner = post and post.user_id == user_id
    is_admin = not SINGLE_USER_MODE and current_user.is_admin

    if not SINGLE_USER_MODE and not (is_comment_owner or is_post_owner or is_admin):
        abort(403)

    db.delete(comment)
    db.commit()
    flash("Comment deleted.", "success")
    return redirect(url_for("blog.blog_detail", post_id=post_id) + "#comments")


@blog_bp.route("/uploads/<int:user_id>/<path:filename>")
def blog_serve_image(user_id, filename):
    """
    Serve blog images from instance/uploads/blog/<user_id>/<filename>.
    Public access: blog detail pages are public, images must be too.
    Path traversal protection via abspath check.
    """
    from flask import send_from_directory

    user_dir = os.path.join(BLOG_UPLOAD_FOLDER, str(user_id))
    base_dir = os.path.abspath(user_dir)
    target = os.path.abspath(os.path.join(user_dir, filename))

    # Prevent path traversal
    if not target.startswith(base_dir + os.sep):
        abort(404)

    if not os.path.exists(target):
        abort(404)

    return send_from_directory(user_dir, filename)


# =============================================================================
# Register Blueprints (must be after all route definitions)
# =============================================================================
app.register_blueprint(core_bp)
app.register_blueprint(api_bp)
app.register_blueprint(journal_bp)
app.register_blueprint(mobile_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(tools_bp)
app.register_blueprint(rest_api_bp, url_prefix="/api/v1")
app.register_blueprint(weather_bp, url_prefix="/api/v1/weather")
app.register_blueprint(blog_bp, url_prefix="/blog")

