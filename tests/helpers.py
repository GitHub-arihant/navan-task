"""Direct SQLite access for assertions that must not go through the API."""

import sqlite3


def query_scalar(database_url: str, sql: str, params: tuple = ()):
    """Run one SQL query against the test DB file and return the first column of the first row."""
    connection = sqlite3.connect(database_url.removeprefix("sqlite:///"))
    try:
        return connection.execute(sql, params).fetchone()[0]
    finally:
        connection.close()


def execute(database_url: str, sql: str, params: tuple = ()) -> None:
    """Run one write statement against the test DB file, bypassing the API, and commit."""
    connection = sqlite3.connect(database_url.removeprefix("sqlite:///"))
    try:
        connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


TRIP_SEEDS = ["flight", "hotel", "rail", "trip_fee", "agent_call_fee"]


def create_trip(client, seeds) -> dict:
    """POST the five trip seeds through the API; return {seed name: transaction id}."""
    ids = {}
    for name in TRIP_SEEDS:
        response = client.post("/transactions", json=seeds[name])
        assert response.status_code == 201
        ids[name] = response.json()["id"]
    return ids
