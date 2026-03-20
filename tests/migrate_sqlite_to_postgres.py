"""
One-time SQLite to PostgreSQL migration helper.

This standalone helper script copies the project data stored in
`enduro_tracker.db` into the PostgreSQL database referenced by `DATABASE_URL`.
It is intended for the controlled Step 10 migration after the PostgreSQL schema
has already been created and stamped.

USAGE EXAMPLE (BASH):
  DATABASE_URL=postgresql+psycopg://enduro_tracker:EnduroPass@localhost:5432/enduro_tracker \
    .venv/bin/python tests/migrate_sqlite_to_postgres.py

OPTIONAL DRY-RUN EXAMPLE (BASH):
  DATABASE_URL=postgresql+psycopg://enduro_tracker:EnduroPass@localhost:5432/enduro_tracker \
    .venv/bin/python tests/migrate_sqlite_to_postgres.py --tables devices,riders,races
"""

#### for running in vscode (comment out when on Raspberry Pi)
import os
import sys

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
####

from pathlib import Path
from typing import Iterator, Sequence
from urllib.parse import urlsplit, urlunsplit
import argparse

from sqlalchemy import Integer, create_engine, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.schema import Table

from src.db.models import Base


# Anchor all paths to the repository root so the script behaves the same whether
# it is launched from the project root, the tests folder, VS Code, or a shell.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DB = PROJECT_ROOT / "enduro_tracker.db"
DEFAULT_BATCH_SIZE = 1000

# Keep the copy order aligned with the schema dependency order from models.py:
# - route depends on races
# - categories depends on route
# - race_riders depends on riders, devices, and categories
# - leaderboard_* depends on categories
# - track_* depends on race_riders
# ingest_raw / points are independent from the race structure and can be copied
# once the base reference tables are in place.
TABLE_COPY_ORDER = [
    "devices",
    "riders",
    "races",
    "route",
    "categories",
    "race_riders",
    "ingest_raw",
    "points",
    "leaderboard_cache",
    "leaderboard_hist",
    "track_cache",
    "track_hist",
]


def parse_args() -> argparse.Namespace:
    """
    Parse CLI arguments for the SQLite-to-PostgreSQL migration helper.

    Returns
    -------
    argparse.Namespace
        Parsed CLI arguments containing the source DB path, batch size, and
        optional comma-separated table subset.
    """
    parser = argparse.ArgumentParser(
        description="Copy data from enduro_tracker.db into the PostgreSQL database in DATABASE_URL."
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE_DB),
        help="Path to the SQLite source database file (default: %(default)s).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Number of rows to insert per batch (default: %(default)s).",
    )
    parser.add_argument(
        "--tables",
        default=None,
        help=(
            "Optional comma-separated subset of tables to migrate in dependency "
            "order, e.g. devices,riders,races."
        ),
    )
    return parser.parse_args()


