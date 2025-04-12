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
        # Because location names (keys) are arbitrary, we validate the values using 'valuesrules'
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
                'Name': {'type': 'string', 'nullable': True},
                'RA': {'type': 'number', 'nullable': True},
                'DEC': {'type': 'number', 'nullable': True},
                'Project': {'type': 'string', 'nullable': True},
                'Type': {'type': ['string', 'null'], 'nullable': True},
            }
        }
    }
}

def validate_config(config_data):
    v = Validator(config_schema)
    if not v.validate(config_data):
        return False, v.errors
    return True, v.document