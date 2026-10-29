"""Shared page shape for list endpoints."""

from typing import Callable


def build_page(rows: list, limit: int, offset: int, to_dict: Callable) -> dict:
    """Turn rows fetched with LIMIT limit+1 into a page.

    The one extra row is never returned; it only tells us whether a next page
    exists, which saves a COUNT(*) over the whole table on every request.
    """
    has_more = len(rows) > limit
    return {
        "items": [to_dict(row) for row in rows[:limit]],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if has_more else None,
    }
