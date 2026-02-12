import time
import traceback

from nova.models import Location
from nova.helpers import get_db


def weather_cache_worker(app):
    """Background worker that periodically refreshes weather data for all active locations."""
    # Import here to avoid circular imports at module level
    from nova.helpers import get_db

    while True:
        try:
            print("[WEATHER WORKER] Starting background refresh cycle...")
            unique_locations = set()
            with app.app_context():
                # Import route-level function lazily
                from nova import get_hybrid_weather_forecast

                db = None
                try:
                    db = get_db()
                    active_locs = db.query(Location).filter_by(active=True).all()
                    for loc in active_locs:
                        if loc.lat is not None and loc.lon is not None:
                            unique_locations.add((round(loc.lat, 5), round(loc.lon, 5)))
                except Exception as e:
                    print(f"[WEATHER WORKER] CRITICAL: Error querying locations from DB: {e}")
                finally:
                    if db: db.close()

            print(f"[WEATHER WORKER] Found {len(unique_locations)} unique active locations to refresh.")
            refreshed_count = 0
            for lat, lon in unique_locations:
                try:
                    with app.app_context():
                        get_hybrid_weather_forecast(lat, lon)
                    refreshed_count += 1
                    time.sleep(5)
                except Exception as e:
                    print(f"[WEATHER WORKER] ERROR: Failed to fetch for ({lat}, {lon}): {e}")

            print(f"[WEATHER WORKER] Refresh cycle complete ({refreshed_count}/{len(unique_locations)} successful). Sleeping for 2 hours.")
            time.sleep(2 * 60 * 60)
        except Exception as e:
            print(f"[WEATHER WORKER] Unhandled exception, restarting in 60s: {e}")
            traceback.print_exc()
            time.sleep(60)
