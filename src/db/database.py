from sqlalchemy import create_engine, text

engine = create_engine("sqlite:///enduro_tracker.db", echo=True)

with engine.connect() as conn:
    conn.execute(text("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY, message TEXT)"))
    conn.execute(text("INSERT INTO test (message) VALUES ('Database connected!')"))
    conn.commit()
