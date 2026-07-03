def daily_sales_report(orders):
    gross = 0.0
    discounts = 0.0
    refunds = 0.0
    for order in orders:
        gross += order.get('subtotal', 0)
        discounts += order.get('discount', 0)
        refunds += order.get('refund', 0)
    return {
        'gross': gross,
        'discounts': discounts,
        'refunds': refunds,
        'net': gross - discounts - refunds,
    }


def payment_breakdown(orders):
    totals = {}
    for order in orders:
        method = order.get('payment_method', 'unknown')
        totals[method] = totals.get(method, 0) + order.get('paid', 0)
    return totals
