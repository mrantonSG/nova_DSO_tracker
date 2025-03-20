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

import numpy as np
import pytz
import ephem
import yaml
import shutil

import matplotlib
matplotlib.use('Agg')  # Use a non-GUI backend for headless servers
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from flask import render_template, jsonify, request, send_file, redirect, url_for, flash, g
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user
from flask import session
from flask import Flask, send_from_directory

from astroquery.simbad import Simbad
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u
# =============================================================================
# Flask and Flask-Login Setup
# =============================================================================
APP_VERSION = "1.6.3"
load_dotenv()

ENV_FILE = ".env"

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

    print(f"✅ Created .env file with a new SECRET_KEY and default user")

# Load SECRET_KEY and users from the .env file
SECRET_KEY = config('SECRET_KEY', default=secrets.token_hex(32))  # Ensure a fallback key

app = Flask(__name__)
app.secret_key = SECRET_KEY

SINGLE_USER_MODE = True  # Set to False for multi‑user mode

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

def sanitize_object_name(object_name):
    return object_name.replace("/", "-")

@app.context_processor
def inject_version():
    return dict(version=APP_VERSION)

# =============================================================================
# User-Specific Configuration Functions
# =============================================================================
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
        return yaml.safe_load(file) or {}


def save_user_config(username, config_data):
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{username}.yaml"
    with open(filename, "w") as file:
        yaml.dump(config_data, file)


@app.route('/login', methods=['GET', 'POST'])
def login():
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
        return jsonify({"status": "error", "message": str(e)}), 500


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
    start_time = local_tz.localize(datetime.combine(base_date - timedelta(days=1), datetime.min.time()).replace(hour=12))
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

# =============================================================================
# Astronomical Calculations
# =============================================================================
# noinspection PyUnusedLocal
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

    # Check if RA and DEC already exist in config
    if obj_entry and "RA" in obj_entry and "DEC" in obj_entry:
        try:
            # Convert RA and DEC to float if they are stored as strings.
            ra_hours = float(obj_entry["RA"])
            dec_degrees = float(obj_entry["DEC"])
        except Exception as exception_value:
            print(f"[ERROR] Converting RA/DEC for {object_name}: {exception_value}")
            ra_hours = obj_entry["RA"]
            dec_degrees = obj_entry["DEC"]
        return {
            "Object": object_name,
            "Common Name": obj_entry.get("Name", object_name),
            "RA (hours)": ra_hours,
            "DEC (degrees)": dec_degrees,
            "Project": obj_entry.get("Project", "none")
        }

    # Fetch RA/DEC from SIMBAD only if not found in config
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

        # Store fetched RA/DEC permanently into YAML
        if obj_entry:
            obj_entry["RA"] = ra_hours
            obj_entry["DEC"] = dec_degrees
        else:
            # If object does not exist yet, create it in YAML
            new_obj = {
                "Object": object_name,
                "Name": object_name,
                "Type": "",
                "Project": "none",
                "RA": ra_hours,
                "DEC": dec_degrees
            }
            objects_config.append(new_obj)

        save_user_config(current_user.username, g.user_config)

        return {
            "Object": object_name,
            "Common Name": object_name,
            "RA (hours)": ra_hours,
            "DEC (degrees)": dec_degrees,
            "Project": "none"
        }

    except Exception as exception_value:
        error_message = f"Error: {str(exception_value)}"
        print(f"[ERROR] Problem processing {object_name}: {error_message}")
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
    local_tz = pytz.timezone(tz_name)
    if hasattr(g, 'times_local') and hasattr(g, 'times_utc'):
        times_local = g.times_local
        times_utc = g.times_utc
    else:
        times_local, times_utc = get_common_time_arrays(tz_name, local_date)
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
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(times_local_naive, altitudes, '-', linewidth=3, color='tab:blue', label=f'{object_name} Altitude')

    # Plot Moon altitude as dashed yellow.
    ax.plot(times_local_naive, moon_altitudes, '-', color='gold',linewidth=1, label='Moon Altitude')
    ax.axhline(y=0, color='black', linewidth=2, linestyle='--', label='Horizon')
    ax.set_xlabel(f'Time (Local - {selected_location})')
    ax.set_ylabel('Altitude (°)', color='k')
    ax.tick_params(axis='y', labelcolor='k')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    plt.xticks(rotation=45)

    # Add a two-line title.
    ax.set_title(f"Altitude and Azimuth for {object_name} ({alt_name}) on {local_date}")

    # Create secondary axis for azimuth.
    ax2 = ax.twinx()
    ax2.plot(times_local_naive, azimuths, '--', linewidth=2, color='tab:cyan', label=f'{object_name} Azimuth')
    ax2.set_ylabel('Azimuth (°)', color='k')
    ax2.tick_params(axis='y', labelcolor='k')
    ax2.set_ylim(0, 360)
    ax2.spines['right'].set_color('k')
    ax2.spines['right'].set_linewidth(1.5)

    # Add Moon azimuth.
    ax2.plot(times_local_naive, moon_azimuths, '--', linewidth=1, color='gold', label='Moon Azimuth')

    # Set x-axis limits.
    plot_start = times_local_naive[0]
    plot_end = plot_start + timedelta(hours=24)
    ax.set_xlim(plot_start, plot_end)

    # Define key times correctly
    midnight = datetime.combine(datetime.strptime(local_date, '%Y-%m-%d'), datetime.min.time())
    noon = midnight + timedelta(hours=12)
    previous_midnight = midnight - timedelta(days=1)

    astro_dawn = event_datetimes_naive.get('curr_astronomical_dawn')
    astro_dusk = event_datetimes_naive.get('prev_astronomical_dusk')  # Corrected for the previous day

    # 1. Shade from **Noon (today) → Dusk (previous day)**
    ax.axvspan(previous_midnight + timedelta(hours=12), astro_dusk, color='lightgray', alpha=0.4)

    # 2. Shade from **Dawn (today) → Noon (today)**
    ax.axvspan(astro_dawn, noon, color='lightgray', alpha=0.4)

    # Draw sun event vertical lines.
    for event, dt in event_datetimes_naive.items():
        if 'transit' in event:
            continue
        if plot_start <= dt <= plot_end:
            # Plot vertical event line
            ax.axvline(x=dt, color='black', linestyle='-', linewidth=1, alpha=0.7)

            # Calculate label position slightly to the right
            label_x = mdates.date2num(dt + timedelta(minutes=10))
            ymin, ymax = ax.get_ylim()
            label_y = ymin + 0.05 * (ymax - ymin)

            # Create and place label
            label = event.replace('prev_', '').replace('curr_', '').replace('_', ' ').capitalize()
            ax.text(label_x, label_y, label, rotation=90,
                    verticalalignment='bottom', fontsize=9, color='grey')

    # Combine legends from both axes.
    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines + lines2, labels + labels2, loc='upper left', bbox_to_anchor=(1.05, 1))
    plt.subplots_adjust(right=0.8)

    ax.grid(True)
    plt.tight_layout()

    # Build the file name using the sanitized object name.
    filename = f"static/{sanitize_object_name(object_name).replace(' ', '_')}_{selected_location.replace(' ', '_')}_altitude_plot.png"
    plt.savefig(filename)
    plt.close()
    return filename

