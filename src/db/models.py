"""
Database models and session factory (SQLAlchemy).

Tables:
- ingest_raw: durable store of original JSON as received.
- ingest_rfid: durable store of RFID reader tag events as received.
- points: parsed storage of individual fixes (linked to devices).
- devices: registry of hardware trackers.
- riders / race_riders: entrants and their race-specific configuration.
- users / auth_tokens / auth_audit_events: browser login accounts,
  password-reset tokens, and security event history.
- races / route / categories: event structure and course metadata.
- leaderboard_cache / track_cache: live data surfaces.
- leaderboard_hist / track_hist: archived snapshots.
"""

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, foreign
from flask_login import UserMixin

# regular imports 
import os
import yaml
from pathlib import Path

# Load configuration from yaml file
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../configs/config.yaml')
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

#set globals
DATABASE_URL_CONFIG = config['global']['database_url']

# DATABASE_URL examples:
# - Compose / runtime: DATABASE_URL=postgresql+psycopg://user:pass@db:5432/dbname
# - Config fallback:   postgresql+psycopg://user:pass@host:5432/dbname
DATABASE_URL = os.getenv("DATABASE_URL", DATABASE_URL_CONFIG)

# set up SQLAlchemy’s core pieces for talking to your database:

# engine (src/db/models.py:24) is the database connection factory. create_engine(DATABASE_URL, future=True, echo=False) tells SQLAlchemy which DB to use (via DATABASE_URL), opts into SQLAlchemy 2.x behaviour (future=True), and disables SQL statement logging (echo=False).
# SessionLocal (src/db/models.py:25) is a sessionmaker, i.e., a callable that hands you new Session objects pre-bound to engine. Here we configure sessions not to flush pending changes automatically (autoflush=False), to require explicit commits (autocommit=False), and to use the newer 2.x API style (future=True).
# Base (src/db/models.py:27) is the declarative base class created by declarative_base(). You subclass it to define ORM models (An ORM (Object–Relational Mapping) model is a Python class that represents a table in a relational database. The ORM layer maps your class attributes to table columns, so you can work with database rows as normal Python objects—creating, querying, updating, and deleting them without writing raw SQL.); it also keeps track of those models’ table metadata so Base.metadata.create_all(bind=engine) can create the tables later.
# When you need to interact with the DB you call SessionLocal() to get a Session that uses the shared engine, and your ORM model classes inherit from Base.

# engine creates a DB connection, you can use echo=True to log SQL statements for debugging
engine = create_engine(DATABASE_URL, future=True, echo=False)
# session activates that connection
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
# Base allows you to use python to build tables and converts python to SQL
Base = declarative_base()


class IngestRaw(Base):
    """
    Durable copy of uploaded JSON payloads.

    Columns:
      id            : PK
      device_id     : device string from body
      payload_json  : original JSON string (compact), e.g. {"device_id":"pi001","f":[...]}
      received_at   : server receipt time (UTC)
      processed_at  : when parsing to Points succeeded (UTC)  [nullable]
      parse_error   : latest parser error message (if any)    [nullable]
    """
    # when creating a relationship, the forgien key must reference a table that is defined earlier in the file
    # a foreign key is on the many side of a one-to-many relationship

    __tablename__ = "ingest_raw"

    id = Column(Integer, primary_key=True)
    device_id = Column(String(64), index=True, nullable=False)
    payload_json = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    received_at_epoch = Column(Integer, nullable=True)

    # New bookkeeping fields (for parsing to points):
    processed_at = Column(DateTime(timezone=True), nullable=True)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    processed_at_epoch = Column(Integer, nullable=True)
    parse_error = Column(Text, nullable=True)


    #device = relationship("Device", back_populates="ingest_records")


class IngestRfid(Base):
    """
    Durable copy of RFID reader tag events.

    Columns:
      id                : PK
      epc               : RFID EPC/tag value read by the reader
      rssi              : signal strength value from the reader event
      ant               : antenna identifier used for the read
      time_stamp_epoch  : reader event timestamp converted to epoch seconds (UTC)
      reader_id         : RFID reader identifier
      avg_rssi          : average signal strength value from the reader event
      received_at_epoch : server receipt time converted to epoch seconds (UTC)
      processed_at_epoch: when the RFID worker processed this row (UTC epoch) [nullable]
      process_error     : latest RFID worker error/skip reason (if any)       [nullable]

    Relationship:
      - Linked to Device through epc == devices.epc_id when the EPC is registered.
      - This is intentionally not enforced with a database foreign key so unknown
        or false RFID reads can still be ingested and reviewed by the worker/UI.
    """

    __tablename__ = "ingest_rfid"

    id = Column(Integer, primary_key=True)
    epc = Column(String(128), index=True, nullable=False)
    rssi = Column(Float, nullable=True)
    ant = Column(String(64), nullable=True)
    time_stamp_epoch = Column(Integer, index=True, nullable=True)
    reader_id = Column(String(64), index=True, nullable=True)
    avg_rssi = Column(Float, nullable=True)
    received_at_epoch = Column(Integer, nullable=True)
    processed_at_epoch = Column(Integer, index=True, nullable=True)
    process_error = Column(Text, nullable=True)

    # This mirrors the Point/RaceRider loose relationship pattern: RFID reads can be
    # traversed to a registered Device when the EPC matches, but inserts are still
    # allowed for unknown EPC values so false reads are not lost.
    device = relationship(
        "Device",
        primaryjoin=lambda: foreign(IngestRfid.epc) == Device.epc_id,
        viewonly=True,
    )


