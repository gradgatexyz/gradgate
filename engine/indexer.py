"""Live state of Pons on Robinhood Chain, kept in memory, fed from the public RPC every few seconds.

Everything the terminal shows comes from here: launches, curve trades, graduations, creator records,
strategy decisions (paper), and a tape. Nothing signs anything.
"""
from __future__ import annotations
import sys, time, threading, sqlite3, json, os
from collections import deque, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rhradar import rpc                      # noqa: E402
from rhradar.pons import *                   # noqa: E402,F403
from rhradar.rpc import logs, addr           # noqa: E402

MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
ZERO = "0x" + "0" * 40
OWN = None                                  # our trading wallet, derived from the key in the environment (address only)
try:
    if os.environ.get("RH_PRIVATE_KEY"):
        from eth_account import Account
        OWN = Account.from_key(os.environ["RH_PRIVATE_KEY"]).address.lower()
except Exception:
    OWN = None
QUOTE_SYMS = {ZERO: "ETH"}
QUOTE_DEC = {ZERO: 18}                      # pair decimals, read with the symbol: USDG 6, cbBTC 8, stock tokens 18


def _amt(v: float) -> float:
    """Pair amounts for the API: three decimals for big numbers, four significant digits for small ones (cbBTC, ETH).
    Below 1e-9 of a pair unit (under a satoshi even for cbBTC) is float residue of in − out: zero."""
    if abs(v) < 1e-9: return 0.0
    return round(v, 3) if abs(v) >= 10 else float(f"{v:.4g}")
from strategies import Engine, live_allowed, RISK, KILL  # noqa: E402
import store                                             # noqa: E402  Postgres layer (DATABASE_URL); inert without it
DB = os.path.join(os.path.dirname(__file__), "terminal.db")


class Creators(dict):
    """Creator records by address. Creators this process touched live here; everyone else's record read from the database
    on start (`base`) moves in on first touch, so the snapshot's top-creators sort stays over the active ones."""
    FIELDS = ("launches", "graduated", "self_buy_launches", "dumps", "eth_out")

    def __init__(self):
        super().__init__(); self.base: dict[str, tuple] = {}

    def __missing__(self, k):
        v = self[k] = {**dict(zip(self.FIELDS, self.base.pop(k, None) or (0, 0, 0, 0, 0.0))), "tokens": []}
        return v


class Token:
    __slots__ = ("token", "curve", "creator", "block", "ts", "name", "symbol", "buys", "sells", "eth_in", "eth_out",
                 "buyers", "sellers", "snipe_tokens", "creator_tokens", "creator_sold_eth", "last_px", "graduated_block",
                 "peak_net", "net_hist", "tokens_bought", "tokens_sold", "last_trade_ts", "named", "quote", "quote_sym", "threshold", "smart", "partial", "fee_rate",
                 "launch_tx", "image", "x_url", "site", "meta",
                 "creator_tax_bps", "fee_recipient", "exempt_n", "dev_buy", "description", "tg_url", "phase", "named_at", "pool", "farm_key")

    def __init__(self, token, curve, creator, block, ts):
        self.token, self.curve, self.creator, self.block, self.ts = token, curve, creator, block, ts
        self.name = self.symbol = ""; self.named = False
        self.buys = self.sells = 0; self.eth_in = self.eth_out = 0.0
        self.buyers = set(); self.sellers = set()
        self.snipe_tokens = 0.0; self.creator_tokens = 0.0; self.creator_sold_eth = 0.0
        self.last_px = None; self.graduated_block = None; self.peak_net = 0.0
        self.net_hist = deque(maxlen=120)   # (ts, net)
        self.tokens_bought = self.tokens_sold = 0.0; self.last_trade_ts = ts
        self.quote = ZERO; self.quote_sym = "ETH"; self.threshold = GRAD_ETH; self.smart = set()
        self.partial = False        # launched before the trade backfill window: the tape's reserve is incomplete
        self.fee_rate = 0.02        # this curve's fee rate, read from its own buys (1–5%)
        self.launch_tx = None; self.image = None; self.x_url = None; self.site = None; self.meta = False   # from the launch calldata (image CID, X link, website)
        # launch record (factory + launchAndBuy calldata, read in name_tokens): what the launch itself declared
        self.creator_tax_bps = None; self.fee_recipient = None; self.exempt_n = None; self.dev_buy = None; self.description = ""; self.tg_url = None; self.phase = None; self.named_at = 0
        self.pool = None            # factory record once graduated: pair, tick_spacing, phase (State.mark_pools)
        self.farm_key = None        # launch-farm fingerprint, set once the launch calldata is read (State.name_tokens)

    @property
    def net(self): return self.eth_in - self.eth_out

    @property
    def eth_quoted(self): return self.quote == ZERO

    @property
    def eq(self):
        """ETH-equivalent per pair unit (as decoded, raw / 1e18). Pons gives every pair's curve the same shape, V0 = 0.4 ×
        threshold , so the ETH curve model works for any pair scaled by 4.2 / threshold."""
        return GRAD_ETH / self.threshold if self.threshold else 1.0

    @property
    def net_eq(self): return self.net * self.eq

    @property
    def scale(self):
        """Decoded pair amounts × scale = whole pair units (USDG has 6 decimals: ×1e12)."""
        return 10 ** (18 - QUOTE_DEC.get(self.quote, 18))

    @property
    def progress(self): return 100.0 if self.graduated_block else round(100 * min(self.net, self.threshold) / self.threshold, 1)

    def view(self, now):
        k = self.scale
        return {"token": self.token, "symbol": self.symbol, "name": self.name, "creator": self.creator, "block": self.block,
                "age_s": int(now - self.ts), "buys": self.buys, "sells": self.sells, "buyers": len(self.buyers), "sellers": len(self.sellers),
                # amounts in whole units of the launch's pair (ETH, USDG, NVDA…), not ETH
                "eth_in": _amt(self.eth_in * k), "eth_out": _amt(self.eth_out * k), "net": _amt(self.net * k),
                "progress": self.progress, "quote": self.quote_sym, "threshold": _amt(self.threshold * k),
                "snipe_pct": round(100 * self.snipe_tokens / 1e9, 1), "creator_pct": round(100 * self.creator_tokens / 1e9, 1),
                "creator_sold_eth": _amt(self.creator_sold_eth * k), "graduated": bool(self.graduated_block), "smart": len(self.smart),
                "px": self.last_px * k if self.last_px is not None else None, "peak_net": _amt(self.peak_net * k), "idle_s": int(now - self.last_trade_ts),
                "image": self.image, "x_url": self.x_url, "site": self.site,
                "creator_tax_bps": self.creator_tax_bps, "fee_third_party": (self.fee_recipient is not None and self.fee_recipient != self.creator),
                "exempt_n": self.exempt_n, "dev_buy": self.dev_buy * k if self.dev_buy is not None else None, "description": self.description[:140], "tg_url": self.tg_url}

    @property
    def third_party(self): return self.fee_recipient is not None and self.fee_recipient != self.creator

    @property
    def has_socials(self): return bool(self.x_url or self.site or self.tg_url)


