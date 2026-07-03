def receipt_header(order_id, cashier):
    return [
        f'Order: {order_id}',
        f'Cashier: {cashier}',
    ]


def receipt_items(items):
    lines = []
    for item in items:
        quantity = item.get('quantity', 1)
        total = item['price'] * quantity
        lines.append(f'{quantity}x {item["name"]}: ${total:.2f}')
    return lines


def receipt_footer(total):
    return [
        '-' * 20,
        f'Total: ${total:.2f}',
    ]


def render_receipt(order):
    lines = receipt_header(order['id'], order['cashier'])
    lines.extend(receipt_items(order['items']))
    lines.extend(receipt_footer(order['total']))
    return '\n'.join(lines)
