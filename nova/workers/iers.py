import time
import traceback

from nova.config import CACHE_DIR

# IERS data is considered stale after 90 days (3 months)
# IERS-A predictions extend 3-6 months ahead and are reasonably stable.
# For amateur astronomy, quarterly refresh is sufficient.
IERS_MAX_AGE_DAYS = 90


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
            print("[IERS WORKER] Checking IERS data freshness...")

            # Use set_temp_cache to redirect IERS downloads to app's cache directory
            # This context manager handles the cache directory redirection properly
            with app.app_context():
                with set_temp_cache(CACHE_DIR):
                    # Temporarily enable settings to allow fetching fresh IERS data
                    # (The main app may have disabled these at startup for faster boot)
                    original_auto_download = iers.conf.auto_download
                    original_auto_max_age = iers.conf.auto_max_age

                    iers.conf.auto_download = True
                    iers.conf.auto_max_age = IERS_MAX_AGE_DAYS

                    try:
                        # IERS_Auto.open() will download fresh data if cache is stale
                        # (older than auto_max_age days)
                        iers_table = iers.IERS_Auto.open()
                        print(f"[IERS WORKER] IERS table ready. MJD range: {iers_table['MJD'].min():.1f} - {iers_table['MJD'].max():.1f}")
                    finally:
                        # Restore original settings
                        iers.conf.auto_download = original_auto_download
                        iers.conf.auto_max_age = original_auto_max_age

            print("[IERS WORKER] Check complete. Sleeping for 24 hours.")
            time.sleep(24 * 60 * 60)
        except Exception as e:
            print(f"[IERS WORKER] Unhandled exception, restarting in 60s: {e}")
            traceback.print_exc()
            time.sleep(60)