def require_database_url() -> str:
    """
    Read and validate the PostgreSQL connection URL from the environment.

    Returns
    -------
    str
        The DATABASE_URL value to use for the PostgreSQL target connection.

    Raises
    ------
    RuntimeError
        If DATABASE_URL is missing or still points at SQLite.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL must be set before running tests/migrate_sqlite_to_postgres.py."
        )
    if database_url.startswith("sqlite"):
        raise RuntimeError(
            "DATABASE_URL must point to PostgreSQL for this migration helper, not SQLite."
        )
    return database_url


def redact_database_url(database_url: str) -> str:
    """
    Return a log-safe DATABASE_URL string with the password masked.

    Parameters
    ----------
    database_url : str
        Full SQLAlchemy connection URL for the PostgreSQL target database.

    Returns
    -------
    str
        Redacted URL suitable for console output.
    """
    parsed = urlsplit(database_url)
    username = parsed.username or ""
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    auth = f"{username}:****@" if username else ""
    return urlunsplit((parsed.scheme, auth + hostname + port, parsed.path, parsed.query, parsed.fragment))


def resolve_tables(raw_tables: str | None) -> list[str]:
    """
    Resolve the requested table subset while preserving dependency order.

    Parameters
    ----------
    raw_tables : str | None
        Optional comma-separated list of table names from the CLI.

    Returns
    -------
    list[str]
        Ordered table names that should be migrated.

    Raises
    ------
    RuntimeError
        If an unknown table name is requested.
    """
    if raw_tables is None:
        return TABLE_COPY_ORDER.copy()

    requested_tables = [name.strip() for name in raw_tables.split(",") if name.strip()]
    unknown_tables = sorted(set(requested_tables) - set(TABLE_COPY_ORDER))
    if unknown_tables:
        raise RuntimeError(
            f"Unknown table(s) requested: {', '.join(unknown_tables)}. "
            f"Allowed tables: {', '.join(TABLE_COPY_ORDER)}"
        )

    # Preserve the canonical dependency order even when the caller supplies a
    # subset, so the dry-run path cannot accidentally violate foreign keys.
    return [name for name in TABLE_COPY_ORDER if name in requested_tables]


def build_engine(url: str) -> Engine:
    """
    Create a SQLAlchemy engine for the supplied URL.

    Parameters
    ----------
    url : str
        SQLAlchemy database URL for either the SQLite source or PostgreSQL target.

    Returns
    -------
    Engine
        SQLAlchemy engine configured for SQLAlchemy 2.x style usage.
    """
    return create_engine(url, future=True)


def ensure_tables_exist(engine: Engine, table_names: Sequence[str], database_label: str) -> None:
    """
    Verify that every expected table exists in the given database.

    Parameters
    ----------
    engine : Engine
        SQLAlchemy engine pointing at the DB to inspect.
    table_names : Sequence[str]
        Table names expected to exist.
    database_label : str
        Short label used in error messages (for example "SQLite source").

    Raises
    ------
    RuntimeError
        If one or more expected tables are missing.
    """
    inspector = inspect(engine)
    missing_tables = [table_name for table_name in table_names if not inspector.has_table(table_name)]
    if missing_tables:
        raise RuntimeError(
            f"{database_label} is missing expected table(s): {', '.join(missing_tables)}"
        )


def get_table(table_name: str) -> Table:
    """
    Fetch the shared SQLAlchemy Table object from the ORM metadata.

    Parameters
    ----------
    table_name : str
        Name of the table to fetch.

    Returns
    -------
    Table
        SQLAlchemy Table object from Base.metadata.

    Raises
    ------
    RuntimeError
        If the table is not defined in the ORM metadata.
    """
    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise RuntimeError(f"Table '{table_name}' is not defined in src/db/models.py metadata.")
    return table


def count_rows(conn: Connection, table: Table) -> int:
    """
    Count rows in a table using the supplied DB connection.

    Parameters
    ----------
    conn : Connection
        Open SQLAlchemy connection to the relevant database.
    table : Table
        SQLAlchemy table to count.

    Returns
    -------
    int
        Row count for the table.
    """
    return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def build_ordered_select(table: Table):
    """
    Build a deterministic SELECT statement for batch reading.

    Parameters
    ----------
    table : Table
        SQLAlchemy table to read from the SQLite source.

    Returns
    -------
    sqlalchemy.sql.Select
        Select statement ordered by the table primary key columns when present.
    """
    primary_key_columns = list(table.primary_key.columns)
    if primary_key_columns:
        return select(table).order_by(*primary_key_columns)
    return select(table)


def iter_source_batches(
    source_conn: Connection,
    table: Table,
    batch_size: int,
) -> Iterator[list[dict[str, object]]]:
    """
    Yield SQLite source rows in insert-ready batches.

    Parameters
    ----------
    source_conn : Connection
        Open SQLite connection used to read the source data.
    table : Table
        SQLAlchemy table definition shared by SQLite and PostgreSQL.
    batch_size : int
        Number of rows to yield at a time.

    Yields
    ------
    list[dict[str, object]]
        Batch of rows converted into plain dictionaries suitable for
        SQLAlchemy executemany-style INSERT execution.
    """
    result = source_conn.execute(build_ordered_select(table)).mappings()

    while True:
        batch = result.fetchmany(batch_size)
        if not batch:
            break
        yield [dict(row) for row in batch]


def reset_postgres_sequence(target_conn: Connection, table: Table) -> None:
    """
    Reset the PostgreSQL sequence behind an integer `id` primary key table.

    Parameters
    ----------
    target_conn : Connection
        Open PostgreSQL connection inside the table transaction.
    table : Table
        SQLAlchemy table whose sequence should be aligned to the copied data.

    Notes
    -----
    - Tables without an integer `id` primary key are skipped.
    - Tables whose primary key is a foreign key cache key (for example
      `track_cache.race_rider_id`) do not have a separate sequence and are also
      skipped automatically.
    """
    id_column = table.c.get("id")
    if id_column is None or not id_column.primary_key or not isinstance(id_column.type, Integer):
        print(f"[migrate] {table.name}: sequence reset skipped (no integer id primary key).")
        return

    sequence_name = target_conn.execute(
        text(f"SELECT pg_get_serial_sequence('{table.name}', 'id')")
    ).scalar_one_or_none()
    if not sequence_name:
        print(f"[migrate] {table.name}: sequence reset skipped (no PostgreSQL sequence found).")
        return

    max_id = target_conn.execute(select(func.max(id_column))).scalar_one()
    if max_id is None:
        target_conn.execute(
            text("SELECT setval(to_regclass(:sequence_name), :sequence_value, :is_called)"),
            {"sequence_name": sequence_name, "sequence_value": 1, "is_called": False},
        )
        print(f"[migrate] {table.name}: sequence reset to start at 1 for an empty table.")
        return

    target_conn.execute(
        text("SELECT setval(to_regclass(:sequence_name), :sequence_value, :is_called)"),
        {"sequence_name": sequence_name, "sequence_value": int(max_id), "is_called": True},
    )
    print(f"[migrate] {table.name}: sequence reset to max(id)={int(max_id)}.")


def copy_table(
    source_conn: Connection,
    target_engine: Engine,
    table_name: str,
    batch_size: int,
) -> None:
    """
    Copy one table from SQLite into PostgreSQL in batched inserts.

    Parameters
    ----------
    source_conn : Connection
        Open SQLite source connection.
    target_engine : Engine
        PostgreSQL engine used for per-table transactional writes.
    table_name : str
        Name of the table to migrate.
    batch_size : int
        Number of rows to insert per batch.

    Raises
    ------
    RuntimeError
        If the PostgreSQL target table is not empty or if the copied row count
        does not match the SQLite source row count.
    """
    table = get_table(table_name)
    source_count = count_rows(source_conn, table)

    print(f"[migrate] {table_name}: source rows = {source_count}")

    with target_engine.begin() as target_conn:
        target_count_before = count_rows(target_conn, table)
        if target_count_before != 0:
            raise RuntimeError(
                f"Target table '{table_name}' is not empty (rows={target_count_before}). "
                "Reset PostgreSQL back to a schema-only state before running the migration."
            )

        inserted_count = 0
        for batch in iter_source_batches(source_conn, table, batch_size):
            # SQLAlchemy executemany-style INSERT keeps the copy efficient while
            # still preserving every column exactly as it appears in SQLite.
            target_conn.execute(table.insert(), batch)
            inserted_count += len(batch)
            print(f"[migrate] {table_name}: inserted {inserted_count}/{source_count}")

        target_count_after = count_rows(target_conn, table)
        if target_count_after != source_count:
            raise RuntimeError(
                f"Row count mismatch for '{table_name}': SQLite={source_count}, PostgreSQL={target_count_after}"
            )

        reset_postgres_sequence(target_conn, table)
        print(f"[migrate] {table_name}: committed {target_count_after} rows")


def migrate_sqlite_to_postgres(
    source_db_path: Path,
    database_url: str,
    table_names: Sequence[str],
    batch_size: int,
) -> None:
    """
    Copy the selected SQLite tables into PostgreSQL in dependency order.

    Parameters
    ----------
    source_db_path : Path
        Path to the SQLite source database file.
    database_url : str
        PostgreSQL SQLAlchemy connection URL read from DATABASE_URL.
    table_names : Sequence[str]
        Ordered table names to migrate.
    batch_size : int
        Number of rows to insert per batch.
    """
    if not source_db_path.exists():
        raise RuntimeError(f"SQLite source DB not found: {source_db_path}")
    if batch_size <= 0:
        raise RuntimeError("--batch-size must be greater than zero.")

    source_engine = build_engine(f"sqlite:///{source_db_path}")
    target_engine = build_engine(database_url)

    try:
        ensure_tables_exist(source_engine, table_names, "SQLite source")
        ensure_tables_exist(target_engine, table_names, "PostgreSQL target")

        with source_engine.connect() as source_conn:
            for table_name in table_names:
                copy_table(source_conn, target_engine, table_name, batch_size)

        print("[migrate] migration completed successfully.")
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> int:
    """
    CLI entry point for the SQLite-to-PostgreSQL migration helper.

    Returns
    -------
    int
        Process exit code (0 success, 1 failure).
    """
    try:
        args = parse_args()
        database_url = require_database_url()
        source_db_path = Path(args.source).expanduser().resolve()
        table_names = resolve_tables(args.tables)

        print(f"[migrate] source SQLite DB: {source_db_path}")
        print(f"[migrate] target PostgreSQL DB: {redact_database_url(database_url)}")
        print(f"[migrate] table order: {', '.join(table_names)}")
        print(f"[migrate] batch size: {args.batch_size}")
        print("[migrate] alembic_version is intentionally excluded; PostgreSQL should already be stamped.")

        migrate_sqlite_to_postgres(
            source_db_path=source_db_path,
            database_url=database_url,
            table_names=table_names,
            batch_size=args.batch_size,
        )
        return 0
    except Exception as e:
        print(f"[migrate] error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
