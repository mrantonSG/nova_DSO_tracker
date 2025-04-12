"""
astro_calculations.py

This module contains all the astronomical calculation functions used by the Nova DSO Tracker.

"""

import numpy as np
import ephem
import pytz
from datetime import datetime, timedelta
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u

def calculate_transit_time(ra_hours, lat, lon, tz_name):
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

def get_utc_time_for_local_11pm(tz_name):
    local_tz = pytz.timezone(tz_name)
    now_local = datetime.now(local_tz)

    # Create a new naive datetime for today at 23:00:
    today_naive = datetime(now_local.year, now_local.month, now_local.day, 23, 0, 0)
    # Localize it (make it timezone aware):
    eleven_pm_local = local_tz.localize(today_naive)

    # If the current time is after (or equal to) today's 11pm,
    # then we want to use tomorrow's 11pm.
    if now_local >= eleven_pm_local:
        eleven_pm_local += timedelta(days=1)

    # Convert the 11pm local time to UTC:
    utc_time = eleven_pm_local.astimezone(pytz.utc)
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

def ephem_to_local(ephem_date, tz_name):
    utc_dt = ephem.Date(ephem_date).datetime()
    local_tz = pytz.timezone(tz_name)
    local_dt = pytz.utc.localize(utc_dt).astimezone(local_tz)
    return local_dt

def calculate_sun_events(date_str, tz_name, lat, lon):
    local_tz = pytz.timezone(tz_name)
    local_date = datetime.strptime(date_str, "%Y-%m-%d")
    local_midnight = local_tz.localize(datetime.combine(local_date, datetime.min.time()))
    midnight_utc = local_midnight.astimezone(pytz.utc)
    sun = ephem.Sun()
    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
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
    astro_dawn_local = ephem_to_local(astro_dawn, tz_name).strftime('%H:%M')
    sunrise_local    = ephem_to_local(sunrise, tz_name).strftime('%H:%M')
    transit_local    = ephem_to_local(transit, tz_name).strftime('%H:%M')
    sunset_local     = ephem_to_local(sunset, tz_name).strftime('%H:%M')
    astro_dusk_local = ephem_to_local(astro_dusk, tz_name).strftime('%H:%M')
    return {
        "astronomical_dawn": astro_dawn_local,
        "sunrise": sunrise_local,
        "transit": transit_local,
        "sunset": sunset_local,
        "astronomical_dusk": astro_dusk_local
    }

# Global cache for sun events: key = (date_str, tz_name, lat, lon)
SUN_EVENTS_CACHE = {}

def calculate_sun_events_cached(date_str, tz_name, lat, lon):
    """
    Returns the sun events for the given date, timezone, latitude, and longitude.
    If the result for these parameters has been calculated before, it will be returned from the cache.
    Otherwise, it calculates and stores the result in the cache.
    """
    key = (date_str, tz_name, lat, lon)
    if key in SUN_EVENTS_CACHE:
        return SUN_EVENTS_CACHE[key]

    events = calculate_sun_events(date_str, tz_name, lat, lon)
    SUN_EVENTS_CACHE[key] = events
    return events


