"""Real money moves only when every gate is open: a key, a strategy switched to live by hand, the size limit, no KILL.
Each test arms a trap in place of the order sender, so a regression fails loudly instead of trading."""
import ast
import glob
import os
import types

import pytest

import exec_pons
import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCH = 1_000_000


@pytest.fixture(autouse=True)
def no_orders(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(exec_pons, "send_buy", lambda *a, **k: sent.append((a, k)) or "0xtx")
    monkeypatch.setattr(S, "KILL", str(tmp_path / "KILL"))
    monkeypatch.setattr(S, "RISK_PATH", str(tmp_path / "risk.json"))
    monkeypatch.setattr(S, "LIVE_POS_PATH", str(tmp_path / "live_positions.json"))
    monkeypatch.setitem(S.RISK, "max_live_size", 0.01)
    monkeypatch.setitem(S.RISK, "max_live_open", 3)
    monkeypatch.setitem(S.RISK, "daily_loss_stop", 0.05)
    monkeypatch.delenv("RH_PRIVATE_KEY", raising=False)
    return sent


def engine(mode: str, size: float) -> tuple[S.Engine, dict]:
    s = S.normalize({"id": "x", "name": "x", "enabled": True, "size": size, "cash": 2.0})
    s["mode"] = mode
    e = S.Engine.__new__(S.Engine)
    e.strats = [s]; e.books = {"x": S.Book(2.0)}
    e.latency = 1.0; e.pending = {}; e.reserve_hook = None; e.farm_hook = None; e.dropped = []
    return e, s


def token():
    return types.SimpleNamespace(token="0xt", symbol="T", curve="0xc", ts=LAUNCH, last_px=1e-9, net_eq=0.0, eq=1.0, quote_sym="ETH",
                                 fee_rate=0.01, graduated_block=None, partial=False, eth_quoted=True, quote="ETH")


def open_once(e, s):
    return e.open(s, e.books["x"], token(), LAUNCH + 60, "test", None)


def test_live_strategy_without_a_key_never_sends(no_orders):
    e, s = engine("live", 0.01)
    assert open_once(e, s) is None
    assert not no_orders and not e.books["x"].positions
    assert "RH_PRIVATE_KEY" in e.books["x"].decisions[0]["reason"]


def test_size_over_the_limit_never_sends(no_orders, monkeypatch):
    monkeypatch.setenv("RH_PRIVATE_KEY", "0x" + "11" * 32)
    e, s = engine("live", 0.5)
    assert open_once(e, s) is None
    assert not no_orders and "LIVE_MAX_SIZE" in e.books["x"].decisions[0]["reason"]


def test_kill_file_stops_new_live_entries(no_orders, monkeypatch):
    monkeypatch.setenv("RH_PRIVATE_KEY", "0x" + "11" * 32)
    open(S.KILL, "w").write("stop")
    e, s = engine("live", 0.01)
    assert open_once(e, s) is None
    assert not no_orders and "KILL" in e.books["x"].decisions[0]["reason"]


def test_paper_strategy_never_sends_even_with_a_key(no_orders, monkeypatch):
    monkeypatch.setenv("RH_PRIVATE_KEY", "0x" + "11" * 32)
    e, s = engine("paper", 0.05)
    assert open_once(e, s) is not None
    assert not no_orders and e.books["x"].positions["0xt"]["live"] is False


def test_armed_live_strategy_does_send(no_orders, monkeypatch):
    """The control: with every gate open the order does go out, so the traps above are not passing by accident."""
    monkeypatch.setenv("RH_PRIVATE_KEY", "0x" + "11" * 32)
    e, s = engine("live", 0.01)
    assert open_once(e, s) is not None
    assert len(no_orders) == 1 and e.books["x"].positions["0xt"]["live"] is True


def test_house_strategies_ship_as_paper():
    import json
    for s in json.load(open(os.path.join(ROOT, "engine", "strategies.default.json"))):
        assert s.get("mode", "paper") == "paper", s["id"]


def key_readers() -> set[str]:
    """Every file that reads RH_PRIVATE_KEY out of the environment."""
    found = set()
    for f in glob.glob(os.path.join(ROOT, "engine", "**", "*.py"), recursive=True) + glob.glob(os.path.join(ROOT, "gradgate", "*.py")):
        for node in ast.walk(ast.parse(open(f).read())):
            if isinstance(node, ast.Constant) and node.value == "RH_PRIVATE_KEY":
                found.add(os.path.relpath(f, ROOT))
    return found


def test_only_known_places_read_the_key():
    # exec_pons signs; strategies checks it is set; indexer derives its own address; trade.py plans manual trades from that
    # address; the cli refuses to send without it and its doctor shows the address.
    assert key_readers() <= {"engine/exec_pons.py", "engine/strategies.py", "engine/indexer.py", "engine/trade.py", "gradgate/cli.py"}
    assert "engine/server.py" not in key_readers() and "engine/store.py" not in key_readers()
