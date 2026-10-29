"""FastAPI dependencies shared by the routers."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Query, Request
from sqlalchemy import Engine

from app.schemas.common import DEFAULT_LIMIT, MAX_LIMIT


def get_engine(request: Request) -> Engine:
    """The engine created at startup (see app/main.py lifespan)."""
    return request.app.state.engine


@dataclass
class PageParams:
    limit: int
    offset: int


def get_page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    """?limit=&offset= for list endpoints. Out-of-range values (e.g. limit=1000) are a 400."""
    return PageParams(limit=limit, offset=offset)
