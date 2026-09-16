"""Manual trades plan first and refuse what they cannot do; no network, no key."""
import types

import pytest

import trade
from gradgate import cli


def fake_info(phase=0, pair=trade.ZERO):
    return {"curve": "0x" + "c" * 40, "pair": pair, "tick_spacing": 200, "phase": phase, "token": "0x" + "a" * 40, "symbol": "T", "name": "t",
            "phase_name": ("curve", "swept", "pool", "rescued")[phase], "eth_pair": pair == trade.ZERO}


@pytest.mark.parametrize("phase,pair,needle", [(2, trade.ZERO, "curve is closed"), (1, trade.ZERO, "curve is closed"),
                                               (0, "0x5fc5360d0400a0fd4f2af552add042d716f1d168", "ETH-paired")])
def test_buy_refuses_what_it_cannot_do(monkeypatch, phase, pair, needle):
    monkeypatch.setattr(trade, "inspect", lambda t: fake_info(phase, pair))
    p = trade.plan_buy("0xt", 0.001)
    assert not p["ok"] and needle in p["error"]


def test_buy_waits_out_the_opening_tax(monkeypatch):
    monkeypatch.setattr(trade, "inspect", lambda t: fake_info())
    monkeypatch.setattr(trade, "opening_tax_bps", lambda c, a: 618)
    monkeypatch.setattr(trade.exec_pons, "quote_buy_onchain", lambda c, e: 1000.0)
    monkeypatch.setattr(trade.exec_pons.w3.eth, "call", lambda *a, **k: b"")
    monkeypatch.setattr(trade.exec_pons.w3.eth, "estimate_gas", lambda *a, **k: 100_000)
    p = trade.plan_buy("0xt", 0.001)
    assert not p["ok"] and "opening tax" in p["error"]


def test_sell_needs_a_balance(monkeypatch):
    monkeypatch.setattr(trade, "inspect", lambda t: fake_info())
    monkeypatch.setattr(trade, "balance", lambda t, o: 0.0)
    p = trade.plan_sell("0xt", owner="0x" + "1" * 40)
    assert not p["ok"] and "holds none" in p["error"]


def test_cli_buy_over_the_size_limit_stops_before_planning(monkeypatch, capsys):
    monkeypatch.setenv("LIVE_MAX_SIZE", "0.01")
    monkeypatch.setattr(cli, "_trade_module", lambda: pytest.fail("a buy over the limit must not reach planning"))
    with pytest.raises(SystemExit):
        cli.cmd_buy(types.SimpleNamespace(token="0xt", eth=0.5, slippage=3.0, send=False, yes=False))


def test_cli_send_without_a_key_stops(monkeypatch):
    monkeypatch.delenv("RH_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(cli, "load_env", lambda *a, **k: False)
    with pytest.raises(SystemExit):
        cli._need_key()


def test_a_whole_sell_leaves_no_dust(monkeypatch):
    """A float of the balance loses the last wei; a 100% sell must still ask the sender for the whole balance."""
    held_wei = 59801066325187741065214
    sent = {}
    monkeypatch.setattr(trade.exec_pons, "_account", lambda: type("A", (), {"address": "0x" + "1" * 40})())
    monkeypatch.setattr(trade, "plan_sell", lambda *a, **k: {"ok": True, "venue": "pool", "tokens": held_wei / 1e18, "curve": "0x" + "c" * 40,
                                                             "pair": trade.ZERO, "tick_spacing": 200, "phase": 2})
    monkeypatch.setattr(trade, "eth_balance", lambda a: 0.0)
    monkeypatch.setattr(trade.pons_pool, "send_sell", lambda token, tokens, **k: sent.setdefault("wei", min(int(tokens * 1e18), held_wei)) and "0x" + "ab" * 32)
    monkeypatch.setattr(trade.exec_pons.w3.eth, "wait_for_transaction_receipt", lambda h, timeout=0: {"status": 1})
    trade.sell("0x" + "a" * 40, 100)
    assert sent["wei"] == held_wei


def test_curve_sell_min_out_comes_from_the_curve_itself(monkeypatch):
    """The curve's fee plus the creator's tax come off a sell; the formula at the 1 % base overstated a real sell by 4 %
    and could make the minimum unreachable. The minimum is taken from the curve's own eth_call answer."""
    ex = trade.exec_pons
    me = "0x" + "1" * 40
    sent = {}
    monkeypatch.setattr(ex, "_account", lambda: type("A", (), {"address": me})())
    monkeypatch.setattr(ex, "token_balance", lambda t, o: 508684.3645144689)
    monkeypatch.setattr(ex.w3.eth, "call", lambda tx, *a, **k: (508684364514468851153989).to_bytes(32, "big"))
    monkeypatch.setattr(ex, "ensure_allowance", lambda *a: None)
    monkeypatch.setattr(ex, "quote_sell_onchain", lambda c, wei, o: 940900000000001000)
    monkeypatch.setattr(ex, "_send", lambda tx: sent.setdefault("data", tx["data"]) and "0x" + "ab" * 32)
    ex.send_sell("0x" + "c" * 40, 508684.3645144689, slippage=0.05, token="0x" + "a" * 40)
    min_out = int(sent["data"][2 + 8 + 64: 2 + 8 + 128], 16)
    assert min_out == int(940900000000001000 * 0.95)
