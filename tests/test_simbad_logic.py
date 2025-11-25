import pytest
from unittest.mock import MagicMock, patch
from nova import get_ra_dec, app


@pytest.fixture
def mock_app_context():
    with app.app_context():
        with patch('nova.get_db') as mock_get_db:
            mock_db_session = MagicMock()
            mock_get_db.return_value = mock_db_session
            mock_db_session.query.return_value.filter_by.return_value.one_or_none.return_value = None

            with patch('nova.get_constellation', return_value="Cas"):
                yield


def test_simbad_small_degree_value_is_converted_to_hours(mock_app_context):
    with patch('nova.Simbad') as MockSimbad:
        mock_instance = MockSimbad.return_value

        mock_table = MagicMock()
        # FIX: Define __len__ so len(result) == 1
        mock_table.__len__.return_value = 1
        mock_table.colnames = ['main_id', 'ra', 'dec', 'otype']

        def get_column_data(col_name):
            if col_name.lower() == 'ra': return [14.7557]
            if col_name.lower() == 'dec': return [60.888]
            if col_name.lower() == 'otype': return ['HII']
            return ['Unknown']

        mock_table.__getitem__.side_effect = get_column_data
        mock_instance.query_object.return_value = mock_table

        result = get_ra_dec("IC 63")

        print(f"\nResult: {result}")

        expected_ra = 14.7557 / 15.0
        actual_ra = result.get('RA (hours)')

        assert actual_ra is not None, f"RA is None. Result: {result}"
        assert abs(actual_ra - expected_ra) < 0.001


def test_simbad_large_degree_value(mock_app_context):
    with patch('nova.Simbad') as MockSimbad:
        mock_instance = MockSimbad.return_value
        mock_table = MagicMock()
        # FIX: Define __len__
        mock_table.__len__.return_value = 1
        mock_table.colnames = ['ra', 'dec']

        def get_column_data(col_name):
            if col_name.lower() == 'ra': return [350.5]
            if col_name.lower() == 'dec': return [10.0]
            return ['Unknown']

        mock_table.__getitem__.side_effect = get_column_data
        mock_instance.query_object.return_value = mock_table

        result = get_ra_dec("TestObj")

        actual_ra = result.get('RA (hours)')
        assert actual_ra is not None, f"RA is None. Result: {result}"
        assert abs(actual_ra - (350.5 / 15.0)) < 0.001