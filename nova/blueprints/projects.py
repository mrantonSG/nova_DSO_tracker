"""
Nova DSO Tracker - Projects Blueprint
-------------------------------------
Routes for project management: detail view, report page, and deletion.
"""

# =============================================================================
# Standard Library Imports
# =============================================================================
import os
import traceback
from datetime import datetime

# =============================================================================
# Third-Party Imports
# =============================================================================
import bleach
from bleach.css_sanitizer import CSSSanitizer
from flask import (
    Blueprint, render_template, redirect, url_for, flash,
    request, g
)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from sqlalchemy import func

# =============================================================================
# Nova Package Imports (no circular import)
# =============================================================================
from nova import SINGLE_USER_MODE  # Import from nova for test patching compatibility
from nova.config import UPLOAD_FOLDER
from nova.models import (
    DbUser, Project, JournalSession, AstroObject
)
from nova.helpers import (
    get_db, load_full_astro_context, read_log_content
)
from nova.analytics import record_event
from nova.report_graphs import generate_session_charts


# =============================================================================
# Blueprint Definition
# =============================================================================
projects_bp = Blueprint('projects', __name__)


# =============================================================================
# Project Routes
# =============================================================================

@projects_bp.route('/project/<string:project_id>', methods=['GET', 'POST'])
@login_required
def project_detail(project_id):
    from nova import _handle_project_image_upload  # Lazy import to avoid circular

    load_full_astro_context()  # Ensures g.db_user is loaded
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()

    try:
        project = db.query(Project).filter_by(id=project_id, user_id=g.db_user.id).one_or_none()
        if not project:
            flash(_("Error deleting old image."), "warning")

        # --- Aggregated Statistics ---
        total_integration_minutes = db.query(
            func.sum(JournalSession.calculated_integration_time_minutes)
        ).filter_by(project_id=project_id, user_id=g.db_user.id).scalar() or 0

        # Format integration time (e.g., 10h 30m)
        total_minutes = int(total_integration_minutes)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        total_integration_str = f"{hours}h {minutes}m"

        # Fetch all linked sessions eagerly to display them
        sessions = db.query(JournalSession).filter_by(project_id=project_id, user_id=g.db_user.id).order_by(
            JournalSession.date_utc.desc()).all()

        # --- Handle POST Request (Update Project) ---
        if request.method == 'POST':

            # 1. Handle image deletion
            if request.form.get('delete_final_image') == '1' and project.final_image_file:
                try:
                    os.remove(os.path.join(UPLOAD_FOLDER, username, project.final_image_file))
                    project.final_image_file = None
                except Exception as e:
                    print(f"Error deleting final image: {e}")
                    flash(_("Error deleting old image."), "warning")

            # 2. Handle image upload (returns new/existing filename)
            new_filename = _handle_project_image_upload(
                request.files.get('final_image'),
                project.id,
                username,
                project.final_image_file
            )

            # 3. Update all new fields (including the rich text from Trix)
            project.name = request.form.get('name')
            project.target_object_name = request.form.get('target_object_id')  # Note: Renamed from 'target_object_name'
            project.status = request.form.get('status')

            # Rich text notes (Trix content is received as raw HTML)
            project.goals = request.form.get('goals')
            project.description_notes = request.form.get('description_notes')
            project.framing_notes = request.form.get('framing_notes')
            project.processing_notes = request.form.get('processing_notes')

            # Finalize image file update
            if new_filename:
                project.final_image_file = new_filename

            # If the primary target is changed, check if the linked object has notes
            if project.target_object_name:
                target_obj_in_config = db.query(AstroObject).filter_by(
                    user_id=g.db_user.id, object_name=project.target_object_name
                ).one_or_none()
                if target_obj_in_config:
                    # Update active_project status based on this primary project
                    # Set active if status is "In Progress", otherwise set inactive
                    target_obj_in_config.active_project = (project.status == "In Progress")

            db.commit()
            flash(_("Project updated successfully."), "success")

            # --- Redirect Logic (Updated) ---
            # Check if we should return to a specific page (like the Journal tab)
            next_url = request.args.get('next')
            if next_url:
                return redirect(next_url)

            # Redirect to graph dashboard with project's target object
            if project.target_object_name:
                return redirect(url_for('core.graph_dashboard', object_name=project.target_object_name, tab='framing'))
            return redirect(url_for('core.index'))

        # --- Handle GET Request ---
        # Project details are now shown inline in the graph dashboard (via _project_subtab.html)
        # Redirect to the graph dashboard with the project's target object
        if project.target_object_name:
            return redirect(url_for('core.graph_dashboard', object_name=project.target_object_name, tab='framing'))
        return redirect(url_for('core.index'))
    except Exception as e:
        db.rollback()
        flash(_("An error occurred: %(error)s", error=e), "error")
        print(f"Error in project_detail route: {e}")
        traceback.print_exc()
        return redirect(url_for('core.index'))