class Busy(RuntimeError):
    """The state lock is held by a live order or a log batch; the API serves its last answer instead."""


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.tokens: dict[str, Token] = {}
        self.curve2tok: dict[str, str] = {}
        self.creators = Creators()
        self.tape = deque(maxlen=400)
        self.decisions = deque(maxlen=300)
        self.positions: dict[str, dict] = {}
        self.closed = deque(maxlen=200)
        self.hour = deque(maxlen=80000)     # (ts, kind, eth) for rolling 1h stats (~15k trades/h)
        self.head = 0; self.head_ts = 0; self.last_tick = 0; self.tick_ms = 0; self.rpc_calls = 0
        self.started = time.time(); self.backfilled = False; self.errors = deque(maxlen=20); self.phase = "init"
        self.launch_seq = 0; self.launch_log = deque(maxlen=500)      # (seq, token): what the feed stream pushes as it lands
        self.farm = defaultdict(list)             # launch-farm fingerprint -> [(launch ts, creator)], kept 2 h
        self.name_wake = threading.Event()        # a new launch wakes the naming thread at once (no row without a name)
        self.engine = Engine(); self.engine.farm_hook = self.farm_twins
        self.seen_fills = set(); self.seen_order = deque(maxlen=200000)
        self.wallet_tokens = defaultdict(dict)     # wallet -> {token: first buy block}
        self.grad_blocks = {}                      # token -> graduation block (history + live)
        self.wallet_stats = {}                     # wallet -> (launches bought, graduated) from smart_wallets.json.gz when no history.db
        # what the Postgres writer carries on its next round (store.py); kept up without a database too, it costs nothing
        self.launch_meta: dict[str, tuple] = {}    # token -> (launch ts, creator) for the last day, whole Token loaded or not
        self.minutes: dict[int, list] = {}         # minute -> [launches, grads, buys, sells, eth_in, eth_out], last 2 h
        self.fill_log = deque(maxlen=50000)        # (block, fill key): fills past the cursor are stored with it, never counted twice
        self.dirty = set(); self.dirty_creators = set(); self.dirty_minutes = set()
        self.new_buyers = []; self.pending_grads = []; self.saved_closed = set(); self.db_status = None
        self.last_block = 0; self.persist = False  # persist: the database was read back and its writer runs (loop)
        self.seed_wallets()
        self.ws_status = "off"; self.ws_events = 0; self.ws_blocks = 0; self.last_ws = 0
        self.db = sqlite3.connect(DB, check_same_thread=False)
        self.db.executescript("""CREATE TABLE IF NOT EXISTS launches(token TEXT PRIMARY KEY, curve TEXT, creator TEXT, block INT, ts INT);
        CREATE TABLE IF NOT EXISTS trades(tx TEXT, idx INT, token TEXT, kind TEXT, wallet TEXT, eth REAL, tokens REAL, block INT, ts INT, PRIMARY KEY(tx, idx));
        CREATE TABLE IF NOT EXISTS grads(token TEXT PRIMARY KEY, block INT, ts INT);
        CREATE TABLE IF NOT EXISTS decisions(ts INT, token TEXT, rule TEXT, action TEXT, reason TEXT);
        CREATE TABLE IF NOT EXISTS paper(token TEXT, rule TEXT, open_ts INT, close_ts INT, entry_px REAL, exit_px REAL, size_eth REAL, pnl_eth REAL, reason TEXT);""")

    def seed_wallets(self, fallback: bool = False):
        """Wallet records from history.db so 'smart buyer' counts mean something from the first minute."""
        if store.enabled() and not fallback: return    # the smart_wallets table is the record; store.restore reads it on start
        hp = os.path.join(os.path.dirname(__file__), "history.db")
        if not os.path.exists(hp):
            # no 6.4 GB history on this machine: a compact record per wallet (bought ≥3 launches), built from that history, instead
            sp = os.path.join(os.path.dirname(__file__), "smart_wallets.json.gz")
            if os.path.exists(sp):
                import gzip
                try:
                    self.wallet_stats = {w: (int(v[0]), int(v[1])) for w, v in json.load(gzip.open(sp, "rt"))["wallets"].items()}
                    self.errors.append(f"seeded {len(self.wallet_stats)} wallet records from smart_wallets.json.gz")
                except Exception as e:
                    self.errors.append(f"seed snapshot: {str(e)[:80]}")
            # the dev records ship the same way: launches, graduations, self-buys, dumps per creator
            cp = os.path.join(os.path.dirname(__file__), "creators.json.gz")
            if os.path.exists(cp):
                import gzip
                try:
                    self.creators.base = {c: tuple(v) for c, v in json.load(gzip.open(cp, "rt"))["creators"].items()}
                    self.errors.append(f"seeded {len(self.creators.base)} dev records from creators.json.gz")
                except Exception as e:
                    self.errors.append(f"seed dev records: {str(e)[:80]}")
            return
        try:
            h = sqlite3.connect(hp)
            self.grad_blocks.update(dict(h.execute("select token, block from grads").fetchall()))
            c2t = dict(h.execute("select curve, token from launches").fetchall())
            for w, cv, b in h.execute("select wallet, curve, min(block) from trades where kind='buy' group by wallet, curve"):
                tok = c2t.get(cv)
                if tok: self.wallet_tokens[w][tok] = b
            self.errors.append(f"seeded {len(self.wallet_tokens)} wallets, {len(self.grad_blocks)} graduations from history.db")
        except Exception as e:
            self.errors.append(f"seed: {str(e)[:80]}")

    def register_token(self, tok: str, hours: int = 72) -> "Token | None":
        """Register a token launched before our window (e.g. a live position restored after a restart)."""
        tok = tok.lower()
        if tok in self.tokens: return self.tokens[tok]
        h = rpc.head(); a = h - int(hours * 3600 / rpc.BLOCK_S)
        found = logs(a, h, FACTORY, [TOKEN_LAUNCHED, rpc.topic(tok)])
        if not found: return None
        self.add_launch(found[0], tape=False)
        t = self.tokens[tok]; t.partial = True
        try: self.refresh_reserve(t); t.last_px = curve_price(t)
        except Exception as e: self.errors.append(f"register {tok[:10]}: {str(e)[:60]}")
        return t

    def refresh_reserve(self, t: "Token"):
        """Replace the tape-derived reserve with the one implied by the curve's own quote (one eth_call)."""
        if not t.eth_quoted: return            # the reserve is backed out of a payable ETH quote; a pair curve keeps the tape's
        import exec_pons
        r = exec_pons.reserve_from_quote(t.curve, fee=exec_pons.curve_fee(t.curve))   # fee + creator tax shrink what a buy puts in
        t.eth_in = r + t.eth_out; t.partial = False
        t.peak_net = max(t.peak_net, t.net); self.dirty.add(t.token)

    def is_smart(self, w: str, tok: str, blk: int) -> bool:
        hist = self.wallet_tokens[w]
        prior = [(tk, b) for tk, b in hist.items() if tk != tok and b < blk]
        n0, g0 = self.wallet_stats.get(w, (0, 0))       # the snapshot record (empty when history.db seeded wallet_tokens)
        n = n0 + len(prior)
        if n < 3: return False
        g = g0 + sum(1 for tk, b in prior if self.grad_blocks.get(tk, 10**12) < blk)
        return g / n >= 0.25

    # ---- ingestion -------------------------------------------------------------------------
    def ts_of(self, block): return int(self.head_ts - (self.head - block) * rpc.BLOCK_S)

    def _stat(self, ts: int, kind: str, eth: float = 0.0):
        """One event into the rolling hour (pulse) and its minute bucket (stats_minute, what a restart reads the pulse from)."""
        self.hour.append((ts, kind, eth))
        m = ts - ts % 60; b = self.minutes.get(m)
        if b is None: b = self.minutes[m] = [0, 0, 0, 0, 0.0, 0.0]
        i = ("launch", "grad", "buy", "sell").index(kind); b[i] += 1
        if kind in ("buy", "sell"): b[i + 2] += eth
        self.dirty_minutes.add(m)

    def add_launch(self, l, tape=True):
        x = decode_launch(l)
        if x["token"] in self.tokens: return
        t = Token(x["token"], x["curve"], x["creator"], x["block"], self.ts_of(x["block"]))
        t.launch_tx = l.get("transactionHash")
        w = words(l["data"])
        if len(w) >= 3:
            t.quote = ("0x%040x" % w[0]) if w[0] else ZERO
            t.threshold = (w[2] / 1e18) if w[2] else GRAD_ETH
            t.quote_sym = QUOTE_SYMS.get(t.quote, t.quote[:6])
        self.tokens[t.token] = t; self.curve2tok[t.curve] = t.token
        if t.token in self.launch_meta: return     # read back from the database as a key row: counted before the restart
        self.launch_meta[t.token] = (t.ts, t.creator); self.dirty.add(t.token); self.dirty_creators.add(t.creator)
        c = self.creators[t.creator]; c["launches"] += 1; c["tokens"].append(t.token)
        self.db.execute("INSERT OR IGNORE INTO launches VALUES(?,?,?,?,?)", (t.token, t.curve, t.creator, t.block, t.ts))
        self._stat(t.ts, "launch")
        if tape: self.tape.appendleft({"ts": t.ts, "kind": "LAUNCH", "token": t.token, "sym": "", "wallet": t.creator, "eth": 0, "note": f"creator #{c['launches']}"})
        if self.backfilled: self.launch_seq += 1; self.launch_log.append((self.launch_seq, t.token)); self.name_wake.set()

    def add_grad(self, l, tape=True):
        tok = addr(l["topics"][1]); b = int(l["blockNumber"], 16)
        t = self.tokens.get(tok)
        if (t and t.graduated_block) or (not t and tok in self.grad_blocks): return      # socket + poller + restart replay: once
        self.db.execute("INSERT OR IGNORE INTO grads VALUES(?,?,?)", (tok, b, self.ts_of(b)))
        self._stat(self.ts_of(b), "grad")
        if not t:
            # a launch of the last day kept as a key row after a restart: its creator and buyers still get the graduation
            if tok in self.launch_meta:
                self.grad_blocks[tok] = b; cr = self.launch_meta[tok][1]
                self.creators[cr]["graduated"] += 1; self.dirty_creators.add(cr); self.pending_grads.append((b, tok))
            return
        t.graduated_block = b; self.creators[t.creator]["graduated"] += 1; self.grad_blocks[tok] = b
        self.dirty.add(tok); self.dirty_creators.add(t.creator)
        if tape: self.tape.appendleft({"ts": self.ts_of(b), "kind": "GRAD", "token": tok, "sym": t.symbol, "wallet": "", "eth": round(t.net, 3), "note": f"{(b - t.block) * rpc.BLOCK_S / 60:.1f} min after launch"})

    def add_trade(self, l, tape=True):
        tok = self.curve2tok.get(l["address"].lower())
        if not tok: return
        key = (l["transactionHash"], l["logIndex"])
        if key in self.seen_fills: return          # the socket and the poller both deliver; count once
        self.seen_fills.add(key); self.seen_order.append(key)
        if len(self.seen_fills) > 190000:
            old = self.seen_order.popleft(); self.seen_fills.discard(old)
        t = self.tokens[tok]
        if l["topics"][0] == CURVE_BUY:
            d = decode_buy(l); t.buys += 1; t.eth_in += d["eth"] - d["fee"]; t.buyers.add(d["wallet"]); t.tokens_bought += d["tokens"]   # reserve is net of fee
            if d["tokens"] > 0: t.last_px = d["eth"] / d["tokens"]
            # snipers = others buying in the launch window; the creator's own buy there (launchAndBuy dev buy) is creator_pct, not a snipe
            if d["block"] <= t.block + 2 and d["wallet"] != t.creator: t.snipe_tokens += d["tokens"]
            if self.is_smart(d["wallet"], tok, d["block"]): t.smart.add(d["wallet"])
            if d["eth"] * t.eq > 0.002: t.fee_rate = d["fee"] / d["eth"]
            if tok not in self.wallet_tokens[d["wallet"]]:
                self.wallet_tokens[d["wallet"]][tok] = d["block"]
                if self.persist: self.new_buyers.append((tok, d["wallet"], d["block"]))    # no writer: nothing would drain it
            if d["wallet"] == t.creator:
                if t.creator_tokens == 0: self.creators[t.creator]["self_buy_launches"] += 1
                t.creator_tokens += d["tokens"]; self.dirty_creators.add(t.creator)
            kind = "BUY"
        else:
            d = decode_sell(l); t.sells += 1; t.eth_out += d["eth"] + d["fee"]; t.sellers.add(d["wallet"]); t.tokens_sold += d["tokens"]   # fee leaves the reserve too
            if d["tokens"] > 0: t.last_px = d["eth"] / d["tokens"]
            if d["wallet"] == t.creator:
                if t.creator_sold_eth == 0: self.creators[t.creator]["dumps"] += 1
                t.creator_sold_eth += d["eth"]; self.creators[t.creator]["eth_out"] += d["eth"]; self.dirty_creators.add(t.creator)
            kind = "SELL"
        ts = self.ts_of(d["block"]); t.last_trade_ts = ts
        self.fill_log.append((d["block"], key)); self.dirty.add(tok)
        if OWN and d["wallet"] == OWN: self.engine.on_own_fill(tok, d)
        t.peak_net = max(t.peak_net, t.net); t.net_hist.append((ts, t.net))
        self.db.execute("INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?,?)", (d["tx"], d["idx"], tok, kind.lower(), d["wallet"], d["eth"], d["tokens"], d["block"], ts))
        self._stat(ts, kind.lower(), d["eth"] if t.eth_quoted else 0.0)      # eth_in_1h counts ETH pairs only
        if tape and (d["eth"] >= 0.05 or kind == "SELL" and d["wallet"] == t.creator):
            self.tape.appendleft({"ts": ts, "kind": kind, "token": tok, "sym": t.symbol, "wallet": d["wallet"], "eth": round(d["eth"], 4),
                                  "note": "CREATOR" if d["wallet"] == t.creator else ("snipe" if d["block"] <= t.block + 2 else "")})
        return t

    def name_tokens(self, limit=15):
        """Fill name/symbol + launch record for tokens without a name yet, one HTTP batch per call.
        Every launch of the feed window is named right away, buys or not: the free publicnode RPC made the
        old "only tokens that trade" rule (a paid-Alchemy saving) pointless, and it left half the feed "(unnamed)"
        with no tax, image or socials. Newest launches first, then anything still trading in the last 15 minutes."""
        now_ts = int(time.time())
        fresh, trading = now_ts - 1200, now_ts - 900
        # + launches that graduated in the last hour: the --graduated list shows them long after their curve went quiet
        grad_cut = now_ts - 3600
        todo = sorted((t for t in self.tokens.values() if not t.named and (t.ts >= fresh or (t.buys > 0 and t.last_trade_ts >= trading)
                                                                            or (t.graduated_block and self.ts_of(t.graduated_block) >= grad_cut))),
                      # graduations first: a handful an hour, and after a restart the fresh launches would bury them for minutes
                      key=lambda t: (0 if t.graduated_block else 1, -max(t.ts, t.last_trade_ts)))[:limit]
        self.phase = "naming"
        for fk in list(self.farm):                         # fingerprints older than 2 h can no longer twin a feed launch
            if all(ts < now_ts - 7200 for ts, _ in self.farm[fk]): del self.farm[fk]
        for q in {t.quote for t in self.tokens.values()} - set(QUOTE_SYMS):
            try:
                dec = rpc.call("eth_call", [{"to": q, "data": "0x313ce567"}, "latest"])          # decimals()
                QUOTE_DEC[q] = int(dec, 16) if dec and dec != "0x" else 18
                QUOTE_SYMS[q] = _str(rpc.call("eth_call", [{"to": q, "data": "0x95d89b41"}, "latest"])) or q[:6]
                for t in self.tokens.values():
                    if t.quote == q: t.quote_sym = QUOTE_SYMS[q]
            except Exception: QUOTE_SYMS[q] = q[:6]
        if not todo: return
        calls = []
        for t in todo:
            calls += [("eth_call", [{"to": t.token, "data": "0x95d89b41"}, "latest"]), ("eth_call", [{"to": t.token, "data": "0x06fdde03"}, "latest"]),
                      ("eth_call", [{"to": FACTORY, "data": SEL_GET_LAUNCHED + t.token[2:].lower().rjust(64, "0")}, "latest"]),   # factory record
                      ("eth_call", [{"to": t.token, "data": SEL_TOKEN_INFO}, "latest"])]                                              # logo, description, socials
        with_tx = [t for t in todo if t.launch_tx and not t.meta]
        for t in with_tx: calls.append(("eth_getTransactionByHash", [t.launch_tx]))        # launch calldata: dev buy, exempt wallets (bundle)
        try:
            res = rpc.batch(calls, timeout=30); self.rpc_calls += 1
        except Exception as e:
            self.errors.append(f"{time.strftime('%H:%M:%S')} name batch: {str(e)[:80]}"); return
        k = 4 * len(todo)
        for t, sym, n, rec, info in zip(todo, res[0:k:4], res[1:k:4], res[2:k:4], res[3:k:4]):
            t.symbol = _str(sym); t.name = _str(n); t.named = True; t.named_at = int(time.time()); self.dirty.add(t.token)
            r = decode_launched(rec)
            if r: t.fee_recipient, t.creator_tax_bps, t.phase = r["fee_recipient"], r["creator_tax_bps"], r["phase"]
            i = decode_token_info(info)
            if i:
                t.image = t.image or i["image"]; t.description = i["description"]
                t.x_url = i["x_url"] or t.x_url; t.tg_url = i["tg_url"]; t.site = i["site"] or t.site
        for t, tx in zip(with_tx, res[k:]):
            t.meta = True; self.dirty.add(t.token)
            try:
                inp = (tx or {}).get("input", "")
                lb = decode_launch_and_buy(inp)
                if lb:
                    t.dev_buy = lb["dev_buy_wei"] / 1e18; t.exempt_n = len(lb["exempt"])
                    # launch farm fingerprint: what the launch calldata fixed — exact dev-buy wei,
                    # creator tax, which links, declared exempt wallets; twins are counted per request in row_extra
                    t.farm_key = f"{lb['dev_buy_wei']}|{t.creator_tax_bps}|{int(bool(t.x_url))}{int(bool(t.site))}{int(bool(t.tg_url))}|{len(lb['exempt'])}"
                    self.farm[t.farm_key].append((t.ts, t.creator))
                else:
                    t.dev_buy = t.dev_buy if t.dev_buy is not None else 0.0; t.exempt_n = t.exempt_n if t.exempt_n is not None else 0
                if not (t.image and t.x_url):
                    m = launch_meta(inp)
                    t.image = t.image or m["image"]; t.x_url = t.x_url or m["x_url"]; t.site = t.site or m["site"]
            except Exception: pass

    # ---- graduated positions: marks from the v4 pool -----------------------------------------
    def mark_pools(self):
        """Every position whose token has graduated gets priced in its Uniswap v4 pool: the Quoter's answer for the held
        amount (after the hook fee and impact), in ETH-equivalent, written as p["pool_value"] / p["pool_ts"]. Network calls
        run outside the lock. The factory record (pair, tick spacing, phase) is read once; a swept launch (phase 1, pool not
        created yet) is re-read every 10 s. One Quoter call per token, not per position, so the RPC load does
        not grow with the number of users holding it: the largest holding is quoted, smaller ones are scaled from it
        (slightly conservative, since the price impact of the larger amount is spread over them)."""
        import pons_pool
        with self.soft(5.0):
            held = defaultdict(list)            # token -> [(position, tokens held)]
            for b in self.engine.books.values():
                for p in b.positions.values():
                    if p["token"] in self.tokens and self.tokens[p["token"]].graduated_block: held[p["token"]].append((p, p["tokens"]))
        now = time.time(); marks = []
        for tok, ps in held.items():
            t = self.tokens[tok]
            try:
                if not t.pool or (t.pool["phase"] != 2 and now - t.pool["at"] > 10):
                    t.pool = {**pons_pool.record(t.token), "at": now}
                if t.pool["phase"] != 2: continue
                n_max = max(n for _, n in ps)
                raw = pons_pool.quote_sell(t.token, int(n_max * 1e18), t.pool["pair"], t.pool["tick_spacing"])
                per_token = raw / 1e18 * t.eq / n_max if n_max else 0.0
                marks += [(p, n, per_token * n) for p, n in ps]
            except Exception as e:
                self.errors.append(f"{time.strftime('%H:%M:%S')} pool mark {t.token[:10]}: {str(e)[:60]}")
        with self.soft(5.0):
            for p, n, v in marks:
                if p.get("tokens") == n: p["pool_value"] = v; p["pool_ts"] = int(now)       # a half sold meanwhile: next round
            self.rpc_calls += len(held)

    # ---- strategies (paper) ------------------------------------------------------------------
    def decide(self, t: Token, now: int):
        self.engine.evaluate(t, self.creators[t.creator], now, self.db)

    def manage(self, now: int):
        for p in self.engine.fill_pending(self.tokens, time.time(), self.db):
            self.tape.appendleft({"ts": now, "kind": "PAPER", "token": p["token"], "sym": p["sym"], "wallet": p["strategy"], "eth": p["size"], "note": f"BUY · {p['why'][:60]}"})
        for p in self.engine.manage(self.tokens, now, self.db):
            self.tape.appendleft({"ts": now, "kind": "PAPER", "token": p["token"], "sym": p["sym"], "wallet": p["strategy"],
                                  "eth": round(p["size"] + p["pnl"], 4), "note": f"SELL {p['pnl']:+.4f} · {p['close_reason']}"})

    # ---- views -------------------------------------------------------------------------------
    def snapshot(self):
        """Never block the UI on the state lock: if a live order or a log batch holds it, serve the last snapshot as stale."""
        if not self.lock.acquire(timeout=1.5):
            last = getattr(self, "_last_snap", None)
            if last: return {**last, "stale_s": int(time.time() - last["now"])}
            self.lock.acquire()
        try:
            snap = self._snapshot(); self._last_snap = snap; return snap
        finally:
            self.lock.release()

    def _snapshot(self):
        if True:
            now = int(time.time())
            toks = list(self.tokens.values())
            hr = [x for x in self.hour if x[0] >= now - 3600]
            recent = sorted(toks, key=lambda t: -t.ts)[:80]
            hot = sorted([t for t in toks if not t.graduated_block and t.buys > 0 and now - t.last_trade_ts < 600], key=lambda t: (-t.progress, -len(t.buyers)))[:25]
            grads = sorted([t for t in toks if t.graduated_block], key=lambda t: -t.graduated_block)[:20]
            cre = sorted(((a, c) for a, c in self.creators.items() if a != MULTICALL3), key=lambda kv: -kv[1]["launches"])[:15]
            return {
                "now": now, "head": self.head, "head_age_s": int(now - self.head_ts) if self.head_ts else None, "tick_ms": self.tick_ms,
                "live": {"wallet": OWN, "allowed": live_allowed(), "risk": RISK, "kill": os.path.exists(KILL)},
                "phase": self.phase, "ws": {"status": self.ws_status, "events": self.ws_events, "blocks": self.ws_blocks,
                                            "age_s": int(now - self.last_ws) if self.last_ws else None},
                "rpc_calls": self.rpc_calls, "cu": dict(rpc.CU), "fast": bool(rpc.FAST), "uptime_s": int(now - self.started), "backfilled": self.backfilled, "errors": list(self.errors)[-5:],
                "db": self.db_status if store.enabled() else None,
                "pulse": {"launches_1h": sum(1 for x in hr if x[1] == "launch"), "grads_1h": sum(1 for x in hr if x[1] == "grad"),
                          "buys_1h": sum(1 for x in hr if x[1] == "buy"), "sells_1h": sum(1 for x in hr if x[1] == "sell"),
                          "eth_in_1h": round(sum(x[2] for x in hr if x[1] == "buy"), 2), "eth_out_1h": round(sum(x[2] for x in hr if x[1] == "sell"), 2),
                          "tokens_tracked": len(toks), "creators": len(self.creators),
                          "serial_creators": sum(1 for c in self.creators.values() if c["launches"] > 1)},
                "recent": [t.view(now) for t in recent], "hot": [t.view(now) for t in hot], "grads": [t.view(now) for t in grads],
                "creators": [{"creator": a, **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items() if k != "tokens"}, "last": c["tokens"][-1] if c["tokens"] else None} for a, c in cre],
                "tape": list(self.tape)[:120],
                "strategies": self.engine.view(self.tokens, now),
            }

    def soft(self, timeout: float = 1.5):
        """Lock for read-only API views: give up instead of hanging behind a live order or a log batch."""
        import contextlib
        @contextlib.contextmanager
        def cm():
            if not self.lock.acquire(timeout=timeout): raise Busy()
            try: yield
            finally: self.lock.release()
        return cm()

    def _rows(self, toks, now: int) -> list:
        """Feed rows: facts about each launch only. No strategy checklist — the feed is the same for every
        visitor (the coin card carries every strategy's take); the empty checklist fields keep the row shape stable."""
        return [{**t.view(now), **self.row_extra(t, now), "items": [], "failed": [], "ready": False, "held": False, "done": False, "ts": t.ts}
                for t in toks if shown(t, now)]

    def feed(self, sid: str | None = None, limit: int = 100, minutes: int = 20):
        """The public feed for polling (the stream's fallback) and the pulse: the newest launches. `sid` is accepted and ignored."""
        if not self.backfilled:      # the backfill holds the lock for minutes: answer at once instead of queueing behind it
            return {"strategy": None, "rows": [], "now": int(time.time()), "warming": True, "phase": self.phase}
        with self.soft():
            now = int(time.time())
            cut = now - minutes * 60
            rows = self._rows(sorted((t for t in self.tokens.values() if t.ts >= cut), key=lambda t: -t.block)[:limit], now)
            return {"strategy": None, "rows": rows, "now": now,
                    "head": self.head, "head_age_s": int(now - self.head_ts) if self.head_ts else None,
                    "pulse": {"launches_1h": sum(1 for x in self.hour if x[0] >= now - 3600 and x[1] == "launch"),
                              "grads_1h": sum(1 for x in self.hour if x[0] >= now - 3600 and x[1] == "grad"),
                              "launches_5m": sum(1 for x in self.hour if x[0] >= now - 300 and x[1] == "launch"),
                              "buys_1h": sum(1 for x in self.hour if x[0] >= now - 3600 and x[1] == "buy"),
                              "eth_in_1h": round(sum(x[2] for x in self.hour if x[0] >= now - 3600 and x[1] == "buy"), 1),
                              "tracked": sum(1 for t in self.tokens.values() if not t.graduated_block and now - t.last_trade_ts < 600)}}

    def lists(self, limit: int = 50) -> dict:
        """Every feed filter looks at the last hour . The stream carries the 100 newest launches (a few
        minutes); this adds what they cannot hold, plus the hour's counts for the filter badges:
        hot — launched this hour, 5+ trades in the last minute, busiest first;
        near — launched this hour, curve 60 %+, not graduated, traded in the last 10 min, fullest first;
        graduated — graduated in the last hour, newest graduation first (pons sees ~5 an hour)."""
        with self.soft():
            now = int(time.time()); hour = now - 3600
            row = lambda t: {**t.view(now), **self.row_extra(t, now), "items": [], "failed": [], "ready": False, "held": False, "done": False, "ts": t.ts}
            launched = [t for t in self.tokens.values() if t.ts >= hour and shown(t, now)]
            busy = [(t, sum(1 for ts, _ in t.net_hist if ts >= now - 60)) for t in launched if not t.graduated_block and now - t.last_trade_ts <= 60]
            hot = [t for t, n in sorted(busy, key=lambda x: -x[1]) if n >= 5][:limit]
            near = sorted((t for t in launched if not t.graduated_block and t.progress >= 60 and now - t.last_trade_ts <= 600),
                          key=lambda t: -t.progress)[:limit]
            grads = sorted((t for t in self.tokens.values() if t.graduated_block and self.ts_of(t.graduated_block) >= hour),
                           key=lambda t: -t.graduated_block)[:limit]
            return {"hot": [row(t) for t in hot], "near": [row(t) for t in near],
                    "graduated": [{**row(t), "graduated_ts": self.ts_of(t.graduated_block)} for t in grads],
                    "counts": {"all": len(launched), "live": sum(1 for t in launched if t.buys > 0)}, "now": now}

    def feed_rows(self, tokens: list | None = None, minutes: int = 20, limit: int = 100) -> dict:
        """Rows for the feed stream: the newest launches of the window (tokens=None) or just the given ones."""
        with self.soft():
            now = int(time.time())
            if tokens is None:
                cut = now - minutes * 60
                toks = sorted((t for t in self.tokens.values() if t.ts >= cut and shown(t, now)), key=lambda t: -t.block)[:limit]
            else:
                toks = [self.tokens[x] for x in tokens if x in self.tokens]
            return {"strategy": None, "rows": self._rows(toks, now), "now": now, "head": self.head,
                    "head_age_s": int(now - self.head_ts) if self.head_ts else None}

    def preview(self, strategy: dict, minutes: int = 20, limit: int = 150) -> dict:
        """How many launches of the feed window a draft strategy would pass right now — for the editor, before saving.
        Nothing is stored: the draft is normalized, checked against the same checklist the engine trades on, and dropped."""
        from strategies import normalize
        s = normalize(strategy)
        with self.soft():
            now = int(time.time()); cut = now - minutes * 60
            toks = sorted((t for t in self.tokens.values() if t.ts >= cut), key=lambda t: -t.block)[:limit]
            ready = near = 0
            for t in toks:
                if not t.buys: continue
                if s.get("quote", "ETH") == "ETH" and not t.eth_quoted: continue
                cl = self.engine.checklist(s, t, self.creators[t.creator], now)
                if cl.get("ready"): ready += 1
                elif len(cl.get("failed", [])) == 1: near += 1
            return {"pass": ready, "near": near, "total": len(toks), "now": now}

    def farm_twins(self, t: Token) -> int:
        """Earlier launches (30 min) from other creators with this launch's exact fingerprint; 0 until the calldata is read."""
        if not t.farm_key: return 0
        return sum(1 for ts, cr in self.farm.get(t.farm_key, ()) if t.ts - 1800 <= ts <= t.ts and cr != t.creator)

    def row_extra(self, t: Token, now: int) -> dict:
        """Strategy-free facts the terminal shades feed rows with: the creator's record (without this launch) and how alive
        the curve is — last trade, trades in the last minute, share of the curve filled in the last minute."""
        c = self.creators[t.creator]; cut = now - 60
        hist = list(t.net_hist)
        recent = [x for x in hist if x[0] >= cut]
        before = [x for x in hist if x[0] < cut]
        net_then = before[-1][1] if before else (0.0 if t.ts >= cut else (recent[0][1] if recent else t.net))
        return {"creator_prior": max(c["launches"] - 1, 0), "creator_dumps": max(c["dumps"] - (1 if t.creator_sold_eth > 0 else 0), 0),
                "creator_grads": max(c["graduated"] - (1 if t.graduated_block else 0), 0),
                "last_trade_ts": t.last_trade_ts if (t.buys or t.sells) else None, "trades_1m": len(recent),
                "farm_twins": self.farm_twins(t),
                "flow_1m": round(100 * (t.net - net_then) / t.threshold, 1) if t.threshold else 0.0}

    def changed_since(self, ts: int, minutes: int = 20) -> list:
        """Tokens of the feed window that traded (or got named) since `ts` — the stream's delta."""
        with self.soft():
            now = int(time.time()); cut = now - minutes * 60
            return [t.token for t in self.tokens.values() if t.ts >= cut and shown(t, now) and (t.last_trade_ts >= ts or (t.named and getattr(t, "named_at", 0) >= ts))]

    def token_detail(self, tok, owner: str | None = None):
        """The coin card. Strategy takes and marks come from one owner's strategies only: the house set (owner None,
        public) or a signed-in user's own — never everyone's."""
        with self.lock:
            t = self.tokens.get(tok.lower())
            if not t: return None
            rows = self.db.execute("SELECT kind, wallet, eth, tokens, block, ts FROM trades WHERE token=? ORDER BY block DESC, idx DESC LIMIT 150", (tok.lower(),)).fetchall()
            ticks = self.db.execute("SELECT ts, kind, eth, tokens, wallet FROM trades WHERE token=? AND tokens>0 ORDER BY block, idx", (tok.lower(),)).fetchall()
            own = [s for s in self.engine.strats if s.get("owner") == owner]
            marks = []
            for sid, b in ((s["id"], self.engine.books[s["id"]]) for s in own if s["id"] in self.engine.books):
                for d in b.decisions:
                    if d["token"] == t.token and d["action"] in ("BUY", "SELL"): marks.append({"ts": d["ts"], "strategy": sid, "action": d["action"], "reason": d["reason"]})
                p = b.positions.get(t.token)
                if p: marks.append({"ts": p["open_ts"], "strategy": sid, "action": "OPEN", "px": p["entry_px"] / t.eq * t.scale})   # book price is ETH-eq
            k = t.scale                                     # trades are stored as decoded (raw / 1e18); show whole pair units
            now = int(time.time())
            # every strategy's take on this launch (coin card, tab "strategies"): holds it, traded it, or its checklist stage
            strats = []
            for s in own:
                b = self.engine.books.get(s["id"])
                if not b: continue
                row = {"id": s["id"], "name": s["name"], "enabled": bool(s.get("enabled")), "items": [], "failed": [], "blocked": []}
                if t.buys and not t.graduated_block:
                    cl = self.engine.checklist(s, t, self.creators[t.creator], now)
                    row.update(stage=cl["stage"], items=cl["items"], failed=cl["failed"], blocked=cl["blocked"])
                else:
                    row["stage"] = "graduated" if t.graduated_block else "no buys"
                p = b.positions.get(t.token)
                if p:
                    val = self.engine.value(p, t) + p.get("realized", 0.0)
                    row.update(stage="held", pnl_pct=round(100 * (val - p["size"]) / p["size"], 1), venue="pool" if t.graduated_block else "curve")
                elif t.token in b.done:
                    c = next((c for c in b.closed if c["token"] == t.token), None)
                    row.update(stage="traded", pnl_pct=c.get("pnl_pct") if c else None, reason=c.get("close_reason") if c else None)
                strats.append(row)
            return {**t.view(now), **self.row_extra(t, now), "ts": t.ts, "strategies": strats, "creator_record": dict(self.creators[t.creator], tokens=self.creators[t.creator]["tokens"][-10:]),
                    "trades": [dict(zip(("kind", "wallet", "eth", "tokens", "block", "ts"), (r[0], r[1], r[2] * k, r[3], r[4], r[5]))) for r in rows],
                    "ticks": [[r[0], r[1][0], float(f"{r[2] * k / r[3]:.6g}"), _amt(r[2] * k), r[4] == t.creator] for r in ticks],
                    "marks": marks, "net_hist": list(t.net_hist)}