def calculate_max_observable_altitude(ra, dec, lat, lon, local_date, tz_name, altitude_threshold):
    """
    Calculate the maximum altitude (in degrees) and its corresponding time
    for the object given its right ascension (RA in hours), declination (dec in degrees),
    observer location (lat, lon), on a specified local_date in the provided time zone (tz_name).

    The function uses cached sun events to determine dusk and dawn times.
    altitude_threshold is provided for future use if you want to filter results;
    currently, it is not applied directly in the calculation.

    Returns:
        max_altitude (float): The maximum altitude (°) of the object.
        max_time (datetime): The local time at which the maximum altitude occurs.
    """
    # Get local timezone
    local_tz = pytz.timezone(tz_name)

    # Retrieve (or calculate) sun events for the given date and observer location.
    sun_events = calculate_sun_events_cached(local_date, tz_name, lat, lon)

    # Calculate dusk time as a datetime object.
    dusk_dt = local_tz.localize(datetime.combine(
        datetime.strptime(local_date, '%Y-%m-%d'),
        datetime.strptime(sun_events["astronomical_dusk"], '%H:%M').time()
    ))

    # Calculate dawn time (for the next day) as a datetime object.
    dawn_date = datetime.strptime(local_date, '%Y-%m-%d') + timedelta(days=1)
    dawn_dt = local_tz.localize(datetime.combine(
        dawn_date,
        datetime.strptime(sun_events["astronomical_dawn"], '%H:%M').time()
    ))

    # Choose a sample interval (10 minutes)
    sample_interval = timedelta(minutes=10)
    # Calculate number of samples between dusk and dawn (inclusive)
    num_samples = int((dawn_dt - dusk_dt).total_seconds() / sample_interval.total_seconds()) + 1
    # Generate a list of sample times starting at dusk
    times = [dusk_dt + i * sample_interval for i in range(num_samples)]

    # Convert each sample time to a UTC ISO8601 string and then to an Astropy Time object
    times_utc = Time([t.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S') for t in times],
                     format='isot', scale='utc')

    # Create an observer location using the provided latitude and longitude
    location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    # Create a SkyCoord object for the target object using RA (hours) and dec (degrees)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

    # Transform the object's coordinates to the AltAz frame at each sample time.
    altaz_frame = AltAz(obstime=times_utc, location=location_obj)
    altaz = sky_coord.transform_to(altaz_frame)
    altitudes = altaz.alt.deg  # This is a numpy array of altitude values in degrees.

    # Identify the maximum altitude and the corresponding sample time.
    max_altitude_value = np.max(altitudes)
    max_index = np.argmax(altitudes)
    max_time = times[max_index]

    return max_altitude_value, max_time


def calculate_altitude_curve(ra, dec, lat, lon, local_date, tz_name):
    """
    Calculate the altitude curve for a celestial object.

    Parameters:
      ra (float): Right ascension in hours.
      dec (float): Declination in degrees.
      lat (float): Observer's latitude in degrees.
      lon (float): Observer's longitude in degrees.
      local_date (str): Local date for the calculation (format: 'YYYY-MM-DD').
      tz_name (str): Time zone name (e.g. 'Asia/Singapore').

    Returns:
      times_local (list): List of local datetime objects used for sampling.
      altitudes (numpy.array): Array of altitudes (in degrees) corresponding to the sample times.
    """
    # Compute the common time arrays (local and UTC) for the specified date and timezone.
    times_local, times_utc = get_common_time_arrays(tz_name, local_date)

    # Create an EarthLocation object from the observer's latitude and longitude.
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)

    # Create a SkyCoord object for the celestial object using its RA and declination.
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

    # Create the AltAz frame for the observation times and location.
    altaz_frame = AltAz(obstime=times_utc, location=location)

    # Transform the object's sky coordinates to the AltAz frame.
    altaz = sky_coord.transform_to(altaz_frame)

    # Extract the altitude values (in degrees) from the transformed coordinates.
    altitudes = altaz.alt.deg

    return times_local, altitudes


