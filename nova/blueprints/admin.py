from flask import Blueprint, request, redirect, url_for, render_template, flash
from flask_login import login_required, current_user
from flask_babel import gettext as _

from nova.config import SINGLE_USER_MODE

admin_bp = Blueprint('admin', __name__)


def _admin_guard():
    """Return a redirect response if the request is not from an admin, else None."""
    if SINGLE_USER_MODE:
        return redirect(url_for("core.index"))
    if current_user.username != "admin":
        flash(_("Not authorized."), "error")
        return redirect(url_for("core.index"))
    return None


@admin_bp.before_request
def csrf_protect_admin():
    if SINGLE_USER_MODE:
        return
    if request.method == "POST" and request.path.startswith("/admin/"):
        from nova import csrf
        csrf.protect()


@admin_bp.route("/admin/users")
@login_required
def admin_users():
    guard = _admin_guard()
    if guard:
        return guard
    from nova import db, User
    users = db.session.scalars(db.select(User).order_by(User.id)).all()
    return render_template("admin_users.html", users=users)


@admin_bp.route("/admin/users/create", methods=["POST"])
@login_required
def admin_create_user():
    guard = _admin_guard()
    if guard:
        return guard
    from nova import db, User
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        flash(_("Username and password are required."), "error")
        return redirect(url_for("admin.admin_users"))
    if db.session.scalar(db.select(User).where(User.username == username)):
        flash(_("User '%(username)s' already exists.", username=username), "error")
        return redirect(url_for("admin.admin_users"))
    user = User(username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(_("User '%(username)s' created successfully.", username=username), "success")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@login_required
def admin_toggle_user(user_id):
    guard = _admin_guard()
    if guard:
        return guard
    from nova import db, User
    user = db.session.get(User, user_id)
    if not user:
        flash(_("User not found."), "error")
        return redirect(url_for("admin.admin_users"))
    if user.username == "admin":
        flash(_("Cannot deactivate the admin account."), "error")
        return redirect(url_for("admin.admin_users"))
    user.active = not user.active
    db.session.commit()
    status = "activated" if user.active else "deactivated"
    flash(_("User '%(username)s' %(status)s.", username=user.username, status=status), "success")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
def admin_reset_password(user_id):
    guard = _admin_guard()
    if guard:
        return guard
    from nova import db, User
    user = db.session.get(User, user_id)
    if not user:
        flash(_("User not found."), "error")
        return redirect(url_for("admin.admin_users"))
    new_password = request.form.get("new_password", "")
    if not new_password:
        flash(_("Password cannot be empty."), "error")
        return redirect(url_for("admin.admin_users"))
    user.set_password(new_password)
    db.session.commit()
    flash(_("Password reset for '%(username)s'.", username=user.username), "success")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    guard = _admin_guard()
    if guard:
        return guard
    from nova import db, User
    user = db.session.get(User, user_id)
    if not user:
        flash(_("User not found."), "error")
        return redirect(url_for("admin.admin_users"))
    if user.username == "admin":
        flash(_("Cannot delete the admin account."), "error")
        return redirect(url_for("admin.admin_users"))
    uname = user.username
    db.session.delete(user)
    db.session.commit()
    flash(_("User '%(username)s' deleted.", username=uname), "success")
    return redirect(url_for("admin.admin_users"))