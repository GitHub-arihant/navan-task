"""Initial schema: transactions (+ taxes, fees, per-type details) and invoice snapshots.

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Transaction side -------------------------------------------------

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("occurred_at", sa.String(40), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.CheckConstraint("type IN ('flight', 'hotel', 'rail', 'navan_fee')", name="ck_transactions_type"),
        sa.CheckConstraint("length(currency) = 3", name="ck_transactions_currency_length"),
        sqlite_autoincrement=True,
    )
    # Duplicate identity is enforced here, by the database.
    op.create_index("uq_transactions_external_id", "transactions", ["external_id"], unique=True)

    op.create_table(
        "transaction_tax_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("rate", sa.String(32), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_transaction_tax_lines"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_transaction_tax_lines_transaction_id_transactions", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_transaction_tax_lines_transaction_id", "transaction_tax_lines", ["transaction_id"])

    op.create_table(
        "transaction_fee_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_transaction_fee_lines"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_transaction_fee_lines_transaction_id_transactions", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_transaction_fee_lines_transaction_id", "transaction_fee_lines", ["transaction_id"])

    op.create_table(
        "flight_details",
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(64), nullable=False),
        sa.Column("destination", sa.String(64), nullable=False),
        sa.Column("fare_cents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id", name="pk_flight_details"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_flight_details_transaction_id_transactions", ondelete="CASCADE",
        ),
    )

    op.create_table(
        "flight_coupons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("flight_number", sa.String(16), nullable=False),
        sa.Column("carrier", sa.String(16), nullable=True),
        sa.Column("from_airport", sa.String(64), nullable=False),
        sa.Column("to_airport", sa.String(64), nullable=False),
        sa.Column("departure", sa.String(40), nullable=False),
        sa.Column("arrival", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_flight_coupons"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_flight_coupons_transaction_id_transactions", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_flight_coupons_transaction_id", "flight_coupons", ["transaction_id"])

    op.create_table(
        "hotel_details",
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("street", sa.String(255), nullable=False),
        sa.Column("city", sa.String(128), nullable=False),
        sa.Column("country", sa.String(64), nullable=False),
        sa.Column("check_in", sa.String(10), nullable=False),
        sa.Column("check_out", sa.String(10), nullable=False),
        sa.Column("nights", sa.Integer(), nullable=False),
        sa.Column("room_rate_per_night_cents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id", name="pk_hotel_details"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_hotel_details_transaction_id_transactions", ondelete="CASCADE",
        ),
    )

    op.create_table(
        "hotel_extras",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_hotel_extras"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_hotel_extras_transaction_id_transactions", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_hotel_extras_transaction_id", "hotel_extras", ["transaction_id"])

    op.create_table(
        "rail_legs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(128), nullable=False),
        sa.Column("destination", sa.String(128), nullable=False),
        sa.Column("departure", sa.String(40), nullable=False),
        sa.Column("arrival", sa.String(40), nullable=False),
        sa.Column("train_number", sa.String(32), nullable=True),
        sa.Column("travel_class", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_rail_legs"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_rail_legs_transaction_id_transactions", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_rail_legs_transaction_id", "rail_legs", ["transaction_id"])

    op.create_table(
        "navan_fee_details",
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("fee_kind", sa.String(32), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("related_external_id", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("transaction_id", name="pk_navan_fee_details"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_navan_fee_details_transaction_id_transactions", ondelete="CASCADE",
        ),
        sa.CheckConstraint("fee_kind IN ('trip_fee', 'agent_call_fee')", name="ck_navan_fee_details_fee_kind"),
    )

    # --- Invoice side (frozen snapshot) ---------------------------------------

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invoices"),
        sa.CheckConstraint("length(currency) = 3", name="ck_invoices_currency_length"),
        sqlite_autoincrement=True,
    )

    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_lines"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"],
            name="fk_invoice_lines_invoice_id_invoices", ondelete="CASCADE",
        ),
        # RESTRICT: a billed transaction cannot be deleted, even by raw SQL.
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"],
            name="fk_invoice_lines_transaction_id_transactions", ondelete="RESTRICT",
        ),
    )
    # Also serves "load lines for invoice X" (invoice_id is the leading column).
    op.create_index(
        "uq_invoice_lines_invoice_id_transaction_id", "invoice_lines",
        ["invoice_id", "transaction_id"], unique=True,
    )
    # Makes "is this transaction on any invoice?" cheap (PATCH/DELETE transaction).
    op.create_index("ix_invoice_lines_transaction_id", "invoice_lines", ["transaction_id"])

    op.create_table(
        "invoice_tax_rollup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("rate", sa.String(32), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_tax_rollup"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"],
            name="fk_invoice_tax_rollup_invoice_id_invoices", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_invoice_tax_rollup_invoice_id", "invoice_tax_rollup", ["invoice_id"])

    op.create_table(
        "invoice_fee_rollup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_fee_rollup"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"],
            name="fk_invoice_fee_rollup_invoice_id_invoices", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_invoice_fee_rollup_invoice_id", "invoice_fee_rollup", ["invoice_id"])


def downgrade() -> None:
    # Children before parents so foreign keys never dangle.
    for table in [
        "invoice_fee_rollup",
        "invoice_tax_rollup",
        "invoice_lines",
        "invoices",
        "navan_fee_details",
        "rail_legs",
        "hotel_extras",
        "hotel_details",
        "flight_coupons",
        "flight_details",
        "transaction_fee_lines",
        "transaction_tax_lines",
        "transactions",
    ]:
        op.drop_table(table)
