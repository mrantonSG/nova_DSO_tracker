
# Nova DSO Tracker

A Flask-based web application designed specifically for astrophotographers, providing essential data for tracking deep-sky objects (DSOs).

## Features

  - Real-time altitude and azimuth tracking for DSOs
  - Visibility forecasts based on altitude, moon illumination, and angular separation
  - Integration with Stellarium for live sky visualization
  - Customizable imaging opportunity alerts
  - Imaging Journal
  - Horizon Masking
  - Aladin based Framing Assistant
  - **New in 3.8.0:** Stable SQLite database backend for all user data
  - **New in 3.8.0:** Background-processed weather and "Monthly Outlook" caches for a faster UI
  - **New in 3.8.0:** Automatic version checking

## Technologies Used

  - Python (Flask, AstroPy, Ephem, SQLAlchemy)
  - Raspberry Pi compatible
  - API integrations (SIMBAD, Stellarium)
  - SSO for Multi User installations

-----

## Upgrading to Version 3.8.0 (Migration Guide)

Version 3.8.0 introduces a major change: all data (objects, locations, journals, rigs) is now stored in a single SQLite database (`instance/app.db`) instead of `.yaml` files. This makes the app faster and more reliable.

Please follow the correct path for your installation.

### Single-Users (The Easiest Path)

For a new installation, the easiest way to get your data into the new version is to import your old YAML files.

**Safety Belt:** Before you install this new version, we **highly recommend** you go to your *old* Nova's "Config" page and use the **"Download" buttons** to get fresh backups of your `config_default.yaml`, `rigs_default.yaml`, and `journal_default.yaml` files.

**Your Simple 3-Step Migration:**

1.  **Install & Run:** Install and run version 3.8.0. The app will start up with a blank, "factory-default" setup.
2.  **Go to Config:** Navigate to the "Config" page in the web UI.
3.  **Import:** Use the **"Import" buttons** to upload your saved `config_default.yaml`, `rigs_default.yaml`, and `journal_default.yaml` files one by one.

Your system will be fully migrated and running on the new database.

### Multi-User (MU) Admins

For multi-user installations, the migration is a **manual, controlled process** that you must run from the command line.

A detailed guide named `MIGRATION_MANUAL.md` is included in the main folder of this release. **Please follow this document carefully** to safely migrate all your users.

-----

# Nova DSO Altitude Tracker 3.8 - Quick Guide

### Purpose

Nova helps track Deep Sky Objects (DSOs) positions throughout the night for astrophotography or visual observations.

Nova updates DSOs' positions every minute. Objects marked for special attention are highlighted. It also displays the Moon's illumination and provides graphical insights into the objects' positions throughout the night.

Positions (RA, DEC) are automatically fetched from SIMBAD. Altitude (Alt) and Azimuth (Az) calculations are performed in real time, with updates reflected every minute on the web interface.

In addition it provides information about the angular separation between the objects and the moon, the time they can be imaged and the maximum altitude they reach during this time.

Includes a comprehensive Imaging Journal to log your imaging sessions.

Powerful, interactive framing assistant

### Main Interface

When opening Nova, you'll see a list of DSOs sorted by their current altitude (descending order). Objects with project notes are highlighted. You'll also see the date, local time at your selected location, and current Moon illumination. Altitudes above a definable threshold are highlighted in green. If you define a Horizon Mask and there is an obstruction, the field will be colored yellow. Under "Observable" you can find the time in minutes an object is above the altitude threshold (default 20°) and between astronomical dusk and dawn. You can also see the angular separation of the object to the moon.

The main view is organized into four tabs:

1.  **"Position"**: Contains all fields you are used to from the previous versions.
2.  **"Properties"**: Contains object-specific physical data such as the object's Type, Magnitude, Size, and its Constellation (shown as a three-letter abbreviation).
3.  **"Outlook"**: Shows the upcoming imaging opportunities for your highlighted objects.
4.  **"Journal"**: Lets you take notes to your imaging sessions.

![Screenshot_35_index.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_35_index.png)


### Sorting and Searching

  - **Sorting:** By default, objects are sorted by descending altitude. You can change sorting by clicking on column headers. Clicking twice reverses the sorting order. Also new in 2.7 is the sorting indicator for every column.

  - **Searching:** Each column header includes a search field allowing filtering. You can combine search terms and use logical operators like `<` or `>` for refined filtering. With `!` you can exclude content. Nova retains your sorting and filtering choices until you alter them.

  - You can now also add multiple phrases in a search field. For example multiple DSO types:

![Screenshot_29_filter.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_30_filter.png)

and you can combine filters and sorting from both tabs:

![Screenshot_28_filter_sort.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_30_filter_sort.png)