def ephem_to_local(ephem_date, tz_name):
    utc_dt = ephem.Date(ephem_date).datetime()
    local_tz = pytz.timezone(tz_name)
    local_dt = pytz.utc.localize(utc_dt).astimezone(local_tz)
    return local_dt


# noinspection PyUnresolvedReferences
def calculate_sun_events(date_str):
    local_tz = pytz.timezone(g.tz_name)
    local_date = datetime.strptime(date_str, "%Y-%m-%d")
    local_midnight = local_tz.localize(datetime.combine(local_date, datetime.min.time()))
    midnight_utc = local_midnight.astimezone(pytz.utc)
    sun = ephem.Sun()
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

def calculate_observable_duration_vectorized(ra, dec, lat, lon, local_date, tz_name ):
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
    num_samples = int(((dawn_time - dusk_time).total_seconds()) / sample_interval.total_seconds()) + 1
    times = [dusk_time + i * sample_interval for i in range(num_samples)]
    times_utc = Time([t.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S') for t in times],
                     format='isot', scale='utc')

    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)
    altaz = sky_coord.transform_to(altaz_frame)
    altitudes = altaz.alt.deg

    # Use user-defined altitude threshold
    altitude_threshold = g.altitude_threshold
    above_threshold = np.array(altitudes) > altitude_threshold
    observable_duration_minutes = np.sum(above_threshold) * 10
    return timedelta(minutes=int(observable_duration_minutes))

