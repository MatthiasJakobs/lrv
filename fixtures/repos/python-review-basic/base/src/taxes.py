def taxable_amount(items):
    total = 0.0
    for item in items:
        if item.get('tax_exempt'):
            continue
        total += item['price'] * item.get('quantity', 1)
    return total


def tax_for_region(amount, region):
    rates = {
        'default': 0.0,
        'local': 7.5,
    }
    rate = rates.get(region, rates['default'])
    return amount * rate / 100


def tax_summary(items, region):
    amount = taxable_amount(items)
    return {
        'taxable': amount,
        'tax': tax_for_region(amount, region),
    }