class Device(Base):
    """
    Registered hardware device (spot tracker, phone, etc.).

    Columns:
      id          : device identifier used by trackers and race_riders
      device_info : optional descriptive text for the hardware
      epc_id      : optional unique RFID EPC/tag assigned to this device

    Relationship:
      - One Device can have many IngestRfid rows when ingest_rfid.epc matches epc_id.
    """

    __tablename__ = "devices"

    id = Column(String(64), primary_key=True)
    device_info = Column(Text, nullable=True)
    epc_id = Column(String(128), nullable=True)

    # EPC values must map to at most one Device. PostgreSQL still permits multiple
    # NULL values, which is useful while older devices have not been assigned tags yet.
    __table_args__ = (
        UniqueConstraint("epc_id", name="ux_devices_epc_id"),
    )

    # shouldn't need the commented two as relationship is inherited through race_riders
    #ingest_records = relationship("IngestRaw", back_populates="device")
    #points = relationship("Point", back_populates="device")
    race_riders = relationship("RaceRider", back_populates="device")
    rfid_records = relationship(
        "IngestRfid",
        primaryjoin=lambda: Device.epc_id == foreign(IngestRfid.epc),
        viewonly=True,
    )


class Point(Base):
    """
    Parsed GNSS points table (per fix).

    units:
      - t_epoch: int (seconds since epoch, UTC)
      - lat, lon: degrees (float)
      - ele: meters (float)
      - sog: knots (float)    [convert to km/h in queries if needed]
      - cog: degrees (float)
      - hdop: dimensionless (float)
      - fx: fix quality (int)
      - nsat: satellites used (int)
      - received_at: server time when we inserted the parsed row

    Idempotency:
      - We expect (device_id, t_epoch) to be unique for a given device.
      - If your device can emit multiple fixes with the same t_epoch, adjust the UniqueConstraint.
    """

    __tablename__ = "points"

    id = Column(Integer, primary_key=True)

    device_id = Column(String(64), index=True, nullable=False)
    t_epoch = Column(Integer, index=True, nullable=False)  # epoch seconds (UTC)

    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    ele = Column(Float, nullable=True)
    sog = Column(Float, nullable=True)
    cog = Column(Float, nullable=True)
    fx = Column(Integer, nullable=True)
    hdop = Column(Float, nullable=True)
    nsat = Column(Integer, nullable=True)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    received_at_epoch = Column(Integer, nullable=True)

    # The following creates a Idempotency constraint on device_id and t_epoch
    # This will prevent duplicates (no two entries can have the same device_id and epoch_t)
    __table_args__ = (
        UniqueConstraint("device_id", "t_epoch", name="ux_points_device_time"),
    )
    
    #device = relationship("Device", back_populates="points")
    # HOW you do a many to many relationship without a direct foreign key constraint
    # A Point can be linked to multiple RaceRiders (if multiple riders share the same device_id), and a RaceRider can have multiple Points (over time).
    # Since there’s no direct foreign key constraint
    # primaryjoin="Point.device_id == RaceRider.device_id" is the join condition SQLAlchemy should use when you access point.race_riders. Because there’s no direct foreign key between points and race_riders, we spell out the link manually: match rows where both tables share the same device_id.
    # viewonly=True makes the relationship read-only. You can traverse from a Point to the matching RaceRider rows, but SQLAlchemy won’t try to manage inserts/updates through that relationship since it isn’t backed by a foreign-key constraint.
    race_riders = relationship(
        "RaceRider",
        primaryjoin=lambda: foreign(Point.device_id) == RaceRider.device_id,
        viewonly=True,
    )


class Rider(Base):
    """
    Core athlete information.
    """

    __tablename__ = "riders"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    bike = Column(String(128), nullable=True)
    bio = Column(Text, nullable=True)
    team = Column(String(128), nullable=True)
    category = Column(String(64), nullable=True)

    race_entries = relationship("RaceRider", back_populates="rider")
    user_account = relationship("User", back_populates="rider", uselist=False)


