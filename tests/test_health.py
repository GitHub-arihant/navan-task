import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.errors import DuplicateExternalId, NotFound
from app.main import create_app


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_on_live_server(live_server):
    response = httpx.get(f"{live_server}/health")
    assert response.status_code == 200


def _app_with_test_routes(database_url) -> FastAPI:
    """The real app plus two throwaway routes, to exercise the error handlers."""
    app = create_app(database_url)

    class Body(BaseModel):
        name: str

    @app.post("/_test/validate")
    def validate(body: Body):
        return {"ok": True}

    @app.get("/_test/not-found")
    def not_found():
        raise NotFound("Transaction 7 not found")

    @app.get("/_test/duplicate")
    def duplicate():
        raise DuplicateExternalId("abc", existing_id=3)

    return app


def test_validation_error_is_400_not_422(database_url):
    with TestClient(_app_with_test_routes(database_url)) as client:
        response = client.post("/_test/validate", json={"wrong": 1})
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"


def test_domain_errors_map_to_http(database_url):
    with TestClient(_app_with_test_routes(database_url)) as client:
        not_found = client.get("/_test/not-found")
        duplicate = client.get("/_test/duplicate")

    assert not_found.status_code == 404
    assert not_found.json() == {"error": "not_found", "detail": "Transaction 7 not found"}

    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "duplicate_external_id"
    assert duplicate.json()["existing_id"] == 3
