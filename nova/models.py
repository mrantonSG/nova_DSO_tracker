import os
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, Date,
    ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session

# --- DB path setup ---
INSTANCE_PATH = os.environ.get("INSTANCE_PATH") or os.path.join(os.getcwd(), "instance")
os.makedirs(INSTANCE_PATH, exist_ok=True)
DB_PATH = os.path.join(INSTANCE_PATH, 'app.db')
DB_URI = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URI, echo=False, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
Base = declarative_base()


# --- MODELS ------------------------------------------------------------------
class DbUser(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=True)
    active = Column(Boolean, nullable=False, default=True)

    # --- Relationships ---
    locations = relationship("Location", back_populates="user", cascade="all, delete-orphan")
    objects = relationship("AstroObject", foreign_keys="AstroObject.user_id", back_populates="user",
                           cascade="all, delete-orphan")
    saved_views = relationship("SavedView", foreign_keys="SavedView.user_id", back_populates="user",
                               cascade="all, delete-orphan")
    components = relationship("Component", foreign_keys="Component.user_id", back_populates="user",
                              cascade="all, delete-orphan")
    rigs = relationship("Rig", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("JournalSession", back_populates="user", cascade="all, delete-orphan")
    ui_prefs = relationship("UiPref", back_populates="user", uselist=False, cascade="all, delete-orphan")

    # --- This is the line we added to fix the test ---
    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = 'projects'
    # The project_id from YAML will be our primary key. It's a string (UUID).
    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    name = Column(String(256), nullable=False)

    # --- NEW FIELDS FOR PROJECT DETAIL PAGE ---
    target_object_name = Column(String(256), nullable=True)  # Primary object for the project
    description_notes = Column(Text, nullable=True)  # Project-level story/learnings (rich text)
    framing_notes = Column(Text, nullable=True)  # Framing/composition notes (rich text)
    processing_notes = Column(Text, nullable=True)  # Processing workflow (rich text)
    final_image_file = Column(String(256), nullable=True)  # Path to the final image (similar to session_image_file)
    goals = Column(Text, nullable=True)  # Goals and completion status (rich text)
    status = Column(String(32), nullable=False, default="In Progress")  # e.g., 'In Progress', 'Completed', 'On Hold'

    user = relationship("DbUser", back_populates="projects")
    sessions = relationship("JournalSession", back_populates="project")

    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_user_project_name'),)

class SavedView(Base):
    __tablename__ = 'saved_views'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    name = Column(String(256), nullable=False)
    description = Column(String(500), nullable=True) # <-- New
    settings_json = Column(Text, nullable=False)
    is_shared = Column(Boolean, nullable=False, default=False, index=True) # <-- New
    original_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True) # <-- New
    original_item_id = Column(Integer, nullable=True, index=True) # <-- New
    user = relationship("DbUser", foreign_keys=[user_id], back_populates="saved_views")
    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_user_view_name'),)

