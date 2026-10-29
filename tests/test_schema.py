"""The migration creates the schema from PLAN.md §3, and models.py matches it."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db import make_engine, run_migrations
from app.models import Base

EXPECTED_TABLES = {
    "transactions",
    "transaction_tax_lines",
    "transaction_fee_lines",
    "flight_details",
    "flight_coupons",
    "hotel_details",
    "hotel_extras",
    "rail_legs",
    "navan_fee_details",
    "invoices",
    "invoice_lines",
    "invoice_tax_rollup",
    "invoice_fee_rollup",
}


@pytest.fixture
def engine(database_url):
    run_migrations(database_url)
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def _indexes(engine, table):
    return {index["name"]: index for index in inspect(engine).get_indexes(table)}


def test_migration_creates_every_table(engine):
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == EXPECTED_TABLES


def test_external_id_has_unique_index(engine):
    index = _indexes(engine, "transactions")["uq_transactions_external_id"]
    assert index["column_names"] == ["external_id"]
    assert index["unique"]


def test_invoice_lines_transaction_id_has_unique_index(engine):
    # Migration 0002: one invoice per transaction (no double billing).
    indexes = _indexes(engine, "invoice_lines")
    assert "ix_invoice_lines_transaction_id" not in indexes
    index = indexes["uq_invoice_lines_transaction_id"]
    assert index["column_names"] == ["transaction_id"]
    assert index["unique"]


def test_invoice_lines_transaction_fk_is_restrict(engine):
    fks = inspect(engine).get_foreign_keys("invoice_lines")
    to_transactions = [fk for fk in fks if fk["referred_table"] == "transactions"]
    assert to_transactions[0]["options"]["ondelete"] == "RESTRICT"


def test_models_match_migration(engine):
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []
