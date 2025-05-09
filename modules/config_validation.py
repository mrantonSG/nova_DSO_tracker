from cerberus import Validator

config_schema = {
    'altitude_threshold': {'type': 'integer', 'required': True},
    'default_location': {'type': 'string', 'required': True},
    'imaging_criteria': {
        'type': 'dict',
        'required': True,
        'schema': {
            'max_moon_illumination': {'type': 'integer', 'min': 0, 'max': 100, 'required': True},
            'min_angular_distance': {'type': 'integer', 'min': 0, 'required': True},
            'min_max_altitude': {'type': 'integer', 'min': 0, 'max': 90, 'required': True},
            'min_observable_minutes': {'type': 'integer', 'min': 0, 'required': True},
            'search_horizon_months': {'type': 'integer', 'min': 0, 'required': True},
        }
    },
    'locations': {
        'type': 'dict',
        'required': True,
        'valuesrules': {
            'type': 'dict',
            'schema': {
                'lat': {'type': 'float', 'required': True},
                'lon': {'type': 'float', 'required': True},
                'timezone': {'type': 'string', 'required': True},
            }
        }
    },
    'objects': {
        'type': 'list',
        'required': True,
        'schema': {
            'type': 'dict',
            'schema': {
                'Object': {'type': 'string', 'required': True},
                'Name': {'type': 'string', 'nullable': True, 'empty': True},
                'RA': {
                    'anyof': [
                        {'type': 'float'},
                        {'type': 'string'}
                    ],
                    'nullable': True,
                    'empty': True
                },
                'DEC': {
                    'anyof': [
                        {'type': 'float'},
                        {'type': 'string'}
                    ],
                    'nullable': True,
                    'empty': True
                },
                'Project': {'type': 'string', 'nullable': True, 'empty': True},
                'Type': {'type': 'string', 'nullable': True, 'empty': True},

                # ⬇️ New optional enrichment fields
                'surface_brightness': {'type': 'float', 'required': False},
                'fov_minimum': {'type': 'string', 'required': False},
                'recommended_aperture': {'type': 'string', 'required': False},
                'base_integration_f5': {'type': 'float', 'required': False},
                'mag_source': {'type': 'string', 'required': False},
                'size_source': {'type': 'string', 'required': False},
                'type_source': {'type': 'string', 'required': False},
            }
        }
    }
}

def validate_config(config_data):
    v = Validator(config_schema, allow_unknown=True)
    if not v.validate(config_data):
        return False, v.errors
    return True, v.document