NAME_WAIT_S = 3


def shown(t, now: float) -> bool:
    """The feed shows a launch once its name is read, or after NAME_WAIT_S if the read is slow: no "(unnamed)" flash."""
    return t.named or now - t.ts >= NAME_WAIT_S


def curve_price(t) -> float:
    """Marginal curve price at the token's current reserve (used when a restored token has no trade yet)."""
    import curve
    return curve.price(max(t.net_eq, 0.0)) / t.eq


from web3 import Web3 as _W3
from eth_abi import decode as _abi_decode


def _hx(v: str) -> str: return v if v.startswith("0x") else "0x" + v


SEL_GET_LAUNCHED = _hx(_W3.keccak(text="getLaunchedToken(address)")[:4].hex())
SEL_TOKEN_INFO = _hx(_W3.keccak(text="getTokenInfo()")[:4].hex())
SEL_LAUNCH_AND_BUY = _hx(_W3.keccak(text="launchAndBuy((string,string,string,string,(string,string,string,string,string),address,uint16,bool,bytes32,bytes32),uint256,address,uint256,uint256,address,address[])")[:4].hex())
LAUNCHED_TYPES = ["address", "address", "address", "address", "address", "uint256", "uint24", "int24", "uint16", "bool", "uint8", "uint256", "uint256", "uint256", "bool"]
TOKEN_INFO_TYPES = ["address", "string", "string", "(string,string,string,string,string)"]
LAUNCH_AND_BUY_TYPES = ["(string,string,string,string,(string,string,string,string,string),address,uint16,bool,bytes32,bytes32)", "uint256", "address", "uint256", "uint256", "address", "address[]"]


