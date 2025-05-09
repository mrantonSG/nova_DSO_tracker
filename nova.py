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
import requests
import secrets
from dotenv import load_dotenv
import calendar

import pytz
import ephem
import yaml
import shutil
import subprocess
import sys
import time
from modules.config_validation import validate_config

import matplotlib
matplotlib.use('Agg')  # Use a non-GUI backend for headless servers
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from flask import render_template, jsonify, request, send_file, redirect, url_for, flash, g
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from flask import session, get_flashed_messages, Blueprint
from flask import Flask, send_from_directory

from astroquery.simbad import Simbad
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u

from modules.astro_calculations import (
    calculate_transit_time,
    get_utc_time_for_local_11pm,
    is_decimal,
    parse_ra_dec,
    hms_to_hours,
    dms_to_degrees,
    ra_dec_to_alt_az,
    calculate_max_observable_altitude,
    calculate_altitude_curve,
    get_common_time_arrays,
    # ephem_to_local,
    # calculate_sun_events,
    calculate_sun_events_cached,
    calculate_observable_duration_vectorized
)

from modules import nova_data_fetcher

# =============================================================================
# Flask and Flask-Login Setup
# =============================================================================

APP_VERSION = "2.7.0"

SINGLE_USER_MODE = config('SINGLE_USER_MODE',  default='True') == 'True'

load_dotenv()
static_cache = {}
moon_separation_cache = {}
config_cache = {}
config_mtime = {}
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config_default.yaml")
ENV_FILE = ".env"
STELLARIUM_ERROR_MESSAGE = os.getenv("STELLARIUM_ERROR_MESSAGE")
# Automatically create .env if it doesn't exist
if not os.path.exists(ENV_FILE):
    secret_key = secrets.token_hex(32)

    default_user = "admin"
    default_password = "admin123"

    with open(ENV_FILE, "w") as f:
        f.write(f"SECRET_KEY={secret_key}\n")
        f.write(f"USERS={default_user}\n")  # Add default user
        f.write(f"USER_{default_user.upper()}_ID={default_user}\n")
        f.write(f"USER_{default_user.upper()}_USERNAME={default_user}\n")
        f.write(f"USER_{default_user.upper()}_PASSWORD={default_password}\n")

    print(f"Created .env file with a new SECRET_KEY and default user")

# Load SECRET_KEY and users from the .env file
SECRET_KEY = config('SECRET_KEY', default=secrets.token_hex(32))  # Ensure a fallback key

app = Flask(__name__)
app.secret_key = SECRET_KEY

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = None

# Load user credentials dynamically from .env
usernames = config("USERS", default="").split(",")

users = {}

for username in usernames:
    username = username.strip()
    if username:
        user_id = config(f"USER_{username.upper()}_ID", default=username)
        user_username = config(f"USER_{username.upper()}_USERNAME", default=username)
        user_password = config(f"USER_{username.upper()}_PASSWORD", default="changeme")

        users[username] = {
            "id": user_id,
            "username": user_username,
            "password": user_password,
        }

class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    for user in users.values():
        if user["id"] == user_id:
            return User(user["id"], user["username"])
    return None

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
    observable_duration, max_altitude = calculate_observable_duration_vectorized(
        ra, dec, lat, lon, local_date, tz_name, altitude_threshold
    )

    static_cache[key] = {
        "Altitude 11PM": alt_11pm,
        "Azimuth 11PM": az_11pm,
        "Transit Time": transit_time,
        "Observable Duration (min)": int(observable_duration.total_seconds() / 60),
        "Max Altitude (°)": round(max_altitude, 1) if max_altitude is not None else "N/A"
    }
    return static_cache[key]

@app.route('/trigger_update', methods=['POST'])
def trigger_update():
    try:
        script_path = os.path.join(os.path.dirname(__file__), 'updater.py')
        subprocess.Popen([sys.executable, script_path])
        print("Exiting current app to allow updater to restart it...")
        os._exit(0)  # Force exit without cleanup
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