def _build_project_exposure_summary(sessions):
    """
    Aggregate per-filter exposure totals across all sessions in a project.
    Returns a dict with keys:
      'filters': list of dicts {name, subs, exposure_sec, total_sec, mixed_duration}
                 ordered: L, R, G, B, Ha, OIII, SII, then custom filters alpha-sorted
      'grand_total_sec': int
      'has_simple_mode': bool (True if any session used simple/free-text mode)
    """
    import json as _json

    BUILTIN_FILTERS = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']

    # {filter_name: {subs: int, total_sec: int, durations: set()}}
    agg = {}

    def _add(name, subs, exp_sec):
        if not subs:
            return
        if name not in agg:
            agg[name] = {'subs': 0, 'total_sec': 0, 'durations': set()}
        agg[name]['subs'] += subs
        agg[name]['total_sec'] += subs * (exp_sec or 0)
        if exp_sec:
            agg[name]['durations'].add(exp_sec)

    for s in sessions:
        # Built-in monochrome filters
        for key in BUILTIN_FILTERS:
            subs = getattr(s, f'filter_{key}_subs', None)
            exp_sec = getattr(s, f'filter_{key}_exposure_sec', None)
            _add(key, subs, exp_sec)

        # Custom filters (JSON column — raw string, needs parsing)
        raw = getattr(s, 'custom_filter_data', None)
        if raw:
            try:
                custom = _json.loads(raw)
                if isinstance(custom, dict):
                    for fname, fdata in custom.items():
                        if isinstance(fdata, dict):
                            _add(fname,
                                 fdata.get('subs') or fdata.get('number_of_subs'),
                                 fdata.get('exposure_sec') or fdata.get('exposure_time_per_sub_sec'))
            except (ValueError, TypeError):
                pass

        # Simple mode sessions — use filter_used_session as the key
        simple_subs = getattr(s, 'number_of_subs_light', None)
        if simple_subs:
            simple_filter = (getattr(s, 'filter_used_session', None) or 'Light').strip()
            simple_exp = getattr(s, 'exposure_time_per_sub_sec', None)
            _add(simple_filter, simple_subs, simple_exp)

    # Build ordered output list
    filters_out = []
    seen = set()
    for key in BUILTIN_FILTERS:
        if key in agg:
            d = agg[key]
            filters_out.append({
                'name': key,
                'subs': d['subs'],
                'total_sec': d['total_sec'],
                'mixed_duration': len(d['durations']) > 1,
                'exposure_sec': next(iter(d['durations'])) if len(d['durations']) == 1 else None,
            })
            seen.add(key)
    for key in sorted(agg.keys()):
        if key not in seen:
            d = agg[key]
            filters_out.append({
                'name': key,
                'subs': d['subs'],
                'total_sec': d['total_sec'],
                'mixed_duration': len(d['durations']) > 1,
                'exposure_sec': next(iter(d['durations'])) if len(d['durations']) == 1 else None,
            })

    grand_total = sum(f['total_sec'] for f in filters_out)

    return {
        'filters': filters_out,
        'grand_total_sec': grand_total,
    }


