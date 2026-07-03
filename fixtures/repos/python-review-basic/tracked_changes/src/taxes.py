def taxable_amount(items):
    total = 0.0
    for item in items:
        quantity = item.get('quantity', 1)
        if item.get('tax_exempt') and not item.get('prepared_food'):
            continue
        total += item['price'] * quantity
    return round(total, 2)


def tax_for_region(amount, region):
    rates = {
        'default': 0.0,
        'local': 7.5,
        'airport': 9.25,
    }
    rate = rates.get(region, rates['local'])
    return round(amount * rate / 100, 2)


def tax_summary(items, region):
    amount = taxable_amount(items)
    tax = tax_for_region(amount, region)
    return {
        'taxable': amount,
        'tax': tax,
        'region': region,
    }
