"""User-defined paper strategies. Each one has filters, a size, exits, and its own book.

A strategy is a dict (stored in strategies.json, edited from the UI):
  id, name, enabled, size, cash, quote ("ETH" | "any"),
  entry: {min_age_s, max_age_s, min_buyers, [max_buyers], min_net, max_net, min_progress, max_progress, max_snipe_pct,
          max_creator_pct, creator_max_prior, creator_min_grads, creator_no_dumps, require_rising, min_buy_sell_ratio}
  exit:  {tp_pct, sl_pct, timeout_s, on_graduation, on_creator_sell, flow_reversal_pct}
Sizes, cash and net thresholds are ETH-equivalent: a pair launch (USDG, NVDA…) runs on the same curve model scaled by
4.2 / its threshold (indexer Token.eq); live orders spend the pair token itself.
After graduation a position is priced and sold in the launch's Uniswap v4 pool (pons_pool.py), not the closed curve.
"""
from __future__ import annotations
import json, os, time, uuid
from collections import deque
import curve

PATH = os.path.join(os.path.dirname(__file__), "strategies.json")
KILL = os.path.join(os.path.dirname(__file__), "KILL")          # touch this file: no new live orders
FEE = 0.01
# Pons opening tax: every curve charges buys snipeTaxStartBps=9900 decaying over snipeTaxSeconds=3. Read off
# currentSnipeTaxBps at the blocks after four launches, it steps by whole block seconds since launchedAt:
# 9900 in the launch second, 618 one second later, 19 after two, 0 from the third. No strategy fills above the ceiling.
OPENING_TAX_BPS = (9900, 618, 19)
MAX_OPENING_TAX_BPS = 300
RECEIPT_WAIT_S = 120                     # a live transaction not mined by then is treated as dropped
EXIT_SLIPPAGE = (0.05, 0.15, 0.40, 0.99)
CHAIN_QUOTE_EVERY_S = 2                  # a live curve position is priced by the curve itself this often
CHAIN_QUOTE_MAX_AGE_S = 6                # older than this, the tape formula prices it again  # per attempt: a live exit widens until the venue takes it


def opening_tax_bps(t, now: float) -> int:
    s = int(now) - int(t.ts)
    return OPENING_TAX_BPS[s] if 0 <= s < len(OPENING_TAX_BPS) else 0
RISK = {"max_live_size": float(os.environ.get("LIVE_MAX_SIZE", "0.01")), "max_live_open": int(os.environ.get("LIVE_MAX_OPEN", "3")),
        "daily_loss_stop": float(os.environ.get("LIVE_DAILY_STOP", "0.05"))}


RISK_PATH = os.path.join(os.path.dirname(__file__), "risk.json")
LIVE_POS_PATH = os.path.join(os.path.dirname(__file__), "live_positions.json")


def risk() -> dict:
    """Limits, hot-reloaded from risk.json on every check (no restart needed); env values are the fallback."""
    try:
        RISK.update({k: type(RISK[k])(v) for k, v in json.load(open(RISK_PATH, encoding="utf-8")).items() if k in RISK})
    except Exception:
        pass
    return RISK


def live_allowed() -> tuple[bool, str]:
    if os.path.exists(KILL): return False, "KILL file present"
    if not os.environ.get("RH_PRIVATE_KEY"): return False, "RH_PRIVATE_KEY not set"
    return True, ""

# Every rule a strategy can carry, in its neutral ("off") position. normalize() keeps only these keys and fills missing
# ones from here, so a new rule must be added here first (the trap of 13.09: unknown keys were silently dropped).
# Off means: 0 for max_buyers / max_smart / max_creator_tax_bps, -1 for max_exempt / max_farm_twins, False for flags.
BASE = {
    "id": "", "name": "", "enabled": True, "size": 0.05, "cash": 2.0, "quote": "any",
    "entry": {"min_age_s": 0, "max_age_s": 1800, "min_buyers": 1, "max_buyers": 0, "min_net": 0.0, "max_net": 999.0,
              "min_progress": 0, "max_progress": 100, "max_snipe_pct": 100.0, "max_creator_pct": 100.0,
              "creator_max_prior": 999, "creator_min_grads": 0, "creator_no_dumps": False, "require_rising": False,
              "min_buy_sell_ratio": 0.0, "min_vel": 0.0, "min_smart": 0, "max_smart": 0,
              "max_creator_tax_bps": 0, "max_exempt": -1, "require_socials": False, "no_third_party": False,
              "max_farm_twins": -1},
    "exit": {"tp_pct": 10000, "sl_pct": 99, "timeout_s": 1800, "on_graduation": True, "on_creator_sell": False,
             "flow_reversal_pct": 100, "trail_pct": 100, "partial_pct": 0, "pre_grad_pct": 0},
}


# Strategies are data, never code : the running set is strategies.json; when it is missing the engine
# starts from strategies.default.json (the house set a first visitor sees). Ids, names and rules all live in those files.
DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "strategies.default.json")