def get_common_time_arrays(tz_name, local_date):
    """
    Generate two arrays of times for a given local date and time zone.

    The function starts at local noon on the provided date and creates a list
    of local datetime objects sampled every 10 minutes over a 24-hour period.
    It also returns an equivalent Astropy Time object array with these times
    converted to UTC.

    Parameters:
      tz_name (str): A valid time zone identifier (e.g., 'Asia/Singapore').
      local_date (str): A date string in the format 'YYYY-MM-DD'.

    Returns:
      times_local (list of datetime.datetime): A list of local time samples.
      times_utc (astropy.time.Time): An Astropy Time object containing the UTC times.
    """

    # Create the local timezone object.
    local_tz = pytz.timezone(tz_name)
    # Parse the input date.
    base_date = datetime.strptime(local_date, '%Y-%m-%d')
    # Start at local noon on the given date.
    start_time = local_tz.localize(datetime.combine(base_date, datetime.min.time()).replace(hour=12))
    # Generate a list of local datetime samples every 10 minutes for 24 hours.
    num_samples = 24 * 6  # 6 samples per hour * 24 hours
    times_local = [start_time + timedelta(minutes=10 * i) for i in range(num_samples)]
    # Convert local times to UTC and build an Astropy Time object.
    times_utc = Time([t.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S') for t in times_local],
                     format='isot', scale='utc')

    return times_local, times_utc


def calculate_observable_duration_vectorized(ra, dec, lat, lon, local_date, tz_name, altitude_threshold):
    """
    Calculate the duration (as a timedelta) over which a celestial object is observable
    (i.e. its altitude is above a given threshold) during the night and determine the maximum altitude.

    Parameters:
      ra (float): Right ascension of the object (in hours)
      dec (float): Declination of the object (in degrees)
      lat (float): Observer's latitude (in degrees)
      lon (float): Observer's longitude (in degrees)
      local_date (str): The local date for the calculation in 'YYYY-MM-DD' format
      tz_name (str): Time zone name (e.g., 'Asia/Singapore')
      altitude_threshold (float): The minimum altitude (in degrees) at which the object is considered observable

    Returns:
      observable_duration (timedelta): Total duration when the object's altitude exceeds the threshold.
      max_altitude (float): The maximum altitude (in degrees) attained by the object during the night.
    """

    # Create a timezone object using the provided tz_name
    local_tz = pytz.timezone(tz_name)
    date_obj = datetime.strptime(local_date, "%Y-%m-%d")

    # Retrieve sun events using the cached version (make sure calculate_sun_events_cached is updated too)
    sun_events = calculate_sun_events_cached(local_date, tz_name, lat, lon)
    dusk_str = sun_events.get("astronomical_dusk")
    dawn_str = sun_events.get("astronomical_dawn")

    if not dusk_str or not dawn_str:
        print(f"[WARN] Missing sun events for {local_date}")
        return timedelta(0), 0

    # Convert dusk and dawn strings to time objects
    dusk_time = datetime.strptime(dusk_str, "%H:%M").time()
    dawn_time = datetime.strptime(dawn_str, "%H:%M").time()

    # Combine the local date with dusk and dawn times
    dusk_dt = local_tz.localize(datetime.combine(date_obj, dusk_time))
    dawn_dt = local_tz.localize(datetime.combine(date_obj, dawn_time))
    # If dawn is less than or equal to dusk (i.e. dawn occurs after midnight), add one day to dawn_dt
    if dawn_dt <= dusk_dt:
        dawn_dt += timedelta(days=1)

    # Define a sample interval (e.g. every 10 minutes)
    sample_interval = timedelta(minutes=10)
    times = []
    current = dusk_dt
    while current <= dawn_dt:
        times.append(current)
        current += sample_interval

    if not times:
        return timedelta(0), 0

    # Convert the sample times to UTC for Astropy
    times_utc = Time([t.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S') for t in times],
                     format='isot', scale='utc')

    # Create an EarthLocation and SkyCoord for the object
    location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

    # Transform the object coordinates into the AltAz frame for all sample times
    frame = AltAz(obstime=times_utc, location=location_obj)
    altaz = sky_coord.transform_to(frame)
    altitudes = altaz.alt.deg

    # Use the provided threshold to determine which samples are "observable"
    mask = np.array(altitudes) > altitude_threshold
    observable_minutes = int(np.sum(mask) * 10)
    max_altitude = float(np.max(altitudes)) if len(altitudes) > 0 else 0

    return timedelta(minutes=observable_minutes), max_altitude