def decode_launched(r: str) -> dict | None:
    """factory.getLaunchedToken(token) → creatorFeeRecipient, creatorTaxBps, phase (0 curve · 1 swept · 2 pool · 3 rescued)."""
    try:
        if not r or len(r) < 2 + 64 * 15: return None
        v = _abi_decode(LAUNCHED_TYPES, bytes.fromhex(r[2:]))
        if not v[14]: return None
        return {"fee_recipient": v[3].lower(), "creator_tax_bps": int(v[8]), "phase": int(v[10])}
    except Exception:
        return None


def decode_token_info(r: str) -> dict | None:
    """token.getTokenInfo() → logo, description, socials {twitter, telegram, discord, website, farcaster}."""
    try:
        if not r or r == "0x": return None
        dep, logo, desc, soc = _abi_decode(TOKEN_INFO_TYPES, bytes.fromhex(r[2:]))
        return {"image": logo or None, "description": (desc or "").strip(), "x_url": (soc[0] or "").strip() or None,
                "tg_url": (soc[1] or "").strip() or None, "site": (soc[3] or "").strip() or None}
    except Exception:
        return None


def decode_launch_and_buy(inp: str) -> dict | None:
    """The launch transaction's calldata when it went through the pons LaunchAndBuy router: dev buy (quote units) and the
    wallets declared exempt from the opening tax (the declared bundle)."""
    try:
        if not inp or not inp.lower().startswith(SEL_LAUNCH_AND_BUY): return None
        v = _abi_decode(LAUNCH_AND_BUY_TYPES, bytes.fromhex(inp[10:]))
        return {"dev_buy_wei": int(v[3]), "recipient": v[5].lower(), "exempt": [a.lower() for a in v[6]]}
    except Exception:
        return None


