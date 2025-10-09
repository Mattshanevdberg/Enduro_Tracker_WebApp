"""
Database models and session factory (SQLAlchemy).

Tables:
- ingest_raw: durable store of original JSON as received.
- points: parsed storage of individual fixes.

Note:
- Using SQLite in dev for simplicity. Switch DATABASE_URL (env var) to Postgres later.
"""

import os
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Text, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

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

# you pass Base so that the class is recognized as a table (this is 'subclassing' to define ORM models)
# essentially suppies the ORM plumbing to the class (ORM is a type of class that maps to a DB table)
class IngestRaw(Base):
    """
    Durable copy of uploaded JSON payloads.
    Columns:
      id            : PK
      device_id     : device string from header/body
      payload_json  : original JSON string (compact)
      received_at   : server receipt time (UTC)
    """
    __tablename__ = "ingest_raw"
    id = Column(Integer, primary_key=True)
    device_id = Column(String(64), index=True, nullable=False)
    payload_json = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

class Point(Base):
    """
    Optional parsed points table (enable when you want to parse/insert). 
    Columns reflect your compact schema (scaled ints or floats after conversion).
    """
    __tablename__ = "points"
    id = Column(Integer, primary_key=True)
    #rider_id = Column(String(64), index=True, nullable=True)  # optional mapping later
    t = Column(Integer, index=True, nullable=False)           # epoch seconds (UTC)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    alt = Column(Float, nullable=True)
    sog = Column(Float, nullable=True)
    cog = Column(Float, nullable=True)
    fx  = Column(Integer, nullable=True)
    hdop = Column(Float, nullable=True)
    nsat = Column(Integer, nullable=True)
    device_id = Column(String(64), index=True, nullable=False)
    src = Column(String(16), nullable=False, default="cell")  # "cell" | "wifi" | "lora" | "replay"
    race_id = Column(String(64), index=True, nullable=True)  # optional mapping later

def init_db() -> None:
    """
    Create tables if they do not exist.
    Returns: None
    """
    Base.metadata.create_all(bind=engine)

# test init_db() when this module is run directly
# if __name__ == "__main__":
#     init_db()
#     print("Database tables created (if not exist).")  
