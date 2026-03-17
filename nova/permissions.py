"""
Role-Based Access Control (RBAC) decorators for Nova.

This module provides decorators for protecting routes with permission checks.
It supports both Flask-Login authenticated users and API key authenticated users.

Usage:
    from nova.permissions import admin_required, permission_required

    @app.route('/admin/users')
    @login_required
    @admin_required
    def admin_users():
        ...

    @app.route('/export')
    @login_required
    @permission_required('data.export.any')
    def export_data():
        ...
"""

from functools import wraps
from typing import Callable, Optional

from flask import abort, g, jsonify, request
from flask_login import current_user


# =============================================================================
# System Permission Names
# =============================================================================

SYSTEM_PERMISSIONS = [
    # =========================================================================
    # Admin permissions (require admin role by default)
    # =========================================================================
    ("admin.users.view", "View all users in admin panel"),
    ("admin.users.manage", "Create, edit, deactivate, and delete users"),
    ("admin.roles.view", "View roles and their permissions"),
    ("admin.roles.manage", "Create, edit, and delete roles"),
    ("admin.db.repair", "Run database repair tools"),
    ("admin.data.export", "Export any user's data (admin export)"),
    ("admin.data.import", "Import data for any user (admin import)"),
    # =========================================================================
    # Dashboard & Analytics
    # =========================================================================
    ("dashboard.view", "View main dashboard with DSO list"),
    ("dashboard.analytics", "View analytics and graphs"),
    ("dashboard.weather", "View weather forecast data"),
    ("dashboard.sun_events", "View sun/twilight events"),
    # =========================================================================
    # DSO Objects
    # =========================================================================
    ("objects.view", "View DSO objects list and details"),
    ("objects.create", "Add new DSO objects"),
    ("objects.edit", "Edit DSO object details and notes"),
    ("objects.delete", "Delete DSO objects"),
    ("objects.bulk_edit", "Perform bulk operations on objects"),
    ("objects.merge", "Merge duplicate objects"),
    # =========================================================================
    # Observation Journal
    # =========================================================================
    ("journal.view", "View observation journal sessions"),
    ("journal.create", "Add new observation sessions"),
    ("journal.edit", "Edit observation sessions"),
    ("journal.delete", "Delete observation sessions"),
    ("journal.export", "Export journal to CSV/PDF"),
    # =========================================================================
    # Projects
    # =========================================================================
    ("projects.view", "View imaging projects"),
    ("projects.create", "Create new projects"),
    ("projects.edit", "Edit project details"),
    ("projects.delete", "Delete projects"),
    ("projects.report", "Generate project reports"),
    # =========================================================================
    # Equipment (Components & Rigs)
    # =========================================================================
    ("equipment.view", "View equipment components and rigs"),
    ("equipment.create", "Add new equipment"),
    ("equipment.edit", "Edit equipment details"),
    ("equipment.delete", "Delete equipment"),
    # =========================================================================
    # Locations
    # =========================================================================
    ("locations.view", "View observation locations"),
    ("locations.create", "Add new locations"),
    ("locations.edit", "Edit location details"),
    ("locations.delete", "Delete locations"),
    ("locations.set_active", "Change active location"),
    # =========================================================================
    # Saved Views
    # =========================================================================
    ("views.view", "View saved views"),
    ("views.create", "Create saved views"),
    ("views.edit", "Edit saved views"),
    ("views.delete", "Delete saved views"),
    # =========================================================================
    # Framings (FOV overlays)
    # =========================================================================
    ("framings.view", "View framing data"),
    ("framings.create", "Create framing overlays"),
    ("framings.edit", "Edit framing data"),
    ("framings.delete", "Delete framing data"),
    # =========================================================================
    # Custom Filters
    # =========================================================================
    ("filters.view", "View custom filters"),
    ("filters.create", "Create custom filters"),
    ("filters.edit", "Edit custom filters"),
    ("filters.delete", "Delete custom filters"),
    # =========================================================================
    # Import/Export (own data)
    # =========================================================================
    ("data.export", "Export own data (YAML/CSV)"),
    ("data.import", "Import data (YAML/catalogs)"),
    # =========================================================================
    # Settings & Configuration
    # =========================================================================
    ("settings.view", "View application settings"),
    ("settings.edit", "Edit application settings"),
    ("settings.stellarium", "Configure Stellarium integration"),
    # =========================================================================
    # API Keys
    # =========================================================================
    ("api_keys.view", "View own API keys"),
    ("api_keys.manage", "Create and delete API keys"),
    # =========================================================================
    # Shared Content
    # =========================================================================
    ("shared.objects.view", "View shared DSO objects from others"),
    ("shared.views.view", "View shared saved views from others"),
    ("shared.components.view", "View shared equipment from others"),
    ("shared.objects.fork", "Copy shared items to own collection"),
    # =========================================================================
    # Mobile Interface
    # =========================================================================
    ("mobile.access", "Access mobile interface"),
    # =========================================================================
    # Blog / Community
    # =========================================================================
    ("blog.view", "View blog posts from all users"),
    ("blog.create", "Create new blog posts"),
    ("blog.edit", "Edit own blog posts"),
    ("blog.delete", "Delete own blog posts"),
    ("blog.comment", "Post comments on blog posts"),
]


