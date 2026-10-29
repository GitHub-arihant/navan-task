"""The DB plumbing every later phase relies on: file-only, pragmas, BEGIN IMMEDIATE."""

import sqlite3

import pytest
from sqlalchemy import text

from app.db import make_engine, read_session, run_migrations, write_transaction


@pytest.fixture
def engine(database_url):
    run_migrations(database_url)
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def _db_path(database_url: str) -> str:
    return database_url.removeprefix("sqlite:///")


def test_in_memory_database_is_rejected():
    with pytest.raises(ValueError):
        make_engine("sqlite:///:memory:")
    with pytest.raises(ValueError):
        make_engine("sqlite://")


def test_pragmas_are_set(engine):
    with read_session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert session.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert session.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_write_transaction_takes_the_write_lock_up_front(engine, database_url):
    # Another connection that refuses to wait (timeout=0) must find the DB
    # write-locked as soon as write_transaction() is entered, before any SQL.
    other = sqlite3.connect(_db_path(database_url), timeout=0, isolation_level=None)
    try:
        with write_transaction(engine):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        # Lock released after commit.
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
    finally:
        other.close()


def test_read_session_does_not_block_writers(engine, database_url):
    other = sqlite3.connect(_db_path(database_url), timeout=0, isolation_level=None)
    try:
        with read_session(engine) as session:
            session.execute(text("SELECT count(*) FROM transactions")).scalar()
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
    finally:
        other.close()


def test_write_transaction_rolls_back_on_error(engine):
    insert = text(
        "INSERT INTO invoices (currency, total_cents, created_at, updated_at) "
        "VALUES ('EUR', 100, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    with pytest.raises(RuntimeError):
        with write_transaction(engine) as session:
            session.execute(insert)
            raise RuntimeError("boom")

    with read_session(engine) as session:
        assert session.execute(text("SELECT count(*) FROM invoices")).scalar() == 0
