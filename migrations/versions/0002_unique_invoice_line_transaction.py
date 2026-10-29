"""Prevent double billing: a transaction can be on at most one invoice.

Replaces the plain index on invoice_lines.transaction_id with a UNIQUE index.
It still serves the "is this transaction invoiced?" lookup, and it is the
database backstop that wins races between two invoices claiming the same
transaction. (Upgrading fails if a database already has a transaction on two
invoices; those must be resolved by hand first.)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_invoice_lines_transaction_id", table_name="invoice_lines")
    op.create_index("uq_invoice_lines_transaction_id", "invoice_lines", ["transaction_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_invoice_lines_transaction_id", table_name="invoice_lines")
    op.create_index("ix_invoice_lines_transaction_id", "invoice_lines", ["transaction_id"])
