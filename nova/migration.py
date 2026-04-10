import os
import re
import json
import traceback
from datetime import datetime

import yaml
import requests
from flask import current_app
from sqlalchemy.orm import selectinload
from astropy.coordinates import SkyCoord, get_constellation
import astropy.units as u

from nova.config import (
    SINGLE_USER_MODE, CONFIG_DIR, NOVA_CATALOG_URL,
    DEFAULT_HTTP_TIMEOUT,
)
from nova.helpers import (
    get_db, normalize_object_name, _atomic_write_yaml,
    _read_yaml, discover_catalog_packs,
    _compute_rig_metrics_from_components, dither_display,
)
from nova.models import (
    DbUser, Location, HorizonPoint, AstroObject,
    SavedFraming, SavedView, Component, Rig,
    JournalSession, Project, UserCustomFilter, UiPref,
)

def load_catalog_pack(pack_id: str) -> tuple[dict | None, dict | None]:
    """Load a specific catalog pack from the central web repository."""

    # 1. Get the manifest (this will be cached)
    all_packs_meta = discover_catalog_packs()

    # 2. Find the metadata for the requested pack
    meta = next((p for p in all_packs_meta if p.get("id") == pack_id), None)

    if not meta:
        print(f"[CATALOG LOAD] Pack ID '{pack_id}' not found in manifest.")
        return (None, None)

    filename = meta.get("filename")
    if not filename:
        print(f"[CATALOG LOAD] Pack ID '{pack_id}' has no filename in manifest.")
        return (None, None)

    # --- THIS IS THE NEW LOGIC ---
    # Get the URL from the (possibly empty) config
    url_to_use = NOVA_CATALOG_URL
    if not url_to_use:
        # If it's not in the config, use the hardcoded default
        url_to_use = "https://catalogs.nova-tracker.com"
    # --- END NEW LOGIC ---

    # 3. Check if a URL is available (from either source)
    if not url_to_use:
        print("[CATALOG LOAD] No Catalog URL is configured. Cannot download pack.")
        return (None, None)

    pack_url = f"{url_to_use.rstrip('/')}/{filename}"

    try:
        # 4. Fetch the YAML file
        print(f"[CATALOG LOAD] Downloading pack from {pack_url}")
        r = requests.get(pack_url, timeout=15)
        r.raise_for_status()

        # 5. Parse the YAML content from the response
        pack_data = yaml.safe_load(r.text) or {}

        return (pack_data, meta)

    except requests.exceptions.RequestException as e:
        print(f"[CATALOG LOAD] Failed to download pack '{filename}': {e}")
    except yaml.YAMLError as e:
        print(f"[CATALOG LOAD] Failed to parse YAML for '{filename}': {e}")
    except Exception as e:
        print(f"[CATALOG LOAD] An unexpected error occurred: {e}")

    return (None, None)


def _upsert_user(db, username: str) -> DbUser:
    u = db.query(DbUser).filter_by(username=username).one_or_none()
    if not u:
        u = DbUser(username=username, active=True)
        db.add(u)
        db.flush()
    return u


def _migrate_locations(db, user: DbUser, config: dict):
    """
    Idempotent import of locations:
      - Upsert per (user_id, name)
      - Replace horizon points on update
      - Ensure only default_location has is_default=True
    """
    locs = (config or {}).get("locations", {}) or {}
    default_name = (config or {}).get("default_location")

    # First, clear default flags for this user's locations. We'll set the correct one below.
    db.query(Location).filter_by(user_id=user.id).update({Location.is_default: False})
    db.flush()

    for name, loc in locs.items():
        try:
            lat = float(loc.get("lat"))
            lon = float(loc.get("lon"))
            tz = loc.get("timezone", "UTC")
            alt_thr_val = loc.get("altitude_threshold")
            alt_thr = float(alt_thr_val) if alt_thr_val is not None else None
            new_is_default = (name == default_name)

            # Bortle scale: validate 1-9 if present, ignore silently if absent
            raw_bortle = loc.get("bortle_scale")
            bortle_val = None
            if raw_bortle is not None:
                try:
                    bortle_int = int(raw_bortle)
                    if 1 <= bortle_int <= 9:
                        bortle_val = bortle_int
                except (ValueError, TypeError):
                    pass

            existing = db.query(Location).filter_by(user_id=user.id, name=name).one_or_none()
            if existing:
                # --- UPDATE existing row
                existing.lat = lat
                existing.lon = lon
                existing.timezone = tz
                existing.altitude_threshold = alt_thr
                existing.is_default = new_is_default
                existing.active = loc.get("active", True)
                existing.bortle_scale = bortle_val
                existing.comments = loc.get("comments")

                # --- START FIX: Replace horizon points using relationship cascade ---
                new_horizon_points = []
                hm = loc.get("horizon_mask")
                if isinstance(hm, list):
                    for pair in hm:
                        try:
                            az, altmin = float(pair[0]), float(pair[1])
                            # Create the object, but don't add it to the session.
                            # Appending to the list handles the relationship.
                            new_horizon_points.append(
                                HorizonPoint(az_deg=az, alt_min_deg=altmin)
                            )
                        except (ValueError, TypeError, IndexError) as hp_err:
                            current_app.logger.warning(f"[MIGRATION] Invalid horizon point skipped for location '{name}': {pair} - {hp_err}")

                # Assigning the new list triggers the 'delete-orphan' cascade.
                # All old points are deleted, all new points are added.
                existing.horizon_points = new_horizon_points
                # --- END FIX ---

            else:
                # --- INSERT new row
                row = Location(
                    user_id=user.id,
                    name=name,
                    lat=lat,
                    lon=lon,
                    timezone=tz,
                    altitude_threshold=alt_thr,
                    is_default=new_is_default,
                    active=loc.get("active", True),
                    bortle_scale=bortle_val,
                    comments=loc.get("comments")
                )
                db.add(row);
                db.flush()  # Flush to get the row.id

                # --- START REFACTOR: Use the same pattern for consistency ---
                new_horizon_points = []
                hm = loc.get("horizon_mask")
                if isinstance(hm, list):
                    for pair in hm:
                        try:
                            az, altmin = float(pair[0]), float(pair[1])
                            new_horizon_points.append(
                                HorizonPoint(az_deg=az, alt_min_deg=altmin)
                            )
                        except (ValueError, TypeError, IndexError) as hp_err:
                            current_app.logger.warning(f"[MIGRATION] Invalid horizon point skipped for new location '{name}': {pair} - {hp_err}")

                # Assign the new list to the new row object
                row.horizon_points = new_horizon_points
                # --- END REFACTOR ---
        except Exception as e:
            print(f"[MIGRATION] Skip/repair location '{name}': {e}")



def _heal_saved_framings(db, user: DbUser):
    """
    Scans for SavedFraming records that have a rig_name but no rig_id
    (orphaned because config was imported before rigs) and tries to link them.
    """
    try:
        orphans = db.query(SavedFraming).filter(
            SavedFraming.user_id == user.id,
            SavedFraming.rig_name != None,
            SavedFraming.rig_id == None
        ).all()

        count = 0
        for f in orphans:
            # Try to find the rig by name
            rig = db.query(Rig).filter_by(user_id=user.id, rig_name=f.rig_name).one_or_none()
            if rig:
                f.rig_id = rig.id
                count += 1

        if count > 0:
            print(f"[MIGRATION] Healed {count} saved framing links (connected to newly imported rigs).")
            db.flush()
    except Exception as e:
        db.rollback()
        print(f"[MIGRATION] Error healing saved framings: {e}")



