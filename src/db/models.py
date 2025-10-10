"""
Database models and session factory (SQLAlchemy).

Tables:
- ingest_raw: durable store of original JSON as received.
- points: parsed storage of individual fixes (linked to devices).
- devices: registry of hardware trackers.
- riders / race_riders: entrants and their race-specific configuration.
- races / route / categories: event structure and course metadata.
- leaderboard_cache / track_cache: live data surfaces.
- leaderboard_hist / track_hist: archived snapshots.
"""

import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, foreign

# DATABASE_URL examples:
# - Dev (SQLite): sqlite:///enduro_tracker.db
# - Prod (Postgres): postgresql+psycopg2://user:pass@host:5432/dbname
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///enduro_tracker.db")

# set up SQLAlchemy’s core pieces for talking to your database:
'''
engine (src/db/models.py:24) is the database connection factory. create_engine(DATABASE_URL, future=True, echo=False) tells SQLAlchemy which DB to use (via DATABASE_URL), opts into SQLAlchemy 2.x behaviour (future=True), and disables SQL statement logging (echo=False).
SessionLocal (src/db/models.py:25) is a sessionmaker, i.e., a callable that hands you new Session objects pre-bound to engine. Here we configure sessions not to flush pending changes automatically (autoflush=False), to require explicit commits (autocommit=False), and to use the newer 2.x API style (future=True).
Base (src/db/models.py:27) is the declarative base class created by declarative_base(). You subclass it to define ORM models (An ORM (Object–Relational Mapping) model is a Python class that represents a table in a relational database. The ORM layer maps your class attributes to table columns, so you can work with database rows as normal Python objects—creating, querying, updating, and deleting them without writing raw SQL.); it also keeps track of those models’ table metadata so Base.metadata.create_all(bind=engine) can create the tables later.
When you need to interact with the DB you call SessionLocal() to get a Session that uses the shared engine, and your ORM model classes inherit from Base.
'''
# engine creates a DB connection, you can use echo=True to log SQL statements for debugging
engine = create_engine(DATABASE_URL, future=True, echo=False)
# session activates that connection
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
# Base allows you to use python to build tables and converts python to SQL
Base = declarative_base()


class IngestRaw(Base):
    """
    Durable copy of uploaded JSON payloads.
    """
    # when creating a relationship, the forgien key must reference a table that is defined earlier in the file
    # a foreign key is on the many side of a one-to-many relationship

    __tablename__ = "ingest_raw"

    id = Column(Integer, primary_key=True)
    device_id = Column(String(64), index=True, nullable=False)
    payload_json = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


    #device = relationship("Device", back_populates="ingest_records")


class Device(Base):
    """
    Registered hardware device (spot tracker, phone, etc.).
    """

    __tablename__ = "devices"

    id = Column(String(64), primary_key=True)
    device_info = Column(Text, nullable=True)

    # shouldn't need the commented two as relationship is inherited through race_riders
    #ingest_records = relationship("IngestRaw", back_populates="device")
    #points = relationship("Point", back_populates="device")
    race_riders = relationship("RaceRider", back_populates="device")


class Point(Base):
    """
    Parsed position fixes linked to a specific device.
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
    ends_at = Column(DateTime(timezone=True), nullable=True)
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

    route = relationship("Route", back_populates="categories")
    race_riders = relationship("RaceRider", back_populates="category")
    # uselist=False is for a one to one relationship
    leaderboard_cache = relationship("LeaderboardCache", back_populates="category", uselist=False)
    leaderboard_history = relationship("LeaderboardHist", back_populates="category", uselist=False)


class RaceRider(Base):
    """
    Entry linking a rider, device, and category for a race.
    """

    __tablename__ = "race_riders"

    id = Column(Integer, primary_key=True)
    rider_id = Column(Integer, ForeignKey("riders.id"), nullable=False, index=True)
    device_id = Column(String(64), ForeignKey("devices.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)
    comm_setting = Column(String(32), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    recording = Column(Boolean, nullable=False, default=True)

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
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    race_rider = relationship("RaceRider", back_populates="track_history")


def init_db() -> None:
    """
    Create tables if they do not exist.
    """

    Base.metadata.create_all(bind=engine)
