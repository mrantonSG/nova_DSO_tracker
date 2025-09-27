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
from datetime import datetime, timedelta, timezone
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
from datetime import datetime, timedelta
import traceback
import uuid

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

import matplotlib
matplotlib.use('Agg')  # Use a non-GUI backend for headless servers
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from flask import render_template, jsonify, request, send_file, redirect, url_for, flash, g, current_app
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from flask import session, get_flashed_messages
from flask import Flask, send_from_directory, has_request_context

from astroquery.simbad import Simbad
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body, get_constellation
from astropy.time import Time
import astropy.units as u

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
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
    calculate_observable_duration_vectorized
)


from modules import nova_data_fetcher
from modules import rig_config
from modules.rig_config import calculate_rig_data, get_rig_config_path
from modules.rig_config import save_rig_config, load_rig_config

# =============================================================================
# Flask and Flask-Login Setup
# =============================================================================

APP_VERSION = "3.5.D2"

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

# CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config_default.yaml")


STELLARIUM_ERROR_MESSAGE = os.getenv("STELLARIUM_ERROR_MESSAGE")

CACHE_DIR = os.path.join(INSTANCE_PATH, "cache")
CONFIG_DIR = os.path.join(INSTANCE_PATH, "configs") # This is the only directory we need for YAMLs
BACKUP_DIR = os.path.join(INSTANCE_PATH, "backups")


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

