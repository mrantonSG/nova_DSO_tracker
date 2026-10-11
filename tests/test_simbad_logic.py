import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from nova.helpers import get_ra_dec, list_simbad_common_names, pick_simbad_common_name
from nova import app


@pytest.fixture
def mock_app_context():
    with app.app_context():
        with patch('nova.helpers.get_db') as mock_get_db:
            mock_db_session = MagicMock()
            mock_get_db.return_value = mock_db_session
            mock_db_session.query.return_value.filter_by.return_value.one_or_none.return_value = None

            with patch('nova.helpers.get_constellation', return_value="Cas"):
                yield


def test_simbad_small_degree_value_is_converted_to_hours(mock_app_context):
    with patch('nova.helpers.Simbad') as MockSimbad:
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
    with patch('nova.helpers.Simbad') as MockSimbad:
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


# --- list_simbad_common_names / pick_simbad_common_name: pure unit tests ---

def test_list_simbad_common_names_none_returns_empty_list():
    assert list_simbad_common_names(None) == []


def test_list_simbad_common_names_empty_string_returns_empty_list():
    assert list_simbad_common_names("") == []


def test_list_simbad_common_names_no_name_entries_returns_empty_list():
    assert list_simbad_common_names("NGC  7000|SH  2-117|CTB 100") == []


def test_list_simbad_common_names_bytes_input_is_decoded():
    assert list_simbad_common_names("NAME Horsehead Nebula".encode('utf-8')) == ["Horsehead Nebula"]


def test_list_simbad_common_names_masked_returns_empty_list():
    assert list_simbad_common_names(np.ma.masked) == []


def test_list_simbad_common_names_non_string_returns_empty_list():
    assert list_simbad_common_names(1234) == []


def test_list_simbad_common_names_drops_all_capitals():
    assert list_simbad_common_names("NAME NORTH AMER NEB|NAME North America") == ["North America"]


def test_list_simbad_common_names_collapses_double_spaces():
    assert list_simbad_common_names("NAME  North   America  Nebula ") == ["North America Nebula"]


def test_list_simbad_common_names_removes_duplicates():
    assert list_simbad_common_names("NAME Cygnus Loop|NAME Cygnus Loop") == ["Cygnus Loop"]


# --- real cases: full ordered list and the pick ---

def test_real_case_m51():
    ids = "NAME Whirlpool|NAME Question Mark Galaxy|NAME Whirlpool Galaxy"
    names = list_simbad_common_names(ids)
    assert names == ["Question Mark Galaxy", "Whirlpool Galaxy", "Whirlpool"]
    assert pick_simbad_common_name(names) == "Question Mark Galaxy"


def test_real_case_m33():
    ids = "NAME Triangulum Galaxy|NAME Triangulum Pinwheel"
    names = list_simbad_common_names(ids)
    assert names == ["Triangulum Galaxy", "Triangulum Pinwheel"]
    assert pick_simbad_common_name(names) == "Triangulum Galaxy"


def test_real_case_m31():
    ids = "NAME Andromeda|NAME Andromeda Galaxy|NAME And Nebula|NAME Andromeda Nebula"
    names = list_simbad_common_names(ids)
    assert pick_simbad_common_name(names) == "Andromeda Galaxy"


def test_real_case_m17():
    ids = ("NAME NGC 6618 Horseshoe|NAME Checkmark Nebula|NAME Lobster Nebula|"
           "NAME omega Nebula|NAME Swan Nebula")
    names = list_simbad_common_names(ids)
    assert names[-1] == "NGC 6618 Horseshoe"
    assert pick_simbad_common_name(names) == "Checkmark Nebula"


def test_real_case_m81():
    ids = "NAME M 81*|NAME Bode's Galaxy"
    names = list_simbad_common_names(ids)
    assert pick_simbad_common_name(names) == "Bode's Galaxy"


def test_real_case_ic1805():
    ids = "NAME AFGL 333 Cloud"
    names = list_simbad_common_names(ids)
    assert names == ["AFGL 333 Cloud"]
    assert pick_simbad_common_name(names) is None


def test_real_case_ngc7000():
    ids = ("NAME NORTH AMER NEB|NAME North America|NAME North America Nebula|"
           "NAME Bermuda Cluster")
    names = list_simbad_common_names(ids)
    assert pick_simbad_common_name(names) == "North America Nebula"


# --- get_ra_dec: the new "SIMBAD Names"/"SIMBAD Name" keys on the SIMBAD path ---

