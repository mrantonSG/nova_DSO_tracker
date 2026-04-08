import json
import os
import time
import calendar
import traceback
import threading
import re
import requests
import numpy as np
from datetime import datetime, timedelta, UTC, timezone

from flask import (
    Blueprint, request, jsonify, g, url_for,
    current_app, send_from_directory
)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import selectinload

from astropy.coordinates import EarthLocation, SkyCoord, AltAz, get_body, search_around_sky, get_constellation
from astropy import units as u
from astropy.time import Time
import ephem
import pytz

from nova.config import (
    SINGLE_USER_MODE, TELEMETRY_DEBUG_STATE, LATEST_VERSION_INFO,
    nightly_curves_cache, observable_objects_cache,
    weather_cache, CACHE_DIR,
)
from nova.helpers import (
    get_db, load_full_astro_context, get_locale,
    get_all_mobile_up_now_data, get_ra_dec, safe_float,
    read_log_content, enable_user, disable_user, delete_user,
)
from nova.models import (
    DbUser, AstroObject, JournalSession, Project,
    Component, SavedView, SavedFraming, Rig, Location, UiPref
)
from nova.auth import db as auth_db, User
from nova.analytics import record_event
from modules.astro_calculations import (
    calculate_sun_events_cached,
    calculate_observable_duration_vectorized,
    calculate_transit_time,
    ra_dec_to_alt_az,
    get_utc_time_for_local_11pm,
    interpolate_horizon,
    get_common_time_arrays,
)
from nova import log_parser as nova_log_parser
import modules.nova_data_fetcher as nova_data_fetcher
import markdown

