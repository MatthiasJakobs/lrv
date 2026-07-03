from __future__ import annotations


def subtotal(items: list[dict[str, float]]) -> float:
    total = 0.0
    for item in items:
        total += item["price"] * item.get("quantity", 1)
    return round(total, 2)


def apply_discount(total: float, percent: float) -> float:
    return total - (total * percent / 100)


def apply_tax(total: float, tax_rate: float) -> float:
    if tax_rate <= 0:
        return total
    return round(total + (total * tax_rate / 100), 2)


def format_total(total: float) -> str:
    return f"USD {total:.2f}"
