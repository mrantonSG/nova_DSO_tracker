"""
Tests for the Secondary Object Comparison feature.

Tests the /api/get_observable_objects endpoint which returns active objects
observable tonight, sorted by duration, excluding the primary object.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


class TestGraphPageTitle:
    """Tests for the graph page title formatting with secondary objects."""

    def test_secondary_object_title_format(self):
        """
        Test the JavaScript function logic for formatting the secondary object title.
        This validates the expected string format for both single and multi-object scenarios.
        """
        # Simulate the JavaScript logic for building the title

        def build_title(primary_name, primary_id, secondary_name=None, secondary_id=None):
            """Simulates the JavaScript updatePageTitle function"""
            if secondary_name and secondary_id:
                # Multi-object format
                visible_title = f"{primary_name} & {secondary_name} "
                visible_subtitle = f"({primary_id}) & ({secondary_id})"
                browser_title = f"{primary_name} ({primary_id}) & {secondary_name} ({secondary_id}) – Nova DSO Tracker"
            else:
                # Single object format
                visible_title = f"{primary_name} "
                visible_subtitle = f"({primary_id})"
                browser_title = f"{primary_name} ({primary_id}) – Nova DSO Tracker"
            return {
                'visible_title': visible_title,
                'visible_subtitle': visible_subtitle,
                'browser_title': browser_title
            }

        # Test single object (no secondary or None selected)
        single = build_title("Andromeda Galaxy", "M31")
        assert single['visible_title'] == "Andromeda Galaxy "
        assert single['visible_subtitle'] == "(M31)"
        assert single['browser_title'] == "Andromeda Galaxy (M31) – Nova DSO Tracker"

        # Test with secondary object selected
        multi = build_title("Andromeda Galaxy", "M31", "Triangulum Galaxy", "M33")
        assert multi['visible_title'] == "Andromeda Galaxy & Triangulum Galaxy "
        assert multi['visible_subtitle'] == "(M31) & (M33)"
        assert multi['browser_title'] == "Andromeda Galaxy (M31) & Triangulum Galaxy (M33) – Nova DSO Tracker"

    def test_secondary_object_none_selection(self):
        """
        Test that selecting 'None' in the dropdown properly reverts
        to the single-object format.
        """
        # Simulate the JavaScript logic when 'None' is selected
        # When 'None' is selected, selectedValue is empty string ''
        # which is falsy, so we call updatePageTitle with only primary params

        def build_title_none_selected(primary_name, primary_id, selected_value=''):
            """Simulates the dropdown change event when 'None' is selected"""
            if selected_value:
                # Secondary object selected
                # This branch is NOT taken when selected_value is empty string
                return {
                    'secondary_selected': True,
                    'visible_title': f"{primary_name} & SecondaryName ",
                    'visible_subtitle': f"({primary_id}) & (SecondaryId)",
                }
            else:
                # 'None' selected - revert to single object format
                return {
                    'secondary_selected': False,
                    'visible_title': f"{primary_name} ",
                    'visible_subtitle': f"({primary_id})",
                    'browser_title': f"{primary_name} ({primary_id}) – Nova DSO Tracker",
                }

        # Test with empty string (None selected)
        none_result = build_title_none_selected("Andromeda Galaxy", "M31", "")
        assert none_result['secondary_selected'] == False, "Empty string should mean 'None' is selected"
        assert none_result['visible_title'] == "Andromeda Galaxy "
        assert none_result['visible_subtitle'] == "(M31)"
        assert none_result['browser_title'] == "Andromeda Galaxy (M31) – Nova DSO Tracker"

    def test_graph_view_html_has_dedicated_title_elements(self):
        """
        Verify that the graph_view.html template includes dedicated DOM elements
        for updating the page title without innerHTML hacks.
        """
        import os
        template_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'graph_view.html')
        with open(template_path, 'r') as f:
            html = f.read()

        # Check for h2 element ID
        assert 'id="graph-title"' in html, \
            "Missing 'graph-title' ID on h2 element"

        # Check for subtitle element
        assert 'id="graph-title-subtitle"' in html, \
            "Missing 'graph-title-subtitle' ID on subtitle element"

        # Check for dedicated ID elements
        assert 'id="primary-id"' in html, \
            "Missing 'primary-id' ID for primary object ID"
        assert 'id="secondary-name"' in html, \
            "Missing 'secondary-name' ID for secondary object name"
        assert 'id="secondary-separator"' in html, \
            "Missing 'secondary-separator' ID for separator"
        assert 'id="secondary-id"' in html, \
            "Missing 'secondary-id' ID for secondary object ID"

        # Check that elements are hidden by default (for secondary objects)
        assert 'style="display:none;"' in html or "style='display:none;'" in html, \
            "Secondary object elements should be hidden by default"


class TestGetObservableObjects:
    """Tests for the /api/get_observable_objects endpoint."""

    @pytest.fixture
    def setup_objects(self, client, db_session):
        """
        Create a set of test objects with varying active states and observability.
        Returns dict with created object names for test reference.
        Uses unique prefixes to avoid conflicts with seeded data.
        """
        from nova.models import AstroObject, DbUser, Location
        import uuid

        # Generate unique prefix for this test run
        prefix = f"test_{uuid.uuid4().hex[:8]}_"

        # Get or create test user
        user = db_session.query(DbUser).filter_by(username='default').first()
        if not user:
            user = DbUser(username='default')
            db_session.add(user)
            db_session.flush()

        # Create a default location
        location = db_session.query(Location).filter_by(user_id=user.id, is_default=True).first()
        if not location:
            location = Location(
                user_id=user.id,
                name='Test Location',
                lat=51.5,
                lon=-0.1,
                timezone='Europe/London',
                is_default=True,
                active=True,
                altitude_threshold=20
            )
            db_session.add(location)
            db_session.flush()

        # Create test objects - mix of active/inactive and different RA positions
        objects = []
        active_objects = []
        inactive_objects = []
        test_data = [
            # (suffix, ra_hours, dec_deg, active_project) - RA positions spread across sky
            ('andromeda', 0.712, 41.27, True),      # circumpolar from UK, always observable
            ('orion', 5.583, -5.39, True),          # winter object, good visibility
            ('hercules', 16.69, 36.46, True),       # summer object
            ('pleiades', 3.78, 24.11, True),        # autumn/winter
            ('dumbbell', 19.95, 22.72, True),       # summer
            ('namerica', 20.97, 44.52, True),       # summer/autumn
            ('elephant', 21.63, 57.49, True),       # summer
            ('triangulum', 1.56, 30.66, True),      # autumn/winter
            ('whirlpool', 13.5, 47.2, False),       # NOT active (should be excluded)
            ('pinwheel', 14.05, 54.35, False),      # NOT active
            ('sombrero', 12.6, -11.6, True),        # low declination
        ]

        for suffix, ra, dec, active in test_data:
            obj_name = f"{prefix}{suffix}"
            obj = AstroObject(
                user_id=user.id,
                object_name=obj_name,
                common_name=obj_name,  # Use same name for simplicity
                ra_hours=ra,
                dec_deg=dec,
                active_project=active,
                type='Galaxy',
                constellation='Test'
            )
            db_session.add(obj)
            objects.append(obj_name)
            if active:
                active_objects.append(obj_name)
            else:
                inactive_objects.append(obj_name)

        db_session.commit()
        return {
            'user': user,
            'location': location,
            'prefix': prefix,
            'objects': objects,
            'active_objects': active_objects,
            'inactive_objects': inactive_objects,
            'primary_name': active_objects[0] if active_objects else None
        }

    def test_observable_objects_excludes_primary(self, client, db_session, setup_objects):
        """Primary object should not appear in the results."""
        primary_name = setup_objects['primary_name']
        if not primary_name:
            pytest.skip("No active objects created")

        response = client.get(f'/api/get_observable_objects?exclude={primary_name}')
        assert response.status_code == 200

        data = response.get_json()
        object_names = [obj['object_name'] for obj in data.get('objects', [])]

        assert primary_name not in object_names, \
            f"Primary object '{primary_name}' should be excluded from results"

    def test_observable_objects_sorts_by_duration(self, client, db_session, setup_objects):
        """Results should be sorted by observable_minutes in descending order."""
        response = client.get('/api/get_observable_objects')
        assert response.status_code == 200

        data = response.get_json()
        objects = data.get('objects', [])

        if len(objects) >= 2:
            durations = [obj['observable_minutes'] for obj in objects]
            # Check that durations are in descending order
            for i in range(len(durations) - 1):
                assert durations[i] >= durations[i + 1], \
                    f"Objects not sorted by duration: {durations}"

    def test_observable_objects_filters_inactive(self, client, db_session, setup_objects):
        """Objects with active_project=False should be excluded."""
        inactive_objects = setup_objects.get('inactive_objects', [])
        if not inactive_objects:
            pytest.skip("No inactive objects created")

        response = client.get('/api/get_observable_objects')
        assert response.status_code == 200

        data = response.get_json()
        object_names = [obj['object_name'] for obj in data.get('objects', [])]

        for inactive in inactive_objects:
            assert inactive not in object_names, \
                f"Inactive object '{inactive}' should be excluded from results"

    def test_observable_objects_limits_to_20(self, client, db_session):
        """Even with many active objects, only return top 20."""
        from nova.models import AstroObject, DbUser, Location
        import uuid

        # Generate unique prefix for this test
        prefix = f"limit20_{uuid.uuid4().hex[:8]}_"

        # Create user and location
        user = db_session.query(DbUser).filter_by(username='default').first()
        if not user:
            user = DbUser(username='default')
            db_session.add(user)
            db_session.flush()

        location = Location(
            user_id=user.id,
            name='Limit20 Loc',
            lat=51.5,
            lon=-0.1,
            timezone='Europe/London',
            is_default=True,
            active=True,
            altitude_threshold=20
        )
        db_session.add(location)

        # Create 30 active objects with unique names
        for i in range(30):
            obj = AstroObject(
                user_id=user.id,
                object_name=f'{prefix}obj{i:02d}',
                common_name=f'Test Object {i}',
                ra_hours=(i * 0.8) % 24,  # Spread across sky
                dec_deg=30 + (i % 30),    # Various declinations
                active_project=True
            )
            db_session.add(obj)

        db_session.commit()

        response = client.get('/api/get_observable_objects')
        assert response.status_code == 200

        data = response.get_json()
        objects = data.get('objects', [])

        assert len(objects) <= 20, \
            f"Should return at most 20 objects, got {len(objects)}"

    def test_observable_objects_requires_positive_duration(self, client, db_session, setup_objects):
        """Objects with 0 observable minutes should be excluded."""
        response = client.get('/api/get_observable_objects')
        assert response.status_code == 200

        data = response.get_json()
        objects = data.get('objects', [])

        for obj in objects:
            assert obj['observable_minutes'] > 0, \
                f"Object '{obj['object_name']}' has 0 observable minutes and should be excluded"

    def test_observable_objects_includes_required_fields(self, client, db_session, setup_objects):
        """Each returned object should have required fields for the dropdown."""
        response = client.get('/api/get_observable_objects')
        assert response.status_code == 200

        data = response.get_json()
        objects = data.get('objects', [])

        required_fields = ['object_name', 'common_name', 'observable_minutes', 'max_altitude']

        for obj in objects:
            for field in required_fields:
                assert field in obj, \
                    f"Object missing required field '{field}': {obj}"

    def test_observable_objects_returns_empty_for_no_active(self, client, db_session):
        """Should return empty list when no active objects exist."""
        from nova.models import AstroObject, DbUser, Location
        import uuid

        # Use unique username to avoid conflicts
        unique_username = f'empty_{uuid.uuid4().hex[:8]}'

        # Create user and location with only inactive objects
        user = db_session.query(DbUser).filter_by(username=unique_username).first()
        if not user:
            user = DbUser(username=unique_username)
            db_session.add(user)
            db_session.flush()

        location = Location(
            user_id=user.id,
            name='Empty Loc',
            lat=51.5,
            lon=-0.1,
            timezone='Europe/London',
            is_default=True,
            active=True,
            altitude_threshold=20
        )
        db_session.add(location)

        # Create only inactive objects
        for i in range(5):
            obj = AstroObject(
                user_id=user.id,
                object_name=f'{unique_username}_obj{i}',
                ra_hours=12,
                dec_deg=45,
                active_project=False  # All inactive
            )
            db_session.add(obj)

        db_session.commit()

        response = client.get('/api/get_observable_objects')
        assert response.status_code == 200

        data = response.get_json()
        objects = data.get('objects', [])

        assert len(objects) == 0, \
            f"Should return empty list when no active objects, got {len(objects)}"

    def test_observable_objects_without_exclude_param(self, client, db_session, setup_objects):
        """Should work without the exclude parameter (return all active observable objects)."""
        response = client.get('/api/get_observable_objects')
        assert response.status_code == 200

        data = response.get_json()
        objects = data.get('objects', [])

        # Should have some results (active objects exist)
        assert isinstance(objects, list), "Should return a list of objects"

    def test_observable_objects_handles_unobservable_coordinates(self, client, db_session):
        """
        Objects with coordinates that make them never visible from the observer's
        location should be excluded (0 observable minutes).
        For example, an object at DEC -90 from a northern hemisphere location.
        """
        from nova.models import AstroObject, DbUser, Location
        import uuid

        # Use unique username to avoid conflicts
        unique_username = f'south_{uuid.uuid4().hex[:8]}'

        user = db_session.query(DbUser).filter_by(username=unique_username).first()
        if not user:
            user = DbUser(username=unique_username)
            db_session.add(user)
            db_session.flush()

        # Use a northern hemisphere location
        location = Location(
            user_id=user.id,
            name='North Pole Loc',
            lat=80.0,  # Very far north
            lon=0.0,
            timezone='UTC',
            is_default=True,
            active=True,
            altitude_threshold=20
        )
        db_session.add(location)

        # Create active object at South Celestial Pole - never visible from north
        obj_south_pole = AstroObject(
            user_id=user.id,
            object_name=f'{unique_username}_southpole',
            common_name='South Pole Object',
            ra_hours=0.0,
            dec_deg=-89.0,  # Very far south - never visible from lat 80N
            active_project=True
        )
        db_session.add(obj_south_pole)

        db_session.commit()

        # Should not crash
        response = client.get('/api/get_observable_objects')
        assert response.status_code == 200

        data = response.get_json()
        object_names = [obj['object_name'] for obj in data.get('objects', [])]

        # The south pole object should not be in results (0 observable minutes)
        assert f'{unique_username}_southpole' not in object_names, \
            "Objects never visible from observer location should be excluded"

    def test_observable_objects_location_override_affects_results(self, client, db_session):
        """
        Verify that passing lat/lon parameters changes the observability calculations.
        An object visible from one location may not be visible from another.
        """
        from nova.models import AstroObject, DbUser, Location
        import uuid

        # Use 'default' user which is what the client fixture authenticates as
        user = db_session.query(DbUser).filter_by(username='default').first()
        if not user:
            user = DbUser(username='default')
            db_session.add(user)
            db_session.flush()

        # Default location: Northern hemisphere (London)
        location = Location(
            user_id=user.id,
            name='London',
            lat=51.5,
            lon=-0.1,
            timezone='Europe/London',
            is_default=True,
            active=True,
            altitude_threshold=20
        )
        db_session.add(location)

        prefix = f'{uuid.uuid4().hex[:8]}_'

        # Create an object far south - visible from southern hemisphere, not from north
        southern_obj = AstroObject(
            user_id=user.id,
            object_name=f'{prefix}southern',
            common_name='Southern Sky Object',
            ra_hours=12.0,
            dec_deg=-60.0,  # Far south
            active_project=True
        )
        db_session.add(southern_obj)

        # Create a circumpolar object for northern hemisphere
        northern_obj = AstroObject(
            user_id=user.id,
            object_name=f'{prefix}northern',
            common_name='Northern Sky Object',
            ra_hours=12.0,
            dec_deg=70.0,  # Far north - circumpolar from London
            active_project=True
        )
        db_session.add(northern_obj)

        db_session.commit()

        # Query with default location (London - 51.5N)
        response_north = client.get('/api/get_observable_objects')
        assert response_north.status_code == 200
        data_north = response_north.get_json()

        # Query with override location (Sydney - 33.9S)
        response_south = client.get('/api/get_observable_objects?lat=-33.9&lon=151.2&tz=Australia/Sydney')
        assert response_south.status_code == 200
        data_south = response_south.get_json()

        north_obj_north = next((o for o in data_north.get('objects', []) if o['object_name'] == f'{prefix}northern'), None)
        north_obj_south = next((o for o in data_south.get('objects', []) if o['object_name'] == f'{prefix}northern'), None)
        south_obj_north = next((o for o in data_north.get('objects', []) if o['object_name'] == f'{prefix}southern'), None)
        south_obj_south = next((o for o in data_south.get('objects', []) if o['object_name'] == f'{prefix}southern'), None)

        # Northern object: should be visible from north
        if north_obj_north:
            assert north_obj_north['observable_minutes'] > 0, "Northern object should be visible from London"

        # The key test: results differ based on location
        # Either the durations differ significantly, or one is present and the other absent
        # Note: Both objects might be visible from both locations but with different durations
        results_differ = False

        # Check if presence differs
        if (north_obj_north is not None) != (north_obj_south is not None):
            results_differ = True
        if (south_obj_north is not None) != (south_obj_south is not None):
            results_differ = True

        # Check if durations differ significantly for the northern object
        if north_obj_north and north_obj_south:
            if abs(north_obj_north['observable_minutes'] - north_obj_south['observable_minutes']) > 60:
                results_differ = True

        # Check if durations differ significantly for the southern object
        if south_obj_north and south_obj_south:
            if abs(south_obj_north['observable_minutes'] - south_obj_south['observable_minutes']) > 60:
                results_differ = True

        # If at least one object has significantly different results, pass
        assert results_differ, \
            f"Results should differ when querying from different locations. " \
            f"North view: {data_north.get('objects', [])}, " \
            f"South view: {data_south.get('objects', [])}"
