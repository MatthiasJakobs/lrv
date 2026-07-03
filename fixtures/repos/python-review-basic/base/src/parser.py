def parse_quantity(raw):
    value = int(raw)
    if value < 1:
        raise ValueError('quantity must be positive')
    return value


def parse_price(raw):
    value = float(raw)
    if value < 0:
        raise ValueError('price must be non-negative')
    return value


def parse_sku(raw):
    sku = raw.strip().upper()
    if not sku:
        raise ValueError('sku is required')
    return sku


def parse_line(raw):
    name, quantity, price = raw.split(',')
    return {
        'name': name.strip(),
        'quantity': parse_quantity(quantity),
        'price': parse_price(price),
    }