@projects_bp.route('/project/report_page/<string:project_id>')
@login_required
def show_project_report_page(project_id):
    from nova.log_parser import parse_asiair_log, parse_phd2_log

    db = get_db()
    # 1. Fetch Project
    project = db.query(Project).filter_by(id=project_id, user_id=g.db_user.id).one_or_none()
    if not project:
        return "Project not found", 404

    # 2. Fetch Sessions
    sessions = db.query(JournalSession).filter_by(project_id=project.id, user_id=g.db_user.id).order_by(
        JournalSession.date_utc.asc()).all()

    # 3. Calculate Stats
    total_min = sum(s.calculated_integration_time_minutes or 0 for s in sessions)
    hours = int(total_min) // 60
    mins = int(total_min) % 60
    total_integration = f"{hours}h {mins}m"

    first_date = sessions[0].date_utc.strftime('%d.%m.%Y') if sessions else "-"
    last_date = sessions[-1].date_utc.strftime('%d.%m.%Y') if sessions else "-"

    # 4. Prepare Image
    project_image_url = None
    if project.final_image_file:
        username = "default" if SINGLE_USER_MODE else current_user.username
        project_image_url = url_for('core.get_uploaded_image', username=username, filename=project.final_image_file,
                                    _external=True)

    # 5. Parse Logs for Each Session and Build sessions_with_logs
    sessions_with_logs = []
    for s in sessions:
        session_dict = {c.name: getattr(s, c.name) for c in s.__table__.columns}

        # Parse logs for this session
        log_analysis = {'has_logs': False, 'asiair': None, 'phd2': None}
        chart_images = {
            'guiding_rms': None,
            'guiding_scatter': None,
            'dither_settle': None,
            'af_vcurve': None,
            'af_drift': None,
            'autocenter': None
        }

        try:
            asiair_content = read_log_content(session_dict.get('asiair_log_content'))
            asiair_data = parse_asiair_log(asiair_content) if asiair_content else None

            phd2_content = read_log_content(session_dict.get('phd2_log_content'))
            phd2_data = parse_phd2_log(phd2_content) if phd2_content else None

            has_logs = bool(asiair_data and asiair_data.get('exposures')) or bool(phd2_data and phd2_data.get('frames'))

            if has_logs:
                log_analysis = {
                    'has_logs': True,
                    'asiair': asiair_data,
                    'phd2': phd2_data
                }
                chart_images = generate_session_charts(log_analysis)
        except Exception as log_error:
            print(f"[project_report] Error parsing logs for session {s.id}: {log_error}")

        # Sanitize notes
        raw_notes = session_dict.get('notes') or ""
        if not raw_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>", "<figure>", "<blockquote>", "<h1>", "<h2>", "<h3>", "<h4>", "<h5>", "<h6>")):
            escaped_text = bleach.clean(raw_notes, tags=[], strip=True)
            session_dict['notes'] = escaped_text.replace("\n", "<br>")
        else:
            SAFE_TAGS = ['p', 'strong', 'em', 'ul', 'ol', 'li', 'br', 'div', 'img', 'a', 'figure', 'figcaption', 'span']
            SAFE_ATTRS = {'img': ['src', 'alt', 'width', 'height', 'style'], 'a': ['href'], '*': ['style', 'class']}
            SAFE_CSS = ['text-align', 'width', 'height', 'max-width', 'float', 'margin', 'margin-left', 'margin-right']
            css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS)
            session_dict['notes'] = bleach.clean(raw_notes, tags=SAFE_TAGS, attributes=SAFE_ATTRS, css_sanitizer=css_sanitizer)

        sessions_with_logs.append({
            'session': session_dict,
            'log_analysis': log_analysis,
            'chart_images': chart_images
        })

    # 6. Logo URL
    logo_url = url_for('static', filename='nova-icon-transparent.png', _external=True)

    # 7. Exposure Summary
    exposure_summary = _build_project_exposure_summary(sessions)

    record_event('pdf_report_generated')
    return render_template(
        'project_report.html',
        project=project,
        sessions=sessions,
        sessions_with_logs=sessions_with_logs,
        total_integration=total_integration,
        session_count=len(sessions),
        first_date=first_date,
        last_date=last_date,
        project_image_url=project_image_url,
        logo_url=logo_url,
        exposure_summary=exposure_summary,
        today_date=datetime.now().strftime('%d.%m.%Y')
    )


@projects_bp.route('/project/delete/<string:project_id>', methods=['POST'])
@login_required
def delete_project(project_id):
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        project = db.query(Project).filter_by(id=project_id, user_id=user.id).one_or_none()

        if not project:
            flash(_("Project not found."), "error")
            return redirect(url_for('core.index'))

        # Optional: Unset 'active_project' flag on the associated object if it exists
        if project.target_object_name:
            obj = db.query(AstroObject).filter_by(user_id=user.id, object_name=project.target_object_name).one_or_none()
            if obj:
                obj.active_project = False

        # Delete the project.
        # Note: Sessions will NOT be deleted. Their project_id will automatically set to NULL
        # because of the ForeignKey(ondelete="SET NULL") definition in your model.
        db.delete(project)
        db.commit()

        flash(_("Project '%(project_name)s' deleted. Sessions are now standalone.", project_name=project.name), "success")

        # Redirect back to the object's journal tab
        return redirect(
            url_for('core.graph_dashboard', object_name=request.form.get('redirect_object', 'M31'), tab='journal'))

    except Exception as e:
        db.rollback()
        flash(_("Error deleting project: %(error)s", error=e), "error")
        return redirect(url_for('core.index'))
