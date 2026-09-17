"""The gradgate command: start the engine, check the setup, and read the running engine from a second terminal.

    gradgate start | doctor | hunt | strategies | book <id> | on <id> | off <id> | token <address> | kill | unkill
    gradgate wallet | buy <token> <eth> [--send] | sell <token> [--pct N] [--send]

Everything talks to 127.0.0.1: `start` runs the engine in this process, the other commands ask the running engine over
its local API (the same one the terminal page uses). Nothing leaves the machine except the engine's own RPC calls.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(ROOT, "engine")
MINT, AMBER, RED, MUTE, HEAD, BRAND = "#2ee89a", "#ffb020", "#ff5d4a", "#8b9096", "#e4e8ea", "#c8cdd0"
console = Console(highlight=False)


# ---- setup ----------------------------------------------------------------------------------------------------------
def load_env(path: str = os.path.join(ROOT, ".env")) -> bool:
    """KEY=value lines into the environment; a variable already set in the shell wins."""
    if not os.path.exists(path): return False
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return True


def base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def api(port: int, path: str, method: str = "GET", body=None, timeout: float = 10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base_url(port) + path, data=data, method=method, headers={"content-type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def engine_or_exit(port: int):
    try:
        return api(port, "/api/config", timeout=3)
    except (urllib.error.URLError, OSError):
        console.print(f"[{RED}]the engine is not running on 127.0.0.1:{port}.[/] start it first: [bold]gradgate start[/]")
        sys.exit(1)


def mark() -> Text:
    return Text.assemble(("gradgate", f"bold {HEAD}"), ("_", BRAND))


# ---- formatting -----------------------------------------------------------------------------------------------------
def ago(s: float) -> str:
    s = max(0, int(s))
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s" if s < 3600 else f"{s // 3600}h {s % 3600 // 60:02d}m"


def signed(v: float, d: int = 1, suf: str = "%") -> Text:
    return Text(f"{v:+.{d}f}{suf}", style=MINT if v > 0 else RED if v < 0 else MUTE)


def sym(x: str | None, token: str = "") -> str:
    return "$" + x.lstrip("$") if x else token[:8]


def flags(r: dict) -> Text:
    """The red flags a row carries, in the terminal's own words."""
    out = []
    if (r.get("snipe_pct") or 0) > 5: out.append(f"snipers {r['snipe_pct']:.0f}%")
    if (r.get("exempt_n") or 0) > 0: out.append(f"bundle {r['exempt_n']}")
    if (r.get("farm_twins") or 0) >= 1: out.append(f"farm ×{r['farm_twins'] + 1}")
    if (r.get("creator_prior") or 0) >= 5 and not (r.get("creator_grads") or 0): out.append("serial dev")
    if (r.get("creator_tax_bps") or 0) > 500: out.append(f"tax {r['creator_tax_bps'] / 100:.0f}%")
    return Text(" · ".join(out), style=RED) if out else Text("")


# ---- start ----------------------------------------------------------------------------------------------------------
def cmd_start(a):
    load_env()
    sys.path.insert(0, ENGINE_DIR); os.chdir(ENGINE_DIR)
    import uvicorn
    url = base_url(a.port)
    console.print(Panel(Group(
        Text.assemble(mark(), ("  engine and terminal", MUTE)),
        Text.assemble(("open  ", MUTE), (url, f"bold {HEAD}")),
        Text.assemble(("mode  ", MUTE), (("live off · KILL", HEAD) if os.path.exists(os.path.join(ENGINE_DIR, "KILL")) else ("live armed", f"bold {RED}"))
                      if os.environ.get("RH_PRIVATE_KEY") else ("paper", HEAD),
                      ("  ·  the first start reads an hour of chain, 2–4 min", MUTE)),
        Text("ctrl+c stops it", style=MUTE)), box=box.ROUNDED, border_style="#35383d", padding=(1, 2)))
    if not a.no_open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("server:app", host="127.0.0.1", port=a.port, timeout_graceful_shutdown=2, log_level="warning")


