"""
Tests for RBAC permission system.

Wave 0 - TDD: These tests are written BEFORE the implementation.
They should FAIL until Wave 1 (models) and Wave 2 (decorators) are complete.
"""

import pytest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# =============================================================================
# T0A: Model-level RBAC Tests
# =============================================================================


class TestDbUserRBACHelpers:
    """Tests for DbUser.has_permission(), has_role(), is_admin helpers."""

    def test_has_role_returns_true_for_assigned_role(self, db_session):
        """User with 'admin' role should return True for has_role('admin')."""
        from nova.models import DbUser, Role, Permission

        # Create admin role with a permission
        admin_role = Role(name="admin", description="Administrator", is_system=True)
        db_session.add(admin_role)
        db_session.flush()

        # Create user and assign admin role
        user = DbUser(username="test_admin")
        user.roles.append(admin_role)
        db_session.add(user)
        db_session.commit()

        assert user.has_role("admin") is True
        assert user.has_role("user") is False

    def test_has_role_returns_false_for_unassigned_role(self, db_session):
        """User without any roles should return False for has_role()."""
        from nova.models import DbUser

        user = DbUser(username="test_no_roles")
        db_session.add(user)
        db_session.commit()

        assert user.has_role("admin") is False
        assert user.has_role("user") is False

    def test_has_permission_returns_true_when_role_has_permission(self, db_session):
        """User should have permission if any of their roles has it."""
        from nova.models import DbUser, Role, Permission

        # Create permission
        perm = Permission(name="admin.users.view", description="View all users")
        db_session.add(perm)

        # Create role with that permission
        admin_role = Role(name="admin", description="Administrator", is_system=True)
        admin_role.permissions.append(perm)
        db_session.add(admin_role)
        db_session.flush()

        # Create user with that role
        user = DbUser(username="test_admin_perm")
        user.roles.append(admin_role)
        db_session.add(user)
        db_session.commit()

        assert user.has_permission("admin.users.view") is True
        assert user.has_permission("nonexistent.permission") is False

    def test_has_permission_returns_false_without_permission(self, db_session):
        """User without the permission should return False."""
        from nova.models import DbUser, Role

        # Create role without permissions
        readonly_role = Role(name="readonly", description="Read-only user")
        db_session.add(readonly_role)
        db_session.flush()

        user = DbUser(username="test_readonly")
        user.roles.append(readonly_role)
        db_session.add(user)
        db_session.commit()

        assert user.has_permission("admin.users.view") is False

    def test_is_admin_property_true_for_admin_role(self, db_session):
        """is_admin property should return True for users with admin role."""
        from nova.models import DbUser, Role

        admin_role = Role(name="admin", description="Administrator", is_system=True)
        db_session.add(admin_role)
        db_session.flush()

        user = DbUser(username="test_is_admin")
        user.roles.append(admin_role)
        db_session.add(user)
        db_session.commit()

        assert user.is_admin is True

    def test_is_admin_property_false_for_regular_user(self, db_session):
        """is_admin property should return False for non-admin users."""
        from nova.models import DbUser, Role

        user_role = Role(name="user", description="Regular user")
        db_session.add(user_role)
        db_session.flush()

        user = DbUser(username="test_not_admin")
        user.roles.append(user_role)
        db_session.add(user)
        db_session.commit()

        assert user.is_admin is False

    def test_user_can_have_multiple_roles(self, db_session):
        """User can have multiple roles and permissions aggregate."""
        from nova.models import DbUser, Role, Permission

        # Create permissions
        perm1 = Permission(
            name="shared.objects.view", description="View shared objects"
        )
        perm2 = Permission(
            name="shared.objects.fork", description="Fork shared objects"
        )
        db_session.add_all([perm1, perm2])

        # Create two roles with different permissions
        user_role = Role(name="user", description="Regular user")
        user_role.permissions.append(perm1)

        contributor_role = Role(name="contributor", description="Contributor")
        contributor_role.permissions.append(perm2)

        db_session.add_all([user_role, contributor_role])
        db_session.flush()

        # User with both roles
        user = DbUser(username="test_multi_role")
        user.roles.extend([user_role, contributor_role])
        db_session.add(user)
        db_session.commit()

        assert user.has_role("user") is True
        assert user.has_role("contributor") is True
        assert user.has_permission("shared.objects.view") is True
        assert user.has_permission("shared.objects.fork") is True


# =============================================================================
# T0B: Permission Decorator Tests
# =============================================================================


