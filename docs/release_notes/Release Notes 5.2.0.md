# Nova DSO Tracker v5.2.0

This release introduces guide optics configuration with dither recommendations, custom mono filters, a journal object switcher for quick navigation, and optional analytics/error reporting for multi-user deployments.

---

## New Features

### Guide Optics & Dither Recommendations

You can now configure guiding equipment (OAG or guide scope + guide camera) on each rig. When guide optics are set up:

- **Dither recommendations** appear on rig cards and in the journal form, showing suggested dither pixel values based on your guide camera pixel scale
- **Rig info modal** accessible via a new ⓘ button in the journal form shows full equipment specs including computed FL, f-ratio, image scale, FOV, and dither guidance

### Structured Dither Fields

Dither settings in journal sessions are now structured data instead of free-form text:

- **Dither (px)** - Dither amount in pixels
- **Every N** - Dither every N frames
- **Notes** - Optional text notes

This enables better filtering and analysis of dither settings across sessions.

### Custom Mono Filters

For imagers with filter wheels or manual filter changes, you can now define custom mono filters in the journal:

- Add/remove custom filter definitions (name, max subs target)
- Custom filter exposure data is tracked per-session
- Integration time calculations include custom filter data
- Full YAML export/import support for backup

### Journal Object Switcher

A new quick-switch modal in the journal sidebar lets you navigate between observed DSO objects without returning to the dashboard:

- Searchable list of all objects with imaging data
- Sort by Recent or Most Hours
- Keyboard navigation (Arrow keys, Enter, Escape)
- Preserves location context when switching

### Bulk Fetch Details

The "Fetch Missing Details" button has moved from the config page header to the **Manage My Objects** tab. It now works with object selection:

- Select the objects you want to update
- Click "Fetch Details" to pull missing type, magnitude, size, surface brightness, and constellation from SIMBAD
- Only selected objects are processed

### Analytics & Error Reporting (Multi-User Mode)

Optional infrastructure for multi-user deployments:

- **GDPR-compliant analytics** - Track feature usage and login activity with no third-party services, no cookies, no PII. Enable via `ANALYTICS_ENABLED=True` and view at `/analytics` with token auth
- **Sentry integration** - Automatic error reporting when `SENTRY_DSN` is set and `SINGLE_USER_MODE=False`

---

## Bug Fixes

- **Location minimum enforcement** - Prevented users from deleting all locations or deactivating their only active location via config form, bulk import, and UI checkbox states
- **YAML portability** - Fixed surrogate key leakage by using natural keys (names) for rig and project associations in YAML exports, preventing broken links after restore
- **Integration time calculation** - Fixed missing light frames in calculated integration time (now includes `subs × exposure` for light frames)
- **Journal table layout** - Prevented column overlap on smaller viewports with minimum width, responsive column visibility, and adjusted column widths
- **YAML export completeness** - Fixed missing ASIAIR/PHD2 log content and custom filter data in journal YAML exports
- **Graph date context** - Fixed dropdown API to use user-selected graph date and database horizon mask instead of stale cached values
- **Location persistence** - Fixed location context preservation in journal object switcher navigation

---

## Upgrade Notes

- **Database migration** - Automatic on app startup (new columns for structured dither fields, custom filters, guide optics, and analytics tables)
- **New environment variables** (optional):
  - `ANALYTICS_ENABLED=True` - Enable anonymous usage tracking
  - `ANALYTICS_SECRET_TOKEN=your-token` - Token for viewing `/analytics` dashboard
  - `ANALYTICS_EXCLUDE_USERS=user1,user2` - Exclude specific users from tracking
  - `SENTRY_DSN=https://...` - Enable Sentry error reporting
- **YAML backward compatibility** - Existing backup files import safely with sensible defaults for new fields

---

Thanks for using Nova DSO Tracker! As always, feedback is welcome - please report any issues on GitHub.
