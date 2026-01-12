
# Nova DSO Tracker v4.1.1
**Release Date:** November 22, 2025

### Highlights
This release includes some critical bug fixes for version 4.1.0:

* **Background worker:** bug fix that could cause background worker to crash
* **Concurrency:** potential DB problem prevented with this fix
* **Simba imports:** RA and DEC could be wrongly interpreted when importing through simba


## Nova DSO Tracker v4.1.0
**Release Date:** November 21, 2025

This release represents a major overhaul of the **Journal** system. Nova now supports full **Project Management**, allowing you to group multiple sessions into a single project (e.g., "Mosaic of M31" or "HaOIII data for Helix"). We have also added professional **PDF Report generation** for both individual sessions and complete projects.

Additionally, the main dashboard now supports **Saved Views**, allowing you to save your preferred sorting and tab configurations.

---

### New Features

#### Projects & Advanced Journaling
* **Project Support:** You can now group multiple sessions into a dedicated **Project**. Track total integration time, goals, and status (In Progress, Completed, Abandoned) across multiple nights.
* **PDF Reports:** added the ability to generate and download beautiful PDF reports.
    * **Session Report:** A summary of a single night's imaging, including weather, gear, and notes.
    * **Project Report:** A comprehensive overview of a target, aggregating data from all linked sessions.
* **Rig Snapshots:** When you add a session, Nova now takes a "snapshot" of your rig's metrics at that moment. It automatically calculates and saves:
    * Effective Focal Length (e.g., 386 mm)
    * F-Ratio (e.g., f/4.8)
    * Image Scale (e.g., 1.55 arcsec/pixel)
    * Field of View (FOV) dimensions
* **New Journal Layout:** A redesigned Journal UI that groups sessions by Project and provides a clearer overview of your imaging history.

####  Dashboard Enhancements
* **Saved Views:** You can now save your preferred filter settings and sort orders on the Index page (e.g., "Sort by Altitude", "Sort by Moon Separation"). Quickly switch between different viewing contexts.

###  Improvements & Fixes
* **Guest User Experience:** Improved navigation and interface elements for guest users (read-only mode).
* **Outlook Cache Fix:** Resolved an issue where the deep-sky outlook cache worker was not receiving the correct arguments, preventing background updates.
* **Database Integrity:** Enhanced the startup routines to automatically patch the database schema and migrate legacy session data to the new Project structure without data loss.
* **Import/Export:** Updated YAML export/import tools to include full Project metadata (goals, notes, status) to ensure full data portability.
* **General Bug Fixes:** Various stability improvements and UI tweaks.

---

###  Upgrade Notes for Self-Hosters
This version introduces a new `projects` table and adds several columns to the `journal_sessions` table.
* **No manual action is required.** Upon restarting the container/application, Nova will automatically detect the schema changes and migrate your existing data safely.
* Legacy "active objects" will be automatically converted into Projects.