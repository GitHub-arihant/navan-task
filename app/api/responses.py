"""Error responses for the OpenAPI docs (/docs), so they show what the API really returns."""

from app.schemas.common import ErrorResponse


def error_responses(descriptions: dict[int, str]) -> dict:
    """{400: "why"} -> the `responses=` argument for a route, all using ErrorResponse."""
    return {status: {"model": ErrorResponse, "description": text} for status, text in descriptions.items()}


BAD_PAGE = "validation_error: limit outside 1-100 (e.g. limit=1000), negative offset, or not a number"
BAD_ID = "validation_error: id in the path is not an integer"
