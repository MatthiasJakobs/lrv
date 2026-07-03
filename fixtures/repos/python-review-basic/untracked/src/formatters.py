from __future__ import annotations


def format_receipt_line(name: str, quantity: int, price: float) -> str:
    line_total = quantity * price
    return f"{quantity}x {name}: ${line_total:.2f}"