def test_get_ra_dec_sets_simbad_names_from_ids(mock_app_context):
    with patch('nova.helpers.Simbad') as MockSimbad:
        mock_instance = MockSimbad.return_value
        mock_table = MagicMock()
        mock_table.__len__.return_value = 1
        mock_table.colnames = ['main_id', 'ra', 'dec', 'otype', 'ids']

        ids_value = ("NAME NORTH AMER NEB|NAME North America|NGC  7000|"
                     "NAME North America Nebula|NAME Bermuda Cluster")

        def get_column_data(col_name):
            if col_name.lower() == 'ra': return [314.6958]
            if col_name.lower() == 'dec': return [44.33]
            if col_name.lower() == 'otype': return ['Cl*']
            if col_name.lower() == 'ids': return [ids_value]
            return ['Unknown']

        mock_table.__getitem__.side_effect = get_column_data
        mock_instance.query_object.return_value = mock_table

        result = get_ra_dec("NGC 7000")

        assert result['SIMBAD Names'] == ["North America Nebula", "Bermuda Cluster", "North America"]
        assert result['SIMBAD Name'] == "North America Nebula"
        assert result['Common Name'] == "NGC 7000"   # still the typed text
        assert result['Type'] == "N/A"


def test_get_ra_dec_simbad_names_empty_without_ids_column(mock_app_context):
    with patch('nova.helpers.Simbad') as MockSimbad:
        mock_instance = MockSimbad.return_value
        mock_table = MagicMock()
        mock_table.__len__.return_value = 1
        mock_table.colnames = ['ra', 'dec']

        def get_column_data(col_name):
            if col_name.lower() == 'ra': return [10.0]
            if col_name.lower() == 'dec': return [20.0]
            return ['Unknown']

        mock_table.__getitem__.side_effect = get_column_data
        mock_instance.query_object.return_value = mock_table

        result = get_ra_dec("TestObj")

        assert result['SIMBAD Names'] == []
        assert result['SIMBAD Name'] is None


# --- modules/nova_data_fetcher: object type and angular size helpers ---

import modules.nova_data_fetcher as ndf


def _ndf_simbad_table(colnames, rows):
    """MagicMock SIMBAD table in the style used above; rows maps column -> [value]."""
    table = MagicMock()
    table.__len__.return_value = 1
    table.colnames = colnames

    def get_column_data(col_name):
        return rows.get(col_name, ['Unknown'])

    table.__getitem__.side_effect = get_column_data
    return table


def test_ndf_object_type_specific_otype_is_returned():
    table = _ndf_simbad_table(
        ['main_id', 'otype', 'morph_type'],
        {'otype': ['PN'], 'morph_type': ['']},
    )
    with patch('modules.nova_data_fetcher.Simbad') as MockSimbad:
        MockSimbad.return_value.query_object.return_value = table
        assert ndf.get_object_type_from_simbad("M 57") == "PN"


def test_ndf_object_type_generic_otype_uses_morph_type():
    table = _ndf_simbad_table(
        ['main_id', 'otype', 'morph_type'],
        {'otype': ['Object'], 'morph_type': ['SA(s)b']},
    )
    with patch('modules.nova_data_fetcher.Simbad') as MockSimbad:
        MockSimbad.return_value.query_object.return_value = table
        assert ndf.get_object_type_from_simbad("TestObj") == "SA(s)b"


def test_ndf_object_type_generic_otype_without_morph_type_returns_none():
    table = _ndf_simbad_table(
        ['main_id', 'otype', 'morph_type'],
        {'otype': ['Object'], 'morph_type': ['']},
    )
    with patch('modules.nova_data_fetcher.Simbad') as MockSimbad:
        MockSimbad.return_value.query_object.return_value = table
        assert ndf.get_object_type_from_simbad("TestObj") is None


def test_ndf_angular_size_galdim_majaxis_is_returned():
    table = _ndf_simbad_table(
        ['main_id', 'galdim_majaxis'],
        {'galdim_majaxis': [199.53]},
    )
    with patch('modules.nova_data_fetcher.Simbad') as MockSimbad:
        MockSimbad.return_value.query_object.return_value = table
        assert ndf.get_angular_size_from_simbad("M 31") == {
            "value": 199.53, "source": "SIMBAD (galdim_majaxis)"
        }


def test_ndf_angular_size_without_galdim_majaxis_returns_none():
    table = _ndf_simbad_table(['main_id'], {})
    with patch('modules.nova_data_fetcher.Simbad') as MockSimbad:
        MockSimbad.return_value.query_object.return_value = table
        assert ndf.get_angular_size_from_simbad("NGC 7000") is None


def test_ndf_object_type_activity_otype_with_morph_type_uses_morph_type():
    table = _ndf_simbad_table(
        ['main_id', 'otype', 'morph_type'],
        {'otype': ['AGN'], 'morph_type': ['SA(s)b']},
    )
    with patch('modules.nova_data_fetcher.Simbad') as MockSimbad:
        MockSimbad.return_value.query_object.return_value = table
        assert ndf.get_object_type_from_simbad("M 31") == "SA(s)b"


def test_ndf_object_type_activity_otype_without_morph_type_returns_otype():
    table = _ndf_simbad_table(
        ['main_id', 'otype', 'morph_type'],
        {'otype': ['AGN'], 'morph_type': ['']},
    )
    with patch('modules.nova_data_fetcher.Simbad') as MockSimbad:
        MockSimbad.return_value.query_object.return_value = table
        assert ndf.get_object_type_from_simbad("M 31") == "AGN"