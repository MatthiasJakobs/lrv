def format_receipt_line(name, quantity, price):
    line_total = quantity * price
    return f'{quantity}x {name}: ${line_total:.2f}'


def format_discount(label, amount):
    return f'{label}: -${amount:.2f}'


def format_payment(method, amount):
    return f'Paid by {method}: ${amount:.2f}'


def format_balance(balance):
    if balance <= 0:
        return 'Paid in full'
    return f'Balance due: ${balance:.2f}'