def _migrate_saved_framings(db, user: DbUser, config: dict):
    framings = config.get("saved_framings", []) or []

    for f in framings:
        try:
            obj_name = f.get("object_name")
            if not obj_name: continue

            # Resolve rig_id from rig_name if possible
            rig_name_str = f.get("rig_name")
            rig_id = None
            if rig_name_str:
                rig = db.query(Rig).filter_by(user_id=user.id, rig_name=rig_name_str).one_or_none()
                if rig:
                    rig_id = rig.id

            # Upsert Logic
            existing = db.query(SavedFraming).filter_by(
                user_id=user.id,
                object_name=obj_name
            ).one_or_none()

            if existing:
                existing.rig_id = rig_id
                existing.rig_name = rig_name_str  # <-- Always save the name
                existing.ra = f.get("ra")
                existing.dec = f.get("dec")
                existing.rotation = f.get("rotation")
                existing.survey = f.get("survey")
                existing.blend_survey = f.get("blend_survey")
                existing.blend_opacity = f.get("blend_opacity")
                # Mosaic Data (legacy safe with .get() and defaults)
                existing.mosaic_cols = f.get("mosaic_cols", 1)
                existing.mosaic_rows = f.get("mosaic_rows", 1)
                existing.mosaic_overlap = f.get("mosaic_overlap", 10.0)
                # Image Adjustment Data (legacy safe with .get() and defaults)
                existing.img_brightness = f.get("img_brightness", 0.0)
                existing.img_contrast = f.get("img_contrast", 0.0)
                existing.img_gamma = f.get("img_gamma", 1.0)
                existing.img_saturation = f.get("img_saturation", 0.0)
                # Overlay Preferences (legacy safe with .get() and default)
                existing.geo_belt_enabled = f.get("geo_belt_enabled", True)
            else:
                new_sf = SavedFraming(
                    user_id=user.id,
                    object_name=obj_name,
                    rig_id=rig_id,
                    rig_name=rig_name_str,  # <-- Always save the name
                    ra=f.get("ra"),
                    dec=f.get("dec"),
                    rotation=f.get("rotation"),
                    survey=f.get("survey"),
                    blend_survey=f.get("blend_survey"),
                    blend_opacity=f.get("blend_opacity"),
                    # Mosaic Data (legacy safe with .get() and defaults)
                    mosaic_cols=f.get("mosaic_cols", 1),
                    mosaic_rows=f.get("mosaic_rows", 1),
                    mosaic_overlap=f.get("mosaic_overlap", 10.0),
                    # Image Adjustment Data (legacy safe with .get() and defaults)
                    img_brightness=f.get("img_brightness", 0.0),
                    img_contrast=f.get("img_contrast", 0.0),
                    img_gamma=f.get("img_gamma", 1.0),
                    img_saturation=f.get("img_saturation", 0.0),
                    # Overlay Preferences (legacy safe with .get() and default)
                    geo_belt_enabled=f.get("geo_belt_enabled", True)
                )
                db.add(new_sf)

        except Exception as e:
            db.rollback()
            print(f"[MIGRATION] Error migrating saved framing for {f.get('object_name')}: {e}")

    db.flush()



def _migrate_objects(db, user: DbUser, config: dict):
    """
    Idempotently migrates astronomical objects from a YAML configuration dictionary to the database.

    This function performs an "upsert" (update or insert) for each object based on its
    unique name for a given user. It prevents duplicates, handles various legacy key names,
    and automatically calculates the constellation if it's missing but coordinates are present.

    *** V2: Automatically rewrites '/uploads/...' image links in notes to point to
    *** the importing user's directory.
    """

    # === START: Link Rewriting Logic ===
    # Get the target username (e.g., 'default' or 'mrantonSG')
    target_username = user.username
    # This regex finds '/uploads/', captures the (old) username, and the rest of the path
    link_pattern = re.compile(r'(/uploads/)([^/]+)(/.*?["\'])')
    # This builds the replacement string, e.g., '/uploads/default/image.jpg"'
    replacement_str = r'\1' + re.escape(target_username) + r'\3'
    # === END: Link Rewriting Logic ===

    # Safely get the list of objects, defaulting to an empty list if missing.
    objs = (config or {}).get("objects", []) or []

    for o in objs:
        try:
            # --- 1. Robustly Parse Object Data from Dictionary ---
            # Use .get() with fallbacks to handle different key names found in older YAML files.
            ra_val = o.get("RA") if o.get("RA") is not None else o.get("RA (hours)")
            dec_val = o.get("DEC") if o.get("DEC") is not None else o.get("DEC (degrees)")

            # The canonical object identifier is crucial. Skip if it's missing or blank.
            raw_obj_name = o.get("Object") or o.get("object") or o.get("object_name")
            if not raw_obj_name or not str(raw_obj_name).strip():
                print(f"[MIGRATION][OBJECT SKIP] Entry is missing an 'Object' identifier: {o}")
                continue
            object_name = normalize_object_name(raw_obj_name)

            common_name = o.get("Common Name") or o.get("Name") or o.get("common_name")
            # If common_name is still blank, use the raw (pretty) object name as a fallback
            if not common_name or not str(common_name).strip():
                common_name = str(raw_obj_name).strip()

            obj_type = o.get("Type") or o.get("type")
            constellation = o.get("Constellation") or o.get("constellation")
            magnitude = o.get("Magnitude") if o.get("Magnitude") is not None else o.get("magnitude")
            size = o.get("Size") if o.get("Size") is not None else o.get("size")
            sb = o.get("SB") if o.get("SB") is not None else o.get("sb")
            active_project = bool(o.get("ActiveProject") or o.get("active_project") or False)

            # === START: Link Rewriting Application ===
            project_name = o.get("Project") or o.get("project_name")
            shared_notes = o.get("shared_notes")

            # Rewrite image links to point to the *importer's* directory
            if project_name:
                project_name = link_pattern.sub(replacement_str, project_name)
            if shared_notes:
                shared_notes = link_pattern.sub(replacement_str, shared_notes)
            # === END: Link Rewriting Application ===

            # Default to True for backward compatibility with old backups
            enabled = bool(o.get("enabled", True))
            is_shared = bool(o.get("is_shared", False))
            original_user_id = _as_int(o.get("original_user_id"))
            original_item_id = _as_int(o.get("original_item_id"))
            catalog_sources = o.get("catalog_sources")
            catalog_info = o.get("catalog_info")

            # --- Curation Fields (Backup/Restore Support) ---
            image_url = o.get("image_url")
            image_credit = o.get("image_credit")
            image_source_link = o.get("image_source_link")
            description_text = o.get("description_text")
            description_credit = o.get("description_credit")
            description_source_link = o.get("description_source_link")

            ra_f = float(ra_val) if ra_val is not None else None
            dec_f = float(dec_val) if dec_val is not None else None

            # --- 2. Enrich Data: Calculate Constellation if Missing ---
            # This integrates the logic from the old `backfill_missing_fields` function.
            if (not constellation) and (ra_f is not None) and (dec_f is not None):
                try:
                    # Create a coordinate object and use Astropy to find its constellation.
                    coords = SkyCoord(ra=ra_f * u.hourangle, dec=dec_f * u.deg)
                    constellation = get_constellation(coords)
                except Exception:
                    constellation = None  # Avoid crashing if coordinates are invalid.

            # --- 3. Perform the Idempotent "Upsert" ---
            # Query for an existing object with the normalized name.
            existing = db.query(AstroObject).filter_by(
                user_id=user.id,
                object_name=object_name
            ).one_or_none()
            if existing:
                # UPDATE PATH: The object already exists, so we update its fields.
                # This overwrites existing data with what's in the YAML, ensuring the
                # migration reflects the source of truth.
                existing.common_name = common_name
                existing.ra_hours = ra_f
                existing.dec_deg = dec_f
                existing.type = obj_type
                existing.constellation = constellation
                existing.magnitude = str(magnitude) if magnitude is not None else None
                existing.size = str(size) if size is not None else None
                existing.sb = str(sb) if sb is not None else None
                existing.active_project = active_project

                # --- START NEW ROBUST MERGE LOGIC ---
                existing_notes = existing.project_name or ""
                new_notes = project_name or ""  # Use the *fixed* project_name

                # Define what counts as "empty"
                is_existing_empty = not existing_notes or existing_notes.lower().strip() in ('none', '<div>none</div>',
                                                                                             'null')
                is_new_empty = not new_notes or new_notes.lower().strip() in ('none', '<div>none</div>', 'null')

                if is_new_empty:
                    # New notes are empty, so do nothing. Keep the existing notes.
                    pass
                elif is_existing_empty:
                    # Existing notes are empty, so just replace them with the new notes.
                    existing.project_name = new_notes
                elif new_notes not in existing_notes:
                    # Both have notes, and they are different. Append them.
                    existing.project_name = existing_notes + f"<br>---<br><em>(Merged)</em><br>{new_notes}"
                # --- END NEW ROBUST MERGE LOGIC ---

                existing.is_shared = is_shared
                existing.shared_notes = shared_notes  # Use the *fixed* shared_notes
                existing.original_user_id = original_user_id
                existing.original_item_id = original_item_id
                existing.catalog_sources = catalog_sources
                existing.catalog_info = catalog_info
                existing.enabled = enabled

                # Restore Curation
                existing.image_url = image_url
                existing.image_credit = image_credit
                existing.image_source_link = image_source_link
                existing.description_text = description_text
                existing.description_credit = description_credit
                existing.description_source_link = description_source_link
            else:
                # INSERT PATH: The object is new, so we create a new database record.
                new_object = AstroObject(
                    user_id=user.id,
                    object_name=object_name,
                    common_name=common_name,
                    ra_hours=ra_f,
                    dec_deg=dec_f,
                    type=obj_type,
                    constellation=constellation,
                    magnitude=str(magnitude) if magnitude is not None else None,
                    size=str(size) if size is not None else None,
                    sb=str(sb) if sb is not None else None,
                    active_project=active_project,
                    project_name=project_name,  # Use the *fixed* project_name
                    is_shared=is_shared,
                    shared_notes=shared_notes,  # Use the *fixed* shared_notes
                    original_user_id=original_user_id,
                    original_item_id=original_item_id,
                    catalog_sources=catalog_sources,
                    catalog_info=catalog_info,
                    enabled=enabled,
                    # Restore Curation
                    image_url=image_url,
                    image_credit=image_credit,
                    image_source_link=image_source_link,
                    description_text=description_text,
                    description_credit=description_credit,
                    description_source_link=description_source_link,
                )
                db.add(new_object)
                db.flush()

        except Exception as e:
            # If one object entry is malformed, log the error and continue with the rest.
            db.rollback()
            print(f"[MIGRATION] Could not process object entry '{o}'. Error: {e}")



