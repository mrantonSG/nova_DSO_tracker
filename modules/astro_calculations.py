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
import copy

def calculate_transit_time(ra, dec, lat, lon, tz_name, local_date_str):
    """
    Calculates the meridian transit time for a given object, location, and date.
    """
    try:
        observer = ephem.Observer()
        observer.lat = str(lat)
        observer.lon = str(lon)
        observer.elevation = 0

        local_tz = pytz.timezone(tz_name)

        # KEY FIX: Set the observer's date to noon on the specific local_date_str.
        # This provides a stable starting point for finding the correct transit for that night.
        date_obj = datetime.strptime(local_date_str, '%Y-%m-%d')
        noon_local = local_tz.localize(date_obj.replace(hour=12, minute=0, second=0, microsecond=0))
        observer.date = noon_local.astimezone(pytz.utc)

        body = ephem.FixedBody()
        body._ra = ephem.hours(str(ra))
        body._dec = ephem.degrees(str(dec))
        #body.compute(observer)

        transit_time_utc = observer.next_transit(body).datetime()
        transit_time_local = transit_time_utc.replace(tzinfo=pytz.utc).astimezone(local_tz)

        return transit_time_local.strftime('%H:%M')

    except (ephem.AlwaysUpError, ephem.NeverUpError):
        # For circumpolar or never-rising objects, we can calculate the highest point differently.
        # For simplicity, returning the time of max altitude from a different function might be better,
        # but for now, we can try to find the meridian passing time.
        # This part of the logic can be complex; let's stick to the primary fix for now.
        return "N/A"
    except Exception as e:
            print(f"DEBUG: Error in calculate_transit_time: {e}")  # Add this line to see the error
            return "N/A"

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
    """
    FIXED: Checks if a value is already a float or can be converted to a float.
    This will now correctly return False for "D:M:S" or "H M S" strings.
    """
    if isinstance(value, (np.float64, float)):
        return True

    # Handle None before str() conversion
    if value is None:
        return False

    try:
        # Check if str(value) can be a float
        float(str(value))
        return True
    except (ValueError, TypeError):
        # Catches conversion errors for "45:30:00" or "05 35 17"
        return False

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
    """
    FIXED: Converts a string in D:M:S or D:M format to decimal degrees.
    Also handles inputs that are already decimal (as float or string).
    """
    # Handle None input
    if dms is None:
        return 0.0

    # Handle if it's already a float or np.float
    if isinstance(dms, (np.float64, float)):
        return float(dms)

    # Convert to string for parsing
    dms_str = str(dms).strip()

    # Check if it's already a decimal string
    try:
        return float(dms_str)
    except ValueError:
        pass  # It's not a simple float string, so we parse as D:M:S

    # Now, parse as D:M:S
    try:
        parts = dms_str.split(':')

        # Handle negative sign
        sign = -1 if parts[0].strip().startswith('-') else 1

        if len(parts) == 1:
            # This should have been caught by float(dms_str) but as a fallback
            d = float(parts[0])
            return d

        elif len(parts) == 2:
            # D:M format
            d = float(parts[0])
            m = float(parts[1])
            s = 0.0

        elif len(parts) == 3:
            # D:M:S format
            d = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])

        else:
            # Bad format
            return 0.0

        # Calculate degrees. abs(d) handles the negative sign correctly.
        return sign * (abs(d) + (m / 60.0) + (s / 3600.0))

    except (ValueError, TypeError, AttributeError):
        # Handles "abc:def" or other junk
        return 0.0

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

    # --- Start of Fix ---

    # Calculate Astronomical Dawn
    obs.horizon = '-18'
    try:
        astro_dawn = obs.next_rising(sun, use_center=True)
        astro_dawn_local = ephem_to_local(astro_dawn, tz_name).strftime('%H:%M')
    except (ephem.AlwaysUpError, ephem.NeverUpError):
        astro_dawn_local = "N/A" # Sun never rises to/sets from -18 deg

    # Calculate Sunrise
    obs.horizon = '-0.833'
    try:
        sunrise = obs.next_rising(sun, use_center=True)
        sunrise_local = ephem_to_local(sunrise, tz_name).strftime('%H:%M')
    except (ephem.AlwaysUpError, ephem.NeverUpError):
        sunrise_local = "N/A" # Circumpolar (never sets)

    # Calculate Transit (Noon)
    obs.horizon = '0' # Horizon doesn't matter for transit, but reset
    obs.date = midnight_utc # Reset date to start of day for next_transit
    try:
        transit = obs.next_transit(sun)
        transit_local = ephem_to_local(transit, tz_name).strftime('%H:%M')
    except (ephem.AlwaysUpError, ephem.NeverUpError):
        transit_local = "N/A" # Should not happen for sun, but safe

    # Set observer date to noon for finding *next* setting
    noon_local = local_tz.localize(datetime.combine(local_date, datetime.strptime("12:00", "%H:%M").time()))
    noon_utc = noon_local.astimezone(pytz.utc)
    obs.date = noon_utc

    # Calculate Sunset
    obs.horizon = '-0.833'
    try:
        sunset = obs.next_setting(sun, use_center=True)
        sunset_local = ephem_to_local(sunset, tz_name).strftime('%H:%M')
    except (ephem.AlwaysUpError, ephem.NeverUpError):
        sunset_local = "N/A" # Circumpolar (never sets)

    # Calculate Astronomical Dusk
    obs.horizon = '-18'
    try:
        astro_dusk = obs.next_setting(sun, use_center=True)
        astro_dusk_local = ephem_to_local(astro_dusk, tz_name).strftime('%H:%M')
    except (ephem.AlwaysUpError, ephem.NeverUpError):
        astro_dusk_local = "N/A" # Sun never sets to -18 deg

    # --- End of Fix ---

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


