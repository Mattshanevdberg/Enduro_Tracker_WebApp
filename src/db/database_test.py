import os

from sqlalchemy import create_engine, text

# Use the same DATABASE_URL as the rest of the application so this helper tests
# the active runtime database instead of a stale local database fallback.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL must be set to run src/db/database_test.py against the active PostgreSQL database."
    )

engine = create_engine(DATABASE_URL, echo=True)

with engine.connect() as conn:
    conn.execute(text("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, message TEXT)"))
    conn.execute(text("INSERT INTO test (message) VALUES ('Database connected!')"))
    conn.commit()
