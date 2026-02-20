
# Nova DSO Tracker v5.0.0

This is a major release featuring a complete architecture refactor and a comprehensive design system overhaul. Because of the scale of these changes, **your feedback is especially valuable** — if you encounter any issues, please report them so they can be addressed quickly.

---

## Architecture Refactor

The codebase has been reorganized from a monolithic structure into a modular package:

- **`nova/models.py`** — All SQLAlchemy models (DbUser, AstroObject, Location, Project, JournalSession, Component, Rig, etc.) are now in a dedicated module.
- **`nova/config.py`** — Constants, paths, and shared cache dictionaries are centralized here.
- **`nova/helpers.py`** — Shared utilities including database access, YAML I/O, and settings loading.
- **`nova/workers/`** — Background daemon threads (weather, heatmap, updates) are now isolated in their own package.
- **`nova/blueprints/`** — Blueprint stubs are in place for future route organization.

The entry point `nova.py` is now just 14 lines — it imports the app from the `nova/` package. This refactor improves maintainability and makes future contributions easier.

---

## Design System & Dark Mode Overhaul

A new CSS design token system (`static/css/tokens.css`) provides consistent styling across the entire application:

- **Typography scale** — Standardized font sizes and line heights
- **Spacing scale** — Consistent margins and padding
- **Shadow tokens** — Unified elevation system
- **Color tokens** — Centralized color variables for both light and dark modes
- **Interactive states** — Button hover/active states, form validation styles

Dark mode has been completely overhauled with consistent theming across:
- Dashboard and object lists
- Charts and graphs (including month/year views)
- Configuration forms
- Modals and dialogs
- Journal and project pages

---

## Frontend Asset Modularization

JavaScript and CSS have been extracted from inline templates into dedicated files:

- `static/js/` — Modular JS files (dashboard.js, graph_view.js, config_form.js, modal-manager.js, etc.)
- `static/css/` — CSS files organized by feature (base.css, tokens.css, dashboard.css, etc.)

This improves browser caching, makes debugging easier, and keeps templates cleaner.

---

## Memory Management: Bounded Caches

A new `BoundedCache` class prevents unbounded memory growth during long uptimes:

- All internal caches (weather, moon separation, nightly curves, etc.) now have maximum sizes
- When a cache reaches its limit, the oldest 10% of entries are evicted
- This ensures the application remains stable during extended operation

---

## Bug Fixes

- Fixed modal scroll and focus behavior
- Fixed dark mode inconsistencies across multiple views
- Fixed shared tag and shared items UI issues
- Fixed heatmap data loading
- Fixed undefined alias tokens in graph_view.css

---

## For Developers

If you're running Nova from source:

- The import structure has changed — `nova.py` is now a thin entry point
- Workers use lazy imports to avoid circular dependencies
- The cache dictionaries are now `BoundedCache` instances (drop-in dict replacement)
- Blueprint decorators are applied, but route functions remain in `nova/__init__.py` for now (migration in progress)

---

## Upgrade Notes

- **No database migration required** — your existing `instance/app.db` will work as-is
- **No configuration changes required** — existing `.env` files remain compatible
- Docker users: pull the new image when available

---

As mentioned, this was a substantial refactor. If you notice anything that doesn't work as expected, please open an issue on GitHub or reach out directly. Your feedback helps ensure a stable experience for everyone.

Thanks for using Nova DSO Tracker!
