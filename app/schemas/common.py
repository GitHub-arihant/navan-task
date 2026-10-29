"""Request models shared by every transaction type."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

# A money amount: non-negative, at most 2 decimal places, at most 12 digits.
# Pydantic converts JSON floats via str(), so 0.29 becomes Decimal("0.29") exactly;
# 1.005 is rejected (400) instead of being rounded.
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]

# A tax rate such as 0.07 (7%).
Rate = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=6)]


class TaxLineIn(BaseModel):
    """Government/VAT-style tax on a transaction."""

    name: str = Field(min_length=1, max_length=255)
    rate: Rate | None = None
    amount: Money


class FeeLineIn(BaseModel):
    """Carrier/merchant fee on a transaction (YQ, booking fee, ...). Never mixed into taxes."""

    name: str = Field(min_length=1, max_length=255)
    amount: Money


# Pagination for list endpoints. limit > MAX_LIMIT is a 400, never silently capped.
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


# --- Response models, used to document responses in /docs ---------------------


class DeleteConfirmation(BaseModel):
    """Body of a successful DELETE (200). A 204 could not carry a body."""

    id: int
    deleted: bool
    detail: str


class InvoiceConflict(BaseModel):
    transaction_id: int
    invoice_id: int


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorResponse(BaseModel):
    """Every 4xx body: {"error", "detail"} plus extra fields for some error codes."""

    error: str = Field(description="Machine-readable code, e.g. validation_error, not_found, duplicate_external_id")
    detail: str = Field(description="Human-readable message")
    errors: list[ValidationIssue] | None = Field(default=None, description="validation_error: what failed and where")
    existing_id: int | None = Field(default=None, description="duplicate_external_id: id of the stored transaction")
    missing_ids: list[int] | None = Field(default=None, description="not_found on invoice create/patch: unknown ids")
    conflicts: list[InvoiceConflict] | None = Field(
        default=None, description="transaction_already_invoiced: each transaction and the invoice it is on"
    )