api_bp = Blueprint('api', __name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- Telemetry diagnostics route ---
@api_bp.route('/telemetry/debug', methods=['GET'])
def telemetry_debug():
    # Report current telemetry config and last attempt
    try:
        username = "default" if SINGLE_USER_MODE else (current_user.username if current_user.is_authenticated else "guest_user")
    except Exception:
        username = "default"
    try:
        cfg = g.user_config if hasattr(g, 'user_config') else {}
    except Exception:
        cfg = {}
    enabled = bool(cfg.get('telemetry', {}).get('enabled', True))
    return jsonify({
        'enabled': enabled,
        'endpoint': TELEMETRY_DEBUG_STATE.get('endpoint'),
        'last_payload': TELEMETRY_DEBUG_STATE.get('last_payload'),
        'last_result': TELEMETRY_DEBUG_STATE.get('last_result'),
        'last_error': TELEMETRY_DEBUG_STATE.get('last_error'),
        'last_ts': TELEMETRY_DEBUG_STATE.get('last_ts')
    })


@api_bp.route('/api/latest_version')
def get_latest_version():
    """An API endpoint for the frontend to check for updates."""
    return jsonify(LATEST_VERSION_INFO)


@api_bp.route('/api/update_object', methods=['POST'])
@login_required
def update_object():
    """
    API endpoint to update a single AstroObject from the config form.
    Expects a JSON payload with all object fields.
    """
    db = get_db()
    try:
        data = request.get_json()
        object_name = data.get('object_id')
        username = "default" if SINGLE_USER_MODE else current_user.username

        user = db.query(DbUser).filter_by(username=username).one()
        obj = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if not obj:
            return jsonify({"status": "error", "message": _("Object not found")}), 404

        # Update all fields from the payload
        obj.common_name = data.get('name')
        obj.ra_hours = float(data.get('ra'))
        obj.dec_deg = float(data.get('dec'))
        obj.constellation = data.get('constellation')
        obj.type = data.get('type')
        obj.magnitude = data.get('magnitude')
        obj.size = data.get('size')
        obj.sb = data.get('sb')
        obj.active_project = data.get('is_active')
        # Update notes (JS sends the raw HTML from Trix)
        obj.project_name = data.get('project_notes')

        # --- Curation Fields ---
        obj.image_url = data.get('image_url')
        obj.image_credit = data.get('image_credit')
        obj.image_source_link = data.get('image_source_link')
        obj.description_text = data.get('description_text')
        obj.description_credit = data.get('description_credit')
        obj.description_source_link = data.get('description_source_link')
        # -----------------------

        if not SINGLE_USER_MODE:
            # Only update sharing if it's not an imported item
            if not obj.original_user_id:
                obj.is_shared = data.get('is_shared')
                obj.shared_notes = data.get('shared_notes')

        db.commit()
        return jsonify({"status": "success", "message": f"Object '{object_name}' updated."})

    except Exception as e:
        db.rollback()
        print(f"--- ERROR in /api/update_object ---")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500




@api_bp.route('/api/get_object_list')
def get_object_list():
    load_full_astro_context()
    """
    A new, very fast endpoint that just returns the list of object names.
    """
    # Filter g.objects_list to return only enabled object names
    enabled_names = [o['Object'] for o in g.objects_list if o.get('enabled', True)]
    return jsonify({"objects": enabled_names})


@api_bp.route('/api/journal/objects')
@login_required
def get_journal_objects():
    """
    Returns a list of all objects that have journal sessions with any imaging data,
    sorted by most recent session date. Used by the journal object switcher.
    Includes first_session_date, first_session_id, and first_session_location for navigation.
    """
    db = get_db()
    user_id = g.db_user.id

    # Query all sessions with any imaging data (light subs > 0 OR calculated integration > 0)
    # This captures sessions with mono filter data that don't use number_of_subs_light
    sessions_with_data = db.query(
        JournalSession.id,
        JournalSession.object_name,
        JournalSession.date_utc,
        JournalSession.calculated_integration_time_minutes,
        JournalSession.location_name,
        AstroObject.common_name,
        AstroObject.id.label('astro_id')
    ).outerjoin(
        AstroObject,
        and_(AstroObject.user_id == user_id, AstroObject.object_name == JournalSession.object_name)
    ).filter(
        JournalSession.user_id == user_id,
        or_(
            JournalSession.number_of_subs_light > 0,
            JournalSession.calculated_integration_time_minutes > 0
        )
    ).order_by(
        JournalSession.object_name,
        JournalSession.date_utc.desc()
    ).all()

    # Aggregate by object_name
    objects_map = {}
    for session in sessions_with_data:
        object_name = session.object_name
        if not object_name:
            continue

        if object_name not in objects_map:
            objects_map[object_name] = {
                'id': session.astro_id,
                'name': session.common_name or object_name,
                'catalog_id': object_name,
                'total_minutes': 0,
                'last_session': None,
                'first_session_date': None,
                'first_session_id': None,
                'first_session_location': None
            }

        # Accumulate integration time
        if session.calculated_integration_time_minutes:
            objects_map[object_name]['total_minutes'] += session.calculated_integration_time_minutes

        # Track most recent session date (for sorting)
        if session.date_utc:
            if objects_map[object_name]['last_session'] is None or session.date_utc > objects_map[object_name]['last_session']:
                objects_map[object_name]['last_session'] = session.date_utc

        # Track first (oldest) session date, id, and location (for navigation)
        if session.date_utc:
            if objects_map[object_name]['first_session_date'] is None or session.date_utc < objects_map[object_name]['first_session_date']:
                objects_map[object_name]['first_session_date'] = session.date_utc
                objects_map[object_name]['first_session_id'] = session.id
                objects_map[object_name]['first_session_location'] = session.location_name

    # Convert to list and sort by last_session DESC
    result = []
    for obj in objects_map.values():
        total_hours = round(obj['total_minutes'] / 60.0, 1) if obj['total_minutes'] else 0.0
        first_session_id = obj['first_session_id']
        first_session_location = obj['first_session_location']

        # Only include location if it's a non-empty string
        first_session_location = first_session_location.strip() if first_session_location else None

        # Build URL with location parameter if available
        url_params = {'object_name': obj['catalog_id'], 'tab': 'journal'}
        if first_session_id:
            url_params['session_id'] = first_session_id
        if first_session_location:
            url_params['location'] = first_session_location

        result.append({
            'id': obj['id'],
            'name': obj['name'],
            'catalog_id': obj['catalog_id'],
            'total_hours': total_hours,
            'last_session': obj['last_session'].strftime('%Y-%m-%d') if obj['last_session'] else None,
            'first_session_date': obj['first_session_date'].strftime('%Y-%m-%d') if obj['first_session_date'] else None,
            'first_session_location': first_session_location,
            'url': url_for('core.graph_dashboard', **url_params, _external=False)
        })

    # Sort by last_session descending (most recent first)
    result.sort(key=lambda x: x['last_session'] or '0000-00-00', reverse=True)

    return jsonify(result)


@api_bp.route('/api/bulk_update_objects', methods=['POST'])
@login_required
def bulk_update_objects():
    data = request.get_json()
    action = data.get('action')  # 'enable', 'disable', 'delete'
    object_ids = data.get('object_ids', [])

    if not action or not object_ids:
        return jsonify({"status": "error", "message": "Missing action or object_ids"}), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        query = db.query(AstroObject).filter(
            AstroObject.user_id == user_id,
            AstroObject.object_name.in_(object_ids)
        )

        if action == 'delete':
            # Check for dependencies (Journals or Projects) before deleting
            safe_to_delete = []
            skipped_count = 0

            # We need to iterate to check relationships since bulk delete bypasses Python-level checks
            objects_to_check = query.all()

            for obj in objects_to_check:
                # Check for journal sessions using this object name
                has_journals = db.query(JournalSession).filter_by(
                    user_id=user_id, object_name=obj.object_name
                ).first()

                # Check for projects targeting this object
                has_projects = db.query(Project).filter_by(
                    user_id=user_id, target_object_name=obj.object_name
                ).first()

                if has_journals or has_projects:
                    skipped_count += 1
                else:
                    safe_to_delete.append(obj.object_name)

            if safe_to_delete:
                # Perform the delete only on safe IDs
                delete_q = db.query(AstroObject).filter(
                    AstroObject.user_id == user_id,
                    AstroObject.object_name.in_(safe_to_delete)
                )
                count = delete_q.delete(synchronize_session=False)
            else:
                count = 0

            msg = f"Deleted {count} objects."
            if skipped_count > 0:
                msg += f" (Skipped {skipped_count} objects used in Journals/Projects)"
        elif action == 'enable':
            count = query.update({AstroObject.enabled: True}, synchronize_session=False)
            msg = f"Enabled {count} objects."
        elif action == 'disable':
            count = query.update({AstroObject.enabled: False}, synchronize_session=False)
            msg = f"Disabled {count} objects."
        else:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        db.commit()
        return jsonify({"status": "success", "message": msg})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/help/img/<path:filename>')
def get_help_image(filename):
    """Serves images located in the help_docs directory."""
    return send_from_directory(os.path.join(PROJECT_ROOT, 'help_docs'), filename)

@api_bp.route('/api/help/<topic_id>')
def get_help_content(topic_id):
    """
    Reads a markdown file from help_docs/{locale}/, converts it to HTML, and returns it.
    Falls back to English if the localized file doesn't exist.
    """
    # 1. Sanitize input to prevent directory traversal
    safe_topic = "".join([c for c in topic_id if c.isalnum() or c in "_-"])

    # 2. Determine locale and build file path with fallback
    locale = get_locale()
    lang = str(locale).split('_')[0] if locale else 'en'
    file_path = os.path.join(PROJECT_ROOT, 'help_docs', lang, f'{safe_topic}.md')

    # 3. Fallback to English if localized file doesn't exist
    if not os.path.exists(file_path):
        file_path = os.path.join(PROJECT_ROOT, 'help_docs', 'en', f'{safe_topic}.md')

    # 4. Check if file exists (even after fallback)
    if not os.path.exists(file_path):
        return jsonify({
            "error": True,
            "html": f"<h3>Topic Not Found</h3><p>No help file found for ID: <code>{safe_topic}</code></p>"
        }), 404

    # 5. Read and convert
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
            # Extensions: 'fenced_code' adds support for ```code blocks```
            html_content = markdown.markdown(text, extensions=['fenced_code', 'tables'])
            return jsonify({"status": "success", "html": html_content})
    except Exception as e:
        return jsonify({"error": True, "html": _("<p>Error reading help file: %(error)s</p>", error=str(e))}), 500


@api_bp.route('/api/get_saved_views')
@login_required
def get_saved_views():
    db = get_db()
    try:
        views = db.query(SavedView).filter_by(user_id=g.db_user.id).order_by(SavedView.name).all()
        views_dict = {
            v.name: {
                "id": v.id,
                "name": v.name,
                "settings": json.loads(v.settings_json)
            } for v in views
        }
        return jsonify(views_dict)
    except Exception as e:
        print(f"Error fetching saved views: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route('/api/save_saved_view', methods=['POST'])
@login_required
def save_saved_view():
    db = get_db()
    try:
        data = request.get_json()
        view_name = data.get('name')
        settings = data.get('settings')

        # --- NEW: Capture description and sharing status ---
        description = data.get('description', '')
        is_shared = bool(data.get('is_shared', False))

        if not view_name or not settings:
            return jsonify({"status": "error", "message": "Missing view name or settings."}), 400

        settings_str = json.dumps(settings)

        # Check for existing view by name (upsert logic)
        existing_view = db.query(SavedView).filter_by(user_id=g.db_user.id, name=view_name).one_or_none()

        if existing_view:
            existing_view.settings_json = settings_str

            # Only update description/sharing if it is NOT an imported view
            # (Imported views preserve their original metadata)
            if not existing_view.original_user_id:
                existing_view.description = description
                existing_view.is_shared = is_shared

            message = "View updated"
        else:
            new_view = SavedView(
                user_id=g.db_user.id,
                name=view_name,
                description=description,  # <-- New field
                settings_json=settings_str,
                is_shared=is_shared  # <-- New field
            )
            db.add(new_view)
            message = "View saved"

        db.commit()
        return jsonify({"status": "success", "message": message})

    except Exception as e:
        db.rollback()
        print(f"Error saving view: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/delete_saved_view', methods=['POST'])
@login_required
def delete_saved_view():
    db = get_db()
    try:
        data = request.get_json()
        view_name = data.get('name')
        if not view_name:
            return jsonify({"status": "error", "message": "Missing view name."}), 400

        view_to_delete = db.query(SavedView).filter_by(user_id=g.db_user.id, name=view_name).one_or_none()

        if view_to_delete:
            db.delete(view_to_delete)
            db.commit()
            return jsonify({"status": "success", "message": "View deleted."})
        else:
            return jsonify({"status": "error", "message": "View not found."}), 404
    except Exception as e:
        db.rollback()
        print(f"Error deleting view: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/get_shared_items')
@login_required
def get_shared_items():
    if SINGLE_USER_MODE:
        return jsonify({"objects": [], "components": [], "views": [], "imported_object_ids": [], "imported_component_ids": [], "imported_view_ids": []})

    db = get_db()
    try:
        current_user_id = g.db_user.id

        # --- 1. Get ALL Shared Objects (Including own) ---
        shared_objects_db = db.query(AstroObject, DbUser.username).join(
            DbUser, AstroObject.user_id == DbUser.id
        ).filter(
            AstroObject.is_shared == True
            # Removed: AstroObject.user_id != current_user_id
        ).all()

        shared_objects_list = []
        for obj, username in shared_objects_db:
            shared_objects_list.append({
                "id": obj.id,
                "object_name": obj.object_name,
                "common_name": obj.common_name,
                "type": obj.type,
                "constellation": obj.constellation,
                "ra": obj.ra_hours,
                "dec": obj.dec_deg,
                "shared_by_user": username,
                "shared_notes": obj.shared_notes or "",
                # --- Inspiration Metadata ---
                "image_url": obj.image_url,
                "image_credit": obj.image_credit,
                "image_source_link": obj.image_source_link,
                "description_text": obj.description_text,
                "description_credit": obj.description_credit,
                "description_source_link": obj.description_source_link
            })

        # --- 2. Get ALL Shared Components (Including own) ---
        shared_components_db = db.query(Component, DbUser.username).join(
            DbUser, Component.user_id == DbUser.id
        ).filter(
            Component.is_shared == True
            # Removed: Component.user_id != current_user_id
        ).all()

        shared_components_list = []
        for comp, username in shared_components_db:
            shared_components_list.append({
                "id": comp.id,
                "name": comp.name,
                "kind": comp.kind,
                "shared_by_user": username
            })

        # --- 3. Get ALL Shared Views (Including own) ---
        shared_views_db = db.query(SavedView, DbUser.username).join(
            DbUser, SavedView.user_id == DbUser.id
        ).filter(
            SavedView.is_shared == True
            # Removed: SavedView.user_id != current_user_id
        ).all()

        shared_views_list = []
        for view, username in shared_views_db:
            shared_views_list.append({
                "id": view.id,
                "name": view.name,
                "description": view.description,
                "shared_by_user": username
            })

        # --- 4. Get IDs of items ALREADY imported by CURRENT user ---
        imported_objects = db.query(AstroObject.original_item_id).filter(
            AstroObject.user_id == current_user_id,
            AstroObject.original_item_id != None
        ).all()
        imported_object_ids = {item_id for (item_id,) in imported_objects}

        imported_components = db.query(Component.original_item_id).filter(
            Component.user_id == current_user_id,
            Component.original_item_id != None
        ).all()
        imported_component_ids = {item_id for (item_id,) in imported_components}

        imported_views = db.query(SavedView.original_item_id).filter(
            SavedView.user_id == current_user_id,
            SavedView.original_item_id != None
        ).all()
        imported_view_ids = {item_id for (item_id,) in imported_views}

        return jsonify({
            "objects": shared_objects_list,
            "components": shared_components_list,
            "views": shared_views_list,
            "imported_object_ids": list(imported_object_ids),
            "imported_component_ids": list(imported_component_ids),
            "imported_view_ids": list(imported_view_ids)
        })
    except Exception as e:
        print(f"ERROR in get_shared_items: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@api_bp.route('/api/import_item', methods=['POST'])
@login_required
def import_item():
    if SINGLE_USER_MODE:
        return jsonify({"status": "error", "message": "Sharing is disabled in single-user mode"}), 400

    db = get_db()
    try:
        data = request.get_json()
        item_id = data.get('id')
        item_type = data.get('type')  # 'object' or 'component'

        if not item_id or not item_type:
            return jsonify({"status": "error", "message": "Missing item ID or type"}), 400

        current_user_id = g.db_user.id

        if item_type == 'object':
            # --- Import an Object ---
            original_obj = db.query(AstroObject).filter_by(id=item_id, is_shared=True).one_or_none()
            if not original_obj or original_obj.user_id == current_user_id:
                return jsonify({"status": "error", "message": "Object not found or cannot import your own item"}), 404

            existing = db.query(AstroObject).filter_by(user_id=current_user_id,
                                                       object_name=original_obj.object_name).one_or_none()
            if existing:
                return jsonify({"status": "error",
                                "message": f"You already have an object named '{original_obj.object_name}'"}), 409

            new_obj = AstroObject(
                user_id=current_user_id,
                object_name=original_obj.object_name,
                common_name=original_obj.common_name,
                ra_hours=original_obj.ra_hours,
                dec_deg=original_obj.dec_deg,
                type=original_obj.type,
                constellation=original_obj.constellation,
                magnitude=original_obj.magnitude,
                size=original_obj.size,
                sb=original_obj.sb,
                shared_notes=original_obj.shared_notes,
                original_user_id=original_obj.user_id,
                original_item_id=original_obj.id,  # <-- THE FIX
                is_shared=False,
                project_name="",
                # --- Inspiration Fields Transfer ---
                image_url=original_obj.image_url,
                image_credit=original_obj.image_credit,
                image_source_link=original_obj.image_source_link,
                description_text=original_obj.description_text,
                description_credit=original_obj.description_credit,
                description_source_link=original_obj.description_source_link
            )
            db.add(new_obj)

        elif item_type == 'component':
            # --- Import a Component ---
            original_comp = db.query(Component).filter_by(id=item_id, is_shared=True).one_or_none()
            if not original_comp or original_comp.user_id == current_user_id:
                return jsonify(
                    {"status": "error", "message": "Component not found or cannot import your own item"}), 404

            existing = db.query(Component).filter_by(user_id=current_user_id, kind=original_comp.kind,
                                                     name=original_comp.name).one_or_none()
            if existing:
                return jsonify({"status": "error",
                                "message": f"You already have a {original_comp.kind} named '{original_comp.name}'"}), 409

            new_comp = Component(
                user_id=current_user_id,
                kind=original_comp.kind,
                name=original_comp.name,
                aperture_mm=original_comp.aperture_mm,
                focal_length_mm=original_comp.focal_length_mm,
                sensor_width_mm=original_comp.sensor_width_mm,
                sensor_height_mm=original_comp.sensor_height_mm,
                pixel_size_um=original_comp.pixel_size_um,
                factor=original_comp.factor,
                is_shared=False,
                original_user_id=original_comp.user_id,
                original_item_id=original_comp.id  # <-- THE FIX
            )
            db.add(new_comp)

        elif item_type == 'view':
            # --- Import a View ---
            original_view = db.query(SavedView).filter_by(id=item_id, is_shared=True).one_or_none()
            if not original_view:
                return jsonify({"status": "error", "message": "View not found"}), 404

            existing = db.query(SavedView).filter_by(user_id=current_user_id, name=original_view.name).one_or_none()
            if existing:
                return jsonify(
                    {"status": "error", "message": f"You already have a view named '{original_view.name}'"}), 409

            new_view = SavedView(
                user_id=current_user_id,
                name=original_view.name,
                description=original_view.description,
                settings_json=original_view.settings_json,
                is_shared=False,
                original_user_id=original_view.user_id,
                original_item_id=original_view.id
            )
            db.add(new_view)

        else:
            return jsonify({"status": "error", "message": "Invalid item type"}), 400

        db.commit()
        return jsonify({"status": "success", "message": f"{item_type.capitalize()} imported successfully!"})

    except Exception as e:
        db.rollback()
        print(f"ERROR in import_item: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": "An internal error occurred."}), 500


@api_bp.route('/api/save_framing', methods=['POST'])
@login_required
def save_framing():
    db = get_db()
    try:
        data = request.get_json()
        object_name = data.get('object_name')

        # Find existing framing or create new
        framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id,
            object_name=object_name
        ).one_or_none()

        if not framing:
            framing = SavedFraming(user_id=g.db_user.id, object_name=object_name)
            db.add(framing)

        # Update fields
        rig_id_val = int(data.get('rig')) if data.get('rig') else None

        # Lookup rig_name so we save it for portability (Backup/Restore)
        rig_name_val = None
        if rig_id_val:
            r = db.get(Rig, rig_id_val)
            if r:
                rig_name_val = r.rig_name

        framing.rig_id = rig_id_val
        framing.rig_name = rig_name_val  # <-- Important: Saves name for portability

        framing.ra = float(data['ra']) if data.get('ra') is not None else None
        framing.dec = float(data['dec']) if data.get('dec') is not None else None
        framing.rotation = float(data['rotation']) if data.get('rotation') is not None else 0.0
        framing.survey = data.get('survey')
        framing.blend_survey = data.get('blend')
        framing.blend_opacity = float(data['blend_op']) if data.get('blend_op') is not None else 0.0

        # Mosaic fields
        framing.mosaic_cols = int(data['mosaic_cols']) if data.get('mosaic_cols') is not None else 1
        framing.mosaic_rows = int(data['mosaic_rows']) if data.get('mosaic_rows') is not None else 1
        framing.mosaic_overlap = float(data['mosaic_overlap']) if data.get('mosaic_overlap') is not None else 10.0

        # Image Adjustment fields
        framing.img_brightness = float(data['img_brightness']) if data.get('img_brightness') is not None else 0.0
        framing.img_contrast = float(data['img_contrast']) if data.get('img_contrast') is not None else 0.0
        framing.img_gamma = float(data['img_gamma']) if data.get('img_gamma') is not None else 1.0
        framing.img_saturation = float(data['img_saturation']) if data.get('img_saturation') is not None else 0.0

        # Overlay Preference fields
        framing.geo_belt_enabled = bool(data.get('geo_belt_enabled', True))

        framing.updated_at = datetime.now(UTC)

        db.commit()
        return jsonify({"status": "success", "message": "Framing saved."})
    except Exception as e:
        db.rollback()
        current_app.logger.error(f"[FRAMING API] Failed to save framing for '{object_name}': {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@api_bp.route('/api/get_framing/<path:object_name>')
@login_required
def get_framing(object_name):
    db = get_db()
    try:
        framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id,
            object_name=object_name
        ).one_or_none()

        record_event('framing_opened')
        if framing:
            # Helper: format mosaic columns if present
            return jsonify({
                "status": "found",
                "rig": framing.rig_id,
                "ra": framing.ra,
                "dec": framing.dec,
                "rotation": framing.rotation,
                "survey": framing.survey,
                "blend": framing.blend_survey,
                "blend_op": framing.blend_opacity,
                "mosaic_cols": framing.mosaic_cols or 1,
                "mosaic_rows": framing.mosaic_rows or 1,
                "mosaic_overlap": framing.mosaic_overlap if framing.mosaic_overlap is not None else 10,
                "img_brightness": framing.img_brightness if framing.img_brightness is not None else 0.0,
                "img_contrast": framing.img_contrast if framing.img_contrast is not None else 0.0,
                "img_gamma": framing.img_gamma if framing.img_gamma is not None else 1.0,
                "img_saturation": framing.img_saturation if framing.img_saturation is not None else 0.0,
                "geo_belt_enabled": framing.geo_belt_enabled if framing.geo_belt_enabled is not None else True
            })
        else:
            return jsonify({"status": "empty"})
    except Exception as e:
        current_app.logger.error(f"[FRAMING API] Failed to get framing for '{object_name}': {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/delete_framing', methods=['POST'])
@login_required
def delete_framing():
    db = get_db()
    try:
        data = request.get_json()
        object_name = data.get('object_name')

        framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id,
            object_name=object_name
        ).one_or_none()

        if framing:
            db.delete(framing)
            db.commit()
            return jsonify({"status": "success", "message": "Framing deleted."})
        else:
            return jsonify({"status": "error", "message": "No saved framing found."}), 404
    except Exception as e:
        db.rollback()
        current_app.logger.error(f"[FRAMING API] Failed to delete framing for '{object_name}': {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# --- PHD2 & ASIAIR Log Parsing ---

ASIAIR_PATTERNS = {
    'master': re.compile(r'^(\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}:\d{2})\s+\[(.*?)\]\s+(.*)$'),
    'temp': re.compile(r'temperature\s+([-\d\.]+)'),
    'exposure': re.compile(r'Exposure\s+([\d\.]+)s'),
    'stars': re.compile(r'Star number =\s*(\d+)'),
    'focus_pos': re.compile(r'position is (\d+)'),
    'fallback_ts': re.compile(r'^(\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}:\d{2})')
}


def _parse_phd2_log_content(content):
    """Parses PHD2 Guide Log into a formatted HTML report."""
    lines = content.splitlines()

    sessions = []
    # Added fields for SNR and Event tracking
    current_session = {'data': [], 'start': None, 'end': None, 'snr': [], 'dither_count': 0, 'settle_count': 0}
    col_map = {}

    pixel_scale = 1.0
    profile_name = "Unknown Profile"
    phd_version = "Unknown"
    # Metadata placeholders
    camera_name = "Unknown"
    mount_name = "Unknown"
    focal_length = "Unknown"
    x_algo = "Unknown"
    y_algo = "Unknown"

    # Helper: Robust date parser
    def parse_ts(s):
        s = s.strip()
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%d-%m-%Y %H:%M:%S'):
            try:
                return datetime.strptime(s, fmt)
            except:
                continue
        return None

    for line in lines:
        line = line.strip()
        if not line: continue

        # --- Headers ---
        if line.startswith("PHD2_v"):
            phd_version = line.split(",")[0]

        if "Pixel scale =" in line:
            try:
                # Regex to find float in string like "Pixel scale = 1.03 arc-sec/px"
                match = re.search(r'Pixel scale = ([\d\.]+)', line)
                if match: pixel_scale = float(match.group(1))
                # Also look for Focal Length on this line
                fl_match = re.search(r'Focal length = ([\d\.]+)', line)
                if fl_match: focal_length = f"{fl_match.group(1)} mm"
            except:
                pass

        if "Equipment Profile =" in line:
            profile_name = line.split("=")[1].strip()

            # Track Events within active session
        if current_session.get('start') and not current_session.get('end'):
            if "DITHER" in line:
                current_session['dither_count'] += 1
            if "SETTLING STATE CHANGE" in line:
                current_session['settle_count'] += 1

        if line.startswith("Camera ="):
            # Extract name before the first comma (e.g., "Camera = ZWO ASI174MM Mini, gain = ...")
            camera_name = line.split("=")[1].split(",")[0].strip()

        if line.startswith("Mount ="):
            mount_name = line.split("=")[1].split(",")[0].strip()

        if "X guide algorithm =" in line:
            x_algo = line.split("=")[1].split(",")[0].strip()

        if "Y guide algorithm =" in line:
            y_algo = line.split("=")[1].split(",")[0].strip()

        # --- Session Markers ---
        # Match "Guiding Begins at ..." broadly
        if "Guiding Begins" in line:
            parts = line.split(" at ")
            if len(parts) > 1:
                dt = parse_ts(parts[1])
                if dt:
                    current_session = {'data': [], 'start': dt, 'end': None, 'snr': [], 'dither_count': 0,
                                       'settle_count': 0}
            continue

        if "Guiding Ends" in line:
            parts = line.split(" at ")
            if len(parts) > 1:
                current_session['end'] = parse_ts(parts[1])

            # Archive session if it has data
            if current_session['data'] and current_session['start']:
                sessions.append(current_session)
            # Reset
            current_session = {'data': [], 'start': None, 'end': None, 'snr': [], 'dither_count': 0, 'settle_count': 0}
            continue

        # --- Column Definitions ---
        # Robust check for header line (handles "Frame" or Frame)
        if ("Frame" in line and "Time" in line and "," in line) and (
                line.startswith("Frame") or line.startswith('"Frame"')):
            cols = [c.strip().replace('"', '') for c in line.split(",")]
            col_map = {name: i for i, name in enumerate(cols)}
            continue

        # --- Data Rows ---
        if current_session.get('start') and line[0].isdigit() and ',' in line:
            parts = line.split(',')

            # Flexible Column Lookup (RAErr OR RA)
            def get_col(candidates):
                for c in candidates:
                    if c in col_map and len(parts) > col_map[c]:
                        val = parts[col_map[c]]
                        if val and val.strip():
                            try:
                                return float(val)
                            except:
                                pass
                return None

            # Expanded candidates to support log versions using "RawDistance" (e.g., v2.5)
            ra = get_col(['RAErr', 'RA', 'RARawDistance'])
            dec = get_col(['DecErr', 'Dec', 'DECRawDistance'])

            if ra is not None and dec is not None:
                current_session['data'].append((ra, dec))

                # Extract SNR
            if 'SNR' in col_map and len(parts) > col_map['SNR']:
                try:
                    current_session['snr'].append(float(parts[col_map['SNR']]))
                except:
                    pass

    # Handle unterminated session at EOF
    if current_session['data'] and current_session['start']:
        if not current_session['end']:
            # Fallback end time: start + frames * 2s (approx)
            est_seconds = len(current_session['data']) * 2
            current_session['end'] = current_session['start'] + timedelta(seconds=est_seconds)
        sessions.append(current_session)

    if not sessions:
        return "<p style='color: var(--text-tertiary); font-style: italic;'>No guiding sessions found in this log. Ensure the log contains 'Guiding Begins' and data rows.</p>"

    # --- Select Main Session (Longest) ---
    main_session = max(sessions, key=lambda s: len(s['data']))
    data_points = main_session['data']
    start_dt = main_session['start']
    end_dt = main_session['end']
    duration = end_dt - start_dt

    # --- Statistics ---
    import math

    n = len(data_points)
    sum_ra_sq = sum(d[0] ** 2 for d in data_points)
    sum_dec_sq = sum(d[1] ** 2 for d in data_points)

    # RMS in pixels
    rms_ra_px = math.sqrt(sum_ra_sq / n)
    rms_dec_px = math.sqrt(sum_dec_sq / n)
    rms_tot_px = math.sqrt((sum_ra_sq + sum_dec_sq) / n)

    # Convert to Arcsec
    rms_ra = rms_ra_px * pixel_scale
    rms_dec = rms_dec_px * pixel_scale
    rms_tot = rms_tot_px * pixel_scale

    # Class Grading (<0.5 Excellent, <0.8 Good, <1.0 Ok, >1.0 Poor)
    rms_class = "status-success" if rms_tot < 0.5 else "status-info" if rms_tot < 0.8 else "status-warning" if rms_tot < 1.0 else "status-danger"

    # --- Derived Metrics for HTML ---
    dither_c = main_session.get('dither_count', 0)
    settle_c = main_session.get('settle_count', 0)

    snr_list = main_session.get('snr', [])
    if snr_list:
        avg_snr = sum(snr_list) / len(snr_list)
        min_snr = min(snr_list)
        max_snr = max(snr_list)
    else:
        avg_snr, min_snr, max_snr = 0, 0, 0

    dither_html = ""
    if dither_c > 0:
        dither_html = f"""
            <li><strong>Dithering is Active:</strong> The log shows frequent "DITHER" commands ({dither_c} detected) followed by "SETTLING STATE CHANGE". This confirms your capture sequence is correctly instructing PHD2 to shift the frame between exposures to reduce noise.</li>
            <li><strong>Settling Performance:</strong> There are distinct periods where the mount is "Settling" after a dither ({settle_c} events detected).</li>
            """
    else:
        dither_html = "<li><strong>Dithering:</strong> No dither commands detected in this session.</li>"

    snr_class = "status-success" if avg_snr >= 20 else "status-warning" if avg_snr >= 10 else "status-danger"
    snr_html = f"""<li><strong>Signal Strength (SNR):</strong> <span class="{snr_class}">Avg {avg_snr:.1f}</span> (Range: {min_snr:.1f}-{max_snr:.1f}). Values consistently around 20–30 indicate a very healthy/strong signal, likely helped by the 2x binning.</li>"""

    html = f"""
    <h3>PHD2 Guiding Analysis</h3>
    <p style="margin-bottom: 10px;"><strong>Profile:</strong> {profile_name} &nbsp;|&nbsp; <strong>Date:</strong> {start_dt.strftime('%b %d, %Y')}</p>

    <hr style="margin: 10px 0; border: 0; border-top: 1px solid var(--border-light);">

    <div style="margin-bottom: 2px;"><strong>Performance (RMS)</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Total RMS:</strong> <strong class="{rms_class}">{rms_tot:.2f}"</strong> (RA: {rms_ra:.2f}", Dec: {rms_dec:.2f}")</li>
        <li><strong>Pixel Scale:</strong> {pixel_scale}"/px</li>
        <li><strong>Duration:</strong> {int(duration.total_seconds() // 60)} min ({n} frames)</li>
    </ul>

    <div style="margin-bottom: 2px;"><strong>Equipment & Config</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Camera:</strong> {camera_name}</li>
        <li><strong>Mount:</strong> {mount_name}</li>
        <li><strong>Optics:</strong> {focal_length}</li>
        <li><strong>Algorithms:</strong> RA: {x_algo} / Dec: {y_algo}</li>
    </ul>

    <div style="margin-bottom: 2px;"><strong>Guide Performance & Stability</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        {dither_html}
        {snr_html}
    </ul>

    <div style="margin-bottom: 2px;"><strong>Session Details</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Start:</strong> {start_dt.strftime('%H:%M:%S')}</li>
        <li><strong>End:</strong> {end_dt.strftime('%H:%M:%S')}</li>
        <li><strong>Version:</strong> {phd_version}</li>
    </ul>
    """

    if len(sessions) > 1:
        html += f"<div style='margin-top:10px; font-size:0.85em; color:var(--text-muted);'>* Analyzed longest session ({n} frames). Log contained {len(sessions)} runs.</div>"

    return html


# --- ASIAIR Log Parsing Logic ---


def _parse_asiair_log_content(content):
    """Parses ASIAIR log content into a formatted HTML report."""
    lines = content.splitlines()

    # 1. Collect all raw data points from the log
    raw_data_points = []

    for i, line in enumerate(lines):
        line = line.strip()
        if not line: continue

        # --- Regex Parsing ---
        master_match = ASIAIR_PATTERNS['master'].match(line)
        if not master_match:
            ts_match = ASIAIR_PATTERNS['fallback_ts'].match(line)
            if not ts_match: continue
            dt_str = ts_match.group(1)
            category = "Unknown"
            msg = line[len(dt_str):].strip()
        else:
            dt_str = master_match.group(1)
            category = master_match.group(2)
            msg = master_match.group(3)

        try:
            dt = datetime.strptime(dt_str, '%Y/%m/%d %H:%M:%S')
        except ValueError:
            continue

        # --- Extract Data Points ---

        # Exposure
        if "Exposure" in category or msg.startswith("Exposure"):
            if msg.startswith("Exposure"):
                dur_match = ASIAIR_PATTERNS['exposure'].search(msg)
                seconds = float(dur_match.group(1)) if dur_match else 0
                raw_data_points.append({'dt': dt, 'type': 'exposure', 'seconds': seconds})
                continue

        # Autorun (Target Name & Start/End)
        if "Autorun|Begin" in category or "[Autorun|Begin]" in msg:
            clean_msg = msg.replace("[Autorun|Begin]", "").strip()
            parts = clean_msg.split(" Start")
            tgt = parts[0] if parts else "Unknown"
            raw_data_points.append({'dt': dt, 'type': 'target', 'name': tgt})
            raw_data_points.append({'dt': dt, 'type': 'event', 'text': "Session Start", 'color_class': "status-info"})
            continue
        elif "Autorun|End" in category or "[Autorun|End]" in msg:
            raw_data_points.append({'dt': dt, 'type': 'event', 'text': "Session Ended", 'color_class': "status-info"})
            continue

        # Guiding / Dither
        if "Guide" in category or "[Guide]" in msg:
            if "Dither" in msg and "Settle" not in msg:
                raw_data_points.append({'dt': dt, 'type': 'dither_start'})
            elif "Settle Done" in msg:
                raw_data_points.append({'dt': dt, 'type': 'dither_end'})
            elif "Settle Timeout" in msg:
                raw_data_points.append(
                    {'dt': dt, 'type': 'event', 'text': "Guiding: Dither Settle Timeout", 'color_class': "status-danger"})
            continue

        # Meridian Flip
        if "Meridian Flip" in category or "Meridian Flip" in msg:
            if "Start" in msg:
                raw_data_points.append(
                    {'dt': dt, 'type': 'event', 'text': "Meridian Flip: Sequence started", 'color_class': "status-info"})
            elif "failed" in msg:
                # Store raw error message
                raw_data_points.append({'dt': dt, 'type': 'event', 'text': f"ERROR: {msg}", 'color_class': "status-danger"})
            elif "succeeded" in msg:
                raw_data_points.append(
                    {'dt': dt, 'type': 'event', 'text': "Meridian Flip: Success", 'color_class': "status-success"})
            continue

        # Focus Events
        if ("AutoFocus" in category or "Auto Focus" in msg) and "Begin" in msg:
            raw_data_points.append({'dt': dt, 'type': 'event', 'text': "Auto Focus Run", 'color_class': "status-info"})

        # Environmental Data Extraction
        temp_match = ASIAIR_PATTERNS['temp'].search(msg)
        if temp_match:
            raw_data_points.append({'dt': dt, 'type': 'temp', 'val': float(temp_match.group(1))})

        if "Auto focus succeeded" in msg:
            pos_match = ASIAIR_PATTERNS['focus_pos'].search(msg)
            if pos_match:
                raw_data_points.append({'dt': dt, 'type': 'focus', 'val': int(pos_match.group(1))})

        if "Solve succeeded" in msg:
            star_match = ASIAIR_PATTERNS['stars'].search(msg)
            if star_match:
                raw_data_points.append({'dt': dt, 'type': 'stars', 'val': int(star_match.group(1))})

        if "Mount GoTo Home" in msg:
            raw_data_points.append({'dt': dt, 'type': 'event', 'text': "GoTo Home (Session End)", 'color_class': "status-secondary"})

    # --- 2. Session Grouping Logic ---
    if not raw_data_points:
        return "<p>No parsable data found.</p>"

    # Sort all points by time
    raw_data_points.sort(key=lambda x: x['dt'])

    # Group into separate sessions based on time gaps > 90 minutes
    # This separates "Night Imaging" from "Morning Flats"
    sessions = []
    current_session = []
    GAP_THRESHOLD_MINUTES = 90

    for point in raw_data_points:
        if not current_session:
            current_session.append(point)
        else:
            last_dt = current_session[-1]['dt']
            diff = (point['dt'] - last_dt).total_seconds() / 60
            if diff > GAP_THRESHOLD_MINUTES:
                sessions.append(current_session)
                current_session = [point]
            else:
                current_session.append(point)
    if current_session:
        sessions.append(current_session)

    # Pick the MAIN session (longest duration) for analysis
    main_session = max(sessions, key=lambda s: (s[-1]['dt'] - s[0]['dt']).total_seconds())

    # --- 3. Calculate Stats on Main Session Only ---
    start_dt = main_session[0]['dt']
    end_dt = main_session[-1]['dt']
    total_duration = (end_dt - start_dt).total_seconds()

    target_name = "Unknown Target"
    subs_count = 0
    total_exposure_sec = 0
    dither_durations = []
    last_dither_start = None
    temps = []
    focus_moves = []
    star_counts = []
    timeline_events = []  # List of (dt, color, text)

    for p in main_session:
        ptype = p['type']

        if ptype == 'target':
            target_name = p['name']
        elif ptype == 'exposure':
            subs_count += 1
            total_exposure_sec += p['seconds']
        elif ptype == 'dither_start':
            last_dither_start = p['dt']
        elif ptype == 'dither_end' and last_dither_start:
            dither_durations.append((p['dt'] - last_dither_start).total_seconds())
            last_dither_start = None
        elif ptype == 'temp':
            temps.append(p['val'])
        elif ptype == 'stars':
            star_counts.append(p['val'])
        elif ptype == 'focus':
            last_temp = temps[-1] if temps else None
            if last_temp is not None: focus_moves.append((last_temp, p['val']))
        elif ptype == 'event':
            timeline_events.append((p['dt'], p['color_class'], p['text']))

    # Metrics
    imaging_duration = total_exposure_sec
    duty_cycle = (imaging_duration / total_duration * 100) if total_duration > 0 else 0

    total_h = int(total_duration // 3600)
    total_m = int((total_duration % 3600) // 60)
    img_h = int(imaging_duration // 3600)
    img_m = int((imaging_duration % 3600) // 60)

    # Dithering
    total_dither_time = sum(dither_durations)
    avg_dither = (total_dither_time / len(dither_durations)) if dither_durations else 0
    dither_m = int(total_dither_time // 60)

    # Environment
    start_temp = temps[0] if temps else 0
    end_temp = temps[-1] if temps else 0
    avg_stars = int(sum(star_counts) / len(star_counts)) if star_counts else 0

    focus_summary = "N/A"
    if len(focus_moves) >= 2:
        steps_delta = abs(focus_moves[-1][1] - focus_moves[0][1])
        temp_delta = abs(focus_moves[-1][0] - focus_moves[0][0])
        steps_per_c = int(steps_delta / temp_delta) if temp_delta > 0 else 0
        focus_summary = f"Moved {steps_delta} steps over {temp_delta:.1f}°C (~{steps_per_c} steps/°C)"

    # --- HTML Generation ---
    html = f"""
    <h3>ASIAIR Session Analysis</h3>
    <p style="margin-bottom: 10px;"><strong>Target:</strong> {target_name} &nbsp;|&nbsp; <strong>Date:</strong> {start_dt.strftime('%b %d, %Y')}</p>

    <hr style="margin: 10px 0; border: 0; border-top: 1px solid var(--border-light);">

    <div style="margin-bottom: 2px;"><strong>Efficiency Metrics</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Duty Cycle:</strong> {duty_cycle:.1f}% ({img_h}h {img_m}m Imaging / {total_h}h {total_m}m Total)</li>
        <li><strong>Total Subs:</strong> {subs_count}</li>
        <li><strong>Dithering:</strong> Average settle: {avg_dither:.1f}s. Total wasted: {dither_m} min.</li>
    </ul>

    <div style="margin-bottom: 2px;"><strong>Environmental Data</strong></div>
    <ul style="margin-top: 0; margin-bottom: 12px; padding-left: 20px;">
        <li><strong>Temp Drift:</strong> {start_temp}°C &rarr; {end_temp}°C</li>
        <li><strong>Focus:</strong> {focus_summary}</li>
        <li><strong>Star Count (Avg):</strong> ~{avg_stars}</li>
    </ul>

    <div style="margin-bottom: 2px;"><strong>Key Events Timeline</strong></div>
    <div style="padding-left: 0; margin-top: 0;">
    """

    # --- 4. Timeline Rendering with Error Merging ---
    timeline_html_lines = []

    for dt, color_class, text in timeline_events:
        time_str = dt.strftime('%H:%M') if dt else "--:--"

        # Prepare content string
        if "ERROR" in text:
            # Use class for errors with bold styling
            content_html = f"<span class='{color_class}' style='font-weight: bold;'>{text}</span>"
        else:
            content_html = f"<span class='{color_class}'>{text}</span>"

        # Check for merge condition:
        # If current is an ERROR, and we have a previous line, append it there.
        if "ERROR" in text and timeline_html_lines:
            last_line = timeline_html_lines.pop()
            # Remove the closing div, append separator + error, re-add closing div
            merged_line = last_line.replace("</div>", "") + f" &nbsp; {content_html}</div>"
            timeline_html_lines.append(merged_line)
        else:
            # Standard Line
            row_html = f"<div style='margin-bottom: 2px;'><span style='color: var(--text-muted); font-family: monospace; margin-right: 8px;'>{time_str}</span> {content_html}</div>"
            timeline_html_lines.append(row_html)

    # Join and append timeline
    html += "".join(timeline_html_lines)
    html += "</div>"

    # Warning if sessions were dropped
    if len(sessions) > 1:
        dropped_count = len(sessions) - 1
        html += f"<div style='margin-top:10px; font-size:0.85em; color:var(--text-muted);'>* Analyzed main imaging session only. Excluded {dropped_count} disconnected segment(s).</div>"

    return html


# =============================================================================
# Tier 2 Routes (from __init__.py Step 5)
# =============================================================================


@api_bp.route('/api/parse_asiair_log', methods=['POST'])
@login_required
def api_parse_asiair_log():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": _("No file uploaded")}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": _("Empty filename")}), 400

    try:
        content = file.read().decode('utf-8', errors='ignore')

        # Basic sniffing to determine log type
        first_lines = content[:200]

        if "PHD2" in first_lines or "Guiding Begins" in content:
            # Route to PHD2 Parser
            report_html = _parse_phd2_log_content(content)
        else:
            # Route to ASIAIR Parser (Default)
            report_html = _parse_asiair_log_content(content)

        record_event('log_file_imported')
        return jsonify({"status": "success", "html": report_html})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/session/<int:session_id>/log-analysis')
@login_required
def get_session_log_analysis(session_id):
    """
    Return structured log data for Chart.js visualization with parse-once caching.

    Returns:
    {
        'has_logs': bool,
        'asiair': {...} or null,
        'phd2': {...} or null,
        'nina': {...} or null
    }
    """
    import json
    from nova.log_parser import parse_asiair_log, parse_phd2_log, parse_nina_log

    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one_or_none()

    if not user:
        return jsonify({'error': _('User not found')}), 404

    session = db.query(JournalSession).filter_by(id=session_id, user_id=user.id).one_or_none()
    if not session:
        return jsonify({'error': _('Session not found')}), 404

    # 1. Return cached result if available and valid (has session_start for clock time)
    if session.log_analysis_cache:
        try:
            cached = json.loads(session.log_analysis_cache)
            # Invalidate old cache that doesn't have session_start
            asiair = cached.get('asiair')
            phd2 = cached.get('phd2')
            nina = cached.get('nina')
            has_session_start = (asiair and asiair.get('session_start')) or (phd2 and phd2.get('session_start')) or (nina and nina.get('session_start'))
            if has_session_start:
                return jsonify(cached)
            # Old cache without session_start - fall through to re-parse
        except json.JSONDecodeError:
            pass  # Fall through to re-parse if cache is corrupt

    # 2. Parse logs if any exist
    result = {
        'has_logs': False,
        'asiair': None,
        'phd2': None,
        'nina': None
    }

    # Parse logs - use read_log_content to handle both filesystem paths and legacy raw content
    asiair_content = read_log_content(session.asiair_log_content)
    if asiair_content:
        result['asiair'] = parse_asiair_log(asiair_content)
        result['has_logs'] = True

    phd2_content = read_log_content(session.phd2_log_content)
    if phd2_content:
        result['phd2'] = parse_phd2_log(phd2_content)
        result['has_logs'] = True

    nina_content = read_log_content(session.nina_log_content)
    if nina_content:
        result['nina'] = parse_nina_log(nina_content)
        result['has_logs'] = True

    # 3. Cache the result if parsing happened
    if result['has_logs']:
        session.log_analysis_cache = json.dumps(result)
        db.commit()

    return jsonify(result)


@api_bp.route('/api/bulk_fetch_details', methods=['POST'])
@login_required
def bulk_fetch_details():
    """Fetch missing details (type, magnitude, size, SB, constellation) for selected objects."""
    data = request.get_json()
    object_ids = data.get('object_ids', [])

    if not object_ids:
        return jsonify({"status": "error", "message": "No objects selected"}), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        # Query only the selected objects for this user
        objects_to_check = db.query(AstroObject).filter(
            AstroObject.user_id == user_id,
            AstroObject.object_name.in_(object_ids)
        ).all()

        if not objects_to_check:
            return jsonify({"status": "error", "message": "No valid objects found"}), 400

        updated_count = 0
        error_count = 0
        refetch_triggers = [None, "", "N/A", "Not Found", "Fetch Error"]

        for obj in objects_to_check:
            needs_update = (
                obj.type in refetch_triggers or
                obj.magnitude in refetch_triggers or
                obj.size in refetch_triggers or
                obj.sb in refetch_triggers or
                obj.constellation in refetch_triggers
            )
            if needs_update:
                try:
                    # Auto-calculate Constellation if missing
                    if obj.constellation in refetch_triggers and obj.ra_hours is not None and obj.dec_deg is not None:
                        coords = SkyCoord(ra=obj.ra_hours*u.hourangle, dec=obj.dec_deg*u.deg)
                        obj.constellation = get_constellation(coords, short_name=True)

                    # Fetch other details from external API
                    fetched_data = nova_data_fetcher.get_astronomical_data(obj.object_name)
                    if fetched_data.get("object_type"): obj.type = fetched_data["object_type"]
                    if fetched_data.get("magnitude"): obj.magnitude = str(fetched_data["magnitude"])
                    if fetched_data.get("size_arcmin"): obj.size = str(fetched_data["size_arcmin"])
                    if fetched_data.get("surface_brightness"): obj.sb = str(fetched_data["surface_brightness"])
                    updated_count += 1
                    time.sleep(0.5)  # Be kind to external APIs
                except Exception as e:
                    print(f"Failed to fetch details for {obj.object_name}: {e}")
                    error_count += 1

        db.commit()

        msg = f"Updated details for {updated_count} object(s)."
        if error_count > 0:
            msg += f" ({error_count} failed)"
        if updated_count == 0 and error_count == 0:
            msg = "No missing data found - all selected objects already have complete details."

        return jsonify({"status": "success", "message": msg, "updated": updated_count, "errors": error_count})

    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/find_duplicates')
@login_required
def find_duplicates():
    load_full_astro_context()
    user_id = g.db_user.id

    # 1. Get all objects with valid coordinates
    all_objects = [o for o in g.objects_list if o.get('RA (hours)') is not None and o.get('DEC (degrees)') is not None]

    if len(all_objects) < 2:
        return jsonify({"status": "success", "duplicates": []})

    # 2. Create SkyCoord objects
    ra_vals = [o['RA (hours)'] * 15.0 for o in all_objects]  # Convert to degrees
    dec_vals = [o['DEC (degrees)'] for o in all_objects]

    coords = SkyCoord(ra=ra_vals * u.deg, dec=dec_vals * u.deg)

    # 3. Find matches within 2.5 arcminutes
    # search_around_sky finds all pairs (i, j) where distance < limit
    # This includes (i, i) self-matches and (i, j) + (j, i) duplicates
    idx1, idx2, d2d, d3d = search_around_sky(coords, coords, seplimit=2.5 * u.arcmin)

    potential_duplicates = []
    seen_pairs = set()

    for i, j, dist in zip(idx1, idx2, d2d):
        if i >= j: continue  # Skip self-matches and reverse duplicates

        obj_a = all_objects[i]
        obj_b = all_objects[j]

        # Create a unique key for this pair
        pair_key = tuple(sorted([obj_a['Object'], obj_b['Object']]))
        if pair_key in seen_pairs: continue
        seen_pairs.add(pair_key)

        potential_duplicates.append({
            "object_a": obj_a,
            "object_b": obj_b,
            "separation_arcmin": round(dist.to(u.arcmin).value, 2)
        })

    return jsonify({"status": "success", "duplicates": potential_duplicates})


@api_bp.route('/api/merge_objects', methods=['POST'])
@login_required
def merge_objects():
    data = request.get_json()
    keep_id = data.get('keep_id')
    merge_id = data.get('merge_id')

    if not keep_id or not merge_id:
        return jsonify({"status": "error", "message": "Missing object IDs"}), 400

    db = get_db()
    try:
        user_id = g.db_user.id

        # 1. Fetch Objects
        obj_keep = db.query(AstroObject).filter_by(user_id=user_id, object_name=keep_id).one_or_none()
        obj_merge = db.query(AstroObject).filter_by(user_id=user_id, object_name=merge_id).one_or_none()

        if not obj_keep or not obj_merge:
            return jsonify({"status": "error", "message": "One or both objects not found."}), 404

        print(f"[MERGE] Merging '{merge_id}' INTO '{keep_id}'...")

        # 2. Re-link Journals
        journals = db.query(JournalSession).filter_by(user_id=user_id, object_name=merge_id).all()
        for j in journals:
            j.object_name = keep_id
        print(f"   -> Moved {len(journals)} journal sessions.")

        # 3. Re-link Projects
        projects = db.query(Project).filter_by(user_id=user_id, target_object_name=merge_id).all()
        for p in projects:
            p.target_object_name = keep_id
        print(f"   -> Updated {len(projects)} projects.")

        # 4. Handle Framings
        framing_keep = db.query(SavedFraming).filter_by(user_id=user_id, object_name=keep_id).one_or_none()
        framing_merge = db.query(SavedFraming).filter_by(user_id=user_id, object_name=merge_id).one_or_none()

        if framing_merge:
            if not framing_keep:
                # Move framing to the kept object
                framing_merge.object_name = keep_id
                print(f"   -> Moved framing from {merge_id} to {keep_id}.")
            else:
                # Conflict: Keep existing framing on target, delete merged one
                db.delete(framing_merge)
                print(f"   -> Deleted conflicting framing from {merge_id}.")

        # 5. Merge Notes (Append if different)
        if obj_merge.project_name:
            if not obj_keep.project_name:
                obj_keep.project_name = obj_merge.project_name
            elif obj_merge.project_name not in obj_keep.project_name:
                obj_keep.project_name += f"<br><hr><strong>Merged Notes ({merge_id}):</strong><br>{obj_merge.project_name}"

        # 6. Delete the Merged Object
        db.delete(obj_merge)

        db.commit()
        return jsonify({"status": "success", "message": f"Successfully merged '{merge_id}' into '{keep_id}'."})

    except Exception as e:
        db.rollback()
        print(f"[MERGE ERROR] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# =============================================================================
# MOBILE COMPANION ROUTES
# =============================================================================

@api_bp.route('/api/mobile_data_chunk')
@login_required
def api_mobile_data_chunk():
    """Fetches a specific slice of object data for the mobile progress bar."""
    load_full_astro_context()

    offset = int(request.args.get('offset', 0))
    limit = int(request.args.get('limit', 10))

    user = g.db_user
    location_name = g.selected_location
    user_prefs = g.user_config or {}

    results = []
    total_count = 0

    if user and location_name:
        db = get_db()
        # 1. Get Location
        location_db_obj = db.query(Location).options(
            selectinload(Location.horizon_points)
        ).filter_by(user_id=user.id, name=location_name).one_or_none()

        if location_db_obj:
            # 2. Get All Objects (to count and slice)
            all_objects_query = db.query(AstroObject).filter_by(user_id=user.id).order_by(AstroObject.id)
            total_count = all_objects_query.count()

            # 3. Get Slice
            sliced_objects = all_objects_query.offset(offset).limit(limit).all()

            # 4. Calculate Data for this slice
            results = get_all_mobile_up_now_data(
                user,
                location_db_obj,
                user_prefs,
                sliced_objects,
                db  # Pass DB session
            )

    return jsonify({
        "data": results,
        "total": total_count,
        "offset": offset,
        "limit": limit
    })


@api_bp.route('/api/mobile_status')
@login_required
def api_mobile_status():
    """Returns moon phase and sun events for the mobile status strip."""
    load_full_astro_context()

    user = g.db_user
    location_name = g.selected_location

    if not user or not location_name:
        return jsonify({"error": "No location selected"}), 400

    db = get_db()
    location_db_obj = db.query(Location).filter_by(user_id=user.id, name=location_name).one_or_none()

    if not location_db_obj:
        return jsonify({"error": "Location not found"}), 404

    try:
        lat = location_db_obj.lat
        lon = location_db_obj.lon
        tz_name = location_db_obj.timezone
        local_tz = pytz.timezone(tz_name)
    except Exception as e:
        return jsonify({"error": f"Invalid location data: {e}"}), 400

    current_datetime_local = datetime.now(local_tz)

    # Determine "Observing Night" Date (Noon-to-Noon Logic)
    if current_datetime_local.hour < 12:
        local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        local_date = current_datetime_local.strftime('%Y-%m-%d')

    # Calculate moon phase
    try:
        time_for_phase_local = local_tz.localize(
            datetime.combine(datetime.now().date(), datetime.min.time().replace(hour=12)))
        moon_phase = round(ephem.Moon(time_for_phase_local.astimezone(pytz.utc)).phase, 0)
    except Exception:
        moon_phase = None

    # Calculate sun events
    try:
        sun_events = calculate_sun_events_cached(local_date, tz_name, lat, lon)
        dusk = sun_events.get("astronomical_dusk", "—")
        dawn = sun_events.get("astronomical_dawn", "—")
        dusk_dawn = f"{dusk}–{dawn}" if dusk != "N/A" and dawn != "N/A" else "—"
    except Exception:
        dusk_dawn = "—"

    return jsonify({
        "moon_phase": moon_phase,
        "dusk_dawn": dusk_dawn
    })


@api_bp.route('/api/get_moon_data')
def get_moon_data_for_session():
    # --- Manual Auth Check for Guest Support ---
    if not (current_user.is_authenticated or SINGLE_USER_MODE or getattr(g, 'is_guest', False)):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        date_str = request.args.get('date')
        tz_name = request.args.get('tz')

        # Use safe_float to handle potential empty strings or invalid values
        lat = safe_float(request.args.get('lat'))
        lon = safe_float(request.args.get('lon'))
        ra = safe_float(request.args.get('ra'))
        dec = safe_float(request.args.get('dec'))

        if not all([date_str, tz_name]):
            raise ValueError("Missing date or timezone.")

        # Check if critical values are None after safe_float
        if lat is None or lon is None:
            raise ValueError("Invalid or missing latitude/longitude.")

        # --- Calculate Moon Phase (always possible) ---
        local_tz = pytz.timezone(tz_name)
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        # Use a consistent time (e.g., local noon) for phase calculation
        time_for_phase_local = local_tz.localize(
            datetime.combine(date_obj.date(), datetime.min.time().replace(hour=12)))
        moon_phase = round(ephem.Moon(time_for_phase_local.astimezone(pytz.utc)).phase, 1)

        # --- Calculate Separation (only if RA/DEC are present) ---
        angular_sep_value = None
        if ra is not None and dec is not None:
            # All values present, calculate separation
            sun_events = calculate_sun_events_cached(date_str, tz_name, lat, lon)
            dusk_str = sun_events.get("astronomical_dusk", "21:00")
            dusk_time_obj = datetime.strptime(dusk_str, "%H:%M").time()
            time_for_calc_local = local_tz.localize(datetime.combine(date_obj.date(), dusk_time_obj))
            time_obj = Time(time_for_calc_local.astimezone(pytz.utc))

            location_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            frame = AltAz(obstime=time_obj, location=location_obj)
            obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
            moon_coord = get_body('moon', time_obj, location=location_obj)
            separation = obj_coord.transform_to(frame).separation(moon_coord.transform_to(frame)).deg
            angular_sep_value = round(separation, 1)
        else:
            print("[API Moon Data] RA/DEC not provided, calculating phase only.")

            # --- Calculate Observable Duration (for Max Subs estimate) ---
        obs_duration_min = 0
        if ra is not None and dec is not None:
            try:
                # Attempt to resolve horizon mask and threshold from global context if location matches
                alt_thresh = g.user_config.get("altitude_threshold", 20)
                mask = None

                # Heuristic lookup for location-specific settings
                if hasattr(g, 'locations') and isinstance(g.locations, dict):
                    for loc_details in g.locations.values():
                        # Fuzzy match coordinates to find correct location settings
                        if (abs(loc_details.get('lat', 999) - lat) < 0.001 and
                                abs(loc_details.get('lon', 999) - lon) < 0.001):
                            mask = loc_details.get('horizon_mask')
                            if loc_details.get('altitude_threshold') is not None:
                                alt_thresh = loc_details.get('altitude_threshold')
                            break

                # Use a standard sampling interval for this quick check
                sampling = 15

                obs_dur_td, _, _, _ = calculate_observable_duration_vectorized(
                    ra, dec, lat, lon, date_str, tz_name, alt_thresh, sampling, horizon_mask=mask
                )
                if obs_dur_td:
                    obs_duration_min = int(obs_dur_td.total_seconds() / 60)
            except Exception as e:
                print(f"[API Moon] Duration calc error: {e}")

        return jsonify({
            "status": "success",
            "moon_illumination": moon_phase,
            "angular_separation": angular_sep_value,
            "observable_duration_min": obs_duration_min
        })

    except Exception as e:
        print(f"ERROR in /api/get_moon_data: {e}")
        traceback.print_exc()  # Add traceback for better debugging
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route('/api/get_monthly_plot_data/<path:object_name>')
def get_monthly_plot_data(object_name):
    load_full_astro_context()
    # This function provides data for the monthly chart view.
    data = get_ra_dec(object_name)
    if not data or data.get('RA (hours)') is None:
        return jsonify({"error": "Object data not found"}), 404

    year = int(request.args.get('year'))
    month = int(request.args.get('month'))
    lat = float(request.args.get('plot_lat', g.lat))
    lon = float(request.args.get('plot_lon', g.lon))
    tz_name = request.args.get('plot_tz', g.tz_name)
    local_tz = pytz.timezone(tz_name)

    num_days = calendar.monthrange(year, month)[1]
    dates, obj_altitudes, moon_altitudes = [], [], []

    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=data['RA (hours)'] * u.hourangle, dec=data['DEC (degrees)'] * u.deg)

    for day in range(1, num_days + 1):
        local_midnight = local_tz.localize(datetime(year, month, day, 0, 0))
        time_astropy = Time(local_midnight.astimezone(pytz.utc))

        altaz_frame = AltAz(obstime=time_astropy, location=location)
        obj_alt = sky_coord.transform_to(altaz_frame).alt.deg
        moon_coord = get_body('moon', time_astropy, location)
        moon_alt = moon_coord.transform_to(altaz_frame).alt.deg

        dates.append(local_midnight.strftime('%Y-%m-%d'))
        obj_altitudes.append(obj_alt)
        moon_altitudes.append(moon_alt)

    return jsonify({
        "dates": dates,
        "object_alt": obj_altitudes,
        "moon_alt": moon_altitudes
    })


@api_bp.route('/api/internal/provision_user', methods=['POST'])
def provision_user():
    data = request.get_json()
    provided_key = request.headers.get('X-Api-Key')
    expected_key = os.environ.get('PROVISIONING_API_KEY')

    if not expected_key or provided_key != expected_key:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password required"}), 400

    with current_app.app_context():
        # Check if the user already exists
        existing_user = auth_db.session.scalar(auth_db.select(User).where(User.username == username))

        if existing_user:
            # If the user exists, UPDATE their password
            existing_user.set_password(password)
            auth_db.session.commit()
            print(f"✅ Password updated for user '{username}' via API.")
            return jsonify({"status": "success", "message": f"User {username} password updated"}), 200
        else:
            # If the user does not exist, CREATE them
            new_user = User(username=username)
            new_user.set_password(password)
            auth_db.session.add(new_user)
            auth_db.session.commit()
            print(f"✅ User '{username}' provisioned in database via API.")
            return jsonify({"status": "success", "message": f"User {username} provisioned"}), 201


@api_bp.route('/api/internal/deprovision_user', methods=['POST'])
def deprovision_user():
    api_key = request.headers.get('X-Api-Key')
    if api_key != os.environ.get('PROVISIONING_API_KEY'):
        return jsonify({"status":"error","message":"unauthorized"}), 401

    data = request.get_json(force=True) or {}
    username = data.get('username')
    action = (data.get('action') or 'disable').lower()  # 'disable' or 'delete'

    if not username:
        return jsonify({"status":"error","message":"missing username"}), 400

    if action == 'delete':
        ok = delete_user(username)
        return (jsonify({"status": "success", "message": "deleted"}), 200) if ok else (jsonify({"status":"not_found"}), 404)
    else:
        ok = disable_user(username)
        return (jsonify({"status": "success", "message": "disabled"}), 200) if ok else (jsonify({"status":"not_found"}), 404)


# =============================================================================
# Weather Helper Chain (co-migrated from nova/__init__.py)
# =============================================================================

DEFAULT_HTTP_TIMEOUT = 10  # Standard timeout for most HTTP requests


def get_weather_data_single_attempt(url: str, lat: float, lon: float) -> dict | None:
    """
    Fetches weather data from a single URL with robust error handling.
    Returns a dictionary on success, None on any failure.
    """
    try:
        r = None
        # Use a reasonable timeout (e.g., 10 seconds)
        r = requests.get(url, timeout=DEFAULT_HTTP_TIMEOUT)

        # --- 1. Check for HTTP errors (like 500, 502, 404, etc.) ---
        if r.status_code != 200:
            print(f"[Weather Func] ERROR: Received non-200 status code {r.status_code} for lat={lat}, lon={lon}")
            # Log the error page content for debugging
            print(f"[Weather Func] Response text (first 200 chars): {r.text[:200]}")
            return None

        # --- 2. Try to parse the JSON ---
        # r.json() will automatically handle content-type and raise JSONDecodeError
        data = r.json()
        return data

    except requests.exceptions.JSONDecodeError as e:
        # This is the exact error from your logs!
        print(f"[Weather Func] ERROR: Failed to decode JSON for lat={lat}, lon={lon}. Error: {e}")
        # Log the problematic text that isn't JSON
        response_text = getattr(r, 'text', '<no response object>')
        print(f"[Weather Func] Response text (first 200 chars): {response_text[:200]}")
        return None

    except requests.exceptions.RequestException as e:
        # This handles timeouts, DNS errors, connection errors, etc.
        print(f"[Weather Func] ERROR: Request failed for lat={lat}, lon={lon}. Error: {e}")
        return None

    except Exception as e:
        # Catch any other unexpected errors
        print(f"[Weather Func] ERROR: An unexpected error occurred for lat={lat}, lon={lon}. Error: {e}")
        return None


def get_weather_data_with_retries(lat: float, lon: float, product: str = "meteo") -> dict | None:
    """
    Attempts to fetch weather data from 7Timer! with retries and exponential backoff.
    Builds the URL and calls the single-attempt helper function.

    product:
      - "meteo": standard meteorological product (meteo.php, with profiles etc.)
      - "astro": astronomical product (astro.php, includes seeing/transparency)
    """

    # --- Build the 7Timer! URL correctly depending on product ---
    if product == "astro":
        # ASTRO product uses astro.php; no separate 'product' parameter in the URL
        base_url = "http://www.7timer.info/bin/astro.php"
        url = f"{base_url}?lon={lon}&lat={lat}&ac=0&unit=metric&output=json"
    else:
        # Default / METEO product
        base_url = "http://www.7timer.info/bin/meteo.php"
        # Here the 'product' parameter is still useful (e.g. 'meteo')
        url = f"{base_url}?lon={lon}&lat={lat}&product={product}&ac=0&unit=metric&output=json"

    retries = 3
    delay_seconds = 5  # Start with a 5-second delay

    for i in range(retries):
        # Call our robust single-attempt helper
        data = get_weather_data_single_attempt(url, lat, lon)

        if data is not None:
            # Success!
            # print(f"[Weather Func] Successfully fetched data for lat={lat}, lon={lon} on attempt {i + 1} (product={product})")
            return data

        # If data is None, it failed. Log and retry (if not the last attempt).
        if i < retries - 1:
            print(f"[Weather Func] WARN: Attempt {i + 1} for product='{product}' failed for lat={lat}, lon={lon}. "
                  f"Retrying in {delay_seconds} seconds...")
            time.sleep(delay_seconds)
            delay_seconds *= 2  # Exponential backoff
        else:
            print(f"[Weather Func] ERROR: All attempts ({retries}) failed for product='{product}' at lat={lat}, lon={lon}.")

    return None


def get_open_meteo_data(lat: float, lon: float) -> dict | None:
    """
    Fetches weather data from Open-Meteo with basic error handling.
    Returns parsed JSON data on success, None on failure.
    """
    # print(f"[Weather Fallback] Attempting fetch from Open-Meteo for lat={lat}, lon={lon}")
    try:
        # Request cloud cover at different levels, temperature, and relative humidity
        # We get hourly data for the next 7 days
        base_url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,cloud_cover_low,cloud_cover_mid,cloud_cover_high",
            "forecast_days": 7,
            "timezone": "UTC"  # Request data in UTC for easier processing
        }

        r = requests.get(base_url, params=params, timeout=DEFAULT_HTTP_TIMEOUT)  # 10-second timeout

        # --- Check for HTTP errors ---
        if r.status_code != 200:
            print(f"[Weather Fallback] ERROR (Open-Meteo): Received non-200 status code {r.status_code}")
            print(f"[Weather Fallback] Response text (first 200 chars): {r.text[:200]}")
            return None

        # --- Try to parse the JSON ---
        data = r.json()

        # --- Basic validation ---
        if not data or 'hourly' not in data or 'time' not in data['hourly']:
            print(f"[Weather Fallback] ERROR (Open-Meteo): Invalid data structure received.")
            return None

        # print(f"[Weather Fallback] Successfully fetched data from Open-Meteo.")
        return data

    except requests.exceptions.RequestException as e:
        # Handles timeouts, connection errors, etc.
        print(f"[Weather Fallback] ERROR (Open-Meteo): Request failed. Error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"[Weather Fallback] ERROR (Open-Meteo): Failed to decode JSON. Error: {e}")
        print(f"[Weather Fallback] Response text (first 200 chars): {r.text[:200]}")
        return None
    except Exception as e:
        # Catch any other unexpected errors
        print(f"[Weather Fallback] ERROR (Open-Meteo): An unexpected error occurred. Error: {e}")
        return None


def get_hybrid_weather_forecast(lat, lon):
    # --- Rounding and Cache Key (No change) ---
    rounded_lat = round(lat, 5)
    rounded_lon = round(lon, 5)
    cache_key = f"hybrid_{rounded_lat}_{rounded_lon}"
    # print(f"[Weather Func] Using cache key: '{cache_key}' for lat={lat}, lon={lon}")

    # --- Cache Check (No change) ---
    now = datetime.now(UTC)
    entry = weather_cache.get(cache_key) or {}
    last_good = entry.get('data')
    last_err_ts = entry.get('last_err_ts')
    if entry and 'expires' in entry:
        expires_dt = entry['expires']
        is_expired = False
        try:
            if expires_dt.tzinfo is not None:
                if now >= expires_dt: is_expired = True
            elif now.replace(tzinfo=None) >= expires_dt:
                is_expired = True
        except TypeError as te:
            print(f"[Weather Func] WARN: Timezone comparison error for key '{cache_key}': {te}")
            is_expired = True
        if not is_expired:
            return entry['data']

    # --- Helper Functions (No change) ---
    def _update_cache_ok(data, ttl_hours=3):
        expiry_time = datetime.now(UTC) + timedelta(hours=ttl_hours)
        weather_cache[cache_key] = {'data': data, 'expires': expiry_time, 'last_err_ts': None}
        # print(f"[Weather Func] Cache UPDATED for '{cache_key}', expires {expiry_time.isoformat()}")

    def _rate_limited_error(msg):
        nonlocal last_err_ts
        now_aware = datetime.now(UTC)
        if not last_err_ts or (now_aware - last_err_ts) > timedelta(minutes=15):
            print(msg)
            weather_cache.setdefault(cache_key, {})['last_err_ts'] = now_aware

    # --- START: NEW HYBRID FETCH LOGIC ---

    final_data_to_cache = None
    base_dataseries = {}

    # --- FIX: Initialize init_time and init_str to None in the outer scope ---
    init_time = None
    init_str = None
    # --- END FIX ---

    # === 1. Always fetch Open-Meteo for reliable cloud data ===
    # print(f"[Weather Func] Fetching base cloud data from Open-Meteo for key '{cache_key}'")
    open_meteo_data = get_open_meteo_data(lat, lon)

    if open_meteo_data and 'hourly' in open_meteo_data:
        # print(f"[Weather Func] Open-Meteo succeeded. Translating data...")
        try:
            translated_dataseries = {}  # Use a dict for easier merging
            om_hourly = open_meteo_data['hourly']
            times = om_hourly.get('time', [])

            # init_time is defined here (if successful)
            if times:
                # --- START FIX: Ensure parsed datetimes are offset-aware ---
                # 1. Parse the naive time (stripping 'Z' if it exists, fromisoformat handles T)
                first_time_naive = datetime.fromisoformat(times[0].split('Z')[0])
                # 2. Attach the UTC timezone to make it aware
                first_time_aware = first_time_naive.replace(tzinfo=UTC)
                # 3. Create the init_time (midnight), which is now guaranteed aware
                init_time = first_time_aware.replace(hour=0, minute=0, second=0, microsecond=0)
                init_str = init_time.strftime("%Y%m%d%H")
            else:
                init_time = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
                init_str = init_time.strftime("%Y%m%d%H")
                print("[Weather Func] WARN: Open-Meteo returned no times. Using current day as init.")

            for i, time_str in enumerate(times):
                # --- FIX: Apply the same logic to the loop variable ---
                current_time_naive = datetime.fromisoformat(time_str.split('Z')[0])
                current_time = current_time_naive.replace(tzinfo=UTC)
                timepoint = int((current_time - init_time).total_seconds() / 3600)
                # --- END FIX ---

                cc_low = om_hourly.get('cloud_cover_low', [0] * len(times))[i]
                cc_mid = om_hourly.get('cloud_cover_mid', [0] * len(times))[i]
                cc_high = om_hourly.get('cloud_cover_high', [0] * len(times))[i]
                total_cloud_percent = max(cc_low or 0, cc_mid or 0, cc_high or 0)

                if total_cloud_percent < 5:
                    cloudcover_1_9 = 1
                elif total_cloud_percent < 15:
                    cloudcover_1_9 = 2
                elif total_cloud_percent < 25:
                    cloudcover_1_9 = 3
                elif total_cloud_percent < 35:
                    cloudcover_1_9 = 4
                elif total_cloud_percent < 55:
                    cloudcover_1_9 = 5
                elif total_cloud_percent < 65:
                    cloudcover_1_9 = 6
                elif total_cloud_percent < 75:
                    cloudcover_1_9 = 7
                elif total_cloud_percent < 85:
                    cloudcover_1_9 = 8
                else:
                    cloudcover_1_9 = 9

                temp = om_hourly.get('temperature_2m', [None] * len(times))[i]
                rh = om_hourly.get('relative_humidity_2m', [None] * len(times))[i]

                block = {
                    "timepoint": timepoint,
                    "cloudcover": cloudcover_1_9,
                    "temp2m": temp,
                    "rh2m": rh,
                    "seeing": -9999,
                    "transparency": -9999,
                }
                translated_dataseries[timepoint] = block  # Store by timepoint

            base_dataseries = translated_dataseries
            # print(f"[Weather Func] Successfully translated {len(base_dataseries)} blocks from Open-Meteo.")

        except Exception as e:
            _rate_limited_error(f"[Weather Func] ERROR: Failed to translate Open-Meteo data: {e}")
            # Continue with empty base_dataseries

    else:
        _rate_limited_error(f"[Weather Func] ERROR: Open-Meteo (base) fetch failed for key '{cache_key}'.")
        # base_dataseries is still {}

    # === 2. Attempt to fetch 7Timer! 'astro' for seeing/transparency ===
    # print(f"[Weather Func] Fetching enhancement data (seeing) from 7Timer! 'astro' for key '{cache_key}'")
    astro_data_7t = get_weather_data_with_retries(lat, lon, product="astro")

    if astro_data_7t and astro_data_7t.get('dataseries'):
        # print("[DEBUG] 7Timer! RAW DATA (first 5 blocks):")
        # print(astro_data_7t['dataseries'][:5])
        # print(f"[Weather Func] 7Timer! 'astro' succeeded. Merging data...")

        # --- START FIX: Calculate 7Timer! init time ---
        astro_init_str = astro_data_7t.get('init')
        try:
            # Get the 7Timer! init time as a datetime object
            astro_init_time = datetime.strptime(astro_init_str, "%Y%m%d%H").replace(tzinfo=UTC)
        except (ValueError, TypeError, AttributeError):
            _rate_limited_error(
                f"[Weather Func] ERROR: 7Timer! 'astro' gave invalid init string: '{astro_init_str}'. Cannot merge seeing data.")
            astro_data_7t = None  # Treat as failed
        # --- END FIX ---

        if astro_data_7t:  # Check if it's still valid after parsing init

            # --- FIX: If init_time is STILL None, Open-Meteo failed. Use 7Timer's init as the base.
            if init_time is None:
                init_time = astro_init_time
                init_str = astro_init_str
                print(f"[Weather Func] WARN: Open-Meteo failed. Using 7Timer! init_time as base: {init_str}")
            # --- END FIX ---

            for ablk in astro_data_7t.get('dataseries', []):
                tp_7timer = ablk.get('timepoint')
                if tp_7timer is None: continue

                try:
                    # --- START FIX: Recalculate timepoint ---
                    # Get the absolute UTC time of the 7Timer! forecast block
                    abs_time = astro_init_time + timedelta(hours=int(tp_7timer))

                    # Calculate the timepoint relative to our *guaranteed* init_time
                    # This 'tp' is the correct key for our base_dataseries
                    tp = int((abs_time - init_time).total_seconds() / 3600)
                    # --- END FIX ---

                    if tp in base_dataseries:
                        # --- FIX: Only merge the keys we want from 7Timer! ---
                        # This preserves the correct 'timepoint' and high-res 'cloudcover'
                        # from the Open-Meteo block, while adding 'seeing' and 'transparency'.
                        if 'seeing' in ablk:
                            base_dataseries[tp]['seeing'] = ablk['seeing']
                        if 'transparency' in ablk:
                            base_dataseries[tp]['transparency'] = ablk['transparency']
                        # --- END FIX ---
                    else:
                        # Open-Meteo failed, use 7Timer! block as a fallback
                        # Add cloudcover placeholder if it doesn't exist
                        ablk.setdefault('cloudcover', 9)
                        base_dataseries[tp] = ablk

                except Exception as e:
                    print(f"[Weather Func] WARN: Skipping 7Timer! block, could not align timepoints. Error: {e}")

        # print(f"[Weather Func] Merge complete.")
    else:
        _rate_limited_error(
            f"[Weather Func] WARN: 7Timer! 'astro' (enhancement) fetch failed for key '{cache_key}'. Seeing data will be unavailable.")

    # --- END: NEW HYBRID FETCH LOGIC ---

    # --- Cache Update or Return Stale ---
    if base_dataseries:  # If we have *any* data (even just Open-Meteo)
        final_data_to_cache = {'init': init_str, 'dataseries': list(base_dataseries.values())}
        _update_cache_ok(final_data_to_cache, ttl_hours=3)
        return final_data_to_cache
    else:
        # Both failed. Return last good data if available.
        print(f"[Weather Func] All sources failed. Returning stale data (if available) for key '{cache_key}'.")
        return last_good or None


# =============================================================================
# Tier 3 Routes (migrated from nova/__init__.py)
# =============================================================================

@api_bp.route('/api/get_plot_data/<path:object_name>')
def get_plot_data(object_name):
    load_full_astro_context()  # Ensures g context is loaded if needed
    """
    API endpoint to provide all necessary data for client-side chart rendering.
    Returns:
      {
        times: [ISO strings incl. sentinel first/last],
        object_alt, object_az, moon_alt, moon_az, horizon_mask_alt: same length as times,
        sun_events: { current:{sunset,astronomical_dusk,...}, next:{astronomical_dawn,sunrise,...} },
        transit_time, date, timezone
      }
    """
    # --- 1) Resolve object RA/DEC ---
    data = get_ra_dec(object_name)  # Uses g.objects_map internally
    if not data:
        return jsonify({"error": _("Object data not found or invalid.")}), 404
    ra = data.get('RA (hours)')
    dec = data.get('DEC (deg)', data.get('DEC (degrees)'))
    if ra is None or dec is None:
        return jsonify({"error": _("RA/DEC missing for object.")}), 404
    try:
        ra = float(ra);
        dec = float(dec)
    except (ValueError, TypeError):
        return jsonify({"error": _("Invalid RA/DEC format for object.")}), 400

    # --- 2) Read params with SAFE fallbacks ---
    default_lat = getattr(g, "lat", None)
    default_lon = getattr(g, "lon", None)
    default_tz = getattr(g, "tz_name", "UTC")
    default_loc_name = getattr(g, "selected_location", "")

    plot_lat_str = request.args.get('plot_lat', '').strip()
    plot_lon_str = request.args.get('plot_lon', '').strip()
    plot_tz_name = request.args.get('plot_tz', '').strip()
    plot_loc_name = request.args.get('plot_loc_name', '').strip()

    try:
        # Determine the effective location name
        loc_name = plot_loc_name if plot_loc_name else default_loc_name

        # --- SIMULATION MODE SUPPORT ---
        # If sim_date is provided, use it to override the calculation anchor
        sim_date_str = request.args.get('sim_date')

        # Retrieve config for this location from the loaded context
        target_loc_conf = g.locations.get(loc_name, {}) if hasattr(g, 'locations') else {}

        # Resolve Lat/Lon/Tz:
        # 1. Use explicit query params if present (e.g. custom link)
        # 2. Fallback to the named location's config (e.g. from inspiration modal)
        # 3. Fallback to the session defaults
        lat = float(plot_lat_str) if plot_lat_str else target_loc_conf.get('lat', default_lat)
        lon = float(plot_lon_str) if plot_lon_str else target_loc_conf.get('lon', default_lon)
        tz_name = plot_tz_name if plot_tz_name else target_loc_conf.get('timezone', default_tz)

        if lat is None or lon is None:
            raise ValueError("Could not determine latitude or longitude.")
        if not tz_name:
            tz_name = "UTC"

        local_tz = pytz.timezone(tz_name)
    except Exception as e:
        print(f"[API Plot Data] Error parsing location parameters: {e}")
        return jsonify({"error": f"Invalid location or timezone data: {e}"}), 400

    if sim_date_str:
        try:
            now_local = local_tz.localize(datetime.strptime(sim_date_str, '%Y-%m-%d'))
        except ValueError:
            now_local = datetime.now(local_tz)
    else:
        now_local = datetime.now(local_tz)

        # Determine default date based on Noon-to-Noon logic (consistent with dashboard)
    if not sim_date_str and now_local.hour < 12:
        default_date = now_local.date() - timedelta(days=1)
    else:
        default_date = now_local.date()

    day = int(request.args.get('day', default_date.day))
    month = int(request.args.get('month', default_date.month))
    year = int(request.args.get('year', default_date.year))
    try:
        local_date_obj = datetime(year, month, day)
        local_date = local_date_obj.strftime('%Y-%m-%d')
    except ValueError:
        print(f"[API Plot Data] Error: Invalid date components ({year}-{month}-{day}). Using current date.")
        local_date_obj = now_local
        local_date = now_local.strftime('%Y-%m-%d')

    # --- 3) Build time grid and object series ---
    sampling_interval = getattr(g, 'sampling_interval', 15)
    times_local, times_utc = get_common_time_arrays(tz_name, local_date,
                                                    sampling_interval_minutes=sampling_interval)
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_obj = sky_coord.transform_to(altaz_frame)
    altitudes = altaz_obj.alt.deg
    azimuths = (altaz_obj.az.deg + 360.0) % 360.0

    # --- 4) Weather forecast section (DECOUPLED) ---
    # Weather is now fetched asynchronously by the client after the chart loads.
    # We just send an empty array so the data structure is consistent.
    weather_forecast_series = []
    is_offline = request.args.get('offline') == 'true'
    print(f"[API Plot Data] Skipping weather fetch (will be loaded by client). Offline mode: {is_offline}")
    # --- END Weather Section ---

    # --- 5) Horizon mask (use g context for user config) ---
    location_config = {}
    try:
        user_cfg = getattr(g, 'user_config', {}) or {}
        locations_cfg = user_cfg.get("locations", {}) or {}
        location_config = locations_cfg.get(loc_name, {})
    except Exception as e:
        print(f"[API Plot Data] WARN: Could not load user/location config from g context: {e}")

    horizon_mask = location_config.get("horizon_mask")

    altitude_threshold = 20
    try:
        user_cfg = getattr(g, 'user_config', {}) or {}
        altitude_threshold = user_cfg.get("altitude_threshold", 20)
    except Exception:
        pass
    if location_config.get("altitude_threshold") is not None:
        altitude_threshold = location_config.get("altitude_threshold")

    if horizon_mask and isinstance(horizon_mask, list) and len(horizon_mask) > 1:
        try:
            # Enforce the active threshold floor on the mask before processing
            clamped_mask = [[p[0], max(p[1], altitude_threshold)] for p in horizon_mask]
            sorted_mask = sorted(clamped_mask, key=lambda p: p[0])
            horizon_mask_altitudes = [interpolate_horizon(az, sorted_mask, altitude_threshold) for az in azimuths]
        except Exception as hm_err:
            print(f"[API Plot Data] ERROR calculating horizon mask altitudes: {hm_err}")
            horizon_mask_altitudes = [altitude_threshold] * len(azimuths)
    else:
        horizon_mask_altitudes = [altitude_threshold] * len(azimuths)

    # --- 6) Moon series ---
    moon_altitudes = [];
    moon_azimuths = []
    try:
        if not times_utc or len(times_utc) == 0:
            raise ValueError("times_utc array is empty or invalid for Moon calculation.")

        # Vectorized calculation (1 call instead of ~288 calls)
        t_ast_array = Time(times_utc)
        # altaz_frame is already defined in step 3 using times_utc, so we can reuse it or rely on the Time array
        moon_icrs = get_body('moon', t_ast_array, location=location)
        moon_altaz = moon_icrs.transform_to(altaz_frame)

        moon_altitudes = moon_altaz.alt.deg.tolist()
        moon_azimuths = ((moon_altaz.az.deg + 360.0) % 360.0).tolist()

    except Exception as moon_err:
        print(f"[API Plot Data] ERROR calculating Moon series: {moon_err}")
        moon_altitudes = [None] * len(altitudes)
        moon_azimuths = [None] * len(altitudes)

    # --- 7) Sun events and transit ---
    try:
        sun_events_curr = calculate_sun_events_cached(local_date, tz_name, lat, lon)
        next_date_str = (local_date_obj + timedelta(days=1)).strftime('%Y-%m-%d')
        sun_events_next = calculate_sun_events_cached(next_date_str, tz_name, lat, lon)
        transit_time_str = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
    except Exception as sun_err:
        print(f"[API Plot Data] ERROR calculating Sun events/transit: {sun_err}")
        sun_events_curr = {}
        sun_events_next = {}
        transit_time_str = "Error"

    # --- 8) Force exactly 24h window ---
    if not times_local:
        print("[API Plot Data] ERROR: times_local array is empty. Cannot generate plot.")
        return jsonify({"error": _("Could not generate time series for plot.")}), 500
    start_time = times_local[0]
    end_time = start_time + timedelta(hours=24)
    final_times_iso = [start_time.isoformat()] + [t.isoformat() for t in times_local] + [end_time.isoformat()]

    # --- Final plot data structure ---
    plot_data = {
        "times": final_times_iso,
        "object_alt": [None] + list(altitudes) + [None],
        "object_az": [None] + list(azimuths) + [None],
        "moon_alt": [None] + moon_altitudes + [None],
        "moon_az": [None] + moon_azimuths + [None],
        "horizon_mask_alt": [None] + horizon_mask_altitudes + [None],
        "sun_events": {"current": sun_events_curr, "next": sun_events_next},
        "transit_time": transit_time_str,
        "date": local_date,
        "timezone": tz_name,
        "weather_forecast": weather_forecast_series # This is now always []
    }

    return jsonify(plot_data)


@api_bp.route('/api/get_observable_objects')
def get_observable_objects():
    """
    Returns active objects observable tonight from the current location.
    Used by the secondary object comparison dropdown in graph view.

    Query params:
        exclude: Object name to exclude (the primary object being viewed)
        lat: Override latitude (from dashboard location)
        lon: Override longitude (from dashboard location)
        tz: Override timezone

    Returns:
        {
            "objects": [
                {
                    "object_name": str,
                    "common_name": str,
                    "observable_minutes": int,
                    "max_altitude": float
                },
                ...
            ]
        }

    Objects are:
        - Active projects only (active_project=True)
        - Have valid RA/DEC coordinates
        - Observable tonight (duration > 0)
        - Excludes the primary object if specified
        - Sorted by observable_minutes descending
        - Limited to top 20
    """
    from nova import load_global_request_context
    from modules.astro_calculations import calculate_observable_duration_vectorized, calculate_sun_events_cached

    load_global_request_context()
    load_full_astro_context()

    # Get exclusion parameter
    exclude_name = request.args.get('exclude', '').strip()

    # Get location context - prefer query params (dashboard location) over defaults
    lat_override = request.args.get('lat')
    lon_override = request.args.get('lon')
    tz_override = request.args.get('tz')
    location_name_param = request.args.get('location')
    # Get graph date parameters (day/month/year from user selection)
    day_param = request.args.get('day')
    month_param = request.args.get('month')
    year_param = request.args.get('year')

    try:
        if lat_override is not None:
            lat = float(lat_override)
        else:
            lat = getattr(g, 'lat', None)

        if lon_override is not None:
            lon = float(lon_override)
        else:
            lon = getattr(g, 'lon', None)

        if tz_override:
            tz_name = tz_override
        else:
            tz_name = getattr(g, 'tz_name', 'UTC')
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid location parameters: {e}", "objects": []}), 400

    altitude_threshold = 20

    # Get user's altitude threshold from config
    try:
        user_cfg = getattr(g, 'user_config', {}) or {}
        altitude_threshold = user_cfg.get("altitude_threshold", 20)
    except Exception:
        pass

    if lat is None or lon is None:
        return jsonify({"error": _("Location not configured"), "objects": []}), 400

    # Determine date for calculations - use passed-in graph date, not server's current time
    local_tz = pytz.timezone(tz_name)

    # Priority 1: Use the graph date passed from frontend (day/month/year)
    if day_param and month_param and year_param:
        try:
            # Parse the date components from strings
            day = int(day_param)
            month = int(month_param)
            year = int(year_param)
            calc_date = datetime(year, month, day).strftime('%Y-%m-%d')
        except (ValueError, TypeError) as e:
            print(f"[API Observable Objects] Invalid date parameters: {e}")
            return jsonify({"error": f"Invalid date parameters: {e}", "objects": []}), 400
    else:
        # Priority 2: Fallback to noon-to-noon logic with current server time (if no date passed)
        now_local = datetime.now(local_tz)
        if now_local.hour < 12:
            calc_date = (now_local.date() - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            calc_date = now_local.date().strftime('%Y-%m-%d')

    db = get_db()
    try:
        # Get current user
        if SINGLE_USER_MODE:
            username = "default"
        elif current_user.is_authenticated:
            username = current_user.username
        else:
            username = "guest_user"

        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            return jsonify({"error": _("User not found"), "objects": []}), 401

        # Query active objects with valid coordinates
        active_objects = db.query(AstroObject).filter_by(
            user_id=user.id,
            active_project=True
        ).filter(
            AstroObject.ra_hours != None,
            AstroObject.dec_deg != None
        ).all()

        # Get horizon mask from database with proper Location lookup
        horizon_mask = None
        location_key_for_cache = "default"

        # Priority 1: Use location name if provided (most accurate)
        if location_name_param:
            try:
                location_obj = db.query(Location).options(
                    selectinload(Location.horizon_points)
                ).filter_by(user_id=user.id, name=location_name_param).one_or_none()
                if location_obj:
                    horizon_mask = [[hp.az_deg, hp.alt_min_deg] for hp in
                                    sorted(location_obj.horizon_points, key=lambda p: p.az_deg)]
                    location_key_for_cache = location_obj.name.lower().replace(' ', '_')
                    # Ensure lat/lon/tz match the location's configured values
                    lat = location_obj.lat
                    lon = location_obj.lon
                    tz_name = location_obj.timezone
                    if location_obj.altitude_threshold is not None:
                        altitude_threshold = location_obj.altitude_threshold
            except Exception as e:
                print(f"[API Observable Objects] Error fetching location from DB: {e}")

        # Priority 2: Fallback to finding location by lat/lon/tz (if no location name provided)
        if horizon_mask is None and hasattr(g, 'locations') and isinstance(g.locations, dict):
            for loc_name, loc_details in g.locations.items():
                if (abs(loc_details.get('lat', 999) - lat) < 0.001 and
                    abs(loc_details.get('lon', 999) - lon) < 0.001 and
                    loc_details.get('timezone') == tz_name):
                    horizon_mask = loc_details.get('horizon_mask')
                    location_key_for_cache = loc_name.lower().replace(' ', '_')
                    break

        # Cache key: username + date + location + threshold uniquely identify the computation
        obs_cache_key = (
            f"obs_objects:{username}:{calc_date}:{lat:.4f}:{lon:.4f}"
            f":{altitude_threshold}:{location_key_for_cache}"
        )

        if obs_cache_key in observable_objects_cache:
            full_list = observable_objects_cache[obs_cache_key]
        else:
            full_list = []
            for obj in active_objects:
                try:
                    duration_td, max_alt, _, _ = calculate_observable_duration_vectorized(
                        ra=obj.ra_hours,
                        dec=obj.dec_deg,
                        lat=lat,
                        lon=lon,
                        local_date=calc_date,
                        tz_name=tz_name,
                        altitude_threshold=altitude_threshold,
                        horizon_mask=horizon_mask
                    )

                    observable_minutes = int(duration_td.total_seconds() / 60)

                    if observable_minutes > 0:
                        full_list.append({
                            "object_name": obj.object_name,
                            "common_name": obj.common_name or obj.object_name,
                            "observable_minutes": observable_minutes,
                            "max_altitude": round(max_alt, 1)
                        })
                except Exception as e:
                    print(f"[API Observable Objects] Error calculating "
                          f"for {obj.object_name}: {e}")
                    continue
            observable_objects_cache[obs_cache_key] = full_list

        # Post-filter: exclude primary object, sort, limit
        observable_list = [
            o for o in full_list
            if o["object_name"] != exclude_name
        ]
        observable_list.sort(
            key=lambda x: x['observable_minutes'], reverse=True
        )
        observable_list = observable_list[:20]

        return jsonify({"objects": observable_list})

    except Exception as e:
        print(f"[API Observable Objects] Error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e), "objects": []}), 500


@api_bp.route('/api/get_weather_forecast')
def get_weather_forecast_api():
    """
    API endpoint to exclusively fetch and return processed weather forecast data.
    """
    from nova import load_global_request_context

    # 1. Get lat, lon, tz from request args, with fallbacks to 'g'
    # We don't need the full astro context, just the location defaults
    if not hasattr(g, 'lat'):
        # A minimal load if 'g' context is missing (e.g., direct API hit)
        load_global_request_context()
        load_full_astro_context()

    try:
        # Use request args if provided and valid, otherwise fallback to g
        lat_str = request.args.get('lat')
        lon_str = request.args.get('lon')
        tz_name_req = request.args.get('tz')

        lat = float(lat_str) if lat_str else g.lat
        lon = float(lon_str) if lon_str else g.lon
        tz_name = tz_name_req if tz_name_req else g.tz_name

        if lat is None or lon is None: raise ValueError("Lat/Lon not found.")
        if not tz_name: tz_name = "UTC"

        local_tz = pytz.timezone(tz_name)  # Validate tz
    except Exception as e:
        return jsonify({"error": f"Invalid location/tz: {e}"}), 400

    # 2. Call the hybrid weather function (this uses the cache)
    print(f"[API Weather] Fetching hybrid weather for lat={lat}, lon={lon}")
    weather_data = get_hybrid_weather_forecast(lat, lon)

    # 3. Process the data (copying logic from old get_plot_data)
    weather_forecast_series = []
    if weather_data and isinstance(weather_data.get('dataseries'), list):
        try:
            init_str = weather_data.get('init', '')
            try:
                init_time = datetime.strptime(init_str, "%Y%m%d%H").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                init_time = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
                print(f"[API Weather] WARN: Invalid init string. Falling back to now: {init_time.isoformat()}")

            for block in weather_data['dataseries']:
                timepoint_hours = block.get('timepoint')
                if timepoint_hours is None: continue
                try:
                    start_time = init_time + timedelta(hours=int(timepoint_hours))

                    # --- FIX: The hybrid cache *always* produces 1-hour blocks. ---
                    # Remove the bad 3-hour guessing logic.
                    end_time = start_time + timedelta(hours=1)
                    # --- END FIX ---

                    seeing_val = block.get("seeing")
                    transparency_val = block.get("transparency")

                    # Pass the raw values (e.g., 5 or -9999) directly.
                    # The JavaScript is built to handle these.
                    processed_block = {
                        "start": start_time.isoformat(),
                        "end": end_time.isoformat(),
                        "cloudcover": block.get("cloudcover"),
                        "seeing": seeing_val,
                        "transparency": transparency_val,
                    }
                    # Append the entire block, not a stripped version.
                    weather_forecast_series.append(processed_block)

                except Exception as e:
                    print(f"[API Weather] WARN: Skipping bad block: {e}")
                    continue  # Skip bad block
        except Exception as e:
            print(f"[API Weather] ERROR: Failed processing dataseries: {e}")
            traceback.print_exc()
            weather_forecast_series = []  # Clear on error
    else:
        print("[API Weather] No dataseries found in weather_data.")

    # 4. Return the processed forecast (with ISO strings)
    return jsonify({"weather_forecast": weather_forecast_series})


@api_bp.route('/api/get_object_data/<path:object_name>')
def get_object_data(object_name):
    # --- 1. Determine User (No change needed here) ---
    if SINGLE_USER_MODE:
        username = "default"
    elif current_user.is_authenticated:
        username = current_user.username
    elif request.args.get('location'):  # Allow guest if location provided
        username = "guest_user"
    else:
        # Deny guest if no location specified (cannot determine defaults)
        return jsonify({
            'Object': object_name, 'Common Name': "Error: Authentication required.", 'error': True
        }), 401

    db = get_db()
    try:
        # --- 2. Get User Record (No change needed here) ---
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            return jsonify({
                'Object': object_name, 'Common Name': "Error: User not found.", 'error': True
            }), 404

        # --- 3. Determine Location to Use (Modified: Query DB directly) ---
        requested_location_name = request.args.get('location')
        selected_location = None
        current_location_config = {}

        if requested_location_name:
            # Try to load the specific location requested
            selected_location = db.query(Location).filter_by(user_id=user.id, name=requested_location_name).options(
                selectinload(Location.horizon_points)).one_or_none()
            if not selected_location:
                return jsonify(
                    {'Object': object_name, 'Common Name': "Error: Requested location not found.", 'error': True}), 404
        else:
            # Fallback to the user's default location
            selected_location = db.query(Location).filter_by(user_id=user.id, is_default=True).options(
                selectinload(Location.horizon_points)).one_or_none()
            # If no default, try the first active one
            if not selected_location:
                selected_location = db.query(Location).filter_by(user_id=user.id, active=True).options(
                    selectinload(Location.horizon_points)).order_by(Location.id).first()

        if not selected_location:
            return jsonify({'Object': object_name, 'Common Name': "Error: No valid location configured or selected.",
                            'error': True}), 400

        # Extract details from the selected location object
        lat = selected_location.lat
        lon = selected_location.lon
        tz_name = selected_location.timezone
        selected_location_name = selected_location.name
        # Build the config-like dict for horizon mask etc.
        horizon_mask = [[hp.az_deg, hp.alt_min_deg] for hp in
                        sorted(selected_location.horizon_points, key=lambda p: p.az_deg)]
        current_location_config = {
            "lat": lat, "lon": lon, "timezone": tz_name,
            "altitude_threshold": selected_location.altitude_threshold,
            "horizon_mask": horizon_mask
            # Add other fields if needed by calculations below
        }
        # --- End Location Determination ---

        # --- 4. Query ONLY the specific object ---
        obj_record = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        # Handle case where object isn't found for this user
        if not obj_record:
            # Optionally try SIMBAD as a fallback *here* if desired,
            # or just return not found. Let's return not found for now.
            return jsonify({
                'Object': object_name, 'Common Name': f"Error: Object '{object_name}' not found in your config.",
                'error': True
            }), 404

        # Extract necessary details
        ra = obj_record.ra_hours
        dec = obj_record.dec_deg
        if ra is None or dec is None:
            return jsonify({
                'Object': object_name, 'Common Name': f"Error: RA/DEC missing for '{object_name}'.",
                'error': True
            }), 400

        # --- 5. Perform Calculations (FIXED DATE LOGIC) ---
        local_tz = pytz.timezone(tz_name)
        current_datetime_local = datetime.now(local_tz)

        # Determine "Observing Night" Date
        # If it's before noon, we associate this time with the previous night's session
        # to ensure the time array (which starts at noon) covers the current moment.
        if current_datetime_local.hour < 12:
            local_date = (current_datetime_local.date() - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            local_date = current_datetime_local.strftime('%Y-%m-%d')

        # Load UI Prefs specifically for altitude_threshold and sampling_interval
        prefs_record = db.query(UiPref).filter_by(user_id=user.id).first()
        user_prefs_dict = {}
        if prefs_record and prefs_record.json_blob:
            try:
                user_prefs_dict = json.loads(prefs_record.json_blob)
            except:
                pass
        altitude_threshold = user_prefs_dict.get("altitude_threshold", 20)
        # Use location-specific threshold if available
        if selected_location.altitude_threshold is not None:
            altitude_threshold = selected_location.altitude_threshold

        # Determine sampling interval based on mode
        sampling_interval = 15  # Default
        if SINGLE_USER_MODE:
            sampling_interval = user_prefs_dict.get('sampling_interval_minutes') or 15
        else:
            sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))

        cache_key = f"{username}_{object_name.lower().replace(' ', '_')}_{local_date}_{lat:.4f}_{lon:.4f}_{altitude_threshold}_{sampling_interval}"

        # Calculate or retrieve cached nightly data (logic remains similar)
        if cache_key not in nightly_curves_cache:
            times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)
            location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
            altaz_frame = AltAz(obstime=times_utc, location=location)
            altitudes = sky_coord.transform_to(altaz_frame).alt.deg
            azimuths = sky_coord.transform_to(altaz_frame).az.deg
            transit_time = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
            obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
                ra, dec, lat, lon, local_date, tz_name, altitude_threshold, sampling_interval,
                horizon_mask=horizon_mask  # Pass the specific mask
            )
            fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)
            alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)
            is_obstructed_at_11pm = False
            if horizon_mask and len(horizon_mask) > 1:
                sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
                required_altitude_11pm = interpolate_horizon(az_11pm, sorted_mask, altitude_threshold)
                if alt_11pm >= altitude_threshold and alt_11pm < required_altitude_11pm:
                    is_obstructed_at_11pm = True

            nightly_curves_cache[cache_key] = {
                "times_local": times_local, "altitudes": altitudes, "azimuths": azimuths, "transit_time": transit_time,
                "obs_duration_minutes": int(obs_duration.total_seconds() / 60) if obs_duration else 0,
                "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
                "alt_11pm": f"{alt_11pm:.2f}", "az_11pm": f"{az_11pm:.2f}",
                "is_obstructed_at_11pm": is_obstructed_at_11pm
            }

        cached_night_data = nightly_curves_cache[cache_key]

        # Calculate current position and trend (logic remains similar)
        now_utc = datetime.now(pytz.utc)
        time_diffs = [abs((t - now_utc).total_seconds()) for t in cached_night_data["times_local"]]
        current_index = np.argmin(time_diffs)
        current_alt = cached_night_data["altitudes"][current_index]
        current_az = cached_night_data["azimuths"][current_index]
        next_index = min(current_index + 1, len(cached_night_data["altitudes"]) - 1)
        next_alt = cached_night_data["altitudes"][next_index]
        trend = '–'
        if abs(next_alt - current_alt) > 0.01: trend = '↑' if next_alt > current_alt else '↓'

        # Check obstruction now
        is_obstructed_now = False
        if horizon_mask and len(horizon_mask) > 1:
            sorted_mask = sorted(horizon_mask, key=lambda p: p[0])
            required_altitude_now = interpolate_horizon(current_az, sorted_mask, altitude_threshold)
            if current_alt >= altitude_threshold and current_alt < required_altitude_now:
                is_obstructed_now = True

        # Calculate Moon separation (logic remains similar)
        time_obj = Time(datetime.now(pytz.utc))
        location_for_moon = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        moon_coord = get_body('moon', time_obj, location_for_moon)
        obj_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        frame = AltAz(obstime=time_obj, location=location_for_moon)
        angular_sep = obj_coord_sky.transform_to(frame).separation(moon_coord.transform_to(frame)).deg

        is_obstructed_at_11pm = cached_night_data.get('is_obstructed_at_11pm', False)

        # --- START OF NEW CALCULATIONS ---
        # 1. Calculate Best Month from RA
        # (RA 0h -> Oct, RA 2h -> Nov, ... RA 22h -> Sep)
        RA_to_Month_Opposition = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"]
        best_month_idx = int(ra / 2) % 12  # Simple floor(ra/2)
        best_month_str = RA_to_Month_Opposition[best_month_idx]

        # 2. Calculate Max Culmination Altitude from Dec and Lat
        max_culmination_alt = 90.0 - abs(lat - dec)
        # --- END OF NEW CALCULATIONS ---

        # --- 6. Assemble JSON using the single object record ---

        # 1. Get all static data from the model's to_dict() method
        single_object_data = obj_record.to_dict()

        # 2. Add all dynamic (calculated) data to that dictionary
        single_object_data.update({
            'Altitude Current': f"{current_alt:.2f}",
            'Azimuth Current': f"{current_az:.2f}",
            'Trend': trend,
            'Altitude 11PM': cached_night_data['alt_11pm'],
            'Azimuth 11PM': cached_night_data['az_11pm'],
            'Transit Time': cached_night_data['transit_time'],
            'Observable Duration (min)': cached_night_data['obs_duration_minutes'],
            'Max Altitude (°)': cached_night_data['max_altitude'],
            'Angular Separation (°)': round(angular_sep),
            'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
            'is_obstructed_now': is_obstructed_now,
            'is_obstructed_at_11pm': is_obstructed_at_11pm,
            'best_month_ra': best_month_str,
            'max_culmination_alt': max_culmination_alt,
            'error': False
        })

        # 3. Ensure 'Project' key has a fallback for the UI
        single_object_data.setdefault('Project', 'none')

        return jsonify(single_object_data)

    except Exception as e:
        print(f"ERROR in get_object_data for '{object_name}': {e}")
        traceback.print_exc()
        # Return a generic error structure
        return jsonify({
            'Object': object_name, 'Common Name': "Error processing request.", 'error': True
        }), 500


@api_bp.route('/api/get_desktop_data_batch')
def get_desktop_data_batch():
    # --- Manual Auth Check for Guest Support ---
    if not (current_user.is_authenticated or SINGLE_USER_MODE or getattr(g, 'is_guest', False)):
        return jsonify({"error": "Unauthorized"}), 401
    """
    Batch processor for the desktop dashboard.
    Calculates data for 50 objects internally to prevent HTTP request flooding.
    """
    load_full_astro_context()

    # 1. Get Pagination Params
    try:
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 50))
    except ValueError:
        offset = 0
        limit = 50

    user = g.db_user
    if not user: return jsonify({"error": "User not found", "results": []}), 404

    # 2. Determine Location
    requested_loc_name = request.args.get('location')
    # Fallback logic: Request Param -> Session (g) -> Default
    if not requested_loc_name:
        requested_loc_name = g.selected_location

    db = get_db()
    try:
        location_obj = db.query(Location).options(
            selectinload(Location.horizon_points)
        ).filter_by(user_id=user.id, name=requested_loc_name).one_or_none()

        if not location_obj: return jsonify({"error": "Location not found", "results": []}), 404

        # 3. Get Object Slice (Only Enabled Objects)
        # OPTIMIZATION: Fetch batch first to determine if there are more results
        batch_objects = db.query(AstroObject).filter_by(user_id=user.id, enabled=True)\
            .order_by(AstroObject.object_name)\
            .offset(offset)\
            .limit(limit)\
            .all()

        # Determine has_more flag based on whether we got a full page
        has_more = len(batch_objects) == limit

        # Only fetch total_count if offset is 0 (first page) to avoid double query on pagination
        # For subsequent pages, client can rely on has_more flag
        total_count = None
        if offset == 0:
            total_count = db.query(func.count(AstroObject.id))\
                .filter_by(user_id=user.id, enabled=True)\
                .scalar() or 0

        # 4. Prepare Calculation Variables
        results = []
        lat, lon, tz_name = location_obj.lat, location_obj.lon, location_obj.timezone
        horizon_mask = [[hp.az_deg, hp.alt_min_deg] for hp in
                        sorted(location_obj.horizon_points, key=lambda p: p.az_deg)]
        altitude_threshold = location_obj.altitude_threshold if location_obj.altitude_threshold is not None else g.user_config.get(
            "altitude_threshold", 20)

        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.utc

            # --- SIMULATION MODE ---
        sim_date_str = request.args.get('sim_date')
        if sim_date_str:
            try:
                # Use current wall-clock time combined with simulated date
                sim_date = datetime.strptime(sim_date_str, '%Y-%m-%d').date()
                now_time = datetime.now(local_tz).time()
                current_datetime_local = local_tz.localize(datetime.combine(sim_date, now_time))
            except ValueError:
                current_datetime_local = datetime.now(local_tz)
        else:
            current_datetime_local = datetime.now(local_tz)

        # Determine "Observing Night" Date
        # If it's before noon, we associate this time with the previous night's session
        # to ensure the time array (which starts at noon) covers the current moment.
        if current_datetime_local.hour < 12:
            local_date = (current_datetime_local.date() - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            local_date = current_datetime_local.strftime('%Y-%m-%d')

        sampling_interval = 15 if SINGLE_USER_MODE else int(os.environ.get('CALCULATION_PRECISION', 15))
        fixed_time_utc_str = get_utc_time_for_local_11pm(tz_name)

        # Moon / Ephem Prep
        time_obj_now = Time(current_datetime_local.astimezone(pytz.utc))
        loc_earth = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        moon_coord = get_body('moon', time_obj_now, loc_earth)
        frame_now = AltAz(obstime=time_obj_now, location=loc_earth)
        moon_in_frame = moon_coord.transform_to(frame_now)
        location_key = location_obj.name.lower().replace(' ', '_')

        # 5. Process Batch
        for obj in batch_objects:
            try:
                item = obj.to_dict()
                ra, dec = obj.ra_hours, obj.dec_deg

                if ra is None or dec is None:
                    item.update({'error': True, 'Common Name': 'Error: Missing RA/DEC'})
                    results.append(item)
                    continue

                    # --- GEOMETRIC PRE-FILTER (Live Request) ---
                calc_invisible = g.user_config.get("calc_invisible", False)

                if not calc_invisible:
                    max_culm_geo = 90.0 - abs(lat - dec)
                    if max_culm_geo < altitude_threshold:
                        # Skip heavy math, return "greyed out" state immediately
                        item.update({
                            'Altitude Current': 'N/A', 'Azimuth Current': 'N/A', 'Trend': '–',
                            'Altitude 11PM': 'N/A', 'Azimuth 11PM': 'N/A', 'Transit Time': 'N/A',
                            'Observable Duration (min)': 0,
                            'Max Altitude (°)': round(max_culm_geo, 1),
                            'Angular Separation (°)': 'N/A',
                            'is_obstructed_now': False,
                            'is_geometrically_impossible': True,
                            'best_month_ra':
                                ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"][
                                    int(ra / 2) % 12],
                            'max_culmination_alt': round(max_culm_geo, 1),
                            'error': False
                        })
                        results.append(item)
                        continue

                # Calculate / Cache
                cache_key = f"{user.username}_{obj.object_name.lower().replace(' ', '_')}_{local_date}_{lat:.4f}_{lon:.4f}_{altitude_threshold}_{sampling_interval}"

                cached = None
                if cache_key in nightly_curves_cache:
                    cached = nightly_curves_cache[cache_key]
                else:
                    obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
                        ra, dec, lat, lon, local_date, tz_name, altitude_threshold, sampling_interval, horizon_mask
                    )
                    transit = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)
                    alt_11, az_11 = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)

                    is_obst_11 = False
                    if horizon_mask:
                        req_alt = interpolate_horizon(az_11, sorted(horizon_mask, key=lambda p: p[0]),
                                                      altitude_threshold)
                        if alt_11 >= altitude_threshold and alt_11 < req_alt: is_obst_11 = True

                    times_local, times_utc = get_common_time_arrays(tz_name, local_date, sampling_interval)
                    sky_c = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    aa_frame = AltAz(obstime=times_utc, location=loc_earth)
                    alts = sky_c.transform_to(aa_frame).alt.deg
                    azs = sky_c.transform_to(aa_frame).az.deg

                    cached = {
                        "times_local": times_local, "altitudes": alts, "azimuths": azs,
                        "transit_time": transit,
                        "obs_duration_minutes": int(obs_duration.total_seconds() / 60) if obs_duration else 0,
                        "max_altitude": round(max_alt, 1) if max_alt is not None else "N/A",
                        "alt_11pm": f"{alt_11:.2f}", "az_11pm": f"{az_11:.2f}",
                        "is_obstructed_at_11pm": is_obst_11
                    }
                    nightly_curves_cache[cache_key] = cached

                # Current Position (Fast Interpolation)
                # Use the effective simulation time converted to UTC
                now_utc = current_datetime_local.astimezone(pytz.utc)
                idx = np.argmin([abs((t - now_utc).total_seconds()) for t in cached["times_local"]])
                cur_alt = cached["altitudes"][idx]
                cur_az = cached["azimuths"][idx]

                next_idx = min(idx + 1, len(cached["altitudes"]) - 1)
                trend = '–'
                if abs(cached["altitudes"][next_idx] - cur_alt) > 0.01:
                    trend = '↑' if cached["altitudes"][next_idx] > cur_alt else '↓'

                is_obst_now = False
                if horizon_mask:
                    req = interpolate_horizon(cur_az, sorted(horizon_mask, key=lambda p: p[0]), altitude_threshold)
                    if cur_alt >= altitude_threshold and cur_alt < req: is_obst_now = True

                sep = "N/A"
                try:
                    sky_c = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    sep = round(sky_c.transform_to(frame_now).separation(moon_in_frame).deg)
                except:
                    pass

                best_m = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"][
                    int(ra / 2) % 12]
                max_culm = 90.0 - abs(lat - dec)

                item.update({
                    'Altitude Current': f"{cur_alt:.2f}",
                    'Azimuth Current': f"{cur_az:.2f}",
                    'Trend': trend,
                    'Altitude 11PM': cached['alt_11pm'],
                    'Azimuth 11PM': cached['az_11pm'],
                    'Transit Time': cached['transit_time'],
                    'Observable Duration (min)': cached['obs_duration_minutes'],
                    'Max Altitude (°)': cached['max_altitude'],
                    'Angular Separation (°)': sep,
                    'is_obstructed_now': is_obst_now,
                    'is_obstructed_at_11pm': cached['is_obstructed_at_11pm'],
                    'best_month_ra': best_m,
                    'max_culmination_alt': max_culm,
                    'error': False
                })
                results.append(item)

            except Exception as e:
                print(f"Batch Error {obj.object_name}: {e}")
                results.append({'Object': obj.object_name, 'Common Name': 'Error: Calc failed', 'error': True})

        response_data = {
            "results": results,
            "total": total_count,
            "has_more": has_more,
            "offset": offset,
            "limit": limit
        }

        return jsonify(response_data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route('/api/get_yearly_heatmap_chunk')
def get_yearly_heatmap_chunk():
    # --- Manual Auth Check for Guest Support ---
    if not (current_user.is_authenticated or SINGLE_USER_MODE or getattr(g, 'is_guest', False)):
        return jsonify({"error": "Unauthorized"}), 401
    load_full_astro_context()

    # NOTE: heatmap_viewed analytics removed - this is a chunk API called multiple times per page
    try:
        # 1. Parse Request Parameters
        chunk_idx = int(request.args.get('chunk_index', 0))  # 0 to 11 (Month index)
        total_chunks = 12

        # Prefer explicit location name from request
        req_loc_name = request.args.get('location_name')
        if req_loc_name and req_loc_name in g.locations:
            loc_data = g.locations[req_loc_name]
            lat = loc_data['lat']
            lon = loc_data['lon']
            tz_name = loc_data['timezone']
            horizon_mask = loc_data.get('horizon_mask')
            selected_loc_key = req_loc_name
        else:
            lat = float(request.args.get('lat', g.lat))
            lon = float(request.args.get('lon', g.lon))
            tz_name = request.args.get('tz', g.tz_name)
            horizon_mask = None
            if g.selected_location and g.selected_location in g.locations:
                horizon_mask = g.locations[g.selected_location].get('horizon_mask')
            selected_loc_key = g.selected_location or "default"

        local_tz = pytz.timezone(tz_name)

        # 2. GENERATE CACHE KEY (Per Chunk)
        db = get_db()
        user_id = g.db_user.id
        obj_count = db.query(AstroObject).filter_by(user_id=user_id, enabled=True).count()
        loc_safe = selected_loc_key.lower().replace(' ', '_')

        # Base filename
        base_cache_name = f"heatmap_v5_{user_id}_{loc_safe}_{obj_count}"
        # Specific chunk filename
        chunk_cache_filename = os.path.join(CACHE_DIR, f"{base_cache_name}.part{chunk_idx}.json")

        # 3. FAST PATH: Read existing chunk from disk
        if os.path.exists(chunk_cache_filename):
            mtime = os.path.getmtime(chunk_cache_filename)
            if (time.time() - mtime) < 86400:  # 24 hours
                try:
                    with open(chunk_cache_filename, 'r') as f:
                        # print(f"[HEATMAP] Serving chunk {chunk_idx} from cache: {chunk_cache_filename}")
                        return jsonify(json.load(f))
                except Exception as e:
                    print(f"[HEATMAP] Error reading chunk cache: {e}")

        # 4. SLOW PATH: Live Calculation
        now = datetime.now(local_tz)
        start_date_year = now.date() - timedelta(days=now.weekday())

        weeks_per_chunk = 52 // total_chunks
        remainder = 52 % total_chunks
        start_week = chunk_idx * weeks_per_chunk + min(chunk_idx, remainder)
        end_week = start_week + weeks_per_chunk + (1 if chunk_idx < remainder else 0)

        weeks_x = []
        target_dates = []
        moon_phases = []

        for i in range(start_week, end_week):
            d = start_date_year + timedelta(weeks=i)
            weeks_x.append(d.strftime('%b %d'))
            target_dates.append(d.strftime('%Y-%m-%d'))
            try:
                dt_moon = local_tz.localize(datetime.combine(d, datetime.min.time())).astimezone(pytz.utc)
                m = ephem.Moon(dt_moon)
                moon_phases.append(round(m.phase, 1))
            except:
                moon_phases.append(0)

        # --- Object Selection ---
        altitude_threshold = g.user_config.get("altitude_threshold", 20)
        sampling_interval = 60

        all_objects = db.query(AstroObject).filter_by(user_id=user_id, enabled=True).all()

        # Validity Check
        valid_objects = [o for o in all_objects if o.ra_hours is not None and o.dec_deg is not None]

        # Geometric Visibility Filter (Consistent across all chunks)
        visible_objects = []
        for obj in valid_objects:
            dec = float(obj.dec_deg)
            # Max theoretical altitude = 90 - |Lat - Dec|
            max_theoretical_alt = 90 - abs(lat - dec)
            if max_theoretical_alt >= altitude_threshold:
                visible_objects.append(obj)

        visible_objects.sort(key=lambda x: float(x.ra_hours))

        # --- Data Generation ---
        z_scores_chunk = []
        y_names = []
        meta_ids = []
        meta_active = []
        meta_types = []
        meta_cons = []
        meta_mags = []
        meta_sizes = []
        meta_sbs = []

        for obj in visible_objects:
            ra = float(obj.ra_hours)
            dec = float(obj.dec_deg)
            obj_scores = []

            for i, date_str in enumerate(target_dates):
                obs_dur, max_alt, _, _ = calculate_observable_duration_vectorized(
                    ra, dec, lat, lon, date_str, tz_name,
                    altitude_threshold, sampling_interval, horizon_mask
                )

                duration_mins = obs_dur.total_seconds() / 60 if obs_dur else 0

                if max_alt is None or max_alt < altitude_threshold or duration_mins < 45:
                    score = 0
                else:
                    norm_alt = min((max_alt - altitude_threshold) / (90 - altitude_threshold), 1.0)
                    norm_dur = min(duration_mins / 480, 1.0)
                    score = (0.4 * norm_alt + 0.6 * norm_dur) * 100

                    current_moon = moon_phases[i]
                    if current_moon > 60:
                        penalty_factor = ((current_moon - 60) / 40) * 0.9
                        score *= (1 - penalty_factor)

                obj_scores.append(round(score, 1))

            z_scores_chunk.append(obj_scores)

            # Metadata
            display_name = obj.common_name or obj.object_name
            if obj.type: display_name += f" [{obj.type}]"
            y_names.append(display_name)
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

        result_data = {
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
            "sbs": meta_sbs
        }

        # 5. SAVE CHUNK TO DISK
        try:
            with open(chunk_cache_filename, 'w') as f:
                json.dump(result_data, f)
            print(f"[HEATMAP] Saved chunk {chunk_idx} to {chunk_cache_filename}")
        except Exception as e:
            print(f"[HEATMAP] Failed to write cache file: {e}")

        return jsonify(result_data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