def _try_float(v):
    try:
        return float(v) if v is not None else None
    except:
        return None


def _as_int(v):
    try:
        return int(str(v)) if v is not None else None
    except:
        return None


def _norm_name(s: str | None) -> str | None:
    """
    Normalize names for consistent lookups:
    - strip outer whitespace
    - collapse internal whitespace to single spaces
    - casefold for case-insensitive matching
    """
    if not s:
        return None
    s2 = " ".join(str(s).strip().split())
    return s2.casefold()



def _migrate_components_and_rigs(db, user: DbUser, rigs_yaml: dict, username: str):
    """
    Idempotent import for components and rigs that unifies all logic.
    - UPSERTS components by (user_id, kind, normalized_name), preventing duplicates.
    - Creates components on-the-fly if referenced by a rig but not explicitly defined.
    - UPSERTS rigs by (user_id, rig_name).
    - Skips creating rigs if a valid telescope or camera cannot be found/created.
    - Removes the need for post-migration deduplication or cleanup.
    """
    if not isinstance(rigs_yaml, dict):
        return

    comps = rigs_yaml.get("components", {}) or {}
    rig_list = rigs_yaml.get("rigs", []) or []

    # --- Internal Helper Functions ---

    def _coerce_float(x):
        try:
            return float(x) if x is not None else None
        except (ValueError, TypeError):
            return None

    # This helper function is already correct from our previous step.
    def _get_or_create_component(kind: str, name: str, **fields) -> Component | None:
        if not kind or not name:
            return None
        trimmed_name = " ".join(str(name).strip().split())
        existing_row = db.query(Component).filter(
            Component.user_id == user.id,
            Component.kind == kind,
            Component.name.collate('NOCASE') == trimmed_name
        ).one_or_none()

        # --- NEW: Get sharing fields from the 'fields' dict ---
        is_shared = bool(fields.get("is_shared", False))
        original_user_id = _as_int(fields.get("original_user_id"))
        original_item_id = _as_int(fields.get("original_item_id"))

        if existing_row:
            if existing_row.aperture_mm is None: existing_row.aperture_mm = _coerce_float(fields.get("aperture_mm"))
            if existing_row.focal_length_mm is None: existing_row.focal_length_mm = _coerce_float(
                fields.get("focal_length_mm"))
            if existing_row.sensor_width_mm is None: existing_row.sensor_width_mm = _coerce_float(
                fields.get("sensor_width_mm"))
            if existing_row.sensor_height_mm is None: existing_row.sensor_height_mm = _coerce_float(
                fields.get("sensor_height_mm"))
            if existing_row.pixel_size_um is None: existing_row.pixel_size_um = _coerce_float(
                fields.get("pixel_size_um"))
            if existing_row.factor is None: existing_row.factor = _coerce_float(fields.get("factor"))

            # We only set them if they're not already set, to avoid overwriting original import data
            if existing_row.is_shared is False:
                existing_row.is_shared = is_shared
            if existing_row.original_user_id is None:
                existing_row.original_user_id = original_user_id
            if existing_row.original_item_id is None:
                existing_row.original_item_id = original_item_id
            # --- END OF BLOCK ---

            db.flush()
            return existing_row

        new_row = Component(
            user_id=user.id, kind=kind, name=trimmed_name,
            aperture_mm=_coerce_float(fields.get("aperture_mm")),
            focal_length_mm=_coerce_float(fields.get("focal_length_mm")),
            sensor_width_mm=_coerce_float(fields.get("sensor_width_mm")),
            sensor_height_mm=_coerce_float(fields.get("sensor_height_mm")),
            pixel_size_um=_coerce_float(fields.get("pixel_size_um")),
            factor=_coerce_float(fields.get("factor")),
            is_shared=is_shared,
            original_user_id=original_user_id,
            original_item_id=original_item_id
        )
        db.add(new_row)
        db.flush()
        return new_row

    # Use a string-keyed dictionary for the legacy IDs.
    legacy_id_to_component_id: dict[tuple[str, str], int] = {}
    name_to_component_id: dict[tuple[str, str | None], int] = {}

    def _remember_component(row: Component | None, kind: str, name: str, legacy_id):
        if row is None or legacy_id is None: return
        legacy_id_to_component_id[(kind, str(legacy_id))] = row.id
        if name:
            name_to_component_id[(kind, _norm_name(name))] = row.id

    def _get_alias(d: dict, key: str, *aliases):
        if key in d and d.get(key) is not None: return d.get(key)
        for a in aliases:
            if a in d and d.get(a) is not None: return d.get(a)
        return None

    # --- 1. Process Components Section ---
    for t in comps.get("telescopes", []):
        row = _get_or_create_component("telescope", _get_alias(t, "name"), aperture_mm=_get_alias(t, "aperture_mm"),
                                       focal_length_mm=_get_alias(t, "focal_length_mm"),
                                       is_shared=t.get("is_shared"), original_user_id=t.get("original_user_id"),
                                       original_item_id=t.get("original_item_id")
                                       )
        _remember_component(row, "telescope", _get_alias(t, "name"), t.get("id"))
    for c in comps.get("cameras", []):
        row = _get_or_create_component("camera", _get_alias(c, "name"),
                                       sensor_width_mm=_get_alias(c, "sensor_width_mm"),
                                       sensor_height_mm=_get_alias(c, "sensor_height_mm"),
                                       pixel_size_um=_get_alias(c, "pixel_size_um"),
                                       is_shared=c.get("is_shared"), original_user_id=c.get("original_user_id"),
                                       original_item_id=c.get("original_item_id")
                                       )
        _remember_component(row, "camera", _get_alias(c, "name"), c.get("id"))
    for r in comps.get("reducers_extenders", []):
        row = _get_or_create_component("reducer_extender", _get_alias(r, "name"), factor=_get_alias(r, "factor"),
                                       is_shared=r.get("is_shared"), original_user_id=r.get("original_user_id"),
                                       original_item_id=r.get("original_item_id")
                                       )
        _remember_component(row, "reducer_extender", _get_alias(r, "name"), r.get("id"))

    def _resolve_component_id(kind: str, legacy_id, name) -> int | None:
        if legacy_id is not None:
            legacy_id_str = str(legacy_id)
            # --- START FIX: Look up the namespaced (kind, id) key ---
            if (kind, legacy_id_str) in legacy_id_to_component_id:
                return legacy_id_to_component_id[(kind, legacy_id_str)]
            # --- END FIX ---
            # (The old lookup for just legacy_id_str is removed)

        # This part for name-based lookup is still correct and needed
        if name:
            norm_key = (kind, _norm_name(name))
            if norm_key in name_to_component_id:
                return name_to_component_id[norm_key]
            row = _get_or_create_component(kind, str(name))
            if row:
                name_to_component_id[norm_key] = row.id
                return row.id
        return None


    # --- 2. Process Rigs Section ---
    for r in rig_list:
        try:
            rig_name = _get_alias(r, "rig_name", "name")
            if not rig_name: continue

            tel_name = _get_alias(r, "telescope", "telescope_name")
            cam_name = _get_alias(r, "camera", "camera_name")
            red_name = _get_alias(r, "reducer_extender", "reducer_extender_name")

            if (not tel_name or not cam_name) and isinstance(rig_name, str) and '+' in rig_name:
                parts = [p.strip() for p in rig_name.split('+')]
                if len(parts) >= 2:
                    tel_name = tel_name or parts[0]
                    cam_name = cam_name or parts[-1]
                    if len(parts) == 3:
                        red_name = red_name or parts[1]

            tel_id = _resolve_component_id("telescope", r.get("telescope_id"), tel_name)
            cam_id = _resolve_component_id("camera", r.get("camera_id"), cam_name)
            red_id = _resolve_component_id("reducer_extender", r.get("reducer_extender_id"), red_name)

            # Guide optics fields
            guide_tel_name = r.get("guide_telescope_name")
            guide_cam_name = r.get("guide_camera_name")
            guide_tel_id = _resolve_component_id("telescope", r.get("guide_telescope_id"), guide_tel_name)
            guide_cam_id = _resolve_component_id("camera", r.get("guide_camera_id"), guide_cam_name)
            guide_is_oag = bool(r.get("guide_is_oag", False))

            if not (tel_id and cam_id):
                print(
                    f"[MIGRATION][RIG SKIP] Rig '{rig_name}' for user '{username}' is missing a valid telescope or camera link. Skipping.")
                continue

            eff_fl, f_ratio, scale, fov_w = (_coerce_float(r.get(k)) for k in
                                             ["effective_focal_length", "f_ratio", "image_scale", "fov_w_arcmin"])
            if any(v is None for v in [eff_fl, f_ratio, scale, fov_w]):
                tel_obj, cam_obj = db.get(Component, tel_id), db.get(Component, cam_id)
                red_obj = db.get(Component, red_id) if red_id else None
                ce_fl, cf_ratio, c_scale, c_fovw = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)
                eff_fl, f_ratio, scale, fov_w = (ce_fl if eff_fl is None else eff_fl,
                                                 cf_ratio if f_ratio is None else f_ratio,
                                                 c_scale if scale is None else scale,
                                                 c_fovw if fov_w is None else fov_w)

            existing_rig = db.query(Rig).filter_by(user_id=user.id, rig_name=rig_name).one_or_none()
            if existing_rig:
                existing_rig.telescope_id, existing_rig.camera_id, existing_rig.reducer_extender_id = tel_id, cam_id, red_id
                existing_rig.effective_focal_length, existing_rig.f_ratio, existing_rig.image_scale, existing_rig.fov_w_arcmin = eff_fl, f_ratio, scale, fov_w
                existing_rig.guide_telescope_id, existing_rig.guide_camera_id, existing_rig.guide_is_oag = guide_tel_id, guide_cam_id, guide_is_oag
            else:
                db.add(Rig(user_id=user.id, rig_name=rig_name, telescope_id=tel_id, camera_id=cam_id,
                           reducer_extender_id=red_id, effective_focal_length=eff_fl, f_ratio=f_ratio,
                           image_scale=scale, fov_w_arcmin=fov_w, guide_telescope_id=guide_tel_id,
                           guide_camera_id=guide_cam_id, guide_is_oag=guide_is_oag))
            db.flush()

        except Exception as e:
            db.rollback()
            print(f"[MIGRATION] Skip/repair rig '{r}': {e}")

    _heal_saved_framings(db, user)



