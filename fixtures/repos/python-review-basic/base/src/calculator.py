def subtotal(items):
    total = 0.0
    for item in items:
        total += item['price'] * item.get('quantity', 1)
    return total


def apply_discount(total, percent):
    if percent <= 0:
        return total
    return total - (total * percent / 100)


def apply_coupon(total, coupon):
    if not coupon:
        return total
    return apply_discount(total, coupon['percent'])


def service_fee(total, rate):
    if rate <= 0:
        return 0.0
    return total * rate / 100


def grand_total(items, discount_percent, fee_rate):
    total = subtotal(items)
    total = apply_discount(total, discount_percent)
    total += service_fee(total, fee_rate)
    return total


def format_total(total):
    return f'${total:.2f}'
