"""
Tests for sharing functionality.

Wave 0 - TDD: These tests are written BEFORE the implementation.
They should FAIL until Wave 4 (sharing endpoints) is complete.
"""

import pytest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# =============================================================================
# T0C: Sharing Endpoint Tests
# =============================================================================


class TestSharingReadEndpoints:
    """Tests for GET /api/v1/shared/* endpoints."""

    def test_get_shared_objects_returns_only_shared_items(self, client, db_session):
        """GET /api/v1/shared/objects should only return objects with is_shared=True."""
        from nova.models import DbUser, AstroObject, Role, Permission

        # Create permission and role for viewing shared items
        view_perm = Permission(name="shared.objects.view")
        db_session.add(view_perm)

        user_role = Role(name="user")
        user_role.permissions.append(view_perm)
        db_session.add(user_role)
        db_session.flush()

        # Create two users
        user1 = DbUser(username="sharer")
        user1.set_password("pass1")
        user1.roles.append(user_role)
        db_session.add(user1)

        user2 = DbUser(username="viewer")
        user2.set_password("pass2")
        user2.roles.append(user_role)
        db_session.add(user2)
        db_session.flush()

        # User1 creates objects - one shared, one private
        shared_obj = AstroObject(
            user_id=user1.id,
            object_name="M31",
            common_name="Andromeda Galaxy",
            ra_hours=0.712,
            dec_deg=41.27,
            is_shared=True,
            shared_notes="My notes on M31",
        )
        private_obj = AstroObject(
            user_id=user1.id,
            object_name="M42",
            common_name="Orion Nebula",
            ra_hours=5.583,
            dec_deg=-5.39,
            is_shared=False,
        )
        db_session.add_all([shared_obj, private_obj])
        db_session.commit()

        # Login as user2 (the viewer)
        client.post("/login", data={"username": "viewer", "password": "pass2"})

        # Request shared objects
        response = client.get("/api/v1/shared/objects")
        assert response.status_code == 200

        data = response.get_json()
        # API wraps response in {"data": {...}}
        objects = data.get("data", {}).get("objects", [])
        object_names = [obj["object_name"] for obj in objects]

        # Should see M31 (shared) but not M42 (private)
        assert "M31" in object_names
        assert "M42" not in object_names

    def test_get_shared_objects_includes_owner_info(self, client, db_session):
        """Shared objects should include information about the original owner."""
        from nova.models import DbUser, AstroObject, Role, Permission

        view_perm = Permission(name="shared.objects.view")
        db_session.add(view_perm)

        user_role = Role(name="user")
        user_role.permissions.append(view_perm)
        db_session.add(user_role)
        db_session.flush()

        owner = DbUser(username="original_owner")
        owner.set_password("pass")
        owner.roles.append(user_role)
        db_session.add(owner)
        db_session.flush()

        shared_obj = AstroObject(
            user_id=owner.id,
            object_name="NGC7000",
            common_name="North America Nebula",
            ra_hours=20.99,
            dec_deg=44.52,
            is_shared=True,
        )
        db_session.add(shared_obj)
        db_session.commit()

        client.post("/login", data={"username": "original_owner", "password": "pass"})

        response = client.get("/api/v1/shared/objects")
        assert response.status_code == 200

        data = response.get_json()
        # API wraps response in {"data": {...}}
        objects = data.get("data", {}).get("objects", [])
        assert len(objects) > 0

        # Each object should have owner username
        for obj in objects:
            assert "owner_username" in obj or "user_id" in obj

    def test_get_shared_components_returns_shared_equipment(self, client, db_session):
        """GET /api/v1/shared/components should return shared equipment."""
        from nova.models import DbUser, Component, Role, Permission

        view_perm = Permission(name="shared.components.view")
        db_session.add(view_perm)

        user_role = Role(name="user")
        user_role.permissions.append(view_perm)
        db_session.add(user_role)
        db_session.flush()

        user = DbUser(username="gear_sharer")
        user.set_password("pass")
        user.roles.append(user_role)
        db_session.add(user)
        db_session.flush()

        shared_scope = Component(
            user_id=user.id,
            kind="telescope",
            name="Celestron C8",
            aperture_mm=203,
            focal_length_mm=2032,
            is_shared=True,
        )
        private_scope = Component(
            user_id=user.id,
            kind="telescope",
            name="My Secret Scope",
            aperture_mm=100,
            focal_length_mm=500,
            is_shared=False,
        )
        db_session.add_all([shared_scope, private_scope])
        db_session.commit()

        client.post("/login", data={"username": "gear_sharer", "password": "pass"})

        response = client.get("/api/v1/shared/components")
        assert response.status_code == 200

        data = response.get_json()
        # API wraps response in {"data": {...}}
        components = data.get("data", {}).get("components", [])
        component_names = [c["name"] for c in components]

        assert "Celestron C8" in component_names
        assert "My Secret Scope" not in component_names

    def test_get_shared_views_returns_shared_saved_views(self, client, db_session):
        """GET /api/v1/shared/views should return shared saved views."""
        from nova.models import DbUser, SavedView, Role, Permission

        view_perm = Permission(name="shared.views.view")
        db_session.add(view_perm)

        user_role = Role(name="user")
        user_role.permissions.append(view_perm)
        db_session.add(user_role)
        db_session.flush()

        user = DbUser(username="view_sharer")
        user.set_password("pass")
        user.roles.append(user_role)
        db_session.add(user)
        db_session.flush()

        shared_view = SavedView(
            user_id=user.id,
            name="My Galaxy Filter",
            description="Great for galaxy season",
            filter_json='{"type": "galaxy"}',
            is_shared=True,
        )
        db_session.add(shared_view)
        db_session.commit()

        client.post("/login", data={"username": "view_sharer", "password": "pass"})

        response = client.get("/api/v1/shared/views")
        assert response.status_code == 200

        data = response.get_json()
        # API wraps response in {"data": {...}}
        views = data.get("data", {}).get("views", [])
        view_names = [v["name"] for v in views]

        assert "My Galaxy Filter" in view_names


