"""Shared test fixtures. Every test gets its own temp SQLite *file* (never in-memory)."""

import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def database_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def client(database_url):
    """In-process HTTP client. Entering the context runs startup (migrations)."""
    with TestClient(create_app(database_url)) as test_client:
        yield test_client


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server(database_url):
    """A real Uvicorn server on a free port, for tests that need truly concurrent
    HTTP requests (TestClient would not overlap them). Yields the base URL."""
    port = _free_port()
    config = uvicorn.Config(create_app(database_url), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("live server did not start")
        time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


SEEDS_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "task-b" / "seed-payloads.json"


@pytest.fixture
def seeds() -> dict:
    """Fresh copy of fixtures/task-b/seed-payloads.json (tests may mutate it)."""
    return json.loads(SEEDS_PATH.read_text())
