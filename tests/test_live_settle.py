"""Live positions settle on receipts, never on a transaction hash (the first live run, 16.09.2026: a reverted sell was booked
as a close, a reverted buy stayed an open position, and two sends collided on one nonce)."""
import threading
import types
from collections import deque

import pytest

import exec_pons
import strategies as S

ME = "0x" + "1" * 40
TOK = "0x" + "a" * 40


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "LIVE_POS_PATH", str(tmp_path / "live.json"))
    monkeypatch.setattr(S.Engine, "_approve", lambda self, tok, p: None)
    monkeypatch.setattr(exec_pons, "_account", lambda: types.SimpleNamespace(address=ME))
    def offline(*a): raise ConnectionError("tests never reach the chain")
    monkeypatch.setattr(exec_pons, "quote_sell_onchain", offline)
    e = S.Engine.__new__(S.Engine)
    s = S.normalize({"id": "lt", "name": "lt", "mode": "live", "size": 0.001, "cash": 0.01, "exit": {"timeout_s": 60}})
    e.strats = [s]; e.books = {"lt": S.Book(0.01)}; e.latency = 0; e.pending = {}; e.dropped = []
    return e


def token(**kw):
    t = dict(token=TOK, symbol="TST", last_px=1e-8, net_eq=0.5, graduated_block=None, fee_rate=0.02, eq=1.0, progress=10,
             creator_sold_eth=0, pool=None, curve="0x" + "c" * 40, eth_quoted=True, quote=None)
    t.update(kw)
    return types.SimpleNamespace(**t)


def live_position(e, **kw):
    b = e.books["lt"]; b.cash -= 0.001
    p = {"token": TOK, "sym": "TST", "strategy": "lt", "open_ts": 1000, "entry_px": 1e-8, "size": 0.001, "tokens": 100000.0,
         "e_net": 0.001, "curve": True, "eq": 1.0, "peak_net": 0.5, "why": "", "live": True, "tx_in": "0x" + "b" * 64,
         "confirmed": False, "curve_addr": "0x" + "c" * 40}
    p.update(kw); b.positions[TOK] = p
    return p


def receipts(monkeypatch, table, tokens=0.0):
    monkeypatch.setattr(exec_pons, "receipt", lambda h: table.get(h))
    monkeypatch.setattr(exec_pons, "tokens_received", lambda r, tok, owner: tokens)


def test_reverted_buy_leaves_the_book_and_returns_cash(eng, monkeypatch):
    live_position(eng)
    receipts(monkeypatch, {"0x" + "b" * 64: {"status": 0, "logs": []}})
    eng.manage({TOK: token()}, 1002)
    b = eng.books["lt"]
    assert TOK not in b.positions and b.cash == pytest.approx(0.01)
    assert "reverted" in b.decisions[0]["reason"]


def test_pending_buy_waits_then_counts_as_dropped(eng, monkeypatch):
    live_position(eng)
    receipts(monkeypatch, {})
    eng.manage({TOK: token()}, 1000 + S.RECEIPT_WAIT_S)
    assert TOK in eng.books["lt"].positions                              # still in flight
    eng.manage({TOK: token()}, 1001 + S.RECEIPT_WAIT_S)
    assert TOK not in eng.books["lt"].positions and eng.books["lt"].cash == pytest.approx(0.01)


def test_rpc_outage_is_not_a_dropped_buy(eng, monkeypatch):
    live_position(eng)
    def down(h): raise ConnectionError("rpc down")
    monkeypatch.setattr(exec_pons, "receipt", down)
    eng.manage({TOK: token()}, 5000)
    assert TOK in eng.books["lt"].positions


def test_mined_buy_takes_the_tokens_from_the_logs(eng, monkeypatch):
    p = live_position(eng)
    receipts(monkeypatch, {p["tx_in"]: {"status": 1, "logs": []}}, tokens=87654.0)
    eng.manage({TOK: token()}, 1002)
    assert p["confirmed"] and p["tokens"] == 87654.0 and p["entry_px"] == pytest.approx(0.001 / 87654.0)


def test_reverted_sell_stays_open_and_retries_wider(eng, monkeypatch):
    p = live_position(eng, confirmed=True)
    sent = []
    monkeypatch.setattr(S.Engine, "_sell", lambda self, t, tokens, slip: sent.append(slip) or f"0x{len(sent):064x}")
    receipts(monkeypatch, {f"0x{1:064x}": {"status": 0, "logs": []}, f"0x{2:064x}": {"status": 1, "logs": []}})
    t = token()
    eng.manage({TOK: t}, 1100)                                             # timeout triggers the exit
    eng.manage({TOK: t}, 1100)                                             # sell 1 sent
    assert sent == [S.EXIT_SLIPPAGE[0]] and TOK in eng.books["lt"].positions
    eng.manage({TOK: t}, 1101)                                             # receipt: reverted -> back in the queue
    assert TOK in eng.books["lt"].positions and not eng.books["lt"].closed
    eng.manage({TOK: t}, 1102)                                             # sell 2 at the wider slippage
    assert sent == [S.EXIT_SLIPPAGE[0], S.EXIT_SLIPPAGE[1]]
    eng.manage({TOK: t}, 1103)                                             # mined -> closed
    b = eng.books["lt"]
    assert TOK not in b.positions and b.closed[0]["tx_out"] == f"0x{2:064x}"


