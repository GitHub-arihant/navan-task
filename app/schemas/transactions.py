"""Request models for POST /transactions.

The body is a union of four models, one per `type`. Pydantic picks the model by
looking at `type` (a "discriminated union"), so:
- an unknown `type` is a validation error (400),
- each type's `metadata` is validated against that type's own model, so a flight
  can never be stored with a hotel's metadata, and an empty `{}` metadata fails.
Unknown extra fields are accepted and ignored (the brief says extra fields are fine).
"""

from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.schemas.common import FeeLineIn, Money, TaxLineIn


class TransactionBase(BaseModel):
    """Fields every transaction type has."""

    external_id: str = Field(min_length=1, max_length=255)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    occurred_at: AwareDatetime
    total: Money
    tax_lines: list[TaxLineIn]
    fee_lines: list[FeeLineIn]


# --- flight -------------------------------------------------------------------


class FlightCoupon(BaseModel):
    flight_number: str = Field(min_length=1, max_length=16)
    carrier: str | None = Field(default=None, max_length=16)
    # "from" is a Python keyword, so the attribute is from_ and the JSON name is "from".
    from_: str = Field(alias="from", min_length=1, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    departure: datetime
    arrival: datetime


class FlightMetadata(BaseModel):
    origin: str = Field(min_length=1, max_length=64)
    destination: str = Field(min_length=1, max_length=64)
    fare: Money
    coupons: list[FlightCoupon]


class FlightTransactionIn(TransactionBase):
    type: Literal["flight"]
    metadata: FlightMetadata


# --- hotel --------------------------------------------------------------------


class HotelAddress(BaseModel):
    street: str = Field(min_length=1, max_length=255)
    city: str = Field(min_length=1, max_length=128)
    country: str = Field(min_length=1, max_length=64)


class HotelExtra(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    amount: Money


class HotelMetadata(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    address: HotelAddress
    check_in: date
    check_out: date
    nights: int = Field(ge=1)
    room_rate_per_night: Money
    extras: list[HotelExtra] = []


class HotelTransactionIn(TransactionBase):
    type: Literal["hotel"]
    metadata: HotelMetadata


# --- rail ---------------------------------------------------------------------


class RailLeg(BaseModel):
    origin: str = Field(min_length=1, max_length=128)
    destination: str = Field(min_length=1, max_length=128)
    departure: datetime
    arrival: datetime
    train_number: str | None = Field(default=None, max_length=32)
    # "class" is a Python keyword; stored in the travel_class column.
    travel_class: str | None = Field(default=None, alias="class", max_length=32)


class RailMetadata(BaseModel):
    legs: list[RailLeg]


class RailTransactionIn(TransactionBase):
    type: Literal["rail"]
    metadata: RailMetadata


# --- navan_fee ----------------------------------------------------------------


class NavanFeeMetadata(BaseModel):
    # Any other fee_kind is a validation error (400).
    fee_kind: Literal["trip_fee", "agent_call_fee"]
    description: str | None = Field(default=None, max_length=255)
    related_external_id: str | None = Field(default=None, max_length=255)


class NavanFeeTransactionIn(TransactionBase):
    type: Literal["navan_fee"]
    metadata: NavanFeeMetadata


TransactionIn = FlightTransactionIn | HotelTransactionIn | RailTransactionIn | NavanFeeTransactionIn


# Used by PATCH, where the body has no `type`: metadata is validated against
# the stored transaction's type.
METADATA_MODELS = {
    "flight": FlightMetadata,
    "hotel": HotelMetadata,
    "rail": RailMetadata,
    "navan_fee": NavanFeeMetadata,
}

PATCHABLE_FIELDS = {"total", "occurred_at", "tax_lines", "fee_lines", "metadata"}
IMMUTABLE_FIELDS = {"type", "external_id", "currency"}


class TransactionPatch(BaseModel):
    """PATCH /transactions/{id}. Send any of the patchable fields; each one sent
    replaces the stored value whole (lists and metadata are not merged).

    `metadata` is a plain dict here because its model depends on the stored
    type; the service validates it with METADATA_MODELS.
    """

    total: Money | None = None
    occurred_at: AwareDatetime | None = None
    tax_lines: list[TaxLineIn] | None = None
    fee_lines: list[FeeLineIn] | None = None
    metadata: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def check_fields(cls, data):
        """400 for immutable fields, explicit nulls, or nothing to change.
        (None in the parsed model therefore always means "not sent".)"""
        if not isinstance(data, dict):
            return data
        immutable = sorted(IMMUTABLE_FIELDS & data.keys())
        if immutable:
            raise ValueError(f"These fields cannot be changed: {', '.join(immutable)}")
        nulls = sorted(field for field in PATCHABLE_FIELDS if field in data and data[field] is None)
        if nulls:
            raise ValueError(f"These fields cannot be null: {', '.join(nulls)}")
        if not PATCHABLE_FIELDS & data.keys():
            raise ValueError(f"Send at least one of: {', '.join(sorted(PATCHABLE_FIELDS))}")
        return data