class Book:
    def __init__(self, cash):
        self.start = cash; self.cash = cash
        self.positions = {}          # token -> position
        self.closed = deque(maxlen=300)
        self.decisions = deque(maxlen=300)
        self.rejected = set(); self.done = set()

    def view(self, tokens, now, engine=None):
        open_val = sum((engine.value(p, tokens[p["token"]]) if engine else p["tokens"] * (tokens[p["token"]].last_px or 0) * (1 - FEE))
                       for p in self.positions.values() if p["token"] in tokens)          # a restored position may precede its token's registration
        # Every closed trade, not just the 300 the deque still holds. `closed` is a display window (maxlen=300), so
        # summing it silently dropped a book's oldest trades once it passed 300 — early and smart were reporting
        # -46% against a real -74% and -93%. Cash carries them all (store.py persists it verbatim) and an open
        # position is holding `size - realized` of it out, which is store.py's own formula read backwards.
        pnl = self.cash - self.start + sum(p["size"] - p.get("realized", 0.0) for p in self.positions.values())
        return {"cash": round(self.cash, 4), "equity": round(self.cash + open_val, 4), "start": self.start,
                "open": len(self.positions), "closed": len(self.closed), "wins": sum(1 for p in self.closed if p["pnl"] > 0),
                "pnl": round(pnl, 4), "pnl_pct": round(100 * pnl / self.start, 2) if self.start else 0,
                "best": round(max((p["pnl"] for p in self.closed), default=0), 4), "worst": round(min((p["pnl"] for p in self.closed), default=0), 4)}


