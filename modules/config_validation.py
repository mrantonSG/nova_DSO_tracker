from cerberus import Validator
import math  # For isnan


# Default values for imaging_criteria if it's missing entirely
# This function will be used by the default_setter
def get_default_imaging_criteria(field, value, error):
    if value is None:  # Or if field not present, Cerberus handles this
        return {
            'max_moon_illumination': 20,
            'min_angular_distance': 30,
            'min_max_altitude': 30,
            'min_observable_minutes': 60,
            'search_horizon_months': 6
        }
    return value  # Return existing value if present


# Coercion function for numeric fields that might be strings or placeholders
def coerce_to_float_or_none(value):
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, str):
        val_strip = value.strip().lower()
        if val_strip in ['n/a', 'none', 'nan', '']:  # Added 'nan' as a common placeholder
            return None
        try:
            f_val = float(value)
            return None if math.isnan(f_val) else f_val  # Handle if string like "NaN" converts to float NaN
        except ValueError:
            return None  # MODIFIED: If string is not a number and not a recognized placeholder, treat as None
    if value is None:
        return None
    # For any other type, or if it's already a float NaN that slipped through (unlikely from YAML)
    try:
        if math.isnan(value):
            return None
    except TypeError:  # value is not a number type
        pass

    # If it's some other type that's not float/int/str and not None,
    # let Cerberus's type validation handle it (it will likely fail if not float).
    return value


config_schema = {
    'altitude_threshold': {'type': 'integer', 'required': True, 'min': 0, 'max': 90},
    'default_location': {'type': 'string', 'required': True, 'empty': False},
    'imaging_criteria': {
        'type': 'dict',
        'required': False,
        'nullable': True,
        'default_setter': lambda doc: {
            'max_moon_illumination': 20,
            'min_angular_distance': 30,
            'min_max_altitude': 30,
            'min_observable_minutes': 60,
            'search_horizon_months': 6
        },
        'schema': {
            'max_moon_illumination': {'type': 'integer', 'min': 0, 'max': 100, 'required': True, 'default': 20},
            'min_angular_distance': {'type': 'integer', 'min': 0, 'max': 180, 'required': True, 'default': 30},
            'min_max_altitude': {'type': 'integer', 'min': 0, 'max': 90, 'required': True, 'default': 30},
            'min_observable_minutes': {'type': 'integer', 'min': 0, 'required': True, 'default': 60},
            'search_horizon_months': {'type': 'integer', 'min': 1, 'max': 24, 'required': True, 'default': 6},
        }
    },
    'locations': {
        'type': 'dict',
        'required': True,
        'allow_unknown': True,
        'valuesrules': {
            'type': 'dict',
            'required': True,
            'schema': {
                'lat': {'type': 'float', 'required': True, 'min': -90, 'max': 90},
                'lon': {'type': 'float', 'required': True, 'min': -180, 'max': 180},
                'timezone': {'type': 'string', 'required': True, 'empty': False},
            }
        }
    },
    'objects': {
        'type': 'list',
        'required': True,
        'schema': {
            'type': 'dict',
            'required': True,
            'schema': {
                'Object': {'type': 'string', 'required': True, 'empty': False},
                'Name': {'type': 'string', 'nullable': True, 'empty': True, 'default': ''},
                'RA': {
                    'anyof': [  # Keep anyof to allow Cerberus to attempt direct float match first
                        {'type': 'float', 'nullable': True},  # Allow None if it's already a float NaN or None
                        {'type': 'string', 'nullable': True, 'empty': True}
                    ],
                    'coerce': coerce_to_float_or_none,
                    'nullable': True,
                    'default': None
                },
                'DEC': {
                    'anyof': [
                        {'type': 'float', 'nullable': True},
                        {'type': 'string', 'nullable': True, 'empty': True}
                    ],
                    'coerce': coerce_to_float_or_none,
                    'nullable': True,
                    'default': None
                },
                'Project': {'type': 'string', 'nullable': True, 'empty': True, 'default': 'none'},
                'Type': {'type': 'string', 'nullable': True, 'empty': True, 'default': ''},

                'Magnitude': {'type': 'float', 'required': False, 'nullable': True, 'default': None,
                              'coerce': coerce_to_float_or_none},
                'Size': {'type': 'float', 'required': False, 'nullable': True, 'default': None,
                         'coerce': coerce_to_float_or_none},
                'SB': {'type': 'float', 'required': False, 'nullable': True, 'default': None,
                       'coerce': coerce_to_float_or_none},

                'fov_minimum': {'type': 'string', 'required': False, 'nullable': True, 'default': ''},
                'recommended_aperture': {'type': 'string', 'required': False, 'nullable': True, 'default': ''},
                'base_integration_f5': {'type': 'float', 'required': False, 'nullable': True, 'default': None,
                                        'coerce': coerce_to_float_or_none},
                'mag_source': {'type': 'string', 'required': False, 'nullable': True, 'default': ''},
                'size_source': {'type': 'string', 'required': False, 'nullable': True, 'default': ''},
                'type_source': {'type': 'string', 'required': False, 'nullable': True, 'default': ''},
            }
        }
    }
}


def validate_config(config_data):
    v = Validator(config_schema, allow_unknown=True)
    is_valid = v.validate(config_data, update=True)
    if not is_valid:
        return False, v.errors
    return True, v.document
