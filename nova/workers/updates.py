import time
import traceback
import requests

from nova.config import APP_VERSION, LATEST_VERSION_INFO


def check_for_updates(app):
    """
    Checks GitHub for the latest release version in a background thread.
    """
    owner = "mrantonSG"
    repo = "nova_DSO_tracker"

    while True:
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            print(f"[VERSION CHECK] Fetching latest release info from {url}")

            response = requests.get(url, timeout=10)
            response.raise_for_status()

            data = response.json()
            latest_version_str = data.get("tag_name", "").lower().lstrip('v')
            current_version_str = APP_VERSION

            if not latest_version_str or not current_version_str:
                print("[VERSION CHECK] Could not determine current or latest version string.")
            else:
                current_version_tuple = tuple(map(int, current_version_str.split('.')))
                latest_version_tuple = tuple(map(int, latest_version_str.split('.')))

                if latest_version_tuple > current_version_tuple:
                    print(f"[VERSION CHECK] New version found: {latest_version_str}")
                    LATEST_VERSION_INFO.update({
                        "new_version": latest_version_str,
                        "url": data.get("html_url")
                    })
                else:
                    print(f"[VERSION CHECK] You are running the latest version (or a newer dev version).")
                    LATEST_VERSION_INFO.clear()

            print("[UPDATE WORKER] Sleeping for 24 hours.")
            time.sleep(24 * 60 * 60)
        except Exception as e:
            print(f"[UPDATE WORKER] Unhandled exception, restarting in 60s: {e}")
            traceback.print_exc()
            time.sleep(60)
