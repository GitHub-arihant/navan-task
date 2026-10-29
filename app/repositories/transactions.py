"""SQL for transactions and their child tables. No business rules here.

Every function takes the caller's Session, so a whole request's reads/writes
share one DB transaction (opened by the service via read_session/write_transaction).
Child-list functions take plain dicts with amounts already in cents.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    FlightCoupon,
    FlightDetails,
    HotelDetails,
    HotelExtra,
    NavanFeeDetails,
    RailLeg,
    Transaction,
    TransactionFeeLine,
    TransactionTaxLine,
)

# --- writes -------------------------------------------------------------------


def insert_transaction(
    session: Session,
    *,
    type: str,
    external_id: str,
    currency: str,
    occurred_at: str,
    total_cents: int,
    now: str,
) -> int:
    """Insert the header row and return its new id.

    The flush sends the INSERT immediately, so a duplicate external_id raises
    IntegrityError here (from the unique index), not later at commit.
    """
    transaction = Transaction(
        type=type,
        external_id=external_id,
        currency=currency,
        occurred_at=occurred_at,
        total_cents=total_cents,
        created_at=now,
        updated_at=now,
    )
    session.add(transaction)
    session.flush()
    return transaction.id


def insert_tax_lines(session: Session, transaction_id: int, lines: list[dict]) -> None:
    """Insert tax lines in the order given. Each dict: name, rate, amount_cents."""
    for position, line in enumerate(lines):
        session.add(TransactionTaxLine(transaction_id=transaction_id, position=position, **line))
    session.flush()


def insert_fee_lines(session: Session, transaction_id: int, lines: list[dict]) -> None:
    """Insert fee lines in the order given. Each dict: name, amount_cents."""
    for position, line in enumerate(lines):
        session.add(TransactionFeeLine(transaction_id=transaction_id, position=position, **line))
    session.flush()


def insert_flight(
    session: Session, transaction_id: int, *, origin: str, destination: str, fare_cents: int, coupons: list[dict]
) -> None:
    """Insert flight_details plus one flight_coupons row per coupon."""
    session.add(
        FlightDetails(transaction_id=transaction_id, origin=origin, destination=destination, fare_cents=fare_cents)
    )
    for position, coupon in enumerate(coupons):
        session.add(FlightCoupon(transaction_id=transaction_id, position=position, **coupon))
    session.flush()


def insert_hotel(session: Session, transaction_id: int, *, details: dict, extras: list[dict]) -> None:
    """Insert hotel_details plus one hotel_extras row per extra."""
    session.add(HotelDetails(transaction_id=transaction_id, **details))
    for position, extra in enumerate(extras):
        session.add(HotelExtra(transaction_id=transaction_id, position=position, **extra))
    session.flush()


def insert_rail_legs(session: Session, transaction_id: int, legs: list[dict]) -> None:
    """Insert one rail_legs row per leg (a change of train = two legs)."""
    for position, leg in enumerate(legs):
        session.add(RailLeg(transaction_id=transaction_id, position=position, **leg))
    session.flush()


def insert_navan_fee(
    session: Session, transaction_id: int, *, fee_kind: str, description: str | None, related_external_id: str | None
) -> None:
    """Insert the navan_fee_details row (fee_kind is also CHECKed by the DB)."""
    session.add(
        NavanFeeDetails(
            transaction_id=transaction_id,
            fee_kind=fee_kind,
            description=description,
            related_external_id=related_external_id,
        )
    )
    session.flush()


def update_transaction_header(
    session: Session, transaction_id: int, *, total_cents: int | None, occurred_at: str | None, now: str
) -> None:
    """Change header fields; None means "leave as is". Always bumps updated_at."""
    transaction = session.get(Transaction, transaction_id)
    if total_cents is not None:
        transaction.total_cents = total_cents
    if occurred_at is not None:
        transaction.occurred_at = occurred_at
    transaction.updated_at = now
    session.flush()


def delete_tax_lines(session: Session, transaction_id: int) -> None:
    """Remove all tax lines; PATCH then inserts the new list in the same DB transaction."""
    session.execute(delete(TransactionTaxLine).where(TransactionTaxLine.transaction_id == transaction_id))


def delete_fee_lines(session: Session, transaction_id: int) -> None:
    """Remove all fee lines; PATCH then inserts the new list in the same DB transaction."""
    session.execute(delete(TransactionFeeLine).where(TransactionFeeLine.transaction_id == transaction_id))


def delete_type_details(session: Session, transaction_id: int) -> None:
    """Remove the type-specific rows (whichever type it is) so PATCH can insert new metadata."""
    for model in (FlightDetails, FlightCoupon, HotelDetails, HotelExtra, RailLeg, NavanFeeDetails):
        session.execute(delete(model).where(model.transaction_id == transaction_id))


def delete_transaction(session: Session, transaction_id: int) -> None:
    """Delete the header row. ON DELETE CASCADE removes tax/fee lines and details;
    the RESTRICT foreign key from invoice_lines makes the DB refuse if it is invoiced."""
    session.execute(delete(Transaction).where(Transaction.id == transaction_id))


# --- reads --------------------------------------------------------------------


def get_transaction(session: Session, transaction_id: int) -> Transaction | None:
    """Header row by primary key, or None."""
    return session.get(Transaction, transaction_id)


def list_transactions(session: Session, limit: int, offset: int) -> list[Transaction]:
    """One page of header rows, oldest first. Always bounded by SQL LIMIT/OFFSET
    (ordered by the primary key, so no extra index is needed)."""
    return list(session.scalars(select(Transaction).order_by(Transaction.id).limit(limit).offset(offset)))


def find_id_by_external_id(session: Session, external_id: str) -> int | None:
    """Used after a duplicate insert fails, to report the existing row's id (uses the unique index)."""
    return session.scalar(select(Transaction.id).where(Transaction.external_id == external_id))


def get_tax_lines(session: Session, transaction_id: int) -> list[TransactionTaxLine]:
    return list(
        session.scalars(
            select(TransactionTaxLine)
            .where(TransactionTaxLine.transaction_id == transaction_id)
            .order_by(TransactionTaxLine.position)
        )
    )


def get_fee_lines(session: Session, transaction_id: int) -> list[TransactionFeeLine]:
    return list(
        session.scalars(
            select(TransactionFeeLine)
            .where(TransactionFeeLine.transaction_id == transaction_id)
            .order_by(TransactionFeeLine.position)
        )
    )


def get_flight_details(session: Session, transaction_id: int) -> FlightDetails | None:
    return session.get(FlightDetails, transaction_id)


def get_flight_coupons(session: Session, transaction_id: int) -> list[FlightCoupon]:
    return list(
        session.scalars(
            select(FlightCoupon).where(FlightCoupon.transaction_id == transaction_id).order_by(FlightCoupon.position)
        )
    )


def get_hotel_details(session: Session, transaction_id: int) -> HotelDetails | None:
    return session.get(HotelDetails, transaction_id)


def get_hotel_extras(session: Session, transaction_id: int) -> list[HotelExtra]:
    return list(
        session.scalars(
            select(HotelExtra).where(HotelExtra.transaction_id == transaction_id).order_by(HotelExtra.position)
        )
    )


def get_rail_legs(session: Session, transaction_id: int) -> list[RailLeg]:
    return list(
        session.scalars(select(RailLeg).where(RailLeg.transaction_id == transaction_id).order_by(RailLeg.position))
    )


def get_navan_fee_details(session: Session, transaction_id: int) -> NavanFeeDetails | None:
    return session.get(NavanFeeDetails, transaction_id)
