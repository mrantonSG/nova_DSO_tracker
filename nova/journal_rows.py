"""
nova/journal_rows.py - the dashboard's Journal/Gallery row payload.

One definition of the rows templates/index.html inlines today as
window.NOVA_INDEX.journalSessions and that /api/journal_sessions serves from
step B onward: core.index() calls build_journal_rows() until step B, the API
route calls it after, so the two can never drift.

Safe to import from any blueprint: it depends only on nova.config, nova.models,
Flask and PIL, all below nova.blueprints.* in the import chain
(nova.models -> nova.config -> nova.helpers -> nova.workers.* -> nova.init ->
nova.blueprints.*). core.get_uploaded_image is reached only through url_for()'s
endpoint string, so core is never imported here.
"""
import os
from datetime import date, datetime

from flask import current_app, url_for
from PIL import Image

from nova.config import UPLOAD_FOLDER
from nova.models import AstroObject, JournalSession, Project, session_projects


# The dashboard contract: exactly the keys static/js/dashboard.js reads.
# Everything else on the model - log_analysis_cache above all - stays
# server-side. tests/test_journal_sessions_endpoint.py pins the row key set to
# this tuple, so a new dashboard field means a new entry here.
JOURNAL_SESSION_FIELDS = (
    'id',
    'is_stub',
    'object_name',
    'target_common_name',
    'project_id',
    'project_name',
    'project_status',
    'date_utc',
    'location_name',
    'telescope_setup_notes',
    'telescope_name_snapshot',
    'reducer_name_snapshot',
    'camera_name_snapshot',
    'filter_used_session',
    'calculated_integration_time_minutes',
    'session_rating_subjective',
    'image_url',
    'other_projects',
    'other_projects_count',
)
_FIELD_SET = frozenset(JOURNAL_SESSION_FIELDS)


def session_image_url(username, session_image_file):
    """The URL of a session image: its thumbnail when one exists, otherwise the
    original - generating the missing thumbnail on the spot so self-hosted
    users never need a manual backfill script. Moved verbatim from core.index().
    """
    if not session_image_file:
        return None

    session_image_filename = session_image_file
    session_thumb_filename = f"thumb_{session_image_filename}"
    session_user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
    if os.path.exists(os.path.join(session_user_upload_dir, session_thumb_filename)):
        session_image_filename = session_thumb_filename
    else:
        # Self-healing: generate the missing thumbnail now so
        # self-hosted users never need a manual backfill script.
        session_thumb_path = os.path.join(session_user_upload_dir, session_thumb_filename)
        session_original_path = os.path.join(session_user_upload_dir, session_image_filename)
        session_upload_root = os.path.realpath(session_user_upload_dir)
        try:
            session_paths_within_user_dir = all(
                os.path.commonpath([os.path.realpath(p), session_upload_root]) == session_upload_root
                for p in (session_original_path, session_thumb_path)
            )
        except ValueError:
            session_paths_within_user_dir = False
        if session_paths_within_user_dir:
            try:
                if os.path.getsize(session_original_path) < 1024:
                    current_app.logger.warning(
                        f"[DASHBOARD] Skipping likely-corrupt file, too small to be a "
                        f"valid image: '{session_image_filename}'"
                    )
                else:
                    session_file_extension = session_image_filename.rsplit('.', 1)[1].lower()
                    with Image.open(session_original_path) as session_img:
                        session_img.thumbnail((480, 480))
                        if session_file_extension in ('jpg', 'jpeg'):
                            session_img.save(session_thumb_path, quality=85)
                        elif session_file_extension == 'png':
                            session_img.save(session_thumb_path, optimize=True)
                        else:
                            session_img.save(session_thumb_path)
                    session_image_filename = session_thumb_filename
            except Exception as session_thumb_err:
                current_app.logger.warning(
                    f"[DASHBOARD] Failed to generate thumbnail for session image "
                    f"'{session_image_filename}': {session_thumb_err}"
                )
    return url_for('core.get_uploaded_image', username=username,
                   filename=session_image_filename)


def build_journal_rows(db, user, username, sessions, all_projects):
    """The dashboard's journal rows: one dict per session, plus one stub row per
    project no session links to.

    `sessions` must already be scoped to the user and ordered by date_utc
    descending (as core.index() and the API route both query it); `all_projects`
    must be that user's projects. Same rows, same order, same values as
    core.index() inlined before this moved.
    """
    project_map = {p.id: {'name': p.name, 'status': p.status} for p in all_projects}
    object_names_lookup = {
        o.record_uid: o.common_name
        for o in db.query(AstroObject).filter_by(user_id=user.id).all()
        if o.record_uid
    }

    # --- Convert the list of objects into a list of JSON-safe dictionaries ---
    sessions_for_template = []
    for session in sessions:
        # Only the columns the dashboard reads (see JOURNAL_SESSION_FIELDS).
        session_dict = {c.name: getattr(session, c.name)
                        for c in session.__table__.columns if c.name in _FIELD_SET}

        # Convert the date object to an ISO string for JavaScript
        if isinstance(session_dict.get('date_utc'), (datetime, date)):
            session_dict['date_utc'] = session_dict['date_utc'].isoformat()

        # Add the common name for convenience in the template
        session_dict['target_common_name'] = object_names_lookup.get(
            session.object_record_uid, session.object_name)

        # Read the column directly: session_image_file is deliberately NOT in
        # JOURNAL_SESSION_FIELDS, so session_dict no longer carries it.
        session_dict['image_url'] = session_image_url(username, session.session_image_file)

        if session.project_id:
            proj = project_map.get(session.project_id)
            if proj:
                session_dict['project_name'] = proj['name']
                session_dict['project_status'] = proj['status']
            else:
                session_dict['project_name'] = "Unknown Project"
                session_dict['project_status'] = None
        else:
            session_dict['project_name'] = "-"
            session_dict['project_status'] = ""

        other_projects = [
            {'id': p.id, 'name': p.name}
            for p in session.projects
            if p.id != session.project_id
        ]
        session_dict['other_projects'] = other_projects
        session_dict['other_projects_count'] = len(other_projects)

        sessions_for_template.append(session_dict)
    # --- END OF FIX ---

    # --- Stub rows for projects with zero linked sessions ---
    # A project only counts as "linked" if a session references it via
    # EITHER the JournalSession.project_id FK OR the session_projects m2m
    # table; otherwise a project with only m2m-linked sessions would wrongly
    # get a stub row alongside its real ones.
    linked_ids = {
        pid for (pid,) in db.query(JournalSession.project_id).filter(
            JournalSession.user_id == user.id,
            JournalSession.project_id.isnot(None)
        ).union(
            db.query(session_projects.c.project_id).join(
                JournalSession, JournalSession.id == session_projects.c.session_id
            ).filter(JournalSession.user_id == user.id)
        ).all()
    }
    stub_projects = db.query(Project).filter_by(user_id=user.id).filter(~Project.id.in_(linked_ids)).all()
    for stub_index, project in enumerate(stub_projects, start=1):
        sessions_for_template.append({
            'id': -stub_index,
            'is_stub': True,
            'project_id': project.id,
            'project_name': project.name,
            'project_status': project.status,
            'object_name': project.target_object_name or None,
            'date_utc': None,
            'location_name': None,
            'telescope_setup_notes': None,
            'calculated_integration_time_minutes': None,
            'session_rating_subjective': None,
            'other_projects': [],
            'other_projects_count': 0,
        })

    return sessions_for_template
