"""SQLAlchemy table definitions. Must match migrations/versions/0001_initial_schema.py
(tests/test_schema.py fails if they drift).

Conventions:
- Money is integer cents (`*_cents`). Tax rates are exact decimal TEXT (e.g. "0.07").
- Timestamps/dates are ISO-8601 TEXT; UTC timestamps sort correctly as text.
- `position` keeps child lists (tax lines, coupons, legs, ...) in the order the client sent.
- Child rows use ON DELETE CASCADE so deleting a transaction/invoice removes its lines.
"""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint/index names, so the migration can name them explicitly.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------------------
# Transaction side: live, editable, typed
# ---------------------------------------------------------------------------


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("type IN ('flight', 'hotel', 'rail', 'navan_fee')", name="type"),
        CheckConstraint("length(currency) = 3", name="currency_length"),
        # The real duplicate guard: two concurrent POSTs with the same external_id
        # cannot both insert; the loser gets an IntegrityError.
        Index("uq_transactions_external_id", "external_id", unique=True),
        # AUTOINCREMENT: ids are never reused after a delete.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3))
    occurred_at: Mapped[str] = mapped_column(String(40))
    total_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class TransactionTaxLine(Base):
    """Government/VAT-style taxes. Kept apart from fees so rollups never mix them."""

    __tablename__ = "transaction_tax_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    rate: Mapped[str | None] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column(Integer)


class TransactionFeeLine(Base):
    """Carrier/merchant fees on this transaction (YQ, booking fee, ...)."""

    __tablename__ = "transaction_fee_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    amount_cents: Mapped[int] = mapped_column(Integer)


class FlightDetails(Base):
    """One row per flight transaction: origin/destination and fare."""

    __tablename__ = "flight_details"

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True
    )
    origin: Mapped[str] = mapped_column(String(64))
    destination: Mapped[str] = mapped_column(String(64))
    fare_cents: Mapped[int] = mapped_column(Integer)


class FlightCoupon(Base):
    """One row per flown segment; a round trip has several."""

    __tablename__ = "flight_coupons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    flight_number: Mapped[str] = mapped_column(String(16))
    carrier: Mapped[str | None] = mapped_column(String(16))
    from_airport: Mapped[str] = mapped_column(String(64))
    to_airport: Mapped[str] = mapped_column(String(64))
    departure: Mapped[str] = mapped_column(String(40))
    arrival: Mapped[str] = mapped_column(String(40))


class HotelDetails(Base):
    """One row per hotel transaction: property name, address and stay."""

    __tablename__ = "hotel_details"

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(255))
    street: Mapped[str] = mapped_column(String(255))
    city: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(64))
    check_in: Mapped[str] = mapped_column(String(10))
    check_out: Mapped[str] = mapped_column(String(10))
    nights: Mapped[int] = mapped_column(Integer)
    room_rate_per_night_cents: Mapped[int] = mapped_column(Integer)


class HotelExtra(Base):
    """Optional hotel extras such as breakfast."""

    __tablename__ = "hotel_extras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    amount_cents: Mapped[int] = mapped_column(Integer)


class RailLeg(Base):
    """One row per train; a journey with a change is two legs."""

    __tablename__ = "rail_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(128))
    destination: Mapped[str] = mapped_column(String(128))
    departure: Mapped[str] = mapped_column(String(40))
    arrival: Mapped[str] = mapped_column(String(40))
    train_number: Mapped[str | None] = mapped_column(String(32))
    travel_class: Mapped[str | None] = mapped_column(String(32))  # API field name: "class"


class NavanFeeDetails(Base):
    """One row per navan_fee transaction: which kind of Navan charge it is."""

    __tablename__ = "navan_fee_details"
    __table_args__ = (
        CheckConstraint("fee_kind IN ('trip_fee', 'agent_call_fee')", name="fee_kind"),
    )

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True
    )
    fee_kind: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(String(255))
    related_external_id: Mapped[str | None] = mapped_column(String(255))


# ---------------------------------------------------------------------------
# Invoice side: frozen snapshot, never joined to live transaction tables on read
# ---------------------------------------------------------------------------


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("length(currency) = 3", name="currency_length"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))
    total_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class InvoiceLine(Base):
    """One included transaction, copied at invoicing time. `snapshot` is the full
    transaction JSON, so later edits to the transaction cannot change the invoice."""

    __tablename__ = "invoice_lines"
    __table_args__ = (
        # A transaction appears at most once per invoice. This unique index starts
        # with invoice_id, so it also serves "load lines for invoice X".
        Index("uq_invoice_lines_invoice_id_transaction_id", "invoice_id", "transaction_id", unique=True),
        # No double billing: a transaction can be on at most one invoice, across
        # all invoices. Also makes "is this transaction on any invoice?" a cheap
        # lookup. Added in migration 0002.
        Index("uq_invoice_lines_transaction_id", "transaction_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"))
    # RESTRICT: the DB itself refuses to delete a transaction that is billed.
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(255))
    total_cents: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[str] = mapped_column(Text)


class InvoiceTaxRollup(Base):
    """Invoice-level tax totals, grouped by (name, rate)."""

    __tablename__ = "invoice_tax_rollup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    rate: Mapped[str | None] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column(Integer)


class InvoiceFeeRollup(Base):
    """Invoice-level fee totals, grouped by name."""

    __tablename__ = "invoice_fee_rollup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    amount_cents: Mapped[int] = mapped_column(Integer)