class Engine:
    def __init__(self):
        self.strats: list[dict] = []
        self.books: dict[str, Book] = {}
        self.latency = float(os.environ.get("PAPER_LATENCY_S", "1.0"))   # signal -> fill, same as the simulator
        self.pending: dict[tuple, dict] = {}                              # (strategy, token) -> queued entry
        self.reserve_hook = None                                          # set by the indexer: refresh a token's reserve from chain
        self.farm_hook = None                                             # set by the indexer: launch-farm twins of a token (30 min)
        self.dropped: list[str] = []                                      # strategies reset or deleted: their stored paper rows go (store.py)
        self.load()

    # ---- persistence ------------------------------------------------------------------------
    def load(self):
        if os.path.exists(PATH):
            self.strats = json.load(open(PATH, encoding="utf-8"))
        else:
            self.strats = [normalize(s) for s in (json.load(open(DEFAULT_PATH, encoding="utf-8")) if os.path.exists(DEFAULT_PATH) else [])]; self.save()
        for s in self.strats:
            self.books.setdefault(s["id"], Book(s.get("cash", 2.0)))
        self.restore_live()

    # ---- live positions survive restarts ----------------------------------------------------
    def persist_live(self):
        # serialise first, then swap the file in whole: an unclosed handle left the file one state behind the book (16.09.2026)
        try:
            text = json.dumps([dict(p, strategy=sid) for sid, b in list(self.books.items()) for p in list(b.positions.values()) if p.get("live")], indent=1)
            with open(LIVE_POS_PATH + ".tmp", "w", encoding="utf-8") as f: f.write(text)
            os.replace(LIVE_POS_PATH + ".tmp", LIVE_POS_PATH)
        except Exception:
            pass

    def restore_live(self):
        """Re-attach live positions saved by a previous process; the indexer re-registers their tokens on demand."""
        self.pending_restore = []
        try:
            rows = json.load(open(LIVE_POS_PATH, encoding="utf-8"))
        except Exception:
            return
        for p in rows:
            sid = p.get("strategy")
            if sid in self.books:
                self.books[sid].positions[p["token"]] = p; self.pending_restore.append(p["token"])

    def save(self):
        """strategies.json holds the house set only; users' own strategies live in Postgres (accounts.py)."""
        json.dump([s for s in self.strats if not s.get("owner")], open(PATH, "w", encoding="utf-8"), indent=1)

    def upsert(self, s: dict, owner: str | None = None) -> dict:
        """Add or replace a strategy. `owner` tags a strategy with who made it; a self-hosted engine leaves it None."""
        s = normalize(s)
        if owner: s["owner"] = owner
        for i, old in enumerate(self.strats):
            if old["id"] == s["id"]:
                self.strats[i] = s
                if not owner: self.save()
                return s
        self.strats.append(s); self.books[s["id"]] = Book(s["cash"])
        if not owner: self.save()
        return s

    def delete(self, sid: str):
        self.strats = [s for s in self.strats if s["id"] != sid]; self.books.pop(sid, None); self.save(); self.dropped.append(sid)

    def reset(self, sid: str):
        s = next((x for x in self.strats if x["id"] == sid), None)
        if s: self.books[sid] = Book(s["cash"]); self.dropped.append(sid)

    # ---- evaluation -------------------------------------------------------------------------
    def evaluate(self, t, creator: dict, now: int, db=None):
        """Run every enabled strategy against one token that just traded."""
        for s in self.strats:
            if not s.get("enabled"): continue
            book = self.books[s["id"]]
            if t.token in book.positions or t.token in book.done: continue      # one trade per token per strategy
            if s.get("quote", "ETH") == "ETH" and not t.eth_quoted: continue
            if (s["id"], t.token) in self.pending: continue
            ok, why = self.matches(s, t, creator, now)
            if ok:
                self.pending[(s["id"], t.token)] = {"ts": now, "why": why}
            # a "no" is not logged: one row per strategy × launch is millions an hour once users run thousands
            # of strategies; the coin card shows every strategy's live checklist instead. Buys and sells are still logged.

    def matches(self, s, t, c, now):
        e = s["entry"]; age = now - t.ts
        buyers = len(t.buyers)
        # activity gate: below it the token is simply not a candidate yet, no reason logged
        if buyers < e["min_buyers"] or age < e["min_age_s"]: return False, None
        if age > e["max_age_s"]: return False, None
        if t.graduated_block: return False, None
        snipe = 100 * t.snipe_tokens / 1e9; cre = 100 * t.creator_tokens / 1e9
        prior = c["launches"] - 1; grads = c["graduated"] - (1 if t.graduated_block else 0)
        prog = t.progress
        net = t.net_eq
        if net < e["min_net"]: return False, f"net {net:.2f} < {e['min_net']}"
        if net > e["max_net"]: return False, f"net {net:.2f} > {e['max_net']}"
        if prog < e["min_progress"]: return False, f"progress {prog:.0f}% < {e['min_progress']}%"
        if prog > e["max_progress"]: return False, f"progress {prog:.0f}% > {e['max_progress']}%"
        if snipe > e["max_snipe_pct"]: return False, f"launch-block snipers hold {snipe:.0f}%"
        if cre > e["max_creator_pct"]: return False, f"creator self-bought {cre:.0f}% in block one"
        if prior > e["creator_max_prior"]: return False, f"creator has {prior} prior launches"
        if grads < e["creator_min_grads"]: return False, f"creator graduated {grads} of {prior} prior launches"
        if e["creator_no_dumps"] and c["dumps"] > 0: return False, f"creator dumped {c['dumps']} of its own launches"
        if t.sells and t.buys / t.sells < e["min_buy_sell_ratio"]: return False, f"buy/sell ratio {t.buys / t.sells:.1f} < {e['min_buy_sell_ratio']}"
        if e.get("min_smart", 0) and len(t.smart) < e["min_smart"]: return False, f"smart buyers {len(t.smart)} < {e['min_smart']}"
        if e.get("max_smart") and len(t.smart) > e["max_smart"]: return False, f"smart buyers {len(t.smart)} > {e['max_smart']} (crowded)"
        if e.get("max_buyers") and buyers > e["max_buyers"]: return False, f"buyers {buyers} > {e['max_buyers']} (crowded)"
        if e.get("min_vel", 0) and buyers / max(age / 60.0, 0.5) < e["min_vel"]: return False, f"buyers/min {buyers / max(age / 60.0, 0.5):.1f} < {e['min_vel']}"
        if e.get("max_creator_tax_bps") and t.creator_tax_bps is not None and t.creator_tax_bps > e["max_creator_tax_bps"]: return False, f"creator tax {t.creator_tax_bps / 100:.1f}% > {e['max_creator_tax_bps'] / 100:.1f}%"
        if e.get("max_exempt", -1) >= 0 and t.exempt_n is not None and t.exempt_n > e["max_exempt"]: return False, f"{t.exempt_n} wallets exempt from the opening tax (declared bundle) > {e['max_exempt']}"
        if e.get("require_socials") and t.meta and not t.has_socials: return False, "no socials declared at launch"
        if e.get("no_third_party") and t.third_party: return False, "creator fees routed to a third party"
        if e.get("max_farm_twins", -1) >= 0 and self.farm_hook:
            tw = self.farm_hook(t)
            if tw > e["max_farm_twins"]: return False, f"launch farm: {tw} twins in 30 min > {e['max_farm_twins']}"
        if e["require_rising"] and not rising(t): return False, "flow not rising"
        return True, (f"{buyers} buyers · net {net:.2f} ({prog:.0f}%) · snipe {snipe:.0f}% · creator {grads}/{prior} graduated"
                      + (f" · {c['dumps']} dumps" if c["dumps"] else ""))

    def checklist(self, s, t, c, now) -> dict:
        """Every entry condition with its value and verdict — for the radar, not just the first failure."""
        e = s["entry"]; age = now - t.ts; buyers = len(t.buyers); smart = len(t.smart)
        snipe = 100 * t.snipe_tokens / 1e9; prior = max(c["launches"] - 1, 0); prog = t.progress
        vel = buyers / max(age / 60.0, 0.5); ris = rising(t)
        too_many = bool(e.get("max_buyers")) and buyers > e["max_buyers"]; crowded = bool(e.get("max_smart")) and smart > e["max_smart"]
        items = [
            {"k": "age", "ok": e["min_age_s"] <= age <= e["max_age_s"], "v": f"{int(age)}s", "want": f"{e['min_age_s']}–{e['max_age_s']}s"},
            {"k": "buyers", "ok": buyers >= e["min_buyers"] and not too_many, "v": str(buyers), "want": f"{e['min_buyers']}–{e['max_buyers'] or '∞'}" if e.get("max_buyers") else f"≥{e['min_buyers']}"},
            {"k": "curve", "ok": e["min_progress"] <= prog <= e["max_progress"], "v": f"{prog:.0f}%", "want": f"≤{e['max_progress']}%"},
            {"k": "snipe", "ok": snipe <= e["max_snipe_pct"], "v": f"{snipe:.1f}%", "want": f"≤{e['max_snipe_pct']}%"},
            {"k": "creator", "ok": prior <= e["creator_max_prior"] and not (e["creator_no_dumps"] and c["dumps"] > 0),
             "v": ("fresh" if prior == 0 else f"{prior} prior") + (f" · {c['dumps']} dumps" if c["dumps"] else ""), "want": "fresh" if e["creator_max_prior"] == 0 else "no dumps"},
            {"k": "smart", "ok": smart >= e.get("min_smart", 0) and not crowded, "v": str(smart), "want": f"{e.get('min_smart', 0)}–{e['max_smart']}" if e.get("max_smart") else f"≥{e.get('min_smart', 0)}"},
            {"k": "velocity", "ok": not e.get("min_vel") or vel >= e["min_vel"], "v": f"{vel:.1f}/min", "want": f"≥{e.get('min_vel', 0)}/min"},
            {"k": "flow", "ok": not e["require_rising"] or ris, "v": "rising" if ris else "flat", "want": "rising"},
        ]
        if t.net_eq < e["min_net"]: items.append({"k": "net", "ok": False, "v": f"{t.net_eq:.2f}", "want": f"≥{e['min_net']}"})
        # launch-record rules: unknown (not read yet) counts as ok so a fresh launch is not refused for a missing read
        if e.get("max_creator_tax_bps"):
            tax = t.creator_tax_bps
            items.append({"k": "tax", "ok": tax is None or tax <= e["max_creator_tax_bps"], "v": "?" if tax is None else f"{tax / 100:.1f}%", "want": f"≤{e['max_creator_tax_bps'] / 100:.1f}%"})
        if e.get("max_exempt", -1) >= 0:
            ex = t.exempt_n
            items.append({"k": "bundle", "ok": ex is None or ex <= e["max_exempt"], "v": "?" if ex is None else f"{ex} exempt", "want": f"≤{e['max_exempt']}"})
        if e.get("require_socials"):
            items.append({"k": "socials", "ok": (not t.meta and not t.named) or t.has_socials, "v": "yes" if t.has_socials else "none", "want": "x / site / tg"})
        if e.get("no_third_party"):
            items.append({"k": "fees", "ok": not t.third_party, "v": "third party" if t.third_party else "creator", "want": "to creator"})
        if e.get("max_farm_twins", -1) >= 0 and self.farm_hook:
            tw = self.farm_hook(t)
            items.append({"k": "farm", "ok": tw <= e["max_farm_twins"], "v": f"{tw} twins", "want": f"≤{e['max_farm_twins']}"})
        if s.get("quote", "ETH") == "ETH" and not t.eth_quoted:          # evaluate() never trades it: say so first
            items.insert(0, {"k": "pair", "ok": False, "v": t.quote_sym, "want": "ETH"})
        failed = [i["k"] for i in items if not i["ok"]]
        # stage: "waiting" if time alone can still fix every failed rule (too young, too few buyers yet, no smart buyer yet,
        # flow not rising yet); "no" once one can't change any more (pair, snipe, creator, tax, bundle, crowded, window closed)
        fixed = {"pair", "snipe", "creator", "tax", "bundle", "fees", "socials", "farm"}
        blocked = [k for k in failed if k in fixed or (k == "age" and age > e["max_age_s"]) or (k == "buyers" and too_many)
                   or (k == "smart" and crowded) or (k == "curve" and prog > e["max_progress"])]
        stage = "ready" if not failed else "no" if blocked else "waiting"
        words = {"snipe": "bundled launch", "creator": "creator has history", "curve": "curve too far along", "flow": "flow not rising",
                 "velocity": "too slow", "net": "net outflow", "tax": "creator tax too high", "bundle": "declared bundle", "socials": "no socials", "fees": "fees go to a third party",
                 "pair": "pair not traded by this strategy", "farm": "launch farm"}
        if not failed: verdict = "ready"
        elif failed[0] == "age": verdict = "too young" if age < e["min_age_s"] else "window closed"
        elif failed[0] == "buyers": verdict = "too crowded" if too_many else "too few buyers"
        elif failed[0] == "smart": verdict = "too crowded" if crowded else ("waiting for a smart buyer" if smart == 0 else "needs another smart buyer")
        else: verdict = words.get(failed[0], failed[0])
        return {"items": items, "failed": failed, "verdict": verdict, "ready": not failed, "stage": stage, "blocked": blocked}

    def live_ok(self, s, book) -> tuple[bool, str]:
        ok, why = live_allowed()
        if not ok: return False, why
        risk()
        if s["size"] > RISK["max_live_size"]: return False, f"size {s['size']} > LIVE_MAX_SIZE {RISK['max_live_size']}"
        live_open = sum(1 for b in self.books.values() for p in b.positions.values() if p.get("live"))
        if live_open >= RISK["max_live_open"]: return False, f"{live_open} live positions open (max {RISK['max_live_open']})"
        day = time.strftime("%Y-%m-%d")
        lost = -sum(p["pnl"] for b in self.books.values() for p in b.closed
                    if p.get("live") and p["pnl"] < 0 and time.strftime("%Y-%m-%d", time.localtime(p["close_ts"])) == day)
        if lost >= RISK["daily_loss_stop"]: return False, f"daily loss {lost:.4f} >= stop {RISK['daily_loss_stop']}"
        return True, ""

    def open(self, s, book, t, now, why, db):
        size = s["size"]
        if t.last_px is None or book.cash < size: return
        live = s.get("mode") == "live"; txh = None
        if live:
            ok, reason = self.live_ok(s, book)
            if not ok:
                book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": t.token, "sym": t.symbol, "action": "NO", "reason": f"live blocked: {reason}"}); return
            try:
                import exec_pons
                txh = exec_pons.send_buy(t.curve, size, reserve_eth=max(t.net_eq, 0.0), pair=None if t.eth_quoted else t.quote, eq=t.eq)
            except Exception as e:
                book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": t.token, "sym": t.symbol, "action": "NO", "reason": f"live send failed: {str(e)[:80]}"}); return
        # every pair trades on the same curve shape: fill in ETH-equivalent at the pair's scaled reserve
        r0 = max(t.net_eq, 0.0)
        tax = opening_tax_bps(t, now) / 1e4                              # what is left of the opening tax (≤ the ceiling)
        tax += max(getattr(t, "fee_rate", 0.02) - FEE, 0.0)                # the curve's own fee above the 1% baseline
        toks, r_after = curve.buy(r0, size, tax); px = size / toks; e_net = r_after - r0
        book.positions[t.token] = {"token": t.token, "sym": t.symbol, "strategy": s["id"], "open_ts": now, "entry_px": px, "size": size,
                                   "tokens": toks, "e_net": e_net, "curve": True, "eq": t.eq, "peak_net": t.net_eq, "why": why, "quote": t.quote_sym,
                                   "live": live, "tx_in": txh, "confirmed": not live, "curve_addr": t.curve}
        book.cash -= size
        if live: self.persist_live()
        d = {"ts": now, "strategy": s["id"], "token": t.token, "sym": t.symbol, "action": "BUY", "reason": why}
        book.decisions.appendleft(d)
        if db: db.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (now, t.token, s["id"], "BUY", why))
        return d

    def value(self, p: dict, t) -> float:
        """What selling the position returns now, ETH-equivalent: the curve formula while the curve trades; after graduation
        the v4 Quoter's answer for the held amount (indexer mark_pools, at most 30 s old), else the last curve price with a
        5 % impact/fee haircut until the first mark lands."""
        if not t.graduated_block:
            if p.get("live") and time.time() - p.get("chain_ts", 0) <= CHAIN_QUOTE_MAX_AGE_S: return p["chain_value"]
            return curve.sell(max(t.net_eq, 0.0) + p.get("e_net", 0.0), p["tokens"], getattr(t, "fee_rate", 0.02))[0]
        if pool_fresh(p): return p["pool_value"]
        return p["tokens"] * (t.last_px or 0) * t.eq * 0.95

    def opening_tax_bps(self, s, t, now: float) -> int:
        """A live strategy asks the curve itself (currentSnipeTaxBps for our address); paper, or a failed read, uses the clock."""
        if s.get("mode") == "live" and int(now) - int(t.ts) < 5:
            try:
                import exec_pons
                return exec_pons.opening_tax_bps(t.curve)
            except Exception:
                pass
        return opening_tax_bps(t, now)

    def fill_pending(self, tokens: dict, now: float, db=None) -> list[dict]:
        """Entries whose latency has elapsed fill now, at the reserve the curve has now."""
        opened = []
        for key, q in list(self.pending.items()):
            if now - q["ts"] < self.latency: continue
            sid, tok = key
            s = next((x for x in self.strats if x["id"] == sid), None); t = tokens.get(tok)
            if s and t and self.opening_tax_bps(s, t, now) > MAX_OPENING_TAX_BPS: continue    # still inside the opening tax: wait
            del self.pending[key]
            if not s or not t or t.graduated_block or tok in self.books[sid].positions or tok in self.books[sid].done: continue
            if self.reserve_hook and (s.get("mode") == "live" or getattr(t, "partial", False)):
                try: self.reserve_hook(t)                     # true reserve from the curve's own quote before we price the fill
                except Exception: pass
            d = self.open(s, self.books[sid], t, int(now), q["why"], db)
            if d: opened.append(self.books[sid].positions[tok])
        return opened

    def manage(self, tokens: dict, now: int, db=None) -> list[dict]:
        """Exit checks on every open position of every strategy; an exit fills `latency` seconds after its trigger."""
        out = self.check_live(tokens, now, db)
        for s in self.strats:
            book = self.books.get(s["id"])
            if not book: continue
            x = s["exit"]
            for tok, p in list(book.positions.items()):
                t = tokens.get(tok)
                if t is None or t.last_px is None: continue          # restored position whose token is not registered yet
                p["peak_net"] = max(p["peak_net"], t.net_eq)
                if p.get("live") and p.get("confirmed") and not t.graduated_block: self._chain_quote(p, t)
                value = self.value(p, t); pnl = value - p["size"]; pct = 100 * pnl / p["size"]
                p["peak_pct"] = max(p.get("peak_pct", 0.0), pct)
                # partial take: realise half at +partial_pct once, the rest follows the normal exits
                if p.get("live") and not p.get("confirmed"): continue     # the buy is not mined yet: check_live settles it
                if p.get("tx_half") and not p.get("half_done"): continue   # the half-sell is in flight: wait for its receipt
                if x.get("partial_pct", 0) and not p.get("half_done") and pct >= x["partial_pct"] and now >= p.get("half_retry_at", 0):
                    half = value / 2
                    if p.get("live"):                                   # real position: the half is booked when its sell is mined
                        try:
                            p["tx_half"] = self._sell(t, p["tokens"] / 2, EXIT_SLIPPAGE[0]); p["tx_half_ts"] = now; p["half_value"] = half
                            p["half_pct"] = pct; self.persist_live()
                            book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": tok, "sym": t.symbol, "action": "NO", "reason": f"live half-sell sent · tx {p['tx_half'][:10]}"})
                        except Exception as e:
                            p["half_retry_at"] = now + 5
                            book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": tok, "sym": t.symbol, "action": "NO", "reason": f"live half-sell failed, retry: {str(e)[:80]}"})
                        continue
                    self._book_half(s, book, p, tok, t.symbol, half, pct, now, db)
                    value = self.value(p, t); pnl = value + p["realized"] - p["size"]; pct = 100 * pnl / p["size"]
                elif p.get("half_done"):
                    pnl = value + p["realized"] - p["size"]; pct = 100 * pnl / p["size"]
                reason = None
                if x.get("pre_grad_pct", 0) and not t.graduated_block and t.progress >= x["pre_grad_pct"]: reason = f"pre-graduation sell at {t.progress:.0f}%"
                elif x["on_graduation"] and t.graduated_block: reason = "graduated: take"
                elif pct >= x["tp_pct"]: reason = f"take profit +{pct:.0f}%"
                elif pct <= -x["sl_pct"]: reason = f"stop loss {pct:.0f}%"
                elif x.get("trail_pct", 100) < 100 and p["peak_pct"] > 20 and pct < p["peak_pct"] - x["trail_pct"] / 100 * (100 + p["peak_pct"]):
                    reason = f"trailing: {pct:+.0f}% from peak {p['peak_pct']:+.0f}%"
                elif x["on_creator_sell"] and t.creator_sold_eth > 0: reason = "creator started selling"
                elif x["flow_reversal_pct"] < 100 and now - p["open_ts"] > 20 and t.net_eq < (1 - x["flow_reversal_pct"] / 100) * p["peak_net"]:
                    reason = f"flow reversed: net {t.net_eq:.2f} vs peak {p['peak_net']:.2f}"
                elif now - p["open_ts"] > x["timeout_s"]: reason = f"timeout {x['timeout_s'] // 60} min"
                if "exit_at" in p:
                    if now < p["exit_at"]: continue                   # our sell is in flight; fill at whatever the curve has now
                    # a graduated exit fills at a real pool quote: give the first mark up to 30 s to land
                    if t.graduated_block and not pool_fresh(p) and now - p.setdefault("grad_seen", now) < 30: continue
                    reason = p["exit_reason"]
                elif reason:
                    p["exit_at"] = now + self.latency; p["exit_reason"] = reason
                    if t.graduated_block: p.setdefault("grad_seen", now)          # the 30 s wait for a pool quote runs from the trigger
                    continue
                else:
                    continue
                if p.get("live"):
                    if p.get("tx_out"): continue                            # the sell is in flight: check_live closes it on its receipt
                    venue = "pool" if t.graduated_block else "curve"
                    if p.get("sell_venue") != venue: p["sell_venue"] = venue; p["sell_tries"] = 0     # a new venue starts from the tight slippage again
                    tries = p.get("sell_tries", 0)
                    slip = EXIT_SLIPPAGE[min(tries, len(EXIT_SLIPPAGE) - 1)]   # an exit is an exit: after failed attempts sell at whatever the venue pays
                    try:
                        p["tx_out"] = self._sell(t, p["tokens"] * (1 + 1e-9), slip); p["tx_out_ts"] = now   # a hair over: the sender caps at the exact balance, no dust
                        if venue == "pool": reason += " · sold in the v4 pool"
                        if tries: reason += f" · slippage {int(slip * 100)}%"
                        p["sold_reason"] = reason; p["exit_value"] = value; self.persist_live()
                        book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": tok, "sym": t.symbol, "action": "NO", "reason": f"live sell sent · {reason} · tx {p['tx_out'][:10]}"})
                        continue
                    except Exception as e:
                        if "no token balance" in str(e).lower():          # sold outside the bot (manually): close the book entry, P&L unknown
                            p["tx_out"] = "external"; value = 0.0; pnl = 0.0; pct = 0.0; reason = "closed outside the bot · P&L unknown"
                        elif "pool not open" in str(e):                   # swept: curve closed, pool not created yet; wait, keep the slippage tight
                            p["exit_at"] = now + 10
                            book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": tok, "sym": t.symbol, "action": "NO", "reason": f"live sell waiting: {str(e)[:80]}"}); continue
                        else:
                            p["sell_tries"] = tries + 1; p["exit_at"] = now + 12
                            book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": tok, "sym": t.symbol, "action": "NO", "reason": f"live sell failed, retry: {str(e)[:160]}"}); continue
                d = self._close(s, book, p, tok, t.symbol, t.last_px, value, pnl, pct, reason, now, db)
                out.append(p)
        return out

    # ---- live transactions settle on their receipts ----------------------------------------------
    def check_live(self, tokens: dict, now: int, db=None) -> list[dict]:
        """Nothing live is booked on a transaction hash alone (the first live run, 16.09.2026, booked a reverted sell as a
        close and kept a reverted buy as a position). A buy counts once it is mined with status 1, at the tokens its Transfer
        logs delivered; a reverted or dropped buy leaves the book and its cash comes back. A sell closes the position once
        mined; a reverted or dropped one goes back to the exit queue with the next, wider slippage."""
        closed = []
        if not any(p.get("live") for b in self.books.values() for p in b.positions.values()): return closed
        import exec_pons
        for s in self.strats:
            book = self.books.get(s["id"])
            if not book: continue
            for tok, p in list(book.positions.items()):
                if not p.get("live"): continue
                try:
                    c = self._settle(s, book, tok, p, tokens.get(tok), now, db, exec_pons)
                except Exception as e:                                # an RPC hiccup: try again next tick, never guess
                    c = None; p["check_errors"] = p.get("check_errors", 0) + 1
                    if p["check_errors"] % 30 == 1:
                        book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": tok, "sym": p.get("sym", tok[:8]), "action": "NO", "reason": f"live receipt check failed, retrying: {str(e)[:80]}"})
                if c: closed.append(c)
        return closed

    def _settle(self, s, book, tok, p, t, now, db, exec_pons) -> dict | None:
        """One live position against the chain; returns the position when its sell was mined and it closed."""
        sym = p.get("sym") or (t.symbol if t else tok[:8])
        note = lambda why: book.decisions.appendleft({"ts": now, "strategy": s["id"], "token": tok, "sym": sym, "action": "NO", "reason": why})
        if not p.get("confirmed") and p.get("tx_in"):
            r = exec_pons.receipt(p["tx_in"])
            if r is None and now - p["open_ts"] <= RECEIPT_WAIT_S: return None
            got = exec_pons.tokens_received(r, tok, exec_pons._account().address) if r is not None and r["status"] == 1 else 0.0
            if got <= 0:
                why = "not mined in time" if r is None else ("reverted" if r["status"] != 1 else "mined, but no tokens arrived")
                book.cash += p["size"]; del book.positions[tok]; book.done.add(tok); self.persist_live()
                note(f"live buy {why} · {p['size']:g} back to cash · tx {p['tx_in'][:10]}"); return None
            p["tokens"] = got; p["entry_px"] = p["size"] / got; p["confirmed"] = True; self.persist_live()
            import threading                                              # approve the curve now so the exit does not wait for it
            threading.Thread(target=self._approve, args=(tok, p), daemon=True).start()
        if p.get("tx_half") and not p.get("half_done"):
            r = exec_pons.receipt(p["tx_half"])
            if r is not None and r["status"] == 1:
                self._book_half(s, book, p, tok, sym, p.pop("half_value"), p.get("half_pct", 0.0), now, db)
            elif r is not None or now - p["tx_half_ts"] > RECEIPT_WAIT_S:
                note(f"live half-sell {'reverted' if r is not None else 'not mined in time'}, retry · tx {p['tx_half'][:10]}")
                p.pop("tx_half"); p.pop("half_value", None); p["half_retry_at"] = now + 5; self.persist_live()
        if p.get("tx_out") and p["tx_out"] != "external":
            r = exec_pons.receipt(p["tx_out"])
            if r is None and now - p["tx_out_ts"] <= RECEIPT_WAIT_S: return None
            if r is not None and r["status"] == 1:
                value = p.get("fill_out", p.get("exit_value", 0.0))
                pnl = value + p.get("realized", 0.0) - p["size"]; pct = 100 * pnl / p["size"]
                if "fill_out" in p: p["confirmed_out"] = True
                self._close(s, book, p, tok, sym, t.last_px if t else None, value, pnl, pct, p.get("sold_reason", p.get("exit_reason", "sold")), now, db)
                return p
            tries = p.get("sell_tries", 0) + 1
            note(f"live sell {'reverted' if r is not None else 'not mined in time'} · retry at {int(EXIT_SLIPPAGE[min(tries, len(EXIT_SLIPPAGE) - 1)] * 100)}% slippage · tx {p['tx_out'][:10]}")
            for k in ("tx_out", "tx_out_ts", "exit_value", "fill_out", "sold_reason"): p.pop(k, None)
            p["sell_tries"] = tries; p["exit_at"] = now; self.persist_live()
        return None

    def _chain_quote(self, p: dict, t):
        """The curve's own answer for what the position sells for (sell() in eth_call, creator tax and fee included).
        The tape formula drifted on fast curves in the second live run (16.09.2026): a take profit fired at +154% on a
        position that sold at -4%, and a stop at -24% sold at -44%. Needs the approval, so it starts a few seconds after
        the buy; until then, and whenever the call fails, the formula prices the position."""
        if time.time() - p.get("chain_try", 0) < CHAIN_QUOTE_EVERY_S: return
        p["chain_try"] = time.time()
        try:
            import exec_pons
            raw = exec_pons.quote_sell_onchain(t.curve, int(p["tokens"] * 1e18), exec_pons._account().address)
            p["chain_value"] = raw / 1e18 * p.get("eq", 1.0); p["chain_ts"] = time.time()
        except Exception:
            pass

    def _book_half(self, s, book, p, tok, sym, half, pct, now, db):
        book.cash += half; p["realized"] = half; p["tokens"] /= 2; p["half_done"] = True
        if p.get("live"): self.persist_live()
        d = {"ts": now, "strategy": s["id"], "token": tok, "sym": sym, "action": "SELL",
             "reason": f"half at +{pct:.0f}% · {half - p['size'] / 2:+.4f}" + (f" · tx {p['tx_half'][:10]}" if p.get("tx_half") else "")}
        book.decisions.appendleft(d)
        if db: db.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (now, tok, s["id"], "SELL", d["reason"]))

    def _close(self, s, book, p, tok, sym, exit_px, value, pnl, pct, reason, now, db):
        p.update({"close_ts": now, "exit_px": exit_px, "pnl": round(pnl, 4), "pnl_pct": round(pct, 1), "close_reason": reason, "value_booked": value})
        book.cash += value; book.closed.appendleft(p); del book.positions[tok]; book.done.add(tok)
        if p.get("live"): self.persist_live()
        d = {"ts": now, "strategy": s["id"], "token": tok, "sym": sym, "action": "SELL", "reason": f"{reason} · {pnl:+.4f} ({pct:+.0f}%)"}
        book.decisions.appendleft(d)
        if db:
            db.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (now, tok, s["id"], "SELL", d["reason"]))
            db.execute("INSERT INTO paper VALUES(?,?,?,?,?,?,?,?,?)", (tok, s["id"], p["open_ts"], now, p["entry_px"], exit_px, p["size"], pnl, reason))
        return d

    def _sell(self, t, tokens: float, slippage: float) -> str:
        """One live sell on whichever venue is open: the curve while it trades, the Uniswap v4 pool after graduation."""
        if t.graduated_block:
            import pons_pool
            if not t.pool or t.pool.get("phase") != 2: t.pool = {**pons_pool.record(t.token), "at": time.time()}
            return pons_pool.send_sell(t.token, tokens, slippage, rec=t.pool)
        import exec_pons
        return exec_pons.send_sell(t.curve, tokens, reserve_eth=max(t.net_eq, 0.0), slippage=slippage, token=t.token,
                                   pair=None if t.eth_quoted else t.quote, eq=t.eq)

    def _approve(self, tok: str, p: dict):
        try:
            import exec_pons
            p["tx_approve"] = exec_pons.ensure_allowance(tok, p["curve_addr"], int(p["tokens"] * 1e18)) or "already"
        except Exception as e:
            p["tx_approve"] = f"failed: {str(e)[:60]}"

    def on_own_fill(self, tok: str, d: dict):
        """A CurveSell by our wallet arrived: the real proceeds replace the quote the exit was sent on. Buys settle on their
        receipt (check_live); the event is not needed for them."""
        if d["kind"] != "sell": return
        txh = "0x" + str(d.get("tx", "")).lower().removeprefix("0x")
        eth = d["eth"]
        for b in self.books.values():
            p = b.positions.get(tok)
            if p and p.get("live") and p.get("tx_out") and p["tx_out"].lower() == txh:
                p["fill_out"] = eth * p.get("eq", 1.0)                # still waiting for the receipt: close at the real proceeds
            for c in b.closed:
                if c["token"] == tok and c.get("live") and c.get("tx_out", "").lower() == txh and not c.get("confirmed_out"):
                    real = eth * c.get("eq", 1.0)
                    b.cash += real - c.get("value_booked", real)
                    c["pnl"] = round(real + c.get("realized", 0.0) - c["size"], 4); c["pnl_pct"] = round(100 * (real + c.get("realized", 0.0) - c["size"]) / c["size"], 1)
                    c["value_booked"] = real; c["confirmed_out"] = True

    def view(self, tokens, now, owner: str | None = None):
        """Strategies with their books, for one owner only: the house set (None) or a user's own."""
        rows = []
        for s in self.strats:
            if s.get("owner") != owner: continue
            b = self.books[s["id"]]
            rows.append({**s, "book": b.view(tokens, now, self), "live_ok": self.live_ok(s, b) if s.get("mode") == "live" else (False, "paper"),
                         "positions": [{**p, "cur_px": tokens[p["token"]].last_px if p["token"] in tokens else None, "age_s": now - p["open_ts"],
                                        "pnl": round(self.value(p, tokens[p["token"]]) + p.get("realized", 0.0) - p["size"], 4) if p["token"] in tokens else None,
                                        "venue": "pool" if getattr(tokens.get(p["token"]), "graduated_block", None) else "curve",
                                        "image": getattr(tokens.get(p["token"]), "image", None), "x_url": getattr(tokens.get(p["token"]), "x_url", None),
                                        # a position opened before the coin's name was read takes the ticker the coin has now
                                        "sym": p.get("sym") or getattr(tokens.get(p["token"]), "symbol", "") or ""} for p in b.positions.values()],
                         "closed": [{**c, "sym": c.get("sym") or getattr(tokens.get(c["token"]), "symbol", "") or ""} for c in list(b.closed)[:30]],
                         "decisions": list(b.decisions)[:40]})
        return rows