# =============================================================================
# Permission Categories (for UI grouping)
# =============================================================================

PERMISSION_CATEGORIES = {
    "Admin": [
        "admin.users.view",
        "admin.users.manage",
        "admin.roles.view",
        "admin.roles.manage",
        "admin.db.repair",
        "admin.data.export",
        "admin.data.import",
    ],
    "Dashboard": [
        "dashboard.view",
        "dashboard.analytics",
        "dashboard.weather",
        "dashboard.sun_events",
    ],
    "Objects": [
        "objects.view",
        "objects.create",
        "objects.edit",
        "objects.delete",
        "objects.bulk_edit",
        "objects.merge",
    ],
    "Journal": [
        "journal.view",
        "journal.create",
        "journal.edit",
        "journal.delete",
        "journal.export",
    ],
    "Projects": [
        "projects.view",
        "projects.create",
        "projects.edit",
        "projects.delete",
        "projects.report",
    ],
    "Equipment": [
        "equipment.view",
        "equipment.create",
        "equipment.edit",
        "equipment.delete",
    ],
    "Locations": [
        "locations.view",
        "locations.create",
        "locations.edit",
        "locations.delete",
        "locations.set_active",
    ],
    "Saved Views": ["views.view", "views.create", "views.edit", "views.delete"],
    "Framings": [
        "framings.view",
        "framings.create",
        "framings.edit",
        "framings.delete",
    ],
    "Custom Filters": [
        "filters.view",
        "filters.create",
        "filters.edit",
        "filters.delete",
    ],
    "Data Import/Export": ["data.export", "data.import"],
    "Settings": ["settings.view", "settings.edit", "settings.stellarium"],
    "API Keys": ["api_keys.view", "api_keys.manage"],
    "Shared Content": [
        "shared.objects.view",
        "shared.views.view",
        "shared.components.view",
        "shared.objects.fork",
    ],
    "Mobile": ["mobile.access"],
    "Blog": ["blog.view", "blog.create", "blog.edit", "blog.delete", "blog.comment"],
}


# Default permissions for system roles
DEFAULT_ROLE_PERMISSIONS = {
    "admin": "*",  # All permissions (handled in code)
    "user": [
        # Dashboard access
        "dashboard.view",
        "dashboard.analytics",
        "dashboard.weather",
        "dashboard.sun_events",
        # Full object management
        "objects.view",
        "objects.create",
        "objects.edit",
        "objects.delete",
        "objects.bulk_edit",
        "objects.merge",
        # Full journal management
        "journal.view",
        "journal.create",
        "journal.edit",
        "journal.delete",
        "journal.export",
        # Full project management
        "projects.view",
        "projects.create",
        "projects.edit",
        "projects.delete",
        "projects.report",
        # Full equipment management
        "equipment.view",
        "equipment.create",
        "equipment.edit",
        "equipment.delete",
        # Full location management
        "locations.view",
        "locations.create",
        "locations.edit",
        "locations.delete",
        "locations.set_active",
        # Full saved views management
        "views.view",
        "views.create",
        "views.edit",
        "views.delete",
        # Full framing management
        "framings.view",
        "framings.create",
        "framings.edit",
        "framings.delete",
        # Full filter management
        "filters.view",
        "filters.create",
        "filters.edit",
        "filters.delete",
        # Import/export own data
        "data.export",
        "data.import",
        # Settings
        "settings.view",
        "settings.edit",
        "settings.stellarium",
        # API keys
        "api_keys.view",
        "api_keys.manage",
        # Shared content (view + fork)
        "shared.objects.view",
        "shared.views.view",
        "shared.components.view",
        "shared.objects.fork",
        # Mobile access
        "mobile.access",
        # Blog (full access)
        "blog.view",
        "blog.create",
        "blog.edit",
        "blog.delete",
        "blog.comment",
    ],
    "readonly": [
        # View-only access
        "dashboard.view",
        "dashboard.analytics",
        "dashboard.weather",
        "dashboard.sun_events",
        "objects.view",
        "journal.view",
        "projects.view",
        "equipment.view",
        "locations.view",
        "views.view",
        "framings.view",
        "filters.view",
        "settings.view",
        "shared.objects.view",
        "shared.views.view",
        "shared.components.view",
        "mobile.access",
        # Blog (view + comment for readonly users)
        "blog.view",
        "blog.comment",
    ],
}


# =============================================================================
# Helper Functions
# =============================================================================