def _migrate_journal(db, user: DbUser, journal_yaml: dict):
    data = journal_yaml or {}
    # Normalize old list-based journals to the new dict structure
    if isinstance(data, list):
        data = {"projects": [], "sessions": data}
    else:
        # Ensure 'projects' and 'sessions' keys exist, handle legacy 'entries' key
        data.setdefault("projects", [])
        data.setdefault("sessions", data.get("entries", [])) # Use 'entries' as fallback

    # === START: Link Rewriting Logic ===
    # Get the target username (e.g., 'default' or 'mrantonSG')
    target_username = user.username
    # This regex finds '/uploads/', captures the (old) username, and the rest of the path
    link_pattern = re.compile(r'(/uploads/)([^/]+)(/.*?["\'])')
    # This builds the replacement string, e.g., '/uploads/default/image.jpg"'
    replacement_str = r'\1' + re.escape(target_username) + r'\3'
    # === END: Link Rewriting Logic ===

    # --- 1. Migrate Projects & Track Valid IDs ---
    valid_project_ids = set()

    for p in (data.get("projects") or []):
        # Check if both project_id and project_name are present and non-empty
        project_id_val = p.get("project_id")
        project_name_val = p.get("project_name")

        if project_id_val and str(project_id_val).strip():
            valid_project_ids.add(str(project_id_val)) # Track valid IDs from the import file

            # Check if project already exists by ID
            existing_project = db.query(Project).filter_by(id=str(project_id_val)).one_or_none()

            # --- NEW: Fields to set/update (Safely defaults to None if key missing) ---
            project_data = {
                "user_id": user.id,
                "name": str(project_name_val).strip() if project_name_val else "Unnamed Project",
                "target_object_name": p.get("target_object_id"),
                "description_notes": p.get("description_notes"),
                "framing_notes": p.get("framing_notes"),
                "processing_notes": p.get("processing_notes"),
                "final_image_file": p.get("final_image_file"),
                "goals": p.get("goals"),
                "status": p.get("status", "In Progress"),
            }

            if existing_project:
                # Update existing project
                for key, value in project_data.items():
                    if value is not None:
                        setattr(existing_project, key, value)
            else:
                # Check if a project with the same name already exists for the user (to avoid name duplicates if ID differs)
                existing_by_name = db.query(Project).filter_by(user_id=user.id, name=project_data["name"]).one_or_none()
                if not existing_by_name:
                    new_project = Project(id=str(project_id_val), **project_data)
                    db.add(new_project)

    db.flush()  # Flush after adding all valid projects from the YAML

    # --- 2. Migrate Sessions with ALL fields ---
    for s in (data.get("sessions") or []):
        # Get external ID, preferring 'session_id' then 'id'
        ext_id = s.get("session_id") or s.get("id")
        # Get date, preferring 'session_date' then 'date'
        date_str = s.get("session_date") or s.get("date")
        if not date_str: continue # Skip if no date

        # Try parsing date (ISO or YYYY-MM-DD)
        try:
            dt = datetime.fromisoformat(date_str).date()
        except:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                print(f"[MIGRATION][SESSION SKIP] Invalid date format '{date_str}' for session with external_id '{ext_id}'. Skipping.")
                continue # Skip if date parsing fails

        # === START: Link Rewriting Application ===
        # Get the raw HTML notes from the YAML
        notes_html = s.get("general_notes_problems_learnings") or s.get("notes")

        # Rewrite image links to point to the *importer's* directory
        if notes_html:
            notes_html = link_pattern.sub(replacement_str, notes_html)
        # === END: Link Rewriting Application ===

        # === START: Orphan Project Check ===
        sess_project_id = s.get("project_id")
        if sess_project_id:
            sess_project_id = str(sess_project_id)
            # If this ID wasn't in the YAML projects block...
            if sess_project_id not in valid_project_ids:
                # ...check if it exists in the DB (maybe from a previous import)
                exists_in_db = db.query(Project).filter_by(id=sess_project_id).first()

                if not exists_in_db:
                    # ORPHAN DETECTED: Auto-create a placeholder project to satisfy Foreign Key
                    print(f"[MIGRATION] Auto-creating missing project {sess_project_id} for session.")
                    placeholder_project = Project(
                        id=sess_project_id,
                        user_id=user.id,
                        name=s.get("project_name") or f"Legacy Project {sess_project_id[:8]}",
                        status="Completed" # Assume legacy projects are done
                    )
                    db.add(placeholder_project)
                    db.flush() # Commit immediately so the session insert works
                    valid_project_ids.add(sess_project_id)
        # === END: Orphan Project Check ===

        # Map all YAML keys to DB columns
        row_values = {
            "user_id": user.id,
            "project_id": sess_project_id, # Use the stringified/checked ID
            "date_utc": dt,
            "object_name": normalize_object_name(s.get("target_object_id") or s.get("object_name")),
            "notes": notes_html,  # <-- USE THE FIXED HTML
            "session_image_file": s.get("session_image_file"),
            "location_name": s.get("location_name"),
            "seeing_observed_fwhm": _try_float(s.get("seeing_observed_fwhm")),
            "sky_sqm_observed": _try_float(s.get("sky_sqm_observed")),
            "moon_illumination_session": _as_int(s.get("moon_illumination_session")),
            "moon_angular_separation_session": _try_float(s.get("moon_angular_separation_session")),
            "weather_notes": s.get("weather_notes"),
            "telescope_setup_notes": s.get("telescope_setup_notes"),
            "filter_used_session": s.get("filter_used_session"),
            "guiding_rms_avg_arcsec": _try_float(s.get("guiding_rms_avg_arcsec")),
            "guiding_equipment": s.get("guiding_equipment"),
            "dither_details": s.get("dither_details"),
            "dither_pixels": _as_int(s.get("dither_pixels")),  # None for old backups
            "dither_every_n": _as_int(s.get("dither_every_n")),  # None for old backups
            "dither_notes": s.get("dither_notes"),  # None for old backups
            "acquisition_software": s.get("acquisition_software"),
            "gain_setting": _as_int(s.get("gain_setting")),
            "offset_setting": _as_int(s.get("offset_setting")),
            "camera_temp_setpoint_c": _try_float(s.get("camera_temp_setpoint_c")),
            "camera_temp_actual_avg_c": _try_float(s.get("camera_temp_actual_avg_c")),
            "binning_session": s.get("binning_session"),
            "darks_strategy": s.get("darks_strategy"),
            "flats_strategy": s.get("flats_strategy"),
            "bias_darkflats_strategy": s.get("bias_darkflats_strategy"),
            "session_rating_subjective": _as_int(s.get("session_rating_subjective")),
            "transparency_observed_scale": s.get("transparency_observed_scale"),
            "number_of_subs_light": _as_int(s.get("number_of_subs_light")),
            "exposure_time_per_sub_sec": _as_int(s.get("exposure_time_per_sub_sec")),
            "filter_L_subs": _as_int(s.get("filter_L_subs")),
            "filter_L_exposure_sec": _as_int(s.get("filter_L_exposure_sec")),
            "filter_R_subs": _as_int(s.get("filter_R_subs")),
            "filter_R_exposure_sec": _as_int(s.get("filter_R_exposure_sec")),
            "filter_G_subs": _as_int(s.get("filter_G_subs")),
            "filter_G_exposure_sec": _as_int(s.get("filter_G_exposure_sec")),
            "filter_B_subs": _as_int(s.get("filter_B_subs")),
            "filter_B_exposure_sec": _as_int(s.get("filter_B_exposure_sec")),
            "filter_Ha_subs": _as_int(s.get("filter_Ha_subs")),
            "filter_Ha_exposure_sec": _as_int(s.get("filter_Ha_exposure_sec")),
            "filter_OIII_subs": _as_int(s.get("filter_OIII_subs")),
            "filter_OIII_exposure_sec": _as_int(s.get("filter_OIII_exposure_sec")),
            "filter_SII_subs": _as_int(s.get("filter_SII_subs")),
            "filter_SII_exposure_sec": _as_int(s.get("filter_SII_exposure_sec")),
            "rig_id_snapshot": _as_int(s.get("rig_id_snapshot")),
            "rig_name_snapshot": s.get("rig_name_snapshot"),
            "rig_efl_snapshot": _try_float(s.get("rig_efl_snapshot")),
            "rig_fr_snapshot": _try_float(s.get("rig_fr_snapshot")),
            "rig_scale_snapshot": _try_float(s.get("rig_scale_snapshot")),
            "rig_fov_w_snapshot": _try_float(s.get("rig_fov_w_snapshot")),
            "rig_fov_h_snapshot": _try_float(s.get("rig_fov_h_snapshot")),
            "telescope_name_snapshot": s.get("telescope_name_snapshot"),
            "reducer_name_snapshot": s.get("reducer_name_snapshot"),
            "camera_name_snapshot": s.get("camera_name_snapshot"),
            "calculated_integration_time_minutes": _try_float(s.get("calculated_integration_time_minutes")),
            # Ensure external_id is stored as string if it exists
            "external_id": str(ext_id) if ext_id else None,
            # Custom filter data (JSON string for user-defined filters)
            "custom_filter_data": s.get("custom_filter_data"),
            "asiair_log_content": s.get("asiair_log_content"),
            "phd2_log_content": s.get("phd2_log_content"),
            "log_analysis_cache": s.get("log_analysis_cache"),
        }
        # *** START: Simplified Upsert Logic ***
        if ext_id:
            # Try to find an existing session with this external_id for this user
            existing_session = db.query(JournalSession).filter_by(
                user_id=user.id,
                external_id=str(ext_id)
            ).one_or_none()

            if existing_session:
                # UPDATE: Session found, update its fields
                for k, v in row_values.items():
                    # Only update if the new value is not None
                    if v is not None:
                        setattr(existing_session, k, v)
                # No need to db.add() here
            else:
                # INSERT: Session not found, create a new one
                new_session = JournalSession(**row_values)
                db.add(new_session)
        else:
            # INSERT (No external ID provided): Always create a new session
            new_session = JournalSession(**row_values)
            db.add(new_session)

        # *** START: Legacy dither migration ***
        # If new structured fields are absent but old dither_details is present,
        # migrate the old text into dither_notes
        if row_values.get("dither_pixels") is None and row_values.get("dither_details"):
            # Get the session object (either existing_session or new_session)
            session_obj = existing_session if existing_session else new_session
            session_obj.dither_notes = row_values.get("dither_details")
        # *** END: Legacy dither migration ***
        # *** END: Simplified Upsert Logic ***

    # --- Import custom filter definitions ---
    for cf_def in data.get('custom_mono_filters', []):
        key = (cf_def.get('key') or '').strip()
        label = (cf_def.get('label') or '').strip()
        if not key or not label:
            continue
        if not db.query(UserCustomFilter).filter_by(user_id=user.id, filter_key=key).first():
            db.add(UserCustomFilter(user_id=user.id, filter_key=key, filter_label=label))
    db.flush()



