import os
import secrets
import threading

from decouple import config
from dotenv import load_dotenv

from nova.models import INSTANCE_PATH

# --- App version ---
APP_VERSION = "5.0.0"

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

# --- Keys & external config ---
SECRET_KEY = config('SECRET_KEY', default=secrets.token_hex(32))
STELLARIUM_ERROR_MESSAGE = os.getenv("STELLARIUM_ERROR_MESSAGE")
NOVA_CATALOG_URL = config('NOVA_CATALOG_URL', default='')

# --- File uploads ---
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- Limits ---
MAX_ACTIVE_LOCATIONS = 5

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
static_cache = BoundedCache(2000)
moon_separation_cache = BoundedCache(1000)
nightly_curves_cache = BoundedCache(2000)
cache_worker_status = {}
monthly_top_targets_cache = {}
config_cache = {}
config_mtime = {}
journal_cache = {}
journal_mtime = {}
LATEST_VERSION_INFO = {}
rig_data_cache = {}
weather_cache = {}
CATALOG_MANIFEST_CACHE = {"data": None, "expires": 0}

# --- Telemetry state ---
_telemetry_startup_once = threading.Event()

TELEMETRY_DEBUG_STATE = {
    'endpoint': None,
    'last_payload': None,
    'last_result': None,
    'last_error': None,
    'last_ts': None
}
