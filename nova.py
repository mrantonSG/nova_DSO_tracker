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
from datetime import datetime, timedelta
from decouple import config
import requests
import secrets
from dotenv import load_dotenv
import calendar

import numpy as np
import pytz
import ephem
import yaml
import shutil
import subprocess
import sys

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
# =============================================================================
# Flask and Flask-Login Setup
# =============================================================================
APP_VERSION = "2.4.7"

SINGLE_USER_MODE = False  # Set to False for multi‑user mode

load_dotenv()
static_cache = {}
moon_separation_cache = {}
update_bp = Blueprint('update', __name__)
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
def inject_version():
    return dict(version=APP_VERSION)

@app.context_processor
def inject_user_mode():
    from flask_login import current_user
    return {
        "SINGLE_USER_MODE": SINGLE_USER_MODE,
        "current_user": current_user
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
    observable_duration, max_altitude = calculate_observable_duration_vectorized(ra, dec, lat, lon, local_date, tz_name)

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
    """Load user-specific configuration, creating one from the default if missing."""
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{username}.yaml"

    # If user config is missing, create it from the default
    if not os.path.exists(filename):
        print(f"⚠️ Config file '{filename}' not found. Creating from default.")
        try:
            shutil.copy("config_default.yaml", filename)
        except FileNotFoundError:
            print("❌ ERROR: Default config file 'config_default.yaml' is missing!")
            return {}  # Return empty config to prevent crashes
        except Exception as e:
            print(f"❌ ERROR: Failed to create user config: {e}")
            return {}

    # Load and return the YAML configuration
    with open(filename, "r") as file:
        config = yaml.safe_load(file) or {}

    # Ensure imaging_criteria exists with all defaults
    config.setdefault("imaging_criteria", {})
    config["imaging_criteria"].setdefault("min_observable_minutes", 60)
    config["imaging_criteria"].setdefault("min_max_altitude", 30)
    config["imaging_criteria"].setdefault("max_moon_illumination", 20)
    config["imaging_criteria"].setdefault("min_angular_distance", 30)
    config["imaging_criteria"].setdefault("search_horizon_months", 6)

    print(f"[LOAD CONFIG] Loading {filename}")
    return config


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
    # In single-user mode, always load config_default.yaml.
    if SINGLE_USER_MODE:
        g.user_config = load_user_config("default")
    elif current_user.is_authenticated:
        g.user_config = load_user_config(current_user.username)
    else:
        g.user_config = {}
    # Then populate the other variables (locations, objects, etc.)
    g.locations = g.user_config.get("locations", {})
    g.selected_location = g.user_config.get("default_location", "")
    g.altitude_threshold = g.user_config.get("altitude_threshold", 20)
    loc_config = g.locations.get(g.selected_location, {})
    g.lat = loc_config.get("lat")
    g.lon = loc_config.get("lon")
    g.tz_name = loc_config.get("timezone", "UTC")
    g.objects_list = g.user_config.get("objects", [])
    g.alternative_names = {obj.get("Object").lower(): obj.get("Name") for obj in g.objects_list}
    g.projects = {obj.get("Object").lower(): obj.get("Project") for obj in g.objects_list}
    g.objects = [obj.get("Object") for obj in g.objects_list]

if not os.path.exists('static'):
    os.makedirs('static')

def get_common_time_arrays(tz_name, local_date):
    local_tz = pytz.timezone(tz_name)
    base_date = datetime.strptime(local_date, '%Y-%m-%d')
    # Corrected to start from noon of the selected local date itself
    start_time = local_tz.localize(datetime.combine(base_date, datetime.min.time()).replace(hour=12))
    times_local = [start_time + timedelta(minutes=10 * i) for i in range(24 * 6)]
    times_utc = Time([t.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S') for t in times_local],
                     format='isot', scale='utc')
    return times_local, times_utc

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.context_processor
def inject_version():
    return dict(version=APP_VERSION)

@app.route('/download_config')
@login_required
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
@login_required
def import_config():
    try:
        if 'file' not in request.files:
            return "No file uploaded", 400

        file = request.files['file']
        contents = file.read().decode('utf-8')

        # Parse YAML safely
        new_config = yaml.safe_load(contents)

        # Validation
        if not isinstance(new_config, dict):
            return "Invalid YAML structure.", 400
        if 'locations' not in new_config or not isinstance(new_config['locations'], dict) or not new_config['locations']:
            return "Missing or empty 'locations' key.", 400
        if 'objects' not in new_config or not isinstance(new_config['objects'], list):
            return "Missing or invalid 'objects' key.", 400

        for loc_name, loc_data in new_config['locations'].items():
            if not all(k in loc_data for k in ['lat', 'lon', 'timezone']):
                return f"Location '{loc_name}' is missing required fields.", 400

        for obj in new_config['objects']:
            if not isinstance(obj, dict) or 'Object' not in obj:
                return f"One of the objects is malformed or missing 'Object' field.", 400

        # Determine correct CONFIG_PATH for this user
        username = current_user.username
        config_path = os.path.join(os.path.dirname(__file__), f"config_{username}.yaml")

        # Backup current config to backups/ folder
        if os.path.exists(config_path):
            backup_dir = os.path.join(os.path.dirname(config_path), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(backup_dir, f"{username}_backup_{timestamp}.yaml")
            shutil.copy(config_path, backup_path)
            print(f"[IMPORT] Backed up current config to {backup_path}")

        # Save new config
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

def calculate_transit_time(ra_hours, lat, lon, tz_name):
    #location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
    local_tz = pytz.timezone(tz_name)
    now_local = datetime.now(local_tz)
    date_str = now_local.strftime('%Y-%m-%d')
    midnight_local = local_tz.localize(datetime.strptime(date_str, '%Y-%m-%d'))
    midnight_utc = midnight_local.astimezone(pytz.utc)
    midnight_time = Time(midnight_utc)
    lst_midnight = midnight_time.sidereal_time('mean', longitude=lon * u.deg).hour
    delta_hours = (ra_hours - lst_midnight) % 24
    transit_utc = midnight_time + delta_hours * u.hour
    transit_local = transit_utc.to_datetime(timezone=pytz.utc).astimezone(local_tz)
    return transit_local.strftime('%H:%M')

def get_utc_time_for_local_11pm():
    local_tz = pytz.timezone(g.tz_name)
    now_local = datetime.now(local_tz)
    eleven_pm_local = now_local.replace(hour=23, minute=0, second=0, microsecond=0)
    if now_local > eleven_pm_local:
        eleven_pm_local += timedelta(days=1)
    utc_time = eleven_pm_local.astimezone(pytz.utc)
    #print(f"[DEBUG] 11 PM Local Time ({g.tz_name}): {eleven_pm_local}, Converted to UTC: {utc_time}")
    return utc_time.strftime('%Y-%m-%dT%H:%M:%S')

def is_decimal(value):
    return isinstance(value, (np.float64, float)) or len(str(value).split()) == 1

def parse_ra_dec(value):
    if is_decimal(value):
        return float(value)
    parts = value.split()
    if len(parts) == 2:
        d, m = map(float, parts)
        s = 0.0
    elif len(parts) == 3:
        d, m, s = map(float, parts)
    else:
        raise ValueError(f"Invalid RA/DEC format: {value}")
    return d, m, s

def hms_to_hours(hms):
    if is_decimal(hms):
        return float(hms) / 15
    h, m, s = parse_ra_dec(hms)
    return h + (m / 60) + (s / 3600)

def dms_to_degrees(dms):
    if is_decimal(dms):
        return float(dms)
    d, m, s = parse_ra_dec(dms)
    sign = -1 if d < 0 else 1
    return sign * (abs(d) + (m / 60) + (s / 3600))


def get_ra_dec(object_name):
    obj_key = object_name.lower()
    objects_config = g.user_config.get("objects", [])
    obj_entry = next((item for item in objects_config if item["Object"].lower() == obj_key), None)

    # ✅ If RA and DEC already exist in the config, return early
    if obj_entry:
        ra = obj_entry.get("RA")
        dec = obj_entry.get("DEC")
        if ra is not None and dec is not None:
            try:
                return {
                    "Object": object_name,
                    "Common Name": obj_entry.get("Name", object_name),
                    "RA (hours)": float(ra),
                    "DEC (degrees)": float(dec),
                    "Project": obj_entry.get("Project", "none")
                }
            except ValueError as ve:
                print(f"[ERROR] Failed to parse RA/DEC for {object_name}: {ve}")
                return {
                    "Object": object_name,
                    "Common Name": f"Error: Invalid RA/DEC format",
                    "RA (hours)": None,
                    "DEC (degrees)": None,
                    "Project": obj_entry.get("Project", "none")
                }

    # 🛰 Only fetch from SIMBAD if needed
    print(f"[SIMBAD] Querying SIMBAD for {object_name}...")
    Simbad.TIMEOUT = 60
    Simbad.ROW_LIMIT = 1

    try:
        result = Simbad.query_object(object_name)
        if result is None or len(result) == 0:
            raise ValueError(f"No results for object '{object_name}' in SIMBAD.")

        result = {k.lower(): v for k, v in result.items()}
        ra_value = result["ra"][0]
        dec_value = result["dec"][0]

        ra_hours = hms_to_hours(ra_value)
        dec_degrees = dms_to_degrees(dec_value)

        #  Save RA/DEC back into config for future use
        if obj_entry:
            obj_entry["RA"] = ra_hours
            obj_entry["DEC"] = dec_degrees
        else:
            objects_config.append({
                "Object": object_name,
                "Name": object_name,
                "Type": "",
                "Project": "none",
                "RA": ra_hours,
                "DEC": dec_degrees
            })

        save_user_config(current_user.username, g.user_config)

        return {
            "Object": object_name,
            "Common Name": object_name,
            "RA (hours)": ra_hours,
            "DEC (degrees)": dec_degrees,
            "Project": "none"
        }

    except Exception as ex:
        error_message = f"Error: {str(ex)}"
        print(f"[ERROR] SIMBAD lookup for {object_name} failed: {error_message}")
        return {
            "Object": object_name,
            "Common Name": error_message,
            "RA (hours)": None,
            "DEC (degrees)": None,
            "Project": error_message
        }

def ra_dec_to_alt_az(ra, dec, lat, lon, time_utc):
    if "T" not in time_utc:
        time_utc = time_utc.replace(" ", "T")
    time_utc = time_utc.split("+")[0]
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
    observation_time = Time(time_utc, format='isot', scale='utc')
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_frame = AltAz(obstime=observation_time, location=location)
    altaz = sky_coord.transform_to(altaz_frame)
    return altaz.alt.deg, altaz.az.deg


def calculate_max_observable_altitude(ra, dec, lat, lon, local_date, tz_name, altitude_threshold):
    local_tz = pytz.timezone(tz_name)
    sun_events = calculate_sun_events(local_date)

    dusk_time = local_tz.localize(datetime.combine(
        datetime.strptime(local_date, '%Y-%m-%d'),
        datetime.strptime(sun_events["astronomical_dusk"], '%H:%M').time()
    ))

    dawn_date = datetime.strptime(local_date, '%Y-%m-%d') + timedelta(days=1)
    dawn_time = local_tz.localize(datetime.combine(
        dawn_date,
        datetime.strptime(sun_events["astronomical_dawn"], '%H:%M').time()
    ))

    sample_interval = timedelta(minutes=10)
    num_samples = int((dawn_time - dusk_time).total_seconds() / sample_interval.total_seconds()) + 1
    times = [dusk_time + i * sample_interval for i in range(num_samples)]
    times_utc = Time([t.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S') for t in times],
                     format='isot', scale='utc')

    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)
    altaz = sky_coord.transform_to(altaz_frame)
    altitudes = altaz.alt.deg

    max_altitude = np.max(altitudes)
    max_index = np.argmax(altitudes)
    max_time = times[max_index]

    return max_altitude, max_time

def calculate_altitude_curve(ra, dec, lat, lon, local_date, tz_name):
    if hasattr(g, 'times_local') and hasattr(g, 'times_utc'):
        times_local = g.times_local
        times_utc = g.times_utc
    else:
        times_local, times_utc = get_common_time_arrays(tz_name, local_date)
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)
    altaz = sky_coord.transform_to(altaz_frame)
    altitudes = altaz.alt.deg
    return times_local, altitudes


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
    sun_events_curr = calculate_sun_events(local_date)
    previous_date = (datetime.strptime(local_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    sun_events_prev = calculate_sun_events(previous_date)

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

    # --- Corrected Background Shading Logic ---
    ax.set_facecolor("lightgray")

    # Define plot start and end clearly
    plot_start = times_local_naive[0]  # noon of selected day
    plot_end = plot_start + timedelta(hours=24)  # next day noon
    midnight = plot_start + timedelta(hours=12)  # midnight is always halfway

    # Calculate sun events
    sun_events_curr = calculate_sun_events(local_date)
    next_date_str = (datetime.strptime(local_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    sun_events_next = calculate_sun_events(next_date_str)

    # Parse astronomical dusk (current day)
    astro_dusk_curr_str = sun_events_curr.get("astronomical_dusk")
    astro_dusk_curr = local_tz.localize(datetime.combine(datetime.strptime(local_date, '%Y-%m-%d'),
                                                         datetime.strptime(astro_dusk_curr_str, "%H:%M").time()))

    # Parse astronomical dawn (next day)
    astro_dawn_next_str = sun_events_next.get("astronomical_dawn")
    astro_dawn_next = local_tz.localize(datetime.combine(datetime.strptime(next_date_str, '%Y-%m-%d'),
                                                         datetime.strptime(astro_dawn_next_str, "%H:%M").time()))

    # Convert to naive for plotting
    astro_dusk_curr_naive = astro_dusk_curr.replace(tzinfo=None)
    astro_dawn_next_naive = astro_dawn_next.replace(tzinfo=None)

    # Shade night time (white) from astronomical dusk until astronomical dawn next day
    if plot_start <= astro_dusk_curr_naive <= plot_end and plot_start <= astro_dawn_next_naive <= plot_end:
        ax.axvspan(astro_dusk_curr_naive, astro_dawn_next_naive, facecolor="white", alpha=1.0)

    # Draw sun event vertical lines correctly
    event_datetimes = {
        "Astronomical dusk": astro_dusk_curr_naive,
        "Astronomical dawn": astro_dawn_next_naive,
        "Sunset": local_tz.localize(datetime.combine(datetime.strptime(local_date, '%Y-%m-%d'),
                                                     datetime.strptime(sun_events_curr["sunset"],
                                                                       '%H:%M').time())).replace(tzinfo=None),
        "Sunrise": local_tz.localize(datetime.combine(datetime.strptime(next_date_str, '%Y-%m-%d'),
                                                      datetime.strptime(sun_events_next["sunrise"],
                                                                        '%H:%M').time())).replace(tzinfo=None),
    }

    for event, dt in event_datetimes.items():
        if plot_start <= dt <= plot_end:
            ax.axvline(x=dt, color='black', linestyle='-', linewidth=1, alpha=0.7)
            label_x = mdates.date2num(dt + timedelta(minutes=10))
            ymin, ymax = ax.get_ylim()
            label_y = ymin + 0.05 * (ymax - ymin)
            ax.text(label_x, label_y, event, rotation=90,
                    verticalalignment='bottom', fontsize=9, color='grey')

    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, loc='upper left', bbox_to_anchor=(1.05, 1))
    plt.subplots_adjust(right=0.8)
    ax.grid(True)
    plt.tight_layout()

    filename = f"static/{sanitize_object_name(object_name).replace(' ', '_')}_{selected_location.replace(' ', '_')}_altitude_plot.png"
    plt.savefig(filename)
    plt.close()
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

def ephem_to_local(ephem_date, tz_name):
    utc_dt = ephem.Date(ephem_date).datetime()
    local_tz = pytz.timezone(tz_name)
    local_dt = pytz.utc.localize(utc_dt).astimezone(local_tz)
    return local_dt

def calculate_sun_events(date_str):
    local_tz = pytz.timezone(g.tz_name)
    local_date = datetime.strptime(date_str, "%Y-%m-%d")
    local_midnight = local_tz.localize(datetime.combine(local_date, datetime.min.time()))
    midnight_utc = local_midnight.astimezone(pytz.utc)
    sun = ephem.Sun() # noinspection PyUnresolvedReferences
    obs = ephem.Observer()
    obs.lat = str(g.lat)
    obs.lon = str(g.lon)
    obs.date = midnight_utc
    obs.horizon = '-18'
    astro_dawn = obs.next_rising(sun, use_center=True)
    obs.horizon = '-0.833'
    sunrise = obs.next_rising(sun, use_center=True)
    obs.horizon = '0'
    obs.date = midnight_utc
    transit = obs.next_transit(sun)
    noon_local = local_tz.localize(datetime.combine(local_date, datetime.strptime("12:00", "%H:%M").time()))
    noon_utc = noon_local.astimezone(pytz.utc)
    obs.date = noon_utc
    obs.horizon = '-0.833'
    sunset = obs.next_setting(sun, use_center=True)
    obs.horizon = '-18'
    astro_dusk = obs.next_setting(sun, use_center=True)
    astro_dawn_local = ephem_to_local(astro_dawn, g.tz_name).strftime('%H:%M')
    sunrise_local    = ephem_to_local(sunrise, g.tz_name).strftime('%H:%M')
    transit_local    = ephem_to_local(transit, g.tz_name).strftime('%H:%M')
    sunset_local     = ephem_to_local(sunset, g.tz_name).strftime('%H:%M')
    astro_dusk_local = ephem_to_local(astro_dusk, g.tz_name).strftime('%H:%M')
    return {
        "astronomical_dawn": astro_dawn_local,
        "sunrise": sunrise_local,
        "transit": transit_local,
        "sunset": sunset_local,
        "astronomical_dusk": astro_dusk_local
    }

@app.route('/set_location', methods=['POST'])
@login_required
def set_location_api():
    data = request.get_json()
    location_name = data.get("location")
    if location_name in g.locations:
        g.user_config['default_location'] = location_name
        save_user_config(current_user.username, g.user_config)
        g.selected_location = location_name
        return jsonify({"status": "success", "message": f"Location set to {location_name}"})
    else:
        return jsonify({"status": "error", "message": "Invalid location"}), 404

# =============================================================================
# Protected Routes
# =============================================================================

@app.route('/get_locations')
@login_required
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


@app.route('/data')
@login_required
def get_data():
    local_tz = pytz.timezone(g.tz_name)
    current_datetime_local = datetime.now(local_tz)
    local_date = current_datetime_local.strftime('%Y-%m-%d')
    current_time_utc = current_datetime_local.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S')
    fixed_time_utc_str = get_utc_time_for_local_11pm()
    altitude_threshold = g.user_config.get("altitude_threshold", 20)

    object_data = []
    prev_alts = session.get('previous_altitudes', {})

    for obj in g.objects:
        data = get_ra_dec(obj)
        if not data or data["RA (hours)"] is None or data["DEC (degrees)"] is None:
            object_data.append({
                'Object': obj,
                'Common Name': data.get("Project", "Error"),
                'RA (hours)': "N/A",
                'DEC (degrees)': "N/A",
                'Altitude Current': 100,
                'Azimuth Current': "N/A",
                'Altitude 11PM': "N/A",
                'Azimuth 11PM': "N/A",
                'Transit Time': "N/A",
                'Observable Duration (min)': "N/A",
                'Trend': "N/A",
                'Project': data.get('Project', "none"),
                'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
                'Max Altitude (°)': "N/A",
            })
            continue

        try:
            ra = data["RA (hours)"]
            dec = data["DEC (degrees)"]

            # --- Compute live values ---
            alt_current, az_current = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, current_time_utc)

            # --- Get 11PM/transit/obs/max from static_cache ---
            cache_key = f"{obj.lower()}_{local_date}_{g.selected_location}"
            if cache_key in static_cache:
                cached = static_cache[cache_key]
            else:
                alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, fixed_time_utc_str)
                transit_time = calculate_transit_time(ra, g.lat, g.lon, g.tz_name)
                obs_duration, max_alt = calculate_observable_duration_vectorized(
                    ra, dec, g.lat, g.lon, local_date, g.tz_name
                )
                cached = {
                    'Altitude 11PM': alt_11pm,
                    'Azimuth 11PM': az_11pm,
                    'Transit Time': transit_time,
                    'Observable Duration (min)': int(obs_duration.total_seconds() / 60),
                    'Max Altitude (°)': round(max_alt, 1)
                }
                static_cache[cache_key] = cached

            # --- Moon angular separation (hourly cache) ---
            current_hour_str = current_datetime_local.strftime('%Y-%m-%d_%H')
            moon_key = f"{obj.lower()}_{current_hour_str}_{g.selected_location}"

            if moon_key in moon_separation_cache:
                angular_sep = moon_separation_cache[moon_key]
            else:
                location = EarthLocation(lat=g.lat * u.deg, lon=g.lon * u.deg)
                time_obj = Time(current_time_utc, format='isot', scale='utc')
                moon_coord = get_body('moon', time_obj, location=location)
                moon_altaz = moon_coord.transform_to(AltAz(obstime=time_obj, location=location))

                obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                obj_altaz = obj_coord.transform_to(AltAz(obstime=time_obj, location=location))

                angular_sep = obj_altaz.separation(moon_altaz).deg
                moon_separation_cache[moon_key] = round(angular_sep, 1)

            # --- Trend ---
            prev_alt = prev_alts.get(obj)
            trend = '→'
            if prev_alt is not None:
                trend = '↑' if alt_current > prev_alt else '↓' if alt_current < prev_alt else '→'
            prev_alts[obj] = alt_current

            object_data.append({
                'Object': data['Object'],
                'Common Name': data['Common Name'],
                'RA (hours)': ra,
                'DEC (degrees)': dec,
                'Altitude Current': alt_current,
                'Azimuth Current': az_current,
                'Altitude 11PM': cached['Altitude 11PM'],
                'Azimuth 11PM': cached['Azimuth 11PM'],
                'Transit Time': cached['Transit Time'],
                'Observable Duration (min)': cached['Observable Duration (min)'],
                'Max Altitude (°)': cached['Max Altitude (°)'],
                'Angular Separation (°)': round(angular_sep) if angular_sep is not None else "N/A",
                'Trend': trend,
                'Project': data.get('Project', "none"),
                'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
            })

        except Exception as e:
            print(f"[ERROR] {obj}: {e}")

    session['previous_altitudes'] = prev_alts
    sorted_objects = sorted(
        object_data,
        key=lambda x: x['Altitude Current'] if isinstance(x['Altitude Current'], (int, float)) else -1,
        reverse=True
    )
    return jsonify({
        "date": local_date,
        "time": current_datetime_local.strftime('%H:%M:%S'),
        "phase": round(ephem.Moon(current_datetime_local).phase, 0),
        "altitude_threshold": altitude_threshold,
        "objects": sorted_objects
    })

@app.route('/sun_events')
@login_required
def sun_events():
    local_date = datetime.now(pytz.timezone(g.tz_name)).strftime('%Y-%m-%d')
    events = calculate_sun_events(local_date)
    events["date"] = local_date
    return jsonify(events)

@app.route('/')
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
    sun_events = calculate_sun_events(selected_date_str)

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
@login_required
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
@login_required
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
    sun_events = calculate_sun_events(local_date_str)

    return jsonify({
        "date": local_date_str,
        "phase": phase,
        "astronomical_dawn": sun_events.get("astronomical_dawn", "N/A"),
        "astronomical_dusk": sun_events.get("astronomical_dusk", "N/A")
    })

def calculate_observable_duration_vectorized(ra, dec, lat, lon, local_date, tz_name):
    local_tz = pytz.timezone(tz_name)
    date_obj = datetime.strptime(local_date, "%Y-%m-%d")

    # Get dusk and dawn for the current local_date
    sun_events = calculate_sun_events(local_date)

    dusk_str = sun_events.get("astronomical_dusk")
    dawn_str = sun_events.get("astronomical_dawn")

    if not dusk_str or not dawn_str:
        print(f"[WARN] Missing sun events for {local_date}")
        return timedelta(0), 0

    dusk_time = datetime.strptime(dusk_str, "%H:%M").time()
    dawn_time = datetime.strptime(dawn_str, "%H:%M").time()

    # Both dusk and dawn belong to the same local_date (regardless of whether dusk is after midnight)
    dusk_dt = local_tz.localize(datetime.combine(date_obj, dusk_time))
    dawn_dt = local_tz.localize(datetime.combine(date_obj, dawn_time))

    # If dawn is earlier than dusk, it must be after midnight (i.e., next day)
    if dawn_dt <= dusk_dt:
        dawn_dt += timedelta(days=1)

    # Time samples between dusk and dawn
    sample_interval = timedelta(minutes=10)
    times = []
    current = dusk_dt
    while current <= dawn_dt:
        times.append(current)
        current += sample_interval

    if not times:
        return timedelta(0), 0

    times_utc = Time([t.astimezone(pytz.utc) for t in times], scale='utc')
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

    frame = AltAz(obstime=times_utc, location=location)
    altaz = sky_coord.transform_to(frame)
    altitudes = altaz.alt.deg

    threshold = g.altitude_threshold
    mask = np.array(altitudes) > threshold
    observable_minutes = int(np.sum(mask) * 10)
    max_altitude = float(np.max(altitudes)) if len(altitudes) > 0 else 0

    return timedelta(minutes=observable_minutes), max_altitude

@app.route('/get_imaging_opportunities/<object_name>')
@login_required
def get_imaging_opportunities(object_name):
    # Load object data
    data = get_ra_dec(object_name)
    if not data or data.get("RA (hours)") is None or data.get("DEC (degrees)") is None:
        return jsonify({"status": "error", "message": "Object has no valid RA/DEC."}), 400

    ra = data["RA (hours)"]
    dec = data["DEC (degrees)"]
    alt_name = data.get("Common Name", object_name)

    criteria = get_imaging_criteria()
    min_obs = criteria["min_observable_minutes"]
    min_alt = criteria["min_max_altitude"]
    max_moon = criteria["max_moon_illumination"]
    min_sep = criteria["min_angular_distance"]
    months = criteria.get("search_horizon_months", 6)

    # Search range
    local_tz = pytz.timezone(g.tz_name)
    today = datetime.now(local_tz).date()
    end_date = today + timedelta(days=months * 30)
    dates = [today + timedelta(days=i) for i in range((end_date - today).days)]

    results = []

    for d in dates:
        date_str = d.strftime('%Y-%m-%d')
        try:
            obs_duration, max_altitude = calculate_observable_duration_vectorized(
                ra, dec, g.lat, g.lon, date_str, g.tz_name
            )

            if obs_duration.total_seconds() / 60 < min_obs:
                continue
            if max_altitude < min_alt:
                continue

            # Get moon phase
            local_tz = pytz.timezone(g.tz_name)
            local_time = local_tz.localize(datetime.combine(d, datetime.now().time()))
            moon_phase = ephem.Moon(local_time.astimezone(pytz.utc)).phase
            if moon_phase > max_moon:
                continue

            # Compute angular distance to moon
            location = EarthLocation(lat=g.lat * u.deg, lon=g.lon * u.deg)
            dusk = calculate_sun_events(date_str).get("astronomical_dusk", "20:00")
            dusk_dt = local_tz.localize(datetime.combine(d, datetime.strptime(dusk, '%H:%M').time()))
            dusk_utc = dusk_dt.astimezone(pytz.utc)

            frame = AltAz(obstime=Time(dusk_utc), location=location)
            obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
            moon_coord = get_body('moon', Time(dusk_utc), location=location)

            obj_altaz = obj_coord.transform_to(frame)
            moon_altaz = moon_coord.transform_to(frame)

            # Angular distance
            separation = obj_altaz.separation(moon_altaz).deg
            if separation < min_sep:
                continue

            # Normalize criteria into 0–1 range
            MIN_ALTITUDE = 20  # degrees

            if max_altitude < MIN_ALTITUDE:
                score_alt = 0
            else:
                score_alt = (max_altitude - MIN_ALTITUDE) / (90 - MIN_ALTITUDE)
                score_alt = min(score_alt, 1)  # just in case

            #score_alt = min(max_altitude / 90, 1)
            score_duration = min(obs_duration.total_seconds() / 3600 / 12, 1)  # max 12h
            score_moon_illum = 1 - min(moon_phase / 100, 1)
            score_moon_sep = min(separation / 180, 1)

            # Composite score (simple equal weights)
            composite_score = 100 * (0.30 * score_alt + 0.20 * score_duration + 0.4 * score_moon_illum + 0.1 * score_moon_sep)

            # Map to stars (1 to 5 stars)
            stars = int(round((composite_score / 100) * 4)) + 1
            star_string = "★" * stars + "☆" * (5 - stars)

            # Passed all checks
            results.append({
                "date": date_str,
                "obs_minutes": int(obs_duration.total_seconds() / 60),
                "max_alt": round(max_altitude, 1),
                "moon_illumination": round(moon_phase, 1),
                "moon_separation": round(separation, 1),
                "rating": star_string
            })

        except Exception as e:
            print(f"[WARN] Skipping date {date_str}: {e}")

    return jsonify({"status": "success", "object": object_name, "alt_name": alt_name, "results": results})
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