def _migrate_ui_prefs(db, user: DbUser, config: dict):
    """
    Saves all general, user-specific settings from the config YAML
    into a single JSON blob in the ui_prefs table.
    """
    # Gather all the top-level settings we want to save
    settings_to_save = {
        "altitude_threshold": config.get("altitude_threshold"),
        "default_location": config.get("default_location"),
        "imaging_criteria": config.get("imaging_criteria"),
        "sampling_interval_minutes": config.get("sampling_interval_minutes"),
        "telemetry": config.get("telemetry"),
        "rig_sort": (config.get("ui") or {}).get("rig_sort")
    }

    # Only create a record if there's at least one setting to save
    if any(v is not None for v in settings_to_save.values()):
        # Upsert logic: find existing pref or create a new one
        existing_pref = db.query(UiPref).filter_by(user_id=user.id).one_or_none()

        blob = json.dumps(settings_to_save, ensure_ascii=False)

        if existing_pref:
            existing_pref.json_blob = blob
        else:
            new_pref = UiPref(user_id=user.id, json_blob=blob)
            db.add(new_pref)



def export_user_to_yaml(username: str, out_dir: str = None) -> bool:
    """
    Write three YAML files (config_*.yaml, rigs_default.yaml, journal_*.yaml) in out_dir.
    """
    db = get_db()
    out_dir = out_dir or CONFIG_DIR
    os.makedirs(out_dir, exist_ok=True)

    u = db.query(DbUser).filter_by(username=username).one_or_none()
    if not u:
        return False

    # CONFIG (locations + objects + defaults)
    locs = db.query(Location).options(selectinload(Location.horizon_points)).filter_by(user_id=u.id).all()
    default_loc = next((l.name for l in locs if l.is_default), None)
    saved_framings_db = db.query(SavedFraming).filter_by(user_id=u.id).all()
    saved_framings_list = []
    for sf in saved_framings_db:
        # Resolve rig name for portability (ID is local to DB)
        r_name = None
        if sf.rig_id:
            # We can query efficiently or just let it be lazy if N is small
            rig_obj = db.get(Rig, sf.rig_id)
            if rig_obj: r_name = rig_obj.rig_name

        saved_framings_list.append({
            "object_name": sf.object_name,
            "rig_name": r_name,
            "ra": sf.ra,
            "dec": sf.dec,
            "rotation": sf.rotation,
            "survey": sf.survey,
            "blend_survey": sf.blend_survey,
            "blend_opacity": sf.blend_opacity,
            # Mosaic Data
            "mosaic_cols": sf.mosaic_cols,
            "mosaic_rows": sf.mosaic_rows,
            "mosaic_overlap": sf.mosaic_overlap,
            # Image Adjustment Data
            "img_brightness": sf.img_brightness,
            "img_contrast": sf.img_contrast,
            "img_gamma": sf.img_gamma,
            "img_saturation": sf.img_saturation,
            # Overlay Preferences
            "geo_belt_enabled": sf.geo_belt_enabled
        })
    cfg = {
        "default_location": default_loc,
        "locations": {
            l.name: {
                **{
                    "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                    "altitude_threshold": l.altitude_threshold,
                    "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
                },
                **({"bortle_scale": l.bortle_scale} if l.bortle_scale is not None else {}),
                **({"comments": l.comments} if l.comments else {})
            } for l in locs
        },
        "objects": [
            o.to_dict() for o in db.query(AstroObject).filter_by(user_id=u.id).all()
        ],
        "saved_framings": saved_framings_list,
        "saved_views": [
            {
                "name": v.name,
                "description": v.description,
                "is_shared": v.is_shared,
                "settings": json.loads(v.settings_json)
            }
            for v in db.query(SavedView).filter_by(user_id=u.id).order_by(SavedView.name).all()
        ]
    }
    cfg_file = "config_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"config_{username}.yaml"
    _atomic_write_yaml(os.path.join(out_dir, cfg_file), cfg)

    # RIGS/COMPONENTS
    comps = db.query(Component).filter_by(user_id=u.id).all()
    rigs = db.query(Rig).filter_by(user_id=u.id).all()

    # Create a lookup map for component names by ID to ensure portable exports
    comp_map = {c.id: c.name for c in comps}

    def bykind(k):
        return [c for c in comps if c.kind == k]

    rigs_doc = {
        "components": {
            "telescopes": [
                {"id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm,
                 "is_shared": c.is_shared, "original_user_id": c.original_user_id,
                 "original_item_id": c.original_item_id}
                for c in bykind("telescope")
            ],
            "cameras": [
                {"id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                 "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um, "is_shared": c.is_shared,
                 "original_user_id": c.original_user_id, "original_item_id": c.original_item_id}
                for c in bykind("camera")
            ],
            "reducers_extenders": [
                {"id": c.id, "name": c.name, "factor": c.factor, "is_shared": c.is_shared,
                 "original_user_id": c.original_user_id, "original_item_id": c.original_item_id}
                for c in bykind("reducer_extender")
            ],
        },
        "rigs": [
            {
                "rig_name": r.rig_name,
                "telescope_id": r.telescope_id,
                "telescope_name": comp_map.get(r.telescope_id),  # Export name for portability
                "camera_id": r.camera_id,
                "camera_name": comp_map.get(r.camera_id),  # Export name for portability
                "reducer_extender_id": r.reducer_extender_id,
                "reducer_extender_name": comp_map.get(r.reducer_extender_id),  # Export name
                "effective_focal_length": r.effective_focal_length,
                "f_ratio": r.f_ratio,
                "image_scale": r.image_scale,
                "fov_w_arcmin": r.fov_w_arcmin,
                # Guide optics fields
                "guide_telescope_id": r.guide_telescope_id,
                "guide_telescope_name": comp_map.get(r.guide_telescope_id),
                "guide_camera_id": r.guide_camera_id,
                "guide_camera_name": comp_map.get(r.guide_camera_id),
                "guide_is_oag": r.guide_is_oag or False
            } for r in rigs
        ]
    }
    rig_file = "rigs_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"rigs_{username}.yaml"
    _atomic_write_yaml(os.path.join(out_dir, rig_file), rigs_doc)
    try:
        print(f"[EXPORT] Rigs for '{username}' written to {rig_file} (count={len(rigs)})")
    except Exception:
        pass

    # JOURNAL
    sessions = db.query(JournalSession).filter_by(user_id=u.id).order_by(JournalSession.date_utc.asc()).all()

    db_projects = db.query(Project).filter_by(user_id=u.id).all()
    projects_list = []
    # FIX: Build project lookup dict for session export (natural key resolution)
    project_lookup = {p.id: p.name for p in db_projects}
    
    for p in db_projects:
        projects_list.append({
            "project_id": p.id,  # Legacy: kept for backward compatibility
            "project_name": p.name,
            "target_object_id": p.target_object_name,
            "status": p.status,
            "goals": p.goals,
            "description_notes": p.description_notes,
            "framing_notes": p.framing_notes,
            "processing_notes": p.processing_notes,
            "final_image_file": p.final_image_file
        })

    # Custom filter definitions for this user
    custom_filters_db = db.query(UserCustomFilter).filter_by(user_id=u.id).order_by(UserCustomFilter.created_at).all()
    custom_filters_list = [
        {'key': cf.filter_key, 'label': cf.filter_label}
        for cf in custom_filters_db
    ]

    jdoc = {
        "projects": projects_list,
        "custom_mono_filters": custom_filters_list,
        "sessions": [
            {
                "date": s.date_utc.isoformat(),
                "object_name": s.object_name,
                "notes": s.notes,
                "session_id": s.external_id or s.id,
                "project_id": s.project_id,  # Legacy: kept for backward compatibility
                "project_name": project_lookup.get(s.project_id) if s.project_id else None,

                # Capture Details
                "number_of_subs_light": s.number_of_subs_light,
                "exposure_time_per_sub_sec": s.exposure_time_per_sub_sec,
                "filter_used_session": s.filter_used_session,
                "gain_setting": s.gain_setting,
                "offset_setting": s.offset_setting,
                "binning_session": s.binning_session,
                "camera_temp_setpoint_c": s.camera_temp_setpoint_c,
                "camera_temp_actual_avg_c": s.camera_temp_actual_avg_c,
                "calculated_integration_time_minutes": s.calculated_integration_time_minutes,

                # Environmental & Location
                "location_name": s.location_name,
                "seeing_observed_fwhm": s.seeing_observed_fwhm,
                "sky_sqm_observed": s.sky_sqm_observed,
                "transparency_observed_scale": s.transparency_observed_scale,
                "moon_illumination_session": s.moon_illumination_session,
                "moon_angular_separation_session": s.moon_angular_separation_session,
                "weather_notes": s.weather_notes,

                # Gear & Guiding
                "telescope_setup_notes": s.telescope_setup_notes,
                "guiding_rms_avg_arcsec": s.guiding_rms_avg_arcsec,
                "guiding_equipment": s.guiding_equipment,
                "dither_details": s.dither_details,
                "dither_pixels": s.dither_pixels,
                "dither_every_n": s.dither_every_n,
                "dither_notes": s.dither_notes,
                "dither_display": dither_display(s),
                "acquisition_software": s.acquisition_software,

                # Calibration Strategy
                "darks_strategy": s.darks_strategy,
                "flats_strategy": s.flats_strategy,
                "bias_darkflats_strategy": s.bias_darkflats_strategy,
                "session_rating_subjective": s.session_rating_subjective,

                # Mono Filters
                "filter_L_subs": s.filter_L_subs, "filter_L_exposure_sec": s.filter_L_exposure_sec,
                "filter_R_subs": s.filter_R_subs, "filter_R_exposure_sec": s.filter_R_exposure_sec,
                "filter_G_subs": s.filter_G_subs, "filter_G_exposure_sec": s.filter_G_exposure_sec,
                "filter_B_subs": s.filter_B_subs, "filter_B_exposure_sec": s.filter_B_exposure_sec,
                "filter_Ha_subs": s.filter_Ha_subs, "filter_Ha_exposure_sec": s.filter_Ha_exposure_sec,
                "filter_OIII_subs": s.filter_OIII_subs, "filter_OIII_exposure_sec": s.filter_OIII_exposure_sec,
                "filter_SII_subs": s.filter_SII_subs, "filter_SII_exposure_sec": s.filter_SII_exposure_sec,

                # Rig Snapshots
                "rig_id_snapshot": s.rig_id_snapshot,
                "rig_name_snapshot": s.rig_name_snapshot,
                "rig_efl_snapshot": s.rig_efl_snapshot,
                "rig_fr_snapshot": s.rig_fr_snapshot,
                "rig_scale_snapshot": s.rig_scale_snapshot,
                "rig_fov_w_snapshot": s.rig_fov_w_snapshot,
                "rig_fov_h_snapshot": s.rig_fov_h_snapshot,
                "telescope_name_snapshot": s.telescope_name_snapshot,
                "reducer_name_snapshot": s.reducer_name_snapshot,
                "camera_name_snapshot": s.camera_name_snapshot,

                # Custom filter data (JSON string for user-defined filters)
                "custom_filter_data": s.custom_filter_data,
            } for s in sessions
        ]
    }
    jfile = "journal_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"journal_{username}.yaml"
    _atomic_write_yaml(os.path.join(out_dir, jfile), jdoc)
    return True