class Location(Base):
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    name = Column(String(128), nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    timezone = Column(String(64), nullable=False)
    altitude_threshold = Column(Float, nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    comments = Column(String(500), nullable=True)
    user = relationship("DbUser", back_populates="locations")
    horizon_points = relationship("HorizonPoint", back_populates="location", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_user_location_name'),)


class SavedFraming(Base):
    __tablename__ = 'saved_framings'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    object_name = Column(String(256), nullable=False)

    # Framing Data
    rig_id = Column(Integer, ForeignKey('rigs.id', ondelete="SET NULL"), nullable=True)
    rig_name = Column(String(256), nullable=True)
    ra = Column(Float, nullable=True)
    dec = Column(Float, nullable=True)
    rotation = Column(Float, nullable=True)

    # Survey Data
    survey = Column(String(256), nullable=True)
    blend_survey = Column(String(256), nullable=True)
    blend_opacity = Column(Float, nullable=True)

    # Mosaic Data
    mosaic_cols = Column(Integer, default=1)
    mosaic_rows = Column(Integer, default=1)
    mosaic_overlap = Column(Float, default=10.0)

    updated_at = Column(Date, default=datetime.utcnow)

    user = relationship("DbUser", backref="saved_framings")
    __table_args__ = (UniqueConstraint('user_id', 'object_name', name='uq_user_object_framing'),)

class HorizonPoint(Base):
    __tablename__ = 'horizon_points'
    id = Column(Integer, primary_key=True)
    location_id = Column(Integer, ForeignKey('locations.id', ondelete="CASCADE"), index=True)
    az_deg = Column(Float, nullable=False)
    alt_min_deg = Column(Float, nullable=False)
    location = relationship("Location", back_populates="horizon_points")

class AstroObject(Base):
    __tablename__ = 'astro_objects'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    object_name = Column(String(256), nullable=False)
    common_name = Column(String(256), nullable=True)
    ra_hours = Column(Float, nullable=False)
    dec_deg = Column(Float, nullable=False)
    type = Column(String(128), nullable=True)
    constellation = Column(String(64), nullable=True)
    magnitude = Column(String(32), nullable=True)
    size = Column(String(64), nullable=True)
    sb = Column(String(64), nullable=True)
    active_project = Column(Boolean, nullable=False, default=False)
    project_name = Column(Text, nullable=True)
    is_shared = Column(Boolean, nullable=False, default=False, index=True)
    shared_notes = Column(Text, nullable=True)
    original_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True)
    user = relationship("DbUser", foreign_keys=[user_id], back_populates="objects")
    original_item_id = Column(Integer, nullable=True, index=True)
    catalog_sources = Column(Text, nullable=True)
    catalog_info = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)  # New State Flag

    # Curation & Attribution
    image_url = Column(String(500), nullable=True)
    image_credit = Column(String(256), nullable=True)
    image_source_link = Column(String(500), nullable=True)

    description_text = Column(Text, nullable=True)
    description_credit = Column(String(256), nullable=True)
    description_source_link = Column(String(500), nullable=True)

    __table_args__ = (UniqueConstraint('user_id', 'object_name', name='uq_user_object'),)

    def to_dict(self):
        """Converts this object into a YAML-safe dictionary."""
        return {
            # Base fields
            "Object": self.object_name,
            "Common Name": self.common_name,
            "RA (hours)": self.ra_hours,
            "DEC (degrees)": self.dec_deg,
            "Type": self.type,
            "Constellation": self.constellation,
            "Magnitude": self.magnitude,
            "Size": self.size,
            "SB": self.sb,

            # Compatibility aliases
            "Name": self.common_name,
            "RA": self.ra_hours,
            "DEC": self.dec_deg,

            # Project fields
            "ActiveProject": self.active_project,
            "Project": self.project_name,

            # Sharing fields
            "is_shared": self.is_shared,
            "shared_notes": self.shared_notes,
            "original_user_id": self.original_user_id,
            "original_item_id": self.original_item_id,

            # Catalog metadata
            "catalog_sources": self.catalog_sources,
            "catalog_info": self.catalog_info,
            "enabled": self.enabled,

            # Curation & Attribution (Exposed to API)
            "image_url": self.image_url,
            "image_credit": self.image_credit,
            "image_source_link": self.image_source_link,
            "description_text": self.description_text,
            "description_credit": self.description_credit,
            "description_source_link": self.description_source_link
        }


class Component(Base):
    __tablename__ = 'components'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    kind = Column(String(32), nullable=False)  # 'telescope' | 'camera' | 'reducer_extender'
    name = Column(String(256), nullable=False)
    aperture_mm = Column(Float, nullable=True)
    focal_length_mm = Column(Float, nullable=True)
    sensor_width_mm = Column(Float, nullable=True)
    sensor_height_mm = Column(Float, nullable=True)
    pixel_size_um = Column(Float, nullable=True)
    factor = Column(Float, nullable=True)
    is_shared = Column(Boolean, nullable=False, default=False, index=True)
    original_user_id = Column(Integer, ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True)
    original_item_id = Column(Integer, nullable=True, index=True)
    user = relationship("DbUser", foreign_keys=[user_id], back_populates="components")
    rigs_using = relationship("Rig", back_populates="telescope", foreign_keys="Rig.telescope_id")


class Rig(Base):
    __tablename__ = 'rigs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    rig_name = Column(String(256), nullable=False)
    telescope_id = Column(Integer, ForeignKey('components.id', ondelete="SET NULL"), nullable=True)
    camera_id = Column(Integer, ForeignKey('components.id', ondelete="SET NULL"), nullable=True)
    reducer_extender_id = Column(Integer, ForeignKey('components.id', ondelete="SET NULL"), nullable=True)
    effective_focal_length = Column(Float, nullable=True)
    f_ratio = Column(Float, nullable=True)
    image_scale = Column(Float, nullable=True)
    fov_w_arcmin = Column(Float, nullable=True)
    user = relationship("DbUser", back_populates="rigs")
    telescope = relationship("Component", foreign_keys=[telescope_id])
    camera = relationship("Component", foreign_keys=[camera_id])
    reducer_extender = relationship("Component", foreign_keys=[reducer_extender_id])
    __table_args__ = (UniqueConstraint('user_id', 'rig_name', name='uq_user_rig_name'),)


class JournalSession(Base):
    __tablename__ = 'journal_sessions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    project_id = Column(String(64), ForeignKey('projects.id', ondelete="SET NULL"), nullable=True, index=True)
    date_utc = Column(Date, nullable=False)
    object_name = Column(String(256), nullable=True)
    notes = Column(Text, nullable=True)
    session_image_file = Column(String(256), nullable=True)

    # --- NEW & CORRECTED COLUMNS START HERE ---
    location_name = Column(String(128), nullable=True)
    seeing_observed_fwhm = Column(Float, nullable=True)
    sky_sqm_observed = Column(Float, nullable=True)
    moon_illumination_session = Column(Integer, nullable=True)
    moon_angular_separation_session = Column(Float, nullable=True)
    weather_notes = Column(Text, nullable=True)
    telescope_setup_notes = Column(Text, nullable=True)
    filter_used_session = Column(String(128), nullable=True)
    guiding_rms_avg_arcsec = Column(Float, nullable=True)
    guiding_equipment = Column(String(256), nullable=True)
    dither_details = Column(String(256), nullable=True)
    acquisition_software = Column(String(128), nullable=True)
    gain_setting = Column(Integer, nullable=True)
    offset_setting = Column(Integer, nullable=True)
    camera_temp_setpoint_c = Column(Float, nullable=True)
    camera_temp_actual_avg_c = Column(Float, nullable=True)
    binning_session = Column(String(16), nullable=True)
    darks_strategy = Column(Text, nullable=True)
    flats_strategy = Column(Text, nullable=True)
    bias_darkflats_strategy = Column(Text, nullable=True)
    session_rating_subjective = Column(Integer, nullable=True)
    transparency_observed_scale = Column(String(64), nullable=True)
    # --- END OF NEW COLUMNS ---

    number_of_subs_light = Column(Integer, nullable=True)
    exposure_time_per_sub_sec = Column(Integer, nullable=True)
    filter_L_subs = Column(Integer, nullable=True);
    filter_L_exposure_sec = Column(Integer, nullable=True)
    filter_R_subs = Column(Integer, nullable=True);
    filter_R_exposure_sec = Column(Integer, nullable=True)
    filter_G_subs = Column(Integer, nullable=True);
    filter_G_exposure_sec = Column(Integer, nullable=True)
    filter_B_subs = Column(Integer, nullable=True);
    filter_B_exposure_sec = Column(Integer, nullable=True)
    filter_Ha_subs = Column(Integer, nullable=True);
    filter_Ha_exposure_sec = Column(Integer, nullable=True)
    filter_OIII_subs = Column(Integer, nullable=True);
    filter_OIII_exposure_sec = Column(Integer, nullable=True)
    filter_SII_subs = Column(Integer, nullable=True);
    filter_SII_exposure_sec = Column(Integer, nullable=True)

    # --- NEW: Rig Snapshot Fields ---
    rig_id_snapshot = Column(Integer, ForeignKey('rigs.id', ondelete="SET NULL"), nullable=True) # <-- ADDED THIS
    rig_name_snapshot = Column(String(256), nullable=True)
    rig_efl_snapshot = Column(Float, nullable=True)
    rig_fr_snapshot = Column(Float, nullable=True)
    rig_scale_snapshot = Column(Float, nullable=True)
    rig_fov_w_snapshot = Column(Float, nullable=True)
    rig_fov_h_snapshot = Column(Float, nullable=True)
    telescope_name_snapshot = Column(String(256), nullable=True)
    reducer_name_snapshot = Column(String(256), nullable=True)
    camera_name_snapshot = Column(String(256), nullable=True)

    calculated_integration_time_minutes = Column(Float, nullable=True)
    external_id = Column(String(64), nullable=True, index=True)

    user = relationship("DbUser", back_populates="sessions")
    project = relationship("Project", back_populates="sessions")
    rig_snapshot = relationship("Rig", foreign_keys=[rig_id_snapshot]) # <-- ADDED THIS

class UiPref(Base):
    __tablename__ = 'ui_prefs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), index=True)
    json_blob = Column(Text, nullable=True)
    user = relationship("DbUser", back_populates="ui_prefs")
