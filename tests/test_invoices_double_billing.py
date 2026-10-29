"""A transaction can be on at most one invoice (PLAN.md Phase 2b)."""

import sqlite3
import threading

import httpx
import pytest

from app.services import invoices as invoices_service
from tests.helpers import query_scalar


def create(client, seeds, name) -> int:
    response = client.post("/transactions", json=seeds[name])
    assert response.status_code == 201
    return response.json()["id"]


def test_second_invoice_with_invoiced_transaction_is_409(client, seeds, database_url):
    flight_id = create(client, seeds, "flight")
    first = client.post("/invoices", json={"transaction_id": flight_id})
    assert first.status_code == 201

    second = client.post("/invoices", json={"transaction_id": flight_id})
    assert second.status_code == 409
    assert second.json()["error"] == "transaction_already_invoiced"
    assert second.json()["conflicts"] == [{"transaction_id": flight_id, "invoice_id": first.json()["id"]}]
    assert query_scalar(database_url, "SELECT count(*) FROM invoices") == 1


def test_conflicts_list_only_the_invoiced_transactions(client, seeds, database_url):
    flight_id = create(client, seeds, "flight")
    hotel_id = create(client, seeds, "hotel")
    rail_id = create(client, seeds, "rail")
    first_id = client.post("/invoices", json={"transaction_id": hotel_id}).json()["id"]

    response = client.post("/invoices", json={"transaction_ids": [flight_id, hotel_id, rail_id]})
    assert response.status_code == 409
    assert response.json()["conflicts"] == [{"transaction_id": hotel_id, "invoice_id": first_id}]
    assert query_scalar(database_url, "SELECT count(*) FROM invoices") == 1
    assert query_scalar(database_url, "SELECT count(*) FROM invoice_lines") == 1


def test_unique_index_is_the_backstop(client, seeds, database_url, monkeypatch):
    """Switch off the service check: the DB unique index must still refuse, mapped to the same 409."""
    flight_id = create(client, seeds, "flight")
    first_id = client.post("/invoices", json={"transaction_id": flight_id}).json()["id"]

    monkeypatch.setattr(invoices_service, "_check_not_invoiced", lambda session, ids, invoice_id=None: None)
    response = client.post("/invoices", json={"transaction_id": flight_id})

    assert response.status_code == 409
    assert response.json()["error"] == "transaction_already_invoiced"
    assert response.json()["conflicts"] == [{"transaction_id": flight_id, "invoice_id": first_id}]
    # The rolled-back attempt left no invoice header behind.
    assert query_scalar(database_url, "SELECT count(*) FROM invoices") == 1


def test_database_rejects_second_line_for_same_transaction(client, seeds, database_url):
    """Even raw SQL that skips the app cannot put one transaction on two invoices."""
    flight_id = create(client, seeds, "flight")
    client.post("/invoices", json={"transaction_id": flight_id})

    connection = sqlite3.connect(database_url.removeprefix("sqlite:///"))
    try:
        connection.execute(
            "INSERT INTO invoices (currency, total_cents, created_at, updated_at) VALUES ('EUR', 0, 'x', 'x')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="invoice_lines.transaction_id"):
            connection.execute(
                "INSERT INTO invoice_lines (invoice_id, transaction_id, position, type, external_id, total_cents, snapshot) "
                "VALUES (last_insert_rowid(), ?, 0, 'flight', 'x', 0, '{}')",
                (flight_id,),
            )
    finally:
        connection.close()


def test_concurrent_invoices_for_same_transaction(live_server, seeds, database_url):
    with httpx.Client(base_url=live_server) as http:
        flight_id = http.post("/transactions", json=seeds["flight"]).json()["id"]

    attempts = 5
    barrier = threading.Barrier(attempts)
    responses = [None] * attempts

    def post(index):
        with httpx.Client(base_url=live_server, timeout=30) as http:
            barrier.wait()  # release all threads at the same moment
            responses[index] = http.post("/invoices", json={"transaction_id": flight_id})

    threads = [threading.Thread(target=post, args=(i,)) for i in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201] + [409] * (attempts - 1)

    winner_id = next(r.json()["id"] for r in responses if r.status_code == 201)
    for response in responses:
        if response.status_code == 409:
            assert response.json()["conflicts"] == [{"transaction_id": flight_id, "invoice_id": winner_id}]
    assert query_scalar(database_url, "SELECT count(*) FROM invoices") == 1
    assert query_scalar(database_url, "SELECT count(*) FROM invoice_lines") == 1
