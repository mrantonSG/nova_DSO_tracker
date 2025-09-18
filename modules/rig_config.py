# modules/rig_config.py

import os
import yaml
import uuid
from datetime import datetime

rig_cache = {}
rig_mtime = {}
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_DIR = os.path.join(APP_ROOT, "instance", "configs")


def get_rig_config_path(username, is_single_user_mode):
    """Returns the absolute path to the user's rig config file."""
    if is_single_user_mode:
        filename = "rig_config_default.yaml"
    else:
        filename = f"rig_config_{username}.yaml"

    # This now correctly points to /instance/configs/rig_config_...
    return os.path.join(CONFIG_DIR, filename)


def load_rig_config(username, is_single_user_mode):
    """Loads the rig configuration file for the given user."""
    filepath = get_rig_config_path(username, is_single_user_mode)

    # Ensure the directory exists before trying to read from it
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    if not os.path.exists(filepath):
        # If the file doesn't exist, return a default empty structure
        return {'components': {'telescopes': [], 'cameras': [], 'reducers_extenders': []}, 'rigs': []}

    try:
        with open(filepath, 'r') as f:
            # Return empty dict if file is empty, to prevent errors
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"ERROR: Could not load rig config at {filepath}: {e}")
        return {}


def save_rig_config(username, data, is_single_user_mode):
    """Saves the rig configuration data for the given user."""
    filepath = get_rig_config_path(username, is_single_user_mode)

    # Ensure the directory exists before writing to it
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # Add creation/update timestamps for sorting by 'recent'
    for rig in data.get('rigs', []):
        if 'rig_id' not in rig or not rig.get('rig_id'):
            rig['rig_id'] = uuid.uuid4().hex
            rig['created_at'] = datetime.utcnow().isoformat() + 'Z'
        rig['updated_at'] = datetime.utcnow().isoformat() + 'Z'

    try:
        with open(filepath, 'w') as f:
            yaml.dump(data, f, sort_keys=False)
    except Exception as e:
        print(f"ERROR: Could not save rig config to {filepath}: {e}")


def calculate_rig_data(rig, all_components):
    """Calculates derived data like f-ratio and FOV for a given rig."""
    try:
        telescope = next((t for t in all_components['telescopes'] if t['id'] == rig['telescope_id']), None)
        camera = next((c for c in all_components['cameras'] if c['id'] == rig['camera_id']), None)

        if not telescope or not camera:
            return {}

        fl = float(telescope['focal_length_mm'])
        aperture = float(telescope['aperture_mm'])

        if rig.get('reducer_extender_id'):
            reducer = next((r for r in all_components['reducers_extenders'] if r['id'] == rig['reducer_extender_id']),
                           None)
            if reducer:
                fl *= float(reducer['factor'])

        f_ratio = fl / aperture if aperture > 0 else 0
        pixel_size = float(camera['pixel_size_um'])
        sensor_w = float(camera['sensor_width_mm'])
        sensor_h = float(camera['sensor_height_mm'])

        image_scale = (pixel_size / fl) * 206.265 if fl > 0 else 0
        fov_w_arcmin = (sensor_w / fl) * 3437.75 if fl > 0 else 0
        fov_h_arcmin = (sensor_h / fl) * 3437.75 if fl > 0 else 0

        return {
            'effective_focal_length': round(fl, 1),
            'f_ratio': round(f_ratio, 2),
            'image_scale': round(image_scale, 2),
            'fov_w_arcmin': round(fov_w_arcmin, 1),
            'fov_h_arcmin': round(fov_h_arcmin, 1)
        }
    except (ValueError, TypeError, KeyError) as e:
        print(f"Warning: Could not calculate data for rig '{rig.get('rig_name')}': {e}")
        return {}