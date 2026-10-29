"""POST /invoices and GET /invoices/{id} (brief tests 3, 4, 5 + duplicate/missing ids)."""

import json
from decimal import Decimal
from pathlib import Path

from tests.helpers import create_trip, execute, query_scalar

EXPECTED_INVOICE = json.loads(
    (Path(__file__).resolve().parent.parent / "fixtures" / "task-b" / "expected-invoice.json").read_text()
)


def line_totals_by_seed(invoice: dict) -> dict:
    """Map each invoice line to the expected-invoice.json key (navan fees by fee_kind)."""
    totals = {}
    for line in invoice["lines"]:
        key = line["snapshot"]["metadata"]["fee_kind"] if line["type"] == "navan_fee" else line["type"]
        totals[key] = Decimal(line["total"])
    return totals


# --- Test 3: trip invoice matches expected-invoice.json -----------------------


def test_trip_invoice_matches_expected(client, seeds):
    ids = create_trip(client, seeds)
    response = client.post("/invoices", json={"transaction_ids": list(ids.values())})
    assert response.status_code == 201
    invoice = response.json()

    expected_totals = {key: Decimal(str(value)) for key, value in EXPECTED_INVOICE["totals"].items()}
    assert invoice["currency"] == EXPECTED_INVOICE["currency"]
    assert invoice["total"] == "685.70"
    assert Decimal(invoice["total"]) == expected_totals.pop("grand_total")
    assert line_totals_by_seed(invoice) == expected_totals
    assert len(invoice["lines"]) >= EXPECTED_INVOICE["line_count_minimum"]

    assert invoice["tax_rollup"] == [
        {"name": "Airport tax", "rate": None, "amount": "15.00"},
        {"name": "VAT", "rate": "0.07", "amount": "21.70"},
    ]
    assert invoice["fee_rollup"] == [
        {"name": "YQ", "amount": "25.00"},
        {"name": "Booking fee", "amount": "4.00"},
    ]

    # Each line carries the full typed transaction, not just a total.
    flight_line = invoice["lines"][0]
    assert flight_line["snapshot"]["metadata"]["coupons"][0]["flight_number"] == "BA982"

    # GET returns the same stored invoice.
    assert client.get(f"/invoices/{invoice['id']}").json() == invoice


def test_invoice_for_single_transaction_id(client, seeds):
    created = client.post("/transactions", json=seeds["hotel"]).json()
    response = client.post("/invoices", json={"transaction_id": created["id"]})
    assert response.status_code == 201
    assert response.json()["total"] == "331.70"
    assert len(response.json()["lines"]) == 1


# --- Test 4: the invoice is a frozen snapshot ---------------------------------


def test_invoice_does_not_change_when_flight_changes(client, seeds, database_url):
    ids = create_trip(client, seeds)
    invoice_id = client.post("/invoices", json={"transaction_ids": list(ids.values())}).json()["id"]

    # Change the stored flight directly in the DB (the API refuses edits to
    # invoiced transactions, so going around it is the only way to test this).
    execute(database_url, "UPDATE transactions SET total_cents = 99999 WHERE id = ?", (ids["flight"],))
    execute(
        database_url, "UPDATE transaction_tax_lines SET amount_cents = 5000 WHERE transaction_id = ?", (ids["flight"],)
    )
    assert client.get(f"/transactions/{ids['flight']}").json()["total"] == "999.99"

    invoice = client.get(f"/invoices/{invoice_id}").json()
    flight_line = next(line for line in invoice["lines"] if line["type"] == "flight")
    assert flight_line["total"] == "220.00"
    assert flight_line["snapshot"]["total"] == "220.00"
    assert invoice["total"] == "685.70"
    assert invoice["tax_rollup"][0] == {"name": "Airport tax", "rate": None, "amount": "15.00"}


# --- Test 5 and other 4xx: nothing is written ---------------------------------


def test_mixed_currency_is_409(client, seeds, database_url):
    eur = client.post("/transactions", json=seeds["flight"]).json()
    usd_body = seeds["trip_fee"] | {"currency": "USD", "external_id": "navan-trip-fee-usd"}
    usd = client.post("/transactions", json=usd_body).json()

    response = client.post("/invoices", json={"transaction_ids": [eur["id"], usd["id"]]})
    assert response.status_code == 409
    assert response.json()["error"] == "mixed_currency"
    assert query_scalar(database_url, "SELECT count(*) FROM invoices") == 0


def test_same_transaction_twice_is_409(client, seeds, database_url):
    created = client.post("/transactions", json=seeds["flight"]).json()
    response = client.post("/invoices", json={"transaction_ids": [created["id"], created["id"]]})
    assert response.status_code == 409
    assert response.json()["error"] == "duplicate_transaction_id"
    assert query_scalar(database_url, "SELECT count(*) FROM invoices") == 0


def test_missing_transaction_is_404(client, seeds, database_url):
    created = client.post("/transactions", json=seeds["flight"]).json()
    response = client.post("/invoices", json={"transaction_ids": [created["id"], 999]})
    assert response.status_code == 404
    assert response.json()["missing_ids"] == [999]
    assert query_scalar(database_url, "SELECT count(*) FROM invoices") == 0


def test_invalid_invoice_bodies_are_400(client):
    for body in [{}, {"transaction_id": 1, "transaction_ids": [1]}, {"transaction_ids": []}]:
        assert client.post("/invoices", json=body).status_code == 400


def test_get_missing_invoice_is_404(client):
    response = client.get("/invoices/999")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
