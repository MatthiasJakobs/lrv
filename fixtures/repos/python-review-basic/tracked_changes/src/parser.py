def parse_quantity(raw):
    value = int(raw)
    if value < 1:
        return 1
    return value


def parse_price(raw):
    value = float(raw.replace('$', ''))
    if value < 0:
        return 0
    return round(value, 2)


def parse_sku(raw):
    sku = raw.strip().upper().replace(' ', '-')
    return sku or 'UNKNOWN'


def parse_line(raw):
    parts = raw.split(',')
    name = parts[0].strip()
    quantity = parse_quantity(parts[1])
    price = parse_price(parts[2])
    sku = parse_sku(parts[3]) if len(parts) > 3 else name
    return {
        'name': name,
        'quantity': quantity,
        'price': price,
        'sku': sku,
    }