def load_user_config(username):
    global config_cache, config_mtime
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{username}.yaml"
    filepath = os.path.join(os.path.dirname(__file__), filename)

    try:
        # Check if the file has changed or if it's not in the cache
        if filepath not in config_cache or not os.path.exists(filepath) or (os.path.exists(filepath) and os.path.getmtime(filepath) > config_mtime.get(filepath, 0)):
            if not os.path.exists(filepath):
                print(f"⚠️ Config file '{filename}' not found. Creating from default.")
                try:
                    shutil.copy("config_default.yaml", filename)
                except FileNotFoundError:
                    print("❌ ERROR: Default config file 'config_default.yaml' is missing!")
                    config_cache[filepath] = {}  # Return empty config to prevent crashes
                    config_mtime[filepath] = 0
                    return {}
                except Exception as e:
                    print(f"❌ ERROR: Failed to create user config: {e}")
                    config_cache[filepath] = {}
                    config_mtime[filepath] = 0
                    return {}

            with open(filepath, "r") as file:
                config_cache[filepath] = yaml.safe_load(file) or {}
            if os.path.exists(filepath):
                config_mtime[filepath] = os.path.getmtime(filepath)
            else:
                config_mtime[filepath] = 0  # Or some default value

            print(f"[LOAD CONFIG] Loading (and caching) {filename}")
        else:
            print(f"[LOAD CONFIG] Loading from cache: {filename}")
        return config_cache[filepath]

    except Exception as e:
        print(f"❌ ERROR: Failed to load user config: {e}")
        return {}  # Return empty config to prevent crashes


def save_user_config(username, config_data):
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{username}.yaml"
    with open(filename, "w") as file:
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        get_flashed_messages(with_categories=True)  # clear old messages

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user_record = users.get(username)
        if user_record and user_record['password'] == password:
            user = User(user_record['id'], user_record['username'])
            login_user(user)
            flash("Logged in successfully!", "success")
            return redirect(url_for('index'))
        else:
            flash("Invalid username or password.", "error")

    return render_template('login.html')