def test_sell_closes_at_the_real_proceeds(eng, monkeypatch):
    p = live_position(eng, confirmed=True, tx_out="0x" + "d" * 64, tx_out_ts=1100, exit_value=0.0009, exit_reason="timeout 1 min")
    receipts(monkeypatch, {p["tx_out"]: {"status": 1, "logs": []}})
    eng.on_own_fill(TOK, {"kind": "sell", "tx": "d" * 64, "eth": 0.0012})   # the event may arrive before the receipt
    eng.manage({TOK: token()}, 1101)
    c = eng.books["lt"].closed[0]
    assert c["pnl"] == pytest.approx(0.0002) and eng.books["lt"].cash == pytest.approx(0.0102)


def test_late_sell_event_corrects_cash(eng, monkeypatch):
    p = live_position(eng, confirmed=True, tx_out="0x" + "d" * 64, tx_out_ts=1100, exit_value=0.0009, exit_reason="timeout 1 min")
    receipts(monkeypatch, {p["tx_out"]: {"status": 1, "logs": []}})
    eng.manage({TOK: token()}, 1101)
    assert eng.books["lt"].cash == pytest.approx(0.0099)
    eng.on_own_fill(TOK, {"kind": "sell", "tx": "0x" + "d" * 64, "eth": 0.0012})
    assert eng.books["lt"].cash == pytest.approx(0.0102) and eng.books["lt"].closed[0]["pnl"] == pytest.approx(0.0002)


def test_concurrent_sends_get_distinct_nonces(monkeypatch):
    nonces, lock = [], threading.Lock()
    acct = types.SimpleNamespace(address=ME, sign_transaction=lambda tx: types.SimpleNamespace(raw_transaction=tx["nonce"]))
    eth = types.SimpleNamespace(
        get_transaction_count=lambda a, block="latest": 90,               # the node lags: every reader sees the same count
        get_block=lambda b: {"baseFeePerGas": 1},
        estimate_gas=lambda tx: 21000,
        send_raw_transaction=lambda raw: (lock.acquire(), nonces.append(raw), lock.release()) and bytes([raw]) * 32,
    )
    monkeypatch.setattr(exec_pons, "_account", lambda: acct)
    monkeypatch.setattr(exec_pons, "w3", types.SimpleNamespace(eth=eth))
    monkeypatch.setattr(exec_pons, "_nonce", {"next": None, "at": 0.0})
    hashes = []
    ts = [threading.Thread(target=lambda: hashes.append(exec_pons._send({"to": ME, "value": 0}))) for _ in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(nonces) == [90, 91, 92, 93, 94]
    assert all(h.startswith("0x") for h in hashes)


def test_tokens_received_reads_transfer_logs():
    topic = lambda addr: bytes.fromhex(addr[2:].rjust(64, "0"))
    log = lambda token, to, amount: {"address": token, "topics": [bytes.fromhex(exec_pons.TRANSFER), topic("0x" + "c" * 40), topic(to)],
                                     "data": amount.to_bytes(32, "big")}
    r = {"logs": [log(TOK, ME, 5 * 10**18), log("0x" + "e" * 40, ME, 9 * 10**18), log(TOK, "0x" + "2" * 40, 7 * 10**18)]}
    assert exec_pons.tokens_received(r, TOK, ME) == 5.0


def test_live_exit_triggers_on_the_curves_own_quote(eng, monkeypatch):
    """The tape formula said +154% on a position that sold at -4% (16.09.2026): a live curve position is priced by eth_call."""
    p = live_position(eng, confirmed=True, open_ts=1090)
    eng.strats[0]["exit"]["tp_pct"] = 25
    monkeypatch.setattr(exec_pons, "quote_sell_onchain", lambda curve, wei, owner: int(0.00096 * 1e18))   # -4 %
    t = token(net_eq=5.0)                                                  # a stale tape reserve: the formula reads a big gain
    assert S.curve.sell(5.0 + p["e_net"], p["tokens"], 0.02)[0] > 0.00125
    eng.manage({TOK: t}, 1100)
    assert "exit_at" not in p and p["chain_value"] == pytest.approx(0.00096)


def test_failed_quote_falls_back_to_the_formula(eng, monkeypatch):
    p = live_position(eng, confirmed=True, open_ts=1090)
    def not_approved(*a): raise ValueError("execution reverted: allowance")
    monkeypatch.setattr(exec_pons, "quote_sell_onchain", not_approved)
    t = token()
    assert eng.value(p, t) == pytest.approx(S.curve.sell(t.net_eq + p["e_net"], p["tokens"], 0.02)[0])
    eng.manage({TOK: t}, 1100)
    assert "chain_value" not in p


def test_whole_exit_asks_a_hair_over_the_book(eng, monkeypatch):
    p = live_position(eng, confirmed=True)
    asked = []
    monkeypatch.setattr(S.Engine, "_sell", lambda self, t, tokens, slip: asked.append(tokens) or "0x" + "e" * 64)
    monkeypatch.setattr(exec_pons, "quote_sell_onchain", lambda *a: int(0.001 * 1e18))
    receipts(monkeypatch, {})
    eng.manage({TOK: token()}, 1100); eng.manage({TOK: token()}, 1100)
    assert asked and asked[0] > p["tokens"]
