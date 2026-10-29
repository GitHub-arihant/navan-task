"""Datetime <-> ISO-8601 text, the format timestamps are stored in."""

from datetime import UTC, datetime


def to_iso(value: datetime) -> str:
    """Timezone-aware values are converted to UTC and written with a "Z" suffix,
    so they sort correctly as text. Naive values (e.g. local departure times) are kept as sent."""
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utc_now_iso() -> str:
    """Server timestamp for created_at / updated_at."""
    return to_iso(datetime.now(UTC).replace(microsecond=0))
