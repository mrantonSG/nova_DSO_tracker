"""
Test for pagination fix - ensures that objects beyond the first 50 are properly loaded.

This test verifies the fix for the 'Data Silencing' bug where objects beyond
index 50 were not being loaded because the API returned total=null for offset > 0.
"""
import pytest

from nova import AstroObject


def test_pagination_has_more_flag(client, db_session):
    """
    Test that the API returns has_more flag correctly and doesn't
    prematurely terminate pagination at index 50.

    This test creates 100 objects and verifies that all 100 are returned
    when paginating with batch size of 50.
    """
    # Get the default user (created by conftest.py client fixture)
    user_id = db_session.query(__import__('nova').DbUser).filter_by(username="default").first().id

    # Create 100 astro objects
    for i in range(100):
        obj = AstroObject(
            user_id=user_id,
            object_name=f"Test Object {i:03d}",
            common_name=f"Common {i:03d}",
            ra_hours=12.0 + (i * 0.01),
            dec_deg=40.0 + (i * 0.01),
            type="Galaxy",
            constellation="Test",
            magnitude="10.0",
            enabled=True
        )
        db_session.add(obj)

    db_session.commit()

    # Simulate the client pagination loop
    BATCH_SIZE = 50
    offset = 0
    total = None
    all_objects = []

    print(f"\n=== Starting Pagination Test with 100 objects ===")

    page_count = 0
    while True:
        print(f"Fetching batch: offset={offset}, limit={BATCH_SIZE}")

        response = client.get(f'/api/get_desktop_data_batch?offset={offset}&limit={BATCH_SIZE}&location=Default Test Loc')

        assert response.status_code == 200, f"API request failed with status {response.status_code}"

        json_data = response.get_json()

        # Verify response structure
        assert 'results' in json_data, "Response missing 'results' field"
        assert 'has_more' in json_data, "Response missing 'has_more' field"

        results = json_data['results']
        has_more = json_data['has_more']

        # Update total if provided (first page)
        if json_data.get('total') is not None:
            total = json_data['total']

        print(f"  - Got {len(results)} results, has_more={has_more}, total={total}")

        all_objects.extend(results)

        # The critical fix: use has_more instead of offset < total
        # This ensures we don't terminate when total is null
        if not has_more or len(results) < BATCH_SIZE:
            break

        offset += BATCH_SIZE
        page_count += 1

        # Safety: prevent infinite loop
        if page_count > 10:
            pytest.fail("Pagination loop did not terminate after 10 pages")

    # Verify all objects were loaded (101 because conftest.py client fixture adds M42)
    print(f"\n=== Pagination Complete ===")
    print(f"Total pages fetched: {page_count + 1}")
    print(f"Total objects loaded: {len(all_objects)}")
    print(f"Expected total: 101 (100 test objects + 1 M42 from conftest.py)")

    assert len(all_objects) == 101, (
        f"Expected 101 objects but only loaded {len(all_objects)}. "
        f"This indicates the 'Data Silencing' bug where pagination terminated early."
    )

    # Verify first page had total count
    assert total is not None, "First page should return total count"
    assert total == 101, f"Expected total=101 but got {total}"

    # Verify no duplicate objects
    object_names = [obj['Object'] for obj in all_objects]
    assert len(object_names) == len(set(object_names)), "Duplicate objects detected in pagination"

    print("✓ All 100 objects successfully loaded without premature termination")
    print("✓ Pagination fix verified - has_more flag working correctly")


def test_pagination_single_page(client, db_session):
    """
    Test that pagination works correctly when results fit in a single page.
    """
    # Get the default user
    user_id = db_session.query(__import__('nova').DbUser).filter_by(username="default").first().id

    # Create only 10 objects (less than batch size)
    for i in range(10):
        obj = AstroObject(
            user_id=user_id,
            object_name=f"Single Page Object {i}",
            common_name=f"Single {i}",
            ra_hours=12.0,
            dec_deg=40.0,
            type="Galaxy",
            constellation="Test",
            magnitude="10.0",
            enabled=True
        )
        db_session.add(obj)

    db_session.commit()

    response = client.get(f'/api/get_desktop_data_batch?offset=0&limit=50&location=Default Test Loc')

    assert response.status_code == 200

    json_data = response.get_json()

    # Verify has_more is False when we have fewer results than limit
    assert json_data['has_more'] is False, "has_more should be False for single page results"
    # 11 objects because conftest.py client fixture adds M42
    assert len(json_data['results']) == 11, f"Should return all 11 objects (10 test + 1 M42 from conftest.py)"
    assert json_data['total'] == 11, "Total should be 11"