def launch_meta(inp: str) -> dict:
    """Strings ABI-encoded in the factory launch calldata → {image, x_url, site}. Image is an IPFS CID or URL."""
    out = {"image": None, "x_url": None, "site": None}
    if not inp or len(inp) < 10: return out
    b = bytes.fromhex(inp[10:] if inp.startswith("0x") else inp[8:])
    strs = []
    for off in range(0, max(len(b) - 32, 0), 32):
        n = int.from_bytes(b[off:off + 32], "big")
        if 0 < n <= 2000 and off + 32 + n <= len(b):
            try: s = b[off + 32:off + 32 + n].decode("utf-8")
            except UnicodeDecodeError: continue
            if s and all(ch.isprintable() for ch in s): strs.append(s.strip())
    for s in strs:
        low = s.lower()
        if not out["image"] and (low.startswith(("baf", "qm", "ipfs://")) or (low.startswith("http") and any(low.endswith(e) for e in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")))):
            out["image"] = s
        elif not out["x_url"] and ("x.com/" in low or "twitter.com/" in low):
            out["x_url"] = s if low.startswith("http") else "https://" + s
        elif not out["site"] and low.startswith("http") and "t.me/" not in low:
            out["site"] = s
    return out


def _str(r):
    if not r or r == "0x": return ""
    b = bytes.fromhex(r[2:])
    try:
        off = int.from_bytes(b[:32], "big"); ln = int.from_bytes(b[off:off + 32], "big"); return b[off + 32:off + 32 + ln].decode(errors="replace")[:24]
    except Exception:
        return b.rstrip(b"\0").decode(errors="replace")[:24]


STATE = State()


def backfill(launch_min=60, trade_min=60, since: int | None = None):
    """Refill memory after a start. Without a database: the last hour read back off the chain (~4 min) — the feed, its
    last-hour filters and the coin card need that hour. With one, store.restore has already read the last day back and
    `since` is the block after its cursor: only the gap is read (a deploy: seconds), capped at the same hour."""
    s = STATE
    h = rpc.head(); hts = rpc.block_ts(h)
    with s.lock: s.head, s.head_ts = h, hts; s.rpc_calls += 2
    a = h - int(launch_min * 60 / rpc.BLOCK_S)
    if since is not None:
        if since < a: s.errors.append(f"db cursor {h - since} blocks behind: only the last hour is read back, the rest of the gap is lost")
        a = max(a, since)
    ls = logs(a, h, FACTORY, [TOKEN_LAUNCHED]); gs = logs(a, h, FACTORY, [POOL_GRADUATED])
    with s.lock:
        for l in ls: s.add_launch(l, tape=False)
        for l in gs: s.add_grad(l, tape=False)
        s.db.commit()
    b = h - int(trade_min * 60 / rpc.BLOCK_S) if since is None else a
    for chunk_a in range(b, h + 1, 6000):
        tr = logs(chunk_a, min(chunk_a + 5999, h), None, [[CURVE_BUY, CURVE_SELL]], span=6000)
        with s.lock:
            for l in tr: s.add_trade(l, tape=False)
            s.db.commit()
    with s.lock:
        for t in s.tokens.values():
            if since is None and t.block < b: t.partial = True     # its trades before the window were never seen
        s.engine.reserve_hook = s.refresh_reserve
        for tok in getattr(s.engine, "pending_restore", []):        # live positions from the previous process
            t = s.register_token(tok)
            s.errors.append(f"restored live position {tok[:10]}: {'ok' if t else 'token not found'}")
        s.backfilled = True; s.last_block = h


def exits_loop():
    s = STATE
    while True:
        time.sleep(1.0)
        try:
            with s.lock:
                if s.backfilled: s.manage(int(time.time()))
        except Exception as e:
            s.errors.append(f"exits: {str(e)[:80]}")


def naming_loop():
    """Names + launch metadata (image, X link) in their own thread: on a slow node a batch takes seconds and must not delay the tape."""
    s = STATE
    while True:
        # publicnode is free and fast (36-call batch in ~270 ms): a new launch wakes this at once, otherwise every 1.5 s
        s.name_wake.wait(1.5); s.name_wake.clear()
        try:
            if s.backfilled: s.name_tokens(limit=20)
        except Exception as e:
            s.errors.append(f"naming: {str(e)[:80]}")


def pool_loop():
    """Pool marks for graduated positions every 3 s (a few Quoter eth_calls; nothing when no book holds a graduated token)."""
    s = STATE
    while True:
        time.sleep(3.0)
        try:
            if s.backfilled: s.mark_pools()
        except Busy:
            pass
        except Exception as e:
            s.errors.append(f"pool marks: {str(e)[:80]}")


def loop(every=3.0):
    """Tape poller. With a fast provider (Alchemy): one eth_blockNumber + a ≤10-block eth_getLogs per second, ~0.4M CU/h.
    Without one, or after repeated fast-provider errors: the public node every `every` s in ≤1500-block windows."""
    s = STATE
    threading.Thread(target=exits_loop, daemon=True).start()
    threading.Thread(target=naming_loop, daemon=True).start()
    threading.Thread(target=pool_loop, daemon=True).start()
    # the database first, before the socket delivers anything: the last day back in seconds, then only the gap from the chain.
    # The writer runs only after a good read — writing absolute creator records from a half-empty memory would erase them.
    since = None
    if store.enabled():
        for attempt in range(3):
            try:
                store.ensure(s); info = store.restore(s, Token); s.persist = True
                since = info["block"] + 1 if info["block"] else None
                break
            except Exception as e:
                s.errors.append(f"db restore try {attempt + 1}/3: {str(e)[:100]}")
                if attempt < 2: time.sleep(5)
        if not s.persist:
            s.errors.append("db unavailable: this run keeps nothing, wallet records from the snapshot file")
            s.seed_wallets(fallback=True)
    ws_full = False
    try:
        import live_ws
        if live_ws.start(s): ws_full = live_ws.full_feed(live_ws.ws_url() or "")
    except Exception as e:
        s.errors.append(f"ws: {str(e)[:80]}")
    # a failed backfill used to start the feed empty with no retry: try three times, then go live without it
    for attempt in range(3):
        try:
            backfill(since=since); break
        except Exception as e:
            s.errors.append(f"backfill try {attempt + 1}/3: {str(e)[:100]}")
            if attempt < 2: time.sleep(10); continue
            with s.lock:
                s.head = rpc.head(); s.head_ts = rpc.block_ts(s.head); s.last_block = s.head; s.backfilled = True
    if s.persist: store.start(s)
    ALL = [[TOKEN_LAUNCHED, POOL_GRADUATED, CURVE_BUY, CURVE_SELL]]
    fast_until_fail = bool(rpc.FAST); fast_fails = 0; fast_retry_at = 0.0
    ts_h, ts_v, ts_at = s.head, s.head_ts, time.time()          # last real block timestamp; in between it is extrapolated
    lag = 0
    while True:
        t0 = time.time()
        # a full free socket (publicnode) carries the tape itself: the poller is then only a gap-filler through the public
        # node's wide getLogs every 20 s, and the metered provider is left to quotes and orders
        ws_alive = bool(ws_full and s.last_ws and time.time() - s.last_ws < 30)
        fast = (not ws_alive) and (fast_until_fail or (rpc.FAST and time.time() > fast_retry_at))
        try:
            s.phase = "head"; true_h = rpc.head()
            if not fast or time.time() - ts_at > 30:
                ts_h, ts_v, ts_at = true_h, rpc.block_ts(true_h), time.time()
            hts = int(ts_v + (true_h - ts_h) * rpc.BLOCK_S)
            with s.lock:
                s.head, s.head_ts = true_h, hts; s.rpc_calls += 1
                frm = s.last_block + 1
            lag = true_h - frm + 1
            use_fast = bool(fast and lag <= 300)               # a big gap is cheaper and faster through the public node's wide spans
            # fast mode: one FAST_SPAN-block getLogs per tick (60 CU); ~10 blocks/s arrive, so a lag of a few blocks just
            # rolls into the next tick. Only when the lag passes two spans do we spend extra calls to catch up (≤6 per tick).
            span = rpc.FAST_SPAN
            k = 1 if lag <= 2 * span else min(6, -(-lag // span))
            h = min(true_h, frm + (span * k if use_fast else 1500) - 1)
            if h >= frm:
                # network calls run WITHOUT the state lock: on a slow node they took 100+ s and froze every API request
                s.phase = f"{'fast' if use_fast else 'public'} logs {frm}-{h}"
                if use_fast:
                    try:
                        al = rpc.fast_logs(frm, h, ALL); fast_fails = 0; fast_until_fail = True
                    except rpc.RpcError as e:
                        fast_fails += 1
                        s.errors.append(f"{time.strftime('%H:%M:%S')} fast logs: {str(e)[:80]}")
                        if fast_fails >= 3:
                            fast_until_fail = False; fast_retry_at = time.time() + 120
                            s.errors.append(f"{time.strftime('%H:%M:%S')} fast provider off for 120 s, public node")
                        continue
                    ls = [l for l in al if l["address"].lower() == FACTORY.lower() and l["topics"][0] in (TOKEN_LAUNCHED, POOL_GRADUATED)]
                    tl = [l for l in al if l["topics"][0] in (CURVE_BUY, CURVE_SELL)]
                else:
                    ls = logs(frm, h, FACTORY, [[TOKEN_LAUNCHED, POOL_GRADUATED]])
                    tl = logs(frm, h, None, [[CURVE_BUY, CURVE_SELL]])
                with s.lock:
                    s.rpc_calls += 2
                    for l in ls:
                        (s.add_launch if l["topics"][0] == TOKEN_LAUNCHED else s.add_grad)(l)
                    touched = set()
                    for l in tl:
                        t = s.add_trade(l)
                        if t: touched.add(t.token)
                    now = int(time.time())
                    for tok in touched: s.decide(s.tokens[tok], now)
                    s.manage(now)
                    s.last_block = h
                    s.db.commit()
            s.phase = "idle"
        except Exception as e:
            s.errors.append(f"{time.strftime('%H:%M:%S')} tick: {str(e)[:100]}")
        s.tick_ms = int((time.time() - t0) * 1000); s.last_tick = time.time(); s.phase = "sleep"
        cadence = 20.0 if ws_alive else (1.0 if (fast_until_fail and lag <= 300) else every)
        time.sleep(max(0.2, cadence - (time.time() - t0)))