# ---- doctor ---------------------------------------------------------------------------------------------------------
def rpc_call(url: str, method: str, params: list, timeout: float = 10):
    req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                 headers={"content-type": "application/json", "user-agent": "gradgate/0.1"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    if "error" in d: raise RuntimeError(str(d["error"])[:80])
    return d["result"], (time.time() - t) * 1000


def cmd_doctor(a):
    has_env = load_env()
    rows: list[tuple[str, bool | None, str]] = []
    add = lambda name, ok, note: rows.append((name, ok, note))
    add("python", sys.version_info >= (3, 11), sys.version.split()[0])
    add(".env", has_env or None, "found" if has_env else "missing — copy .env.example to .env (defaults are free public endpoints)")
    rpc = os.environ.get("RH_RPC", "https://robinhood-rpc.publicnode.com")
    head = None
    try:
        h, ms = rpc_call(rpc, "eth_blockNumber", []); head = int(h, 16)
        add("rpc", True, f"{rpc} · head {head:,} · {ms:.0f} ms")
    except Exception as e:
        add("rpc", False, f"{rpc} · {str(e)[:70]}")
    logs_url = os.environ.get("RH_RPC_LOGS", "https://rpc.mainnet.chain.robinhood.com")
    if head:
        try:
            got, ms = rpc_call(logs_url, "eth_getLogs", [{"fromBlock": hex(head - 50), "toBlock": hex(head),
                                                            "address": "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"}])
            add("logs rpc", True, f"{logs_url} · {len(got)} launches in the last 50 blocks · {ms:.0f} ms")
        except Exception as e:
            add("logs rpc", False, f"{logs_url} · {str(e)[:70]}")
    ws = os.environ.get("RH_WS", "")
    if ws:
        try:
            from websockets.sync.client import connect
            t = time.time()
            with connect(ws, open_timeout=8) as c:
                c.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]})); c.recv(timeout=8)
                c.recv(timeout=8)
            add("websocket", True, f"{ws} · a new block in {(time.time() - t) * 1000:.0f} ms")
        except Exception as e:
            add("websocket", False, f"{ws} · {str(e)[:70]}")
    else:
        add("websocket", None, "RH_WS not set — the feed falls back to polling every few seconds")
    db = os.environ.get("DATABASE_URL", "")
    if db:
        try:
            import psycopg
            with psycopg.connect(db, connect_timeout=6) as c: c.execute("select 1")
            add("database", True, "postgres answers · starts take seconds")
        except Exception as e:
            add("database", False, str(e)[:80])
    else:
        add("database", None, "not set — works without it; every start warms up for 2–4 min")
    key = os.environ.get("RH_PRIVATE_KEY", "")
    if key:
        try:
            from eth_account import Account
            addr = Account.from_key(key).address
            bal = int(rpc_call(rpc, "eth_getBalance", [addr, "latest"])[0], 16) / 1e18
            add("live key", True, f"{addr[:6]}…{addr[-4:]} · {bal:.4f} ETH · limits: size ≤{os.environ.get('LIVE_MAX_SIZE', '0.01')}, "
                                  f"{os.environ.get('LIVE_MAX_OPEN', '3')} open, daily stop {os.environ.get('LIVE_DAILY_STOP', '0.05')}")
        except Exception as e:
            add("live key", False, f"RH_PRIVATE_KEY does not load: {str(e)[:60]}")
    else:
        add("live key", None, "not set — paper only (the safe default)")
    if os.path.exists(os.path.join(ENGINE_DIR, "KILL")): add("kill switch", None, "engine/KILL present — no new live entries")
    ui = os.path.exists(os.path.join(ROOT, "ui", "dist", "index.html"))
    add("terminal ui", ui, "ui/dist built" if ui else "not built — npm --prefix ui install && npm --prefix ui run build")
    try:
        api(a.port, "/api/config", timeout=2); add("engine", True, f"running on 127.0.0.1:{a.port}")
    except Exception:
        add("engine", None, f"not running — gradgate start")

    t = Table(box=None, show_header=False, padding=(0, 2))
    for name, ok, note in rows:
        m = Text("ok", style=f"bold {MINT}") if ok is True else Text("fail", style=f"bold {RED}") if ok is False else Text("··", style=AMBER)
        t.add_row(m, Text(name, style=HEAD), Text(note, style=MUTE))
    console.print(Text.assemble(mark(), ("  doctor", MUTE)))
    console.print(t)
    if any(ok is False for _, ok, _ in rows): sys.exit(1)


