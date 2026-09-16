"""Pons charges buys an opening tax that steps down by whole block seconds; no strategy may fill above 3 %."""
import types

import curve
import strategies as S

LAUNCH = 1_000_000


def engine_with(strategy: dict) -> S.Engine:
    e = S.Engine.__new__(S.Engine)
    e.strats = [S.normalize(strategy)]
    e.books = {strategy["id"]: S.Book(2.0)}
    e.latency = 1.0; e.pending = {}; e.reserve_hook = None; e.farm_hook = None; e.dropped = []
    return e


def token(addr: str = "0xt") -> types.SimpleNamespace:
    return types.SimpleNamespace(token=addr, symbol="T", curve="0xc", ts=LAUNCH, last_px=1e-9, net_eq=0.0, eq=1.0,
                                 quote_sym="ETH", fee_rate=0.01, graduated_block=None, partial=False)


def test_schedule_by_block_second():
    t = token()
    got = [S.opening_tax_bps(t, LAUNCH + d) for d in (0, 0.9, 1, 1.5, 2, 2.9, 3, 60)]
    assert got == [9900, 9900, 618, 618, 19, 19, 0, 0]


def test_entry_waits_until_the_tax_is_under_the_ceiling():
    e = engine_with({"id": "x", "name": "x", "enabled": True, "size": 0.05, "cash": 2.0})
    t = token()
    e.pending[("x", t.token)] = {"ts": LAUNCH + 0.2, "why": "test"}
    assert e.fill_pending({t.token: t}, LAUNCH + 1.3) == []          # 6.18 %: still waiting
    assert e.pending
    opened = e.fill_pending({t.token: t}, LAUNCH + 2.1)               # 0.19 %: fills, and pays it
    assert len(opened) == 1 and not e.pending
    held = e.books["x"].positions[t.token]["tokens"]
    assert abs(held - curve.buy(0.0, 0.05, 0.0019)[0]) < 1e-6 * held
    assert held < curve.buy(0.0, 0.05, 0.0)[0]


def test_late_signal_fills_untaxed():
    e = engine_with({"id": "x", "name": "x", "enabled": True, "size": 0.05, "cash": 2.0})
    t = token("0xu")
    e.pending[("x", t.token)] = {"ts": LAUNCH + 10, "why": "test"}
    assert len(e.fill_pending({t.token: t}, LAUNCH + 11)) == 1
    held = e.books["x"].positions[t.token]["tokens"]
    assert abs(held - curve.buy(0.0, 0.05, 0.0)[0]) < 1e-6 * held
