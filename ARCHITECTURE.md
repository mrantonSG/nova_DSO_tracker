# Architecture Reference

## 1. Project Overview

Nova DSO Tracker is a self-hosted Flask web application for astrophotographers to plan deep-sky imaging sessions, track observable objects, log session data, and manage equipment. It uses SQLAlchemy with SQLite for persistence, Jinja2 templates for server-rendered HTML, and background daemon threads for weather data, heatmap caching, and update checks. The app runs as a single-process Flask dev server or behind gunicorn in Docker; all state lives in `instance/app.db` (plus `instance/users.db` in multi-user mode). Deployment is a single container with a mounted `instance/` volume — no external databases, message queues, or cloud services required.

## 2. Blueprint Map

Eight blueprints, all registered with no URL prefix. Route functions live in `nova/blueprints/` (blueprint refactor is complete — routes are no longer in `nova/__init__.py`).

### core_bp — `nova/blueprints/core.py` (2,346 lines)

Dashboard, auth, object search, location management, graph views.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Main dashboard |
| GET/POST | `/config_form` | User configuration |
| GET/POST | `/login` | User login |
| POST | `/logout` | User logout |
| GET | `/sso/login` | SSO login (JWT) |
| POST | `/search_object` | SIMBAD/object search |
| POST | `/confirm_object` | Confirm and add object |
| POST | `/fetch_object_details` | Fetch single object details |
| POST | `/fetch_all_details` | Fetch all objects' details |
| GET | `/stream_fetch_details` | SSE stream for bulk fetch |
| GET | `/graph_dashboard/<name>` | Altitude graph page |
| GET | `/get_imaging_opportunities/<name>` | Best imaging dates |
| GET | `/get_date_info/<name>` | Date-specific object info |
| GET | `/sun_events` | Sun rise/set/transit |
| GET | `/get_locations` | User's locations |
| POST | `/set_location` | Set active location |
| GET | `/get_outlook_data` | Weather outlook |
| POST | `/proxy_focus` | Proxy to Stellarium |
| GET | `/generate_ics/<name>` | Generate .ics calendar |
| GET | `/analytics` | Analytics dashboard |
| GET | `/set_language/<lang>` | Set language preference |
| POST | `/trigger_update` | Trigger background update |
| POST | `/update_project` | Update project fields |
| POST | `/update_project_active` | Toggle project active |
| POST | `/api/journal/custom-filters` | Add custom journal filter |
| DELETE | `/api/journal/custom-filters/<key>` | Delete custom filter |
| GET | `/uploads/<user>/<file>` | Serve uploaded images |
| GET | `/favicon.ico` | Favicon |

### api_bp — `nova/blueprints/api.py` (3,626 lines)

JSON API endpoints consumed by frontend JavaScript.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/get_plot_data/<name>` | Altitude plot data |
| GET | `/api/get_monthly_plot_data/<name>` | Monthly plot data |
| GET | `/api/get_yearly_heatmap_chunk` | Heatmap tile data |
| GET | `/api/get_object_data/<name>` | Single object details |
| GET | `/api/get_object_list` | All user objects |
| GET | `/api/get_observable_objects` | Currently observable |
| GET | `/api/get_framing/<name>` | Saved framing config |
| POST | `/api/save_framing` | Save framing config |
| POST | `/api/delete_framing` | Delete framing |
| GET | `/api/get_saved_views` | List saved graph views |
| POST | `/api/save_saved_view` | Save a graph view |
| POST | `/api/delete_saved_view` | Delete a saved view |
| GET | `/api/get_shared_items` | Community shared items |
| POST | `/api/import_item` | Import shared item |
| GET | `/api/get_weather_forecast` | Weather forecast JSON |
| GET | `/api/get_moon_data` | Moon phase/position |
| GET | `/api/calibration_star` | Calibration star data |
| GET | `/api/get_desktop_data_batch` | Batch data for desktop |
| GET | `/api/mobile_data_chunk` | Paginated mobile data |
| GET | `/api/mobile_status` | Mobile status check |
| POST | `/api/bulk_fetch_details` | Bulk SIMBAD fetch |
| POST | `/api/bulk_update_objects` | Bulk object updates |
| POST | `/api/update_object` | Update single object |
| POST | `/api/merge_objects` | Merge duplicate objects |
| GET | `/api/find_duplicates` | Detect duplicates |
| GET | `/api/journal/objects` | Objects for journal dropdown |
| POST | `/api/parse_asiair_log` | Parse ASIAir log file |
| GET | `/api/session/<id>/log-analysis` | Log analysis results |
| GET | `/api/latest_version` | Version check |
| GET | `/api/help/<topic>` | Help content |
| GET | `/api/help/img/<file>` | Help images |
| POST | `/api/internal/provision_user` | SSO user provisioning |
| POST | `/api/internal/deprovision_user` | SSO user removal |
| POST | `/telemetry/ping` | Telemetry beacon |
| GET | `/telemetry/debug` | Telemetry debug |

### journal_bp — `nova/blueprints/journal.py` (1,081 lines)

Session logging with equipment, weather, and image data.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/journal` | Session list |
| GET/POST | `/journal/add` | New session |
| GET/POST | `/journal/add_for_target/<name>` | New session for object |
| GET/POST | `/journal/edit/<id>` | Edit session |
| POST | `/journal/delete/<id>` | Delete session |
| POST | `/journal/duplicate/<id>` | Duplicate session |
| POST | `/journal/add_project` | Create project from session |
| GET | `/journal/report_page/<id>` | Session report |
| GET | `/journal/download_csv/<type>/<id>` | CSV export |

