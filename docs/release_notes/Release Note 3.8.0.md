
---

## **Version 3.8.0 - The Great Database Migration!**

This is a major update that fundamentally improves how Nova works. We've moved all user data (configs, objects, rigs, and journals) from individual `.yaml` files into a single, high-performance SQLite database (`app.db`).

This change makes the app **faster, more reliable, and much more stable**, especially when saving data or running in multi-user environments.

Because this is a big change, **please read the instructions for your setup carefully.**

---

### **For Single-Users (The Easiest Path)**

For a new installation, the easiest way to get your data into the new version is to import your old YAML files.

**Safety Belt:** Before you install or pull this new version, we **highly recommend** you go to your *old* Nova's "Config" page and use the **"Download" buttons** to get fresh backups of your `config_default.yaml`, `rigs_default.yaml`, and `journal_default.yaml` files. Save them somewhere safe.

**Your Simple 3-Step Migration:**

1.  **Install & Run:** Install and run version 3.8.0. The app will start up with a blank, "factory-default" setup.
2.  **Go to Config:** Navigate to the "Config" page in the web UI.
3.  **Import:** Use the **"Import" buttons** to upload your saved `config_default.yaml`, `rigs_default.yaml`, and `journal_default.yaml` files one by one.

That's it! Your system will be fully migrated and running on the new database.

---

### **For Multi-User (MU) Admins**

For multi-user installations, the migration is a **manual, controlled process** that you must run from the command line. This gives you full control to back up your system before proceeding.

We have included a detailed guide named `migration from yaml to DB V3.8.0.md` in the main folder of this release. **Please follow this document carefully.**

The new process uses a dedicated `flask` command to safely migrate all users from your `users.db` and their corresponding YAML files.

---

### **Other Fixes & Improvements in v3.8.0**

This release also includes several other key improvements:

* **New Install Flow:** A brand-new installation (with no `instance` folder) will now correctly create a "factory-default" setup with template files.
* **Background Workers:** The app now intelligently pre-loads weather data and calculates your "Monthly Outlook" in the background for a much faster and snappier experience.
* **Multi-User Provisioning:** Greatly improved the back-end logic for creating, managing, and provisioning users in multi-user setups.