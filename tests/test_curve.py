import curve


def test_buy_then_sell_loses_only_fees():
    toks, r = curve.buy(1.0, 0.1)
    back, _ = curve.sell(r, toks, curve.FEE)
    assert 0.1 * (1 - 2 * curve.FEE) - 1e-9 <= back < 0.1


def test_price_rises_with_the_reserve():
    assert curve.price(0.0) < curve.price(1.0) < curve.price(4.2)
