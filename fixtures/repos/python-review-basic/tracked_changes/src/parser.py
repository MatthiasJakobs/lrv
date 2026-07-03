from __future__ import annotations


def parse_quantity(raw: str) -> int:
    value = int(raw)
    if value < 1:
        return 1
    return value


def parse_price(raw: str) -> float:
    value = float(raw)
    if value < 0:
        raise ValueError("price must be non-negative")
    return value
