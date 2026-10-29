"""PATCH and DELETE /invoices/{id} (brief test 9, invoice rebuild, Phase 2b leftovers)."""

from tests.helpers import create_trip, execute, query_scalar


def create(client, seeds, name, **overrides) -> int:
    response = client.post("/transactions", json=seeds[name] | overrides)
    assert response.status_code == 201
    return response.json()["id"]


# --- Test 9: DELETE invoice ---------------------------------------------------


def test_delete_invoice_keeps_transactions_and_unfreezes_them(client, seeds, database_url):
    ids = create_trip(client, seeds)
    invoice_id = client.post("/invoices", json={"transaction_ids": list(ids.values())}).json()["id"]

    response = client.delete(f"/invoices/{invoice_id}")
    assert response.status_code == 200
    assert response.json() == {"id": invoice_id, "deleted": True, "detail": f"Invoice {invoice_id} deleted"}
    assert client.get(f"/invoices/{invoice_id}").status_code == 404

    for transaction_id in ids.values():
        assert client.get(f"/transactions/{transaction_id}").status_code == 200
    # Lines and rollups went with the invoice.
    for table in ["invoice_lines", "invoice_tax_rollup", "invoice_fee_rollup"]:
        assert query_scalar(database_url, f"SELECT count(*) FROM {table}") == 0, table

    # The flight is no longer billed, so it can be edited again...
    patched = client.patch(f"/transactions/{ids['flight']}", json={"total": 230})
    assert patched.status_code == 200
    # ...and invoiced again (Phase 2b: deleting the invoice frees the transaction).
    again = client.post("/invoices", json={"transaction_id": ids["flight"]})
    assert again.status_code == 201
    assert again.json()["total"] == "230.00"


def test_delete_missing_invoice_is_404(client):
    assert client.delete("/invoices/999").status_code == 404


# --- PATCH invoice ------------------------------------------------------------


def test_patch_invoice_rebuilds_snapshot_from_current_data(client, seeds, database_url):
    hotel_id = create(client, seeds, "hotel")
    flight_id = create(client, seeds, "flight")
    invoice = client.post("/invoices", json={"transaction_id": hotel_id}).json()

    # The flight is not invoiced yet, so it can be edited through the API.
    client.patch(f"/transactions/{flight_id}", json={"total": 230})
    # The hotel is invoiced (API would 409); change it in the DB to prove the
    # rebuild reads current data, not the old snapshot.
    execute(database_url, "UPDATE transactions SET total_cents = 34000 WHERE id = ?", (hotel_id,))

    # Keep the hotel (already on *this* invoice: allowed) and add the flight.
    response = client.patch(f"/invoices/{invoice['id']}", json={"transaction_ids": [hotel_id, flight_id]})
    assert response.status_code == 200
    patched = response.json()

    assert patched["id"] == invoice["id"]
    assert [line["transaction_id"] for line in patched["lines"]] == [hotel_id, flight_id]
    assert [line["total"] for line in patched["lines"]] == ["340.00", "230.00"]
    assert patched["total"] == "570.00"
    assert patched["tax_rollup"] == [
        {"name": "VAT", "rate": "0.07", "amount": "21.70"},
        {"name": "Airport tax", "rate": None, "amount": "15.00"},
    ]
    assert patched["fee_rollup"] == [{"name": "YQ", "amount": "25.00"}]
    assert patched["created_at"] == invoice["created_at"]
    assert client.get(f"/invoices/{invoice['id']}").json() == patched

    # Still a frozen snapshot after the rebuild.
    execute(database_url, "UPDATE transactions SET total_cents = 1 WHERE id = ?", (flight_id,))
    assert client.get(f"/invoices/{invoice['id']}").json()["total"] == "570.00"


def test_patch_invoice_removing_a_transaction_frees_it(client, seeds):
    flight_id = create(client, seeds, "flight")
    hotel_id = create(client, seeds, "hotel")
    invoice_id = client.post("/invoices", json={"transaction_ids": [flight_id, hotel_id]}).json()["id"]

    patched = client.patch(f"/invoices/{invoice_id}", json={"transaction_id": hotel_id})
    assert patched.status_code == 200
    assert patched.json()["total"] == "331.70"

    assert client.patch(f"/transactions/{flight_id}", json={"total": 1}).status_code == 200
    assert client.post("/invoices", json={"transaction_id": flight_id}).status_code == 201


def test_patch_invoice_with_transaction_on_another_invoice_is_409(client, seeds):
    flight_id = create(client, seeds, "flight")
    hotel_id = create(client, seeds, "hotel")
    flight_invoice_id = client.post("/invoices", json={"transaction_id": flight_id}).json()["id"]
    hotel_invoice = client.post("/invoices", json={"transaction_id": hotel_id}).json()

    response = client.patch(f"/invoices/{hotel_invoice['id']}", json={"transaction_ids": [hotel_id, flight_id]})
    assert response.status_code == 409
    assert response.json()["conflicts"] == [{"transaction_id": flight_id, "invoice_id": flight_invoice_id}]
    assert client.get(f"/invoices/{hotel_invoice['id']}").json() == hotel_invoice


def test_patch_invoice_errors_leave_it_unchanged(client, seeds):
    flight_id = create(client, seeds, "flight")
    usd_id = create(client, seeds, "trip_fee", currency="USD")
    invoice = client.post("/invoices", json={"transaction_id": flight_id}).json()

    cases = [
        ({"transaction_ids": [flight_id, 999]}, 404),  # missing transaction
        ({"transaction_ids": [flight_id, usd_id]}, 409),  # mixed currency
        ({"transaction_ids": [flight_id, flight_id]}, 409),  # same id twice
        ({}, 400),  # bad body
    ]
    for body, status in cases:
        assert client.patch(f"/invoices/{invoice['id']}", json=body).status_code == status, body
        assert client.get(f"/invoices/{invoice['id']}").json() == invoice


def test_patch_missing_invoice_is_404(client, seeds):
    flight_id = create(client, seeds, "flight")
    assert client.patch("/invoices/999", json={"transaction_id": flight_id}).status_code == 404
