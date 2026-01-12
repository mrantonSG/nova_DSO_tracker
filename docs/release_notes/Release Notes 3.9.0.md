
## Nova DSO Tracker v3.9.0: The Critical Fix & Sharing Update

This is an important and accelerated release to address a critical bug that affected new users. I have also included several major new features, enhancements, and other bug fixes that were in development.

The primary issue resolved is a bug that prevented newly registered users from receiving the default set of locations and objects. This created a poor first-time experience, and I sincerely apologize for the issue. This update fully resolves it for new and existing users.

### Critical Bug Fix: New User Data Provisioning

The most important fix in this release is for user data:

* **New Users:** All newly registered users will now correctly receive the full set of default astronomical objects and locations upon their first login.
* **Existing Users:** A repair script has been run to **safely and non-destructively** add all missing default data to any existing accounts that were affected by this bug (e.g., users who had locations but no objects). Any data you added manually has been preserved.

---

### New Features

* **Sharing System:** You can now share your **Astro-Objects** and **Components** (telescopes, cameras, etc.) with other users on the same server.
    * In the config screen, you can mark an item as "shared."
    * Other users can now see a new "Import Shared Items" tab to browse and import your shared gear and targets into their own accounts.
* **NSNS Survey Support:** The Aladin modal now includes a direct link to the **NSNS (Nearby Supernova Factory Survey)**, providing another powerful resource for your targets.

---

### Enhancements & Bug Fixes

* **Locations:** Added a **Timezone dropdown field** when creating or editing locations. This prevents errors from manual entry and ensures correct dates for observations.
* **Aladin Modal:** The Aladin window now **correctly uses your local RA/DEC data** from your database to frame your target, rather than querying Simbad for it.
* **Add Object (Fix):** The **Constellation field** is no longer read-only and can be edited when adding a new object.
* **Add Object (UI):** If an object search via Simbad fails, a **"Cancel" button** now appears so you can easily exit the process.
* **Config Screen (UI):** Fixed a visual glitch where all config tabs would briefly stack on top of each other before organizing. The view now loads cleanly.
* **Database Refactor:** The YAML import/export functions have been fully refactored to be compatible with the new, more robust database structure.