class TestForkEndpoints:
    """Tests for POST /api/v1/shared/*/fork endpoints."""

    def test_fork_object_creates_copy_for_current_user(self, client, db_session):
        """Forking a shared object should create a copy owned by the forker."""
        from nova.models import DbUser, AstroObject, Role, Permission

        fork_perm = Permission(name="shared.objects.fork")
        view_perm = Permission(name="shared.objects.view")
        db_session.add_all([fork_perm, view_perm])

        user_role = Role(name="user")
        user_role.permissions.extend([fork_perm, view_perm])
        db_session.add(user_role)
        db_session.flush()

        # Original owner
        owner = DbUser(username="obj_owner")
        owner.set_password("pass1")
        owner.roles.append(user_role)
        db_session.add(owner)

        # User who will fork
        forker = DbUser(username="obj_forker")
        forker.set_password("pass2")
        forker.roles.append(user_role)
        db_session.add(forker)
        db_session.flush()

        # Create shared object
        shared_obj = AstroObject(
            user_id=owner.id,
            object_name="IC1805",
            common_name="Heart Nebula",
            ra_hours=2.556,
            dec_deg=61.45,
            is_shared=True,
        )
        db_session.add(shared_obj)
        db_session.commit()
        shared_id = shared_obj.id

        # Login as forker
        client.post("/login", data={"username": "obj_forker", "password": "pass2"})

        # Fork the object
        response = client.post(f"/api/v1/shared/objects/{shared_id}/fork")
        assert response.status_code in [200, 201]

        # Verify the forked copy exists
        forked = (
            db_session.query(AstroObject)
            .filter_by(user_id=forker.id, object_name="IC1805")
            .first()
        )

        assert forked is not None
        assert forked.user_id == forker.id
        assert forked.original_user_id == owner.id
        assert forked.original_item_id == shared_id
        assert forked.is_shared is False  # Fork is private by default

    def test_fork_sets_provenance_fields(self, client, db_session):
        """Forked item should have original_user_id and original_item_id set."""
        from nova.models import DbUser, Component, Role, Permission

        fork_perm = Permission(name="shared.objects.fork")
        db_session.add(fork_perm)

        user_role = Role(name="user")
        user_role.permissions.append(fork_perm)
        db_session.add(user_role)
        db_session.flush()

        owner = DbUser(username="comp_owner")
        owner.set_password("pass1")
        owner.roles.append(user_role)
        db_session.add(owner)

        forker = DbUser(username="comp_forker")
        forker.set_password("pass2")
        forker.roles.append(user_role)
        db_session.add(forker)
        db_session.flush()

        shared_comp = Component(
            user_id=owner.id,
            kind="camera",
            name="ASI294MC Pro",
            sensor_width_mm=19.1,
            sensor_height_mm=13.0,
            pixel_size_um=4.63,
            is_shared=True,
        )
        db_session.add(shared_comp)
        db_session.commit()
        original_id = shared_comp.id

        client.post("/login", data={"username": "comp_forker", "password": "pass2"})

        response = client.post(f"/api/v1/shared/components/{original_id}/fork")
        assert response.status_code in [200, 201]

        forked = (
            db_session.query(Component)
            .filter_by(user_id=forker.id, name="ASI294MC Pro")
            .first()
        )

        assert forked is not None
        assert forked.original_user_id == owner.id
        assert forked.original_item_id == original_id

    def test_fork_private_item_fails(self, client, db_session):
        """Attempting to fork a non-shared item should fail with 403 or 404."""
        from nova.models import DbUser, AstroObject, Role, Permission

        fork_perm = Permission(name="shared.objects.fork")
        db_session.add(fork_perm)

        user_role = Role(name="user")
        user_role.permissions.append(fork_perm)
        db_session.add(user_role)
        db_session.flush()

        owner = DbUser(username="private_owner")
        owner.set_password("pass1")
        owner.roles.append(user_role)
        db_session.add(owner)

        attacker = DbUser(username="attacker")
        attacker.set_password("pass2")
        attacker.roles.append(user_role)
        db_session.add(attacker)
        db_session.flush()

        private_obj = AstroObject(
            user_id=owner.id,
            object_name="SecretObject",
            ra_hours=0.0,
            dec_deg=0.0,
            is_shared=False,  # NOT shared
        )
        db_session.add(private_obj)
        db_session.commit()
        private_id = private_obj.id

        client.post("/login", data={"username": "attacker", "password": "pass2"})

        response = client.post(f"/api/v1/shared/objects/{private_id}/fork")
        assert response.status_code in [403, 404]

    def test_fork_without_permission_fails(self, client, db_session):
        """User without fork permission should get 403."""
        from nova.models import DbUser, AstroObject, Role, Permission

        # Role WITHOUT fork permission
        readonly_role = Role(name="readonly")
        db_session.add(readonly_role)
        db_session.flush()

        owner = DbUser(username="fork_owner")
        owner.set_password("pass1")
        db_session.add(owner)

        no_fork_user = DbUser(username="no_fork_user")
        no_fork_user.set_password("pass2")
        no_fork_user.roles.append(readonly_role)
        db_session.add(no_fork_user)
        db_session.flush()

        shared_obj = AstroObject(
            user_id=owner.id,
            object_name="ForkTarget",
            ra_hours=0.0,
            dec_deg=0.0,
            is_shared=True,
        )
        db_session.add(shared_obj)
        db_session.commit()
        obj_id = shared_obj.id

        client.post("/login", data={"username": "no_fork_user", "password": "pass2"})

        response = client.post(f"/api/v1/shared/objects/{obj_id}/fork")
        assert response.status_code == 403
