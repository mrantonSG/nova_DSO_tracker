
#### Data Management & Backup

These three buttons allow you to enrich your data, back it up, or transfer it between different **Nova App** instances (e.g., moving data from a personal laptop to a cloud server).

**1. Fetch Missing Details**

If you have objects in your library with missing data (like Magnitude, Size, or Classification), click this button.

* **How it works:** Nova scans your library for incomplete entries and queries external astronomical databases to automatically fill in the blanks.
* **Note:** This process might take a long time depending on how many objects need updating.

**2. Download (Backup)**

Click the **Download ▼** dropdown to export your data into portable files. This is essential for backing up your work or migrating to a new device.

* **Configuration:** Exports your Locations, Objects, and General Settings (YAML).
* **Journal:** Exports all your Projects and Session logs (YAML).
* **Rigs:** Exports your Telescope, Camera, and Rig definitions (YAML).
* **Journal Photos:** Downloads a ZIP archive containing all images attached to your observation logs.

**3. Import (Restore & Transfer)**

Click the **Import ▼** dropdown to load data from a backup file.

* **Workflow:** Select the type of data you want to load (Config, Journal, Rigs, or Photos) and choose the corresponding file from your computer.
* **⚠️ Important Warning:** Importing is generally a **"Replace"** operation. For example, importing a Configuration file will replace your current Locations and Objects with those in the file. This ensures your system exactly matches the backup state, which is perfect for restoring data or syncing a server with your local version.