def import_user_from_yaml(username: str,
                          config_path: str,
                          rigs_path: str,
                          journal_path: str,
                          clear_existing: bool = False) -> bool:
    """
    Upsert from YAML into DB. Optionally clears existing user data first.
    """
    db = get_db()
    try:
        user = _upsert_user(db, username)
        if clear_existing:
            # cascades remove all
            db.delete(user); db.flush()
            user = _upsert_user(db, username)

        cfg_tuple = _read_yaml(config_path);
        rigs_tuple = _read_yaml(rigs_path);
        jrn_tuple = _read_yaml(journal_path)

        # Extract the data dictionary (first element) from each tuple
        cfg_data = cfg_tuple[0]
        rigs_data = rigs_tuple[0]
        jrn_data = jrn_tuple[0]

        # Pass the extracted dictionaries to the migration functions
        _migrate_locations(db, user, cfg_data)
        _migrate_objects(db, user, cfg_data)
        _migrate_components_and_rigs(db, user, rigs_data, username)
        _migrate_saved_framings(db, user, cfg_data)
        _migrate_journal(db, user, jrn_data)
        _migrate_ui_prefs(db, user, cfg_data)
        db.commit()
        return True
    except Exception as import_err:
        db.rollback()
        current_app.logger.error(f"[YAML IMPORT] Failed to import config for user '{username}': {import_err}")
        traceback.print_exc()
        return False


