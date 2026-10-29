"""Transaction rules: create (with duplicate handling), read, patch and delete.

Converts validated request models into cent amounts / ISO text for the
repository, and turns stored rows back into the API shape. That API shape
(`build_transaction_dict`) is also what invoices will copy as their snapshot.
"""

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import read_session, write_transaction
from app.errors import Conflict, DuplicateExternalId, InvalidInput, NotFound, TransactionAlreadyInvoiced
from app.money import format_cents, normalize_rate, to_cents
from app.repositories import invoices as invoices_repo
from app.repositories import transactions as repo
from app.schemas.common import FeeLineIn, TaxLineIn
from app.schemas.transactions import (
    METADATA_MODELS,
    FlightMetadata,
    HotelMetadata,
    NavanFeeMetadata,
    RailMetadata,
    TransactionIn,
    TransactionPatch,
)
from app.services.pagination import build_page
from app.timestamps import to_iso, utc_now_iso


def create_transaction(engine: Engine, data: TransactionIn) -> dict:
    """Store header + tax lines + fee lines + type details in one write transaction.

    Duplicates are caught by the database: the unique index on external_id makes
    the INSERT fail with IntegrityError, even when two POSTs race (BEGIN IMMEDIATE
    makes the second wait, and it then hits the constraint). We never pre-check in
    Python. The failed transaction is rolled back in full, so nothing is written.
    """
    try:
        with write_transaction(engine) as session:
            transaction_id = _insert_all(session, data)
            result = build_transaction_dict(session, transaction_id)
    except IntegrityError as exc:
        if "transactions.external_id" not in str(exc.orig):
            raise
        # The write transaction is already rolled back; look up the winner's id.
        with read_session(engine) as session:
            existing_id = repo.find_id_by_external_id(session, data.external_id)
        if existing_id is None:
            # The existing row was deleted between our failed insert and this read.
            raise Conflict(f"Transaction with external_id {data.external_id!r} was modified concurrently; retry")
        raise DuplicateExternalId(data.external_id, existing_id)
    return result


def get_transaction(engine: Engine, transaction_id: int) -> dict:
    """Full transaction (header, tax lines, fee lines, typed metadata), or NotFound."""
    with read_session(engine) as session:
        if repo.get_transaction(session, transaction_id) is None:
            raise NotFound(f"Transaction {transaction_id} not found")
        return build_transaction_dict(session, transaction_id)


def list_transactions(engine: Engine, limit: int, offset: int) -> dict:
    """One page of transaction summaries (header fields only, no child lines),
    so a page is a single bounded query. Full detail is GET /transactions/{id}."""
    with read_session(engine) as session:
        rows = repo.list_transactions(session, limit + 1, offset)
        return build_page(rows, limit, offset, _transaction_summary)


def _transaction_summary(transaction) -> dict:
    return {
        "id": transaction.id,
        "type": transaction.type,
        "external_id": transaction.external_id,
        "currency": transaction.currency,
        "occurred_at": transaction.occurred_at,
        "total": format_cents(transaction.total_cents),
        "created_at": transaction.created_at,
        "updated_at": transaction.updated_at,
    }


def patch_transaction(engine: Engine, transaction_id: int, patch: TransactionPatch) -> dict:
    """Replace the fields that were sent, unless the transaction is already invoiced.

    BEGIN IMMEDIATE so the "is it invoiced?" check and the write cannot interleave
    with an invoice being created: either the invoice snapshot sees the old data
    and this PATCH gets 409, or this PATCH lands first and the invoice sees the new data.
    Lists and metadata are replaced by delete + insert inside the same DB
    transaction, so a concurrent reader or writer never sees a half-replaced list.
    """
    with write_transaction(engine) as session:
        transaction = repo.get_transaction(session, transaction_id)
        if transaction is None:
            raise NotFound(f"Transaction {transaction_id} not found")
        _check_not_invoiced(session, transaction_id)

        # Validate before writing anything (not strictly needed, as any error rolls back).
        metadata = None
        if patch.metadata is not None:
            metadata = _validate_metadata(transaction.type, patch.metadata)

        repo.update_transaction_header(
            session,
            transaction_id,
            total_cents=to_cents(patch.total) if patch.total is not None else None,
            occurred_at=to_iso(patch.occurred_at) if patch.occurred_at is not None else None,
            now=utc_now_iso(),
        )
        if patch.tax_lines is not None:
            repo.delete_tax_lines(session, transaction_id)
            _insert_tax_lines(session, transaction_id, patch.tax_lines)
        if patch.fee_lines is not None:
            repo.delete_fee_lines(session, transaction_id)
            _insert_fee_lines(session, transaction_id, patch.fee_lines)
        if metadata is not None:
            repo.delete_type_details(session, transaction_id)
            _insert_metadata(session, transaction_id, metadata)

        return build_transaction_dict(session, transaction_id)


