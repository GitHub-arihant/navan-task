"""GET /transactions and GET /invoices: bounded pages (brief test 10)."""

import pytest
from sqlalchemy import event


def create_fees(client, seeds, count) -> list[int]:
    """POST `count` navan fees with distinct external_ids; return their ids."""
    ids = []
    for i in range(count):
        body = seeds["trip_fee"] | {"external_id": f"fee-{i}"}
        response = client.post("/transactions", json=body)
        assert response.status_code == 201
        ids.append(response.json()["id"])
    return ids


# --- Test 10: GET /transactions -----------------------------------------------


def test_transactions_default_page_is_50(client, seeds):
    ids = create_fees(client, seeds, 55)

    first = client.get("/transactions")
    assert first.status_code == 200
    page = first.json()
    assert page["limit"] == 50
    assert page["offset"] == 0
    assert page["next_offset"] == 50
    assert [item["id"] for item in page["items"]] == ids[:50]

    second = client.get("/transactions", params={"offset": page["next_offset"]}).json()
    assert [item["id"] for item in second["items"]] == ids[50:]
    assert second["next_offset"] is None


def test_transactions_limit_above_100_is_400(client):
    response = client.get("/transactions", params={"limit": 1000})
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": -1}, {"offset": -1}, {"limit": "abc"}])
def test_transactions_bad_page_params_are_400(client, params):
    assert client.get("/transactions", params=params).status_code == 400


def test_transactions_limit_100_is_allowed(client, seeds):
    create_fees(client, seeds, 3)
    page = client.get("/transactions", params={"limit": 100}).json()
    assert page["limit"] == 100
    assert len(page["items"]) == 3
    assert page["next_offset"] is None


def test_transaction_list_items_are_summaries(client, seeds):
    created = client.post("/transactions", json=seeds["flight"]).json()
    item = client.get("/transactions").json()["items"][0]
    assert item == {
        "id": created["id"],
        "type": "flight",
        "external_id": created["external_id"],
        "currency": "EUR",
        "occurred_at": created["occurred_at"],
        "total": "220.00",
        "created_at": created["created_at"],
        "updated_at": created["updated_at"],
    }


def test_empty_list(client):
    assert client.get("/transactions").json() == {"items": [], "limit": 50, "offset": 0, "next_offset": None}


# --- GET /invoices ------------------------------------------------------------


def test_invoices_are_paginated(client, seeds):
    fee_ids = create_fees(client, seeds, 3)
    invoice_ids = [client.post("/invoices", json={"transaction_id": fid}).json()["id"] for fid in fee_ids]

    first = client.get("/invoices", params={"limit": 2}).json()
    assert [item["id"] for item in first["items"]] == invoice_ids[:2]
    assert first["next_offset"] == 2
    assert first["items"][0] == {
        "id": invoice_ids[0],
        "currency": "EUR",
        "total": "25.00",
        "created_at": first["items"][0]["created_at"],
        "updated_at": first["items"][0]["updated_at"],
    }

    second = client.get("/invoices", params={"limit": 2, "offset": 2}).json()
    assert [item["id"] for item in second["items"]] == invoice_ids[2:]
    assert second["next_offset"] is None

    assert client.get("/invoices").json()["limit"] == 50


def test_invoices_limit_above_100_is_400(client):
    assert client.get("/invoices", params={"limit": 1000}).status_code == 400


# --- The SQL itself is bounded ------------------------------------------------


@pytest.mark.parametrize("path, table", [("/transactions", "transactions"), ("/invoices", "invoices")])
def test_list_sql_uses_limit_and_offset(client, path, table):
    """Capture the SQL the list endpoint sends: one bounded query, no full-table read."""
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        client.get(path, params={"limit": 10, "offset": 20})
    finally:
        event.remove(engine, "before_cursor_execute", record)

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1
    assert f"FROM {table}" in selects[0]
    assert "LIMIT" in selects[0] and "OFFSET" in selects[0]