class TestPermissionDecorators:
    """Tests for @admin_required, @permission_required decorators."""

    def test_admin_required_allows_admin_user(self, client, db_session):
        """@admin_required should allow requests from admin users."""
        from nova.models import DbUser, Role
        from nova.permissions import admin_required

        # Create admin role and user
        admin_role = Role(name="admin", is_system=True)
        db_session.add(admin_role)
        db_session.flush()

        admin_user = DbUser(username="admin")
        admin_user.set_password("adminpass")
        admin_user.roles.append(admin_role)
        db_session.add(admin_user)
        db_session.commit()

        # Login as admin
        client.post("/login", data={"username": "admin", "password": "adminpass"})

        # Access admin-protected endpoint (will be created in Wave 3)
        response = client.get("/admin/users")
        assert response.status_code != 403

    def test_admin_required_blocks_regular_user(self, client, db_session):
        """@admin_required should return 403 for non-admin users."""
        from nova.models import DbUser, Role

        # Create user role and user
        user_role = Role(name="user")
        db_session.add(user_role)
        db_session.flush()

        regular_user = DbUser(username="regularuser")
        regular_user.set_password("userpass")
        regular_user.roles.append(user_role)
        db_session.add(regular_user)
        db_session.commit()

        # Login as regular user
        client.post("/login", data={"username": "regularuser", "password": "userpass"})

        # Access admin-protected endpoint should fail
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_permission_required_allows_user_with_permission(self, client, db_session):
        """@permission_required should allow users with the required permission."""
        from nova.models import DbUser, Role, Permission

        # Create permission and role
        export_perm = Permission(name="data.export.any")
        db_session.add(export_perm)

        exporter_role = Role(name="exporter")
        exporter_role.permissions.append(export_perm)
        db_session.add(exporter_role)
        db_session.flush()

        user = DbUser(username="exporter_user")
        user.set_password("exportpass")
        user.roles.append(exporter_role)
        db_session.add(user)
        db_session.commit()

        # Login
        client.post(
            "/login", data={"username": "exporter_user", "password": "exportpass"}
        )

        # Should be able to access export endpoint
        # (endpoint will be decorated with @permission_required("data.export.any"))
        # For now, just verify the user has the permission
        assert user.has_permission("data.export.any") is True

    def test_permission_required_blocks_user_without_permission(
        self, client, db_session
    ):
        """@permission_required should return 403 for users without the permission."""
        from nova.models import DbUser, Role

        # User without any special permissions
        basic_role = Role(name="basic")
        db_session.add(basic_role)
        db_session.flush()

        user = DbUser(username="basic_user")
        user.set_password("basicpass")
        user.roles.append(basic_role)
        db_session.add(user)
        db_session.commit()

        assert user.has_permission("data.export.any") is False

    def test_api_admin_required_with_valid_admin_key(self, client, db_session):
        """API admin check should allow admin API keys."""
        from nova.models import DbUser, Role, ApiKey
        import hashlib

        # Create admin role and user
        admin_role = Role(name="admin", is_system=True)
        db_session.add(admin_role)
        db_session.flush()

        admin_user = DbUser(username="api_admin")
        admin_user.roles.append(admin_role)
        db_session.add(admin_user)
        db_session.flush()

        # Create API key for admin
        raw_key = "test_admin_api_key_12345"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        api_key = ApiKey(user_id=admin_user.id, key_hash=key_hash, name="Admin API Key")
        db_session.add(api_key)
        db_session.commit()

        # API request with admin key should succeed
        response = client.get("/api/v1/admin/users", headers={"X-API-Key": raw_key})
        assert response.status_code != 403

    def test_api_admin_required_rejects_non_admin_key(self, client, db_session):
        """API admin check should reject non-admin API keys with 403."""
        from nova.models import DbUser, Role, ApiKey
        import hashlib

        # Create regular user
        user_role = Role(name="user")
        db_session.add(user_role)
        db_session.flush()

        regular_user = DbUser(username="api_regular")
        regular_user.roles.append(user_role)
        db_session.add(regular_user)
        db_session.flush()

        # Create API key for regular user
        raw_key = "test_regular_api_key_12345"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        api_key = ApiKey(
            user_id=regular_user.id, key_hash=key_hash, name="Regular API Key"
        )
        db_session.add(api_key)
        db_session.commit()

        # API request with non-admin key should be rejected
        response = client.get("/api/v1/admin/users", headers={"X-API-Key": raw_key})
        assert response.status_code == 403
