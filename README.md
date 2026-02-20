
# Nova DSO Tracker

A Flask-based web application designed specifically for astrophotographers, providing essential data for tracking deep-sky objects (DSOs), planning imaging projects, and logging sessions.

## Features

* **New in 5.0: Architecture Refactor:** Complete codebase reorganization into a modular package structure. The monolithic `nova.py` has been split into dedicated modules (`nova/models.py`, `nova/config.py`, `nova/helpers.py`) and organized blueprints for better maintainability.
* **New in 5.0: Design System:** A comprehensive CSS token system (`tokens.css`) provides consistent typography, spacing, shadows, and color variables across the entire application.
* **New in 5.0: Dark Mode Overhaul:** Fully refined dark theme support across all views including charts, graphs, forms, and modals. Consistent color management throughout the UI.
* **New in 5.0: Improved Caching:** Bounded cache implementation prevents memory growth during long uptimes. Weather cache and data caches now have sensible limits.
* **New in 5.0: Extracted Frontend Assets:** JavaScript and CSS have been modularized into separate files (`static/js/`, `static/css/`) for better organization and caching.
* **Log File Import (Beta):** A tool to upload and analyze ASIAIR and PHD2 log files, providing insights into session efficiency, guiding performance, and environmental conditions.
* **NSNS V2 Support:** The Framing Assistant includes the Northern Sky Narrowband Survey (V2).
* **Night Explorer (Inspiration Tab):** A visual gallery displaying targets currently visible from your location, prioritizing objects high in the sky.
* **Advanced Object Management:** Bulk actions, filtering by catalog source, and enable/disable toggles without deleting objects.
* **Duplicate Management:** Scan for objects with similar coordinates and merge them into a single entry.
* **Custom Imagery & Inspiration:** Link your own astrophotos to objects for display in the Inspiration tab.
* **Mosaic Planning & Export:** Plan multi-pane mosaics in the Framing Assistant and export as CSV for ASIAIR or N.I.N.A.
* **Yearly Heatmap:** "Waterfall Heatmap" visualization for long-term target visibility over 12 months.
* **Project Management:** Group imaging sessions into Projects, track total integration time, and monitor status.
* **Rig Snapshots:** Equipment specs recorded at session log time, preserving historical accuracy.
* **Real-time Tracking:** Altitude and azimuth tracking for DSOs updated every minute.
* **Visibility Forecasts:** "Outlook" calculations based on altitude, moon illumination, and angular separation.

## Technologies Used

* **Backend:** Python (Flask, SQLAlchemy, AstroPy, Ephem)
* **Database:** SQLite
* **Frontend:** HTML5, JavaScript, Aladin Lite
* **Integrations:** SIMBAD (Object data), Stellarium (Planetarium control)

---

# Nova DSO Tracker - User Guide

### Purpose

Nova helps track Deep Sky Objects (DSOs) positions throughout the night for astrophotography or visual observations. It updates positions every minute, highlights objects marked for attention, and provides graphical insights into visibility, moon illumination, and imaging windows.

### Main Interface (The Dashboard)

When opening Nova, you see a list of DSOs sorted by default by their current altitude.

* **Highlights:** Objects with active project notes are highlighted.
* **Altitude Color Coding:** Altitudes above your defined threshold appear in green.
* **Horizon Mask:** If a Horizon Mask is defined and an object is obstructed by terrain, the field turns yellow.
* **Observable Window:** The "Observable" column shows the minutes an object is visible between astronomical dusk and dawn.
* **Quick View:** Click the 'i' icon next to an object's name to quickly see its inspiration image and altitude graph without leaving the dashboard.

![Screenshot 2026-02-20 at 12.43.28.jpg](docs/Screenshot%202026-02-20%20at%2012.43.28.jpg)
![Screenshot 2026-02-20 at 12.43.54.jpg](docs/Screenshot%202026-02-20%20at%2012.43.54.jpg)

### Mobile Companion

Nova includes a streamlined, mobile-first interface for essential planning on the go. Access it by navigating to `/m/up_now` on your instance.

*   **Up Now:** A responsive list of currently visible objects.
*   **Outlook:** Check upcoming imaging opportunities.
*   **Quick Add:** Add new objects to your database directly from your mobile device.

![Screenshot 2026-01-12 at 22.29.30.png](docs/Screenshot%202026-01-12%20at%2022.29.30.png)
### Sorting, Filtering, and Saved Views

  * **Sorting:** Click column headers to sort. A second click reverses the order.
  * **Filtering:** Each column has a search box. You can use operators like `>` (greater than), `<` (less than), or `!` (exclude).
      * *Example:* Type `>45` in the Altitude column to see only objects currently high in the sky.
      * *Example:* Type `Galaxy` in the Type column.
  * **Saved Views:** You can save a specific combination of filters and sort orders.
    1.  Set up your filters (e.g., "Nebulae", "Altitude \> 30").
    2.  Click the **"Views"** dropdown.
    3.  Save the view (e.g., "Good Nebulae").
    4.  **Sharing:** You can mark a view as "Shared" to let other users on your server use it.