# ---- hunt: the live feed in the terminal ----------------------------------------------------------------------------
def feed_table(rows: dict, pulse: dict | None, head: tuple, limit: int) -> Group:
    now = time.time()
    t = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MUTE}", border_style="#2a2d31", pad_edge=False, expand=True)
    for c, j in (("age", "right"), ("launch", "left"), ("pair", "left"), ("trades", "right"), ("buyers", "right"),
                 ("smart", "right"), ("curve", "right"), ("dev", "right"), ("flags", "left")):
        t.add_column(c, justify=j, no_wrap=True, overflow="ellipsis", ratio=3 if c in ("launch", "flags") else None)
    for r in sorted(rows.values(), key=lambda r: -r.get("block", 0))[:limit]:
        grad = r.get("graduated")
        name = Text.assemble((r.get("name") or "(unnamed)", HEAD), ("  " + sym(r.get("symbol"), r.get("token", "")), MUTE))
        t.add_row(Text(ago(now - r.get("ts", now)), style=MUTE), name, Text(r.get("quote") or "", style=MUTE),
                  str(r.get("buys", 0) + r.get("sells", 0)), str(r.get("buyers", 0)),
                  Text(str(r.get("smart", 0)), style=MINT if r.get("smart") else MUTE),
                  Text("grad" if grad else f"{r.get('progress', 0):.0f}%", style=BRAND if grad else HEAD),
                  Text(f"{r.get('creator_pct', 0):.1f}%", style=HEAD if r.get("creator_pct") else MUTE), flags(r))
    p = pulse or {}
    top = Text.assemble(mark(), ("  hunt", MUTE), ("   ●", MINT if head[1] is not None and head[1] < 20 else AMBER),
                        (f" head {head[0]:,} · {head[1]}s" if head[0] else " connecting…", MUTE),
                        (f"   launches/h {p.get('launches_1h', '—')}  ·  graduations/h {p.get('grads_1h', '—')}  ·  buys/h {p.get('buys_1h', '—')}", MUTE))
    return Group(top, t, Text("ctrl+c to leave · the engine keeps running", style=MUTE))


def cmd_hunt(a):
    engine_or_exit(a.port)
    rows: dict[str, dict] = {}; state = {"pulse": None, "head": (0, None), "warming": None}

    def reader():
        while True:
            try:
                with urllib.request.urlopen(base_url(a.port) + "/api/feed/stream", timeout=60) as r:
                    kind = None
                    for raw in r:
                        line = raw.decode().rstrip("\n")
                        if line.startswith("event: "): kind = line[7:]
                        elif line.startswith("data: "):
                            d = json.loads(line[6:])
                            if kind == "hello":
                                state["warming"] = d.get("phase") if d.get("warming") else None
                                for x in d.get("rows", []): rows[x["token"]] = x
                            elif kind == "launch": rows[d["token"]] = d
                            elif kind == "rows":
                                for x in d.get("rows", []): rows[x["token"]] = x
                            elif kind == "tick":
                                state["warming"] = d.get("phase") if d.get("warming") else None
                                if d.get("pulse"): state["pulse"] = d["pulse"]
                                if d.get("head"): state["head"] = (d["head"], d.get("head_age_s"))
                            if len(rows) > 400:
                                for k in sorted(rows, key=lambda k: rows[k].get("block", 0))[:200]: rows.pop(k, None)
            except Exception:
                time.sleep(2)

    threading.Thread(target=reader, daemon=True).start()
    with Live(console=console, refresh_per_second=2, screen=not a.no_screen) as live:
        try:
            while True:
                if state["warming"]:
                    live.update(Group(Text.assemble(mark(), ("  hunt", MUTE)), Text(f"the engine is warming up · {state['warming']}", style=AMBER)))
                else:
                    live.update(feed_table(rows, state["pulse"], state["head"], a.rows))
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass


