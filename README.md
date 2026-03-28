# Nova DSO Tracker

A Flask-based web application for astrophotographers — track deep-sky objects, plan imaging sessions, and log your results.

---

## Features

* **Ask Nova AI:** AI-powered target ranking that analyzes your object list against current sky conditions, moon phase, and equipment to recommend the best targets for tonight. Supports Anthropic, OpenAI, Ollama, and any OpenAI-compatible provider. Entirely optional — works without AI.
* **Log File Analysis:** Import and analyze session logs from ASIAIR, PHD2, and N.I.N.A. Reports include guiding performance, autofocus V-curves, exposure stats, and session swimlane timelines.
* **Multi-Language Support:** Available in English, German, French, Spanish, Japanese, and Chinese Simplified. Language selector in the header with persistent preference per user.
* **Mobile Companion:** Full planning on the go — object detail, altitude charts, filtering, outlook, journal entry creation, and framing assistant from your phone.
* **User Management (Multi-User Mode):** Web-based admin panel to create, activate/deactivate, reset passwords, and delete users — no command line required.
* **Guide Optics & Dither Recommendations:** Configure guiding equipment per rig and get dither pixel recommendations based on your guide camera pixel scale.
* **Custom Mono Filters:** Define custom mono filters in the journal with per-session tracking and full YAML export/import support.
* **Night Explorer (Inspiration Tab):** Visual gallery of targets currently observable from your location, sorted by altitude and visibility duration.
* **Yearly Heatmap:** Waterfall visualization of target visibility over 12 months with moon period indicators.
* **Project Management & Journal:** Group imaging sessions into Projects, track integration time, generate PDF reports with embedded log charts.
* **Mosaic Planning & Export:** Plan multi-pane mosaics in the Framing Assistant and export as CSV for ASIAIR or N.I.N.A.
* **Real-time Tracking:** Altitude and azimuth tracking for DSOs updated every minute.
* **Theme Preference:** Choose Light, Dark, or Follow System. Your preference persists across sessions.
* **Duplicate Management:** Scan for objects with similar coordinates and merge them into a single entry.

## Technologies Used

* **Backend:** Python (Flask, SQLAlchemy, AstroPy, Ephem)
* **Database:** SQLite (with Alembic migrations)
* **Frontend:** HTML5, JavaScript, Aladin Lite
* **Integrations:** SIMBAD (Object data), Stellarium (Planetarium control)
* **AI (optional):** Anthropic, OpenAI, or Ollama for Ask Nova features

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

![Dashboard overview](docs/Screenshot%202026-02-27%20at%2014.07.27.jpg)
![Dashboard detail](docs/Screenshot%202026-02-27%20at%2014.07.46.jpg)

### Ask Nova AI

Ask Nova is an optional AI assistant that ranks your visible objects to recommend the best targets for tonight. It considers altitude curves, transit times, moon phase and separation, and your equipment specs.

* **Ask Nova button** on the dashboard toolbar triggers an AI ranking. Objects are re-sorted with a dedicated Nova Rank column.
* **Restore Nova** reloads a cached ranking instantly (cached per location and day). **Re-ask** forces a fresh AI query.
* **Inspiration Tab** also respects Nova rankings — when active, tiles are ordered by AI recommendation.
* **Journal AI features:** Generate observation notes for any target, or stream a full session summary (weather, conditions, equipment, targets) directly from the journal form.

