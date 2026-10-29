"""Invoice rules: build a frozen snapshot of the chosen transactions, with tax/fee rollups.

An invoice copies each transaction's full API representation into
invoice_lines.snapshot at creation time. Reading an invoice only reads invoice
tables, so later edits to a transaction can never change an existing invoice.
"""

import json
from decimal import Decimal

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import read_session, write_transaction
from app.errors import DuplicateTransactionId, MixedCurrency, NotFound, TransactionAlreadyInvoiced
from app.money import format_cents, to_cents
from app.repositories import invoices as repo
from app.repositories import transactions as transactions_repo
from app.services.pagination import build_page
from app.services.transactions import build_transaction_dict
from app.timestamps import utc_now_iso


def create_invoice(engine: Engine, transaction_ids: list[int]) -> dict:
    """Snapshot the given transactions into a new invoice.

    Runs in one BEGIN IMMEDIATE write transaction, so no transaction can be
    edited between reading it for the snapshot and saving the invoice lines.
    Any error (duplicate id, missing id, already invoiced, mixed currency)
    writes nothing.
    """
    try:
        with write_transaction(engine) as session:
            content = _build_invoice_content(session, transaction_ids)
            invoice_id = repo.insert_invoice(
                session, currency=content["currency"], total_cents=content["total_cents"], now=utc_now_iso()
            )
            repo.insert_lines(session, invoice_id, content["lines"])
            repo.insert_tax_rollup(session, invoice_id, content["tax_rollup"])
            repo.insert_fee_rollup(session, invoice_id, content["fee_rollup"])
            return build_invoice_dict(session, invoice_id)
    except IntegrityError as exc:
        _raise_if_double_billing(engine, exc, transaction_ids)
        raise


def get_invoice(engine: Engine, invoice_id: int) -> dict:
    """Stored invoice, read from invoice tables only, or NotFound."""
    with read_session(engine) as session:
        if repo.get_invoice(session, invoice_id) is None:
            raise NotFound(f"Invoice {invoice_id} not found")
        return build_invoice_dict(session, invoice_id)


def list_invoices(engine: Engine, limit: int, offset: int) -> dict:
    """One page of invoice summaries (header only; lines and rollups are on GET /invoices/{id})."""
    with read_session(engine) as session:
        rows = repo.list_invoices(session, limit + 1, offset)
        return build_page(rows, limit, offset, _invoice_summary)


def _invoice_summary(invoice) -> dict:
    return {
        "id": invoice.id,
        "currency": invoice.currency,
        "total": format_cents(invoice.total_cents),
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
    }


def patch_invoice(engine: Engine, invoice_id: int, transaction_ids: list[int]) -> dict:
    """Replace the invoice's transactions and rebuild the snapshot from their *current* data.

    One BEGIN IMMEDIATE write transaction: old lines and rollups are deleted and
    the new ones inserted together, so the invoice is never seen half-rebuilt,
    and any error (404, 409) leaves it exactly as it was. The result is again a
    frozen copy, not a live join.
    """
    try:
        with write_transaction(engine) as session:
            if repo.get_invoice(session, invoice_id) is None:
                raise NotFound(f"Invoice {invoice_id} not found")
            content = _build_invoice_content(session, transaction_ids, invoice_id=invoice_id)
            repo.delete_lines_and_rollups(session, invoice_id)
            repo.insert_lines(session, invoice_id, content["lines"])
            repo.insert_tax_rollup(session, invoice_id, content["tax_rollup"])
            repo.insert_fee_rollup(session, invoice_id, content["fee_rollup"])
            repo.update_invoice_header(
                session, invoice_id, currency=content["currency"], total_cents=content["total_cents"], now=utc_now_iso()
            )
            return build_invoice_dict(session, invoice_id)
    except IntegrityError as exc:
        _raise_if_double_billing(engine, exc, transaction_ids, invoice_id=invoice_id)
        raise


def delete_invoice(engine: Engine, invoice_id: int) -> dict:
    """Delete the invoice with its lines and rollups. The transactions stay, and
    are free to be invoiced again (their invoice_lines rows are gone).
    Returns a confirmation body (DELETE answers 200, not an empty 204)."""
    with write_transaction(engine) as session:
        if repo.get_invoice(session, invoice_id) is None:
            raise NotFound(f"Invoice {invoice_id} not found")
        repo.delete_invoice(session, invoice_id)
    return {"id": invoice_id, "deleted": True, "detail": f"Invoice {invoice_id} deleted"}


def _raise_if_double_billing(
    engine: Engine, exc: IntegrityError, transaction_ids: list[int], invoice_id: int | None = None
) -> None:
    """Backstop for double billing. The service check (_check_not_invoiced)
    normally catches this first, but the UNIQUE index on
    invoice_lines.transaction_id is the real guarantee. If it fired, the write
    transaction is already rolled back; report the same 409. Any other
    IntegrityError is left for the caller to re-raise.
    """
    if "UNIQUE constraint failed: invoice_lines.transaction_id" not in str(exc.orig):
        return
    with read_session(engine) as session:
        conflicts = _conflicts_on_other_invoices(session, transaction_ids, invoice_id)
    raise TransactionAlreadyInvoiced(conflicts)


