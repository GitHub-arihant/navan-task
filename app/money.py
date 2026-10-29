"""Money conversions. Money is integer cents in the DB and Decimal in Python; never float.

Request validation (app/schemas/common.py) already guarantees at most 2 decimal
places, so converting to cents is exact.
"""

from decimal import Decimal

CENT = Decimal("0.01")


def to_cents(amount: Decimal) -> int:
    """Decimal("331.70") -> 33170. Exact because amounts have at most 2 decimals."""
    return int(amount * 100)


def format_cents(cents: int) -> str:
    """33170 -> "331.70". Strings keep the value exact on the wire."""
    return str((Decimal(cents) / 100).quantize(CENT))


def normalize_rate(rate: Decimal | None) -> str | None:
    """Decimal("0.070") -> "0.07". One canonical text form, so rollups can group by rate."""
    if rate is None:
        return None
    return format(rate.normalize(), "f")
