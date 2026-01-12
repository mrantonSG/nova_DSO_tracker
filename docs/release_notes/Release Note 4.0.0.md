
# Nova DSO Tracker 4.0.0

This is a massive update, the result of a long journey to refactor the app's core. Version 4.0.0 transitions Nova to a fully robust data model, introduces powerful new sharing and library features, and fixes a host of long-standing bugs related to data integrity and user experience.

## CRITICAL: Migration Guide for v4.0.0

This version cleans all object data. You **MUST** follow the correct path for your installation to avoid data loss.

### For Single-Users (Docker or Local Python)

Your upgrade process is an "Export / Import". The new version will automatically clean your data when you import it.

1.  **Before you upgrade:** Open your OLD version. Go to "Config" and **Download** your three files: `config.yaml`, `rigs.yaml`, and `journal.yaml`.
2.  **Install v4.0.0:** Stop your old app. Install the new version (e.g., `git pull`, `docker compose build`). **Delete your old `instance` folder** to start fresh.
3.  **Run v4.0.0:** Start the new app.
4.  **Import your data:** Go to "Config". Import your files **in this order:**
    1.  First: `config.yaml`
    2.  Second: `rigs.yaml`
    3.  Third: `journal.yaml`

The new "smart" import logic will de-duplicate your objects, merge your notes, and correctly re-link all your journal entries. Your data is now migrated.

### For Multi-User (MU) Admins

You must run a **one-time admin command** to clean your live database. This command will merge duplicates, merge all notes, and re-link all journal entries for all users.

**This requires a brief maintenance window (app must be stopped).**

1.  **Update Code:** Pull the new code on your server (`git pull`).
2.  **Build New Image:** Build the new Docker image (`docker compose build nova`).
3.  **STOP THE APP:** Take the app offline (`docker compose stop nova`).
4.  **RUN THE SCRIPT:** From your terminal, run the one-time cleanup command:
    ```bash
    docker compose run --rm nova flask --app nova clean-object-ids
    ```
5.  **Verify:** Watch the log. It will show you all the "Merging" and "Renaming" operations. Wait for it to complete.
6.  **RESTART:** Bring the app back online (`docker compose up -d nova`).

Your system is now fully migrated. All users will see a clean, de-duplicated object list, and all journal links will be preserved.

-----

## Major New Features

  * **Smart Object Library & De-duplication:** The core of this update. The app now understands that "M 42", "M-42", and "M42" are the same object. This prevents duplicates when adding or importing objects.
  * **Object Catalog Importer:** A new "Import from Catalog" tab on the Objects page lets you add curated lists (like the Messier catalog) directly from a central repository.
  * **Sharing (Multi-User):** You can now share your objects and rig components with other users on the same server. Imported items are clearly marked.
  * **Smarter "Add Object" Workflow:** The "Add New Object" tool now checks your local database *first*. It will find your existing "M42" and load it for editing, rather than immediately searching SIMBAD.
  * **Enhanced Framing:** The Aladin-based framing assistant now uses your local, correct RA/Dec, includes new surveys (NSNS), and can overlay all objects from your database to find nearby targets.

## Fixes & UI Improvements

  * **UI: Fixed "Blink" on Journal:** Fixed the bug where the wrong chart would flash for a second when switching between journal sessions.
  * **UI: Import/Export Messages:** Importing and exporting YAML files now correctly shows a "Success" or "Importing, please wait..." message.
  * **UI: Object Page Refactor:** The "Objects" tab is now organized into sub-tabs (Manage, Add, Import) with better filters.
  * **Data: Robust Note Merging:** When importing a `config.yaml`, the app now intelligently merges project notes from duplicates instead of overwriting them or adding "none".
  * **Fix: Weather:** The 7timer weather service logic has been improved.
  * **Fix: Locations:** The "Locations" page now uses a dropdown for timezones to prevent invalid data.

-----

This was a huge effort to improve the foundation of the app. Thank you, and clear skies\!