In this example the objects are sorted by maximum observation time.
In combination with the filter under "Properties" this shows potential observation time only for the DSO type selected.
If you have only a limited view to the sky, for instance because you image from a balcony, you can also set azimuth filters, to focus on objects actually visible to you.
Once you set the sorting order and filters, the screen will continue updating every minute, so you can see where the objects are at any moment. These settings will stay even when switch screens, until you manually reset them.

### Configuration

Nova comes pre-loaded with several DSOs. You can manage (add, remove, or edit) locations and objects from the configuration screen. To add an object, enter its ID and click `search`. This will trigger a SIMBAD search. If an object was found you can edit its name and project fields and finally add it to your list.

  - **Object Designations:** SIMBAD may not recognize all object IDs. You can however add objects also manually. In that case you need to enter RA and DEC.
  - **Highlighting Objects:** Entering text in the "Notes" field (project in the yaml file) highlights the corresponding object in the main interface. The main purpose is to mark objects you plan to image. In the project field you can put all necessary information, such as the rig you plan to use.

![Screenshot_35_HM.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_35_HM.png)
![Screenshot_27_config.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_30_rigs.png)


#### Data Storage in v3.8.0+ (The Database)

Starting in version 3.8.0, all your locations, objects, and settings are stored in a single database file (`instance/app.db`). The old `.yaml` files are **no longer used** for live data.

The "Download" and "Upload" buttons on the config page are now used for **backing up and restoring** your data from the database.

There is an optional field in locations, where you can define a horizon mask. So if you have certain obstructions at your imaging sites you can configure them there. All calculations will then be based on obstruction free view.

Under the tab "Rigs" you can configure your equipment, configure rigs and calculate the sampling. The rigs configured here can later be selected in the journal.

### Populating Missing Object Details

Nova includes fields for object Type, Magnitude, Size, etc. If you imported data from an older version, these fields might be empty.

To fix this, press the **"Fetch Missing Details"** button. A message will pop up reminding you that this process will take some time.

In order to successfully add the information, your system needs to be connected to the internet.
Nova will query multiple catalogs to find the requested information. If you have Stellarium on your computer, you should open it as well - nova will use it as fallback if the search in the various catalogs was not successful.

After pressing "ok" just wait until the page reloads.
If everything went well, you can find the additional fields now filled out.

### Detailed Object Information

Clicking on a DSO in the main list opens detailed graphical information about its nightly position and altitude. These graphics are generated on-demand and might take a few seconds to appear, depending on your computer's performance.
You can not only see the current night, but you can select a date you want to see. Just select the day and or month and year and click on "Day".
The daily graph also displays a vertical dashed line indicating the meridian transit, which is the point of highest altitude and a crucial time for equatorial mount users to plan for a "meridian flip."

![Screenshot_36_graphic.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_36_graphic.png)

![Screenshot_28_month.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_32_month.png)

![Screenshot_28_year.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_32_year.png)

If you click on the button "Find Imaging Opportunities", you will get a list of dates and times when imaging the selected object is possible.
From here you can also add the information to your calendar.

![Screenshot_29_opportunities.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_29_opportunities.png)

In addition, you will also see an approximate fit of your configured rigs.

![Screenshot_36_rig_fit.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_36_rig_fit.png)

You can edit the selection criteria for the opportunity search in the configuration settings.

The "Show Framing" button opens a powerful framing assistant.

![Screenshot_29_opportunities.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_32_framing.png)

### Imaging Journal

Nova DSO Tracker also offers the additional possibility to take notes to imaging sessions.
On the main screen you can find the Journal at the fourth tab:

![Screenshot_28_Index_journal.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_30_Index_journal.png)

Here you can find all your imaging sessions. If you want to see the detail, click on it and the graph screen for that particular day will open and in addition you will find the session details:

![Screenshot_36_sessions.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_36_sessions.png)

You can add new sessions by clicking the "add new session" button - and filling up the form:

![Screenshot_36_addform.png](https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/docs/Screenshot_36_addform.png)

### Anonymous Telemetry for Better Development

Version 3.3 (and later) introduces an optional and anonymous telemetry system to help me understand how the app is used and where to focus development efforts.

  * **What it is for**: It sends a small, anonymous "heartbeat" to help me understand things like which operating systems are most common and if it runs under Docker.
  * **What it collects**: It only sends **anonymous aggregate data**, such as your app version, OS type (e.g., Windows, Linux), and the *counts* of your objects, rigs, and locations. (to understand if we run in a bottleneck)
  * **What it DOES NOT collect**: It **NEVER** sends any personal data, including the names of your objects, your location coordinates, your project notes, or any other sensitive information.
  * **It is Opt-Out**: You can disable this feature at any time on the **Configuration -\> General** page.