class User(UserMixin, Base):
    """
    Browser login account for rider and admin users.

    Columns:
      id                  : PK
      first_name          : user's given name
      last_name           : user's surname
      username            : display/login username exactly as last saved
      username_normalized : lower/trimmed username for case-insensitive uniqueness
      email               : email address exactly as last saved
      email_normalized    : lower/trimmed email for case-insensitive uniqueness
      password_hash       : safely hashed password, never the plaintext password
      role                : permission level for logged-in users (`rider` or `admin`)
      rider_id            : optional one-to-one link to the athlete profile row
      is_active           : false disables login without deleting history
      auth_version        : session invalidation counter incremented on reset/deactivation
      created_at          : account creation time (UTC)
      updated_at          : latest account update time (UTC)
      last_login_at       : latest successful login time (UTC) [nullable]

    Relationships:
      - Optional one-to-one link to Rider through rider_id.
      - One User can have many AuthToken rows for one-time auth flows.
      - One User can be actor/target for many AuthAuditEvent rows.

      - Unique constraints on the username_normalized and email_normalized
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    first_name = Column(String(128), nullable=False)
    last_name = Column(String(128), nullable=False)
    username = Column(String(64), nullable=False)
    username_normalized = Column(String(64), nullable=False)
    email = Column(String(256), nullable=False)
    email_normalized = Column(String(256), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, default="rider")
    rider_id = Column(Integer, ForeignKey("riders.id"), nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    auth_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("role IN ('rider', 'admin')", name="ck_users_role"),
        UniqueConstraint("username_normalized", name="ux_users_username_normalized"),
        UniqueConstraint("email_normalized", name="ux_users_email_normalized"),
        UniqueConstraint("rider_id", name="ux_users_rider_id"),
    )

    rider = relationship("Rider", back_populates="user_account")
    auth_tokens = relationship("AuthToken", back_populates="user")
    audit_events_as_actor = relationship(
        "AuthAuditEvent",
        back_populates="actor_user",
        foreign_keys=lambda: [AuthAuditEvent.actor_user_id],
    )
    audit_events_as_target = relationship(
        "AuthAuditEvent",
        back_populates="target_user",
        foreign_keys=lambda: [AuthAuditEvent.target_user_id],
    )


class AuthToken(Base):
    """
    One-time hashed tokens for authentication flows such as password reset.

    Columns:
      id         : PK
      user_id    : account this token belongs to
      purpose    : token purpose, currently `password_reset`
      token_hash : hashed token value, never the raw email link token
      expires_at : timestamp after which the token is invalid
      used_at    : timestamp when the token was consumed [nullable]
      created_at : token creation time (UTC)

    Relationship:
      - Belongs to one User.
    """

    __tablename__ = "auth_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    purpose = Column(String(32), nullable=False)
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", back_populates="auth_tokens")


class AuthAuditEvent(Base):
    """
    Security-relevant account event history. Essentially just a history of who has done what to whom.

    Columns:
      id             : PK
      actor_user_id  : user who performed the action [nullable for public/system events]
      target_user_id : user affected by the action [nullable when not account-specific]
      action         : short event name, e.g. signup, password_reset, promote_admin
      metadata_json  : optional safe JSON details; never store passwords or raw tokens
      created_at     : event creation time (UTC)

    Relationships:
      - actor_user points to the User who performed the action.
      - target_user points to the User affected by the action.
    """

    __tablename__ = "auth_audit_events"

    id = Column(Integer, primary_key=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    actor_user = relationship(
        "User",
        back_populates="audit_events_as_actor",
        foreign_keys=[actor_user_id],
    )
    target_user = relationship(
        "User",
        back_populates="audit_events_as_target",
        foreign_keys=[target_user_id],
    )


class Race(Base):
    """
    Organized event metadata.
    """

    __tablename__ = "races"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    website = Column(String(256), nullable=True)
    starts_at = Column(DateTime(timezone=True), nullable=True)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    starts_at_epoch = Column(Integer, nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    ends_at_epoch = Column(Integer, nullable=True)
    active = Column(Boolean, nullable=False, default=True)

    routes = relationship("Route", back_populates="race")


class Route(Base):
    """
    Course geometry per race.
    """

    # A foreign key is a column (or set of columns) in one table that references the primary key of another table. It tells the database, “every value in this column must match an existing id in that other table.” The database enforces that rule for you:
    #     You can’t insert a race_riders row with device_id="abc" unless "abc" already exists in devices.id.
    #     You can’t delete a device while there are ingest_raw rows still pointing at it (unless you’ve configured cascades)

    __tablename__ = "route"

    id = Column(Integer, primary_key=True)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=False, index=True)
    geojson = Column(Text, nullable=True)
    gpx = Column(Text, nullable=True)

    race = relationship("Race", back_populates="routes")
    categories = relationship("Category", back_populates="route")


class Category(Base):
    """
    Category grouping tied to a route.
    """

    __tablename__ = "categories"

    id = Column(Integer, primary_key=True) 
    route_id = Column(Integer, ForeignKey("route.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False) # store the label (e.g., "Professional", "Open", "Junior")

    # make sure that each category name is unique within a given route
    __table_args__ = (
        UniqueConstraint("route_id", "name", name="ux_route_category_name"),
    )

    route = relationship("Route", back_populates="categories")
    race_riders = relationship("RaceRider", back_populates="category")
    # uselist=False is for a one to one relationship
    leaderboard_cache = relationship("LeaderboardCache", back_populates="category", uselist=False)
    leaderboard_history = relationship("LeaderboardHist", back_populates="category", uselist=False)


class RaceRider(Base):
    """
    Entry linking a rider, device, and category for a race.

    Columns include timing fields from both RFID and Pi sources. multiple_rfid_flag
    marks entries where RFID processing saw extra reads that could not be confidently
    grouped into the start or finish timing window. finish_time_rfid_confirmed
    freezes the accepted RFID finish timing after organiser review.
    """

    __tablename__ = "race_riders"

    id = Column(Integer, primary_key=True)
    rider_id = Column(Integer, ForeignKey("riders.id"), nullable=False, index=True)
    device_id = Column(String(64), ForeignKey("devices.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)
    comm_setting = Column(String(32), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    recording = Column(Boolean, nullable=False, default=True)
    start_time_rfid = Column(DateTime(timezone=True), nullable=True) # when the rider starts the race - set by the RFID on the start line
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    start_time_rfid_epoch = Column(Integer, nullable=True)
    start_time_pi = Column(DateTime(timezone=True), nullable=True) # when the rider starts the race - set by the PI on the start line
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    start_time_pi_epoch = Column(Integer, nullable=True)
    finish_time_rfid = Column(DateTime(timezone=True), nullable=True)# when the rider finishes the race - set by the RFID on the start line
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    finish_time_rfid_epoch = Column(Integer, nullable=True)
    finish_time_pi = Column(DateTime(timezone=True), nullable=True)# when the rider finishes the race - set by the PI on the start line
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    finish_time_pi_epoch = Column(Integer, nullable=True)
    multiple_rfid_flag = Column(Boolean, nullable=False, default=False)
    finish_time_rfid_confirmed = Column(Boolean, nullable=False, default=False)
    #pi_offset_time = Column(Integer, nullable=True) # offset in seconds to apply to the pi's clock to sync with official - removed as can just calculate it

    rider = relationship("Rider", back_populates="race_entries")
    device = relationship("Device", back_populates="race_riders")
    category = relationship("Category", back_populates="race_riders")
    points = relationship(
        "Point",
        primaryjoin=lambda: RaceRider.device_id == foreign(Point.device_id),
        viewonly=True,
    )
    track_cache = relationship("TrackCache", back_populates="race_rider", uselist=False)
    track_history = relationship("TrackHist", back_populates="race_rider", uselist=False)


class LeaderboardCache(Base):
    """
    Live leaderboard cache (single current snapshot per category).
    """

    __tablename__ = "leaderboard_cache"

    category_id = Column(Integer, ForeignKey("categories.id"), primary_key=True)
    payload_json = Column(Text, nullable=False)
    etag = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    updated_at_epoch = Column(Integer, nullable=True)

    category = relationship("Category", back_populates="leaderboard_cache")


class TrackCache(Base):
    """
    Live track cache for a rider entry.
    """

    __tablename__ = "track_cache"

    race_rider_id = Column(Integer, ForeignKey("race_riders.id"), primary_key=True)
    geojson = Column(Text, nullable=True)
    etag = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    updated_at_epoch = Column(Integer, nullable=True)

    race_rider = relationship("RaceRider", back_populates="track_cache")


class LeaderboardHist(Base):
    """
    Archived leaderboard snapshots per category.
    """

    __tablename__ = "leaderboard_hist"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    official_pdf = Column(LargeBinary, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    updated_at_epoch = Column(Integer, nullable=True)

    category = relationship("Category", back_populates="leaderboard_history")


class TrackHist(Base):
    """
    Archived GPS tracks per rider entry.
    """

    __tablename__ = "track_hist"

    id = Column(Integer, primary_key=True)
    race_rider_id = Column(Integer, ForeignKey("race_riders.id"), nullable=False, index=True)
    geojson = Column(Text, nullable=True)
    gpx = Column(Text, nullable=True)
    raw_txt = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    # Phase A: epoch mirror (UTC seconds) for future migration away from DateTime.
    updated_at_epoch = Column(Integer, nullable=True)

    race_rider = relationship("RaceRider", back_populates="track_history")


def init_db() -> None:
    """
    Create tables if they do not exist.
    """

    Base.metadata.create_all(bind=engine)
