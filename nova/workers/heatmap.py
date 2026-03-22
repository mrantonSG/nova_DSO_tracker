import os
import json
import time
import traceback
import warnings
from datetime import datetime, timedelta

import pytz
import ephem
from sqlalchemy.orm import selectinload
from sqlalchemy import func

from nova.models import DbUser, Location, AstroObject, UiPref, SessionLocal
from nova.config import CACHE_DIR
from nova.helpers import get_db
from modules.astro_calculations import calculate_observable_duration_vectorized


def heatmap_background_worker(app):
    """
    Background thread that gently checks for stale heatmap caches (older than 24h)
    and regenerates them chunk-by-chunk without blocking the CPU.
    """
    # Ensure cache directory exists
    os.makedirs(CACHE_DIR, exist_ok=True)

    # Initial startup delay to let the app boot
    time.sleep(30)

    while True:
        print("[HEATMAP WORKER] Starting maintenance cycle...")

        try:
            # 1. Gather Tasks (Users & Locations)
            tasks = []
            with app.app_context():
                db = get_db()
                # Eager load relationships to avoid N+1 queries
                users = (
                    db.query(DbUser)
                    .filter_by(active=True)
                    .options(
                        selectinload(DbUser.ui_prefs),
                        selectinload(DbUser.locations).selectinload(
                            Location.horizon_points
                        ),
                    )
                    .all()
                )

                # Pre-fetch object counts for all users in one query
                obj_counts = (
                    db.query(
                        AstroObject.user_id, func.count(AstroObject.id).label("count")
                    )
                    .filter_by(enabled=True)
                    .group_by(AstroObject.user_id)
                    .all()
                )
                obj_count_map = {user_id: count for user_id, count in obj_counts}

                for u in users:
                    # Get User's Config for preferences (already eager loaded)
                    user_cfg = {}
                    if u.ui_prefs and u.ui_prefs.json_blob:
                        try:
                            user_cfg = json.loads(u.ui_prefs.json_blob)
                        except:
                            pass

                    # Get Active Locations (already eager loaded)
                    locs = [loc for loc in u.locations if loc.active]

                    # Get Object Count from pre-fetched map
                    obj_count = obj_count_map.get(u.id, 0)

                    for loc in locs:
                        tasks.append(
                            {
                                "user_id": u.id,
                                "obj_count": obj_count,
                                "loc_name": loc.name,
                                "lat": loc.lat,
                                "lon": loc.lon,
                                "tz": loc.timezone,
                                "mask": [
                                    [hp.az_deg, hp.alt_min_deg]
                                    for hp in loc.horizon_points
                                ],
                                "alt_threshold": user_cfg.get("altitude_threshold", 20),
                            }
                        )

            # 2. Process Tasks
            for task in tasks:
                user_id = task["user_id"]
                loc_safe = task["loc_name"].lower().replace(" ", "_")
                obj_count = task["obj_count"]

                # Check the timestamp of the LAST chunk (part11) as a proxy for the whole set
                base_filename = f"heatmap_v5_{user_id}_{loc_safe}_{obj_count}"
                last_chunk_path = os.path.join(
                    CACHE_DIR, f"{base_filename}.part11.json"
                )

                should_update = True
                if os.path.exists(last_chunk_path):
                    age = time.time() - os.path.getmtime(last_chunk_path)
                    if age < 86400:  # 24 Hours
                        should_update = False

                if should_update:
                    print(
                        f"[HEATMAP WORKER] Updating stale cache for User {user_id} @ {task['loc_name']}..."
                    )

                    # --- REGENERATE ALL 12 CHUNKS ---
                    with app.app_context():
                        db = get_db()
                        # Only calculate heatmap for enabled objects
                        all_objects = (
                            db.query(AstroObject)
                            .filter_by(user_id=user_id, enabled=True)
                            .all()
                        )
                        valid_objects = [
                            o
                            for o in all_objects
                            if o.ra_hours is not None and o.dec_deg is not None
                        ]

                        # Filter Invisible (Geometric)
                        visible_objects = []
                        for obj in valid_objects:
                            dec = float(obj.dec_deg)
                            if (90 - abs(task["lat"] - dec)) >= task["alt_threshold"]:
                                visible_objects.append(obj)
                        visible_objects.sort(key=lambda x: float(x.ra_hours))

                        # Validate Timezone
                        try:
                            local_tz = pytz.timezone(task["tz"])
                            valid_tz = task["tz"]
                        except Exception:
                            print(
                                f"[HEATMAP WORKER] WARN: Invalid timezone '{task['tz']}' for '{task['loc_name']}'. Using UTC."
                            )
                            local_tz = pytz.utc
                            valid_tz = "UTC"

                        now = datetime.now(local_tz)
                        start_date_year = now.date() - timedelta(days=now.weekday())

                        # Loop 12 chunks
                        for chunk_idx in range(12):
                            weeks_per_chunk = 52 // 12
                            remainder = 52 % 12
                            start_week = chunk_idx * weeks_per_chunk + min(
                                chunk_idx, remainder
                            )
                            end_week = (
                                start_week
                                + weeks_per_chunk
                                + (1 if chunk_idx < remainder else 0)
                            )

                            weeks_x = []
                            target_dates = []
                            moon_phases = []

                            for i in range(start_week, end_week):
                                d = start_date_year + timedelta(weeks=i)
                                weeks_x.append(d.strftime("%b %d"))
                                target_dates.append(d.strftime("%Y-%m-%d"))
                                try:
                                    dt_moon = local_tz.localize(
                                        datetime.combine(d, datetime.min.time())
                                    ).astimezone(pytz.utc)
                                    moon_phases.append(
                                        round(ephem.Moon(dt_moon).phase, 1)
                                    )
                                except:
                                    moon_phases.append(0)

                            z_scores_chunk = []
                            y_names, meta_ids, meta_active = [], [], []
                            meta_types, meta_cons, meta_mags, meta_sizes, meta_sbs = (
                                [],
                                [],
                                [],
                                [],
                                [],
                            )

                            for obj in visible_objects:
                                ra, dec = float(obj.ra_hours), float(obj.dec_deg)
                                obj_scores = []
                                for i, date_str in enumerate(target_dates):
                                    with warnings.catch_warnings():
                                        warnings.filterwarnings(
                                            "ignore",
                                            message=".*Tried to get polar motions.*",
                                        )
                                        obs_dur, max_alt, _, _ = (
                                            calculate_observable_duration_vectorized(
                                                ra,
                                                dec,
                                                task["lat"],
                                                task["lon"],
                                                date_str,
                                                valid_tz,
                                                task["alt_threshold"],
                                                60,
                                                horizon_mask=task["mask"],
                                            )
                                        )
                                    score = 0
                                    duration_mins = (
                                        obs_dur.total_seconds() / 60 if obs_dur else 0
                                    )
                                    if (
                                        max_alt is not None
                                        and max_alt >= task["alt_threshold"]
                                        and duration_mins >= 45
                                    ):
                                        norm_alt = min(
                                            (max_alt - task["alt_threshold"])
                                            / (90 - task["alt_threshold"]),
                                            1.0,
                                        )
                                        norm_dur = min(duration_mins / 480, 1.0)
                                        score = (0.4 * norm_alt + 0.6 * norm_dur) * 100
                                        if moon_phases[i] > 60:
                                            score *= (
                                                1 - ((moon_phases[i] - 60) / 40) * 0.9
                                            )
                                    obj_scores.append(round(score, 1))

                                z_scores_chunk.append(obj_scores)

                                # Metadata
                                dname = obj.common_name or obj.object_name
                                if obj.type:
                                    dname += f" [{obj.type}]"
                                y_names.append(dname)
                                meta_ids.append(obj.object_name)
                                meta_active.append(1 if obj.active_project else 0)
                                meta_types.append(str(obj.type or ""))
                                meta_cons.append(str(obj.constellation or ""))
                                try:
                                    meta_mags.append(float(obj.magnitude))
                                except:
                                    meta_mags.append(999.0)
                                try:
                                    meta_sizes.append(float(obj.size))
                                except:
                                    meta_sizes.append(0.0)
                                try:
                                    meta_sbs.append(float(obj.sb))
                                except:
                                    meta_sbs.append(999.0)

                            chunk_data = {
                                "chunk_index": chunk_idx,
                                "x": weeks_x,
                                "z_chunk": z_scores_chunk,
                                "y": y_names,
                                "moon_phases": moon_phases,
                                "ids": meta_ids,
                                "active": meta_active,
                                "dates": target_dates,
                                "types": meta_types,
                                "cons": meta_cons,
                                "mags": meta_mags,
                                "sizes": meta_sizes,
                                "sbs": meta_sbs,
                            }

                            # Save Chunk
                            chunk_filename = os.path.join(
                                CACHE_DIR, f"{base_filename}.part{chunk_idx}.json"
                            )
                            with open(chunk_filename, "w") as f:
                                json.dump(chunk_data, f)

                            # Sleep briefly between chunks to yield CPU
                            time.sleep(2)

                    print(f"[HEATMAP WORKER] Finished updating {task['loc_name']}.")
                    # Sleep between locations
                    time.sleep(30)

            # Sleep 4 hours before next check
            print("[HEATMAP WORKER] Cycle done. Sleeping 4 hours.")
            time.sleep(4 * 60 * 60)
        except Exception as e:
            print(f"[HEATMAP WORKER] Unhandled exception, restarting in 60s: {e}")
            traceback.print_exc()
            time.sleep(60)
