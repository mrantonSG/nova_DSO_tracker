
#### Data Management & Backup

These four buttons allow you to enrich your data, back it up, transfer it between different **Nova App** instances (e.g., moving data from a personal laptop to a cloud server), or keep a local Nova and a server account in step.

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

**4. Sync**

Click **Sync** to keep this Nova and your account on app.nova-tracker.com in step — objects, locations, rigs, projects, journal sessions, photos and log files. Every computer that syncs needs its own token, so several computers can follow one account on the server. On a multi-user installation, **Sync with a server** only appears if the administrator has turned it on — see the help on the **Sync** page.

* **Setup:** On the server, click **Sync** and create a token under **Use this Nova instance as a server**. It is shown only once, so copy it right away. On the other computer, click **Sync**, enter the **Server URL** and the **Sync token**, then click **Connect**.
* **Your data is only on this computer so far?** Export it with **Download ▼**, import those YAML files on the server, and only then connect: a server that holds no data refuses a download.
* **First step:** **Download from server** replaces this computer's data with the server's. **Sync now** stays disabled until this download is done. If this computer already holds data, Nova asks you to type REPLACE. A local Nova never overwrites the server with its whole dataset — after that first download it only sends your own changes.
* **Syncing:** **Sync now** sends your changes up and pulls the server's changes down. Read the **Sync summary** first: **Up** and **Down**, each with new, changed and deleted, plus the files. Nothing is applied until you click **Apply**, and **Abort** cancels. A sync that would delete a large share of the records on either side asks you to confirm twice.
* **Changed on both sides?** The newer change wins — also when one side deleted the item — and a tie goes to the server. The summary lists each of these items and which copy was kept: "… — this computer's version kept" or "… — server version kept".
* **Backups:** Nova backs up this computer's data before every download and every sync. The 5 most recent sync backups are kept.
* **Disconnect** removes the pairing from this computer and deletes nothing on either side. The token stays valid on the server until you **Revoke** it there.