Ask Nova is disabled by default. To enable it, see [AI Configuration](#ai-configuration-optional).

![Screenshot 2026-03-28 at 13.57.23.jpg](docs/Screenshot%202026-03-28%20at%2013.57.23.jpg)
![Screenshot 2026-03-28 at 13.58.23.jpg](docs/Screenshot%202026-03-28%20at%2013.58.23.jpg)

### Multi-Language Support

Nova is available in 6 languages. Use the language selector in the header to switch at any time — your preference is saved per user account.

| Language | Status                                       |
|---|----------------------------------------------|
| English | Original                                     |
| German | Full translation, community review welcome   |
| French | Machine-translated, community review welcome |
| Spanish | Machine-translated, community review welcome |
| Japanese | Machine-translated, community review welcome |
| Chinese Simplified | Machine-translated, community review welcome |

![Screenshot 2026-03-08 at 10.26.56.jpg](docs/Screenshot%202026-03-08%20at%2010.26.56.jpg)

### Mobile Companion

Nova includes a mobile-first interface for essential planning on the go. Access it at `/m/up_now` on your instance.

* **Up Now:** Responsive list of currently visible objects with slider-based altitude/size/magnitude filters, sort dropdown, and tap-to-navigate opportunity cards.
* **Outlook:** Upcoming imaging opportunities grouped by date with click-to-navigate to the altitude chart.
* **Object Detail:** Full target info, altitude charts, moon separation, and framing assistant.
* **Journal:** Create new session entries from your phone with rig and filter selection.
* **Quick Add:** Add new objects to your database directly from your mobile device.


### Sorting, Filtering, and Saved Views

* **Sorting:** Click column headers to sort. A second click reverses the order.
* **Filtering:** Each column has a search box. You can use operators like `>` (greater than), `<` (less than), or `!` (exclude).
    * *Example:* Type `>45` in the Altitude column to see only objects currently high in the sky.
    * *Example:* Type `Galaxy` in the Type column.
* **Saved Views:** Save a specific combination of filters and sort orders.
    1. Set up your filters (e.g., "Nebulae", "Altitude > 30").
    2. Click the **"Views"** dropdown.
    3. Save the view (e.g., "Good Nebulae").
    4. **Sharing:** You can mark a view as "Shared" to let other users on your server use it.

### Visual Discovery: The Inspiration Tab (Night Explorer)

The Inspiration Tab offers a visual way to browse potential targets. Instead of a data table, this view presents tiles for objects that are currently observable.

* **Smart Sorting:** Objects are prioritized based on their current altitude and visibility duration (or by AI recommendation when Ask Nova is active).
* **Imagery:** Tiles display survey images (DSS2) by default. If you have uploaded your own astrophoto for an object, it will be displayed here.
* **Quick Info:** Each tile displays the object's type, current altitude, and constellation. Clicking a tile opens a detail modal with a summary and a link to the full charts.

![Inspiration tab](docs/Screenshot%202026-02-27%20at%2014.08.21.jpg)
![Inspiration detail](docs/Screenshot%202026-02-27%20at%2014.08.35.jpg)

### Long-Term Planning: The Yearly Heatmap

The Yearly Heatmap visualizes target visibility over the next 12 months.

* **Waterfall Visualization:** Darker green indicates higher quality imaging time. Vertical white bands highlight full moon periods.
* **Data Loading:** Data is loaded in chunks and stored for 24 hours for performance.
* **Integrated Filtering:** Apply your Saved Views directly to the heatmap to narrow down targets.
* **Active Only:** A checkbox filters the view to show only your currently active projects.

![Yearly heatmap](docs/Screenshot%202026-02-27%20at%2014.09.32.jpg)

### Projects & Imaging Journal

The Journal is a full **Project Management System**.

![Journal overview](docs/Screenshot%202026-02-27%20at%2014.10.01.jpg)

1. **Projects:** A Project groups multiple imaging sessions toward a single goal (e.g., "Mosaic of M31" or "HaOIII data for Helix").

    * **Dedicated Project Pages:** Each project has its own detail page with aggregated stats and rich text notes for goals, framing, and processing.
    * **Status:** Track if a project is `In Progress`, `Completed`, or `Abandoned`.
    * **Integration:** Nova automatically sums the exposure time from all linked sessions.

![Project detail](docs/Screenshot%202026-02-27%20at%2014.10.16.jpg)

2. **Planning Mode ("New Project"):** Create a project before you have data to plan targets ahead of time.

![New project](docs/Screenshot%202026-02-27%20at%2014.10.39.jpg)

3. **Rig Snapshots:** When you add a session, Nova records a snapshot of your rig's metrics. If you change your telescope configuration later, the history of old sessions remains accurate.

4. **Guide Optics & Dither Recommendations:** Configure guiding equipment (OAG or guide scope + guide camera) per rig. When set up, dither recommendations appear on rig cards and in the journal form, showing suggested dither pixel values based on your guide camera pixel scale. A rig info modal (⓪ button in the journal form) shows full computed specs including FL, f-ratio, image scale, FOV, and dither guidance.

![Screenshot 2026-03-08 at 10.28.26.jpg](docs/Screenshot%202026-03-08%20at%2010.28.26.jpg)

5. **Custom Mono Filters:** Define custom mono filters for filter wheels or manual changes. Track exposure data per session with full YAML export/import support.

6. **Object Switcher:** Navigate between observed DSO objects directly from the journal sidebar without returning to the dashboard.

7. **Reports:** Generate PDF reports for a specific night (Session Report) or a full target summary (Project Report).

8. **Log File Import & Analysis:** Upload and analyze log files from ASIAIR, PHD2, and N.I.N.A.

    * **ASIAIR Logs:** Capture counts, exposure times, filter usage, and dithering data.
    * **PHD2 Logs:** Guiding RMS, corrections, and drift analysis.
    * **N.I.N.A. Logs:** Full session swimlane (equipment startup, guiding, imaging, platesolving, flats), autofocus analysis with V-curve plots, and error tracking.

![Screenshot 2026-03-28 at 14.01.14.jpg](docs/Screenshot%202026-03-28%20at%2014.01.14.jpg)
![Screenshot 2026-03-28 at 14.02.25.jpg](docs/Screenshot%202026-03-28%20at%2014.02.25.jpg)
![Reports](docs/Screenshot%202026-02-27%20at%2014.11.42.jpg)
![Log file import](docs/Screenshot%202026-02-27%20at%2014.12.11.jpg)

### Detailed Object Information

Clicking a DSO in the main list opens the Detailed View.

* **Altitude Graphs:** Shows the object's path for the current night.
* **Moon Separation:** Displays angular separation from the moon on the main graph.
* **Imaging Opportunities:** Click "Find Imaging Opportunities" to calculate the best dates based on your horizon and moon constraints.
* **AI Observation Notes:** Generate contextual notes about the target directly from the graph view.

![Object detail](docs/Screenshot%202026-02-27%20at%2014.12.55.jpg)

### Framing Assistant

The Framing Assistant integrates Aladin Lite for visual framing and mosaic planning.

1. **Search:** Enter an object name to center the view.
2. **Camera Overlay:** Select your rig to overlay the exact sensor footprint.
3. **Rotation:** Adjust the camera rotation angle.

**Exporting Plans:** Use the **"Copy Plan (CSV)"** button to export plans compatible with ASIAIR (Plan → Import) and N.I.N.A. (Sequencer).

![Framing assistant](docs/Screenshot%202026-02-27%20at%2014.19.04.jpg)

### Configuration and Object Management

The Configuration page manages your library of objects, locations, and equipment.

* **Locations:** Manage observing sites and define **Horizon Masks** (upload a CSV/YAML list of Azimuth/Altitude points to block out terrain).
* **Objects:**
    * **Smart Add:** Nova checks your local database for duplicates before querying SIMBAD.
    * **Import Catalog:** Download curated lists (Messier, Caldwell, etc.) directly from the Nova server.
    * **Range Filters:** Filter by Magnitude and Size using expressions like `>5 <30` or `<15`.
* **Rigs:** Define your optical trains. Accurate data here is required for the Framing Assistant, Rig Snapshots, and dither recommendations.
* **Theme:** Choose Light, Dark, or Follow System. Your choice persists across sessions.

![Configuration page](docs/Screenshot%202026-02-27%20at%2014.40.01.jpg)
![Configuration detail](docs/Screenshot%202026-02-27%20at%2014.19.39.jpg)

**Manage Objects:**

* **Filtering:** Filter by ID, Name, Type, or Source. Use range expressions for Magnitude (`>5 <30`) and Size.
* **Bulk Actions:** Enable or disable multiple objects at once. Disabled objects remain in your database but are excluded from calculations and the dashboard.
* **Inspiration Content:** Upload a custom image URL, credit, and description for any object.

![Manage objects](docs/Screenshot%202026-02-27%20at%2014.20.18.jpg)

**Duplicate Manager:** Scan your library for objects within 2.5 arcminutes of each other. Choose which entry to keep — Nova automatically migrates all journal entries and projects before deleting the duplicate.

![Duplicate manager](docs/Screenshot%202026-02-27%20at%2014.20.28.jpg)

---

# Nova DSO Tracker — Setup Guide

The easiest way to install Nova DSO Tracker is via Docker.

[![Watch the video](https://img.youtube.com/vi/CF__VZEtH_I/0.jpg)](https://youtu.be/CF__VZEtH_I)

### Docker Image

A pre-built Docker image is available on Docker Hub:

[![Docker Pulls](https://img.shields.io/docker/pulls/mrantonsg/nova-dso-tracker.svg)](https://hub.docker.com/r/mrantonsg/nova-dso-tracker)

**[View on Docker Hub: mrantonsg/nova-dso-tracker](https://hub.docker.com/r/mrantonsg/nova-dso-tracker)**

### Docker Compose (Recommended)

```bash
docker compose up -d
```

This uses the official published image. For local development from source, use:

```bash
docker compose -f docker-compose-dev.yml up -d
```

### Manual Docker Installation

1. **Pull the image:**
```bash
docker pull mrantonsg/nova-dso-tracker
```

2. **Run the container:**
```bash
docker run -d \
  -p 5001:5001 \
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

2. **Create a virtual environment:**
```bash
python3 -m venv venv
source venv/bin/activate
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Run the application:**
```bash
export FLASK_APP=nova
flask run
```

Access the application at `http://localhost:5001`.

---

### AI Configuration (Optional)

Ask Nova is disabled by default. To enable it, add the following to your `instance/.env`:

```bash
# AI provider: anthropic, openai, openai-compatible, or ollama
AI_PROVIDER=anthropic

# API key for your provider (leave empty to keep AI disabled)
AI_API_KEY=sk-ant-...

# Model name (must match your provider)
#   Anthropic: claude-sonnet-4-20250514, claude-haiku-4-5-20251001
#   OpenAI:    gpt-4o, gpt-4o-mini
#   Ollama:    llama3, mistral, qwen2.5
AI_MODEL=claude-sonnet-4-20250514

# Base URL — required for openai-compatible and ollama, ignored otherwise
#   Ollama (local):  http://localhost:11434
#   MiniMax:         https://api.minimax.io/v1
AI_BASE_URL=

# Who can use AI features: 'all', or comma-separated usernames
AI_ALLOWED_USERS=all
```

**Provider notes:**
- **Anthropic** — good ranking quality, no base URL needed.
- **OpenAI** — works out of the box, no base URL needed.
- **OpenAI-compatible** — for Z.AI, MiniMax, or any custom endpoint. Requires `AI_BASE_URL`.
- **Ollama** — free, runs locally. Requires `AI_BASE_URL` (default `http://localhost:11434`). No API key needed.

In multi-user mode, set `AI_ALLOWED_USERS` to specific usernames to restrict access. Leave `AI_API_KEY` or `AI_ALLOWED_USERS` empty to hide all AI UI completely.

### User Modes

Nova supports two modes, configured in `instance/.env`:

* **Single-user mode (Default):** No login required. The app assumes one user and bypasses authentication.
* **Multi-user mode:** Ideal for hosting on a shared or public server. Requires login.
    * To enable, set `SINGLE_USER_MODE=False` in your `.env` file.
    * Initialize the database and create the first admin account with `flask init-db`.

### User Management (Multi-User Mode)

**Web Admin Panel:**

When logged in as `admin`, a **Users** link appears in the header. The admin panel at `/admin/users` allows you to:

* Create new user accounts
* Activate or deactivate users
* Reset passwords
* Delete users (login credentials only — observing data is preserved)

![Screenshot 2026-03-08 at 10.30.40.jpg](docs/Screenshot%202026-03-08%20at%2010.30.40.jpg)

**CLI Commands:**

All commands are interactive and prompt for input.

| Command | Description |
|---|---|
| `flask init-db` | Initialize the database and create the first admin user |
| `flask add-user` | Create a new user account |
| `flask rename-user` | Rename an existing user |
| `flask change-password` | Change a user's password |
| `flask delete-user` | Delete a user account |

---

### License

Nova DSO Tracker is licensed under the Apache 2.0 License **with the Commons Clause**.
Free for personal, educational, and non-commercial use only. Commercial use requires explicit written permission from the author.