def _get_current_user():
    """
    Get the current authenticated user from either Flask-Login or API auth.

    For Flask-Login: uses current_user
    For API auth: uses g.db_user (set by api_key_required decorator)

    Returns:
        DbUser instance or None if not authenticated
    """
    # API authentication path - g.db_user is set by api_key_required
    if hasattr(g, "db_user") and g.db_user is not None:
        return g.db_user

    # Flask-Login authentication path
    if current_user and current_user.is_authenticated:
        return current_user

    return None


def _is_api_request() -> bool:
    """Check if this is an API request (expects JSON response)."""
    return (
        request.path.startswith("/api/")
        or request.headers.get("Accept", "").startswith("application/json")
        or request.headers.get("X-API-Key") is not None
    )


def _abort_forbidden(message: str = "Permission denied"):
    """Abort with 403, returning JSON for API requests."""
    if _is_api_request():
        return jsonify({"error": message, "status": "forbidden"}), 403
    abort(403, description=message)


# =============================================================================
# Permission Decorators
# =============================================================================


def admin_required(f: Callable) -> Callable:
    """
    Decorator to require admin role for a route.

    Use AFTER @login_required or @api_key_required.

    Example:
        @app.route('/admin/users')
        @login_required
        @admin_required
        def admin_users():
            ...
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = _get_current_user()

        if user is None:
            return _abort_forbidden("Authentication required")

        if not user.is_admin:
            return _abort_forbidden("Admin access required")

        return f(*args, **kwargs)

    return decorated_function


def permission_required(perm_name: str) -> Callable:
    """
    Decorator factory to require a specific permission for a route.

    Use AFTER @login_required or @api_key_required.

    Args:
        perm_name: The permission name to check (e.g., 'data.export.any')

    Example:
        @app.route('/export')
        @login_required
        @permission_required('data.export.any')
        def export_data():
            ...
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = _get_current_user()

            if user is None:
                return _abort_forbidden("Authentication required")

            # Admins have all permissions
            if user.is_admin:
                return f(*args, **kwargs)

            if not user.has_permission(perm_name):
                return _abort_forbidden(f"Permission required: {perm_name}")

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def require_owner_or_permission(
    perm_name: str, get_owner_id: Callable[..., Optional[int]]
) -> Callable:
    """
    Decorator factory for resource ownership OR permission check.

    Allows access if:
    1. User owns the resource (owner_id matches user.id), OR
    2. User has the specified permission (or is admin)

    Args:
        perm_name: The permission name that grants access to others' resources
        get_owner_id: Function that takes route kwargs and returns the owner's user_id
                     (should return None if resource not found)

    Example:
        def get_object_owner(**kwargs):
            obj_id = kwargs.get('object_id')
            obj = db.query(AstroObject).get(obj_id)
            return obj.user_id if obj else None

        @app.route('/objects/<int:object_id>')
        @login_required
        @require_owner_or_permission('data.export.any', get_object_owner)
        def view_object(object_id):
            ...
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = _get_current_user()

            if user is None:
                return _abort_forbidden("Authentication required")

            # Admins bypass all checks
            if user.is_admin:
                return f(*args, **kwargs)

            # Check ownership
            owner_id = get_owner_id(**kwargs)
            if owner_id is not None and owner_id == user.id:
                return f(*args, **kwargs)

            # Check permission
            if user.has_permission(perm_name):
                return f(*args, **kwargs)

            return _abort_forbidden(
                f"You don't own this resource and lack permission: {perm_name}"
            )

        return decorated_function

    return decorator


# =============================================================================
# API-specific Decorators
# =============================================================================


def api_admin_required(f: Callable) -> Callable:
    """
    Decorator to require admin role for API routes.

    Similar to admin_required but always returns JSON responses.
    Use AFTER @api_key_required.

    Example:
        @api_bp.route('/admin/users')
        @api_key_required
        @api_admin_required
        def list_users():
            ...
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = _get_current_user()

        if user is None:
            return jsonify(
                {"error": "Authentication required", "status": "unauthorized"}
            ), 401

        if not user.is_admin:
            return jsonify(
                {"error": "Admin access required", "status": "forbidden"}
            ), 403

        return f(*args, **kwargs)

    return decorated_function


def api_permission_required(perm_name: str) -> Callable:
    """
    Decorator factory to require a specific permission for API routes.

    Always returns JSON responses.
    Use AFTER @api_key_required.

    Args:
        perm_name: The permission name to check

    Example:
        @api_bp.route('/shared/objects')
        @api_key_required
        @api_permission_required('shared.objects.view')
        def list_shared_objects():
            ...
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = _get_current_user()

            if user is None:
                return jsonify(
                    {"error": "Authentication required", "status": "unauthorized"}
                ), 401

            # Admins have all permissions
            if user.is_admin:
                return f(*args, **kwargs)

            if not user.has_permission(perm_name):
                return jsonify(
                    {
                        "error": f"Permission required: {perm_name}",
                        "status": "forbidden",
                    }
                ), 403

            return f(*args, **kwargs)

        return decorated_function

    return decorator
