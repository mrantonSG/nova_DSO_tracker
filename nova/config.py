import os
import secrets
import threading

from decouple import config
from dotenv import load_dotenv

from nova.models import INSTANCE_PATH

# --- App version ---
APP_VERSION = "6.2.0"

# --- Directories ---
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config_templates")
CACHE_DIR = os.path.join(INSTANCE_PATH, "cache")
CONFIG_DIR = os.path.join(INSTANCE_PATH, "configs")
BACKUP_DIR = os.path.join(INSTANCE_PATH, "backups")
UPLOAD_FOLDER = os.path.join(INSTANCE_PATH, 'uploads')

# --- .env handling ---
ENV_FILE = os.path.join(INSTANCE_PATH, ".env")
load_dotenv(dotenv_path=ENV_FILE)

FIRST_RUN_ENV_CREATED = False

# --- Mode ---
SINGLE_USER_MODE = config('SINGLE_USER_MODE', default='True') == 'True'

ADMIN_USERS_RAW = config('ADMIN_USERS', default='admin')
ADMIN_USERS = {u.strip() for u in ADMIN_USERS_RAW.split(",") if u.strip()}
USER_ADMIN_USERNAME = config('USER_ADMIN_USERNAME', default='admin')
USER_ADMIN_PASSWORD = config('USER_ADMIN_PASSWORD', default='')

# --- Sentry (error reporting, multi-user only) ---
SENTRY_DSN = config('SENTRY_DSN', default='')

# --- Keys & external config ---
SECRET_KEY = config('SECRET_KEY', default=secrets.token_hex(32))
STELLARIUM_ERROR_MESSAGE = os.getenv("STELLARIUM_ERROR_MESSAGE")
NOVA_CATALOG_URL = config('NOVA_CATALOG_URL', default='')

# --- File uploads ---
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- Limits ---
MAX_ACTIVE_LOCATIONS = 5

# --- Timeouts ---
SIMBAD_TIMEOUT = 60  # SIMBAD queries can be slow

# --- Dither defaults ---
DEFAULT_DITHER_MAIN_SHIFT_PX = 10  # Default desired shift on main camera sensor (pixels)

# --- Bounded cache to prevent unbounded memory growth ---
class BoundedCache(dict):
    """Dict with max size — evicts oldest entries when full."""
    def __init__(self, maxsize=2000):
        super().__init__()
        self._maxsize = maxsize
    def __setitem__(self, key, value):
        if len(self) >= self._maxsize:
            to_remove = list(self.keys())[:self._maxsize // 10 or 1]
            for k in to_remove:
                del self[k]
        super().__setitem__(key, value)

# --- Mutable cache dicts (shared between workers and routes) ---
nightly_curves_cache = BoundedCache(2000)
observable_objects_cache = BoundedCache(200)
cache_worker_status = BoundedCache(500)
LATEST_VERSION_INFO = BoundedCache(10)
weather_cache = BoundedCache(1000)
astro_context_cache = BoundedCache(500)  # keyed by user_id (int)
CATALOG_MANIFEST_CACHE = {"data": None, "expires": 0}
DEFAULT_HTTP_TIMEOUT = 10  # Standard timeout for HTTP requests

# --- Translation status ---
TRANSLATION_STATUS = {
    'en': 'validated',  # English is the source language
    'de': 'auto',  # German translations auto-generated
    'fr': 'auto',       # French translations auto-generated
    'zh': 'auto',       # Chinese translations auto-generated
    'ja': 'auto',       # Japanese translations auto-generated
    'es': 'auto',       # Spanish translations auto-generated
}

# --- AI Configuration ---
AI_PROVIDER = config('AI_PROVIDER', default='anthropic')
AI_API_KEY = config('AI_API_KEY', default='')
AI_MODEL = config('AI_MODEL', default='claude-sonnet-4-20250514')
AI_BASE_URL = config('AI_BASE_URL', default='')
AI_ALLOWED_USERS = config('AI_ALLOWED_USERS', default='')

# --- Telemetry state ---
_telemetry_startup_once = threading.Event()

TELEMETRY_DEBUG_STATE = {
    'endpoint': None,
    'last_payload': None,
    'last_result': None,
    'last_error': None,
    'last_ts': None
}