### mobile_bp — `nova/blueprints/mobile.py` (739 lines)

Touch-optimized views for phone/tablet use.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/m` | Mobile dashboard |
| GET | `/m/add_object` | Add object |
| GET | `/m/object/<name>` | Object detail |
| GET | `/m/up_now` | What's up right now |
| GET | `/m/outlook` | Weather outlook |
| GET | `/m/location` | Location info |
| GET | `/m/framing_coords/<name>` | Framing tool |
| GET | `/m/mosaic/<name>` | Mosaic planner |
| GET | `/m/edit_notes/<name>` | Edit object notes |
| GET/POST | `/m/journal/new` | Quick journal entry |
| GET | `/sw.js` | Service worker |

### projects_bp — `nova/blueprints/projects.py` (382 lines)

Project management tying objects to multi-session imaging efforts.

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/project/<id>` | Project detail/overview |
| POST | `/project/delete/<id>` | Delete project |
| GET | `/project/report_page/<id>` | Project report |

### tools_bp — `nova/blueprints/tools.py` (1,261 lines)

Equipment CRUD, YAML/CSV import/export, database maintenance.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/add_component` | Add equipment piece |
| POST | `/update_component` | Update equipment |
| POST | `/delete_component` | Delete equipment |
| POST | `/add_rig` | Create rig (scope+camera) |
| POST | `/delete_rig` | Delete rig |
| GET | `/get_rig_data` | All rigs/components |
| POST | `/set_rig_sort_preference` | Sort preference |
| GET | `/download_config` | Export objects as YAML |
| POST | `/import_config` | Import objects from YAML |
| GET | `/download_rig_config` | Export equipment YAML |
| POST | `/import_rig_config` | Import equipment YAML |
| GET | `/download_journal` | Export journal CSV |
| POST | `/import_journal` | Import journal CSV |
| GET | `/download_journal_photos` | Export session photos |
| POST | `/import_journal_photos` | Import session photos |
| POST | `/import_catalog/<pack>` | Import catalog pack |
| GET | `/tools/export/<user>` | Full user YAML export |
| POST | `/tools/import` | Full user YAML import |
| POST | `/tools/repair_db` | Database repair |
| POST | `/upload_editor_image` | Rich-text image upload |

### admin_bp — `nova/blueprints/admin.py` (122 lines)

User management (multi-user mode only).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/users` | User list |
| POST | `/admin/users/create` | Create user |
| POST | `/admin/users/<id>/toggle` | Activate/deactivate |
| POST | `/admin/users/<id>/reset-password` | Reset password |
| POST | `/admin/users/<id>/delete` | Delete user |

### ai_bp — `nova/ai/routes.py` (1,499 lines)

