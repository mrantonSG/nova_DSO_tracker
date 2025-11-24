
# Nova DSO Tracker

A Flask-based web application designed specifically for astrophotographers, providing essential data for tracking deep-sky objects (DSOs), planning imaging projects, and logging sessions.

## Features

  * **New in 4.2.0: Core Database Architecture:** A complete migration from flat YAML files to a robust **SQLite database backend**. This ensures faster performance, data safety, and enables complex features like project grouping.
  * **New in 4.1.0: Project Management:** Move beyond simple session logging. Group multiple imaging sessions into dedicated **Projects** (e.g., "Mosaic of M31"). Track total integration time, set goals, and monitor status (In Progress, Completed, Abandoned).
  * **New in 4.1.0: Rig Snapshots:** When you log a session, Nova now takes a "snapshot" of your equipment specs (Focal Length, F-Ratio, Image Scale) at that exact moment. If you change your telescope later, your historical session data remains accurate.
  * **New in 4.1.0: Saved Views:** Save your complex filter and sorting configurations (e.g., "Galaxies \> 45° Altitude") as a "View" on the dashboard. These views can be shared with other users on the server.
  * **New in 4.0.0: Smart Object Library:** Automatic duplicate detection. `M42`, `M 42`, and `m-42` are recognized as the same object, preventing database clutter.
  * **New in 4.0.0: Enhanced Framing:** The Aladin-based framing assistant now uses your local, correct RA/Dec, includes new surveys (NSNS), and can overlay all objects from your database to find nearby targets.
  * **PDF Reporting:** Generate professional PDF reports for individual sessions or complete project summaries.
  * **Real-time Tracking:** Altitude and azimuth tracking for DSOs updated every minute.
  * **Visibility Forecasts:** "Outlook" calculations based on altitude, moon illumination, and angular separation.
  * **Integration:** Connects with Stellarium for live sky visualization and telescope slewing.

## Technologies Used

  * **Backend:** Python (Flask, SQLAlchemy, AstroPy, Ephem)
  * **Database:** SQLite (v4.2+)
  * **Frontend:** HTML5, JavaScript, Aladin Lite
  * **Integrations:** SIMBAD (Object data), Stellarium (Planetarium control)

-----

## Upgrading to Version 4.2.0 (Data Migration)

Version 4.2.0 introduces a significant architectural shift: **YAML files are no longer the primary storage.** All data is now stored in a SQLite database (`instance/app.db`).

### Automatic Migration (Single-User)

If you are running in **Single-User Mode** (default), the migration is automatic.

1.  **Backup:** Download your current `config`, `rigs`, and `journal` YAML files as a precaution.
2.  **Update:** Pull the new Docker image or update the code.
3.  **Run:** Upon first startup, import the saved yaml files. 

### Multi-User Migration

If you are running a multi-user instance, the admin must trigger the migration manually to ensure data integrity across user accounts.

```bash
# Run this command inside the container or environment
flask migrate-yaml-to-db
```

-----

# Nova DSO Tracker - User Guide

### Purpose

Nova helps track Deep Sky Objects (DSOs) positions throughout the night for astrophotography or visual observations. It updates positions every minute, highlights objects marked for attention, and provides graphical insights into visibility, moon illumination, and imaging windows.

### Main Interface (The Dashboard)

When opening Nova, you see a list of DSOs sorted by default by their current altitude (descending order).

  * **Highlights:** Objects with active project notes are highlighted.
  * **Altitude Color Coding:** Altitudes above your defined threshold (green) or below (white).
  * **Horizon Mask:** If a Horizon Mask is defined and an object is obstructed by terrain, the field turns yellow.
  * **Observable Window:** The "Observable" column shows the minutes an object is visible between astronomical dusk and dawn, accounting for your altitude threshold.

![Screenshot _42_index.png](docs/Screenshot%20_42_index.png)

### Sorting, Filtering, and Saved Views

  * **Sorting:** Click column headers to sort. A second click reverses the order.
  * **Filtering:** Each column has a search box. You can use operators like `>` (greater than), `<` (less than), or `!` (exclude).
      * *Example:* Type `>45` in the Altitude column to see only objects currently high in the sky.
      * *Example:* Type `Galaxy` in the Type column.
  * **Saved Views (New in v4.1):** You can now save a specific combination of filters and sort orders.
    1.  Set up your filters (e.g., "Nebulae", "Altitude \> 30").
    2.  Click the **"Views"** dropdown.
    3.  Save the view (e.g., "Good Nebulae").
    4.  **Sharing:** You can mark a view as "Shared" to let other users on your server use it.

### Projects & Imaging Journal (New in v4.2)

The Journal has been completely overhauled. It is no longer just a flat list of entries; it is a **Project Management System**.

![Screenshot_42_journal.png](docs/Screenshot_42_journal.png)

