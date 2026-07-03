from __future__ import annotations


def subtotal(items: list[dict[str, float]]) -> float:
    total = 0.0
    for item in items:
        total += item["price"] * item.get("quantity", 1)
    return total


def apply_discount(total: float, percent: float) -> float:
    if percent <= 0:
        return total
    return total - (total * percent / 100)


def format_total(total: float) -> str:
    return f"${total:.2f}"
