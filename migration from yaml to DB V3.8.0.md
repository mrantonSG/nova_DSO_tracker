
-----

## Upgrading to v3.8.0+ (Database Migration)

Version 3.8.0 introduces a new SQLite database (`app.db`) that replaces the old YAML files (`config_*.yaml`, `journal_*.yaml`, `rigs_*.yaml`) for storing all user data. This provides significant performance and stability improvements.

When you first run this version, your existing data must be migrated from your YAML files into this new database. This migration process is **automatic for Single-Users** and **manual for Multi-User admins**.

### **Prerequisite for All Upgrades**

This migration only runs if the new database file (`app.db`) does **not** exist.

If you are upgrading from an older version, you **must** first stop the application and delete the old database file (if it exists) to trigger the migration.

-----

## **Part 1: Docker Installations**

### Single-User Migration (Docker)

For most users, the migration is fully automatic.

1.  **Configuration:** Ensure your `instance/.env` file is set to `SINGLE_USER_MODE=True` (or that the variable is not present, as `True` is the default).
2.  **Stop & Delete DB:**
    ```bash
    docker compose down
    rm ./instance/app.db
    ```
3.  **Place Files:** Make sure your existing YAML files (`config_default.yaml`, `journal_default.yaml`, `rigs_default.yaml`) are located in the `./instance/configs/` directory.
4.  **Start:** Run the application as usual:
    ```bash
    docker compose up -d
    ```

**What Happens:** The application will automatically detect that it's a first run. It will use a file lock to safely handle the migration (even with Gunicorn) and import all your data. This first boot may take a few moments.

### Multi-User / Admin Migration (Docker)

For multi-user installations, the migration is a manual, one-time command.

1.  **Configuration:** Ensure your `instance/.env` file is set to `SINGLE_USER_MODE=False`.
2.  **Stop & Backup:** Stop the application and back up your `instance` directory.
    ```bash
    docker compose down
    tar czvf instance_backup_$(date +%Y%m%d_%H%M%S).tar.gz ./instance
    ```
3.  **Place Files:** Ensure all user YAML files (e.g., `config_anton.yaml`, `rigs_anton.yaml`, etc.) are in the `./instance/configs/` directory.
4.  **Delete Old DB:**
    ```bash
    rm ./instance/app.db
    ```
5.  **Run Migration:** Execute the migration command from your terminal:
    ```bash
    docker compose run --rm nova flask --app nova migrate-yaml-to-db
    ```
6.  **Monitor:** Watch the output. The script will log all users it finds and migrates. It will finish with `✅ Migration task complete.`
7.  **Start:** Once the migration is successful, start your application normally:
    ```bash
    docker compose up -d
    ```

-----

## **Part 2: Non-Docker (Bare Metal / Venv) Installations**

### Single-User Migration (Non-Docker)

The migration is fully automatic, whether you use the Flask dev server or Gunicorn.

1.  **Configuration:** Ensure your `instance/.env` file is set to `SINGLE_USER_MODE=True`.
2.  **Stop Server:** Stop your Gunicorn or Flask process (e.g., `Ctrl+C`).
3.  **Delete Old DB:**
    ```bash
    rm ./instance/app.db
    ```
4.  **Place Files:** Make sure your YAML files are in `./instance/configs/`.
5.  **Activate Venv:**
    ```bash
    source venv/bin/activate
    ```
6.  **Start:** Run the application as you normally would:
      * *(For Flask dev server):*
        ```bash
        python nova.py
        ```
      * *(For Gunicorn):*
        ```bash
        gunicorn --workers 4 -b 0.0.0.0:5001 "nova:app"
        ```

**What Happens:** The application will start, acquire the file lock, run the migration, and then continue starting the server.

### Multi-User / Admin Migration (Non-Docker)

This is a manual, one-time command, just like the Docker version.

1.  **Configuration:** Ensure your `instance/.env` file is set to `SINGLE_USER_MODE=False`.
2.  **Stop Gunicorn:** Stop the Gunicorn process using your process manager.
      * *(If using systemd):*
        ```bash
        sudo systemctl stop nova.service
        ```
      * *(If using supervisor):*
        ```bash
        sudo supervisorctl stop nova
        ```
3.  **Backup:**
    ```bash
    tar czvf instance_backup_$(date +%Y%m%d_%H%M%S).tar.gz ./instance
    ```
4.  **Place Files:** Ensure all user YAML files are in `./instance/configs/`.
5.  **Delete Old DB:**
    ```bash
    rm ./instance/app.db
    ```
6.  **Run Migration:**
      * First, activate your virtual environment:
        ```bash
        source venv/bin/activate
        ```
      * Then, run the Flask migration command:
        ```bash
        flask --app nova migrate-yaml-to-db
        ```
7.  **Monitor:** Watch the terminal. It will finish with `✅ Migration task complete.`
8.  **Start Gunicorn:** Restart your Gunicorn service.
      * *(If using systemd):*
        ```bash
        sudo systemctl start nova.service
        ```
      * *(If using supervisor):*
        ```bash
        sudo supervisorctl start nova
        ```