def get_common_time_arrays(tz_name, local_date, sampling_interval_minutes=15):
    """
    Generate two arrays of times for a given local date and time zone,
    using a configurable sampling interval.
    """
    local_tz = pytz.timezone(tz_name)
    base_date = datetime.strptime(local_date, '%Y-%m-%d')
    start_time = local_tz.localize(datetime.combine(base_date, datetime.min.time()).replace(hour=12))

    # Calculate number of samples based on the interval
    samples_per_hour = 60 / sampling_interval_minutes
    num_samples = int(25 * samples_per_hour)
    times_local = [start_time + timedelta(minutes=sampling_interval_minutes * i) for i in range(num_samples)]

    times_utc = Time([t.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S') for t in times_local],
                     format='isot', scale='utc')

    return times_local, times_utc


def calculate_observable_duration_vectorized(ra, dec, lat, lon, local_date, tz_name, altitude_threshold,
                                             sampling_interval_minutes=15, horizon_mask=None):
    """
    Calculates observable duration, max altitude, and start/end times,
    now with support for a custom horizon mask.
    """
    local_tz = pytz.timezone(tz_name)
    date_obj = datetime.strptime(local_date, "%Y-%m-%d")

    sun_events = calculate_sun_events_cached(local_date, tz_name, lat, lon)
    dusk_str = sun_events.get("astronomical_dusk")
    dawn_str = sun_events.get("astronomical_dawn")

    if not dusk_str or not dawn_str:
        return timedelta(0), 0, None, None

    dusk_time = datetime.strptime(dusk_str, "%H:%M").time()
    dawn_time = datetime.strptime(dawn_str, "%H:%M").time()

    dusk_dt = local_tz.localize(datetime.combine(date_obj, dusk_time))
    dawn_dt = local_tz.localize(datetime.combine(date_obj, dawn_time))
    if dawn_dt <= dusk_dt:
        dawn_dt += timedelta(days=1)

    sample_interval = timedelta(minutes=sampling_interval_minutes)
    times = []
    current = dusk_dt
    while current <= dawn_dt:
        times.append(current)
        current += sample_interval

    if not times:
        return timedelta(0), 0, None, None

    times_utc = Time([t.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S') for t in times],
                     format='isot', scale='utc')
    location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    frame = AltAz(obstime=times_utc, location=location_obj)
    altaz = sky_coord.transform_to(frame)
    altitudes = altaz.alt.deg
    azimuths = altaz.az.deg

    # --- NEW HORIZON MASK LOGIC ---
    if horizon_mask and len(horizon_mask) > 1:
        # Sort mask by azimuth just in case it's not already
        sorted_mask = sorted(copy.deepcopy(horizon_mask), key=lambda p: p[0])

        # If any altitude in the mask is 0, replace it with the baseline threshold
        for point in sorted_mask:
            if point[1] == 0:
                point[1] = altitude_threshold

        # Calculate the true minimum altitude for each point in time using the mask
        min_altitudes = np.array([interpolate_horizon(az, sorted_mask, altitude_threshold) for az in azimuths]) # <-- FIXED LINE
    else:
        # If no mask, the minimum altitude is the same for all azimuths
        min_altitudes = np.full_like(altitudes, altitude_threshold)

    # An object is observable if its altitude is above the calculated minimum for its azimuth
    mask = altitudes >= min_altitudes
    # --- END OF NEW LOGIC ---

    observable_indices = np.where(mask)[0]
    observable_from, observable_to = (None, None)
    if observable_indices.size > 0:
        observable_from = times[observable_indices[0]]
        observable_to = times[observable_indices[-1]]

    observable_minutes = int(np.sum(mask) * sampling_interval_minutes)

    # Calculate max altitude only during the observable (unobstructed) period
    max_altitude = float(np.max(altitudes[mask])) if observable_indices.size > 0 else 0

    return timedelta(minutes=observable_minutes), max_altitude, observable_from, observable_to

def interpolate_horizon(azimuth, horizon_mask, default_altitude):
    if not horizon_mask:
        return default_altitude

    # Create a deep copy to avoid modifying the original config data
    mask_copy = copy.deepcopy(horizon_mask)

    # Check for the special '0' value and replace it with the baseline
    for point in mask_copy:
        if point[1] == 0:
            point[1] = default_altitude

    # Sort the processed mask
    sorted_mask = sorted(mask_copy, key=lambda p: p[0])

    # Build a complete 0-360 degree profile for interpolation
    profile = [[0, default_altitude]]

    # If the user has a mask, add points to create the "walls"
    if sorted_mask:
        # Add a point on the ground just before the first obstruction begins
        profile.append([sorted_mask[0][0] - 0.001, default_altitude])

        # Add all the points from the user's mask
        profile.extend(sorted_mask)

        # Add a point on the ground just after the last obstruction ends
        profile.append([sorted_mask[-1][0] + 0.001, default_altitude])

    # Add the final point to complete the 360-degree profile
    profile.append([360, default_altitude])

    # Find the two points in our complete profile that the azimuth falls between
    p1, p2 = None, None
    for i in range(len(profile) - 1):
        if profile[i][0] <= azimuth <= profile[i+1][0]:
            p1 = profile[i]
            p2 = profile[i+1]
            break

    if not p1:
        return default_altitude # Fallback

    az1, alt1 = p1
    az2, alt2 = p2

    # If the azimuth points are identical or extremely close, return the first altitude
    # This prevents division-by-zero errors with very steep curves.
    if abs(az2 - az1) < 1e-9:
        return alt1

    # Standard linear interpolation formula
    return alt1 + (alt2 - alt1) * ((azimuth - az1) / (az2 - az1))