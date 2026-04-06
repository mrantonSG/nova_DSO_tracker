import json
import os
import traceback
from datetime import datetime, UTC

from flask import (
    Blueprint, request, jsonify, g, url_for,
    current_app, send_from_directory
)
from flask_login import login_required, current_user
from sqlalchemy import and_, or_

from nova.config import SINGLE_USER_MODE, TELEMETRY_DEBUG_STATE, LATEST_VERSION_INFO
from nova.helpers import get_db, load_full_astro_context, get_locale
from nova.models import (
    DbUser, AstroObject, JournalSession, Project,
    Component, SavedView, SavedFraming, Rig
)
from nova.analytics import record_event
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
