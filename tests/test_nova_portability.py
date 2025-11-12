import pytest
import sys, os
import yaml
import io
import zipfile
from datetime import date
from unittest.mock import

# 1. Add the project's parent directory to the system path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 2. Import models and db helpers
from nova import (
    app,
    DbUser,
    AstroObject,
    get_db,
    Location,
    Rig,
    Component,
    JournalSession,
    Project,
    UPLOAD_FOLDER,
)


# Note: We don't need to import the 'client' fixture, pytest provides it.

def test_download_config_yaml(client):
    """
    Tests that the config download endpoint works and contains
    the correct data from the database.
    """
    # 1. ARRANGE
    db = get_db()
    user = db.query(DbUser).filter_by(username="default").one()

    # Add an object to the DB. (The client fixture already added a location)
    m42 = AstroObject(
        user_id=user.id,
        object_name="M42",
        common_name="Orion Nebula",
        ra_hours=5.58,
        dec_deg=-5.4
    )
    db.add(m42)
    db.commit()

    # 2. ACT
    response = client.get('/download_config')

    # 3. ASSERT
    assert response.status_code == 200
    assert response.mimetype == 'text/yaml'
    assert response.headers['Content-Disposition'] == 'attachment; filename=config_default.yaml'

    # Load the YAML data from the response
    data = response.data.decode('utf-8')
    config_data = yaml.safe_load(data)

    # Check that the data from the DB is in the file
    assert config_data is not None
    assert config_data['default_location'] == "Default Test Loc"
    assert config_data['locations']['Default Test Loc']['lat'] == 50.0

    assert len(config_data['objects']) == 1
    assert config_data['objects'][0]['Object'] == "M42"
    assert config_data['objects'][0]['Common Name'] == "Orion Nebula"


def test_import_config_yaml(client):
    """
    Tests that a user can upload a new config YAML file.
    This test will REPLACE all existing data.
    """
    # 1. ARRANGE
    db = get_db()

    # Define a new, simple config to upload
    new_config_yaml = """
    default_location: New Home
    locations:
        New Home:
            lat: 40.7
            lon: -74.0
            timezone: America/New_York
            active: true
    objects:
        - Object: M31
          Common Name: Andromeda
          RA: 0.75
          DEC: 41.2
    """
    # Convert the string to a file-like object
    mock_file = io.BytesIO(new_config_yaml.encode('utf-8'))

    # 2. ACT
    # We send the file as multipart/form-data
    response = client.post(
        '/import_config',
        data={
            'file': (mock_file, 'test_config.yaml')
        },
        follow_redirects=True
    )

    # 3. ASSERT
    assert response.status_code == 200  # Landed back on config page
    assert b"Config imported and synced to database successfully!" in response.data

    # Check the database *directly*
    user = db.query(DbUser).filter_by(username="default").one()

    # Check that the OLD location is gone
    old_loc = db.query(Location).filter_by(user_id=user.id, name="Default Test Loc").one_or_none()
    assert old_loc is None

    # Check that the NEW object exists
    new_obj = db.query(AstroObject).filter_by(user_id=user.id, object_name="M31").one_or_none()
    assert new_obj is not None
    assert new_obj.common_name == "Andromeda"

    # Check that the NEW location exists
    new_loc = db.query(Location).filter_by(user_id=user.id, name="New Home").one_or_none()
    assert new_loc is not None
    assert new_loc.lat == 40.7