# Nova Astronomical Tracker Setup Guide

The easiest way to install Nova DSO Tracker is via Docker. There is also a video that explains the process.

\<div align="center"\>

[![Watch the video](https://img.youtube.com/vi/CF__VZEtH_I/0.jpg)](https://youtu.be/CF__VZEtH_I)

\</div\>

Alternatively you can of course run it directly in Python:

This guide walks you through setting up your Flask astronomical tracking app, including creating a virtual environment and installing all required dependencies.

## 1\. Install Python 3 (if applicable)

Using **Homebrew** (recommended):

1.  Open **Terminal**.
2.  Install Python 3 by running:

<!-- end list -->

```bash
brew install python
```

3.  Verify the installation:

<!-- end list -->

```bash
python3 --version
pip3 --version
```

## 2\. Create a Project Directory

1.  Open **Terminal**.

(Optional) change the directory to where you want the software folder to be installed

2.  Clone the repository and open the new directory

<!-- end list -->

```bash
git clone https://github.com/mrantonSG/nova_DSO_tracker.git
cd nova_DSO_tracker
```

## 3\. Set Up a Virtual Environment

A virtual environment keeps your project's dependencies isolated.

1.  Create a virtual environment named `nova`:

<!-- end list -->

```bash
python3 -m venv nova
```

2.  Activate the virtual environment:

<!-- end list -->

```bash
source nova/bin/activate
```

Your terminal prompt should now start with `(nova)`.

## 4\. Install Required Dependencies

Install the required Python packages:

```bash
pip install Flask numpy pytz ephem PyYAML matplotlib astroquery astropy flask_login python-decouple python-dotenv cerberus ics arrow flask_sqlalchemy sqlalchemy pyjwt
```

(Optional) Verify installed packages:

```bash
pip freeze
```

## 5\. Run the Application

1.  With your virtual environment activated, run:

<!-- end list -->

```bash
python nova.py
```

2.  Open your browser and navigate to:

<!-- end list -->

```
http://localhost:5001
```

*Note: The first startup may take a minute.*

## 6\. (Optional) Deactivate the Virtual Environment

When finished, deactivate by running:

```bash
deactivate
```

## 7\. (Optional) Running it in the background

if you want to close the terminal window, for instance after starting the software on a server, start it like that:

```bash
nohup python3 nova.py > app.log 2>&1 &
```

once you want to later stop it, look for the process:

```bash
ps aux | grep python
```

and stop it:

```bash
kill <the number you've found>
```

## Additional Notes

### User Modes in Nova

Nova supports two user modes, set in the `instance/.env` file:

  - **Single-user mode**: This is the default setting.
  - **Multi-user mode**: Ideal for embedding into external websites where advanced user management is handled externally.

Basic user management is built-in primarily for testing or simple use cases. For integration into a website, it's recommended to handle user management via the website itself.

Additionally, Nova supports a limited **guest mode** when running in multi-user mode. Guest mode provides automatic access without requiring login credentials, offering a basic overview and limited functionality to give new users a general impression of the app.

### Switching Modes

To enable multi-user mode, update or add the following line in your `instance/.env` file:

```env
SINGLE_USER_MODE=False
```

To revert to single-user mode:

```env
SINGLE_USER_MODE=True
```

### Installation on a server:

In case you want to have access from various different devices (computers, iPad ...) from within or outside of your home network, you can install it on a server. The software is now optimized to run on a Raspberry Pi5 with good performance. In order to still being able to send objects to Stellarium, please read "[stellarium\_access\_from\_server](https://www.google.com/search?q=stellarium_access_from_server.md)".

#### 💡 Tip for Raspberry Pi 5:

For better performance, run Nova with [Gunicorn](https://gunicorn.org), a lightweight WSGI server. Install it via `pip install gunicorn`, then start the app using `gunicorn -w 4 -b 127.0.0.1:8090 "nova:app"`. This reduces CPU load and improves response times compared to the built-in Flask server.

### Docker Image:

A pre-built Docker image is available on Docker Hub for easy setup:

[![Docker Pulls](https://img.shields.io/docker/pulls/mrantonsg/nova-dso-tracker.svg)](https://hub.docker.com/r/mrantonsg/nova-dso-tracker)

**[View on Docker Hub: mrantonsg/nova-dso-tracker](https://hub.docker.com/r/mrantonsg/nova-dso-tracker)**

See the Docker Hub page for instructions on how to run the container.

### License:

Nova DSO Tracker is licensed under the Apache 2.0 License **with the Commons Clause**.
Free for personal, educational, and non-commercial use only. Commercial use requires explicit permission.
See [LICENSE](LICENSE)  for full details.

clear skies\!