### Visual Discovery: The Inspiration Tab (Night Explorer)

The Inspiration Tab offers a visual way to browse potential targets. Instead of a data table, this view presents tiles for objects that are currently observable.

* **Smart Sorting:** Objects are prioritized based on their current altitude and visibility duration.
* **Imagery:** Tiles display survey images (DSS2) by default. If you have uploaded your own astrophoto for an object, it will be displayed here.
* **Quick Info:** Each tile displays the object's type, current altitude, and constellation. Clicking a tile opens a detail modal with a summary and a link to the full charts.

![Screenshot 2026-02-20 at 12.44.43.jpg](docs/Screenshot%202026-02-20%20at%2012.44.43.jpg)
![Screenshot 2026-02-20 at 12.45.06.jpg](docs/Screenshot%202026-02-20%20at%2012.45.06.jpg)

### Long-Term Planning: The Yearly Heatmap

The Yearly Heatmap visualizes target visibility over the next 12 months.

  * **Waterfall Visualization:** This chart visualizes target visibility over the next 12 months. Darker green indicates higher quality imaging time, while vertical white bands highlight full moon periods where imaging may be difficult.
  * **Data Loading:** To ensure performance, data is loaded in chunks and stored for 24hrs.
  * **Integrated Filtering:** You can apply your "Saved Views" directly to this heatmap to narrow down targets (e.g., only show "Galaxies").
  * **Active Only:** A checkbox allows you to quickly filter the view to show only your currently active projects.

![Screenshot 2026-02-20 at 12.45.38.jpg](docs/Screenshot%202026-02-20%20at%2012.45.38.jpg)

### Projects & Imaging Journal

The Journal has been completely overhauled. It is no longer just a flat list of entries; it is a **Project Management System**.

![Screenshot 2026-02-20 at 12.46.25.jpg](docs/Screenshot%202026-02-20%20at%2012.46.25.jpg)

1.  **Projects:** A Project groups multiple imaging sessions toward a single goal (e.g., "Mosaic of M31" or "HaOIII data for Helix").

      * **Dedicated Project Pages:** Each project has its own detail page where you can manage status, view aggregated stats, and keep detailed notes for goals, framing, and processing using rich text editors.
      * **Status:** Track if a project is `In Progress`, `Completed`, or `Abandoned`.
      * **Integration:** Nova automatically sums the exposure time from all linked sessions to show total integration time.
      * **Notes:** Keep project-level notes (framing plans, processing goals) separate from nightly session notes.
      
![Screenshot 2026-02-20 at 12.46.43.jpg](docs/Screenshot%202026-02-20%20at%2012.46.43.jpg)

2.  **Planning Mode ("New Project"):**
    You no longer need to wait until you have data to create an entry. Use the **"New Project"** button to plan targets ahead of time. This creates a container for your future data.

![Screenshot 2026-02-20 at 12.47.15.jpg](docs/Screenshot%202026-02-20%20at%2012.47.15.jpg)

3.  **Rig Snapshots:**
    When you add a session, Nova records a **Snapshot** of your rig's metrics (Focal Length, F-Ratio, Camera).

      * *Why?* If you change your telescope configuration in 6 months, the history of your old sessions remains accurate. It will not be overwritten by your new settings.

4.  **Reports:**
    You can generate PDF reports for a specific night (Session Report) or a summary of an entire target (Project Report).

5.  **Log File Import (Beta):**
    Upload and analyze log files from ASIAIR and PHD2. This tool extracts key session data, such as guiding performance, exposure stats, and environmental conditions, providing a detailed report directly within your journal.
    
![Screenshot 2026-02-20 at 12.48.46.jpg](docs/Screenshot%202026-02-20%20at%2012.48.46.jpg)

### Detailed Object Information

Clicking a DSO in the main list opens the Detailed View.

  * **Altitude Graphs:** Shows the object's path for the current night.
  * **Moon Separation:** Displays angular separation from the moon on the main graph.
  * **Imaging Opportunities:** Click "Find Imaging Opportunities" to calculate the best dates for imaging this object based on your specific horizon and moon constraints.