def test_import_rig_config(client):
    """
    Tests that a user can upload a new rig config YAML file.
    This test will REPLACE all existing components and rigs.
    """
    # 1. ARRANGE
    db = get_db()
    user = db.query(DbUser).filter_by(username="default").one()

    # (Optional) Add a dummy rig to make sure it gets deleted
    old_comp = Component(user_id=user.id, kind="telescope", name="Old Scope")
    db.add(old_comp)
    db.commit()

    # Define a new, simple rig config to upload
    new_rig_yaml = """
    components:
      telescopes:
        - id: 1
          name: "Test Scope"
          aperture_mm: 80
          focal_length_mm: 480
      cameras:
        - id: 1
          name: "Test Camera"
          pixel_size_um: 3.76
          sensor_width_mm: 17.6
          sensor_height_mm: 13.3
    rigs:
      - rig_name: "My Test Rig"
        telescope_id: 1
        camera_id: 1
    """
    mock_file = io.BytesIO(new_rig_yaml.encode('utf-8'))

    # 2. ACT
    response = client.post(
        '/import_rig_config',
        data={
            'file': (mock_file, 'test_rigs.yaml')
        },
        follow_redirects=True
    )

    # 3. ASSERT
    assert response.status_code == 200  # Landed back on config page
    assert b"Rigs configuration imported and synced to database successfully!" in response.data

    # Check the database *directly*

    # Check that the OLD component is gone
    old_comp_check = db.query(Component).filter_by(name="Old Scope").one_or_none()
    assert old_comp_check is None

    # Check that the NEW components exist
    new_scope = db.query(Component).filter_by(user_id=user.id, name="Test Scope").one_or_none()
    assert new_scope is not None
    assert new_scope.aperture_mm == 80

    new_cam = db.query(Component).filter_by(user_id=user.id, name="Test Camera").one_or_none()
    assert new_cam is not None
    assert new_cam.pixel_size_um == 3.76

    # Check that the NEW rig exists and is linked
    new_rig = db.query(Rig).filter_by(user_id=user.id, rig_name="My Test Rig").one_or_none()
    assert new_rig is not None
    assert new_rig.telescope_id == new_scope.id
    assert new_rig.camera_id == new_cam.id


def test_download_journal_yaml(client):
    """
    Tests that the journal download endpoint works and contains
    the correct data from the database.
    """
    # 1. ARRANGE
    db = get_db()
    user = db.query(DbUser).filter_by(username="default").one()

    # Add a project and a session
    proj = Project(id="proj1", user_id=user.id, name="Test Project")
    db.add(proj)

    sess = JournalSession(
        user_id=user.id,
        project_id="proj1",
        date_utc=date(2025, 10, 20),
        object_name="M31",
        notes="Test session notes",
        calculated_integration_time_minutes=120
    )
    db.add(sess)
    db.commit()

    # 2. ACT
    response = client.get('/download_journal')

    # 3. ASSERT
    assert response.status_code == 200
    assert response.mimetype == 'text/yaml'
    assert response.headers['Content-Disposition'] == 'attachment; filename=journal_default.yaml'

    # Load the YAML data from the response
    journal_data = yaml.safe_load(response.data.decode('utf-8'))

    # Check that the data from the DB is in the file
    assert journal_data is not None
    assert len(journal_data['projects']) == 1
    assert journal_data['projects'][0]['project_name'] == "Test Project"

    assert len(journal_data['sessions']) == 1
    assert journal_data['sessions'][0]['target_object_id'] == "M31"
    assert journal_data['sessions'][0]['general_notes_problems_learnings'] == "Test session notes"
    assert journal_data['sessions'][0]['calculated_integration_time_minutes'] == 120


def test_import_journal_yaml(client):
    """
    Tests that a user can upload a new journal YAML file.
    This test *adds* to existing data (it's an upsert).
    """
    # 1. ARRANGE
    db = get_db()

    # Define a new, simple journal to upload
    new_journal_yaml = """
    projects:
      - project_id: "new_proj_1"
        project_name: "New Galaxy Project"
    sessions:
      - session_id: "session_abc"
        project_id: "new_proj_1"
        session_date: "2024-01-15"
        target_object_id: "NGC104"
        general_notes_problems_learnings: "Imported notes"
    """
    mock_file = io.BytesIO(new_journal_yaml.encode('utf-8'))

    # 2. ACT
    response = client.post(
        '/import_journal',
        data={
            'file': (mock_file, 'test_journal.yaml')
        },
        follow_redirects=True
    )

    # 3. ASSERT
    assert response.status_code == 200  # Landed back on config page
    assert b"Journal imported and synced to database successfully!" in response.data

    # Check the database *directly*
    user = db.query(DbUser).filter_by(username="default").one()

    # Check that the NEW project exists
    new_proj = db.query(Project).filter_by(user_id=user.id, name="New Galaxy Project").one_or_none()
    assert new_proj is not None
    assert new_proj.id == "new_proj_1"

    # Check that the NEW session exists
    new_sess = db.query(JournalSession).filter_by(user_id=user.id, object_name="NGC 104").one_or_none()
    assert new_sess is not None
    assert new_sess.notes == "Imported notes"
    assert new_sess.project_id == "new_proj_1"
    assert new_sess.external_id == "session_abc"


