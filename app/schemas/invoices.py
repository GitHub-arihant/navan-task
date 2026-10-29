"""Request model for POST /invoices (and, in Phase 3, PATCH /invoices/{id})."""

from pydantic import BaseModel, Field, model_validator


class InvoiceIn(BaseModel):
    """Either {"transaction_id": 1} or {"transaction_ids": [1, 2, ...]}; both or neither is a 400."""

    transaction_id: int | None = None
    transaction_ids: list[int] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_shape(self):
        if (self.transaction_id is None) == (self.transaction_ids is None):
            raise ValueError("Send exactly one of transaction_id or transaction_ids")
        return self

    def ids(self) -> list[int]:
        """The requested ids as one list, whichever shape was sent."""
        if self.transaction_id is not None:
            return [self.transaction_id]
        return self.transaction_ids