def pool_fresh(p: dict) -> bool:
    """The position carries a v4 pool quote from the last 30 s."""
    return bool(p.get("pool_ts")) and time.time() - p["pool_ts"] < 30


def rising(t) -> bool:
    h = list(t.net_hist)
    if len(h) < 4: return False
    return h[-1][1] > h[-4][1] and h[-1][1] >= 0.9 * t.peak_net


def normalize(s: dict) -> dict:
    base = json.loads(json.dumps(BASE))
    mode = s.get("mode", "paper") if live_allowed()[0] else "paper"
    out = {"id": s.get("id") or uuid.uuid4().hex[:6], "name": s.get("name") or "untitled", "enabled": bool(s.get("enabled", True)),
           "mode": mode, "size": float(s.get("size", base["size"])), "cash": float(s.get("cash", base["cash"])),
           "quote": "ETH" if s.get("quote") == "ETH" else "any",
           "entry": {k: _num(s.get("entry", {}).get(k, v), v) for k, v in base["entry"].items()},
           "exit": {k: _num(s.get("exit", {}).get(k, v), v) for k, v in base["exit"].items()}}
    return out


def _num(v, default):
    if isinstance(default, bool): return bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "on", "yes")
    try: return type(default)(v)
    except (TypeError, ValueError): return default