def test_import_journal_photos_zip(client, monkeypatch, tmp_path):  # <-- Add fixtures
    """
    Tests that a user can upload a ZIP file of photos.
    It should extract the files flatly into the user's upload dir.
    """
    # 1. ARRANGE
    # --- START FIX ---
    # Create a temporary folder for this test
    mock_upload_folder = tmp_path / "uploads"
    # Tell the app to use this temporary folder
    monkeypatch.setattr('nova.UPLOAD_FOLDER', str(mock_upload_folder))
    # --- END FIX ---

    # Create a mock zip file in memory
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, 'w') as zf:
        zf.writestr('uploads/some_other_user/image1.jpg', b'image data one')
        zf.writestr('image2.png', b'image data two')
    mem_zip.seek(0)

    # 2. ACT
    response = client.post(
        '/import_journal_photos',
        data={'file': (mem_zip, 'test_photos.zip')},
        follow_redirects=True
    )

    # 3. ASSERT
    assert response.status_code == 200
    assert b"Extracted 2 files." in response.data

    # --- START FIX ---
    # Check the temporary folder we created
    upload_dir = os.path.join(mock_upload_folder, 'default')
    # --- END FIX ---

    file1_path = os.path.join(upload_dir, 'image1.jpg')
    assert os.path.exists(file1_path)
    with open(file1_path, 'rb') as f:
        assert f.read() == b'image data one'

    file2_path = os.path.join(upload_dir, 'image2.png')
    assert os.path.exists(file2_path)
    with open(file2_path, 'rb') as f:
        assert f.read() == b'image data two'


def test_download_journal_photos_zip(client, monkeypatch, tmp_path):  # <-- Add fixtures
    """
    Tests that the server correctly zips and serves the user's photos.
    """
    # 1. ARRANGE
    # --- START FIX ---
    # Create a temporary folder for this test
    mock_upload_folder = tmp_path / "uploads"
    # Tell the app to use this temporary folder
    monkeypatch.setattr('nova.UPLOAD_FOLDER', str(mock_upload_folder))

    # Create mock files in the 'default' user's *temporary* upload directory
    upload_dir = os.path.join(mock_upload_folder, 'default')
    # --- END FIX ---

    os.makedirs(upload_dir, exist_ok=True)

    with open(os.path.join(upload_dir, 'my_photo_1.jpg'), 'wb') as f:
        f.write(b'jpeg data')
    with open(os.path.join(upload_dir, 'my_photo_2.png'), 'wb') as f:
        f.write(b'png data')

    # 2. ACT
    response = client.get('/download_journal_photos')

    # 3. ASSERT
    assert response.status_code == 200
    assert response.mimetype == 'application/zip'
    assert response.headers['Content-Disposition'].startswith('attachment; filename=nova_journal_photos_default_')

    # Check the contents of the zip file
    mem_zip = io.BytesIO(response.data)
    with zipfile.ZipFile(mem_zip, 'r') as zf:
        namelist = zf.namelist()
        assert 'my_photo_1.jpg' in namelist
        assert 'my_photo_2.png' in namelist

        with zf.open('my_photo_1.jpg') as f:
            assert f.read() == b'jpeg data'
        with zf.open('my_photo_2.png') as f:
            assert f.read() == b'png data'

# TODO: Add more tests here
# def test_download_journal_yaml(client):
#     ...
#
# def test_import_journal_yaml(client):
#     ...
#
# def test_import_rig_config(client):
#     ...
#
# def test_download_journal_photos_zip(client):
#     ...
#
# def test_import_journal_photos_zip(client):
#     ...