def import_catalog_pack_for_user(db, user: DbUser, catalog_config: dict, pack_id: str) -> tuple[int, int, int]:
    """
    Import a catalog pack. Returns (created_count, enriched_count, skipped_count).
    Enrichment is NON-DESTRUCTIVE: it only fills missing (empty) fields.
    """
    created = 0
    enriched = 0
    skipped = 0

    objs = (catalog_config or {}).get("objects", []) or []

    def _merge_sources(current: str | None, new_id: str) -> str:
        if not new_id: return current or ""
        if not current: return new_id
        parts = {p.strip() for p in str(current).split(',') if p.strip()}
        parts.add(new_id)
        return ",".join(sorted(parts))

    for o in objs:
        try:
            # --- 1. Parse Common Data ---
            ra_val = o.get("RA") if o.get("RA") is not None else o.get("RA (hours)")
            dec_val = o.get("DEC") if o.get("DEC") is not None else o.get("DEC (degrees)")

            raw_obj_name = o.get("Object") or o.get("object") or o.get("object_name")
            if not raw_obj_name or not str(raw_obj_name).strip():
                skipped += 1
                continue

            object_name = normalize_object_name(raw_obj_name)

            # --- 2. Check for Existing Object ---
            existing = db.query(AstroObject).filter_by(
                user_id=user.id,
                object_name=object_name
            ).one_or_none()

            # --- 3. Extract Curation Data from Pack ---
            pack_img_url = o.get("image_url")
            pack_img_credit = o.get("image_credit")
            pack_img_link = o.get("image_source_link")
            pack_desc_text = o.get("description_text")
            pack_desc_credit = o.get("description_credit")
            pack_desc_link = o.get("description_source_link")

            if existing:
                # --- UPDATE LOGIC (Authoritative for Inspiration) ---
                was_enriched = False

                # Force update Inspiration fields if the pack provides them.
                # We assume the catalog is the master source for these fields,
                # while preserving user-specific data like Project Notes.
                if pack_img_url:
                    # Check if actually different to avoid unnecessary writes/counts
                    if existing.image_url != pack_img_url or existing.image_credit != pack_img_credit:
                        existing.image_url = pack_img_url
                        existing.image_credit = pack_img_credit
                        existing.image_source_link = pack_img_link
                        was_enriched = True

                if pack_desc_text:
                    if existing.description_text != pack_desc_text:
                        existing.description_text = pack_desc_text
                        existing.description_credit = pack_desc_credit
                        existing.description_source_link = pack_desc_link
                        was_enriched = True

                # Always update source tracking
                existing.catalog_sources = _merge_sources(existing.catalog_sources, pack_id)

                if was_enriched:
                    enriched += 1
                else:
                    skipped += 1
                continue

            # --- 4. Create New Object ---
            # (Only if RA/DEC exist)
            ra_f = float(ra_val) if ra_val is not None else None
            dec_f = float(dec_val) if dec_val is not None else None

            if (ra_f is None) or (dec_f is None):
                skipped += 1
                continue

            # Basic Fields
            common_name = o.get("Common Name") or o.get("Name") or o.get("common_name") or str(raw_obj_name).strip()
            obj_type = o.get("Type") or o.get("type")
            constellation = o.get("Constellation") or o.get("constellation")
            magnitude = str(o.get("Magnitude") if o.get("Magnitude") is not None else o.get("magnitude") or "")
            size = str(o.get("Size") if o.get("Size") is not None else o.get("size") or "")
            sb = str(o.get("SB") if o.get("SB") is not None else o.get("sb") or "")

            # Constellation Calc
            if (not constellation) and (ra_f is not None) and (dec_f is not None):
                try:
                    coords = SkyCoord(ra=ra_f * u.hourangle, dec=dec_f * u.deg)
                    constellation = get_constellation(coords)
                except Exception:
                    constellation = None

            new_object = AstroObject(
                user_id=user.id,
                object_name=object_name,
                common_name=common_name,
                ra_hours=ra_f,
                dec_deg=dec_f,
                type=obj_type,
                constellation=constellation,
                magnitude=magnitude if magnitude else None,
                size=size if size else None,
                sb=sb if sb else None,
                active_project=False,
                project_name=None,
                is_shared=False,
                shared_notes=None,
                original_user_id=None,
                original_item_id=None,
                catalog_sources=pack_id,
                catalog_info=o.get("catalog_info"),
                # Curation
                image_url=pack_img_url,
                image_credit=pack_img_credit,
                image_source_link=pack_img_link,
                description_text=pack_desc_text,
                description_credit=pack_desc_credit,
                description_source_link=pack_desc_link,
            )
            db.add(new_object)
            created += 1

        except Exception as e:
            print(f"[CATALOG IMPORT] Error processing '{o}': {e}")
            skipped += 1

    return (created, enriched, skipped)