@app.route('/proxy_focus', methods=['POST'])
def proxy_focus():
    payload = request.form
    try:
        r = requests.post("http://localhost:8090/api/main/focus", data=payload)
        return jsonify({"status": "success", "stellarium_response": r.text})
    except Exception as e:
        user_ip = request.remote_addr
        if user_ip == "127.0.0.1" or user_ip == "localhost":
            message = "Stellarium is not running or remote control is not enabled."
        else:
            message = STELLARIUM_ERROR_MESSAGE or "Could not connect to Stellarium."
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
def download_config():
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{current_user.username}.yaml"

    filepath = os.path.join(os.getcwd(), filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    else:
        return "Configuration file not found.", 404


@app.route('/import_config', methods=['POST'])
def import_config():
    try:
        if 'file' not in request.files:
            return "No file uploaded", 400

        file = request.files['file']
        contents = file.read().decode('utf-8')

        # Parse YAML safely
        new_config = yaml.safe_load(contents)

        # Validate using Cerberus via the helper function
        valid, errors = validate_config(new_config)
        if not valid:
            error_message = f"Configuration validation failed: {errors}"
            print("[IMPORT ERROR]", error_message)
            return error_message, 400

        # Determine the correct config file for this user
        username = current_user.username
        config_path = os.path.join(os.path.dirname(__file__), f"config_{username}.yaml")

        # Backup current config if it exists
        if os.path.exists(config_path):
            backup_dir = os.path.join(os.path.dirname(config_path), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(backup_dir, f"{username}_backup_{timestamp}.yaml")
            shutil.copy(config_path, backup_path)
            print(f"[IMPORT] Backed up current config to {backup_path}")

        # Save new config if valid
        with open(config_path, 'w') as f:
            yaml.dump(new_config, f, default_flow_style=False)

        print(f"[IMPORT] Overwrote {config_path} successfully with new config.")
        return redirect(url_for('config_form', message="Config imported successfully!"))

    except Exception as e:
        print(f"[IMPORT ERROR] {e}")
        return redirect(url_for('config_form', error=f"Import failed: {str(e)}"))

# =============================================================================
# Astronomical Calculations
# =============================================================================


def get_ra_dec(object_name):
    # obj_key is already lowercase, which is good for matching
    obj_key = object_name.lower()
    objects_config = g.user_config.get("objects", [])
    # Find the object's entry in the loaded YAML configuration
    obj_entry = next((item for item in objects_config if item["Object"].lower() == obj_key), None)

    # Default values for the new fields if not found in config
    default_type = "N/A"
    default_magnitude = "N/A"  # Using string "N/A" for consistency if data is missing
    default_size = "N/A"
    default_sb = "N/A"
    default_project = "none"  # Your existing default

    if obj_entry:
        # Get RA and DEC. If they exist, try to parse them.
        ra_str = obj_entry.get("RA")
        dec_str = obj_entry.get("DEC")

        # Retrieve additional properties directly from the config entry
        # Use .get() with a default value if the key might be missing
        type_val = obj_entry.get("Type", default_type)
        magnitude_val = obj_entry.get("Magnitude", default_magnitude)
        size_val = obj_entry.get("Size", default_size)  # Assuming 'Size' is the key in YAML for arcminutes
        sb_val = obj_entry.get("SB", default_sb)
        project_val = obj_entry.get("Project", default_project)
        common_name_val = obj_entry.get("Name", object_name)  # Use object_name as fallback for common name

        if ra_str is not None and dec_str is not None:
            try:
                # Ensure RA/DEC are floats. If they are already floats, this is fine.
                # If they are strings that need conversion, this handles it.
                ra_hours_float = float(ra_str)
                dec_degrees_float = float(dec_str)

                return {
                    "Object": object_name,  # Return original case for Object ID
                    "Common Name": common_name_val,
                    "RA (hours)": ra_hours_float,
                    "DEC (degrees)": dec_degrees_float,
                    "Project": project_val,
                    "Type": type_val if type_val else default_type,  # Ensure "N/A" if empty string
                    "Magnitude": magnitude_val if magnitude_val else default_magnitude,
                    "Size": size_val if size_val else default_size,
                    "SB": sb_val if sb_val else default_sb,
                }
            except ValueError as ve:
                print(f"[ERROR] Failed to parse RA/DEC for {object_name} from config: {ve}")
                # Return with error in Common Name, but still include other available config fields
                return {
                    "Object": object_name,
                    "Common Name": f"Error: Invalid RA/DEC in config",
                    "RA (hours)": None,
                    "DEC (degrees)": None,
                    "Project": project_val,
                    "Type": type_val if type_val else default_type,
                    "Magnitude": magnitude_val if magnitude_val else default_magnitude,
                    "Size": size_val if size_val else default_size,
                    "SB": sb_val if sb_val else default_sb,
                }
        else:
            # RA/DEC are missing in config, attempt SIMBAD lookup (existing logic)
            # This part should ideally not fetch Type, Mag, Size, SB from SIMBAD here
            # to keep this function focused on what's in the config or basic RA/DEC.
            # For now, we'll keep your existing SIMBAD logic but ensure it doesn't overwrite Type, Mag etc.
            # if they were already found (though in this branch, RA/DEC were missing).
            print(f"[SIMBAD] RA/DEC missing for {object_name} in config. Querying SIMBAD...")
            Simbad.TIMEOUT = 60
            Simbad.ROW_LIMIT = 1
            try:
                result = Simbad.query_object(object_name)
                if result is None or len(result) == 0:
                    raise ValueError(f"No results for object '{object_name}' in SIMBAD.")

                # SIMBAD results are ByteStrings, need conversion
                ra_value_simbad = result["RA"][0].decode('utf-8') if isinstance(result["RA"][0], bytes) else \
                result["RA"][0]
                dec_value_simbad = result["DEC"][0].decode('utf-8') if isinstance(result["DEC"][0], bytes) else \
                result["DEC"][0]

                ra_hours_simbad = hms_to_hours(ra_value_simbad)
                dec_degrees_simbad = dms_to_degrees(dec_value_simbad)

                # Update the obj_entry in memory (and it will be saved if config is saved later)
                obj_entry["RA"] = ra_hours_simbad
                obj_entry["DEC"] = dec_degrees_simbad
                # Do NOT update Type, Mag, Size, SB here from SIMBAD in this flow.
                # That's handled by check_and_fill_object_data or fetch_object_details.

                # It's better to save config explicitly when changes are made,
                # not implicitly within a get function.
                # save_user_config(current_user.username, g.user_config) # Consider removing save from here

                return {
                    "Object": object_name,
                    "Common Name": common_name_val,  # Use name from config if available
                    "RA (hours)": ra_hours_simbad,
                    "DEC (degrees)": dec_degrees_simbad,
                    "Project": project_val,
                    "Type": type_val if type_val else default_type,  # From config
                    "Magnitude": magnitude_val if magnitude_val else default_magnitude,  # From config
                    "Size": size_val if size_val else default_size,  # From config
                    "SB": sb_val if sb_val else default_sb,  # From config
                }
            except Exception as ex:
                error_message = f"Error: SIMBAD lookup failed: {str(ex)}"
                print(f"[ERROR] {error_message} for {object_name}")
                return {
                    "Object": object_name,
                    "Common Name": error_message,
                    "RA (hours)": None,
                    "DEC (degrees)": None,
                    "Project": project_val,  # Still return project from config if object entry existed
                    "Type": type_val if obj_entry and type_val else default_type,
                    "Magnitude": magnitude_val if obj_entry and magnitude_val else default_magnitude,
                    "Size": size_val if obj_entry and size_val else default_size,
                    "SB": sb_val if obj_entry and sb_val else default_sb,
                }
    else:  # Object not in config at all
        print(f"[INFO] Object {object_name} not found in config. Attempting SIMBAD lookup for RA/DEC only.")
        # SIMBAD lookup for basic RA/DEC if object is entirely new
        Simbad.TIMEOUT = 60
        Simbad.ROW_LIMIT = 1
        try:
            result = Simbad.query_object(object_name)
            if result is None or len(result) == 0:
                raise ValueError(f"No results for object '{object_name}' in SIMBAD.")

            ra_value_simbad = result["RA"][0].decode('utf-8') if isinstance(result["RA"][0], bytes) else result["RA"][0]
            dec_value_simbad = result["DEC"][0].decode('utf-8') if isinstance(result["DEC"][0], bytes) else \
            result["DEC"][0]

            ra_hours_simbad = hms_to_hours(ra_value_simbad)
            dec_degrees_simbad = dms_to_degrees(dec_value_simbad)

            # This object isn't in the config, so we can't save it here.
            # The config form is the place to add new objects.
            # For the /data endpoint, we just provide what we can.
            return {
                "Object": object_name,
                "Common Name": object_name,  # Default to object_name as common name
                "RA (hours)": ra_hours_simbad,
                "DEC (degrees)": dec_degrees_simbad,
                "Project": default_project,  # Default project
                "Type": default_type,  # Default Type
                "Magnitude": default_magnitude,  # Default Magnitude
                "Size": default_size,  # Default Size
                "SB": default_sb,  # Default SB
            }
        except Exception as ex:
            error_message = f"Error: SIMBAD lookup failed: {str(ex)}"
            print(f"[ERROR] {error_message} for {object_name}")
            return {
                "Object": object_name,
                "Common Name": error_message,
                "RA (hours)": None,
                "DEC (degrees)": None,
                "Project": default_project,
                "Type": default_type,
                "Magnitude": default_magnitude,
                "Size": default_size,
                "SB": default_sb,
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
    ax2.plot(times_local_naive, azimuths, '--', linewidth=1.5, color='tab:cyan', label=f'{object_name} Azimuth')
    ax2.set_ylabel('Azimuth (°)', color='k')
    ax2.tick_params(axis='y', labelcolor='k')
    ax2.set_ylim(0, 360)
    ax2.spines['right'].set_color('k')
    ax2.spines['right'].set_linewidth(1.5)

    # Add Moon azimuth.
    ax2.plot(times_local_naive, moon_azimuths, '--', linewidth=1.5, color='gold', label='Moon Azimuth')

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


    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()

    # Changed bbox_to_anchor x-value from 1.03 to 1.05
    ax.legend(lines + lines2, labels + labels2, loc='upper left', bbox_to_anchor=(1.05, 1.0), borderaxespad=0.)

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
                        try:
                            if isinstance(new_value_from_fetcher, float):
                                if config_key == "Magnitude":
                                    new_value_formatted = round(new_value_from_fetcher, 2)
                                elif config_key == "Size":
                                    new_value_formatted = round(new_value_from_fetcher, 2)
                                elif config_key == "SB":
                                    new_value_formatted = round(new_value_from_fetcher, 2)
                                else:
                                    new_value_formatted = new_value_from_fetcher
                            else:
                                new_value_formatted = str(new_value_from_fetcher).strip()
                        except ValueError:
                            print(
                                f"    [WARN] Could not format fetched value '{new_value_from_fetcher}' for {config_key} of {object_name}. Storing as is or placeholder.")
                            new_value_formatted = placeholder_on_fetch_failure

                        current_config_value = obj_entry.get(config_key)
                        should_update = False
                        if current_config_value in refetch_trigger_values or \
                                (isinstance(current_config_value,
                                            str) and current_config_value.strip().lower() == 'none'):
                            should_update = True  # Always update if current value is a trigger
                        elif current_config_value != new_value_formatted:
                            should_update = True  # Update if different from existing valid value

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
        return jsonify({
            "status": "success",
            "data": {
                "Type":      fetched.get("object_type")        or "",
                "Magnitude": fetched.get("magnitude")          or "",
                "Size":      fetched.get("size_arcmin")        or "",
                "SB":        fetched.get("surface_brightness") or ""
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

    if not object_name or not common_name:
        return jsonify({"status": "error", "message": "Object ID and name are required."}), 400

    config_data = load_user_config(current_user.username)
    objects_list = config_data.setdefault('objects', [])

    existing = next((obj for obj in objects_list if obj["Object"].lower() == object_name.lower()), None)
    if existing:
        existing["Name"] = common_name
        existing["Project"] = project
        existing["RA"] = ra
        existing["DEC"] = dec
    else:
        new_obj = {
            "Object": object_name,
            "Name": common_name,
            "Project": project,
            "Type": "",
            "RA": ra,
            "DEC": dec
        }
        objects_list.append(new_obj)

    save_user_config(current_user.username, config_data)
    return jsonify({"status": "success"})


from datetime import datetime, timedelta

@app.route('/data')
def get_data():
    local_tz = pytz.timezone(g.tz_name)
    current_datetime_local = datetime.now(local_tz)
    local_date = current_datetime_local.strftime('%Y-%m-%d')
    current_time_utc = current_datetime_local.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S')
    fixed_time_utc_str = get_utc_time_for_local_11pm(g.tz_name)
    altitude_threshold = g.user_config.get("altitude_threshold", 20)

    object_data_list = [] # Renamed to avoid conflict with 'data' variable from get_ra_dec
    prev_alts = session.get('previous_altitudes', {})

    # g.objects is a list of object names from the config
    for obj_name_from_config in g.objects:
        # Call the modified get_ra_dec to get all details (RA/DEC + Type, Mag, etc. from config)
        # The 'data' variable here will now hold the extended dictionary
        obj_details = get_ra_dec(obj_name_from_config)

        if not obj_details or obj_details.get("RA (hours)") is None or obj_details.get("DEC (degrees)") is None:
            # Handle cases where essential RA/DEC are missing even after get_ra_dec logic
            object_data_list.append({
                'Object': obj_name_from_config,
                'Common Name': obj_details.get("Common Name", "Error: RA/DEC lookup failed"),
                'RA (hours)': "N/A",
                'DEC (degrees)': "N/A",
                'Altitude Current': 100, # Or some other error indicator
                'Azimuth Current': "N/A",
                'Altitude 11PM': "N/A",
                'Azimuth 11PM': "N/A",
                'Transit Time': "N/A",
                'Observable Duration (min)': "N/A",
                'Trend': "N/A",
                'Project': obj_details.get('Project', "none"),
                'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
                'Max Altitude (°)': "N/A",
                'Angular Separation (°)': "N/A",
                # Add placeholders for new fields in error case too
                'Type': obj_details.get('Type', 'N/A'),
                'Magnitude': obj_details.get('Magnitude', 'N/A'),
                'Size': obj_details.get('Size', 'N/A'),
                'SB': obj_details.get('SB', 'N/A'),
            })
            continue

        try:
            ra = obj_details["RA (hours)"] # Already float from get_ra_dec
            dec = obj_details["DEC (degrees)"] # Already float from get_ra_dec

            # --- Compute live values ---
            alt_current, az_current = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, current_time_utc)

            # --- Get 11PM/transit/obs/max from static_cache ---
            # Using obj_name_from_config for cache key consistency
            cache_key = f"{obj_name_from_config.lower()}_{local_date}_{g.selected_location}"
            if cache_key in static_cache:
                cached_positional = static_cache[cache_key]
            else:
                alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, fixed_time_utc_str)
                transit_time = calculate_transit_time(ra, g.lat, g.lon, g.tz_name)
                # altitude_threshold already defined above
                obs_duration, max_alt = calculate_observable_duration_vectorized(
                    ra, dec, g.lat, g.lon, local_date, g.tz_name, altitude_threshold
                )
                cached_positional = {
                    'Altitude 11PM': alt_11pm,
                    'Azimuth 11PM': az_11pm,
                    'Transit Time': transit_time,
                    'Observable Duration (min)': int(obs_duration.total_seconds() / 60) if obs_duration else 0,
                    'Max Altitude (°)': round(max_alt, 1) if max_alt is not None else "N/A"
                }
                static_cache[cache_key] = cached_positional

            # --- Moon angular separation (hourly cache) ---
            current_hour_str = current_datetime_local.strftime('%Y-%m-%d_%H')
            moon_key = f"{obj_name_from_config.lower()}_{current_hour_str}_{g.selected_location}"

            if moon_key in moon_separation_cache:
                angular_sep = moon_separation_cache[moon_key]
            else:
                location = EarthLocation(lat=g.lat * u.deg, lon=g.lon * u.deg)
                time_obj = Time(current_time_utc, format='isot', scale='utc')
                moon_coord = get_body('moon', time_obj, location=location)
                moon_altaz = moon_coord.transform_to(AltAz(obstime=time_obj, location=location))

                obj_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg) # Renamed to avoid conflict
                obj_altaz_sky = obj_coord_sky.transform_to(AltAz(obstime=time_obj, location=location))

                angular_sep = obj_altaz_sky.separation(moon_altaz).deg
                moon_separation_cache[moon_key] = round(angular_sep, 1)


            # --- Trend ---
            prev_alt = prev_alts.get(obj_name_from_config)
            trend = '→' # Default trend
            if prev_alt is not None: # Check if there's a previous altitude
                if alt_current > prev_alt:
                    trend = '↑'
                elif alt_current < prev_alt:
                    trend = '↓'
            prev_alts[obj_name_from_config] = alt_current


            # Append all data, including new fields from obj_details
            object_data_list.append({
                'Object': obj_details['Object'], # From get_ra_dec
                'Common Name': obj_details['Common Name'], # From get_ra_dec
                'RA (hours)': ra, # Already float
                'DEC (degrees)': dec, # Already float
                'Altitude Current': alt_current,
                'Azimuth Current': az_current,
                'Altitude 11PM': cached_positional['Altitude 11PM'],
                'Azimuth 11PM': cached_positional['Azimuth 11PM'],
                'Transit Time': cached_positional['Transit Time'],
                'Observable Duration (min)': cached_positional['Observable Duration (min)'],
                'Max Altitude (°)': cached_positional['Max Altitude (°)'],
                'Angular Separation (°)': round(angular_sep) if angular_sep is not None else "N/A",
                'Trend': trend,
                'Project': obj_details.get('Project', "none"), # From get_ra_dec
                'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
                # NEWLY ADDED FIELDS (from obj_details, which gets them from config)
                'Type': obj_details.get('Type', 'N/A'),
                'Magnitude': obj_details.get('Magnitude', 'N/A'),
                'Size': obj_details.get('Size', 'N/A'),
                'SB': obj_details.get('SB', 'N/A'),
            })

        except Exception as e:
            print(f"[ERROR processing object {obj_name_from_config} in /data]: {e}")
            # Append with error indication if something goes wrong during processing an object
            object_data_list.append({
                'Object': obj_name_from_config,
                'Common Name': f"Error processing: {e}",
                # ... fill other fields with N/A or defaults ...
                'Type': 'N/A', 'Magnitude': 'N/A', 'Size': 'N/A', 'SB': 'N/A',
            })


    session['previous_altitudes'] = prev_alts
    # Sort by current altitude, ensuring 'Altitude Current' is float for sorting
    sorted_objects = sorted(
        object_data_list,
        key=lambda x: float(x['Altitude Current']) if isinstance(x.get('Altitude Current'), (int, float, str)) and str(x.get('Altitude Current')).replace('.', '', 1).isdigit() else -float('inf'),
        reverse=True
    )
    response = jsonify({
        "date": local_date,
        "time": current_datetime_local.strftime('%H:%M:%S'),
        "phase": round(ephem.Moon(current_datetime_local).phase, 0),
        "altitude_threshold": altitude_threshold,
        "objects": sorted_objects
    })

    cache_timeout = 60
    response.headers['Cache-Control'] = f'public, max-age={cache_timeout}'
    response.headers['Expires'] = (datetime.now(timezone.utc) + timedelta(seconds=cache_timeout)).strftime('%a, %d %b %Y %H:%M:%S GMT')

    return response

@app.route('/sun_events')
def sun_events():
    local_date = datetime.now(pytz.timezone(g.tz_name)).strftime('%Y-%m-%d')
    events = calculate_sun_events_cached(local_date,g.tz_name, g.lat, g.lon)
    events["date"] = local_date
    return jsonify(events)

@app.route('/')
def index():
    return render_template('index.html')

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
                    error = str(ve)

                g.user_config['default_location'] = new_default_location if new_default_location else "Singapore"

                #  NEW: Save imaging criteria from form
                imaging = g.user_config.setdefault("imaging_criteria", {})
                try:
                    imaging["min_observable_minutes"] = int(request.form.get("min_observable_minutes", 60))
                    imaging["min_max_altitude"] = int(request.form.get("min_max_altitude", 30))
                    imaging["max_moon_illumination"] = int(request.form.get("max_moon_illumination", 20))
                    imaging["min_angular_distance"] = int(request.form.get("min_angular_separation", 30))
                    imaging["search_horizon_months"] = int(request.form.get("search_horizon_months", 6))
                except ValueError as ve:
                    error = f"Invalid imaging criteria: {ve}"

                message = "Settings updated."
                updated = True

            elif 'submit_new_location' in request.form:
                new_location_name = request.form.get("new_location")
                new_location_lat = request.form.get("new_lat")
                new_location_lon = request.form.get("new_lon")
                new_location_timezone = request.form.get("new_timezone")

                # Validate inputs before adding
                if not new_location_name or not new_location_lat or not new_location_lon or not new_location_timezone:
                    error = "All fields are required to add a new location."
                else:
                    try:
                        lat_val = float(new_location_lat)
                        lon_val = float(new_location_lon)

                        # Check if timezone is valid
                        if new_location_timezone not in pytz.all_timezones:
                            raise ValueError("Invalid timezone provided.")

                        # Input looks good; save location
                        g.user_config.setdefault('locations', {})[new_location_name] = {
                            "lat": lat_val,
                            "lon": lon_val,
                            "timezone": new_location_timezone
                        }
                        message = "New location added successfully."
                        updated = True

                    except ValueError as ve:
                        error = f"Invalid input: {ve}"

            elif 'submit_locations' in request.form:
                updated_locations = {}
                for loc_key, loc_data in g.user_config.get("locations", {}).items():
                    if request.form.get(f"delete_loc_{loc_key}") == "on":
                        continue
                    new_lat = request.form.get(f"lat_{loc_key}", loc_data.get("lat"))
                    new_lon = request.form.get(f"lon_{loc_key}", loc_data.get("lon"))
                    new_timezone = request.form.get(f"timezone_{loc_key}", loc_data.get("timezone"))
                    updated_locations[loc_key] = {
                        "lat": float(new_lat),
                        "lon": float(new_lon),
                        "timezone": new_timezone
                    }
                g.user_config['locations'] = updated_locations
                message = "Locations updated."
                updated = True

            elif 'submit_new_object' in request.form:
                new_object = request.form.get("new_object")
                new_obj_name = request.form.get("new_name") or ""  # default to empty string if not provided
                new_type = request.form.get("new_type", "")
                new_obj_project = request.form.get("new_project")
                if new_object:  # only require the object identifier
                    g.user_config.setdefault('objects', []).append({
                        "Object": new_object,
                        "Name": new_obj_name,  # even if empty, it gets stored
                        "Project": new_obj_project if new_obj_project else "none",
                        "Type": new_type
                    })
                    message = "New object added."
                    updated = True

            elif 'submit_objects' in request.form:
                updated_objects = []
                for obj in g.user_config.get("objects", []):
                    object_key = obj.get("Object")
                    if request.form.get(f"delete_{object_key}") == "on":
                        continue
                    new_name = request.form.get(f"name_{object_key}", obj.get("Name"))
                    new_ra = request.form.get(f"ra_{object_key}", obj.get("RA"))
                    new_dec = request.form.get(f"dec_{object_key}", obj.get("DEC"))
                    new_type = request.form.get(f"type_{object_key}", obj.get("Type"))
                    new_project = request.form.get(f"project_{object_key}", obj.get("Project"))
                    updated_obj = {
                        "Object": object_key,
                        "Name": new_name,
                        "RA": new_ra,
                        "DEC": new_dec,
                        "Type": new_type,
                        "Project": new_project
                    }
                    updated_objects.append(updated_obj)
                g.user_config['objects'] = updated_objects
                save_user_config(current_user.username, g.user_config)

            if updated:
                save_user_config(current_user.username, g.user_config)
                # Clear the in-memory persistent cache.
                                # Optionally remove the cache file so that it's rebuilt.
                message += " Configuration saved."

        except Exception as exception_value:
            error = str(exception_value)

    if not error:
        error = request.args.get("error")
    if not message:
        message = request.args.get("message")

    return render_template('config_form.html', config=g.user_config, locations=g.locations, error=error, message=message)


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

    data = get_ra_dec(object_name)
    if not data or data['RA (hours)'] is None or data['DEC (degrees)'] is None:
        return jsonify({"error": f"No valid RA/DEC for object {object_name}."}), 400

    alt_name = data.get("Common Name", object_name)
    ra = data['RA (hours)']
    dec = data['DEC (degrees)']

    year = request.args.get('year', default=2025, type=int)

    plot_path = plot_yearly_altitude_curve(
        object_name=object_name,
        alt_name=alt_name,
        ra=ra,
        dec=dec,
        lat=g.lat,
        lon=g.lon,
        tz_name=g.tz_name,
        selected_location=g.selected_location,
        year=year
    )

    # Return the plot file directly or embed in a template
    return send_file(plot_path, mimetype='image/png')


@app.route('/plot_monthly_altitude/<path:object_name>')
def plot_monthly_altitude(object_name):
    data = get_ra_dec(object_name)
    if not data or data['RA (hours)'] is None or data['DEC (degrees)'] is None:
        return jsonify({"error": f"No valid RA/DEC for object {object_name}."}), 400

    alt_name = data.get("Common Name", object_name)
    ra = data['RA (hours)']
    dec = data['DEC (degrees)']

    import datetime
    now = datetime.datetime.now(pytz.timezone(g.tz_name))
    year = request.args.get('year', default=now.year, type=int)
    month = request.args.get('month', default=now.month, type=int)
    # Validate month: if invalid (<1 or >12), use current month.
    if month < 1 or month > 12:
        month = now.month

    plot_path = plot_monthly_altitude_curve(
        object_name=object_name,
        alt_name=alt_name,
        ra=ra,
        dec=dec,
        lat=g.lat,
        lon=g.lon,
        tz_name=g.tz_name,
        selected_location=g.selected_location,
        year=year,
        month=month
    )

    return send_file(plot_path, mimetype='image/png')


@app.route('/plot/<object_name>')
def plot_altitude(object_name):
    print("DEBUG: request.args =", request.args)
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
        print("DEBUG: Plotting for date:", local_date)  # Debug print

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


@app.route('/graph_dashboard/<object_name>')
def graph_dashboard(object_name):
    current_location_name = g.selected_location or "Unknown"

    tz = pytz.timezone(g.tz_name)
    now = datetime.now(tz)

    try:
        selected_day = int(request.args.get('day') or now.day)
    except ValueError:
        selected_day = now.day

    try:
        selected_month = int(request.args.get('month') or now.month)
    except ValueError:
        selected_month = now.month
    if selected_month < 1 or selected_month > 12:
        selected_month = now.month

    try:
        selected_year = int(request.args.get('year') or now.year)
    except ValueError:
        selected_year = now.year
    if selected_year < 1:
        selected_year = now.year

    selected_date_str = f"{selected_year}-{selected_month:02d}-{selected_day:02d}"
    selected_date = tz.localize(datetime.strptime(selected_date_str, "%Y-%m-%d"))

    # Moon phase
    now_local = datetime.now(pytz.timezone(g.tz_name))
    phase = round(ephem.Moon(now_local).phase, 0)

    # Sun events
    sun_events = calculate_sun_events_cached(selected_date_str,g.tz_name, g.lat, g.lon)

    project = get_ra_dec(object_name).get("Project", "none")
    timestamp = now.timestamp()

    return render_template('graph_view.html',
                           object_name=object_name,
                           selected_day=selected_day,
                           selected_month=selected_month,
                           selected_year=selected_year,
                           selected_date=selected_date_str,
                           project=project,
                           filename="your_daily_graph_filename.png",  # adjust if needed
                           timestamp=timestamp,
                           date=selected_date_str,
                           time=selected_date.strftime('%H:%M:%S'),
                           phase=phase,
                           astronomical_dawn=sun_events.get("astronomical_dawn", "N/A"),
                           astronomical_dusk=sun_events.get("astronomical_dusk", "N/A"),
                           location_name=current_location_name)


@app.route('/plot_day/<object_name>')
def plot_day(object_name):

    # 1. Parse query parameters for day, month, year
    now_local = datetime.now(pytz.timezone(g.tz_name))
    day_str = request.args.get('day')
    month_str = request.args.get('month')
    year_str = request.args.get('year')

    try:
        day = int(day_str) if day_str and day_str.strip() else now_local.day
    except ValueError:
        day = now_local.day

    try:
        month = int(month_str) if month_str and month_str.strip() else now_local.month
    except ValueError:
        month = now_local.month

    try:
        year = int(year_str) if year_str and year_str.strip() else now_local.year
    except ValueError:
        year = now_local.year

    local_date = f"{year}-{month:02d}-{day:02d}"
    print("DEBUG: Plotting day graph for date:", local_date)

    # 2. Fetch RA/DEC from config or SIMBAD
    data = get_ra_dec(object_name)
    if not data or data.get('RA (hours)') is None:
        return jsonify({"error": "No valid RA/DEC for object"}), 400

    # 3. Generate the plot (same code as monthly/yearly approach)
    alt_name = data.get("Common Name", object_name)
    plot_path = plot_altitude_curve(
        object_name,
        alt_name,
        data['RA (hours)'],
        data['DEC (degrees)'],
        g.lat, g.lon,
        local_date,
        g.tz_name,
        g.selected_location
    )

    # 4. Return the PNG directly
    return send_file(plot_path, mimetype='image/png')

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
        obs_duration, max_altitude = calculate_observable_duration_vectorized(
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
            "max_alt": round(max_altitude, 1),
            "moon_illumination": round(moon_phase, 1),
            "moon_separation": round(separation, 1),
            "rating": star_string
        })

    return jsonify({"status": "success", "object": object_name, "alt_name": alt_name, "results": final_results})

# =============================================================================
# Main Entry Point
# =============================================================================
if __name__ == '__main__':
    # Automatically disable debugger and reloader if set by the updater
    disable_debug = os.environ.get("NOVA_NO_DEBUG") == "1"

    app.run(
        debug=not disable_debug,
        use_reloader=not disable_debug,
        host='0.0.0.0',
        port=5001
    )

