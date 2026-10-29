"""Domain errors raised by services. No HTTP here; app/main.py maps each class to a status code."""


class DomainError(Exception):
    """Base class. `code` is the machine-readable error, `extra` adds fields to the error body."""

    code = "error"

    def __init__(self, detail: str, **extra):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class BadRequest(DomainError):
    """Input passed schema validation but breaks a rule (e.g. money with 3 decimals)."""

    code = "bad_request"


class NotFound(DomainError):
    """A transaction or invoice id does not exist."""

    code = "not_found"


class Conflict(DomainError):
    """The request clashes with stored state (invoiced transaction, mixed currency, ...)."""

    code = "conflict"


class DuplicateExternalId(Conflict):
    """A transaction with this external_id already exists. Carries `existing_id`."""

    code = "duplicate_external_id"

    def __init__(self, external_id: str, existing_id: int):
        super().__init__(
            f"Transaction with external_id {external_id!r} already exists",
            existing_id=existing_id,
        )


class DuplicateTransactionId(Conflict):
    """The same transaction id was listed twice on one invoice. Rejected, never double-counted."""

    code = "duplicate_transaction_id"


class MixedCurrency(Conflict):
    """An invoice's transactions are not all in one currency."""

    code = "mixed_currency"


class TransactionAlreadyInvoiced(Conflict):
    """A transaction is already on an invoice. Raised when billing it again would
    double-bill, and when a PATCH/DELETE would change billed money.
    `conflicts` lists each {transaction_id, invoice_id} pair."""

    code = "transaction_already_invoiced"

    def __init__(self, conflicts: list[dict], detail: str = "Transactions are already on an invoice"):
        super().__init__(detail, conflicts=conflicts)


class InvalidInput(BadRequest):
    """Input that can only be validated in the service (e.g. PATCH metadata,
    whose model depends on the stored type). Same error code as request validation."""

    code = "validation_error"
