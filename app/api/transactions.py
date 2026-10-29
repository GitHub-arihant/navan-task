"""HTTP handlers for /transactions. Parsing and status codes only; rules live in services."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlalchemy import Engine

from app.api.deps import PageParams, get_engine, get_page_params
from app.api.responses import BAD_ID, BAD_PAGE, error_responses
from app.schemas.common import DeleteConfirmation
from app.schemas.transactions import TransactionIn, TransactionPatch
from app.services import transactions as service

router = APIRouter(tags=["transactions"])

NOT_FOUND = "not_found: no transaction with this id"
INVOICED = "transaction_already_invoiced: the transaction is on an invoice and is frozen; `conflicts` names the invoice"


@router.post(
    "/transactions",
    status_code=201,
    responses=error_responses(
        {
            400: "validation_error: unknown type or fee_kind, missing/invalid fields, money with more than 2 decimals",
            409: "duplicate_external_id: nothing was stored; `existing_id` is the stored transaction",
        }
    ),
)
def create_transaction(
    # discriminator="type": pick the flight/hotel/rail/navan_fee model by the `type` field.
    body: Annotated[TransactionIn, Body(discriminator="type")],
    engine: Engine = Depends(get_engine),
) -> dict:
    return service.create_transaction(engine, body)


@router.get("/transactions", responses=error_responses({400: BAD_PAGE}))
def list_transactions(page: PageParams = Depends(get_page_params), engine: Engine = Depends(get_engine)) -> dict:
    return service.list_transactions(engine, page.limit, page.offset)


@router.get("/transactions/{transaction_id}", responses=error_responses({400: BAD_ID, 404: NOT_FOUND}))
def get_transaction(transaction_id: int, engine: Engine = Depends(get_engine)) -> dict:
    return service.get_transaction(engine, transaction_id)


@router.patch(
    "/transactions/{transaction_id}",
    responses=error_responses(
        {
            400: "validation_error: type/external_id/currency sent, null or empty body, or metadata invalid for the stored type",
            404: NOT_FOUND,
            409: INVOICED + "; nothing was changed",
        }
    ),
)
def patch_transaction(transaction_id: int, body: TransactionPatch, engine: Engine = Depends(get_engine)) -> dict:
    return service.patch_transaction(engine, transaction_id, body)


@router.delete(
    "/transactions/{transaction_id}",
    response_model=DeleteConfirmation,
    responses=error_responses({400: BAD_ID, 404: NOT_FOUND, 409: INVOICED + "; nothing was deleted"}),
)
def delete_transaction(transaction_id: int, engine: Engine = Depends(get_engine)) -> dict:
    return service.delete_transaction(engine, transaction_id)