# ---- strategies, books, coins, the kill switch ----------------------------------------------------------------------
def cmd_strategies(a):
    engine_or_exit(a.port)
    s = api(a.port, "/api/state")
    t = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MUTE}", border_style="#2a2d31")
    for c in ("", "id", "name", "mode", "p&l", "equity", "win", "trades", "open"):
        t.add_column(c, justify="right" if c in ("p&l", "equity", "win", "trades", "open") else "left", no_wrap=True)
    for v in s.get("strategies", []):
        b = v["book"]
        win = f"{round(100 * b['wins'] / b['closed'])}%" if b["closed"] else "—"
        t.add_row(Text("●", style=MINT if v["enabled"] else MUTE), Text(v["id"], style=BRAND), Text(v["name"], style=HEAD),
                  Text(v.get("mode") or "paper", style=RED if v.get("mode") == "live" else MUTE), signed(b["pnl_pct"], 2),
                  f"{b['equity']:.3f}", win, str(b["closed"]), str(b["open"]))
    console.print(Text.assemble(mark(), ("  strategies", MUTE)))
    console.print(t)


def cmd_book(a):
    engine_or_exit(a.port)
    v = next((x for x in api(a.port, "/api/state").get("strategies", []) if x["id"] == a.id), None)
    if not v: console.print(f"[{RED}]no strategy '{a.id}'[/] — gradgate strategies lists them"); sys.exit(1)
    b = v["book"]; closed = v.get("closed", [])
    win = f"{round(100 * b['wins'] / b['closed'])}%" if b["closed"] else "—"
    stats = (("p&l", signed(b["pnl_pct"], 2)), ("equity", Text(f"{b['equity']:.3f}", style=HEAD)), ("cash", Text(f"{b['cash']:.3f}", style=HEAD)),
             ("win", Text(win, style=HEAD)), ("trades", Text(str(b["closed"]), style=HEAD)), ("open", Text(str(b["open"]), style=HEAD)))
    head = Table.grid(padding=(0, 3))
    for _ in stats: head.add_column()
    head.add_row(*[Text.assemble((label + " ", MUTE), val) for label, val in stats])
    op = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MUTE}", border_style="#2a2d31", title="open", title_style=f"bold {MUTE}", title_justify="left")
    for c in ("coin", "held", "where", "p&l"): op.add_column(c, justify="right" if c == "p&l" else "left", no_wrap=True)
    for p in v.get("positions", []):
        pct = (p["pnl"] / p["size"] * 100) if p.get("pnl") is not None and p.get("size") else None
        op.add_row(Text(sym(p.get("sym"), p["token"]), style=HEAD), ago(p.get("age_s", 0)), Text("pool" if p.get("venue") == "pool" else "curve", style=MUTE),
                   signed(pct, 0) if pct is not None else Text("—", style=MUTE))
    cl = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MUTE}", border_style="#2a2d31", title="closed", title_style=f"bold {MUTE}", title_justify="left")
    for c in ("coin", "why", "p&l"): cl.add_column(c, justify="right" if c == "p&l" else "left", no_wrap=True, overflow="ellipsis")
    for c in closed[:a.rows]:
        cl.add_row(Text(sym(c.get("sym"), c["token"]), style=HEAD), Text(c.get("close_reason") or c.get("reason") or "", style=MUTE), signed(c["pnl_pct"], 0))
    console.print(Text.assemble(mark(), ("  book ", MUTE), (v["id"], BRAND), ("  " + v["name"], HEAD)))
    console.print(head); console.print(op); console.print(cl)


def cmd_toggle(a, on: bool):
    cfg = engine_or_exit(a.port)
    if not cfg.get("local_edit"): console.print(f"[{RED}]the engine refuses changes from here[/]"); sys.exit(1)
    s = next((x for x in api(a.port, "/api/strategies") if x["id"] == a.id), None)
    if not s: console.print(f"[{RED}]no strategy '{a.id}'[/]"); sys.exit(1)
    api(a.port, "/api/strategies", "POST", {**s, "enabled": on})
    console.print(Text.assemble(("● ", MINT if on else MUTE), (a.id, BRAND), (" is running on its book" if on else " is paused", MUTE)))


