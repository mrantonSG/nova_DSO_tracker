# Architecture Decisions

This file logs *why* the codebase looks the way it does.
One line per decision. Format: Date | What changed | Why

---

## Foundation

2025-03-06 | App born as single-file Flask app (nova.py) with YAML config files | Simplest starting point for a personal astrophotography tool

2025-03-09 | SIMBAD queries forced to HTTPS | Prevent mixed-content warnings and match upstream API changes

2025-09-19 | User accounts moved from YAML files to SQLAlchemy (users.db) | Multi-user hosting requires proper auth, password hashing, and per-user data isolation

2025-09-19 | Dual-database split: app.db for domain data, users.db for auth | Keeps auth concerns isolated; users.db can be swapped for an external auth provider later without touching domain models

2025-09-19 | SINGLE_USER_MODE flag introduced | Allows same codebase to run as a zero-config personal tool (no login screen) or a multi-tenant hosted service

2025-09-21 | Docker packaging with gunicorn entry point | Enables deployment on Raspberry Pi and cloud servers without Python environment setup

## Storage & Schema

2025-10-20 | Hand-written SQL schema patches via `_run_schema_patches()` instead of Alembic | Alembic is overkill for a single-developer SQLite app; ALTER TABLE patches are easy to audit and don't require a migration directory infrastructure

2025-11-12 | YAML demoted to import/export only; SQLAlchemy is the source of truth | YAML was the original storage format but can't handle concurrent writes, relations, or queries; keeping YAML for import/export preserves backward compat with user backups

2026-02-12 | YAML import/export logic extracted into nova/migration.py | Separates the one-time migration path from the active codebase so the YAML dependency can eventually be dropped

## App Structure

2026-02-12 | Flask Blueprints introduced (core_bp, api_bp, journal_bp, mobile_bp, projects_bp, tools_bp) | First step in decomposing the monolith; blueprint decorators mark ownership even though route functions still live in `__init__.py`

2026-02-12 | nova.py reduced to 14-line entry point, app factory stays in nova/__init__.py | Allows `gunicorn nova:app` and `docker build` to import the package without side effects

2026-04-06 | Auth infrastructure extracted to nova/auth.py | Breaks circular imports between User model, Flask-Login, and route code

2026-04-08 | Route migration begins: api_bp and tools_bp routes move to nova/blueprints/*.py | Incremental extraction from the monolith; done per-blueprint to avoid big-bang rewrite risk

2026-04-09 | Blueprint migration complete: all routes now in nova/blueprints/*.py | __init__.py now contains only app factory, hooks, and helpers

## Background Processing

2025-10-24 | Weather cache worker (2h daemon thread) | Open-Meteo API is rate-limited and slow; pre-fetching avoids blocking user requests

2025-11-25 | Heatmap worker (4h daemon thread) with file locking | Altitude calculations are CPU-heavy for many objects/locations; background refresh keeps the UI fast; file locks prevent duplicate work across gunicorn workers

2025-11-25 | Thread locks added for multi-worker gunicorn deployment | SQLite + multiple threads + in-memory caches = race conditions without coordination

## Caching

2025-09-03 | In-memory BoundedCache dicts instead of Redis | Single-process SQLite deployment doesn't need an external cache; BoundedCache caps memory to prevent unbounded growth on long-running instances

2025-10-19 | Cache warmers skip inactive locations | Avoids wasting CPU calculating observability data for locations the user has disabled

## Frontend & Design

2026-02-18 | CSS design system tokens (tokens.css) introduced | Hardcoded color values scattered across 15+ CSS files made dark-mode and consistent styling impossible to maintain

2026-02-19 | All CSS migrated to design token variables | Replaced ~200 hardcoded hex values with semantic tokens so theme changes propagate globally

2026-03-03 | Flask-Babel i18n infrastructure added | Astrophotography community is international; gettext-based approach lets translators work with .po files without touching Python

## Mobile

2025-11-15 | Separate mobile blueprint (/m/*) with dedicated templates | Mobile views serve a PWA with different navigation, layouts, and reduced functionality (no graph editor); responsive CSS alone wouldn't cover the UX gap

## AI

2026-03-19 | AI blueprint added (nova/ai/routes.py) via OpenAI-compatible API | Provider-agnostic design lets users swap MiniMax, OpenAI, or any compatible endpoint via config

## Data Integrity

2025-10-20 | `_fix_mode_switch_sessions()` guard on every request | Switching between single-user and multi-user mode leaves stale session IDs in cookies; this prevents 500 errors from dangling foreign keys

2026-02-22 | Automatic DB migration for journal log content columns | Log format changes between app versions; auto-migration patches the schema on startup so users don't hit column-not-found errors after an update