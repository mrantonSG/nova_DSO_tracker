# modules/rig_config.py

import os
import yaml
import uuid

rig_cache = {}
rig_mtime = {}

def get_rig_config_path(username, single_user_mode):
    """
    Returns the absolute path to the user's rig configuration file.
    This is the single source of truth for the rig filename.
    """
    if single_user_mode:
        # For single user mode, we can use a simpler, default name
        filename = "rig_default.yaml"
    else:
        # Using singular 'rig_' to match other config files like 'journal_'
        filename = f"rig_config_{username}.yaml"

    # Return the full, absolute path to the file
    return os.path.join(os.path.dirname(__file__), '..', filename)


def load_rig_config(username, single_user_mode):
    """Loads rig config from cache or file, checking for modifications."""
    filepath = get_rig_config_path(username, single_user_mode)

    # Caching logic
    last_modified = os.path.getmtime(filepath) if os.path.exists(filepath) else 0
    if filepath in rig_cache and last_modified <= rig_mtime.get(filepath, 0):
        return rig_cache[filepath]

    # Read from file if not in cache or if modified
    if not os.path.exists(filepath):
        return {'components': {'telescopes': [], 'cameras': [], 'reducers_extenders': []}, 'rigs': []}

    try:
        with open(filepath, 'r') as f:
            data = yaml.safe_load(f) or {}

        # Ensure default structure if file is empty or malformed
        if 'components' not in data:
            data['components'] = {'telescopes': [], 'cameras': [], 'reducers_extenders': []}
        if 'rigs' not in data:
            data['rigs'] = []

        # Update cache
        rig_cache[filepath] = data
        rig_mtime[filepath] = last_modified
        return data
    except Exception as e:
        print(f"❌ ERROR loading rig config '{filepath}': {e}")
        return {'components': {'telescopes': [], 'cameras': [], 'reducers_extenders': []}, 'rigs': []}

def save_rig_config(username, rig_data, single_user_mode):
    """Saves the rig configuration for a given user."""
    filepath = get_rig_config_path(username, single_user_mode)

    try:
        with open(filepath, "w", encoding="utf-8") as file:
            yaml.dump(rig_data, file, sort_keys=False, allow_unicode=True, indent=2)
        print(f"💾 Rig configuration saved to '{os.path.basename(filepath)}' successfully.")
    except Exception as e:
        print(f"❌ ERROR: Failed to save rig config '{filepath}': {e}")


def calculate_rig_data(rig, all_components):
    """Calculates and returns derived data for a single rig."""
    telescope = next((t for t in all_components.get('telescopes', []) if t['id'] == rig['telescope_id']), None)
    camera = next((c for c in all_components.get('cameras', []) if c['id'] == rig['camera_id']), None)
    reducer = next(
        (r for r in all_components.get('reducers_extenders', []) if r['id'] == rig.get('reducer_extender_id')), None)

    if not telescope or not camera:
        return {}

    focal_length = float(telescope['focal_length_mm'])
    if reducer:
        focal_length *= float(reducer['factor'])

    pixel_size = float(camera['pixel_size_um'])
    sensor_width = float(camera['sensor_width_mm'])
    sensor_height = float(camera['sensor_height_mm'])

    # Calculate image scale in arcseconds per pixel
    image_scale = (pixel_size / focal_length) * 206.265

    # Calculate Field of View (FOV) in arcminutes
    fov_w_arcmin = (sensor_width / focal_length) * 3437.75
    fov_h_arcmin = (sensor_height / focal_length) * 3437.75

    return {
        "effective_focal_length": focal_length,
        "f_ratio": focal_length / float(telescope['aperture_mm']),
        "image_scale": image_scale,
        "fov_w_arcmin": fov_w_arcmin,
        "fov_h_arcmin": fov_h_arcmin,
    }