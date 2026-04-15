#!/usr/bin/env python3
"""Comprehensive permission system tests."""

from nova import app, SessionLocal, DbUser, Role, Permission
from sqlalchemy.orm import joinedload
from flask import g
from flask_login import login_user, logout_user


def simulate_login(client, user):
    """Simulate a logged-in user session."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def test_routes(client, user, routes, expected_status, description):
    """Test multiple routes for a user."""
    print(f"\n--- {description} ---")
    simulate_login(client, user)

    results = []
    for method, route, expected in routes:
        if method == "GET":
            resp = client.get(route)
        else:
            resp = client.post(route, data={})

        status = "PASS" if resp.status_code == expected else "FAIL"
        results.append((route, resp.status_code, expected, status))
        print(
            f"  {method} {route}: {resp.status_code} (expected {expected}) [{status}]"
        )

    return results


def main():
    db = SessionLocal()

    # Get users with eager loading for roles and permissions
    def get_user(username):
        return (
            db.query(DbUser)
            .options(joinedload(DbUser.roles).joinedload(Role.permissions))
            .filter_by(username=username)
            .first()
        )

    admin_user = get_user("admin")
    testuser = get_user("testuser")
    guest_user = get_user("guest_user")

    # Create a user with 'user' role for testing
    user_role = db.query(Role).filter_by(name="user").first()
    normal_user = get_user("normaluser")
    if not normal_user:
        normal_user = DbUser(username="normaluser", active=True)
        normal_user.set_password("test123")
        db.add(normal_user)
        db.commit()

    # Assign user role if not present (need to query fresh to check)
    normal_user = get_user("normaluser")
    if user_role not in normal_user.roles:
        normal_user.roles.append(user_role)
        db.commit()
        # Re-query with eager loading after role assignment
        normal_user = get_user("normaluser")

    print("=== PERMISSION SYSTEM COMPREHENSIVE TESTS ===")
    print(
        f"Admin user: {admin_user.username}, roles: {[r.name for r in admin_user.roles]}"
    )
    print(
        f"Normal user: {normal_user.username}, roles: {[r.name for r in normal_user.roles]}"
    )
    print(
        f"Test user (readonly): {testuser.username}, roles: {[r.name for r in testuser.roles]}"
    )
    print(
        f"Guest user: {guest_user.username}, roles: {[r.name for r in guest_user.roles]}"
    )

    with app.test_client() as client:
        # Test 1: Admin can access everything
        # NOTE: /analytics requires ANALYTICS_SECRET env var, so it returns 403 even with permission
        admin_routes = [
            ("GET", "/", 200),  # dashboard.view
            ("GET", "/admin/users", 200),  # admin.users.view
            ("GET", "/admin/roles", 200),  # admin.roles.manage
            ("GET", "/config_form", 200),  # settings.edit
        ]
        test_routes(
            client, admin_user, admin_routes, 200, "ADMIN: Can access everything"
        )

        # Test 2: User role - has most permissions except admin
        user_routes = [
            ("GET", "/", 200),  # dashboard.view - YES
            ("GET", "/config_form", 200),  # settings.edit - YES (user has it)
            ("GET", "/admin/users", 403),  # admin.users.view - NO
            ("GET", "/admin/roles", 403),  # admin.roles.manage - NO
        ]
        test_routes(
            client,
            normal_user,
            user_routes,
            200,
            "USER ROLE: Most permissions except admin",
        )

        # Test 3: Readonly role - only view access
        readonly_routes = [
            ("GET", "/", 200),  # dashboard.view - YES
            ("GET", "/config_form", 403),  # settings.edit - NO (only settings.view)
            ("GET", "/admin/users", 403),  # admin.users.view - NO
            ("POST", "/add_component", 403),  # equipment.create - NO
        ]
        test_routes(
            client, testuser, readonly_routes, 200, "READONLY ROLE: View-only access"
        )

        # Test 4: User with NO roles (guest_user) - should get 403 on most things
        no_role_routes = [
            ("GET", "/", 403),  # dashboard.view - NO (no roles!)
            ("GET", "/config_form", 403),  # settings.edit - NO
            ("GET", "/admin/users", 403),  # admin.users.view - NO
        ]
        test_routes(
            client,
            guest_user,
            no_role_routes,
            403,
            "NO ROLES: Should be blocked from everything",
        )

    # Test 5: has_permission and is_admin helpers
    print("\n--- HELPER METHODS ---")
    print(f"admin.is_admin: {admin_user.is_admin} (expected True)")
    print(
        f"admin.has_permission('admin.users.view'): {admin_user.has_permission('admin.users.view')} (expected True)"
    )
    print(f"admin.has_role('admin'): {admin_user.has_role('admin')} (expected True)")

    print(f"normaluser.is_admin: {normal_user.is_admin} (expected False)")
    print(
        f"normaluser.has_permission('dashboard.view'): {normal_user.has_permission('dashboard.view')} (expected True)"
    )
    print(
        f"normaluser.has_permission('admin.users.view'): {normal_user.has_permission('admin.users.view')} (expected False)"
    )

    print(
        f"testuser (readonly).has_permission('settings.view'): {testuser.has_permission('settings.view')} (expected True)"
    )
    print(
        f"testuser (readonly).has_permission('settings.edit'): {testuser.has_permission('settings.edit')} (expected False)"
    )

    print(f"guest_user (no roles).is_admin: {guest_user.is_admin} (expected False)")
    print(
        f"guest_user (no roles).has_permission('dashboard.view'): {guest_user.has_permission('dashboard.view')} (expected False)"
    )

    db.close()
    print("\n=== TESTS COMPLETE ===")


if __name__ == "__main__":
    main()
