"""Alembic environment. Uses the app's own engine so migrations get the same pragmas.

The URL comes from the Alembic config (set by app/db.py:run_migrations) or,
when run from the command line, from DATABASE_URL.
"""

from alembic import context

from app.config import get_database_url
from app.db import make_engine
from app.models import Base

database_url = context.config.get_main_option("sqlalchemy.url") or get_database_url()

engine = make_engine(database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