1.  **Projects:** A Project groups multiple imaging sessions toward a single goal (e.g., "Mosaic of M31" or "HaOIII data for Helix").

      * **Status:** Track if a project is `In Progress`, `Completed`, or `Abandoned`.
      * **Integration:** Nova automatically sums the exposure time from all linked sessions to show total integration time.
      * **Notes:** Keep project-level notes (framing plans, processing goals) separate from nightly session notes.

2.  **Planning Mode ("New Project"):**
    You no longer need to wait until you have data to create an entry. Use the **"New Project"** button to plan targets ahead of time. This creates a container for your future data.

![Screenshot_42_project.png](docs/Screenshot_42_project.png)

3.  **Rig Snapshots:**
    When you add a session, Nova records a **Snapshot** of your rig's metrics (Focal Length, F-Ratio, Camera).

      * *Why?* If you change your telescope configuration in 6 months, the history of your old sessions remains accurate. It will not be overwritten by your new settings.

4.  **Reports:**
    You can generate PDF reports for a specific night (Session Report) or a summary of an entire target (Project Report).

### Detailed Object Information

Clicking a DSO in the main list opens the Detailed View.

  * **Altitude Graphs:** Shows the object's path for the current night.
  * **Moon Separation:** Displays angular separation from the moon.
  * **Imaging Opportunities:** Click "Find Imaging Opportunities" to calculate the best dates for imaging this object based on your specific horizon and moon constraints.

![Screenshot_42_graph.png](docs/Screenshot_42_graph.png)
**Framing Assistant:**
The "Show Framing" button opens the Aladin-based framing tool.

  * **Local Data:** It uses the *exact* RA/Dec from your database (not a generic catalog) to ensure the framing matches your mount's coordinates.
  * **Overlays:** You can overlay **other objects** from your database onto the image. This is incredibly useful for planning mosaics or checking if a nearby bright star is in your Field of View.

![Screenshot _42_framing.png](docs/Screenshot%20_42_framing.png)
### Configuration

The Configuration page is the control center for your data.

  * **Locations:** Manage observing sites. You can define **Horizon Masks** here (uploading a CSV/YAML list of Azimuth/Altitude points) to block out trees or buildings.
  * **Objects:**
      * **Smart Add:** When adding an object (e.g., "M 42"), Nova first checks your local database for duplicates (e.g., "M42"). If not found, it queries SIMBAD.
      * **Import Catalog:** Download curated lists (Messier, Caldwell, etc.) directly from the Nova server.
  * **Rigs:** Define your optical trains. Accurate data here is required for the **Framing Assistant** and **Rig Snapshots**.

![Screenshot_42_rigs.png](docs/Screenshot_42_rigs.png)

![Screenshot_42_mu_sharing.png](docs/Screenshot_42_mu_sharing.png)
-----

# Nova Astronomical Tracker Setup Guide

The easiest way to install Nova DSO Tracker is via Docker.

[![Watch the video](https://img.youtube.com/vi/CF__VZEtH_I/0.jpg)](https://youtu.be/CF__VZEtH_I)



### Docker Image:

A pre-built Docker image is available on Docker Hub for easy setup:

[![Docker Pulls](https://img.shields.io/docker/pulls/mrantonsg/nova-dso-tracker.svg)](https://hub.docker.com/r/mrantonsg/nova-dso-tracker)

**[View on Docker Hub: mrantonsg/nova-dso-tracker](https://hub.docker.com/r/mrantonsg/nova-dso-tracker)**

See the Docker Hub page for instructions on how to run the container.


### Docker Installation

1.  **Pull the image:**

    ```bash
    docker pull mrantonsg/nova-dso-tracker
    ```

2.  **Run the container:**
    **Important:** You must mount a volume to `/app/instance` to persist your database (`app.db`).

    ```bash
    docker run -d \
      -p 5000:5000 \
      -v nova_data:/app/instance \
      --name nova \
      mrantonsg/nova-dso-tracker
    ```

### Manual Python Installation

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/mrantonSG/nova_DSO_tracker.git
    cd nova_DSO_tracker
    ```

2.  **Create a Virtual Environment:**

    ```bash
    python3 -m venv nova
    source nova/bin/activate
    ```

3.  **Install Dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Run the Application:**

    ```bash
    python nova.py
    ```

    Access the application at `http://localhost:5001`.

### User Modes

Nova supports two modes, configured in `instance/.env`:

  * **Single-user mode (Default):** No login required. The app assumes one user ("default") and bypasses authentication.
  * **Multi-user mode:** Ideal for hosting on a public server. Requires user registration and login.
      * To enable, set `SINGLE_USER_MODE=False` in your `.env` file.

### License

Nova DSO Tracker is licensed under the Apache 2.0 License **with the Commons Clause**.
Free for personal, educational, and non-commercial use only. Commercial use requires explicit permission.