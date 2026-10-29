"""App settings. The only setting is where the SQLite file lives."""

import os

DEFAULT_DATABASE_URL = "sqlite:///./data/navan.db"


def get_database_url() -> str:
    """Read DATABASE_URL from the environment so tests and deployments can point at their own file."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
