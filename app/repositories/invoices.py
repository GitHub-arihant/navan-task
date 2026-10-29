"""SQL for invoices, invoice lines and rollups. No business rules here.

These functions only touch invoice tables, so reading an invoice can never
accidentally join live transaction data.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Invoice, InvoiceFeeRollup, InvoiceLine, InvoiceTaxRollup

# --- writes -------------------------------------------------------------------


def insert_invoice(session: Session, *, currency: str, total_cents: int, now: str) -> int:
    """Insert the invoice header and return its new id."""
    invoice = Invoice(currency=currency, total_cents=total_cents, created_at=now, updated_at=now)
    session.add(invoice)
    session.flush()
    return invoice.id


def insert_lines(session: Session, invoice_id: int, lines: list[dict]) -> None:
    """Insert one line per included transaction, in order.
    Each dict: transaction_id, type, external_id, total_cents, snapshot (JSON text)."""
    for position, line in enumerate(lines):
        session.add(InvoiceLine(invoice_id=invoice_id, position=position, **line))
    session.flush()


def insert_tax_rollup(session: Session, invoice_id: int, rows: list[dict]) -> None:
    """Each dict: name, rate, amount_cents."""
    for position, row in enumerate(rows):
        session.add(InvoiceTaxRollup(invoice_id=invoice_id, position=position, **row))
    session.flush()


def insert_fee_rollup(session: Session, invoice_id: int, rows: list[dict]) -> None:
    """Each dict: name, amount_cents."""
    for position, row in enumerate(rows):
        session.add(InvoiceFeeRollup(invoice_id=invoice_id, position=position, **row))
    session.flush()


def update_invoice_header(session: Session, invoice_id: int, *, currency: str, total_cents: int, now: str) -> None:
    """Overwrite the header after a PATCH rebuilt the lines."""
    invoice = session.get(Invoice, invoice_id)
    invoice.currency = currency
    invoice.total_cents = total_cents
    invoice.updated_at = now
    session.flush()


def delete_lines_and_rollups(session: Session, invoice_id: int) -> None:
    """Clear the old snapshot before PATCH inserts the rebuilt one (same DB transaction).
    Deleting the lines first also frees their transactions for the unique index."""
    for model in (InvoiceLine, InvoiceTaxRollup, InvoiceFeeRollup):
        session.execute(delete(model).where(model.invoice_id == invoice_id))


def delete_invoice(session: Session, invoice_id: int) -> None:
    """Delete the header; ON DELETE CASCADE removes lines and rollups. Transactions are untouched."""
    session.execute(delete(Invoice).where(Invoice.id == invoice_id))


# --- reads --------------------------------------------------------------------


def get_invoice(session: Session, invoice_id: int) -> Invoice | None:
    """Header row by primary key, or None."""
    return session.get(Invoice, invoice_id)


def list_invoices(session: Session, limit: int, offset: int) -> list[Invoice]:
    """One page of invoice headers, oldest first. Always bounded by SQL LIMIT/OFFSET."""
    return list(session.scalars(select(Invoice).order_by(Invoice.id).limit(limit).offset(offset)))


def get_lines(session: Session, invoice_id: int) -> list[InvoiceLine]:
    return list(
        session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id).order_by(InvoiceLine.position))
    )


def get_tax_rollup(session: Session, invoice_id: int) -> list[InvoiceTaxRollup]:
    return list(
        session.scalars(
            select(InvoiceTaxRollup)
            .where(InvoiceTaxRollup.invoice_id == invoice_id)
            .order_by(InvoiceTaxRollup.position)
        )
    )


def get_fee_rollup(session: Session, invoice_id: int) -> list[InvoiceFeeRollup]:
    return list(
        session.scalars(
            select(InvoiceFeeRollup)
            .where(InvoiceFeeRollup.invoice_id == invoice_id)
            .order_by(InvoiceFeeRollup.position)
        )
    )


def find_invoices_for_transactions(session: Session, transaction_ids: list[int]) -> list[dict]:
    """Which of these transactions are already on an invoice, and which one.

    Uses the unique index on invoice_lines.transaction_id. Returns
    [{"transaction_id", "invoice_id"}] in the order the ids were given.
    """
    rows = session.execute(
        select(InvoiceLine.transaction_id, InvoiceLine.invoice_id).where(
            InvoiceLine.transaction_id.in_(transaction_ids)
        )
    ).all()
    invoice_by_transaction = {row.transaction_id: row.invoice_id for row in rows}
    return [
        {"transaction_id": tid, "invoice_id": invoice_by_transaction[tid]}
        for tid in transaction_ids
        if tid in invoice_by_transaction
    ]