![Screenshot 2026-02-20 at 12.50.18.jpg](docs/Screenshot%202026-02-20%20at%2012.50.18.jpg)
![Screenshot 2026-02-20 at 12.50.36.jpg](docs/Screenshot%202026-02-20%20at%2012.50.36.jpg)
**Framing Assistant:**
The "Show Framing" button opens the Aladin-based framing tool.

  * **Local Data:** It uses the *exact* RA/Dec from your database (not a generic catalog) to ensure the framing matches your mount's coordinates.
  * **Overlays:** You can overlay **other objects** from your database onto the image. This is incredibly useful for planning mosaics or checking if a nearby bright star is in your Field of View.
  * **Surveys:** Includes various sky surveys, including the Northern Sky Narrowband Survey (NSNS) V2.

![Screenshot 2026-02-20 at 12.52.22.jpg](docs/Screenshot%202026-02-20%20at%2012.52.22.jpg)

**Mosaic Planning:**
Plan multi-panel mosaics directly within Nova.

1.  **Grid Configuration:** Define the number of columns and rows (e.g., 2x2).
2.  **Overlap:** Set the percentage of overlap between panels (default 10%).
3.  **Rotation:** Adjust the camera rotation angle.

**Exporting Plans:**
Once your framing or mosaic is set, use the **"Copy Plan (CSV)"** button. This generates a CSV format compatible with:

  * **ASIAIR:** Import via Plan -\> Import.
  * **N.I.N.A.:** Import into the Sequencer.

![Screenshot 2026-02-20 at 12.56.52.jpg](docs/Screenshot%202026-02-20%20at%2012.56.52.jpg)

### Configuration and Object Management

The Configuration page manages your library of objects, locations, and equipment.

  * **Locations:** Manage observing sites. You can define **Horizon Masks** here (uploading a CSV/YAML list of Azimuth/Altitude points) to block out trees or buildings.
  * **Objects:**
      * **Smart Add:** When adding an object (e.g., "M 42"), Nova first checks your local database for duplicates (e.g., "M42"). If not found, it queries SIMBAD.
      * **Import Catalog:** Download curated lists (Messier, Caldwell, etc.) directly from the Nova server.
  * **Rigs:** Define your optical trains. Accurate data here is required for the **Framing Assistant** and **Rig Snapshots**.

![Screenshot 2026-02-20 at 12.57.43.jpg](docs/Screenshot%202026-02-20%20at%2012.57.43.jpg)

![Screenshot 2026-01-12 at 22.43.45.jpg](docs/Screenshot%202026-01-12%20at%2022.43.45.jpg)

**Manage Objects:**
Advanced management tools include:

* **Filtering:** Use the filter bar to find objects by ID, Name, Type, or **Source** (e.g., filter by 'Messier' to see only objects imported from that catalog).
* **Bulk Actions:** Select multiple objects using the checkboxes. You can then **Enable** or **Disable** them. Disabled objects remain in your database but are excluded from calculations and the main dashboard list.
* **Inspiration Content:** In the edit view of an object, upload a custom image URL, credit, and description text.

![Screenshot 2026-02-20 at 12.58.43.jpg](docs/Screenshot%202026-02-20%20at%2012.58.43.jpg)

**Duplicate Manager:**
Use the "Find Duplicates" button to scan your library. Nova identifies objects with coordinates within 2.5 arcminutes of each other (e.g., `M101` and `NGC5457`). You can choose which entry to keep; Nova will automatically migrate all journal entries and projects to the kept object before deleting the duplicate.

![Screenshot 2026-02-20 at 12.58.57.jpg](docs/Screenshot%202026-02-20%20at%2012.58.57.jpg)

---
# Nova Astronomical Tracker Setup Guide

The easiest way to install Nova DSO Tracker is via Docker.

[![Watch the video](https://img.youtube.com/vi/CF__VZEtH_I/0.jpg)](https://youtu.be/CF__VZEtH_I)



### Docker Image:

A pre-built Docker image is available on Docker Hub for easy setup:

[![Docker Pulls](https://img.shields.io/docker/pulls/mrantonsg/nova-dso-tracker.svg)](https://hub.docker.com/r/mrantonsg/nova-dso-tracker)

**[View on Docker Hub: mrantonsg/nova-dso-tracker](https://hub.docker.com/r/mrantonsg/nova-dso-tracker)**

See the Docker Hub page for instructions on how to run the container.



### Manual Docker Installation

1. **Pull the image:**
```bash
docker pull mrantonsg/nova-dso-tracker

```


2. **Run the container:**
You must mount a volume to `/app/instance` to persist your database.
```bash
docker run -d \
  -p 5000:5000 \
  -v nova_data:/app/instance \
  --name nova \
  mrantonsg/nova-dso-tracker

```



### Manual Python Installation

1. **Clone the repository:**
```bash
git clone https://github.com/mrantonSG/nova_DSO_tracker.git
cd nova_DSO_tracker

```


2. **Create a Virtual Environment:**
```bash
python3 -m venv nova
source nova/bin/activate

```


3. **Install Dependencies:**
```bash
pip install -r requirements.txt

```


4. **Run the Application:**
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