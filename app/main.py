"""FastAPI app factory: wires routers, runs migrations on startup, maps errors to HTTP.

Run with: uvicorn app.main:create_app --factory
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api import health, invoices, transactions
from app.config import get_database_url
from app.db import make_engine, run_migrations
from app.errors import BadRequest, Conflict, DomainError, NotFound

# Checked in order; DuplicateExternalId is a Conflict, so it maps to 409.
STATUS_BY_ERROR = [
    (BadRequest, 400),
    (NotFound, 404),
    (Conflict, 409),
]


def create_app(database_url: str | None = None) -> FastAPI:
    """Build the app. Tests pass their own temp-file URL; otherwise DATABASE_URL is used."""
    database_url = database_url or get_database_url()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run_migrations(database_url)
        app.state.engine = make_engine(database_url)
        yield
        app.state.engine.dispose()

    app = FastAPI(title="Navan transactions and invoices", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(transactions.router)
    app.include_router(invoices.router)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        # FastAPI defaults to 422; the brief requires 400 (e.g. unknown type / fee_kind).
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content={"error": "validation_error", "detail": "Request validation failed", "errors": errors},
        )

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError):
        status = 500
        for error_class, error_status in STATUS_BY_ERROR:
            if isinstance(exc, error_class):
                status = error_status
                break
        return JSONResponse(
            status_code=status,
            content={"error": exc.code, "detail": exc.detail, **exc.extra},
        )

    def openapi_without_422() -> dict:
        """FastAPI documents a 422 on every route that takes input, but this API
        never sends 422 (handle_validation_error turns it into 400). Remove it so
        /docs matches real behavior; the 400s are documented on each route."""
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            for path_item in schema["paths"].values():
                for operation in path_item.values():
                    operation["responses"].pop("422", None)
            component_schemas = schema.get("components", {}).get("schemas", {})
            component_schemas.pop("HTTPValidationError", None)
            component_schemas.pop("ValidationError", None)
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi_without_422
    return app
