"""PATCH and DELETE /transactions/{id} (brief tests 7, 8 + atomic list replacement)."""

import threading

import httpx

from tests.helpers import create_trip, query_scalar

NEW_TAX_LINES = [
    {"name": "Airport tax", "amount": 12.5},
    {"name": "VAT", "rate": 0.19, "amount": 7.6},
]


# --- PATCH --------------------------------------------------------------------


def test_patch_replaces_sent_fields_and_keeps_the_rest(client, seeds):
    created = client.post("/transactions", json=seeds["flight"]).json()
    new_coupons = [
        {"flight_number": "BA983", "from": "LHR", "to": "TXL", "departure": "2026-03-15T09:00:00Z", "arrival": "2026-03-15T11:50:00Z"},
        {"flight_number": "BA984", "carrier": "BA", "from": "TXL", "to": "LHR", "departure": "2026-03-16T07:00:00Z", "arrival": "2026-03-16T08:20:00Z"},
    ]

    response = client.patch(
        f"/transactions/{created['id']}",
        json={
            "total": 230.10,
            "tax_lines": NEW_TAX_LINES,
            "metadata": {"origin": "TXL", "destination": "TXL", "fare": 190, "coupons": new_coupons},
        },
    )
    assert response.status_code == 200
    patched = response.json()

    assert patched["total"] == "230.10"
    assert patched["tax_lines"] == [
        {"name": "Airport tax", "rate": None, "amount": "12.50"},
        {"name": "VAT", "rate": "0.19", "amount": "7.60"},
    ]
    assert [c["flight_number"] for c in patched["metadata"]["coupons"]] == ["BA983", "BA984"]
    assert patched["metadata"]["fare"] == "190.00"
    # Not sent, so unchanged.
    assert patched["fee_lines"] == created["fee_lines"]
    assert patched["occurred_at"] == created["occurred_at"]
    assert patched["external_id"] == created["external_id"]
    assert client.get(f"/transactions/{created['id']}").json() == patched


def test_patch_can_clear_a_list(client, seeds, database_url):
    created = client.post("/transactions", json=seeds["flight"]).json()
    patched = client.patch(f"/transactions/{created['id']}", json={"fee_lines": []}).json()
    assert patched["fee_lines"] == []
    assert query_scalar(database_url, "SELECT count(*) FROM transaction_fee_lines") == 0


def test_patch_metadata_is_validated_against_stored_type(client, seeds):
    created = client.post("/transactions", json=seeds["flight"]).json()
    response = client.patch(
        f"/transactions/{created['id']}",
        json={"total": 1, "metadata": seeds["hotel"]["metadata"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"
    # Nothing was written, not even the valid `total`.
    assert client.get(f"/transactions/{created['id']}").json() == created


def test_patch_navan_fee_with_unknown_fee_kind_is_400(client, seeds):
    created = client.post("/transactions", json=seeds["trip_fee"]).json()
    response = client.patch(f"/transactions/{created['id']}", json={"metadata": {"fee_kind": "lounge_fee"}})
    assert response.status_code == 400


def test_patch_rejects_immutable_null_and_empty_bodies(client, seeds):
    created = client.post("/transactions", json=seeds["flight"]).json()
    for body in [{"currency": "USD"}, {"type": "hotel"}, {"external_id": "x"}, {"total": None}, {}]:
        response = client.patch(f"/transactions/{created['id']}", json=body)
        assert response.status_code == 400, body
    assert client.get(f"/transactions/{created['id']}").json() == created


def test_patch_missing_transaction_is_404(client):
    assert client.patch("/transactions/999", json={"total": 1}).status_code == 404


# --- Test 7: PATCH an invoiced transaction ------------------------------------


def test_patch_invoiced_transaction_is_409_and_nothing_changes(client, seeds):
    ids = create_trip(client, seeds)
    invoice = client.post("/invoices", json={"transaction_ids": list(ids.values())}).json()
    flight_before = client.get(f"/transactions/{ids['flight']}").json()

    response = client.patch(f"/transactions/{ids['flight']}", json={"total": 999.99, "tax_lines": []})
    assert response.status_code == 409
    assert response.json()["error"] == "transaction_already_invoiced"
    assert response.json()["conflicts"] == [{"transaction_id": ids["flight"], "invoice_id": invoice["id"]}]

    assert client.get(f"/transactions/{ids['flight']}").json() == flight_before
    invoice_after = client.get(f"/invoices/{invoice['id']}").json()
    assert invoice_after == invoice
    assert invoice_after["total"] == "685.70"


# --- DELETE -------------------------------------------------------------------


def test_delete_transaction_removes_it_and_its_child_rows(client, seeds, database_url):
    created = client.post("/transactions", json=seeds["hotel"]).json()

    response = client.delete(f"/transactions/{created['id']}")
    assert response.status_code == 200
    assert response.json() == {
        "id": created["id"],
        "deleted": True,
        "detail": f"Transaction {created['id']} deleted",
    }
    assert client.get(f"/transactions/{created['id']}").status_code == 404

    for table in ["transaction_tax_lines", "transaction_fee_lines", "hotel_details", "hotel_extras"]:
        assert query_scalar(database_url, f"SELECT count(*) FROM {table}") == 0, table


def test_delete_missing_transaction_is_404(client):
    assert client.delete("/transactions/999").status_code == 404


# --- Test 8: DELETE an invoiced transaction -----------------------------------


def test_delete_invoiced_transaction_is_409_and_row_stays(client, seeds):
    created = client.post("/transactions", json=seeds["flight"]).json()
    invoice = client.post("/invoices", json={"transaction_id": created["id"]}).json()

    response = client.delete(f"/transactions/{created['id']}")
    assert response.status_code == 409
    assert response.json()["conflicts"] == [{"transaction_id": created["id"], "invoice_id": invoice["id"]}]
    assert client.get(f"/transactions/{created['id']}").json() == created


# --- Concurrent PATCHes never tear a list -------------------------------------


def test_concurrent_patches_leave_one_complete_list(live_server, seeds, database_url):
    """Threads race to replace tax_lines with list A (2 lines) or list B (3 lines).
    Last write wins, but the stored list must be exactly A or exactly B, never a mix."""
    list_a = [{"name": "A1", "amount": 1}, {"name": "A2", "amount": 2}]
    list_b = [{"name": "B1", "amount": 1}, {"name": "B2", "amount": 2}, {"name": "B3", "amount": 3}]

    with httpx.Client(base_url=live_server) as http:
        transaction_id = http.post("/transactions", json=seeds["flight"]).json()["id"]

    attempts = 10
    barrier = threading.Barrier(attempts)
    statuses = [None] * attempts

    def patch(index):
        body = {"tax_lines": list_a if index % 2 == 0 else list_b}
        with httpx.Client(base_url=live_server, timeout=30) as http:
            barrier.wait()
            statuses[index] = http.patch(f"/transactions/{transaction_id}", json=body).status_code

    threads = [threading.Thread(target=patch, args=(i,)) for i in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses == [200] * attempts
    with httpx.Client(base_url=live_server) as http:
        names = [line["name"] for line in http.get(f"/transactions/{transaction_id}").json()["tax_lines"]]
    assert names in (["A1", "A2"], ["B1", "B2", "B3"])
    assert query_scalar(database_url, "SELECT count(*) FROM transaction_tax_lines") == len(names)