def repair_journals(dry_run: bool = False):
    """
    Deduplicate journal_sessions and backfill missing object_name from YAML if possible.

    Dedupe key:
      (user_id, date_utc, object_name, notes, number_of_subs_light, exposure_time_per_sub_sec,
       filter_*_subs, filter_*_exposure_sec, calculated_integration_time_minutes)

    Keep the first row (lowest id) for each identical key; delete the rest.
    Then try to backfill object_name from the user's YAML by date if missing.
    """
    db = get_db()
    try:
        changes = []
        users = db.query(DbUser).all()
        for u in users:
            rows = db.query(JournalSession).filter_by(user_id=u.id).order_by(JournalSession.id.asc()).all()
            seen = {}
            to_delete = []

            def sig(r: JournalSession):
                # tuple signature for exact duplicates
                return (
                    r.date_utc.isoformat() if r.date_utc else "",
                    (r.object_name or "").strip(),
                    (r.notes or "").strip(),
                    r.number_of_subs_light,
                    r.exposure_time_per_sub_sec,
                    r.filter_L_subs, r.filter_L_exposure_sec,
                    r.filter_R_subs, r.filter_R_exposure_sec,
                    r.filter_G_subs, r.filter_G_exposure_sec,
                    r.filter_B_subs, r.filter_B_exposure_sec,
                    r.filter_Ha_subs, r.filter_Ha_exposure_sec,
                    r.filter_OIII_subs, r.filter_OIII_exposure_sec,
                    r.filter_SII_subs, r.filter_SII_exposure_sec,
                    r.calculated_integration_time_minutes,
                )

            for r in rows:
                key = sig(r)
                if key in seen:
                    to_delete.append(r)
                else:
                    seen[key] = r

            if to_delete:
                changes.append(f"[JOURNAL REPAIR] user={u.username} deleting {len(to_delete)} exact duplicates")
                if not dry_run:
                    for r in to_delete:
                        db.delete(r)

            # Backfill missing object_name where possible from YAML (by date)
            # YAML path: per user -> journal_<username>.yaml, single-user -> journal_default.yaml
            s_mode = SINGLE_USER_MODE
            jfile = os.path.join(CONFIG_DIR, "journal_default.yaml" if (s_mode and u.username == "default") else f"journal_{u.username}.yaml")
            by_date = {}
            if os.path.exists(jfile):
                try:
                    y = _read_yaml(jfile)
                    if isinstance(y, dict):
                        for s in (y.get("sessions") or []):
                            # find a name variant
                            name = None
                            for k in ("object_name", "Object", "object", "target", "Name", "name"):
                                v = s.get(k)
                                if isinstance(v, str) and v.strip():
                                    name = v.strip(); break
                            d = s.get("date")
                            if isinstance(d, str) and name:
                                by_date.setdefault(d, []).append(name)
                except Exception as e:
                    print(f"[JOURNAL REPAIR] WARN cannot read YAML for '{u.username}': {e}")

            filled = 0
            if by_date:
                for r in db.query(JournalSession).filter_by(user_id=u.id).all():
                    if not r.object_name and r.date_utc:
                        names = by_date.get(r.date_utc.isoformat())
                        if names:
                            # pick the first available name for that date
                            r.object_name = names[0]
                            filled += 1
                if filled:
                    changes.append(f"[JOURNAL REPAIR] user={u.username} backfilled object_name for {filled} sessions")

        if dry_run:
            for line in changes:
                print(line)
            print("[JOURNAL REPAIR] Dry-run complete; no DB changes.")
            db.rollback()
        else:
            db.commit()
            for line in changes:
                print(line)
            print("[JOURNAL REPAIR] Commit complete.")
    except Exception as e:
        db.rollback()
        print(f"[JOURNAL REPAIR] ERROR: {e}")


def validate_journal_data(journal_data):
    """
    Basic validation for imported journal data.
    Returns True if valid, False otherwise.
    Can be expanded for more detailed schema validation later.
    """
    if not isinstance(journal_data, dict):
        return False, "Uploaded journal is not a valid dictionary structure."
    if "sessions" not in journal_data:
        return False, "Uploaded journal is missing the top-level 'sessions' key."
    if not isinstance(journal_data["sessions"], list):
        return False, "The 'sessions' key in the uploaded journal must be a list."

    # Optional: Check if each session has a session_id (basic check)
    for i, session in enumerate(journal_data["sessions"]):
        if not isinstance(session, dict):
            return False, f"Session entry at index {i} is not a valid dictionary."
        if "session_id" not in session or not session["session_id"]:
            return False, f"Session entry at index {i} is missing a 'session_id'."
        # Add more checks per session if desired (e.g., session_date format)
    return True, "Journal data seems structurally valid."




def _migrate_saved_views(db, user: DbUser, config: dict):
    """
    Idempotent import of saved views. Deletes all existing views and replaces them.
    Now includes description and sharing status.
    """
    # 1. Delete all existing views for this user
    db.query(SavedView).filter_by(user_id=user.id).delete()
    db.flush()

    # 2. Add new views from the config
    views_list = (config or {}).get("saved_views", []) or []
    if not isinstance(views_list, list):
        print("[MIGRATION] 'saved_views' is not a list, skipping.")
        return

    for view_entry in views_list:
        try:
            name = view_entry.get("name")
            settings = view_entry.get("settings")

            # --- New Fields ---
            description = view_entry.get("description")
            is_shared = bool(view_entry.get("is_shared", False))

            if not name or not settings:
                print(f"[MIGRATION] Skipping invalid saved view (missing name or settings): {view_entry}")
                continue

            # Ensure settings are stored as a JSON string
            settings_str = json.dumps(settings)

            new_view = SavedView(
                user_id=user.id,
                name=name,
                description=description,  # <-- Added
                is_shared=is_shared,  # <-- Added
                settings_json=settings_str
            )
            db.add(new_view)
        except Exception as e:
            db.rollback()
            print(f"[MIGRATION] Could not process saved view '{view_entry.get('name')}'. Error: {e}")

    db.flush()


