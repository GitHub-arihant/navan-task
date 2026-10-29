"""Database engine, SQLite pragmas, migrations, and the two session helpers every request uses.

Reads use `read_session()`. Every write uses `write_transaction()`, which starts the
DB transaction with BEGIN IMMEDIATE. One request = one DB transaction.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_engine(database_url: str) -> Engine:
    """Create the engine for a SQLite *file* and install the pragmas and BEGIN handling.

    The brief forbids in-memory storage, so anything that is not a file-backed
    SQLite URL is rejected here instead of silently working.
    """
    if not database_url.startswith("sqlite:///") or ":memory:" in database_url:
        raise ValueError(f"DATABASE_URL must be a SQLite file URL, got {database_url!r}")

    db_path = Path(database_url.removeprefix("sqlite:///"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # FastAPI runs sync handlers in a thread pool, so a pooled connection may be
    # used by a different thread than the one that opened it.
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):
        # Python's sqlite3 driver normally issues its own (deferred) BEGIN behind
        # our back. Turning that off lets SQLAlchemy's "begin" event below decide
        # exactly which BEGIN is sent. This is the recipe from the SQLAlchemy
        # pysqlite docs.
        dbapi_connection.isolation_level = None

        cursor = dbapi_connection.cursor()
        # SQLite ignores foreign keys (and ON DELETE CASCADE/RESTRICT) unless asked.
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL lets readers keep reading while one writer holds the write lock.
        cursor.execute("PRAGMA journal_mode=WAL")
        # A second writer waits up to 5s for the lock instead of failing at once.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        # Writers ask for BEGIN IMMEDIATE (see write_transaction): it takes the
        # database write lock at the start of the transaction, so a "check then
        # write" sequence (e.g. "is this transaction invoiced?" then UPDATE) cannot
        # interleave with another writer. Reads use a plain deferred BEGIN.
        if conn.get_execution_options().get("begin_immediate"):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            conn.exec_driver_sql("BEGIN")

    return engine


@contextmanager
def read_session(engine: Engine) -> Iterator[Session]:
    """Session for read-only requests. Uses a deferred BEGIN so it never blocks writers (WAL)."""
    with Session(engine, expire_on_commit=False) as session:
        yield session


@contextmanager
def write_transaction(engine: Engine) -> Iterator[Session]:
    """The one way to write: a single DB transaction started with BEGIN IMMEDIATE.

    Commits if the block finishes, rolls back everything if it raises, so a
    request's writes (header + tax lines + fee lines + details) all land or none do.
    """
    write_engine = engine.execution_options(begin_immediate=True)
    with Session(write_engine, expire_on_commit=False) as session:
        with session.begin():
            # Open the connection now so BEGIN IMMEDIATE (and the write lock)
            # happens here, not lazily at the first SQL statement.
            session.connection()
            yield session


def run_migrations(database_url: str) -> None:
    """Bring the database file up to the latest schema (alembic upgrade head)."""
    config = Config()
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