STAGE = {"ready": ("would buy", MINT), "waiting": ("waiting", AMBER), "no": ("not a fit", MUTE), "held": ("holding", HEAD),
         "traded": ("traded", HEAD), "graduated": ("graduated", BRAND), "no buys": ("no buys yet", MUTE)}


def cmd_token(a):
    engine_or_exit(a.port)
    try:
        d = api(a.port, f"/api/token/{a.address.lower()}")
    except urllib.error.HTTPError:
        console.print(f"[{RED}]the engine does not track {a.address}[/] (launched before its window)"); sys.exit(1)
    unit = "Ξ" if d.get("quote") == "ETH" else d.get("quote", "")
    facts = Table.grid(padding=(0, 3)); facts.add_column(style=MUTE); facts.add_column(style=HEAD)
    facts.add_row("graduation", "graduated · trading on uniswap" if d.get("graduated") else f"{d.get('progress', 0):.0f}% · {d.get('net', 0):.3f} of {d.get('threshold', 0):.2f} {unit}")
    facts.add_row("buyers", f"{d.get('buyers', 0)} · {d.get('smart', 0)} smart")
    facts.add_row("dev buy", f"{d.get('creator_pct', 0):.1f}% of supply")
    facts.add_row("snipers", f"{d.get('snipe_pct', 0):.1f}% of supply")
    facts.add_row("creator tax", "?" if d.get("creator_tax_bps") is None else f"{d['creator_tax_bps'] / 100:g}%")
    facts.add_row("bundle", "?" if d.get("exempt_n") is None else "none" if d["exempt_n"] == 0 else f"{d['exempt_n']} exempt wallets")
    facts.add_row("deployer", f"{d.get('creator', '')[:6]}…{d.get('creator', '')[-4:]} · {d.get('creator_prior', '?')} prior, {d.get('creator_grads', '?')} graduated")
    takes = Table(box=box.SIMPLE_HEAD, header_style=f"bold {MUTE}", border_style="#2a2d31")
    for c in ("id", "strategy", "verdict", "why"): takes.add_column(c, no_wrap=True, overflow="ellipsis")
    for s in d.get("strategies", []):
        label, color = STAGE.get(s.get("stage"), (s.get("stage") or "", MUTE))
        key = (s.get("blocked") or s.get("failed") or [None])[0]
        it = next((i for i in s.get("items", []) if i["k"] == key), None)
        why = f"{it['k']} {it['v']}, wants {it['want']}" if it else ""
        takes.add_row(Text(s["id"], style=BRAND), Text(s["name"], style=HEAD), Text(label, style=f"bold {color}"), Text(why, style=MUTE))
    console.print(Text.assemble(mark(), ("  ", ""), (d.get("name") or "(unnamed)", f"bold {HEAD}"), ("  " + sym(d.get("symbol"), d["token"]) + " / " + (d.get("quote") or ""), MUTE)))
    console.print(Text(d["token"], style=MUTE)); console.print(facts); console.print(takes)
    if flags(d).plain: console.print(Text.assemble(("flags  ", MUTE), flags(d)))


def cmd_kill(a, on: bool):
    engine_or_exit(a.port)
    api(a.port, "/api/kill", "POST" if on else "DELETE")
    console.print(Text("no new live entries until gradgate unkill" if on else "live entries allowed again", style=RED if on else MINT))


# ---- manual trades: plan by default, send only when asked -----------------------------------------------------------
def _trade_module():
    load_env()
    if ENGINE_DIR not in sys.path: sys.path.insert(0, ENGINE_DIR)
    import trade
    return trade


def _need_key():
    if not os.environ.get("RH_PRIVATE_KEY"):
        console.print(f"[{RED}]RH_PRIVATE_KEY is not set in .env[/] — without it gradgate only plans trades"); sys.exit(1)