AI-powered features via OpenAI-compatible API.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/ai/best_objects` | Recommend objects for tonight |
| POST | `/api/ai/session-summary` | Summarize a session |
| POST | `/api/ai/notes` | Generate DSO notes |
| GET | `/api/ai/status` | AI service health |
| GET | `/api/ai/prefilter_debug` | Debug AI prefiltering |

## 3. Data Models

All models in `nova/models.py`. Two SQLite databases: `instance/app.db` (main) and `instance/users.db` (multi-user auth only). The `User` class is conditionally defined in `nova/auth.py` — as a Flask-SQLAlchemy model backed by `users.db` in multi-user mode, or as a plain `UserMixin` in single-user mode.

### DbUser (`users`)
Central user record. Every user-scoped model has a `user_id` FK with cascade delete.

| Field | Notes |
|-------|-------|
| `username` | String(80), unique |
| `password_hash` | Nullable (set in multi-user mode) |
| `active` | Boolean, default True |

**Relationships**: locations, objects, saved_views, components, rigs, sessions (JournalSession), ui_prefs, projects — all one-to-many, cascade delete.

### Project (`projects`)
Groups multiple imaging sessions around one target.

| Field | Notes |
|-------|-------|
| `id` | UUID string (not auto-increment) |
| `name`, `target_object_name` | |
| `status` | "In Progress", "Complete", etc. |
| `description_notes`, `framing_notes`, `processing_notes`, `goals` | Rich text (Trix editor) |
| `final_image_file` | Path to uploaded image |

**Relationships**: user (DbUser), sessions (JournalSession).

### JournalSession (`journal_sessions`)
The fattest model. Stores a single imaging session with weather, equipment, and image metadata.

| Field | Notes |
|-------|-------|
| `date_utc` | Session date |
| `object_name` | Target |
| `notes` | Free text |
| `session_image_file` | Uploaded photo |
| Location/weather | `location_name`, `seeing_observed_fwhm`, `sky_sqm_observed`, `moon_*`, `weather_notes` |
| Equipment | `filter_used_session`, `guiding_rms_avg_arcsec`, `gain_setting`, `offset_setting`, `camera_temp_*`, `binning_session` |
| Per-filter subs | L, R, G, B, Ha, OIII, SII counts + exposure times |
| `custom_filter_data` | JSON string for user-defined filters |
| Rig snapshot | `rig_*_snapshot` fields — denormalized copy of rig at session time |
| Integration logs | `asiair_log_content`, `phd2_log_content`, `nina_log_content`, `log_analysis_cache` |
| `draft` | Boolean, for WIP sessions |

**Relationships**: user (DbUser), project (Project), rig_snapshot (Rig).

### AstroObject (`astro_objects`)
A tracked deep-sky object.

| Field | Notes |
|-------|-------|
| `object_name` | Catalog designation (e.g. "M 31") |
| `common_name` | "Andromeda Galaxy" |
| `ra_hours`, `dec_deg` | J2000 coordinates |
| `type`, `constellation`, `magnitude`, `size`, `sb` | Catalog metadata |
| `active_project`, `project_name` | Linked project |
| `enabled` | Soft-hide toggle |
| Sharing | `is_shared`, `original_user_id`, `original_item_id` |
| Images/descriptions | `image_url`, `image_credit`, `description_text`, etc. |
| `catalog_sources`, `catalog_info` | JSON from SIMBAD/VizieR |

**Relationships**: user (DbUser).

### Component (`components`)
A single piece of equipment (telescope, camera, or reducer/extender).

| Field | Notes |
|-------|-------|
| `stable_uid` | UUID, survives import/export |
| `kind` | "telescope", "camera", or "reducer_extender" |
| Telescope fields | `aperture_mm`, `focal_length_mm` |
| Camera fields | `sensor_width_mm`, `sensor_height_mm`, `pixel_size_um` |
| Reducer fields | `factor` |
| Sharing | `is_shared`, `original_user_id`, `original_item_id` |

**Relationships**: user (DbUser), rigs_using (Rig).

### Rig (`rigs`)
Combines telescope + camera + optional reducer into an imaging rig. Also supports guide scope/camera.

| Field | Notes |
|-------|-------|
| `stable_uid` | UUID |
| `rig_name` | |
| `telescope_id`, `camera_id`, `reducer_extender_id` | FK → components |
| Computed | `effective_focal_length`, `f_ratio`, `image_scale`, `fov_w_arcmin` |
| Guide optics | `guide_telescope_id`, `guide_camera_id`, `guide_is_oag` |

**Relationships**: user (DbUser), telescope/camera/reducer_extender/guide_telescope/guide_camera → Component.

### Location (`locations`)

| Field | Notes |
|-------|-------|
| `stable_uid` | UUID |
| `name`, `lat`, `lon`, `timezone` | |
| `altitude_threshold` | Min altitude for visibility |
| `bortle_scale` | 1–9 sky darkness |
| `is_default`, `active` | |

**Relationships**: user (DbUser), horizon_points (HorizonPoint).

### HorizonPoint (`horizon_points`)
Azimuth/altitude pairs defining an obstructed horizon for a location.

| Field | Notes |
|-------|-------|
| `location_id` | FK → locations |
| `az_deg`, `alt_min_deg` | |

### SavedView (`saved_views`)
Persisted graph/dashboard configurations.

| Field | Notes |
|-------|-------|
| `name`, `description` | |
| `settings_json` | Serialized view config |
| `is_shared` | Community sharing |

### SavedFraming (`saved_framings`)
Framing tool state per object (survey, rotation, mosaic, image adjustments).

| Field | Notes |
|-------|-------|
| `object_name`, `rig_id` | |
| `ra`, `dec`, `rotation` | |
| `survey`, `blend_survey`, `blend_opacity` | |
| Mosaic | `mosaic_cols`, `mosaic_rows`, `mosaic_overlap` |
| Image adj. | `img_brightness`, `img_contrast`, `img_gamma`, `img_saturation` |

### UiPref (`ui_prefs`)
Single JSON blob per user for UI preferences (theme, sidebar state, etc.).

### AnalyticsEvent (`analytics_event`), AnalyticsLogin (`analytics_login`)
GDPR-compliant counters (event_name + date, no user identifiers).

### UserCustomFilter (`user_custom_filters`)
User-defined filter bands (e.g. "L-Enhance") for journal session tracking.

## 4. Key Dependencies & Integrations

| Dependency | Purpose | Used In |
|------------|---------|---------|
| **Flask** + **SQLAlchemy** | Web framework + ORM | Everywhere |
| **astropy** + **ephem** | Astronomical coordinate transforms, altitude/azimuth, sun/moon events | `modules/astro_calculations.py`, heatmap worker |
| **astroquery** (SIMBAD, VizieR) | Object lookup by name, catalog cross-match | `modules/nova_data_fetcher.py` |
| **Open-Meteo API** | Free weather forecast (cloud cover, humidity, temp) | `nova/workers/weather.py` |
| **Plotly.js** | Altitude/heatmap charts in browser | Frontend (CDN/bundled) |
| **Chart.js** | Secondary charts | Frontend (bundled) |
| **Trix editor** | Rich text for project/session notes | Frontend (CDN) |
| **OpenAI-compatible API** | "Ask Nova" AI features (configurable provider) | `nova/ai/service.py` |
| **Flask-Login** + **bcrypt** | Authentication | `nova/auth.py` |
| **PyJWT** | SSO token verification | `nova/blueprints/core.py` (sso_login) |
| **Stellarium API** | Planetarium integration (local HTTP) | `nova/blueprints/core.py` (proxy_focus) |
| **Pillow** | Image processing (thumbnails, uploads) | `nova/helpers.py` |
| **paho-mqtt** | IoT/device integration | `nova/` (MQTT client) |
| **DeepL API** | UI translation | Translation routes |
| **gunicorn** | Production WSGI server | Docker/production |

Background workers (daemon threads in `nova/workers/`):

| Worker | Cycle | Purpose |
|--------|-------|---------|
| `weather.py` | 2 hours | Fetch Open-Meteo forecast for all active locations |
| `heatmap.py` | On-demand / 24h stale | Pre-compute yearly altitude heatmaps (JSON cache in `instance/cache/`) |
| `updates.py` | 24 hours | Check GitHub for new releases |
| `iers.py` | 24 hours | Refresh Earth rotation data for astropy precision |

## 5. Request Lifecycle

Tracing a dashboard page load (`GET /`):

1. **Before-request hooks** (`nova/__init__.py`):
   - `_fix_mode_switch_sessions()` — clears stale Flask session data if the app was switched between single/multi-user mode.
   - `load_global_request_context()` — resolves the current user. In single-user mode, auto-authenticates as "default". Calls `get_or_create_db_user()` to ensure a `DbUser` row exists. Loads `g.user_config` from `UiPref`. Sets `g.sampling_interval` and `g.telemetry_enabled`.
   - Telemetry hooks — bootstrap and periodic ping (fire-and-forget).

2. **Route handler** (`nova/blueprints/core.py:index`):
   - `load_full_astro_context()` — queries all locations (with horizon points eager-loaded) and all objects for the user, sets `g.locations`, `g.objects_list`, `g.objects_map`, `g.lat`, `g.lon`, `g.tz_name`.
   - Queries `JournalSession` (newest first), `Project`, `AstroObject`.
   - Serializes sessions to dicts with computed fields (common names, project names, ISO dates).
   - Computes the "observing night" boundary (before noon = previous night).

3. **Template render** (`templates/index.html` extends `templates/base.html`):
   - `base.html` provides: theme toggle script, CSS, nav bar, JS includes.
   - `index.html` injects `window.NOVA_INDEX` with session data, config, threshold.
   - Includes partials: `_heatmap_section.html`, `_inspiration_section.html`, `_journal_section.html`, `_objects_section.html`, `_project_subtab.html`.
   - Client-side JS (`dashboard.js`) fetches plot/heatmap data via API calls and renders Plotly charts.

**Data flow**: Route queries SQLAlchemy → serializes to dict → Jinja2 renders initial HTML → JS fetches `/api/get_plot_data/*` and `/api/get_yearly_heatmap_chunk` for interactive charts.

## 6. Known Complexity Hotspots

### `nova/blueprints/api.py` — 3,626 lines
The largest file in the project. Contains 35 routes spanning object data, framing, heatmap chunks, weather, sharing, journal helpers, log parsing, and internal provisioning. No clear internal grouping — it's a catch-all for anything JSON-shaped. Would benefit from splitting along domain lines (objects, framing, sharing, journal-API, admin-API).

### `nova/blueprints/core.py` — 2,346 lines
Second largest. Dashboard rendering, auth flows (login/SSO/logout), object search/confirm, graph views, and miscellaneous utilities. The `index()` function alone does substantial data preparation. Auth logic could live in `nova/auth.py`.

### `nova/__init__.py` — 3,583 lines
Still the single biggest file. Contains the app factory, all before-request hooks, context processors, helper functions (`get_or_create_db_user`, `load_full_astro_context`, `load_effective_settings`), in-memory cache management, and the blueprint registration block. Routes have been moved out, but supporting functions remain.

### `nova/log_parser.py` — 1,978 lines
Parsers for ASIAir, PHD2, and NINA log formats. Each parser is substantial and handles fragile real-world log formats. Tightly coupled to `JournalSession` field names.

### `templates/_journal_section.html` — 3,178 lines
The largest template by far. Renders the journal session table, inline edit forms, filter UI, and session detail panels. Significant amounts of inline JavaScript. Mixes presentation with client-side state management.

### `nova/migration.py` — 1,563 lines
Database migration logic for schema evolution. Runs imperative ALTER TABLE statements rather than using Alembic. Must be maintained in lockstep with model changes.

### `nova/ai/prompts.py` — 1,011 lines
Hard-coded prompt templates for AI features. Pure string data, but large and growing.

### Cross-cutting concerns
- **`g` object as shared state**: Before-request hooks stuff `g` with user, locations, objects, config. Every route and helper accesses `g` directly. This is implicit global state that makes individual functions hard to reason about in isolation.
- **Dual-mode complexity**: `SINGLE_USER_MODE` branches appear in auth, user provisioning, template rendering, and API responses. The conditional `User` class definition means type-checkers and IDEs struggle.
- **In-memory caches**: `nova/config.py` holds mutable dicts (`static_cache`, `weather_cache`, `nightly_curves_cache`) shared between threads. No locking; correctness depends on Python's GIL and the append-only nature of most writes.