def delete_transaction(engine: Engine, transaction_id: int) -> dict:
    """Delete a transaction and all its child rows, unless it is invoiced.
    Returns a confirmation body (DELETE answers 200, not an empty 204).

    Same BEGIN IMMEDIATE reasoning as patch_transaction. The RESTRICT foreign key
    from invoice_lines would also make the DB refuse, but the check gives a clear 409.
    """
    with write_transaction(engine) as session:
        if repo.get_transaction(session, transaction_id) is None:
            raise NotFound(f"Transaction {transaction_id} not found")
        _check_not_invoiced(session, transaction_id)
        repo.delete_transaction(session, transaction_id)
    return {"id": transaction_id, "deleted": True, "detail": f"Transaction {transaction_id} deleted"}


def _check_not_invoiced(session: Session, transaction_id: int) -> None:
    """An invoiced transaction is frozen: 409 naming the invoice it is on."""
    conflicts = invoices_repo.find_invoices_for_transactions(session, [transaction_id])
    if conflicts:
        raise TransactionAlreadyInvoiced(conflicts, detail="Transaction is on an invoice and cannot be changed")


def _validate_metadata(type: str, metadata: dict):
    """Validate PATCH metadata against the stored type's model (400 if it does not fit)."""
    try:
        return METADATA_MODELS[type].model_validate(metadata)
    except ValidationError as exc:
        errors = [{"loc": ["metadata", *error["loc"]], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        raise InvalidInput(f"Invalid metadata for a {type} transaction", errors=errors)


# --- writing: request model -> rows -------------------------------------------


def _insert_all(session: Session, data: TransactionIn) -> int:
    """Insert the header, the shared child lists, then the type-specific rows."""
    transaction_id = repo.insert_transaction(
        session,
        type=data.type,
        external_id=data.external_id,
        currency=data.currency,
        occurred_at=to_iso(data.occurred_at),
        total_cents=to_cents(data.total),
        now=utc_now_iso(),
    )
    _insert_tax_lines(session, transaction_id, data.tax_lines)
    _insert_fee_lines(session, transaction_id, data.fee_lines)
    _insert_metadata(session, transaction_id, data.metadata)
    return transaction_id


def _insert_tax_lines(session: Session, transaction_id: int, lines: list[TaxLineIn]) -> None:
    repo.insert_tax_lines(
        session,
        transaction_id,
        [{"name": line.name, "rate": normalize_rate(line.rate), "amount_cents": to_cents(line.amount)} for line in lines],
    )


def _insert_fee_lines(session: Session, transaction_id: int, lines: list[FeeLineIn]) -> None:
    repo.insert_fee_lines(
        session, transaction_id, [{"name": line.name, "amount_cents": to_cents(line.amount)} for line in lines]
    )


def _insert_metadata(session: Session, transaction_id: int, metadata) -> None:
    """Write the type-specific rows. The metadata model's class says which type it is."""
    if isinstance(metadata, FlightMetadata):
        _insert_flight(session, transaction_id, metadata)
    elif isinstance(metadata, HotelMetadata):
        _insert_hotel(session, transaction_id, metadata)
    elif isinstance(metadata, RailMetadata):
        _insert_rail(session, transaction_id, metadata)
    elif isinstance(metadata, NavanFeeMetadata):
        _insert_navan_fee(session, transaction_id, metadata)
    else:
        raise ValueError(f"Unknown metadata model: {type(metadata).__name__}")


def _insert_flight(session: Session, transaction_id: int, metadata: FlightMetadata) -> None:
    repo.insert_flight(
        session,
        transaction_id,
        origin=metadata.origin,
        destination=metadata.destination,
        fare_cents=to_cents(metadata.fare),
        coupons=[
            {
                "flight_number": coupon.flight_number,
                "carrier": coupon.carrier,
                "from_airport": coupon.from_,
                "to_airport": coupon.to,
                "departure": to_iso(coupon.departure),
                "arrival": to_iso(coupon.arrival),
            }
            for coupon in metadata.coupons
        ],
    )


def _insert_hotel(session: Session, transaction_id: int, metadata: HotelMetadata) -> None:
    repo.insert_hotel(
        session,
        transaction_id,
        details={
            "name": metadata.name,
            "street": metadata.address.street,
            "city": metadata.address.city,
            "country": metadata.address.country,
            "check_in": metadata.check_in.isoformat(),
            "check_out": metadata.check_out.isoformat(),
            "nights": metadata.nights,
            "room_rate_per_night_cents": to_cents(metadata.room_rate_per_night),
        },
        extras=[{"name": extra.name, "amount_cents": to_cents(extra.amount)} for extra in metadata.extras],
    )


def _insert_rail(session: Session, transaction_id: int, metadata: RailMetadata) -> None:
    repo.insert_rail_legs(
        session,
        transaction_id,
        [
            {
                "origin": leg.origin,
                "destination": leg.destination,
                "departure": to_iso(leg.departure),
                "arrival": to_iso(leg.arrival),
                "train_number": leg.train_number,
                "travel_class": leg.travel_class,
            }
            for leg in metadata.legs
        ],
    )


def _insert_navan_fee(session: Session, transaction_id: int, metadata: NavanFeeMetadata) -> None:
    repo.insert_navan_fee(
        session,
        transaction_id,
        fee_kind=metadata.fee_kind,
        description=metadata.description,
        related_external_id=metadata.related_external_id,
    )


# --- reading: rows -> API shape -----------------------------------------------


def build_transaction_dict(session: Session, transaction_id: int) -> dict:
    """The API representation of a stored transaction (money as "0.00" strings).

    Invoices store exactly this dict as their frozen snapshot.
    """
    transaction = repo.get_transaction(session, transaction_id)
    return {
        "id": transaction.id,
        "type": transaction.type,
        "external_id": transaction.external_id,
        "currency": transaction.currency,
        "occurred_at": transaction.occurred_at,
        "total": format_cents(transaction.total_cents),
        "tax_lines": [
            {"name": line.name, "rate": line.rate, "amount": format_cents(line.amount_cents)}
            for line in repo.get_tax_lines(session, transaction_id)
        ],
        "fee_lines": [
            {"name": line.name, "amount": format_cents(line.amount_cents)}
            for line in repo.get_fee_lines(session, transaction_id)
        ],
        "metadata": _build_metadata(session, transaction.type, transaction_id),
        "created_at": transaction.created_at,
        "updated_at": transaction.updated_at,
    }


def _build_metadata(session: Session, type: str, transaction_id: int) -> dict:
    """Read the type-specific tables back into the same `metadata` shape the client sent."""
    if type == "flight":
        details = repo.get_flight_details(session, transaction_id)
        return {
            "origin": details.origin,
            "destination": details.destination,
            "fare": format_cents(details.fare_cents),
            "coupons": [
                {
                    "flight_number": coupon.flight_number,
                    "carrier": coupon.carrier,
                    "from": coupon.from_airport,
                    "to": coupon.to_airport,
                    "departure": coupon.departure,
                    "arrival": coupon.arrival,
                }
                for coupon in repo.get_flight_coupons(session, transaction_id)
            ],
        }
    if type == "hotel":
        details = repo.get_hotel_details(session, transaction_id)
        return {
            "name": details.name,
            "address": {"street": details.street, "city": details.city, "country": details.country},
            "check_in": details.check_in,
            "check_out": details.check_out,
            "nights": details.nights,
            "room_rate_per_night": format_cents(details.room_rate_per_night_cents),
            "extras": [
                {"name": extra.name, "amount": format_cents(extra.amount_cents)}
                for extra in repo.get_hotel_extras(session, transaction_id)
            ],
        }
    if type == "rail":
        return {
            "legs": [
                {
                    "origin": leg.origin,
                    "destination": leg.destination,
                    "departure": leg.departure,
                    "arrival": leg.arrival,
                    "train_number": leg.train_number,
                    "class": leg.travel_class,
                }
                for leg in repo.get_rail_legs(session, transaction_id)
            ]
        }
    if type == "navan_fee":
        details = repo.get_navan_fee_details(session, transaction_id)
        return {
            "fee_kind": details.fee_kind,
            "description": details.description,
            "related_external_id": details.related_external_id,
        }
    raise ValueError(f"Unknown transaction type in database: {type!r}")
