"""HTTP handlers for /invoices. Parsing and status codes only; rules live in services."""

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from app.api.deps import PageParams, get_engine, get_page_params
from app.api.responses import BAD_ID, BAD_PAGE, error_responses
from app.schemas.common import DeleteConfirmation
from app.schemas.invoices import InvoiceIn
from app.services import invoices as service

router = APIRouter(tags=["invoices"])

NOT_FOUND = "not_found: no invoice with this id"
BAD_BODY = "validation_error: send exactly one of transaction_id or a non-empty transaction_ids"
MISSING_TRANSACTIONS = "not_found: some transaction ids do not exist; `missing_ids` lists them"
CONFLICT = (
    "One of: duplicate_transaction_id (same id listed twice), "
    "transaction_already_invoiced (already on another invoice; `conflicts` names it), "
    "mixed_currency"
)


@router.post(
    "/invoices",
    status_code=201,
    responses=error_responses({400: BAD_BODY, 404: MISSING_TRANSACTIONS, 409: CONFLICT + ". Nothing was stored."}),
)
def create_invoice(body: InvoiceIn, engine: Engine = Depends(get_engine)) -> dict:
    return service.create_invoice(engine, body.ids())


@router.get("/invoices", responses=error_responses({400: BAD_PAGE}))
def list_invoices(page: PageParams = Depends(get_page_params), engine: Engine = Depends(get_engine)) -> dict:
    return service.list_invoices(engine, page.limit, page.offset)


@router.get("/invoices/{invoice_id}", responses=error_responses({400: BAD_ID, 404: NOT_FOUND}))
def get_invoice(invoice_id: int, engine: Engine = Depends(get_engine)) -> dict:
    return service.get_invoice(engine, invoice_id)


@router.patch(
    "/invoices/{invoice_id}",
    responses=error_responses(
        {
            400: BAD_BODY,
            404: f"{NOT_FOUND}, or {MISSING_TRANSACTIONS}",
            409: CONFLICT + ". The invoice is unchanged.",
        }
    ),
)
def patch_invoice(invoice_id: int, body: InvoiceIn, engine: Engine = Depends(get_engine)) -> dict:
    """Same body as create; replaces the invoice's transactions and rebuilds the snapshot."""
    return service.patch_invoice(engine, invoice_id, body.ids())


@router.delete(
    "/invoices/{invoice_id}",
    response_model=DeleteConfirmation,
    responses=error_responses({400: BAD_ID, 404: NOT_FOUND}),
)
def delete_invoice(invoice_id: int, engine: Engine = Depends(get_engine)) -> dict:
    """Deletes the invoice and its lines; the transactions stay and can be invoiced again."""
    return service.delete_invoice(engine, invoice_id)
