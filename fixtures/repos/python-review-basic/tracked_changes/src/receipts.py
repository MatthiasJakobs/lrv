def receipt_header(order_id, cashier, store_name):
    return [
        store_name,
        f'Order: {order_id}',
        f'Cashier: {cashier}',
    ]


def receipt_items(items):
    lines = []
    for item in items:
        quantity = item.get('quantity', 1)
        if quantity == 0:
            continue
        total = item['price'] * quantity
        lines.append(f'{quantity}x {item["name"]}: ${total:.2f}')
        if item.get('note'):
            lines.append(f'  note: {item["note"]}')
    return lines


def receipt_footer(total, balance_due):
    return [
        '-' * 24,
        f'Total: ${total:.2f}',
        f'Balance due: ${balance_due:.2f}',
    ]


def render_receipt(order):
    lines = receipt_header(order['id'], order['cashier'], order['store'])
    lines.extend(receipt_items(order['items']))
    lines.extend(receipt_footer(order['total'], order.get('balance_due', 0)))
    return '\n'.join(lines)
