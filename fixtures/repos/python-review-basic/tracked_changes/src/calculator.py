def subtotal(items):
    total = 0.0
    for item in items:
        if item.get('voided'):
            continue
        quantity = item.get('quantity', 1)
        total += item['price'] * quantity
    return round(total, 2)


def apply_discount(total, percent):
    return total - (total * percent / 100)


def apply_coupon(total, coupon):
    if not coupon:
        return total
    if coupon.get('type') == 'flat':
        return total - coupon['amount']
    return apply_discount(total, coupon['percent'])


def service_fee(total, rate):
    return round(total * rate / 100, 2)


def grand_total(items, discount_percent, fee_rate, tax_rate):
    total = subtotal(items)
    total = apply_discount(total, discount_percent)
    total += service_fee(total, fee_rate)
    return apply_tax(total, tax_rate)


def apply_tax(total, tax_rate):
    if tax_rate <= 0:
        return total
    return round(total + (total * tax_rate / 100), 2)


def format_total(total):
    return f'USD {total:.2f}'