# =============================================================================
# Route to Set Location
# =============================================================================
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
        if data:
            # If RA/DEC are missing, assume an error occurred.
            if data['RA (hours)'] is None or data['DEC (degrees)'] is None:
                const_error = data.get('Project', "Error: Unknown error")
                object_data.append({
                    'Object': data['Object'],
                    'Common Name': const_error,  # Use the error message in the name field.
                    'RA (hours)': "N/A",
                    'DEC (degrees)': "N/A",
                    'Altitude Current': 100,  # or "N/A" (or 100 if you want it to sort to the top)
                    'Azimuth Current': "N/A",
                    'Altitude 11PM': "N/A",
                    'Azimuth 11PM': "N/A",
                    'Transit Time': "N/A",
                    'Observable Duration (min)': "N/A",
                    'Trend': "N/A",
                    'Project': data.get('Project', "none"),
                    'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S')
                })
            else:
                try:
                    ra = data['RA (hours)']
                    dec = data['DEC (degrees)']
                    alt_current, az_current = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, current_time_utc)
                    alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, fixed_time_utc_str)
                    transit_time = calculate_transit_time(ra, g.lat, g.lon, g.tz_name)
                    prev_alt = prev_alts.get(obj)
                    if prev_alt is not None:
                        if alt_current > prev_alt:
                            trend = '↑'
                        elif alt_current < prev_alt:
                            trend = '↓'
                        else:
                            trend = '→'
                    else:
                        trend = '→'
                    prev_alts[obj] = alt_current
                    observable_duration = calculate_observable_duration_vectorized(
                        ra, dec, g.lat, g.lon, local_date, g.tz_name
                    )
                    observable_minutes = int(observable_duration.total_seconds() / 60)
                    object_data.append({
                        'Object': data['Object'],
                        'Common Name': data['Common Name'],
                        'RA (hours)': ra,
                        'DEC (degrees)': dec,
                        'Altitude Current': alt_current,
                        'Azimuth Current': az_current,
                        'Altitude 11PM': alt_11pm,
                        'Azimuth 11PM': az_11pm,
                        'Transit Time': transit_time,
                        'Observable Duration (min)': observable_minutes,
                        'Trend': trend,
                        'Project': data.get('Project', "none"),
                        'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S')
                    })
                except Exception as exception_value:
                    print(f"[ERROR] Problem processing object {obj}: {exception_value}")
        else:
            print(f"[DEBUG] No data returned for object: {obj}")

    session['previous_altitudes'] = prev_alts
    sorted_objects = sorted(
        object_data,
        key=lambda x: x['Altitude Current'] if isinstance(x['Altitude Current'], (int, float)) else -1,
        reverse=True
    )
    #print(f"[DEBUG] Finished processing. Total objects processed: {len(sorted_objects)}")
    return jsonify({
        "date": local_date,
        "time": current_datetime_local.strftime('%H:%M:%S'),
        "phase": round(ephem.Moon(current_datetime_local).phase, 0),
        "altitude_threshold": altitude_threshold,
        "objects": sorted_objects
    })


# noinspection PyArgumentList
@app.route('/plot/<object_name>')
@login_required
def get_plot(object_name):
    data = get_ra_dec(object_name)
    if data:
        if data['RA (hours)'] is None or data['DEC (degrees)'] is None:
            return jsonify({"error": f"Graph not available: {data.get('Project', 'No data')}" }), 400
        plot_path = plot_altitude_curve(
            object_name,
            data['RA (hours)'],
            data['DEC (degrees)'],
            g.lat, g.lon,
            datetime.now().strftime('%Y-%m-%d'),
            g.tz_name,
            g.selected_location
        )
        return send_file(plot_path, mimetype='image/png')
    return jsonify({"error": "Object not found"}), 404

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

                # Ensure altitude threshold is stored as an integer
                try:
                    threshold_value = int(new_altitude_threshold)
                    if threshold_value < 0 or threshold_value > 90:
                        raise ValueError("Altitude threshold must be between 0 and 90 degrees.")
                    g.user_config['altitude_threshold'] = threshold_value
                except ValueError as ve:
                    error = str(ve)

                # Preserve default location (avoid setting it to None)
                g.user_config['default_location'] = new_default_location if new_default_location else "Singapore"

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

    return render_template('config_form.html', config=g.user_config, locations=g.locations, error=error, message=message)

@app.before_request
def precompute_time_arrays():
    if current_user.is_authenticated and g.tz_name:
        local_tz = pytz.timezone(g.tz_name)
        local_date = datetime.now(local_tz).strftime('%Y-%m-%d')
        g.times_local, g.times_utc = get_common_time_arrays(g.tz_name, local_date)

@app.route('/plot_altitude/<path:object_name>')
@login_required
def plot_altitude(object_name):
    data = get_ra_dec(object_name)
    if data:
        if data['RA (hours)'] is None or data['DEC (degrees)'] is None:
            return jsonify({"error": f"Graph not available: {data.get('Project', 'No data')}" }), 400
        project = data.get('Project', "none")
        alt_name = data.get("Common Name", object_name)
        local_date = datetime.now(pytz.timezone(g.tz_name)).strftime('%Y-%m-%d')
        # Use the sanitized object name for the file name.
        #safe_object = sanitize_object_name(object_name)
        #filename = f"static/{safe_object.replace(' ', '_')}_{g.selected_location.replace(' ', '_')}_altitude_plot.png"
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
                                   location=g.selected_location)
        else:
            return jsonify({'error': 'Plot not found'}), 404
    return jsonify({"error": "Object not found"}), 404

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
# =============================================================================
# Main Entry Point
# =============================================================================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