def _confirm(a, what: str, entry: bool = True) -> bool:
    # KILL stops new entries; a sell only ever takes risk off, so it stays open under the kill switch (docs/LIVE.md)
    if entry and os.path.exists(os.path.join(ENGINE_DIR, "KILL")):
        console.print(f"[{RED}]engine/KILL is present — no new buys.[/] gradgate unkill (or remove the file) first"); return False
    if a.yes: return True
    console.print(Text.assemble(("real money. ", f"bold {RED}"), (what, HEAD), ("  type yes to send: ", MUTE)), end="")
    return input().strip().lower() == "yes"


def cmd_wallet(a):
    trade = _trade_module(); _need_key()
    me = trade.sender()
    t = Table.grid(padding=(0, 3)); t.add_column(style=MUTE); t.add_column(style=HEAD)
    t.add_row("address", me)
    t.add_row("ETH", f"{trade.eth_balance(me):.5f}")
    t.add_row("limits", f"entry ≤{os.environ.get('LIVE_MAX_SIZE', '0.01')} · {os.environ.get('LIVE_MAX_OPEN', '3')} open · daily stop {os.environ.get('LIVE_DAILY_STOP', '0.05')}")
    t.add_row("kill switch", "on — no new live entries" if os.path.exists(os.path.join(ENGINE_DIR, "KILL")) else "off")
    console.print(Text.assemble(mark(), ("  wallet", MUTE))); console.print(t)
    for tok in a.tokens:
        info = trade.inspect(tok)
        console.print(Text.assemble(("  " + sym(info["symbol"], tok), HEAD), (f"  {trade.balance(tok, me):,.2f}  ·  {info['phase_name']}", MUTE)))


def _plan_table(rows: list[tuple[str, str]]) -> Table:
    t = Table.grid(padding=(0, 3)); t.add_column(style=MUTE); t.add_column(style=HEAD)
    for k, v in rows: t.add_row(k, v)
    return t


def cmd_buy(a):
    load_env()
    limit = float(os.environ.get("LIVE_MAX_SIZE", "0.01"))
    if a.eth > limit:
        console.print(f"[{RED}]{a.eth} ETH is over LIVE_MAX_SIZE ({limit})[/] — raise the limit in .env if you mean it"); sys.exit(1)
    trade = _trade_module()
    p = trade.plan_buy(a.token, a.eth, a.slippage / 100)
    console.print(Text.assemble(mark(), ("  buy ", MUTE), (sym(p.get("symbol"), a.token), f"bold {HEAD}"), (f"  {p.get('name', '')}", MUTE)))
    rows = [("spend", f"{a.eth} ETH"), ("where", p.get("phase_name", "?"))]
    if "tokens_out" in p:
        rows += [("receive", f"≈ {p['tokens_out']:,.0f}  (at least {p['min_tokens_out']:,.0f} at {a.slippage:g}% slippage)"),
                 ("opening tax", f"{p['tax_bps'] / 100:g}%"), ("simulation", f"passes · gas {p['gas']:,}" if p.get("simulated") else "reverted")]
    console.print(_plan_table(rows))
    if not p["ok"]:
        console.print(f"[{RED}]{p.get('error', 'the plan did not pass')}[/]"); sys.exit(1)
    if not a.send:
        console.print(Text("plan only — nothing was sent. add --send to buy for real.", style=MUTE)); return
    _need_key()
    if not _confirm(a, f"buy {sym(p.get('symbol'), a.token)} for {a.eth} ETH."): console.print("cancelled"); return
    try:
        r = trade.buy(a.token, a.eth, a.slippage / 100)
    except Exception as e:
        console.print(f"[{RED}]not sent: {str(e)[:200]}[/]"); sys.exit(1)
    ok = r["status"] == 1
    console.print(Text.assemble(("sent  " if ok else "failed  ", f"bold {MINT if ok else RED}"), (r["tx"], HEAD), (f"  · received {r['tokens']:,.0f}" if ok else "", MUTE)))


