from sqlalchemy import event, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, Float, UniqueConstraint, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.sql import func

SQLALCHEMY_DATABASE_URL = "sqlite:////saas-data/core.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_size=30,
    max_overflow=50,
    pool_timeout=30,
    pool_recycle=1800,
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA cache_size=-64000")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    can_download = Column(Boolean, default=False)
    last_access = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    preferences = relationship(
        "UserPreferences",
        uselist=False,
        back_populates="owner",
        cascade="all, delete-orphan",
    )
    downloads = relationship("DownloadJob", back_populates="owner", cascade="all, delete-orphan")
    collections = relationship("MediaCollection", back_populates="owner", cascade="all, delete-orphan")


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    storage_limit_gb = Column(Integer, default=10)
    metadata_api_key = Column(String, nullable=True)
    metadata_api_token = Column(String, nullable=True)
    subtitle_provider = Column(String, default="manual")
    youtube_cookies_path = Column(String, nullable=True)
    preferred_quality = Column(String, default="1080p")
    # UI/UX preferences (JSON blob, lives as TEXT in SQLite).
    # Stores: accent, font_size, compact_mode, no_anim, home_order,
    # home_visibility, age_limit, rating_country, default_source, etc.
    ui_prefs = Column(Text, nullable=True)
    # Subtitle provider keys (Wyzie + SubDL) per user
    subdl_api_key = Column(String, nullable=True)
    wyzie_api_key = Column(String, nullable=True)
    opensubtitles_api_key = Column(String, nullable=True)

    owner = relationship("User", back_populates="preferences")


class MediaCollection(Base):
    __tablename__ = "media_collections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    source_url = Column(String, nullable=True)
    folder_path = Column(String, nullable=True)
    auto_sync = Column(Boolean, default=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", back_populates="collections")
    items = relationship("CollectionItem", back_populates="collection", cascade="all, delete-orphan")


class CollectionItem(Base):
    __tablename__ = "collection_items"

    id = Column(Integer, primary_key=True, index=True)
    collection_id = Column(Integer, ForeignKey("media_collections.id"))
    title = Column(String, nullable=False)
    file_path = Column(String, nullable=True)
    source_ref = Column(String, nullable=True)

    collection = relationship("MediaCollection", back_populates="items")


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    input_url = Column(String, nullable=False)
    target_collection_id = Column(Integer, ForeignKey("media_collections.id"), nullable=True)
    new_collection_name = Column(String, nullable=True)
    status = Column(String, default="pending")
    progress_percent = Column(Integer, default=0)
    current_file = Column(String, nullable=True)
    error_log = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Campos extendidos (añadidos vía migración en startup)
    title = Column(String, nullable=True)
    job_type = Column(String, default="yt-dlp")   # yt-dlp | torrent
    info_hash = Column(String, nullable=True)
    local_path = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)

    owner = relationship("User", back_populates="downloads")


class UserProfile(Base):
    __tablename__ = "user_profiles"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    display_name = Column(String, default="")
    bio = Column(Text, default="")
    visibility = Column(String, default="private", nullable=False)
    show_metrics = Column(Boolean, default=False, nullable=False)


class FederationGrant(Base):
    """Inbound credentials: only a digest is stored; a token is shown once."""
    __tablename__ = "federation_grants"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    enabled = Column(Boolean, default=True, nullable=False)
    share_media = Column(Boolean, default=True, nullable=False)
    share_profiles = Column(Boolean, default=False, nullable=False)
    created_at = Column(Float, nullable=False)
    last_seen_at = Column(Float, nullable=True)


class FederationPeer(Base):
    """Outbound peer configuration; bearer credentials remain server-side."""
    __tablename__ = "federation_peers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    base_url = Column(String, nullable=False, unique=True)
    token_ciphertext = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    allow_private = Column(Boolean, default=False, nullable=False)
    status = Column(String, default="pending", nullable=False)
    last_seen_at = Column(Float, nullable=True)
    last_synced_at = Column(Float, nullable=True)
    last_error = Column(String, nullable=True)
    media = relationship("FederatedMedia", cascade="all, delete-orphan", back_populates="peer")


class FederatedMedia(Base):
    __tablename__ = "federated_media"
    id = Column(Integer, primary_key=True)
    peer_id = Column(Integer, ForeignKey("federation_peers.id"), nullable=False, index=True)
    remote_id = Column(String, nullable=False)
    tmdb_id = Column(Integer, index=True)
    media_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    title_key = Column(String, index=True)
    year = Column(Integer)
    season_number = Column(Integer)
    episode_number = Column(Integer)
    size_bytes = Column(Integer, default=0)
    quality = Column(String, default="auto")
    poster_path = Column(String)
    peer = relationship("FederationPeer", back_populates="media")
    __table_args__ = (UniqueConstraint("peer_id", "remote_id", name="unique_federated_media"),)


User.profile = relationship("UserProfile", uselist=False, cascade="all, delete-orphan")


Base.metadata.create_all(bind=engine)


def run_migrations():
    """Añade columnas nuevas a tablas existentes sin romper instalaciones previas."""
    new_columns = [
        ("download_jobs", "title", "TEXT"),
        ("download_jobs", "job_type", "TEXT DEFAULT 'yt-dlp'"),
        ("download_jobs", "info_hash", "TEXT"),
        ("download_jobs", "local_path", "TEXT"),
        ("download_jobs", "file_size", "INTEGER"),
        ("user_preferences", "ui_prefs", "TEXT"),
        ("user_preferences", "subdl_api_key", "TEXT"),
        ("user_preferences", "wyzie_api_key", "TEXT"),
        ("user_preferences", "opensubtitles_api_key", "TEXT"),
        ("users", "can_download", "BOOLEAN DEFAULT 0"),
    ]
    with engine.connect() as conn:
        for table, col, col_type in new_columns:
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
                    )
                )
                conn.commit()
            except Exception:
                pass  # La columna ya existe
