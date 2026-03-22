# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Run the app (dev mode, port 5001)
python nova.py

# Run via Docker
docker build -t nova . && docker run -p 5001:5001 -v $(pwd)/instance:/app/instance nova

# Production (gunicorn)
gunicorn -w 2 -k gthread --threads 4 -b 0.0.0.0:5001 nova:app
```

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_nova_api.py -v

# Run a single test
python -m pytest tests/test_nova_api.py::test_get_plot_data_returns_json -v

# Skip live-server tests (require running server at localhost:5001)
python -m pytest tests/ -v -k "not test_live_health"
```

Tests use in-memory SQLite via fixtures in `tests/conftest.py`. Key fixtures: `client` (single-user), `multi_user_client` (multi-user with auth), `db_session` (raw DB session). The `monkeypatch` calls target `nova.SINGLE_USER_MODE`, `nova.User`, etc.

## Architecture

**Nova DSO Tracker** is a Flask app for astrophotographers to track deep-sky objects, plan imaging, and log sessions.

### Package Structure (mid-refactor)

`nova.py` is a 14-line entry point that imports the app from the `nova/` package.

`nova/__init__.py` (~12,400 lines) contains the Flask app, all routes, and most helper functions. Routes are decorated with blueprint decorators (`@core_bp.route`, `@api_bp.route`, etc.) but the functions remain in `__init__.py`. This is a transitional state — route functions should eventually move into their respective `nova/blueprints/*.py` files.

### Key Modules

- **`nova/models.py`** — 11 SQLAlchemy models (DbUser, AstroObject, Location, Project, JournalSession, Component, Rig, etc.) + engine/session setup. Database is SQLite at `instance/app.db`.
- **`nova/config.py`** — Constants, paths, mutable cache dicts shared across modules. Caches (`static_cache`, `weather_cache`, `nightly_curves_cache`, etc.) are plain dicts mutated in-place by workers and routes.
- **`nova/helpers.py`** — Shared utilities: `get_db()`, YAML I/O, data conversion, settings loading.
- **`nova/workers/`** — Three background daemon threads: `weather.py` (2h cycle), `heatmap.py` (4h cycle), `updates.py` (24h cycle). Each accepts `app` as parameter for `app.app_context()`.
- **`modules/astro_calculations.py`** — Core astronomical math: altitude/azimuth, transit times, observable duration, sun events. Used by routes and heatmap worker.
- **`modules/nova_data_fetcher.py`** — SIMBAD/catalog data fetching for object details.

### Blueprints (6 total)

| Blueprint | Prefix | Routes |
|-----------|--------|--------|
| `core_bp` | (none) | `/`, `/config_form`, `/login`, `/graph_dashboard/<name>`, `/search_object`, etc. |
| `api_bp` | (none) | `/api/*`, `/telemetry/*` |
| `journal_bp` | (none) | `/journal/*` |
| `mobile_bp` | (none) | `/m/*` |
| `projects_bp` | (none) | `/project/*` |
| `tools_bp` | (none) | `/download_*`, `/import_*`, `/tools/*`, equipment CRUD |

Blueprints are registered at the bottom of `nova/__init__.py` (must be after all `@bp.route` decorators). `url_for()` calls use blueprint prefixes: `url_for('core.index')`, `url_for('api.get_plot_data', ...)`, etc.

### Dual User Mode

Controlled by `SINGLE_USER_MODE` in `instance/.env`:
- **Single-user** (`True`, default): No login, auto-authenticates as "default" user.
- **Multi-user** (`False`): Flask-Login with password auth, per-user data isolation, JWT for SSO.

The `User` class is defined conditionally inside `nova/__init__.py` — either as a `db.Model` (multi-user with Flask-SQLAlchemy using `instance/users.db`) or a plain `UserMixin` (single-user).

### Request Lifecycle

1. `_fix_mode_switch_sessions()` — guards against stale session IDs across mode switches
2. `load_global_request_context()` — sets `g.db_user`, `g.user_config`, `g.is_guest`; calls `get_or_create_db_user()` to provision new users
3. Route functions call `load_full_astro_context()` explicitly when they need locations/objects in `g`

### Import Chain (no circular imports)

```
nova.models → nova.config → nova.helpers → nova.workers.*
                                         → nova.__init__ (imports from all above)
```

Workers use lazy imports for functions from `nova.__init__` (e.g., `from nova import get_hybrid_weather_forecast` inside the function body).

### Data Flow

- **Config storage**: SQLAlchemy models → exported/imported as YAML files
- **Caching**: In-memory dicts in `nova/config.py`, mutated in-place by workers and routes
- **File storage**: `instance/uploads/` for images, `instance/cache/` for heatmap chunks (JSON), `instance/configs/` for legacy YAML

## Environment Configuration

Configuration lives in `instance/.env`. Key variables:
- `SINGLE_USER_MODE` — `True` or `False`
- `SECRET_KEY` — Flask session key
- `CALCULATION_PRECISION` — sampling interval in minutes (default 15)
- `TELEMETRY_ENABLED` — `true`/`false`
- `NOVA_CATALOG_URL` — catalog server URL
- `STELLARIUM_API_URL_BASE` — Stellarium integration URL

## Docker

Entry point: `gunicorn ... nova:app`. Must mount `instance/` volume for data persistence. Multi-arch images (amd64/arm64) published to Docker Hub via `.github/workflows/docker-publish.yml` on GitHub releases.

## Git Commits

Never add Claude as a co-author in commit messages. Do not include any `Co-authored-by: Claude` trailer or any Anthropic attribution in commits.