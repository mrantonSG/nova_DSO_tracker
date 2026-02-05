#!/bin/bash
# Nova DSO Tracker - Headless Installer (App Compatible)
set -e

# --- SECURITY GUARD ---
# Blocks manual execution. Requires the Swift App to inject this variable.
if [ -z "$NOVA_APP_SESSION" ]; then
    echo "❌ Error: Access Denied."
    echo "This script must be initiated by the Nova DSO Tracker App."
    exit 1
fi

# 1. Validation (Silent)
# We assume the App already checked for Docker, but double-check to be safe.
if ! command -v docker &> /dev/null; then
    echo "Error: Docker not found."
    exit 1
fi

echo "--- INSTALLER STARTED ---"

# 2. Deploy Configuration
# The Swift App has already 'cd'ed into the correct folder.
# We just write the file to the current directory (.).

cat <<EOF > docker-compose.yml
services:
  tracker:
    image: mrantonsg/nova-dso-tracker:latest
    container_name: nova-tracker
    ports:
      - "5001:5001"
    volumes:
      - ./instance:/app/instance
    restart: unless-stopped
EOF

echo "docker-compose.yml created."

# 3. Launch
echo "Pulling images..."
docker compose pull -q

echo "Starting container..."
docker compose up -d

echo "--- INSTALLATION COMPLETE ---"