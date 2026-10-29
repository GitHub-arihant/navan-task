"""/docs (OpenAPI) matches real behavior: no 422 anywhere, real errors and DELETE bodies documented."""

import json

import pytest


@pytest.fixture
def openapi(client) -> dict:
    return client.get("/openapi.json").json()


def test_no_422_anywhere(openapi):
    for path, path_item in openapi["paths"].items():
        for method, operation in path_item.items():
            assert "422" not in operation["responses"], f"{method.upper()} {path}"
    assert "HTTPValidationError" not in json.dumps(openapi)


@pytest.mark.parametrize(
    "method, path, statuses",
    [
        ("post", "/transactions", {"201", "400", "409"}),
        ("get", "/transactions", {"200", "400"}),
        ("get", "/transactions/{transaction_id}", {"200", "400", "404"}),
        ("patch", "/transactions/{transaction_id}", {"200", "400", "404", "409"}),
        ("delete", "/transactions/{transaction_id}", {"200", "400", "404", "409"}),
        ("post", "/invoices", {"201", "400", "404", "409"}),
        ("get", "/invoices", {"200", "400"}),
        ("get", "/invoices/{invoice_id}", {"200", "400", "404"}),
        ("patch", "/invoices/{invoice_id}", {"200", "400", "404", "409"}),
        ("delete", "/invoices/{invoice_id}", {"200", "400", "404"}),
    ],
)
def test_documented_statuses(openapi, method, path, statuses):
    responses = openapi["paths"][path][method]["responses"]
    assert set(responses) == statuses
    for status in statuses - {"200", "201"}:
        schema = responses[status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorResponse"}


def test_delete_documents_confirmation_body(openapi):
    for path in ["/transactions/{transaction_id}", "/invoices/{invoice_id}"]:
        schema = openapi["paths"][path]["delete"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/DeleteConfirmation"}
    assert set(openapi["components"]["schemas"]["DeleteConfirmation"]["properties"]) == {"id", "deleted", "detail"}


def test_docs_page_is_served(client):
    assert client.get("/docs").status_code == 200