def cmd_sell(a):
    trade = _trade_module()
    owner = trade.sender() if os.environ.get("RH_PRIVATE_KEY") else None
    if not owner:
        console.print(f"[{RED}]RH_PRIVATE_KEY is not set[/] — a sell is planned against your own wallet's balance"); sys.exit(1)
    p = trade.plan_sell(a.token, a.pct, a.slippage / 100, owner=owner)
    console.print(Text.assemble(mark(), ("  sell ", MUTE), (sym(p.get("symbol"), a.token), f"bold {HEAD}"), (f"  {p.get('name', '')}", MUTE)))
    rows = [("held", f"{p['held']:,.2f}"), ("sell", f"{p['tokens']:,.2f}  ({a.pct:g}%)")]
    if "out" in p:
        rows += [("where", p["venue"]), ("receive", f"≈ {p['out']:.6f} {p['out_unit']}  (at least {p['min_out']:.6f} at {a.slippage:g}% slippage)")]
    console.print(_plan_table(rows))
    if p.get("note"): console.print(Text(p["note"], style=AMBER))
    if not p["ok"]:
        console.print(f"[{RED}]{p.get('error', 'the plan did not pass')}[/]"); sys.exit(1)
    if not a.send:
        console.print(Text("plan only — nothing was sent. add --send to sell for real.", style=MUTE)); return
    if not _confirm(a, f"sell {a.pct:g}% of {sym(p.get('symbol'), a.token)}.", entry=False): console.print("cancelled"); return
    try:
        r = trade.sell(a.token, a.pct, a.slippage / 100)
    except Exception as e:
        console.print(f"[{RED}]not sent: {str(e)[:200]}[/]"); sys.exit(1)
    ok = r["status"] == 1
    console.print(Text.assemble(("sent  " if ok else "failed  ", f"bold {MINT if ok else RED}"), (r["tx"], HEAD), (f"  · ETH {r['eth_change']:+.6f}" if ok else "", MUTE)))


def main():
    ap = argparse.ArgumentParser(prog="gradgate", description="a terminal for pons launches on robinhood chain")
    ap.add_argument("--port", type=int, default=int(os.environ.get("GRADGATE_PORT", "8765")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start", help="run the engine and the terminal on 127.0.0.1"); p.add_argument("--no-open", action="store_true", help="do not open the browser")
    sub.add_parser("doctor", help="check rpc, websocket, database, key and limits")
    p = sub.add_parser("hunt", help="the live feed in this terminal"); p.add_argument("--rows", type=int, default=24); p.add_argument("--no-screen", action="store_true")
    sub.add_parser("strategies", help="every strategy with its book")
    p = sub.add_parser("book", help="one strategy's book"); p.add_argument("id"); p.add_argument("--rows", type=int, default=12)
    p = sub.add_parser("on", help="switch a strategy on"); p.add_argument("id")
    p = sub.add_parser("off", help="pause a strategy"); p.add_argument("id")
    p = sub.add_parser("token", help="a coin card with every strategy's verdict"); p.add_argument("address")
    sub.add_parser("kill", help="stop new live entries"); sub.add_parser("unkill", help="allow live entries again")
    p = sub.add_parser("wallet", help="your address, ETH, limits and the kill switch"); p.add_argument("tokens", nargs="*", help="token addresses to show balances for")
    p = sub.add_parser("buy", help="buy a launch on its curve — plans unless --send"); p.add_argument("token"); p.add_argument("eth", type=float)
    p.add_argument("--slippage", type=float, default=3.0, help="percent, default 3"); p.add_argument("--send", action="store_true"); p.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    p = sub.add_parser("sell", help="sell on the curve or in the pool — plans unless --send"); p.add_argument("token"); p.add_argument("--pct", type=float, default=100.0)
    p.add_argument("--slippage", type=float, default=5.0, help="percent, default 5"); p.add_argument("--send", action="store_true"); p.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    a = ap.parse_args()
    {"start": cmd_start, "doctor": cmd_doctor, "hunt": cmd_hunt, "strategies": cmd_strategies, "book": cmd_book,
     "on": lambda a: cmd_toggle(a, True), "off": lambda a: cmd_toggle(a, False), "token": cmd_token,
     "kill": lambda a: cmd_kill(a, True), "unkill": lambda a: cmd_kill(a, False),
     "wallet": cmd_wallet, "buy": cmd_buy, "sell": cmd_sell}[a.cmd](a)


if __name__ == "__main__":
    main()
