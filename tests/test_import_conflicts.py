import sys, os
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova import DbUser, AstroObject
from nova.helpers import normalize_object_name


def test_import_conflicts_round_trip_via_file_store(client, db_session, monkeypatch, tmp_path):
    """
    Catalog-import conflicts are persisted to a per-user JSON file in CACHE_DIR,
    can be listed, partially resolved, and fully resolved (which deletes the file).
    """
    # 1. ARRANGE
    monkeypatch.setattr('nova.blueprints.tools.CACHE_DIR', str(tmp_path))

    user = db_session.query(DbUser).filter_by(username="default").one()
    name_a = normalize_object_name("CONFLICT_OBJ_A")
    name_b = normalize_object_name("CONFLICT_OBJ_B")

    db_session.add_all([
        AstroObject(user_id=user.id, object_name=name_a,
                    common_name="Local A", ra_hours=1.0, dec_deg=10.0),
        AstroObject(user_id=user.id, object_name=name_b,
                    common_name="Local B", ra_hours=2.0, dec_deg=20.0),
    ])
    db_session.commit()

    # Same RA/DEC as the DB rows so the only difference is common_name.
    mock_catalog_data = {
        'objects': [
            {'Object': name_a, 'Common Name': 'Catalog A', 'RA': 1.0, 'DEC': 10.0},
            {'Object': name_b, 'Common Name': 'Catalog B', 'RA': 2.0, 'DEC': 20.0},
        ]
    }
    mock_meta_data = {'id': 'conflict_pack', 'name': 'Conflict Pack'}
    mock_load = MagicMock(return_value=(mock_catalog_data, mock_meta_data))
    monkeypatch.setattr('nova.load_catalog_pack', mock_load)
    monkeypatch.setattr('nova.blueprints.tools.load_catalog_pack', mock_load)

    store_file = tmp_path / f"import_conflicts_{user.id}.json"

    # 2. ACT + ASSERT: import produces two pending conflicts, persisted to disk
    response = client.post('/import_catalog/conflict_pack', follow_redirects=True)
    assert response.status_code == 200

    response = client.get('/api/import_conflicts')
    assert response.status_code == 200
    data = response.get_json()
    assert data["pending"] is True
    assert data["pack_name"] == "Conflict Pack"
    assert sorted((c["object_name"], c["field"], c["catalog_value"]) for c in data["conflicts"]) == [
        (name_a, "common_name", "Catalog A"),
        (name_b, "common_name", "Catalog B"),
    ]
    assert store_file.exists()

    # 3. Resolve A with the catalog value; only B remains pending
    response = client.post('/api/resolve_import_conflicts', json={"decisions": {name_a: "catalog"}})
    assert response.status_code == 200
    # The route counts objects with no decision in this request (B) as "kept".
    assert response.get_json() == {"updated": 1, "kept": 1}

    db_session.expire_all()
    obj_a = db_session.query(AstroObject).filter_by(user_id=user.id, object_name=name_a).one()
    assert obj_a.common_name == "Catalog A"

    data = client.get('/api/import_conflicts').get_json()
    assert data["pending"] is True
    assert [c["object_name"] for c in data["conflicts"]] == [name_b]
    assert store_file.exists()

    # 4. Resolve B by keeping the local value; store file is removed
    response = client.post('/api/resolve_import_conflicts', json={"decisions": {name_b: "keep"}})
    assert response.status_code == 200
    assert response.get_json() == {"updated": 0, "kept": 1}

    db_session.expire_all()
    obj_b = db_session.query(AstroObject).filter_by(user_id=user.id, object_name=name_b).one()
    assert obj_b.common_name == "Local B"

    assert not store_file.exists()
    assert client.get('/api/import_conflicts').get_json() == {"pending": False}
