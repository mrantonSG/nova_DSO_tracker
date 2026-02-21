import time
import traceback

from nova.config import CACHE_DIR


def iers_refresh_worker(app):
    """
    Background worker that periodically refreshes IERS (Earth rotation) data for astropy.

    This ensures astropy's IERS tables stay current for accurate coordinate transforms
    without blocking the main application or suppressing warnings.
    """
    from astropy.config import set_temp_cache
    from astropy.utils import iers

    # Initial startup delay to let the app boot
    time.sleep(60)

    while True:
        try:
            print("[IERS WORKER] Starting IERS data refresh...")

            # Use set_temp_cache to redirect IERS downloads to app's cache directory
            # This context manager handles the cache directory redirection properly
            with app.app_context():
                with set_temp_cache(CACHE_DIR):
                    # Temporarily enable auto_download to allow fetching fresh IERS data
                    # (The main app may have disabled this at startup for faster boot)
                    original_auto_download = iers.conf.auto_download
                    iers.conf.auto_download = True

                    try:
                        # IERS_Auto.open() will download fresh data if needed
                        # and update the cached table
                        iers_table = iers.IERS_Auto.open()
                        print(f"[IERS WORKER] IERS table refreshed. Range: {iers_table['MJD'].min()} - {iers_table['MJD'].max()}")
                    finally:
                        # Restore original auto_download setting
                        iers.conf.auto_download = original_auto_download

            print("[IERS WORKER] Refresh complete. Sleeping for 24 hours.")
            time.sleep(24 * 60 * 60)
        except Exception as e:
            print(f"[IERS WORKER] Unhandled exception, restarting in 60s: {e}")
            traceback.print_exc()
            time.sleep(60)