# --- building the snapshot ----------------------------------------------------


def _build_invoice_content(session: Session, transaction_ids: list[int], invoice_id: int | None = None) -> dict:
    """Validate the ids and compute everything the invoice stores (amounts in cents).

    `invoice_id` is set when PATCHing: transactions already on *that* invoice are fine.
    Order of checks: duplicate ids (409), missing ids (404), already on an
    invoice (409), mixed currency (409). Rollups and the total are computed from
    the snapshots themselves, so the stored numbers always agree with the stored lines.
    """
    if len(set(transaction_ids)) != len(transaction_ids):
        raise DuplicateTransactionId("The same transaction id is listed more than once")

    missing_ids = [tid for tid in transaction_ids if transactions_repo.get_transaction(session, tid) is None]
    if missing_ids:
        raise NotFound(f"Transactions not found: {missing_ids}", missing_ids=missing_ids)

    _check_not_invoiced(session, transaction_ids, invoice_id)

    # The snapshot is exactly what GET /transactions/{id} returns right now.
    snapshots = [build_transaction_dict(session, tid) for tid in transaction_ids]

    currencies = {snapshot["currency"] for snapshot in snapshots}
    if len(currencies) > 1:
        raise MixedCurrency(f"Transactions have different currencies: {sorted(currencies)}")

    lines = [
        {
            "transaction_id": snapshot["id"],
            "type": snapshot["type"],
            "external_id": snapshot["external_id"],
            "total_cents": _cents(snapshot["total"]),
            "snapshot": json.dumps(snapshot),
        }
        for snapshot in snapshots
    ]

    return {
        "currency": currencies.pop(),
        "total_cents": sum(line["total_cents"] for line in lines),
        "lines": lines,
        "tax_rollup": _roll_up_taxes(snapshots),
        "fee_rollup": _roll_up_fees(snapshots),
    }


def _check_not_invoiced(session: Session, transaction_ids: list[int], invoice_id: int | None = None) -> None:
    """No double billing: a transaction can be on at most one invoice.

    This check gives the client a clear 409 naming the invoice each transaction
    is already on. It cannot race with another invoice write because we hold
    the BEGIN IMMEDIATE lock; the UNIQUE index is the backstop regardless.
    """
    conflicts = _conflicts_on_other_invoices(session, transaction_ids, invoice_id)
    if conflicts:
        raise TransactionAlreadyInvoiced(conflicts)


def _conflicts_on_other_invoices(session: Session, transaction_ids: list[int], invoice_id: int | None) -> list[dict]:
    """Transactions already on an invoice other than `invoice_id` (None = any invoice)."""
    conflicts = repo.find_invoices_for_transactions(session, transaction_ids)
    return [conflict for conflict in conflicts if conflict["invoice_id"] != invoice_id]


def _roll_up_taxes(snapshots: list[dict]) -> list[dict]:
    """Sum tax lines across transactions, grouped by (name, rate).

    "VAT" at 0.07 and "VAT" at 0.19 stay separate rows; a tax with no rate is its
    own group. A dict keeps insertion order, so rows come out in order of first
    appearance.
    """
    totals: dict[tuple[str, str | None], int] = {}
    for snapshot in snapshots:
        for line in snapshot["tax_lines"]:
            key = (line["name"], line["rate"])
            totals[key] = totals.get(key, 0) + _cents(line["amount"])
    return [{"name": name, "rate": rate, "amount_cents": cents} for (name, rate), cents in totals.items()]


def _roll_up_fees(snapshots: list[dict]) -> list[dict]:
    """Sum fee lines across transactions, grouped by name, in order of first appearance."""
    totals: dict[str, int] = {}
    for snapshot in snapshots:
        for line in snapshot["fee_lines"]:
            totals[line["name"]] = totals.get(line["name"], 0) + _cents(line["amount"])
    return [{"name": name, "amount_cents": cents} for name, cents in totals.items()]


def _cents(amount: str) -> int:
    """Snapshot amounts are "0.00" strings; convert exactly via Decimal."""
    return to_cents(Decimal(amount))


# --- reading: rows -> API shape -----------------------------------------------


def build_invoice_dict(session: Session, invoice_id: int) -> dict:
    """The API representation of a stored invoice. Reads invoice tables only."""
    invoice = repo.get_invoice(session, invoice_id)
    return {
        "id": invoice.id,
        "currency": invoice.currency,
        "total": format_cents(invoice.total_cents),
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
        "lines": [
            {
                "transaction_id": line.transaction_id,
                "type": line.type,
                "external_id": line.external_id,
                "total": format_cents(line.total_cents),
                "snapshot": json.loads(line.snapshot),
            }
            for line in repo.get_lines(session, invoice_id)
        ],
        "tax_rollup": [
            {"name": row.name, "rate": row.rate, "amount": format_cents(row.amount_cents)}
            for row in repo.get_tax_rollup(session, invoice_id)
        ],
        "fee_rollup": [
            {"name": row.name, "amount": format_cents(row.amount_cents)}
            for row in repo.get_fee_rollup(session, invoice_id)
        ],
    }