def to_yaml_filter(data, indent=2):
    """Jinja2 filter to convert a Python object to a YAML string for form display."""
    if data is None:
        return ''
    try:
        # Dumps to a string, now correctly using the indent argument
        return yaml.dump(data, default_flow_style=None, indent=indent, sort_keys=False).strip()
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
    """Loads journal data from cache or file, checking for modifications."""
    if SINGLE_USER_MODE:
        filename = "journal_default.yaml"
    else:
        filename = f"journal_{username}.yaml"
    filepath = os.path.join(CONFIG_DIR, filename)

    # --- NEW: Create journal from template if it doesn't exist ---
    if not SINGLE_USER_MODE and not os.path.exists(filepath):
        print(f"-> Journal for user '{username}' not found. Creating from default template.")
        try:
            default_template_path = os.path.join(TEMPLATE_DIR, 'journal_default.yaml')
            shutil.copy(default_template_path, filepath)
            print(f"   -> Successfully created {filename}.")
        except Exception as e:
            print(f"   -> ❌ ERROR: Could not create journal for '{username}': {e}")
            return {"sessions": []}

    # --- Caching and loading logic continues below ---
    last_modified = os.path.getmtime(filepath) if os.path.exists(filepath) else 0
    if filepath in journal_cache and last_modified <= journal_mtime.get(filepath, 0):
        return journal_cache[filepath]

    if not os.path.exists(filepath):
        return {"sessions": []}

    try:
        with open(filepath, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {"sessions": []}
            if "sessions" not in data or not isinstance(data["sessions"], list):
                data["sessions"] = []

            journal_cache[filepath] = data
            journal_mtime[filepath] = last_modified
            return data
    except Exception as e:
        print(f"❌ ERROR: Failed to load journal '{filename}': {e}")
        return {"sessions": []}

def save_journal(username, journal_data):
    """Saves journal data for the given username to a YAML file."""
    if SINGLE_USER_MODE:
        filename = "journal_default.yaml"
    else:
        filename = f"journal_{username}.yaml"
    filepath = os.path.join(CONFIG_DIR, filename)

    try:
        with open(filepath, "w", encoding="utf-8") as file: # Added encoding
            yaml.dump(journal_data, file, sort_keys=False, allow_unicode=True, indent=2) # Added indent for readability
        print(f"💾 Journal saved to '{filename}' successfully.")
    except Exception as e:
        print(f"❌ ERROR: Failed to save journal '{filename}': {e}")

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
            thread = threading.Thread(target=update_outlook_cache, args=(username, loc_name, user_cfg.copy()))
            thread.start()
    except Exception as e:
        print(f"❌ ERROR: Failed to trigger background Outlook update: {e}")

def trigger_startup_cache_workers():
    """
    REVISED FOR DATABASE: Gets users from the DB to warm caches.
    """
    print("[STARTUP] Checking all caches for freshness...")

    # We need an application context to talk to the database
    with app.app_context():
        if SINGLE_USER_MODE:
            usernames_to_check = ["default"]
        else:
            # Query the User table to get all registered usernames
            try:
                all_db_users = db.session.execute(db.select(User)).scalars().all()
                usernames_to_check = [user.username for user in all_db_users]
            except Exception as e:
                print(f"⚠️ [STARTUP] Could not query users from database, may need initialization. Error: {e}")
                usernames_to_check = [] # Continue with no users if DB isn't ready

        # The rest of this function remains the same
        all_tasks = []
        for username in set(usernames_to_check):
            try:
                print(f"--- Preparing tasks for user: {username} ---")
                config = load_user_config(username)
                if not config:
                    print(f"    -> No config found for user '{username}', skipping.")
                    continue

                locations = config.get("locations", {})
                default_location = config.get("default_location")

                if default_location and default_location in locations:
                    all_tasks.insert(0, (username, default_location, config.copy()))

                for loc_name in locations.keys():
                    if loc_name != default_location:
                        all_tasks.append((username, loc_name, config.copy()))

            except Exception as e:
                print(f"❌ [STARTUP] ERROR: Could not prepare startup tasks for user '{username}': {e}")

        def run_tasks_sequentially(tasks):
            if not tasks:
                print("[STARTUP] All cache workers have completed.")
                return

            username, loc_name, cfg = tasks.pop(0)
            print(f"[STARTUP] Now processing task for user '{username}' at location '{loc_name}'.")
            worker_thread = threading.Thread(target=warm_main_cache, args=(username, loc_name, cfg))
            worker_thread.start()
            threading.Timer(15.0, run_tasks_sequentially, args=[tasks]).start()

        print(f"[STARTUP] Found a total of {len(all_tasks)} user/location tasks to process.")
        if all_tasks:
            run_tasks_sequentially(all_tasks)

def update_outlook_cache(username, location_name, user_config):
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

            criteria = {**{"min_observable_minutes": 60, "min_max_altitude": 30},
                        **user_config.get("imaging_criteria", {})}

            # --- NEW: Filter for objects with a project note ONLY ---
            all_objects_from_config = user_config.get("objects", [])
            project_objects = [
                obj for obj in all_objects_from_config
                if obj.get("Project") and obj.get("Project").lower().strip() not in ["", "none"]
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
                            altitude_threshold, user_config.get('sampling_interval_minutes', 15),
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

def warm_main_cache(username, location_name, user_config):
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
            sampling_interval = user_config.get('sampling_interval_minutes', 15)

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

            nightly_curves_cache[cache_key] = {
                "times_local": times_local, "altitudes": altitudes, "azimuths": azimuths,
                "transit_time": transit_time,
                "obs_duration_minutes": int(obs_duration.total_seconds() / 60) if obs_duration else 0,
                "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
                "alt_11pm": f"{alt_11pm:.2f}", "az_11pm": f"{az_11pm:.2f}"
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
            thread = threading.Thread(target=update_outlook_cache, args=(username, location_name, user_config.copy()))
            thread.start()

    except Exception as e:
        import traceback
        print(f"❌ [CACHE WARMER] FATAL ERROR during cache warming for '{location_name}': {e}")
        traceback.print_exc()

def sort_rigs(rigs, sort_key: str):
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

    # sort with None-safe behavior (None => bottom)
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
    try:
        rig_data = rig_config.load_rig_config(username, SINGLE_USER_MODE)

        component_id = request.form.get('component_id')
        component_type_plural = request.form.get('component_type')  # This will be 'telescopes', 'cameras', etc.
        component_name = request.form.get('name')

        if not component_type_plural or not component_name:
            flash("Component type and name are required.", "error")
            return redirect(url_for('config_form'))

        # Check if this is an UPDATE or an ADD
        if component_id:
            # --- This is an UPDATE ---
            component_to_update = next(
                (c for c in rig_data['components'][component_type_plural] if c['id'] == component_id), None)
            if not component_to_update:
                flash('Component to update not found.', 'error')
                return redirect(url_for('config_form'))

            # Update the fields
            component_to_update['name'] = component_name
            if component_type_plural == 'telescopes':
                component_to_update['aperture_mm'] = float(request.form.get('aperture_mm'))
                component_to_update['focal_length_mm'] = float(request.form.get('focal_length_mm'))
            elif component_type_plural == 'cameras':
                component_to_update['sensor_width_mm'] = float(request.form.get('sensor_width_mm'))
                component_to_update['sensor_height_mm'] = float(request.form.get('sensor_height_mm'))
                component_to_update['pixel_size_um'] = float(request.form.get('pixel_size_um'))
            elif component_type_plural == 'reducers_extenders':
                component_to_update['factor'] = float(request.form.get('factor'))

            flash(f"Component '{component_name}' updated successfully.", "success")

        else:
            # --- This is an ADD ---
            new_component = {"id": uuid.uuid4().hex, "name": component_name}
            if component_type_plural == 'telescopes':
                new_component['aperture_mm'] = float(request.form.get('aperture_mm'))
                new_component['focal_length_mm'] = float(request.form.get('focal_length_mm'))
            elif component_type_plural == 'cameras':
                new_component['sensor_width_mm'] = float(request.form.get('sensor_width_mm'))
                new_component['sensor_height_mm'] = float(request.form.get('sensor_height_mm'))
                new_component['pixel_size_um'] = float(request.form.get('pixel_size_um'))
            elif component_type_plural == 'reducers_extenders':
                new_component['factor'] = float(request.form.get('factor'))

            rig_data['components'][component_type_plural].append(new_component)
            flash(f"Component '{component_name}' added successfully.", "success")

        rig_config.save_rig_config(username, rig_data, SINGLE_USER_MODE)
        rig_data_cache.clear()

    except (ValueError, TypeError) as e:
        flash(f"Invalid data provided. Please ensure all numbers are valid. Error: {e}", "error")
    except Exception as e:
        flash(f"An unexpected error occurred: {e}", "error")

    return redirect(url_for('config_form'))


@app.route('/update_component', methods=['POST'])
@login_required
def update_component():
    username = "default" if SINGLE_USER_MODE else current_user.username
    try:
        rig_data = rig_config.load_rig_config(username, SINGLE_USER_MODE)

        component_id = request.form.get('component_id')
        # The form sends the PLURAL key (e.g., 'telescopes') in this field
        component_type_plural = request.form.get('component_type')

        if not all([component_id, component_type_plural]):
            flash('Missing component data for update.', 'error')
            return redirect(url_for('config_form'))

        # Find the component to update using the plural key
        component_to_update = next(
            (c for c in rig_data['components'][component_type_plural] if c['id'] == component_id), None)

        if not component_to_update:
            flash('Component not found for update.', 'error')
            return redirect(url_for('config_form'))

        # Update common field
        component_to_update['name'] = request.form.get('name')

        # Update specific fields based on the plural type
        if component_type_plural == 'telescopes':
            component_to_update['aperture_mm'] = float(request.form.get('aperture_mm'))
            component_to_update['focal_length_mm'] = float(request.form.get('focal_length_mm'))
        elif component_type_plural == 'cameras':
            component_to_update['sensor_width_mm'] = float(request.form.get('sensor_width_mm'))
            component_to_update['sensor_height_mm'] = float(request.form.get('sensor_height_mm'))
            component_to_update['pixel_size_um'] = float(request.form.get('pixel_size_um'))
        elif component_type_plural == 'reducers_extenders':
            component_to_update['factor'] = float(request.form.get('factor'))

        rig_config.save_rig_config(username, rig_data, SINGLE_USER_MODE)
        rig_data_cache.clear()

        # Create a user-friendly name for the flash message
        display_type = component_type_plural.replace('_', ' ').replace('reducers', 'reducer').rstrip('s').title()
        flash(f"{display_type} '{component_to_update['name']}' updated successfully.", "success")

    except (ValueError, TypeError) as e:
        flash(f"Invalid data for update. Please ensure all numbers are valid. Error: {e}", "error")
    except Exception as e:
        flash(f"An unexpected error occurred during update: {e}", "error")

    return redirect(url_for('config_form'))

@app.route('/add_rig', methods=['POST'])
@login_required
def add_rig():
    username = "default" if SINGLE_USER_MODE else current_user.username
    try:
        rig_data = rig_config.load_rig_config(username, SINGLE_USER_MODE)
        rig_name = request.form.get('rig_name')
        telescope_id = request.form.get('telescope_id')
        camera_id = request.form.get('camera_id')
        reducer_extender_id = request.form.get('reducer_extender_id')
        rig_id = request.form.get('rig_id') # Will be empty for new rigs

        if not rig_name or not telescope_id or not camera_id:
            flash("Rig Name, Telescope, and Camera are all required.", "error")
            return redirect(url_for('config_form'))

        rig_details = {
            "rig_name": rig_name,
            "telescope_id": telescope_id,
            "camera_id": camera_id,
            "reducer_extender_id": reducer_extender_id if reducer_extender_id else None
        }

        if rig_id:  # This is an UPDATE
            found = False
            for i, rig in enumerate(rig_data['rigs']):
                if rig.get('rig_id') == rig_id:
                    rig_data['rigs'][i].update(rig_details)
                    found = True
                    break
            if found:
                flash(f"Rig '{rig_name}' updated successfully.", "success")
            else:
                flash(f"Error: Rig with ID {rig_id} not found for update.", "error")
        else:  # This is an ADD
            rig_details["rig_id"] = uuid.uuid4().hex
            rig_data['rigs'].append(rig_details)
            flash(f"Rig '{rig_name}' created successfully.", "success")

        rig_config.save_rig_config(username, rig_data, SINGLE_USER_MODE)
        rig_data_cache.clear()

    except Exception as e:
        flash(f"An unexpected error occurred while creating/updating the rig: {e}", "error")

    return redirect(url_for('config_form'))

@app.route('/delete_component', methods=['POST'])
@login_required
def delete_component():
    username = "default" if SINGLE_USER_MODE else current_user.username
    try:
        component_id = request.form.get('component_id')
        component_type = request.form.get('component_type') # e.g., 'telescopes'
        if not component_id or not component_type:
            flash("Missing component ID or type for deletion.", "error")
            return redirect(url_for('config_form'))

        rig_data = rig_config.load_rig_config(username, SINGLE_USER_MODE)

        # Check for dependencies in rigs
        is_in_use = False
        for rig in rig_data.get('rigs', []):
            if component_id in [rig.get('telescope_id'), rig.get('camera_id'), rig.get('reducer_extender_id')]:
                is_in_use = True
                break

        if is_in_use:
            flash(f"Cannot delete component: It is currently used in at least one rig.", "error")
            return redirect(url_for('config_form'))

        # If not in use, proceed with deletion
        component_list = rig_data.get('components', {}).get(component_type, [])
        original_len = len(component_list)
        rig_data['components'][component_type] = [c for c in component_list if c.get('id') != component_id]

        if len(rig_data['components'][component_type]) < original_len:
            rig_config.save_rig_config(username, rig_data, SINGLE_USER_MODE)
            rig_data_cache.clear()
            flash("Component deleted successfully.", "success")
        else:
            flash("Component not found for deletion.", "error")

    except Exception as e:
        flash(f"An error occurred during component deletion: {e}", "error")

    return redirect(url_for('config_form'))


@app.route('/delete_rig', methods=['POST'])
@login_required
def delete_rig():
    username = "default" if SINGLE_USER_MODE else current_user.username
    try:
        rig_id = request.form.get('rig_id')
        if not rig_id:
            flash("Missing rig ID for deletion.", "error")
            return redirect(url_for('config_form'))

        rig_data = rig_config.load_rig_config(username, SINGLE_USER_MODE)
        rigs_list = rig_data.get('rigs', [])
        original_len = len(rigs_list)
        rig_data['rigs'] = [r for r in rigs_list if r.get('rig_id') != rig_id]

        if len(rig_data['rigs']) < original_len:
            rig_config.save_rig_config(username, rig_data, SINGLE_USER_MODE)
            rig_data_cache.clear()
            flash("Rig deleted successfully.", "success")
        else:
            flash("Rig not found for deletion.", "error")

    except Exception as e:
        flash(f"An error occurred during rig deletion: {e}", "error")

    return redirect(url_for('config_form'))

@app.route('/set_rig_sort_preference', methods=['POST'])
@login_required
def set_rig_sort_preference():
    """Save the user's rig sort preference (e.g., 'name-asc') into their config YAML."""
    try:
        data = request.get_json(force=True) or {}
        sort_value = data.get('sort', 'name-asc')

        username = "default" if SINGLE_USER_MODE else current_user.username

        # Load existing config
        rig_data = rig_config.load_rig_config(username, SINGLE_USER_MODE) or {}

        # Ensure ui_preferences section exists
        if 'ui_preferences' not in rig_data:
            rig_data['ui_preferences'] = {}

        # Save the new sort order
        rig_data['ui_preferences']['sort_order'] = sort_value

        # Persist the updated config
        rig_config.save_rig_config(username, rig_data, SINGLE_USER_MODE)

        return jsonify({"status": "ok", "sort_order": sort_value})
    except Exception as e:
        print(f"[set_rig_sort_preference] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_rig_data')
@login_required
def get_rig_data():
    """Return components + rigs with calculated fields, sorted per the user's saved preference.
       Also includes `sort_preference` so the frontend can sync its dropdown/state."""
    username = "default" if SINGLE_USER_MODE else current_user.username

    # Load full rig config (components, rigs, ui_preferences, etc.)
    rig_data = rig_config.load_rig_config(username, SINGLE_USER_MODE) or {}

    # Ensure expected keys exist
    rig_data.setdefault('components', {
        'telescopes': [],
        'cameras': [],
        'reducers_extenders': []
    })
    rig_data.setdefault('rigs', [])
    rig_data.setdefault('ui_preferences', {})

    # Calculate derived fields for each rig (image scale, f/ratio, FOV, etc.)
    try:
        components = rig_data.get('components', {})
        for rig in rig_data['rigs']:
            try:
                calc = rig_config.calculate_rig_data(rig, components)
                if isinstance(calc, dict):
                    rig.update(calc)
            except Exception as e:
                # Non-fatal: keep going for other rigs
                print(f"[get_rig_data] Warning: could not calculate fields for rig '{rig.get('rig_name','?')}': {e}")
    except Exception as e:
        print(f"[get_rig_data] Warning during rig calculations: {e}")

    # Resolve the user's saved sort preference (default to name-asc)
    sort_preference = rig_data.get('ui_preferences', {}).get('sort_order') or 'name-asc'

    # Sort rigs using your helper if present; otherwise use a safe local fallback
    def _fallback_sort(rigs, sort_key: str):
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
                    # ISO 8601 with optional Z
                    return datetime.fromisoformat(ts.replace('Z', '+00:00'))
                except Exception:
                    # fall back to id so sort is at least stable
                    return r.get('rig_id') or ''
            # default by name
            return (r.get('rig_name') or '').lower()

        # None-safe key: Nones sink to bottom
        def none_safe(x):
            v = getter(x)
            return (v is None, v)

        return sorted(rigs, key=none_safe, reverse=reverse)

    try:
        # Prefer your existing helper if it exists
        sorted_rigs = sort_rigs_list(rig_data['rigs'], sort_preference)  # type: ignore[name-defined]
    except NameError:
        # Fallback if sort_rigs_list isn't available in this context
        sorted_rigs = _fallback_sort(rig_data['rigs'], sort_preference)
    except Exception as e:
        print(f"[get_rig_data] Warning: sort helper failed ({e}); using fallback.")
        sorted_rigs = _fallback_sort(rig_data['rigs'], sort_preference)

    rig_data['rigs'] = sorted_rigs

    # Expose the effective preference explicitly so the frontend can sync its UI
    rig_data['sort_preference'] = sort_preference

    return jsonify(rig_data)

@app.route('/journal')
@login_required # Or handle SINGLE_USER_MODE appropriately if guests can see a default journal
def journal_list_view():
    if SINGLE_USER_MODE:
        username = "default"
        # If you want g.is_guest to be available in journal_list.html for SINGLE_USER_MODE
        # ensure it's set correctly or just pass a specific variable.
        # For SINGLE_USER_MODE, the user is effectively always "logged in".
        is_guest_for_template = False
    elif current_user.is_authenticated:
        username = current_user.username
        is_guest_for_template = False
    else:
        # This case should ideally not be hit if @login_required is effective.
        # If you allow guests to see a specific journal, handle 'guest_user' here.
        # For now, let's assume they get redirected to login.
        # If you reach here due to some other logic, provide default.
        flash("Please log in to view the journal.", "info")
        return redirect(url_for('login'))

    journal_data = load_journal(username)
    sessions = journal_data.get('sessions', [])

    # Optionally sort sessions by date descending before passing to template
    try:
        sessions.sort(key=lambda s: s.get('session_date', '1900-01-01'), reverse=True)
    except Exception as e:
        print(f"Warning: Could not sort journal sessions by date: {e}")

    return render_template('journal_list.html',
                           journal_sessions=sessions,
                           is_guest=is_guest_for_template # Pass if your base.html or journal_list.html needs it
                           )


def safe_float(value_str):
    if value_str is None or str(value_str).strip() == "":  # Check for None and empty string
        return None
    try:
        return float(value_str)
    except ValueError:
        print(f"Warning: Could not convert '{value_str}' to float.")
        return None


def safe_int(value_str):
    if value_str is None or str(value_str).strip() == "":  # Check for None and empty string
        return None
    try:
        # First try float conversion to handle inputs like "10.0" for an int field, then convert to int
        return int(float(value_str))
    except ValueError:
        print(f"Warning: Could not convert '{value_str}' to int.")
        return None


@app.route('/journal/add', methods=['GET', 'POST'])
@login_required
def journal_add():
    if SINGLE_USER_MODE:
        username = "default"
    else:
        if not current_user.is_authenticated:
            flash("Please log in to add a journal entry.", "warning")
            return redirect(url_for('login'))
        username = current_user.username

    if request.method == 'POST':
        try:
            journal_data = load_journal(username)
            if not isinstance(journal_data.get('sessions'), list):
                journal_data['sessions'] = []

            user_tz = pytz.timezone(g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC')
            today_date_in_user_tz = datetime.now(user_tz).strftime('%Y-%m-%d')

            # THIS IS THE FULL, CORRECTED LIST OF ALL FORM FIELDS
            new_session_data = {
                "session_id": generate_session_id(),
                "session_date": request.form.get("session_date") or today_date_in_user_tz,
                "target_object_id": request.form.get("target_object_id", "").strip(),
                "location_name": request.form.get("location_name", "").strip(),
                "seeing_observed_fwhm": safe_float(request.form.get("seeing_observed_fwhm")),
                "transparency_observed_scale": request.form.get("transparency_observed_scale", "").strip(),
                "sky_sqm_observed": safe_float(request.form.get("sky_sqm_observed")),
                "weather_notes": request.form.get("weather_notes", "").strip(),
                "telescope_setup_notes": request.form.get("telescope_setup_notes", "").strip(),
                "filter_used_session": request.form.get("filter_used_session", "").strip(),
                "guiding_rms_avg_arcsec": safe_float(request.form.get("guiding_rms_avg_arcsec")),
                "guiding_equipment": request.form.get("guiding_equipment", "").strip(),
                "dither_details": request.form.get("dither_details", "").strip(),
                "acquisition_software": request.form.get("acquisition_software", "").strip(),
                "exposure_time_per_sub_sec": safe_int(request.form.get("exposure_time_per_sub_sec")),
                "number_of_subs_light": safe_int(request.form.get("number_of_subs_light")),
                "gain_setting": safe_int(request.form.get("gain_setting")),
                "offset_setting": safe_int(request.form.get("offset_setting")),
                "camera_temp_setpoint_c": safe_float(request.form.get("camera_temp_setpoint_c")),
                "camera_temp_actual_avg_c": safe_float(request.form.get("camera_temp_actual_avg_c")),
                "binning_session": request.form.get("binning_session", "").strip(),
                "darks_strategy": request.form.get("darks_strategy", "").strip(),
                "flats_strategy": request.form.get("flats_strategy", "").strip(),
                "bias_darkflats_strategy": request.form.get("bias_darkflats_strategy", "").strip(),
                "session_rating_subjective": safe_int(request.form.get("session_rating_subjective")),
                "moon_illumination_session": safe_int(request.form.get("moon_illumination_session")),
                "moon_angular_separation_session": safe_float(request.form.get("moon_angular_separation_session")),
                "filter_L_subs": safe_int(request.form.get("filter_L_subs")),
                "filter_L_exposure_sec": safe_int(request.form.get("filter_L_exposure_sec")),
                "filter_R_subs": safe_int(request.form.get("filter_R_subs")),
                "filter_R_exposure_sec": safe_int(request.form.get("filter_R_exposure_sec")),
                "filter_G_subs": safe_int(request.form.get("filter_G_subs")),
                "filter_G_exposure_sec": safe_int(request.form.get("filter_G_exposure_sec")),
                "filter_B_subs": safe_int(request.form.get("filter_B_subs")),
                "filter_B_exposure_sec": safe_int(request.form.get("filter_B_exposure_sec")),
                "filter_Ha_subs": safe_int(request.form.get("filter_Ha_subs")),
                "filter_Ha_exposure_sec": safe_int(request.form.get("filter_Ha_exposure_sec")),
                "filter_OIII_subs": safe_int(request.form.get("filter_OIII_subs")),
                "filter_OIII_exposure_sec": safe_int(request.form.get("filter_OIII_exposure_sec")),
                "filter_SII_subs": safe_int(request.form.get("filter_SII_subs")),
                "filter_SII_exposure_sec": safe_int(request.form.get("filter_SII_exposure_sec")),
                "general_notes_problems_learnings": request.form.get("general_notes_problems_learnings", "").strip()
            }

            final_session_entry = {}
            for k, v in new_session_data.items():
                is_empty_str_for_non_special_field = isinstance(v, str) and v.strip() == "" and k not in [
                    "target_object_id", "location_name"]
                is_none_for_non_special_field = v is None and k not in ["target_object_id", "location_name"]
                if not (is_empty_str_for_non_special_field or is_none_for_non_special_field):
                    final_session_entry[k] = v
            if "session_id" not in final_session_entry:
                final_session_entry["session_id"] = new_session_data["session_id"]
            if "session_date" not in final_session_entry or not final_session_entry["session_date"]:
                final_session_entry["session_date"] = datetime.now().strftime('%Y-%m-%d')
            total_integration_seconds = 0
            has_any_integration_data = False

            try:
                num_subs = int(str(final_session_entry.get('number_of_subs_light', 0)))
                exp_time = int(str(final_session_entry.get('exposure_time_per_sub_sec', 0)))
                if num_subs > 0 and exp_time > 0:
                    total_integration_seconds += (num_subs * exp_time)
                    has_any_integration_data = True
            except (ValueError, TypeError):
                pass

            mono_filters = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']
            for filt in mono_filters:
                try:
                    subs_val = int(str(final_session_entry.get(f'filter_{filt}_subs', 0)))
                    exp_val = int(str(final_session_entry.get(f'filter_{filt}_exposure_sec', 0)))
                    if subs_val > 0 and exp_val > 0:
                        total_integration_seconds += (subs_val * exp_val)
                        has_any_integration_data = True
                except (ValueError, TypeError):
                    pass

            if has_any_integration_data:
                final_session_entry['calculated_integration_time_minutes'] = round(total_integration_seconds / 60.0, 0)
            else:
                final_session_entry['calculated_integration_time_minutes'] = 'N/A'
            journal_data['sessions'].append(final_session_entry)
            save_journal(username, journal_data)
            flash("New journal entry added successfully!", "success")

            target_object_id_for_redirect = final_session_entry.get("target_object_id")
            new_session_id_for_redirect = final_session_entry.get("session_id")

            if target_object_id_for_redirect and target_object_id_for_redirect.strip() != "":
                return redirect(url_for('graph_dashboard', object_name=target_object_id_for_redirect,
                                        session_id=new_session_id_for_redirect))
            else:
                return redirect(url_for('index'))

        except Exception as e:
            flash(f"Error adding journal entry: {e}", "error")
            print(f"❌ ERROR in journal_add POST: {e}")
            return redirect(url_for('journal_add'))

    # --- GET request logic ---
    available_rigs = []
    rig_data = load_rig_config(username, SINGLE_USER_MODE)
    if rig_data and rig_data.get('rigs'):
        components = rig_data.get('components', {})
        telescopes = {t['id']: t['name'] for t in components.get('telescopes', [])}
        cameras = {c['id']: c['name'] for c in components.get('cameras', [])}
        reducers = {r['id']: r['name'] for r in components.get('reducers_extenders', [])}

        for rig in rig_data['rigs']:
            tele_name = telescopes.get(rig['telescope_id'], 'N/A')
            cam_name = cameras.get(rig['camera_id'], 'N/A')

            resolved_parts = [tele_name]
            if rig.get('reducer_extender_id'):
                reducer_name = reducers.get(rig['reducer_extender_id'], 'N/A')
                resolved_parts.append(reducer_name)
            resolved_parts.append(cam_name)

            available_rigs.append({
                'rig_name': rig['rig_name'],
                'resolved_string': ' + '.join(resolved_parts)
            })

    available_objects = g.user_config.get("objects", []) if hasattr(g, 'user_config') else []
    available_locations = g.locations if hasattr(g, 'locations') else {}
    default_loc = g.selected_location if hasattr(g, 'selected_location') else ""
    preselected_target_id = request.args.get('target', None)
    entry_for_form = {}

    if preselected_target_id:
        entry_for_form["target_object_id"] = preselected_target_id
    if not entry_for_form.get("location_name") and default_loc:
        entry_for_form["location_name"] = default_loc

    user_tz = pytz.timezone(g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC')
    today_date_in_user_tz = datetime.now(user_tz).strftime('%Y-%m-%d')

    if not entry_for_form.get("session_date"):
        entry_for_form["session_date"] = today_date_in_user_tz

    cancel_url_for_add = url_for('index')
    if preselected_target_id:
        cancel_url_for_add = url_for('graph_dashboard', object_name=preselected_target_id)
    # --- Apply per-user rig sort preference for the journal form ---
    try:
        username_effective = "default" if SINGLE_USER_MODE else current_user.username
        _user_cfg = rig_config.load_rig_config(username_effective, SINGLE_USER_MODE) or {}
        _sort_pref = (_user_cfg.get('ui_preferences', {}) or {}).get('sort_order') or 'name-asc'

        def _to_num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        _key, _, _direction = _sort_pref.partition('-')
        _reverse = (_direction == 'desc')

        def _getattr_or_dict(x, attr, key):
            if isinstance(x, dict):
                return x.get(key)
            return getattr(x, attr, None)

        def _get(r):
            if _key == 'name':
                v = _getattr_or_dict(r, 'rig_name', 'rig_name') or ''
                return str(v).lower()
            if _key == 'fl':
                return _to_num(_getattr_or_dict(r, 'effective_focal_length', 'effective_focal_length'))
            if _key == 'fr':
                return _to_num(_getattr_or_dict(r, 'f_ratio', 'f_ratio'))
            if _key == 'scale':
                return _to_num(_getattr_or_dict(r, 'image_scale', 'image_scale'))
            if _key == 'fovw':
                return _to_num(_getattr_or_dict(r, 'fov_w_arcmin', 'fov_w_arcmin'))
            if _key == 'recent':
                ts = (
                        _getattr_or_dict(r, 'updated_at', 'updated_at')
                        or _getattr_or_dict(r, 'created_at', 'created_at')
                        or ''
                )
                try:
                    return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                except Exception:
                    return _getattr_or_dict(r, 'rig_id', 'rig_id') or ''
            # default: name
            v = _getattr_or_dict(r, 'rig_name', 'rig_name') or ''
            return str(v).lower()

        def _none_safe(x):
            v = _get(x)
            return (v is None, v)

        # IMPORTANT: this variable name must match what you pass to the template
        available_rigs = sorted(available_rigs, key=_none_safe, reverse=_reverse)

    except Exception as _e:
        print(f"[journal_form] Warning: could not apply rig sort preference: {_e}")
    # --- end rig sort preference ---
    return render_template('journal_form.html',
                           form_title="Add New Imaging Session",
                           form_action_url=url_for('journal_add'),
                           submit_button_text="Add Session",
                           available_objects=available_objects,
                           available_locations=available_locations,
                           available_rigs=available_rigs,
                           entry=entry_for_form,
                           cancel_url=cancel_url_for_add
                           )


@app.route('/journal/edit/<session_id>', methods=['GET', 'POST'])
@login_required
def journal_edit(session_id):
    if SINGLE_USER_MODE:
        username = "default"
    else:
        if not current_user.is_authenticated:
            flash("Please log in to edit journal entries.", "warning")
            return redirect(url_for('login'))
        username = current_user.username

    journal_data = load_journal(username)
    sessions = journal_data.get('sessions', [])
    session_to_edit = None
    session_index = -1

    for index, session_item in enumerate(sessions):
        if session_item.get('session_id') == session_id:
            session_to_edit = session_item
            session_index = index
            break

    if session_index == -1 or not session_to_edit:
        flash(f"Journal entry with ID {session_id} not found.", "error")
        return redirect(url_for('journal_list_view'))

    if request.method == 'POST':
        try:
            # This is the full logic for updating the entry
            updated_session_data = {
                "session_id": session_id,
                "session_date": request.form.get("session_date") or session_to_edit.get("session_date"),
                "target_object_id": request.form.get("target_object_id", "").strip(),
                "location_name": request.form.get("location_name", "").strip(),
                "seeing_observed_fwhm": safe_float(request.form.get("seeing_observed_fwhm")),
                "transparency_observed_scale": request.form.get("transparency_observed_scale", "").strip(),
                "sky_sqm_observed": safe_float(request.form.get("sky_sqm_observed")),
                "weather_notes": request.form.get("weather_notes", "").strip(),
                "telescope_setup_notes": request.form.get("telescope_setup_notes", "").strip(),
                "filter_used_session": request.form.get("filter_used_session", "").strip(),
                "guiding_rms_avg_arcsec": safe_float(request.form.get("guiding_rms_avg_arcsec")),
                "guiding_equipment": request.form.get("guiding_equipment", "").strip(),
                "dither_details": request.form.get("dither_details", "").strip(),
                "acquisition_software": request.form.get("acquisition_software", "").strip(),
                "exposure_time_per_sub_sec": safe_int(request.form.get("exposure_time_per_sub_sec")),
                "number_of_subs_light": safe_int(request.form.get("number_of_subs_light")),
                "gain_setting": safe_int(request.form.get("gain_setting")),
                "offset_setting": safe_int(request.form.get("offset_setting")),
                "camera_temp_setpoint_c": safe_float(request.form.get("camera_temp_setpoint_c")),
                "camera_temp_actual_avg_c": safe_float(request.form.get("camera_temp_actual_avg_c")),
                "binning_session": request.form.get("binning_session", "").strip(),
                "darks_strategy": request.form.get("darks_strategy", "").strip(),
                "flats_strategy": request.form.get("flats_strategy", "").strip(),
                "bias_darkflats_strategy": request.form.get("bias_darkflats_strategy", "").strip(),
                "session_rating_subjective": safe_int(request.form.get("session_rating_subjective")),
                "moon_illumination_session": safe_int(request.form.get("moon_illumination_session")),
                "moon_angular_separation_session": safe_float(request.form.get("moon_angular_separation_session")),
                "filter_L_subs": safe_int(request.form.get("filter_L_subs")),
                "filter_L_exposure_sec": safe_int(request.form.get("filter_L_exposure_sec")),
                "filter_R_subs": safe_int(request.form.get("filter_R_subs")),
                "filter_R_exposure_sec": safe_int(request.form.get("filter_R_exposure_sec")),
                "filter_G_subs": safe_int(request.form.get("filter_G_subs")),
                "filter_G_exposure_sec": safe_int(request.form.get("filter_G_exposure_sec")),
                "filter_B_subs": safe_int(request.form.get("filter_B_subs")),
                "filter_B_exposure_sec": safe_int(request.form.get("filter_B_exposure_sec")),
                "filter_Ha_subs": safe_int(request.form.get("filter_Ha_subs")),
                "filter_Ha_exposure_sec": safe_int(request.form.get("filter_Ha_exposure_sec")),
                "filter_OIII_subs": safe_int(request.form.get("filter_OIII_subs")),
                "filter_OIII_exposure_sec": safe_int(request.form.get("filter_OIII_exposure_sec")),
                "filter_SII_subs": safe_int(request.form.get("filter_SII_subs")),
                "filter_SII_exposure_sec": safe_int(request.form.get("filter_SII_exposure_sec")),
                "general_notes_problems_learnings": request.form.get("general_notes_problems_learnings", "").strip()
            }

            final_updated_entry = {k: v for k, v in updated_session_data.items() if v is not None and v != ""}
            final_updated_entry['session_id'] = session_id  # Ensure session_id is always present
            total_integration_seconds = 0
            has_any_integration_data = False

            try:
                num_subs = int(str(final_updated_entry.get('number_of_subs_light', 0)))
                exp_time = int(str(final_updated_entry.get('exposure_time_per_sub_sec', 0)))
                if num_subs > 0 and exp_time > 0:
                    total_integration_seconds += (num_subs * exp_time)
                    has_any_integration_data = True
            except (ValueError, TypeError):
                pass

            mono_filters = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']
            for filt in mono_filters:
                try:
                    subs_val = int(str(final_updated_entry.get(f'filter_{filt}_subs', 0)))
                    exp_val = int(str(final_updated_entry.get(f'filter_{filt}_exposure_sec', 0)))
                    if subs_val > 0 and exp_val > 0:
                        total_integration_seconds += (subs_val * exp_val)
                        has_any_integration_data = True
                except (ValueError, TypeError):
                    pass

            if has_any_integration_data:
                final_updated_entry['calculated_integration_time_minutes'] = round(total_integration_seconds / 60.0, 0)
            else:
                final_updated_entry['calculated_integration_time_minutes'] = 'N/A'
            sessions[session_index] = final_updated_entry
            journal_data['sessions'] = sessions
            save_journal(username, journal_data)

            flash_message_target = final_updated_entry.get('target_object_id', session_id[:8] + "...")
            flash_message_date = final_updated_entry.get('session_date', 'entry')
            flash(f"Journal entry for '{flash_message_target}' on {flash_message_date} updated successfully!",
                  "success")

            # --- THIS IS THE CRITICAL REDIRECT LOGIC ---
            target_object_id_for_redirect = final_updated_entry.get("target_object_id")
            if target_object_id_for_redirect and target_object_id_for_redirect.strip() != "":
                return redirect(
                    url_for('graph_dashboard', object_name=target_object_id_for_redirect, session_id=session_id))
            else:
                return redirect(url_for('index'))

        except Exception as e:
            flash(f"Error updating journal entry: {e}", "error")
            print(f"❌ ERROR in journal_edit POST for session {session_id}: {e}")
            traceback.print_exc()
            return redirect(url_for('journal_edit', session_id=session_id))

    # --- GET request logic (for loading the form) ---
    available_rigs = []
    rig_data = load_rig_config(username, SINGLE_USER_MODE)
    if rig_data and rig_data.get('rigs'):
        components = rig_data.get('components', {})
        telescopes = {t['id']: t['name'] for t in components.get('telescopes', [])}
        cameras = {c['id']: c['name'] for c in components.get('cameras', [])}
        reducers = {r['id']: r['name'] for r in components.get('reducers_extenders', [])}
        for rig in rig_data['rigs']:
            tele_name = telescopes.get(rig['telescope_id'], 'N/A')
            cam_name = cameras.get(rig['camera_id'], 'N/A')
            resolved_parts = [tele_name]
            if rig.get('reducer_extender_id'):
                reducer_name = reducers.get(rig['reducer_extender_id'], 'N/A')
                resolved_parts.append(reducer_name)
            resolved_parts.append(cam_name)
            available_rigs.append({'rig_name': rig['rig_name'], 'resolved_string': ' + '.join(resolved_parts)})

    available_objects = g.user_config.get("objects", []) if hasattr(g, 'user_config') else []
    available_locations = g.locations if hasattr(g, 'locations') else {}
    target_object_id_for_cancel = session_to_edit.get("target_object_id")
    cancel_url_for_edit = url_for('index')

    if target_object_id_for_cancel and target_object_id_for_cancel.strip() != "":
        cancel_url_for_edit = url_for('graph_dashboard', object_name=target_object_id_for_cancel, session_id=session_id)
    elif session_id:
        cancel_url_for_edit = url_for('journal_list_view')
    # --- Apply per-user rig sort preference for the journal form ---
    try:
        username_effective = "default" if SINGLE_USER_MODE else current_user.username
        _user_cfg = rig_config.load_rig_config(username_effective, SINGLE_USER_MODE) or {}
        _sort_pref = (_user_cfg.get('ui_preferences', {}) or {}).get('sort_order') or 'name-asc'

        def _to_num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        _key, _, _direction = _sort_pref.partition('-')
        _reverse = (_direction == 'desc')

        def _getattr_or_dict(x, attr, key):
            if isinstance(x, dict):
                return x.get(key)
            return getattr(x, attr, None)

        def _get(r):
            if _key == 'name':
                v = _getattr_or_dict(r, 'rig_name', 'rig_name') or ''
                return str(v).lower()
            if _key == 'fl':
                return _to_num(_getattr_or_dict(r, 'effective_focal_length', 'effective_focal_length'))
            if _key == 'fr':
                return _to_num(_getattr_or_dict(r, 'f_ratio', 'f_ratio'))
            if _key == 'scale':
                return _to_num(_getattr_or_dict(r, 'image_scale', 'image_scale'))
            if _key == 'fovw':
                return _to_num(_getattr_or_dict(r, 'fov_w_arcmin', 'fov_w_arcmin'))
            if _key == 'recent':
                ts = (
                        _getattr_or_dict(r, 'updated_at', 'updated_at')
                        or _getattr_or_dict(r, 'created_at', 'created_at')
                        or ''
                )
                try:
                    return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                except Exception:
                    return _getattr_or_dict(r, 'rig_id', 'rig_id') or ''
            # default: name
            v = _getattr_or_dict(r, 'rig_name', 'rig_name') or ''
            return str(v).lower()

        def _none_safe(x):
            v = _get(x)
            return (v is None, v)

        # IMPORTANT: this variable name must match what you pass to the template
        available_rigs = sorted(available_rigs, key=_none_safe, reverse=_reverse)

    except Exception as _e:
        print(f"[journal_form] Warning: could not apply rig sort preference: {_e}")
    # --- end rig sort preference ---
    return render_template('journal_form.html',
                           form_title=f"Edit Imaging Session",
                           form_action_url=url_for('journal_edit', session_id=session_id),
                           submit_button_text="Save Changes",
                           entry=session_to_edit,
                           available_objects=available_objects,
                           available_locations=available_locations,
                           available_rigs=available_rigs,
                           cancel_url=cancel_url_for_edit)

@app.route('/journal/delete/<session_id>', methods=['POST'])
@login_required  # Or your custom logic for SINGLE_USER_MODE access
def journal_delete(session_id):
    if SINGLE_USER_MODE:
        username = "default"
    else:
        if not current_user.is_authenticated:  # Should be caught by @login_required
            flash("Please log in to delete journal entries.", "warning")
            return redirect(url_for('login'))  # Or your login route name
        username = current_user.username

    journal_data = load_journal(username)
    sessions = journal_data.get('sessions', [])

    session_to_delete = None
    target_object_id_of_deleted_session = None  # Variable to store the target ID

    for session_item in sessions:  # Changed loop variable to avoid conflict if 'session' is used by Flask
        if session_item.get('session_id') == session_id:
            session_to_delete = session_item
            target_object_id_of_deleted_session = session_item.get('target_object_id')
            break

    if session_to_delete:
        try:
            sessions.remove(session_to_delete)  # Remove the session from the list
            journal_data['sessions'] = sessions  # Assign the modified list back to the main data
            save_journal(username, journal_data)  # Save the updated journal data

            flash_message_target = target_object_id_of_deleted_session if target_object_id_of_deleted_session else "N/A"
            # Provide a more specific flash message, perhaps using a few chars of the ID if target is missing
            flash_id_snippet = session_id[:8] + "..." if session_id and len(session_id) > 8 else session_id
            flash(f"Journal entry for '{flash_message_target}' (ID: {flash_id_snippet}) deleted successfully.",
                  "success")

            # --- CORRECTED REDIRECT LOGIC after successful delete ---
            if target_object_id_of_deleted_session and target_object_id_of_deleted_session.strip() != "":
                print(f"Redirecting after delete to graph_dashboard for object: {target_object_id_of_deleted_session}")
                return redirect(url_for('graph_dashboard', object_name=target_object_id_of_deleted_session))
            else:
                # Fallback if target_object_id was somehow missing or empty from the deleted session
                print(
                    f"Redirecting after delete to main journal list (target_object_id was missing for deleted session).")
                return redirect(url_for('journal_list_view'))  # Or url_for('index') if you prefer
            # --- END OF CORRECTED REDIRECT LOGIC ---

        except Exception as e:  # Catch potential errors during remove or save
            flash(f"Error processing deletion for session ID {session_id}: {e}", "error")
            print(f"ERROR during deletion/save for session {session_id}: {e}")
            # If error during save, redirect back to where the user was, if possible, or a safe page.
            # Re-fetching target_object_id here as it might be lost if session_to_delete was modified
            # This is a basic fallback; more sophisticated state restoration might be needed for complex cases.
            if target_object_id_of_deleted_session and target_object_id_of_deleted_session.strip() != "":
                return redirect(url_for('graph_dashboard', object_name=target_object_id_of_deleted_session,
                                        session_id=session_id))  # Back to object page, session might still appear if save failed
            else:
                return redirect(url_for('journal_list_view'))  # General fallback

    else:  # This else is for 'if session_to_delete:' (i.e., session was not found)
        flash(f"Journal entry with ID {session_id} not found for deletion.", "error")
        # If the session was not found at all, redirecting to journal_list_view or index is appropriate.
        return redirect(url_for('journal_list_view'))

@app.route('/journal/add_for_target/<path:object_name>')
@login_required
def journal_add_for_target(object_name):
    # Redirect to the main add form, passing the object_name as a query parameter
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

def save_user_config(username, config_data):
    if SINGLE_USER_MODE:
        # Corrected: Only the filename is needed here.
        filename = "config_default.yaml"
    else:
        # This part was already correct.
        filename = f"config_{username}.yaml"

    filepath = os.path.join(CONFIG_DIR, filename)
    with open(filepath, "w") as file:
        yaml.dump(config_data, file)

def get_imaging_criteria():
    default_criteria = {
        "min_observable_minutes": 60,
        "min_max_altitude": 30,
        "max_moon_illumination": 20,
        "min_angular_distance": 30,
        "search_horizon_months": 6
    }
    user_criteria = g.user_config.get("imaging_criteria", {})
    return {**default_criteria, **user_criteria}
# In nova.py

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
    if SINGLE_USER_MODE:
        g.user_config = load_user_config("default")
        g.is_guest = False
    elif current_user.is_authenticated:
        g.user_config = load_user_config(current_user.username)
        g.is_guest = False
    else:
        g.user_config = load_user_config("guest_user")
        g.is_guest = True

    g.locations = g.user_config.get("locations", {})
    g.selected_location = g.user_config.get("default_location", "")
    g.altitude_threshold = g.user_config.get("altitude_threshold", 20)
    loc_cfg = g.locations.get(g.selected_location, {})
    g.lat = loc_cfg.get("lat")
    g.lon = loc_cfg.get("lon")
    g.tz_name = loc_cfg.get("timezone", "UTC")

    # restore these three globals so loops and lookups work:
    g.objects_list = g.user_config.get("objects", [])
    g.alternative_names = {
        obj.get("Object").lower(): obj.get("Name")
        for obj in g.objects_list
    }
    g.projects = {
        obj.get("Object").lower(): obj.get("Project")
        for obj in g.objects_list
    }
    g.objects = [ obj.get("Object") for obj in g.objects_list ]

@app.before_request
def ensure_telemetry_defaults():
    """
    Ensure telemetry defaults safely, without ever overwriting unrelated config
    and without persisting instance_id into YAML.
    """
    try:
        # Proceed only if a valid user config dict is already loaded into g
        if not (hasattr(g, 'user_config') and isinstance(g.user_config, dict)):
            return

        changed = False
        telemetry_config = g.user_config.setdefault('telemetry', {})

        # Default for 'enabled'
        if 'enabled' not in telemetry_config:
            telemetry_config['enabled'] = True
            changed = True

        # Never persist instance_id in YAML; use .env at send-time
        if 'instance_id' in telemetry_config:
            telemetry_config.pop('instance_id', None)
            # no need to mark changed; we remove a field we don't store

        # Save only if we actually added the enabled default
        if changed:
            username = "default" if SINGLE_USER_MODE else (
                current_user.username if getattr(current_user, 'is_authenticated', False) else "guest_user"
            )
            print("[CONFIG] Telemetry defaults were missing. Updating config file (enabled).")
            save_user_config(username, g.user_config)

        # Optionally expose the env-based ID in-memory if other code wants it
        g.telemetry_instance_id = os.environ.get('INSTANCE_ID') or secrets.token_hex(16)

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

@app.route('/fetch_all_details', methods=['POST'])
@login_required
def fetch_all_details():
    """Fetches missing details for all objects in the user's config."""
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    print(f"[FETCH ALL] Triggered for user: {username}")
    try:
        config_data = load_user_config(username)  # Load current config

        # check_and_fill_object_data modifies config_data in place
        # and returns True if changes were made and need saving
        config_modified = check_and_fill_object_data(config_data)

        if config_modified:
            # Save the potentially modified config
            save_user_config(username, config_data)
            flash("Fetched and saved missing object details.", "success")
            print("[FETCH ALL] Data fetched and config saved.")
        else:
            flash("No missing data found or no updates needed.", "info")
            print("[FETCH ALL] No missing data needed fetching.")

    except Exception as e:
        print(f"[FETCH ALL ERROR] Failed during fetch all process: {e}")
        flash(f"An error occurred during data fetching: {e}", "error")

    # Redirect back to the config form to show updated data/messages
    return redirect(url_for('config_form'))

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

    return jsonify({"status": "success", "message": f"Location set to {location_name}"})


@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.context_processor
def inject_version():
    return dict(version=APP_VERSION)

@app.route('/download_config')
@login_required # This was missing, it's good practice to add it
def download_config():
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{current_user.username}.yaml"

    # FIX: Use the CONFIG_DIR variable for a reliable path
    filepath = os.path.join(CONFIG_DIR, filename)

    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    else:
        flash("Configuration file not found.", "error")
        return redirect(url_for('config_form'))

@app.route('/download_journal')
@login_required # Or your custom logic for SINGLE_USER_MODE
def download_journal():
    if SINGLE_USER_MODE:
        filename = "journal_default.yaml"
        # Ensure the user is effectively logged in for single user mode if needed by send_file context
    else:
        if not current_user.is_authenticated: # Should be caught by @login_required
            flash("Please log in to download your journal.", "warning")
            return redirect(url_for('login'))
        username = current_user.username
        filename = f"journal_{username}.yaml"

    # Construct the full path to the file
    # Assuming your journal YAML files are in the same directory as app.py (instance path or app root)
    # If they are in a subdirectory e.g. 'user_data/', adjust the path.
    # For consistency with how load_journal/save_journal build paths:
    filepath = os.path.join(CONFIG_DIR, filename)


    if os.path.exists(filepath):
        try:
            return send_file(filepath, as_attachment=True, download_name=filename)
        except Exception as e:
            print(f"Error sending journal file: {e}")
            flash("Error downloading journal file.", "error")
            return redirect(url_for('config_form')) # Or some other appropriate page
    else:
        flash(f"Journal file '{filename}' not found.", "error")
        # If no journal exists, you could offer to download an empty template,
        # but for now, just an error is fine.
        return redirect(url_for('config_form')) # Redirect back to config form


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

        with open(config_path, 'w') as f:
            yaml.dump(new_config, f, default_flow_style=False)

        print(f"[IMPORT] Overwrote {config_path} successfully with new config.")
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
    objects_config = g.user_config.get("objects", [])
    obj_entry = next((item for item in objects_config if item["Object"].lower() == obj_key), None)

    default_type = "N/A"
    default_magnitude = "N/A"
    default_size = "N/A"
    default_sb = "N/A"
    default_project = "none"
    default_constellation = "N/A"

    if obj_entry:
        ra_str = obj_entry.get("RA")
        dec_str = obj_entry.get("DEC")
        constellation_val = obj_entry.get("Constellation", default_constellation)  # Get existing constellation
        type_val = obj_entry.get("Type", default_type)
        magnitude_val = obj_entry.get("Magnitude", default_magnitude)
        size_val = obj_entry.get("Size", default_size)
        sb_val = obj_entry.get("SB", default_sb)
        project_val = obj_entry.get("Project", default_project)
        common_name_val = obj_entry.get("Name", object_name)

        if ra_str is not None and dec_str is not None:
            try:
                ra_hours_float = float(ra_str)
                dec_degrees_float = float(dec_str)

                if constellation_val in [None, "N/A", ""]:
                    try:
                        coords = SkyCoord(ra=ra_hours_float*u.hourangle, dec=dec_degrees_float*u.deg)
                        constellation_val = get_constellation(coords, short_name=True)
                    except Exception as e:
                        print(f"Constellation calculation failed for {object_name}: {e}")
                        constellation_val = "N/A"

                return {
                    "Object": object_name,
                    "Constellation": constellation_val,  # Add to return dictionary
                    "Common Name": common_name_val,
                    "RA (hours)": ra_hours_float,
                    "DEC (degrees)": dec_degrees_float,
                    "Project": project_val,
                    "Type": type_val if type_val else default_type,
                    "Magnitude": magnitude_val if magnitude_val else default_magnitude,
                    "Size": size_val if size_val else default_size,
                    "SB": sb_val if sb_val else default_sb,
                }
            except ValueError as ve:
                print(f"[ERROR] Failed to parse RA/DEC for {object_name} from config: {ve}")
                return {
                    "Object": object_name,
                    "Constellation": "N/A",
                    "Common Name": f"Error: Invalid RA/DEC in config",
                    "RA (hours)": None, "DEC (degrees)": None,
                    "Project": project_val, "Type": type_val if type_val else default_type,
                    "Magnitude": magnitude_val if magnitude_val else default_magnitude,
                    "Size": size_val if size_val else default_size,
                    "SB": sb_val if sb_val else default_sb,
                }
        else:  # RA/DEC are missing in config for an existing obj_entry
            print(f"[SIMBAD] RA/DEC missing for {object_name} in config. Querying SIMBAD...")
            # Fall through to SIMBAD lookup logic below
            pass  # Explicitly falling through

    # This block handles:
    # 1. Object not in config at all.
    # 2. Object in config but RA/DEC were missing (due to the 'pass' above).
    if not obj_entry or (obj_entry and (obj_entry.get("RA") is None or obj_entry.get("DEC") is None)):
        if not obj_entry:  # Case: Object not found in config
            print(f"[INFO] Object {object_name} not found in config. Attempting SIMBAD lookup.")
        # These values would be from config if obj_entry existed
        common_name_to_use = obj_entry.get("Name", object_name) if obj_entry else object_name
        project_to_use = obj_entry.get("Project", default_project) if obj_entry else default_project
        type_to_use = obj_entry.get("Type", default_type) if obj_entry else default_type
        mag_to_use = obj_entry.get("Magnitude", default_magnitude) if obj_entry else default_magnitude
        size_to_use = obj_entry.get("Size", default_size) if obj_entry else default_size
        sb_to_use = obj_entry.get("SB", default_sb) if obj_entry else default_sb

        try:
            custom_simbad = Simbad()
            custom_simbad.ROW_LIMIT = 1
            custom_simbad.TIMEOUT = 60
            # Explicitly request fields. 'ra' and 'dec' are for J2000 sexagesimal.
            # 'main_id' for a common identifier, 'otype' for object type.
            custom_simbad.add_votable_fields('main_id', 'ra', 'dec', 'otype')

            result = custom_simbad.query_object(object_name)

            if result is None or len(result) == 0:
                raise ValueError(f"No results for object '{object_name}' in SIMBAD.")

            # Crucial Debugging Line:
            # print(f"[SIMBAD DEBUG] Columns for {object_name}: {result.colnames}")

            ra_col, dec_col = None, None
            if 'RA' in result.colnames:
                ra_col = 'RA'
            elif 'ra' in result.colnames:
                ra_col = 'ra'  # Check for lowercase
            else:
                raise ValueError(
                    f"SIMBAD result for '{object_name}' is missing RA column. Available: {result.colnames}")

            if 'DEC' in result.colnames:
                dec_col = 'DEC'
            elif 'dec' in result.colnames:
                dec_col = 'dec'  # Check for lowercase
            else:
                raise ValueError(
                    f"SIMBAD result for '{object_name}' is missing DEC column. Available: {result.colnames}")

            ra_value_simbad = str(result[ra_col][0])
            dec_value_simbad = str(result[dec_col][0])

            ra_hours_simbad = hms_to_hours(ra_value_simbad)
            dec_degrees_simbad = dms_to_degrees(dec_value_simbad)

            simbad_main_id = str(result['MAIN_ID'][0]) if 'MAIN_ID' in result.colnames and result['MAIN_ID'][
                0] else common_name_to_use
            simbad_otype = str(result['OTYPE'][0]) if 'OTYPE' in result.colnames and result['OTYPE'][0] else type_to_use

            try:
                coords = SkyCoord(ra=ra_hours_simbad * u.hourangle, dec=dec_degrees_simbad * u.deg)
                constellation_simbad = get_constellation(coords, short_name=True)
            except Exception:
                constellation_simbad = "N/A"

            # If the object was in config but RA/DEC were missing, update the in-memory entry
            if obj_entry:
                obj_entry["RA"] = ra_hours_simbad
                obj_entry["DEC"] = dec_degrees_simbad
                if obj_entry.get("Name") in [None, "",
                                             object_name] and simbad_main_id != object_name:  # Update common name if generic
                    obj_entry["Name"] = simbad_main_id
                if obj_entry.get("Type") in [None, "", "N/A"] and simbad_otype != "N/A":  # Update type if generic
                    obj_entry["Type"] = simbad_otype

            return {
                "Object": object_name,
                "Constellation": constellation_simbad,
                "Common Name": simbad_main_id if not obj_entry or obj_entry.get("Name") in [None,
                                                                                            ""] else obj_entry.get(
                    "Name"),
                "RA (hours)": ra_hours_simbad,
                "DEC (degrees)": dec_degrees_simbad,
                "Project": project_to_use,
                "Type": simbad_otype if not obj_entry or obj_entry.get("Type") in [None, "", "N/A"] else obj_entry.get(
                    "Type"),
                "Magnitude": mag_to_use,  # These are not fetched by this basic SIMBAD query
                "Size": size_to_use,
                "SB": sb_to_use,
            }

        except Exception as ex:
            # Provide more detailed error including exception type
            error_message = f"Error: SIMBAD lookup failed ({type(ex).__name__}): {str(ex)}"
            print(f"[ERROR] {error_message} for {object_name}")
            return {
                "Object": object_name,
                "Constellation": "N/A",
                "Common Name": error_message,
                "RA (hours)": None, "DEC (degrees)": None,
                "Project": project_to_use, "Type": type_to_use,  # Use defaults or config values if obj_entry existed
                "Magnitude": mag_to_use, "Size": size_to_use, "SB": sb_to_use,
            }

    print(f"[WARN] get_ra_dec: Unhandled case for object {object_name}")
    return {
        "Object": object_name, "Common Name": "Error: Could not determine RA/DEC",
        "RA (hours)": None, "DEC (degrees)": None,
        "Project": default_project, "Type": default_type,
        "Magnitude": default_magnitude, "Size": default_size, "SB": default_sb,
    }

def plot_altitude_curve(object_name, alt_name, ra, dec, lat, lon, local_date, tz_name, selected_location):
    times_local, times_utc = get_common_time_arrays(tz_name, local_date)
    times_local_naive = [t.replace(tzinfo=None) for t in times_local]

    now_local = datetime.now(pytz.timezone(g.tz_name))
    local_tz = pytz.timezone(tz_name)

    # Convert tz-aware local times to naive for plotting.
    times_local_naive = [t.replace(tzinfo=None) for t in times_local]

    # Calculate altitude and azimuth for the object.
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)
    altaz = sky_coord.transform_to(altaz_frame)
    altitudes = altaz.alt.deg
    azimuths = altaz.az.deg

    # Calculate Moon altitude and azimuth.
    moon_altitudes, moon_azimuths = [], []
    for t_utc in times_utc:
        frame = AltAz(obstime=t_utc, location=location)
        moon_coord = get_body('moon', t_utc, location=location)
        moon_altaz = moon_coord.transform_to(frame)
        moon_altitudes.append(moon_altaz.alt.deg)
        moon_azimuths.append(moon_altaz.az.deg)

    # Get sun events.
    sun_events_curr = calculate_sun_events_cached(local_date,g.tz_name, g.lat, g.lon)
    previous_date = (datetime.strptime(local_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    sun_events_prev = calculate_sun_events_cached(previous_date,g.tz_name, g.lat, g.lon)

    # Prepare sun event datetimes.
    event_datetimes = {}
    base_prev = local_tz.localize(datetime.strptime(previous_date, '%Y-%m-%d'))
    for event, t_str in sun_events_prev.items():
        try:
            hour, minute = map(int, t_str.split(':'))
        except Exception:
            continue
        event_dt = base_prev.replace(hour=hour, minute=minute)
        event_datetimes[f'prev_{event}'] = event_dt
    base_curr = local_tz.localize(datetime.strptime(local_date, '%Y-%m-%d'))
    for event, t_str in sun_events_curr.items():
        try:
            hour, minute = map(int, t_str.split(':'))
        except Exception:
            continue
        event_dt = base_curr.replace(hour=hour, minute=minute)
        event_datetimes[f'curr_{event}'] = event_dt

    event_datetimes_naive = {k: v.replace(tzinfo=None) for k, v in event_datetimes.items()}

    # Create figure and primary axis.
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(times_local_naive, altitudes, '-', linewidth=3, color='tab:blue', label=f'{object_name} Altitude')

    #create light gray background
    ax.set_facecolor("lightgray")

    # Plot Moon altitude as dashed yellow.
    ax.plot(times_local_naive, moon_altitudes, '-', color='gold', linewidth=2.5, label='Moon Altitude')

    # Plot Horizon
    ax.axhline(y=0, color='black', linewidth=2, linestyle='-', label='Horizon')

    ax.set_xlabel(f'Time (Local - {selected_location})')
    ax.set_ylabel('Altitude (°)', color='k')
    ax.tick_params(axis='y', labelcolor='k')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    plt.xticks(rotation=45)

    # Fix altitude axis to range from -90 to +90 degrees.
    ax.set_ylim(-90, 90)

    # Add a two-line title.
    ax.set_title(f"Altitude and Azimuth for {object_name} ({alt_name}) on {local_date}", loc='left')

    # Create secondary axis for azimuth.
    ax2 = ax.twinx()
    ax2.plot(times_local_naive, azimuths, '--', linewidth=1.5, color='tab:darkcyan', label=f'{object_name} Azimuth')
    ax2.set_ylabel('Azimuth (°)', color='k')
    ax2.tick_params(axis='y', labelcolor='k')
    ax2.set_ylim(0, 360)
    ax2.spines['right'].set_color('k')
    ax2.spines['right'].set_linewidth(1.5)

    # Add Moon azimuth.
    ax2.plot(times_local_naive, moon_azimuths, '--', linewidth=1.5, color='darkorange', label='Moon Azimuth')

    # Set x-axis limits.
    plot_start = times_local_naive[0]
    plot_end = plot_start + timedelta(hours=24)
    ax.set_xlim(plot_start, plot_end)

    # Define key times correctly
    midnight = datetime.combine(datetime.strptime(local_date, '%Y-%m-%d'), datetime.min.time())
    noon = midnight + timedelta(hours=12)
    previous_midnight = midnight - timedelta(days=1)

    # Define plot_start and plot_end assuming times_local_naive covers the selected day.
    # Define 24-hour plot window
    plot_start = times_local_naive[0]
    plot_end = plot_start + timedelta(hours=24)

# --- Corrected Background Shading and Event Line Logic ---
    ax.set_facecolor("lightgray") # Set default background

    # Define plot start and end clearly (assuming times_local_naive covers the desired 24h)
    plot_start = times_local_naive[0]
    plot_end = times_local_naive[-1] # Use the actual end time from your array

    # Calculate sun events for current and next day
    sun_events_curr = calculate_sun_events_cached(local_date, g.tz_name, g.lat, g.lon)
    next_date_obj = datetime.strptime(local_date, '%Y-%m-%d') + timedelta(days=1)
    next_date_str = next_date_obj.strftime('%Y-%m-%d')
    sun_events_next = calculate_sun_events_cached(next_date_str, g.tz_name, g.lat, g.lon)

    # --- Prepare Event Datetimes with Correct Date Assignment ---
    event_datetimes = {}
    curr_date_obj = datetime.strptime(local_date, '%Y-%m-%d')

    # Helper function to parse and localize time strings safely
    def get_event_datetime(base_date_obj, event_time_str, tz):
        if not event_time_str or ':' not in event_time_str:
            return None # Handle missing or invalid time
        try:
            event_time = datetime.strptime(event_time_str, "%H:%M").time()
            dt_naive = datetime.combine(base_date_obj, event_time)
            return tz.localize(dt_naive)
        except ValueError:
            return None # Handle parsing errors

    # Get key times (handle potential missing events)
    astro_dusk_curr_str = sun_events_curr.get("astronomical_dusk")
    astro_dawn_next_str = sun_events_next.get("astronomical_dawn")
    sunrise_curr_str = sun_events_curr.get("sunrise") # Needed for dusk check
    sunset_curr_str = sun_events_curr.get("sunset")
    sunrise_next_str = sun_events_next.get("sunrise") # Use next day's sunrise for line

    # Determine Astronomical Dusk Datetime (Correcting for post-midnight)
    astro_dusk_naive = None
    if astro_dusk_curr_str and ':' in astro_dusk_curr_str:
        try:
            astro_dusk_time = datetime.strptime(astro_dusk_curr_str, "%H:%M").time()
            # Use sunrise as a reference: if dusk is before sunrise, it's on the next calendar day
            sunrise_curr_time = datetime.strptime(sunrise_curr_str, "%H:%M").time() if sunrise_curr_str and ':' in sunrise_curr_str else datetime.time(6, 0) # Default if sunrise missing

            dusk_date_base = next_date_obj if astro_dusk_time < sunrise_curr_time else curr_date_obj

            astro_dusk_dt_naive = datetime.combine(dusk_date_base, astro_dusk_time)
            astro_dusk_localized = local_tz.localize(astro_dusk_dt_naive)
            astro_dusk_naive = astro_dusk_localized.replace(tzinfo=None)
            event_datetimes["Astronomical dusk"] = astro_dusk_naive
        except ValueError:
            print(f"Warning: Could not parse astronomical dusk time: {astro_dusk_curr_str}")
            astro_dusk_naive = None # Ensure it's None if parsing fails

    # Determine Astronomical Dawn Datetime (Next Day)
    astro_dawn_next_naive = None
    astro_dawn_next_localized = get_event_datetime(next_date_obj, astro_dawn_next_str, local_tz)
    if astro_dawn_next_localized:
        astro_dawn_next_naive = astro_dawn_next_localized.replace(tzinfo=None)
        event_datetimes["Astronomical dawn"] = astro_dawn_next_naive

    # --- Shade Night Time ---
    # Shade only if both dusk and dawn times are valid and dusk < dawn
    if astro_dusk_naive and astro_dawn_next_naive and astro_dusk_naive < astro_dawn_next_naive:
         # Clip shading to plot boundaries
         shade_start = max(plot_start, astro_dusk_naive)
         shade_end = min(plot_end, astro_dawn_next_naive)
         # Only shade if the clipped interval is valid
         if shade_start < shade_end:
              ax.axvspan(shade_start, shade_end, facecolor="white", alpha=1.0, zorder=0) # zorder=0 to draw below plot lines


    # --- Add other event times for vertical lines ---
    sunset_curr_localized = get_event_datetime(curr_date_obj, sunset_curr_str, local_tz)
    if sunset_curr_localized:
        event_datetimes["Sunset"] = sunset_curr_localized.replace(tzinfo=None)

    sunrise_next_localized = get_event_datetime(next_date_obj, sunrise_next_str, local_tz)
    if sunrise_next_localized:
         event_datetimes["Sunrise"] = sunrise_next_localized.replace(tzinfo=None)

    # --- Draw Vertical Lines ---
    for event, dt_naive in event_datetimes.items():
        if dt_naive and plot_start <= dt_naive <= plot_end: # Check dt_naive exists
            ax.axvline(x=dt_naive, color='black', linestyle='-', linewidth=1, alpha=0.7, zorder=1) # zorder=1 to draw above shading
            try:
                # Position text slightly after the line, ensuring it stays within bounds
                text_time = dt_naive + timedelta(minutes=5)
                if text_time > plot_end: # Prevent text going off the right edge
                    text_time = dt_naive - timedelta(minutes=5)
                    ha = 'right'
                else:
                    ha = 'left'

                label_x = mdates.date2num(text_time)
                ymin, ymax = ax.get_ylim()
                label_y = ymin + 0.05 * (ymax - ymin) # Position near bottom
                ax.text(label_x, label_y, event, rotation=90,
                        verticalalignment='bottom', horizontalalignment=ha,
                        fontsize=9, color='dimgray', zorder=2) # zorder=2 to draw above lines
            except Exception as e:
                 print(f"Error plotting text for event {event}: {e}") # Catch potential errors during plotting

    try:
        transit_time_str = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
        if transit_time_str and transit_time_str != "N/A":
            transit_hour, transit_minute = map(int, transit_time_str.split(':'))
            plot_date_obj = datetime.strptime(local_date, '%Y-%m-%d').date()

            # --- KEY LOGIC CHANGE ---
            # If transit is after midnight (e.g., 00:24), assign it to the next calendar day
            # so it correctly falls within the 'night of' the selected date on the plot.
            effective_date = plot_date_obj
            if transit_hour < 12:  # Simple check for any time between 00:00 and 11:59
                effective_date += timedelta(days=1)

            transit_dt_naive = datetime.combine(effective_date, datetime.min.time()) + timedelta(hours=transit_hour,
                                                                                                 minutes=transit_minute)

            # Draw the vertical line and text (this part is now correct)
            ax.axvline(x=transit_dt_naive, color='crimson', linestyle='--', linewidth=1.5, label='Meridian Transit')

            ax.text(mdates.date2num(transit_dt_naive), -88, f" {transit_time_str} ",
                    color='crimson',
                    ha='center', va='bottom',
                    fontsize=8,
                    rotation=90,
                    fontweight='bold',
                    bbox=dict(facecolor='white', alpha=0.9, edgecolor='none', pad=1.5))
    except Exception as e:
        print(f"WARNING: Could not plot meridian line for {object_name}. Reason: {e}")


    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()

    # Combine all labels and handles, then use a dictionary to automatically remove duplicates
    all_labels = labels + labels2
    all_lines = lines + lines2
    unique_entries = dict(zip(all_labels, all_lines))

    # Create the legend from the unique (de-duplicated) entries
    ax.legend(unique_entries.values(), unique_entries.keys(), loc='upper left', bbox_to_anchor=(1.05, 1.0),
              borderaxespad=0.)

    ax.grid(True, linestyle=':', color='dimgray', alpha=1.0)

    plt.tight_layout()

    filename = f"static/{sanitize_object_name(object_name).replace(' ', '_')}_{selected_location.replace(' ', '_')}_altitude_plot.png"
    plt.savefig(filename)
    plt.close(fig)
    return filename


def plot_yearly_altitude_curve(
        object_name, alt_name, ra, dec, lat, lon, tz_name, selected_location, year=2025
):

    local_tz = pytz.timezone(tz_name)

    dates = []
    obj_altitudes = []
    moon_altitudes = []

    # Create an EarthLocation object once, for efficiency
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

    # Loop over each day of the year (Jan 1 through Dec 31)
    start_date = datetime(year, 1, 1)
    end_date = datetime(year + 1, 1, 1)  # up to but not including Jan 1 next year
    delta = timedelta(days=2) #every second day for performance reasons

    current_date = start_date
    while current_date < end_date:
        # Local midnight
        local_midnight = local_tz.localize(
            current_date.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        # Convert local midnight to UTC for astropy
        midnight_utc = local_midnight.astimezone(pytz.utc)
        midnight_time_astropy = Time(midnight_utc.strftime('%Y-%m-%dT%H:%M:%S'), scale='utc')

        # Compute object altitude at local midnight
        altaz_frame = AltAz(obstime=midnight_time_astropy, location=location)
        obj_altaz = sky_coord.transform_to(altaz_frame)
        obj_alt = obj_altaz.alt.deg

        # Compute Moon altitude at local midnight
        moon_coord = get_body('moon', midnight_time_astropy, location=location)
        moon_altaz = moon_coord.transform_to(altaz_frame)
        moon_alt = moon_altaz.alt.deg

        dates.append(local_midnight.replace(tzinfo=None))  # naive for plotting
        obj_altitudes.append(obj_alt)
        moon_altitudes.append(moon_alt)

        current_date += delta

    fig, ax = plt.subplots(figsize=(11, 6))

    # Plot the object altitude
    ax.plot(dates, obj_altitudes, label=f"{object_name} Alt.", color='tab:blue', linewidth=3)

    # Plot the Moon altitude
    ax.plot(dates, moon_altitudes, label="Moon Alt.", color='gold', linestyle='-', linewidth=1.5)

    ax.set_xlabel(f"Date ({year})", fontsize=9)
    ax.set_ylabel("Altitude (°)", fontsize=9)
    ax.set_title(f"Yearly Altitude at Local Midnight for {object_name} ({alt_name}) - {selected_location}", fontsize=12, loc='left')

    # Format x-axis for months
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b'))
    plt.xticks(rotation=45)

    # Plot horizon
    ax.axhline(y=0, color='black', linestyle='-', linewidth=2, label='Horizon', zorder=10)

    ax.set_ylim(-90, 90)
    ax.grid(True)
    ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1))
    plt.tight_layout()

    # Build the filename
    from os.path import join
    filename = f"{sanitize_object_name(object_name)}_{selected_location}_yearly_alt_{year}.png"
    filepath = join("static", filename)
    plt.savefig(filepath)
    plt.close(fig)

    return filepath


def plot_monthly_altitude_curve(
        object_name, alt_name, ra, dec, lat, lon, tz_name, selected_location, year=2025, month=1
):

    local_tz = pytz.timezone(tz_name)
    # Determine the number of days in the month
    num_days = calendar.monthrange(year, month)[1]

    dates = []
    obj_altitudes = []
    moon_altitudes = []

    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

    # Loop over every day in the month
    for day in range(1, num_days + 1):
        current_date = datetime(year, month, day)
        local_midnight = local_tz.localize(current_date.replace(hour=0, minute=0, second=0, microsecond=0))
        midnight_utc = local_midnight.astimezone(pytz.utc)
        time_astropy = Time(midnight_utc.strftime('%Y-%m-%dT%H:%M:%S'), scale='utc')

        altaz_frame = AltAz(obstime=time_astropy, location=location)
        obj_alt = sky_coord.transform_to(altaz_frame).alt.deg

        # Calculate Moon altitude
        moon_coord = get_body('moon', time_astropy, location=location)
        moon_alt = moon_coord.transform_to(altaz_frame).alt.deg

        dates.append(local_midnight.replace(tzinfo=None))  # Naive time for plotting
        obj_altitudes.append(obj_alt)
        moon_altitudes.append(moon_alt)

    # Plotting
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(dates, obj_altitudes, label=f"{object_name} Altitude", color='tab:blue', linewidth=3)
    ax.plot(dates, moon_altitudes, label="Moon Altitude", color='gold', linestyle='-', linewidth=1.5)

    ax.set_xlabel(f"Day of {year}-{month:02d}", fontsize=9)
    ax.set_ylabel("Altitude (°)", fontsize=9)
    ax.set_title(f"Monthly Altitude at Local Midnight for {object_name} ({alt_name}) - {selected_location}", fontsize=12, loc='left')

    # Format the x-axis to show the day of the month
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d'))
    plt.xticks(rotation=45)

    # Plot horizon
    ax.axhline(y=0, color='black', linestyle='-', linewidth=2, label='Horizon', zorder=10)

    ax.set_ylim(-90, 90)
    ax.grid(True)
    ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1))
    plt.tight_layout()

    # Save the plot to a file
    from os.path import join
    filename = f"{sanitize_object_name(object_name)}_{selected_location}_monthly_alt_{year}_{month:02d}.png"
    filepath = join("static", filename)
    plt.savefig(filepath)
    plt.close(fig)

    return filepath

# =============================================================================
# Protected Routes
# =============================================================================

@app.route('/get_locations')
def get_locations():
    return jsonify({"locations": list(g.locations.keys()), "selected": g.selected_location})

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
    object_name = req.get('object')
    common_name = req.get('name')
    ra = req.get('ra')
    dec = req.get('dec')
    project = req.get('project', 'none')
    constellation = req.get('constellation')

    # --- FIX: Use the helper function to clean data before saving ---
    obj_type = convert_to_native_python(req.get('type'))
    magnitude = convert_to_native_python(req.get('magnitude'))
    size = convert_to_native_python(req.get('size'))
    sb = convert_to_native_python(req.get('sb'))

    if not object_name or not common_name:
        return jsonify({"status": "error", "message": "Object ID and name are required."}), 400
    if ra is None or dec is None:
        return jsonify({"status": "error", "message": "RA and DEC are required for the object."}), 400

    config_data = load_user_config(current_user.username)
    objects_list = config_data.setdefault('objects', [])

    existing = next((obj for obj in objects_list if obj["Object"].lower() == object_name.lower()), None)
    if existing:
        existing["Name"] = common_name
        existing["Project"] = project
        existing["RA"] = ra
        existing["DEC"] = dec
        existing["Type"] = obj_type if obj_type is not None else existing.get("Type", "")
        existing["Magnitude"] = magnitude if magnitude is not None else existing.get("Magnitude", "")
        existing["Size"] = size if size is not None else existing.get("Size", "")
        existing["Constellation"] = constellation if constellation is not None else existing.get("Constellation", "N/A")
        existing["SB"] = sb if sb is not None else existing.get("SB", "")
    else:
        new_obj = {
            "Object": object_name,
            "Name": common_name,
            "Project": project,
            "RA": ra,
            "DEC": dec,
            "Constellation": constellation if constellation is not None else "N/A",
            "Type": obj_type if obj_type is not None else "",
            "Magnitude": magnitude if magnitude is not None else "",
            "Size": size if size is not None else "",
            "SB": sb if sb is not None else ""
        }
        objects_list.append(new_obj)

    save_user_config(current_user.username, config_data)
    return jsonify({"status": "success"})

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
    A new endpoint that does the heavy calculation for only ONE object.
    This contains the logic from your old /data endpoint.
    """
    local_tz = pytz.timezone(g.tz_name)
    current_datetime_local = datetime.now(local_tz)

    today_str = current_datetime_local.strftime('%Y-%m-%d')
    dawn_today_str = calculate_sun_events_cached(today_str, g.tz_name, g.lat, g.lon).get("astronomical_dawn")
    local_date = today_str
    if dawn_today_str:
        try:
            dawn_today_dt = local_tz.localize(
                datetime.combine(current_datetime_local.date(), datetime.strptime(dawn_today_str, "%H:%M").time()))
            if current_datetime_local < dawn_today_dt:
                local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            pass

    # --- NEW: Load horizon mask for the current location ---
    current_location_config = g.locations.get(g.selected_location, {})
    horizon_mask = current_location_config.get("horizon_mask")
    # --- END NEW ---

    altitude_threshold = g.user_config.get("altitude_threshold", 20)
    sampling_interval = g.user_config.get('sampling_interval_minutes', 15)
    obj_details = get_ra_dec(object_name)

    if not obj_details or obj_details.get("RA (hours)") is None:
        return jsonify({"error": "Object data not found"}), 404

    ra = obj_details["RA (hours)"]
    dec = obj_details["DEC (degrees)"]
    cache_key = f"{object_name.lower()}_{local_date}_{g.selected_location}"

    # We will force a recalculation for now to test the new logic.
    # Later, we can make the cache smarter.
    # if cache_key in nightly_curves_cache:
    #    del nightly_curves_cache[cache_key]

    if cache_key not in nightly_curves_cache:
        times_local, times_utc = get_common_time_arrays(g.tz_name, local_date, sampling_interval)
        location = EarthLocation(lat=g.lat * u.deg, lon=g.lon * u.deg)
        sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        altaz_frame = AltAz(obstime=times_utc, location=location)
        altitudes = sky_coord.transform_to(altaz_frame).alt.deg
        azimuths = sky_coord.transform_to(altaz_frame).az.deg
        transit_time = calculate_transit_time(ra, dec, g.lat, g.lon, g.tz_name, local_date)

        # --- MODIFIED: Pass the horizon_mask to the calculation function ---
        obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
            ra, dec, g.lat, g.lon, local_date, g.tz_name, altitude_threshold, sampling_interval,
            horizon_mask=horizon_mask
        )
        # --- END MODIFIED ---

        fixed_time_utc_str = get_utc_time_for_local_11pm(g.tz_name)
        alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, fixed_time_utc_str)
        nightly_curves_cache[cache_key] = {
            "times_local": times_local, "altitudes": altitudes, "azimuths": azimuths, "transit_time": transit_time,
            "obs_duration_minutes": int(obs_duration.total_seconds() / 60) if obs_duration else 0,
            "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
            "alt_11pm": f"{alt_11pm:.2f}", "az_11pm": f"{az_11pm:.2f}"
        }

    cached_night_data = nightly_curves_cache[cache_key]
    now_utc = datetime.now(pytz.utc)
    time_diffs = [abs((t - now_utc).total_seconds()) for t in cached_night_data["times_local"]]
    current_index = np.argmin(time_diffs)
    current_alt = cached_night_data["altitudes"][current_index]
    current_az = cached_night_data["azimuths"][current_index]

    next_alt = cached_night_data["altitudes"][min(current_index + 1, len(cached_night_data["altitudes"]) - 1)]
    trend = '–'
    if abs(next_alt - current_alt) > 0.01:
        trend = '↑' if next_alt > current_alt else '↓'

    time_obj = Time(datetime.now(pytz.utc))
    location = EarthLocation(lat=g.lat * u.deg, lon=g.lon * u.deg)
    moon_coord = get_body('moon', time_obj, location)
    obj_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    frame = AltAz(obstime=time_obj, location=location)
    angular_sep = obj_coord_sky.transform_to(frame).separation(moon_coord.transform_to(frame)).deg

    single_object_data = {
        'Object': obj_details['Object'], 'Common Name': obj_details['Common Name'],
        'Altitude Current': f"{current_alt:.2f}", 'Azimuth Current': f"{current_az:.2f}", 'Trend': trend,
        'Altitude 11PM': cached_night_data['alt_11pm'], 'Azimuth 11PM': cached_night_data['az_11pm'],
        'Transit Time': cached_night_data['transit_time'],
        'Observable Duration (min)': cached_night_data['obs_duration_minutes'],
        'Max Altitude (°)': cached_night_data['max_altitude'], 'Angular Separation (°)': round(angular_sep),
        'Project': obj_details.get('Project', "none"), 'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
        'Constellation': obj_details.get('Constellation', 'N/A'), 'Type': obj_details.get('Type', 'N/A'),
        'Magnitude': obj_details.get('Magnitude', 'N/A'), 'Size': obj_details.get('Size', 'N/A'),
        'SB': obj_details.get('SB', 'N/A'),
    }
    return jsonify(single_object_data)
@app.route('/')
def index():
    if SINGLE_USER_MODE:
        username = "default"
        # g.is_guest is likely set to False by your before_request that logs in a dummy user
    elif current_user.is_authenticated:
        username = current_user.username
    else:
        # If guests can view the index page but have no specific journal,
        # or if you have a 'journal_guest.yaml'.
        # For now, assuming a guest might get an empty journal or one named 'guest_user'.
        # Your @app.before_request loads config for 'guest_user' if not authenticated,
        # so we can try to load a journal for 'guest_user'.
        username = "guest_user"

    journal_data = load_journal(username)  # Your function to load journal YAML
    sessions = journal_data.get('sessions', [])

    # --- Add target_common_name and calculated_integration_time to each session ---
    # This assumes g.user_config is populated by your @app.before_request
    # and contains the 'objects' list.
    objects_from_config = []
    if hasattr(g, 'user_config') and g.user_config and "objects" in g.user_config:
        objects_from_config = g.user_config.get("objects", [])

    object_names_lookup = {
        obj.get("Object"): obj.get("Name", obj.get("Object"))  # Fallback to Object ID if Name is missing
        for obj in objects_from_config if obj.get("Object")  # Ensure obj has an "Object" key
    }
    for session_entry in sessions:
        target_id = session_entry.get('target_object_id')
        if target_id:
            # Look up the common name, default to 'N/A' if not found
            common_name = object_names_lookup.get(target_id, 'N/A')
            session_entry['target_common_name'] = common_name
        else:
            session_entry['target_common_name'] = 'N/A'

    # Sort sessions by date descending by default for the journal tab initial view
    try:
        sessions.sort(key=lambda s: s.get('session_date', '1900-01-01'), reverse=True)
    except Exception as e:
        print(f"Warning: Could not sort journal sessions by date in index route: {e}")

    return render_template('index.html',
                           journal_sessions=sessions
                           # Pass any other variables your index.html template already expects.
                           # For example, if your base.html or index.html uses 'is_guest':
                           # is_guest = g.is_guest if hasattr(g, 'is_guest') else (not current_user.is_authenticated and not SINGLE_USER_MODE)
                           )


@app.route('/sun_events')
def sun_events():
    local_tz = pytz.timezone(g.tz_name)
    now_local = datetime.now(local_tz)
    local_date = now_local.strftime('%Y-%m-%d')

    # Calculate sun events
    events = calculate_sun_events_cached(local_date, g.tz_name, g.lat, g.lon)

    # Calculate moon phase
    moon = ephem.Moon()
    observer = ephem.Observer()
    observer.lat = str(g.lat)
    observer.lon = str(g.lon)
    observer.date = now_local.astimezone(pytz.utc)
    moon.compute(observer)

    # Add all data to the response
    events["date"] = local_date
    events["time"] = now_local.strftime('%H:%M')
    events["phase"] = round(moon.phase, 1)

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
    updated = False

    if request.method == 'POST':
        try:
            if 'submit_general' in request.form:
                new_altitude_threshold = request.form.get('altitude_threshold')
                new_default_location = request.form.get('default_location', g.user_config.get("default_location", ""))

                try:
                    threshold_value = int(new_altitude_threshold)
                    if threshold_value < 0 or threshold_value > 90:
                        raise ValueError("Altitude threshold must be between 0 and 90 degrees.")
                    g.user_config['altitude_threshold'] = threshold_value
                except ValueError as ve:
                    error = str(ve)  # Capture error but continue if possible

                g.user_config[
                    'default_location'] = new_default_location if new_default_location else "Singapore"  # Default from original

                g.user_config['sampling_interval_minutes'] = int(request.form.get("sampling_interval", 15))
                # Telemetry checkbox (default True if missing)
                telemetry_enabled = bool(request.form.get('telemetry_enabled'))
                tcfg = g.user_config.setdefault('telemetry', {})
                tcfg['enabled'] = telemetry_enabled
                tcfg.pop('instance_id', None)

                imaging = g.user_config.setdefault("imaging_criteria", {})
                try:
                    imaging["min_observable_minutes"] = int(request.form.get("min_observable_minutes", 60))
                    imaging["min_max_altitude"] = int(request.form.get("min_max_altitude", 30))
                    imaging["max_moon_illumination"] = int(request.form.get("max_moon_illumination", 20))
                    imaging["min_angular_distance"] = int(
                        request.form.get("min_angular_separation", 30))  # name from original
                    imaging["search_horizon_months"] = int(request.form.get("search_horizon_months", 6))
                except ValueError as ve:
                    error = f"Invalid imaging criteria: {ve}" if not error else error + f"; Invalid imaging criteria: {ve}"

                # Only set message and updated if no critical error has stopped us earlier
                # The original code set this regardless of 'error' status for this block.
                # To maintain similar flow, we'll set it, but errors might have occurred.
                message = "Settings updated."
                updated = True

            elif 'submit_new_location' in request.form:
                new_location_name = request.form.get("new_location")
                new_location_lat = request.form.get("new_lat")
                new_location_lon = request.form.get("new_lon")
                new_location_timezone = request.form.get("new_timezone")

                if not new_location_name or not new_location_lat or not new_location_lon or not new_location_timezone:
                    error = "All fields are required to add a new location."
                else:
                    try:
                        lat_val = float(new_location_lat)
                        lon_val = float(new_location_lon)
                        if new_location_timezone not in pytz.all_timezones:
                            raise ValueError("Invalid timezone provided.")
                        g.user_config.setdefault('locations', {})[new_location_name] = {
                            "lat": lat_val,
                            "lon": lon_val,
                            "timezone": new_location_timezone
                        }
                        message = "New location added successfully."
                        updated = True

                        print(f"[CONFIG] New location '{new_location_name}' added. Triggering Outlook cache update.")
                        user_config_for_thread = g.user_config.copy()
                        # Determine the correct username for the thread
                        username_for_thread = "default" if SINGLE_USER_MODE else current_user.username
                        # Create the thread with the correct 3 arguments
                        thread = threading.Thread(target=update_outlook_cache,
                                                  args=(username_for_thread, new_location_name, user_config_for_thread))
                        thread.start()
                    except ValueError as ve:
                        error = f"Invalid input for new location: {ve}"

            elif 'submit_locations' in request.form:
                updated_locations = {}
                changed_in_locations = False
                for loc_key, loc_data in g.user_config.get("locations", {}).items():
                    if request.form.get(f"delete_loc_{loc_key}") == "on":
                        changed_in_locations = True
                        continue  # Skip deletion
                    new_horizon_mask_str = request.form.get(f"horizon_mask_{loc_key}")
                    current_loc_dict = loc_data.copy()
                    new_lat = request.form.get(f"lat_{loc_key}", loc_data.get("lat"))
                    new_lon = request.form.get(f"lon_{loc_key}", loc_data.get("lon"))
                    new_timezone = request.form.get(f"timezone_{loc_key}", loc_data.get("timezone"))

                    potential_new_data = {
                        "lat": float(new_lat) if new_lat is not None else None,  # Ensure conversion
                        "lon": float(new_lon) if new_lon is not None else None,
                        "timezone": new_timezone
                    }
                    if new_horizon_mask_str and new_horizon_mask_str.strip():
                        try:
                            # Parse the YAML string from the textarea into a Python list
                            mask_data = yaml.safe_load(new_horizon_mask_str)
                            if isinstance(mask_data, list):
                                potential_new_data['horizon_mask'] = mask_data
                            else:
                                # Handle case where user enters non-list text
                                print(f"Warning: Invalid horizon mask format for {loc_key}. Ignoring.")
                        except yaml.YAMLError as e:
                            print(f"Warning: Could not parse horizon mask YAML for {loc_key}: {e}")


                    updated_locations[loc_key] = potential_new_data
                    if loc_data != potential_new_data:  # Compare dicts
                        changed_in_locations = True

                if changed_in_locations:
                    g.user_config['locations'] = updated_locations
                    message = "Locations updated."
                    updated = True
                # else: # If no changes, don't necessarily set message/updated status

            elif 'submit_new_object' in request.form:  # Ensure this block is correctly indented
                new_object = request.form.get("new_object")
                new_obj_name = request.form.get("new_name") or ""
                new_type = request.form.get("new_type", "")  # Default to empty string as in original
                new_obj_project = request.form.get("new_project")
                if new_object:
                    g.user_config.setdefault('objects', []).append({
                        "Object": new_object,
                        "Name": new_obj_name,
                        "Project": new_obj_project if new_obj_project else "none",  # Keep original logic
                        "Type": new_type
                        # RA, DEC, Mag, Size, SB are intentionally not set here;
                        # they are fetched by 'check_and_fill_object_data' or 'fetch_all_details'
                    })
                    message = "New object added."
                    updated = True
            # Ensure this 'elif' is at the same indentation level as the one above
            elif 'submit_objects' in request.form:  # <<< THIS IS YOUR LIKELY LINE 1386
                new_objects_list = []
                original_objects_list = g.user_config.get("objects", [])
                made_changes_this_block = False

                for existing_obj_data in original_objects_list:
                    object_key = existing_obj_data.get("Object")

                    if not object_key:
                        new_objects_list.append(existing_obj_data)
                        continue

                    if request.form.get(f"delete_{object_key}") == "on":
                        made_changes_this_block = True
                        continue

                    current_obj_values = existing_obj_data.copy()
                    fields_from_form = {
                        "Name": request.form.get(f"name_{object_key}", current_obj_values.get("Name")),
                        "RA": request.form.get(f"ra_{object_key}", current_obj_values.get("RA")),
                        "DEC": request.form.get(f"dec_{object_key}", current_obj_values.get("DEC")),
                        "Type": request.form.get(f"type_{object_key}", current_obj_values.get("Type")),
                        "Project": request.form.get(f"project_{object_key}", current_obj_values.get("Project")),
                        # Magnitude, Size, SB are preserved because we start with .copy()
                        # and don't try to get them from the form here unless you add input fields for them.
                    }

                    for field_name, new_value in fields_from_form.items():
                        # Check if the value actually changed before marking
                        if current_obj_values.get(field_name) != new_value:
                            current_obj_values[field_name] = new_value
                            made_changes_this_block = True

                    new_objects_list.append(current_obj_values)

                if made_changes_this_block:
                    g.user_config['objects'] = new_objects_list
                    updated = True
                    message = "Objects updated."

            # Centralized save if any 'updated' flag was set to True in any block
            if updated and not error:  # Check for error before saving, or decide how to handle partial saves
                save_user_config(current_user.username, g.user_config)
                trigger_outlook_update_for_user(current_user.username)
                if message:  # If a specific message was set
                    message += " Configuration saved."
                else:  # If no specific message, just say config saved
                    message = "Configuration saved."
                # Clear the in-memory persistent cache.
                # Optionally remove the cache file so that it's rebuilt.
            elif updated and error:
                message = (
                              message if message else "") + f" Configuration partially updated but errors occurred: {error}. Please review."

        except Exception as exception_value:
            # Ensure error is set if an unexpected exception occurs
            error = str(exception_value)
            # A message might already exist from a previous step, or we can set a new one.
            message = (message if message else "") + f" An unexpected error occurred: {exception_value}"

    # Prepare for rendering template
    # Ensure error and message are passed, even if they were modified by an exception
    final_error = error if error else request.args.get("error")
    final_message = message if message else request.args.get("message")

    return render_template('config_form.html', config=g.user_config, locations=g.locations, error=final_error,
                           message=final_message)

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
    new_project = data.get('project')

    if not object_name:
        return jsonify({"status": "error", "error": "Object name is missing."}), 400

    try:
        # Load the current configuration for the user.
        config = load_user_config(current_user.username)
        found = False
        # Locate the object in the objects list.
        for obj in config.get("objects", []):
            if obj.get("Object").lower() == object_name.lower():
                obj["Project"] = new_project
                found = True
                break
        if not found:
            return jsonify({"status": "error", "error": "Object not found in configuration."}), 404

        # Save the updated configuration.
        save_user_config(current_user.username, config)

        trigger_outlook_update_for_user(current_user.username)

        # Optionally update the persistent cache.
        key = object_name.lower()

        return jsonify({"status": "success"})
    except Exception as exception_value:
        return jsonify({"status": "error", "error": str(exception_value)}), 500

@app.before_request
def bypass_login_in_single_user():
    if SINGLE_USER_MODE and not current_user.is_authenticated:
        # Create a dummy user.
        dummy_user = User("default", "default")
        # Log in the dummy user.
        login_user(dummy_user)


@app.route('/plot_yearly_altitude/<path:object_name>')
def plot_yearly_altitude(object_name):
    # --- Determine Year for the plot from request.args ---
    current_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'  # For default year
    now_for_defaults = datetime.now(pytz.timezone(current_tz_name))
    try:
        year = int(request.args.get('year', now_for_defaults.year))
        # Year validation can be added if necessary
    except ValueError:
        year = now_for_defaults.year
        # Optionally flash a message or log if invalid year provided

    # --- Get Object Details ---
    object_details_for_plot = get_ra_dec(object_name)
    if not object_details_for_plot or object_details_for_plot.get('RA (hours)') is None:
        print(f"ERROR: /plot_yearly_altitude - Could not get RA/DEC for object: {object_name}")
        return jsonify({'error': f'Data for object {object_name} not found.'}), 404

    ra_hours = object_details_for_plot['RA (hours)']
    dec_degrees = object_details_for_plot['DEC (degrees)']
    alt_name_for_plot = object_details_for_plot.get("Common Name", object_name)

    # --- Determine Location details for the plot ---
    default_lat = g.lat if hasattr(g, 'lat') else 0.0
    default_lon = g.lon if hasattr(g, 'lon') else 0.0
    default_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    default_location_name = g.selected_location if hasattr(g,
                                                           'selected_location') and g.selected_location else 'Default Location'

    plot_lat_str = request.args.get('plot_lat')
    plot_lon_str = request.args.get('plot_lon')
    plot_lat = float(plot_lat_str) if plot_lat_str is not None else None
    plot_lon = float(plot_lon_str) if plot_lon_str is not None else None
    plot_tz_name = request.args.get('plot_tz', type=str)
    plot_location_display_name = request.args.get('plot_loc_name', type=str)

    final_lat = plot_lat if plot_lat is not None else default_lat
    final_lon = plot_lon if plot_lon is not None else default_lon
    final_tz_name = plot_tz_name if plot_tz_name else default_tz_name
    final_location_name = plot_location_display_name if plot_location_display_name else default_location_name

    # print(f"DEBUG: /plot_yearly_altitude - Plotting for obj='{object_name}', loc='{final_location_name}', year={year}")

    # Call your actual Matplotlib plotting function for yearly view
    filepath = plot_yearly_altitude_curve(  # Your function that generates the image
        object_name=object_name,
        alt_name=alt_name_for_plot,
        ra=ra_hours,
        dec=dec_degrees,
        lat=final_lat,
        lon=final_lon,
        tz_name=final_tz_name,
        selected_location=final_location_name,  # For graph title/filename
        year=year
    )

    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/png')
    else:
        print(f"ERROR: /plot_yearly_altitude - Plot file not found for '{object_name}' at path: {filepath}")
        return jsonify({'error': f'Yearly plot image {os.path.basename(filepath)} not found.'}), 404

@app.route('/plot_monthly_altitude/<path:object_name>')
def plot_monthly_altitude(object_name):
    # --- Determine Year and Month for the plot from request.args ---
    current_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    now_for_defaults = datetime.now(pytz.timezone(current_tz_name))
    try:
        year = int(request.args.get('year', now_for_defaults.year))
        month = int(request.args.get('month', now_for_defaults.month))
        # Basic validation
        if not (1 <= month <= 12):
            month = now_for_defaults.month
        # Year validation can be added if necessary (e.g., range)
    except ValueError:
        year = now_for_defaults.year
        month = now_for_defaults.month
        # Optionally flash a message or log if invalid year/month provided

    # --- Get Object Details ---
    object_details_for_plot = get_ra_dec(object_name)
    if not object_details_for_plot or object_details_for_plot.get('RA (hours)') is None:
        print(f"ERROR: /plot_monthly_altitude - Could not get RA/DEC for object: {object_name}")
        return jsonify({'error': f'Data for object {object_name} not found.'}), 404

    ra_hours = object_details_for_plot['RA (hours)']
    dec_degrees = object_details_for_plot['DEC (degrees)']
    alt_name_for_plot = object_details_for_plot.get("Common Name", object_name)

    # --- Determine Location details for the plot ---
    default_lat = g.lat if hasattr(g, 'lat') else 0.0
    default_lon = g.lon if hasattr(g, 'lon') else 0.0
    default_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    default_location_name = g.selected_location if hasattr(g,
                                                           'selected_location') and g.selected_location else 'Default Location'

    plot_lat_str = request.args.get('plot_lat')
    plot_lon_str = request.args.get('plot_lon')
    plot_lat = float(plot_lat_str) if plot_lat_str is not None else None
    plot_lon = float(plot_lon_str) if plot_lon_str is not None else None
    plot_tz_name = request.args.get('plot_tz', type=str)
    plot_location_display_name = request.args.get('plot_loc_name', type=str)

    final_lat = plot_lat if plot_lat is not None else default_lat
    final_lon = plot_lon if plot_lon is not None else default_lon
    final_tz_name = plot_tz_name if plot_tz_name else default_tz_name
    final_location_name = plot_location_display_name if plot_location_display_name else default_location_name

    print(
        f"DEBUG: /plot_monthly_altitude - Plotting for obj='{object_name}', loc='{final_location_name}', year={year}, month={month}")

    # Call your actual Matplotlib plotting function for monthly view
    filepath = plot_monthly_altitude_curve(  # Your function that generates the image
        object_name=object_name,
        alt_name=alt_name_for_plot,
        ra=ra_hours,
        dec=dec_degrees,
        lat=final_lat,
        lon=final_lon,
        tz_name=final_tz_name,
        selected_location=final_location_name,  # For graph title/filename
        year=year,
        month=month
    )

    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/png')
    else:
        print(f"ERROR: /plot_monthly_altitude - Plot file not found for '{object_name}' at path: {filepath}")
        return jsonify({'error': f'Monthly plot image {os.path.basename(filepath)} not found.'}), 404


@app.route('/plot/<object_name>')
def plot_altitude(object_name):
    # print("DEBUG: request.args =", request.args)
    data = get_ra_dec(object_name)
    if data:
        if data['RA (hours)'] is None or data['DEC (degrees)'] is None:
            return jsonify({"error": f"Graph not available: {data.get('Project', 'No data')}"}), 400
        project = data.get('Project', "none")
        alt_name = data.get("Common Name", object_name)

        now_local = datetime.now(pytz.timezone(g.tz_name))
        day_str = request.args.get('day')
        month_str = request.args.get('month')
        year_str = request.args.get('year')

        try:
            day = int(day_str) if day_str and day_str.strip() != "" else now_local.day
        except ValueError:
            day = now_local.day

        try:
            month = int(month_str) if month_str and month_str.strip() != "" else now_local.month
        except ValueError:
            month = now_local.month

        try:
            year = int(year_str) if year_str and year_str.strip() != "" else now_local.year
        except ValueError:
            year = now_local.year

        local_date = f"{year}-{month:02d}-{day:02d}"
        # print("DEBUG: Plotting for date:", local_date)  # Debug print

        filepath = plot_altitude_curve(
            object_name,
            alt_name,
            data['RA (hours)'],
            data['DEC (degrees)'],
            g.lat, g.lon,
            local_date,
            g.tz_name,
            g.selected_location
        )
        if os.path.exists(filepath):
            return render_template('graph.html',
                                   object_name=object_name,
                                   project=project,
                                   filename=os.path.basename(filepath),
                                   timestamp=datetime.now().timestamp(),
                                   date=local_date,
                                   location=g.selected_location,
                                   selected_month=month,
                                   selected_year=year)
        else:
            return jsonify({'error': 'Plot not found'}), 404
    return jsonify({"error": "Object not found"}), 404


@app.route('/graph_dashboard/<path:object_name>')
def graph_dashboard(object_name):
    # --- Initialize effective context with global defaults/URL args ---
    effective_location_name = g.selected_location if hasattr(g,
                                                             'selected_location') and g.selected_location else "Unknown"
    effective_lat = g.lat if hasattr(g, 'lat') else 0.0  # Ensure float for calcs
    effective_lon = g.lon if hasattr(g, 'lon') else 0.0  # Ensure float for calcs
    effective_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'

    now_at_effective_location = datetime.now(pytz.timezone(effective_tz_name))

    effective_day = now_at_effective_location.day
    effective_month = now_at_effective_location.month
    effective_year = now_at_effective_location.year

    # Override with URL args if present
    if request.args.get('day'):
        try:
            effective_day = int(request.args.get('day'))
        except ValueError:
            pass
    if request.args.get('month'):
        try:
            effective_month = int(request.args.get('month'))
        except ValueError:
            pass
    if request.args.get('year'):
        try:
            effective_year = int(request.args.get('year'))
        except ValueError:
            pass

    # --- Journal Data Logic ---
    if SINGLE_USER_MODE:
        username_for_journal = "default"
    elif current_user.is_authenticated:
        username_for_journal = current_user.username
    else:
        username_for_journal = "guest_user"  # Or handle as per your guest policy

    object_specific_sessions = []
    selected_session_data = None
    requested_session_id = request.args.get('session_id')

    if username_for_journal:
        journal_data = load_journal(username_for_journal)
        all_user_sessions = journal_data.get('sessions', [])

        object_specific_sessions = [s for s in all_user_sessions if s.get('target_object_id') == object_name]
        object_specific_sessions.sort(key=lambda s: s.get('session_date', '1900-01-01'), reverse=True)

        if requested_session_id:
            selected_session_data = next(
                (s for s in object_specific_sessions if s.get('session_id') == requested_session_id), None)
            if selected_session_data:
                session_date_str = selected_session_data.get('session_date')
                if session_date_str:
                    try:
                        session_date_obj = datetime.strptime(session_date_str, '%Y-%m-%d')
                        effective_day = session_date_obj.day
                        effective_month = session_date_obj.month
                        effective_year = session_date_obj.year
                    except ValueError:
                        flash(f"Invalid date in session. Using current/URL date.", "warning")

                session_loc_name = selected_session_data.get('location_name')
                if session_loc_name:
                    all_locations_config = g.user_config.get("locations", {})
                    session_loc_details = all_locations_config.get(session_loc_name)
                    if session_loc_details:
                        effective_lat = session_loc_details.get('lat', effective_lat)
                        effective_lon = session_loc_details.get('lon', effective_lon)
                        effective_tz_name = session_loc_details.get('timezone', effective_tz_name)
                        effective_location_name = session_loc_name
                        now_at_effective_location = datetime.now(pytz.timezone(effective_tz_name))
                    else:
                        flash(f"Location '{session_loc_name}' from session not in config. Using default.", "warning")
            elif requested_session_id:
                flash(f"Requested session ID '{requested_session_id}' not found for this object.", "info")

    # --- Finalize effective date string and dependent calculations ---
    try:
        if not (1 <= effective_month <= 12): effective_month = now_at_effective_location.month
        max_days_in_month = calendar.monthrange(effective_year, effective_month)[1]
        if not (1 <= effective_day <= max_days_in_month):
            effective_day = now_at_effective_location.day if effective_month == now_at_effective_location.month and effective_year == now_at_effective_location.year else 1
        effective_date_obj = datetime(effective_year, effective_month, effective_day)
        effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
    except ValueError:
        effective_date_obj = now_at_effective_location.date()
        effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
        effective_day, effective_month, effective_year = effective_date_obj.day, effective_date_obj.month, effective_date_obj.year
        flash("Invalid date components, defaulting to today.", "warning")

    effective_local_tz = pytz.timezone(effective_tz_name)
    try:
        dt_for_moon_naive = datetime(effective_year, effective_month, effective_day, 12, 0, 0)
        dt_for_moon_local = effective_local_tz.localize(dt_for_moon_naive)
        moon_phase_for_effective_date = round(ephem.Moon(dt_for_moon_local.astimezone(pytz.utc)).phase, 0)
    except Exception as e:
        print(f"Error calculating moon phase for {effective_date_str} at {effective_location_name}: {e}")
        moon_phase_for_effective_date = "N/A"

    sun_events_for_effective_date = calculate_sun_events_cached(effective_date_str, effective_tz_name, effective_lat,
                                                                effective_lon)

    # <<< THIS ENTIRE BLOCK for loading and sorting rigs is the final, corrected version >>>
    # Step 1: Load the full rig config to get both rigs and preferences
    username_for_rigs = "default" if SINGLE_USER_MODE else (
        current_user.username if current_user.is_authenticated else "guest_user")
    full_rig_config = rig_config.load_rig_config(username_for_rigs, SINGLE_USER_MODE)

    # Step 2: Get the raw list of rigs and the sort preference from the config data
    unsorted_rigs = full_rig_config.get('rigs', [])
    sort_preference = full_rig_config.get('ui_preferences', {}).get('sort_order', 'name-asc')

    # Step 3: Calculate data for each rig
    rigs_with_calculated_data = []
    if unsorted_rigs:
        all_components = full_rig_config.get('components', {})
        for rig in unsorted_rigs:
            calculated_data = calculate_rig_data(rig, all_components)
            rig.update(calculated_data)
            rigs_with_calculated_data.append(rig)

    # Step 4: Sort the final list
    rigs_with_fov = sort_rigs_list(rigs_with_calculated_data, sort_preference)
    # <<< END OF REPLACEMENT BLOCK >>>

    object_main_details = get_ra_dec(object_name)
    if not object_main_details or object_main_details.get("RA (hours)") is None:
        flash(f"Details for '{object_name}' could not be found.", "error")
        return redirect(url_for('index'))

    # DEBUG statements have been removed for clarity in the final version

    return render_template('graph_view.html',
                           object_name=object_name,
                           alt_name=object_main_details.get("Common Name", object_name),
                           object_main_details=object_main_details,
                           available_rigs=rigs_with_fov,
                           selected_day=effective_day,
                           selected_month=effective_month,
                           selected_year=effective_year,
                           selected_date_for_display=effective_date_str,
                           header_location_name=effective_location_name,
                           header_date_display=effective_date_str,
                           header_moon_phase=moon_phase_for_effective_date,
                           header_astro_dusk=sun_events_for_effective_date.get("astronomical_dusk", "N/A"),
                           header_astro_dawn=sun_events_for_effective_date.get("astronomical_dawn", "N/A"),
                           project_notes_from_config=object_main_details.get("Project", "N/A"),
                           timestamp=datetime.now(effective_local_tz).timestamp(),
                           object_specific_sessions=object_specific_sessions,
                           selected_session_data=selected_session_data,
                           current_session_id=requested_session_id if selected_session_data else None,
                           graph_location_name_param=effective_location_name,
                           graph_lat_param=effective_lat,
                           graph_lon_param=effective_lon,
                           graph_tz_name_param=effective_tz_name
                           )

@app.route('/plot_day/<path:object_name>')
def plot_day(object_name):
    # --- Determine Date for the plot from request.args ---
    # Get timezone from global context 'g', default to UTC if not available
    current_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    now_for_defaults = datetime.now(pytz.timezone(current_tz_name))

    try:
        day = int(request.args.get('day', now_for_defaults.day))
        month = int(request.args.get('month', now_for_defaults.month))
        year = int(request.args.get('year', now_for_defaults.year))

        # Basic validation for month and day
        if not (1 <= month <= 12): month = now_for_defaults.month
        # Use calendar.monthrange to get the actual number of days in the selected month and year
        max_days_in_month = calendar.monthrange(year, month)[1]
        if not (1 <= day <= max_days_in_month):
            # If current month/year, default to current day, else default to 1st day of selected month
            day = now_for_defaults.day if month == now_for_defaults.month and year == now_for_defaults.year else 1

        local_date_for_plot = f"{year}-{month:02d}-{day:02d}"
    except ValueError:  # Handles cases like non-integer input for day/month/year
        local_date_for_plot = now_for_defaults.strftime('%Y-%m-%d')
        # If date parsing fails, you might want to log this or ensure defaults are robustly set
        # For now, this ensures local_date_for_plot always has a value.

    # --- Get Object Details ---
    object_details_for_plot = get_ra_dec(object_name)  # Your existing function
    if not object_details_for_plot or object_details_for_plot.get('RA (hours)') is None:
        print(f"ERROR: /plot_day - Could not get RA/DEC for object: {object_name}")
        # Consider returning a standard "image not available" placeholder or a 404 HTTP error
        return jsonify({'error': f'Data for object {object_name} not found.'}), 404

    ra_hours = object_details_for_plot['RA (hours)']
    dec_degrees = object_details_for_plot['DEC (degrees)']
    alt_name_for_plot = object_details_for_plot.get("Common Name", object_name)

    # --- Determine Location details for the plot ---
    # These g values are set by @app.before_request based on user's general/global selection
    default_lat = g.lat if hasattr(g, 'lat') else 0.0  # Provide a float default
    default_lon = g.lon if hasattr(g, 'lon') else 0.0  # Provide a float default
    default_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    default_location_name = g.selected_location if hasattr(g,
                                                           'selected_location') and g.selected_location else 'Default Location'

    # Try to get plot-specific location parameters from the URL query string
    plot_lat_str = request.args.get('plot_lat')
    plot_lon_str = request.args.get('plot_lon')

    plot_lat = float(plot_lat_str) if plot_lat_str is not None else None
    plot_lon = float(plot_lon_str) if plot_lon_str is not None else None
    plot_tz_name = request.args.get('plot_tz', type=str)  # Will be None if not provided
    plot_location_display_name = request.args.get('plot_loc_name', type=str)  # Will be None if not provided

    # Use provided plot parameters if they exist and are valid, otherwise use defaults from g
    final_lat = plot_lat if plot_lat is not None else default_lat
    final_lon = plot_lon if plot_lon is not None else default_lon
    final_tz_name = plot_tz_name if plot_tz_name else default_tz_name  # Use default if None or empty
    final_location_name = plot_location_display_name if plot_location_display_name else default_location_name

    print(
        f"DEBUG: /plot_day - Plotting for obj='{object_name}', loc='{final_location_name}', lat={final_lat}, lon={final_lon}, tz='{final_tz_name}', date='{local_date_for_plot}'")

    # Call your actual Matplotlib plotting function
    filepath = plot_altitude_curve(  # This is your function that generates the image
        object_name=object_name,
        alt_name=alt_name_for_plot,  # Common name for the graph title
        ra=ra_hours,
        dec=dec_degrees,
        lat=final_lat,  # Pass the final determined latitude
        lon=final_lon,  # Pass the final determined longitude
        local_date=local_date_for_plot,  # Date for the plot
        tz_name=final_tz_name,  # Pass the final determined timezone name
        selected_location=final_location_name  # Pass the final location name (for graph title/filename)
    )

    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/png')
    else:
        print(
            f"ERROR: /plot_day - Plot file not found or could not be generated for '{object_name}' at path: {filepath}")
        # You could return a placeholder "error image" or a 404 HTTP error
        return jsonify({'error': f'Plot image {os.path.basename(filepath)} not found on server.'}), 404

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


@app.route('/get_imaging_opportunities/<object_name>')
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
    min_sep = criteria["min_angular_distance"]
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
        ics_content = str(c)
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
    # Use the new central function to get the correct path
    filepath = rig_config.get_rig_config_path(username, SINGLE_USER_MODE)

    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath))
    else:
        flash("Rigs configuration file not found.", "error")
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
    """
    # --- 1. Get object and date/location parameters ---
    data = get_ra_dec(object_name)
    if not data or data.get('RA (hours)') is None:
        return jsonify({"error": "Object data not found or invalid."}), 404

    ra = data['RA (hours)']
    dec = data['DEC (degrees)']

    plot_lat_str = request.args.get('plot_lat', g.lat)
    plot_lon_str = request.args.get('plot_lon', g.lon)
    plot_tz_name = request.args.get('plot_tz', g.tz_name)

    try:
        lat = float(plot_lat_str)
        lon = float(plot_lon_str)
        local_tz = pytz.timezone(plot_tz_name)
    except (ValueError, pytz.UnknownTimeZoneError):
        return jsonify({"error": "Invalid location or timezone data."}), 400

    now_local = datetime.now(local_tz)
    day = int(request.args.get('day', now_local.day))
    month = int(request.args.get('month', now_local.month))
    year = int(request.args.get('year', now_local.year))
    local_date = f"{year}-{month:02d}-{day:02d}"

    # --- 2. Perform all necessary astronomical calculations ---
    times_local, times_utc = get_common_time_arrays(plot_tz_name, local_date)
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)
    altitudes = sky_coord.transform_to(altaz_frame).alt.deg
    azimuths = sky_coord.transform_to(altaz_frame).az.deg

    moon_altitudes, moon_azimuths = [], []
    for t_utc in times_utc:
        frame = AltAz(obstime=t_utc, location=location)
        moon_coord = get_body('moon', t_utc, location)
        moon_altaz = moon_coord.transform_to(frame)
        moon_altitudes.append(moon_altaz.alt.deg)
        moon_azimuths.append(moon_altaz.az.deg)

    sun_events_curr = calculate_sun_events_cached(local_date, plot_tz_name, lat, lon)
    next_date_obj = datetime.strptime(local_date, '%Y-%m-%d') + timedelta(days=1)
    next_date_str = next_date_obj.strftime('%Y-%m-%d')
    sun_events_next = calculate_sun_events_cached(next_date_str, plot_tz_name, lat, lon)
    transit_time_str = calculate_transit_time(ra, dec, lat, lon, plot_tz_name, local_date)

    # --- 3. Package data for JSON, FORCING the 24-hour range ---

    # Define the exact start and end times for the 24-hour window
    start_time = times_local[0]
    end_time = start_time + timedelta(hours=24)

    # Create the final time labels array, bookended by the exact start and end times
    final_times_iso = [start_time.isoformat()] + [t.isoformat() for t in times_local] + [end_time.isoformat()]

    # Create the final data arrays, adding 'None' at the start and end.
    # 'None' becomes 'null' in JSON, which tells the chart to create a gap, not draw a line.
    final_object_alt = [None] + list(altitudes) + [None]
    final_object_az = [None] + list(azimuths) + [None]
    final_moon_alt = [None] + moon_altitudes + [None]
    final_moon_az = [None] + moon_azimuths + [None]

    plot_data = {
        "times": final_times_iso,
        "object_alt": final_object_alt,
        "object_az": final_object_az,
        "moon_alt": final_moon_alt,
        "moon_az": final_moon_az,
        "sun_events": {
            "current": sun_events_curr,
            "next": sun_events_next,
        },
        "transit_time": transit_time_str,
        "date": local_date,
        "timezone": plot_tz_name
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

