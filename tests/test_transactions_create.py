"""POST /transactions and GET /transactions/{id} (brief tests 1, 2, 6 + validation)."""

import threading

import httpx
import pytest

from tests.helpers import query_scalar

SEED_NAMES = ["flight", "hotel", "rail", "trip_fee", "agent_call_fee"]


# --- Test 1: create every type from the seeds, GET returns the structure ------


@pytest.mark.parametrize("seed_name", SEED_NAMES)
def test_create_then_get_round_trips(client, seeds, seed_name):
    created = client.post("/transactions", json=seeds[seed_name])
    assert created.status_code == 201
    body = created.json()
    assert isinstance(body["id"], int)

    fetched = client.get(f"/transactions/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_flight_has_coupons_od_and_flight_number(client, seeds):
    created = client.post("/transactions", json=seeds["flight"]).json()
    flight = client.get(f"/transactions/{created['id']}").json()

    assert flight["type"] == "flight"
    assert flight["total"] == "220.00"
    assert flight["tax_lines"] == [{"name": "Airport tax", "rate": None, "amount": "15.00"}]
    assert flight["fee_lines"] == [{"name": "YQ", "amount": "25.00"}]
    assert flight["metadata"] == {
        "origin": "TXL",
        "destination": "LHR",
        "fare": "180.00",
        "coupons": [
            {
                "flight_number": "BA982",
                "carrier": "BA",
                "from": "TXL",
                "to": "LHR",
                "departure": "2026-03-11T07:00:00Z",
                "arrival": "2026-03-11T08:20:00Z",
            }
        ],
    }


def test_hotel_has_name_and_address(client, seeds):
    created = client.post("/transactions", json=seeds["hotel"]).json()
    hotel = client.get(f"/transactions/{created['id']}").json()

    assert hotel["total"] == "331.70"
    assert hotel["tax_lines"] == [{"name": "VAT", "rate": "0.07", "amount": "21.70"}]
    assert hotel["fee_lines"] == []
    assert hotel["metadata"] == {
        "name": "Hotel Mitte London",
        "address": {"street": "12 Kingsway", "city": "London", "country": "GB"},
        "check_in": "2026-03-11",
        "check_out": "2026-03-13",
        "nights": 2,
        "room_rate_per_night": "140.00",
        "extras": [{"name": "Breakfast", "amount": "30.00"}],
    }


def test_rail_has_two_legs(client, seeds):
    created = client.post("/transactions", json=seeds["rail"]).json()
    rail = client.get(f"/transactions/{created['id']}").json()

    legs = rail["metadata"]["legs"]
    assert [(leg["origin"], leg["destination"]) for leg in legs] == [
        ("London St Pancras", "Lille Europe"),
        ("Lille Europe", "Paris Nord"),
    ]
    assert legs[0]["train_number"] == "EST9108"
    assert legs[0]["class"] == "standard"
    assert rail["fee_lines"] == [{"name": "Booking fee", "amount": "4.00"}]


@pytest.mark.parametrize(
    "seed_name, fee_kind, total",
    [("trip_fee", "trip_fee", "25.00"), ("agent_call_fee", "agent_call_fee", "15.00")],
)
def test_navan_fee_has_fee_kind(client, seeds, seed_name, fee_kind, total):
    created = client.post("/transactions", json=seeds[seed_name]).json()
    fee = client.get(f"/transactions/{created['id']}").json()

    assert fee["type"] == "navan_fee"
    assert fee["total"] == total
    assert fee["metadata"]["fee_kind"] == fee_kind
    assert fee["metadata"]["related_external_id"] == seeds[seed_name]["metadata"]["related_external_id"]


# --- Test 2: duplicate external_id --------------------------------------------


def test_duplicate_external_id_returns_409_with_existing_id(client, seeds, database_url):
    first = client.post("/transactions", json=seeds["flight"])
    assert first.status_code == 201
    existing_id = first.json()["id"]

    # Same body again, then the fixture's different body with the same external_id.
    for body in [seeds["flight"], seeds["duplicate_flight"]]:
        duplicate = client.post("/transactions", json=body)
        assert duplicate.status_code == 409
        assert duplicate.json()["error"] == "duplicate_external_id"
        assert duplicate.json()["existing_id"] == existing_id

    external_id = seeds["flight"]["external_id"]
    assert query_scalar(database_url, "SELECT count(*) FROM transactions WHERE external_id = ?", (external_id,)) == 1
    # The rejected attempts left no child rows behind either.
    assert query_scalar(database_url, "SELECT count(*) FROM transaction_tax_lines") == 1
    assert query_scalar(database_url, "SELECT count(*) FROM flight_coupons") == 1


# --- Test 6: concurrent duplicate create --------------------------------------


def test_concurrent_duplicate_posts_store_one_row(live_server, seeds, database_url):
    attempts = 10
    barrier = threading.Barrier(attempts)
    responses = [None] * attempts

    def post(index):
        with httpx.Client(base_url=live_server, timeout=30) as http:
            barrier.wait()  # release all threads at the same moment
            responses[index] = http.post("/transactions", json=seeds["flight"])

    threads = [threading.Thread(target=post, args=(i,)) for i in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201] + [409] * (attempts - 1)

    created_id = next(r.json()["id"] for r in responses if r.status_code == 201)
    assert all(r.json()["existing_id"] == created_id for r in responses if r.status_code == 409)
    assert query_scalar(database_url, "SELECT count(*) FROM transactions") == 1


# --- Validation and 404 -------------------------------------------------------


def test_unknown_type_is_400(client, seeds, database_url):
    body = seeds["flight"] | {"type": "cruise"}
    response = client.post("/transactions", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"
    assert query_scalar(database_url, "SELECT count(*) FROM transactions") == 0


def test_unknown_fee_kind_is_400(client, seeds, database_url):
    body = seeds["trip_fee"]
    body["metadata"]["fee_kind"] = "lounge_fee"
    response = client.post("/transactions", json=body)
    assert response.status_code == 400
    assert query_scalar(database_url, "SELECT count(*) FROM transactions") == 0


def test_empty_metadata_is_400(client, seeds):
    body = seeds["flight"] | {"metadata": {}}
    assert client.post("/transactions", json=body).status_code == 400


def test_hotel_metadata_on_a_flight_is_400(client, seeds):
    body = seeds["flight"] | {"metadata": seeds["hotel"]["metadata"]}
    assert client.post("/transactions", json=body).status_code == 400


def test_more_than_two_decimals_is_400(client, seeds):
    body = seeds["flight"] | {"total": 220.005}
    assert client.post("/transactions", json=body).status_code == 400


def test_money_is_exact_not_float(client, seeds):
    # 0.29 * 100 == 28.999999999999996 in float arithmetic.
    body = seeds["trip_fee"] | {"total": 0.29}
    created = client.post("/transactions", json=body).json()
    assert created["total"] == "0.29"


def test_get_missing_transaction_is_404(client):
    response = client.get("/transactions/999")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
