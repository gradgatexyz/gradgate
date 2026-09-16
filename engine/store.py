"""Postgres layer of the engine: what must outlive a restart, and nothing older than a day.

  smart_wallets   forever  wallet -> launches bought, of them graduated; a launch older than a day is rolled in here
  creators        forever  creator record: launches, graduated, self-buys, dumps, pair out
  launches        24 h     every launch: key columns + the in-memory Token as jsonb
  launch_buyers   24 h     first buy block per (launch, wallet): smart-buyer counts and graduation credit to buyers
  paper_trades    24 h     paper positions of every strategy, open and closed
  stats_minute    24 h     launches / graduations / buys / sells per minute (the pulse rail)
  meta                     the ingestion cursor: last polled block + fills already applied past it

The engine keeps working in memory. A writer thread copies what changed every FLUSH_S seconds (collected under the state
lock, written outside it). On start the engine reads the last day back from here and fills only the gap since the cursor
from the chain, instead of reading a whole hour of logs off the node (~4 min). Without DATABASE_URL none of this runs.
Every restart-sensitive count is either absolute here (creators) or split so it is never counted twice: smart_wallets
holds launches older than a day, launch_buyers the last day, and is_smart adds the two.
"""
from __future__ import annotations
import gzip, json, os, threading, time
from collections import deque

URL = os.environ.get("DATABASE_URL", "")
FLUSH_S = float(os.environ.get("GG_FLUSH_S", "30"))
KEEP_S = 24 * 3600
HOT_S = 3 * 3600            # launches read back whole: launched or traded in the last 3 h, or held; older ones as a key row
HERE = os.path.dirname(__file__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS smart_wallets (wallet text PRIMARY KEY, launches int NOT NULL, graduated int NOT NULL);
CREATE TABLE IF NOT EXISTS creators (creator text PRIMARY KEY, launches int NOT NULL, graduated int NOT NULL,
  self_buy_launches int NOT NULL, dumps int NOT NULL, eth_out double precision NOT NULL);
CREATE TABLE IF NOT EXISTS launches (token text PRIMARY KEY, creator text NOT NULL, curve text NOT NULL, block bigint NOT NULL,
  ts bigint NOT NULL, graduated_block bigint, last_trade_ts bigint NOT NULL, data jsonb NOT NULL);
CREATE INDEX IF NOT EXISTS launches_ts ON launches (ts);
CREATE TABLE IF NOT EXISTS launch_buyers (token text NOT NULL, wallet text NOT NULL, block bigint NOT NULL, PRIMARY KEY (token, wallet));
CREATE TABLE IF NOT EXISTS paper_trades (strategy text NOT NULL, token text NOT NULL, open_ts bigint NOT NULL, close_ts bigint,
  data jsonb NOT NULL, PRIMARY KEY (strategy, token, open_ts));
CREATE INDEX IF NOT EXISTS paper_trades_close ON paper_trades (close_ts);
CREATE TABLE IF NOT EXISTS stats_minute (minute bigint PRIMARY KEY, launches int NOT NULL, grads int NOT NULL, buys int NOT NULL,
  sells int NOT NULL, eth_in double precision NOT NULL, eth_out double precision NOT NULL);
"""

SETS = ("buyers", "sellers", "smart")
SKIP = ("pool",)            # a quote cache re-read by mark_pools; not state


def enabled() -> bool:
    return bool(URL)


def _conn():
    import psycopg
    return psycopg.connect(URL, connect_timeout=10)


# ---- schema and first fill ----------------------------------------------------------------------
def ensure(s):
    """Tables, then the first fill of the permanent ones from the snapshots shipped with the engine (built from the
    14-day history.db): smart_wallets.json.gz and creators.json.gz. Runs once per empty table."""
    with _conn() as conn:
        conn.execute(SCHEMA)
        for table, fname, key, cols in (("smart_wallets", "smart_wallets.json.gz", "wallets", "wallet, launches, graduated"),
                                        ("creators", "creators.json.gz", "creators", "creator, launches, graduated, self_buy_launches, dumps, eth_out")):
            if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone(): continue
            path = os.path.join(HERE, fname)
            if not os.path.exists(path): continue
            rows = json.load(gzip.open(path, "rt"))[key]
            with conn.cursor().copy(f"COPY {table} ({cols}) FROM STDIN") as cp:
                for k, v in rows.items(): cp.write_row((k, *v))
            s.errors.append(f"db: seeded {len(rows)} rows into {table} from {fname}")
        conn.commit()


# ---- memory -> rows (under the state lock) -----------------------------------------------------------
def token_json(t) -> str:
    d = {}
    for k in t.__slots__:
        if k in SKIP: continue
        v = getattr(t, k)
        if k in SETS: v = list(v)
        elif k == "net_hist": v = [list(x) for x in v]
        d[k] = v
    return json.dumps(d, default=str)


def token_from(Token, d: dict):
    t = Token(d["token"], d["curve"], d["creator"], d["block"], d["ts"])
    for k, v in d.items():
        if k not in Token.__slots__ or k in SKIP: continue
        if k in SETS: v = set(v)
        elif k == "net_hist": v = deque((tuple(x) for x in v), maxlen=120)
        setattr(t, k, v)
    return t


def collect(s) -> dict:
    """Everything that changed since the last write, serialized now (the state keeps moving once the lock is released)."""
    now = int(time.time())
    toks = [s.tokens[x] for x in s.dirty if x in s.tokens]
    b = {"tok_ids": list(s.dirty), "creator_ids": list(s.dirty_creators), "minute_ids": list(s.dirty_minutes),
         "launches": [(t.token, t.creator, t.curve, t.block, t.ts, t.graduated_block, t.last_trade_ts, token_json(t)) for t in toks],
         "grads": list(s.pending_grads), "buyers": list(s.new_buyers), "dropped": list(s.engine.dropped)}
    b["creators"] = [(c, r["launches"], r["graduated"], r["self_buy_launches"], r["dumps"], r["eth_out"])
                     for c in s.dirty_creators if (r := s.creators.get(c))]
    b["minutes"] = [(m, *s.minutes[m]) for m in s.dirty_minutes if m in s.minutes]
    paper, closed_keys = [], set()
    for sid, book in s.engine.books.items():
        for p in book.positions.values():
            paper.append((sid, p["token"], p["open_ts"], None, json.dumps(p, default=str)))
        for p in book.closed:
            k = (sid, p["token"], p["open_ts"]); closed_keys.add(k)
            if k not in s.saved_closed: paper.append((sid, p["token"], p["open_ts"], p.get("close_ts"), json.dumps(p, default=str)))
    b["paper"], b["closed_keys"] = paper, closed_keys
    b["cursor"] = {"block": s.last_block, "seen": [list(k) for blk, k in s.fill_log if blk > s.last_block], "at": now,
                   "cash": {sid: book.cash for sid, book in s.engine.books.items()}}
    s.dirty = set(); s.dirty_creators = set(); s.dirty_minutes = set(); s.pending_grads = []; s.new_buyers = []; s.engine.dropped = []
    for m in [m for m in s.minutes if m < now - 7200]: del s.minutes[m]
    return b


def requeue(s, b: dict):
    """A failed write: mark it all dirty again so the next round carries it."""
    s.dirty.update(b["tok_ids"]); s.dirty_creators.update(b["creator_ids"]); s.dirty_minutes.update(b["minute_ids"])
    s.pending_grads = b["grads"] + s.pending_grads; s.new_buyers = b["buyers"] + s.new_buyers; s.engine.dropped = b["dropped"] + s.engine.dropped


def write(conn, b: dict):
    with conn.cursor() as cur:
        if b["dropped"]: cur.execute("DELETE FROM paper_trades WHERE strategy = ANY(%s)", (b["dropped"],))
        cur.executemany("""INSERT INTO launches (token, creator, curve, block, ts, graduated_block, last_trade_ts, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (token) DO UPDATE SET graduated_block = EXCLUDED.graduated_block,
            last_trade_ts = EXCLUDED.last_trade_ts, data = EXCLUDED.data""", b["launches"])
        cur.executemany("UPDATE launches SET graduated_block = %s WHERE token = %s AND graduated_block IS NULL", b["grads"])
        cur.executemany("INSERT INTO launch_buyers (token, wallet, block) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", b["buyers"])
        cur.executemany("""INSERT INTO creators (creator, launches, graduated, self_buy_launches, dumps, eth_out) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (creator) DO UPDATE SET launches = EXCLUDED.launches, graduated = EXCLUDED.graduated,
            self_buy_launches = EXCLUDED.self_buy_launches, dumps = EXCLUDED.dumps, eth_out = EXCLUDED.eth_out""", b["creators"])
        cur.executemany("""INSERT INTO stats_minute (minute, launches, grads, buys, sells, eth_in, eth_out) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (minute) DO UPDATE SET launches = EXCLUDED.launches, grads = EXCLUDED.grads, buys = EXCLUDED.buys,
            sells = EXCLUDED.sells, eth_in = EXCLUDED.eth_in, eth_out = EXCLUDED.eth_out""", b["minutes"])
        cur.executemany("""INSERT INTO paper_trades (strategy, token, open_ts, close_ts, data) VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (strategy, token, open_ts) DO UPDATE SET close_ts = EXCLUDED.close_ts, data = EXCLUDED.data""", b["paper"])
        cur.execute("""INSERT INTO meta (key, value) VALUES ('cursor', %s::jsonb)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (json.dumps(b["cursor"]),))
    conn.commit()


# ---- the 24 h roll ------------------------------------------------------------------------------------
def roll(conn, cutoff: int) -> int:
    """Launches older than a day leave: their buyers are credited to smart_wallets (graduated or not), then the rows go."""
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO smart_wallets (wallet, launches, graduated)
            SELECT b.wallet, count(*), count(*) FILTER (WHERE l.graduated_block IS NOT NULL)
            FROM launch_buyers b JOIN launches l ON l.token = b.token WHERE l.ts < %(c)s GROUP BY b.wallet
            ON CONFLICT (wallet) DO UPDATE SET launches = smart_wallets.launches + EXCLUDED.launches,
            graduated = smart_wallets.graduated + EXCLUDED.graduated""", {"c": cutoff})
        cur.execute("DELETE FROM launch_buyers b USING launches l WHERE l.token = b.token AND l.ts < %(c)s", {"c": cutoff})
        cur.execute("DELETE FROM launches WHERE ts < %(c)s", {"c": cutoff}); n = cur.rowcount
        cur.execute("DELETE FROM paper_trades WHERE close_ts IS NOT NULL AND close_ts < %(c)s", {"c": cutoff})
        cur.execute("DELETE FROM stats_minute WHERE minute < %(c)s", {"c": cutoff})
    conn.commit()
    return n


def day_stats() -> dict | None:
    """The last 24 h, summed straight out of stats_minute. The pulse rail only carries an hour and memory only keeps two,
    but the table is written every flush and pruned at KEEP_S — so the day is already there, just never read back.
    None without a database: a self-hosted engine with no DATABASE_URL has no day to report, and the caller falls back
    to the hour rather than showing zeros."""
    if not enabled(): return None
    now = int(time.time())
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""SELECT COALESCE(SUM(launches), 0), COALESCE(SUM(grads), 0), COALESCE(SUM(buys), 0),
                                  COALESCE(SUM(eth_in), 0), MIN(minute)
                           FROM stats_minute WHERE minute >= %s""", (now - KEEP_S,))
            launches, grads, buys, eth_in, first = cur.fetchone()
            # what became of each launch of the day, for the day's field of marks: graduated, filled at least half its
            # curve, traded past its first minute, or silent within a minute of launching (each launch in one bucket)
            cur.execute("""SELECT count(*) FILTER (WHERE graduated_block IS NOT NULL),
                                  count(*) FILTER (WHERE graduated_block IS NULL AND (data->>'peak_net')::float >= 0.5 * (data->>'threshold')::float),
                                  count(*) FILTER (WHERE graduated_block IS NULL AND (data->>'peak_net')::float < 0.5 * (data->>'threshold')::float AND last_trade_ts - ts >= 60),
                                  count(*) FILTER (WHERE graduated_block IS NULL AND (data->>'peak_net')::float < 0.5 * (data->>'threshold')::float AND last_trade_ts - ts < 60)
                           FROM launches WHERE ts >= %s""", (now - KEEP_S,))
            fate = dict(zip(("graduated", "half", "past_minute", "dead"), (int(x) for x in cur.fetchone())))
    except Exception:
        return None
    return {"launches": int(launches), "grads": int(grads), "buys": int(buys), "eth_in": round(float(eth_in), 1),
            "since": int(first) if first is not None else now, "window_s": KEEP_S, "fate": fate}


def prune_memory(s, cutoff: int):
    """The same roll in memory (under the state lock), so a running engine and a restarted one count alike."""
    held = {p["token"] for b in s.engine.books.values() for p in b.positions.values()}
    old = {tok for tok, (ts, _) in s.launch_meta.items() if ts < cutoff} - held
    if not old: return 0
    for w in list(s.wallet_tokens):
        d = s.wallet_tokens[w]; gone = [tok for tok in d if tok in old]
        if gone:
            n0, g0 = s.wallet_stats.get(w, (0, 0))
            s.wallet_stats[w] = (n0 + len(gone), g0 + sum(1 for tok in gone if tok in s.grad_blocks))
            for tok in gone: del d[tok]
        if not d: del s.wallet_tokens[w]
    for tok in old:
        _, cr = s.launch_meta.pop(tok)
        s.grad_blocks.pop(tok, None)
        t = s.tokens.pop(tok, None)
        if t: s.curve2tok.pop(t.curve, None)
        c = s.creators.get(cr)
        if c and tok in c["tokens"]: c["tokens"].remove(tok)
    try:        # the local sqlite tape (coin card trades) keeps the same day
        s.db.execute("DELETE FROM trades WHERE ts < ?", (cutoff,)); s.db.execute("DELETE FROM launches WHERE ts < ?", (cutoff,))
        s.db.execute("DELETE FROM grads WHERE ts < ?", (cutoff,)); s.db.execute("DELETE FROM decisions WHERE ts < ?", (cutoff,)); s.db.commit()
    except Exception as e:
        s.errors.append(f"sqlite prune: {str(e)[:60]}")
    return len(old)


# ---- start: read the last day back -----------------------------------------------------------------------
def restore(s, Token) -> dict:
    """Wallet and creator records always; launches, buyers, stats, paper books and the cursor when an earlier run left
    them. Everything is read first and applied under the lock in one go. Returns what was read (cursor block or None)."""
    t0 = time.time(); now = int(t0); keep = now - KEEP_S; hot = now - HOT_S
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM meta WHERE key = 'cursor'"); r = cur.fetchone(); cursor = r[0] if r else None
        cur.execute("SELECT wallet, launches, graduated FROM smart_wallets"); wallets = {w: (n, g) for w, n, g in cur}
        cur.execute("SELECT creator, launches, graduated, self_buy_launches, dumps, eth_out FROM creators"); creators = {c: rest for c, *rest in cur}
        cur.execute("SELECT strategy, token, open_ts, close_ts, data FROM paper_trades WHERE close_ts IS NULL OR close_ts >= %s "
                    "ORDER BY close_ts DESC NULLS FIRST", (keep,)); paper = cur.fetchall()
        held = list({tok for _, tok, _, c, _ in paper if c is None})
        cur.execute("""SELECT token, creator, ts, graduated_block, CASE WHEN ts >= %s OR last_trade_ts >= %s OR token = ANY(%s) THEN data END
                       FROM launches WHERE ts >= %s ORDER BY block""", (hot, hot, held, keep)); launches = cur.fetchall()
        cur.execute("SELECT wallet, token, block FROM launch_buyers"); buyers = cur.fetchall()
        cur.execute("SELECT minute, launches, grads, buys, sells, eth_in, eth_out FROM stats_minute WHERE minute >= %s ORDER BY minute", (now - 7200,))
        minutes = cur.fetchall()
    if not cursor:
        launches, buyers, minutes, paper = [], [], [], []       # a fresh database: only the permanent records apply
    n_creators = len(creators)                                  # Creators.__missing__ moves records out of this dict below
    with s.lock:
        s.wallet_stats = wallets
        s.creators.base = creators
        whole = 0
        for tok, cr, ts, gb, data in launches:
            s.launch_meta[tok] = (ts, cr)
            if gb: s.grad_blocks[tok] = gb
            s.creators[cr]["tokens"].append(tok)
            if data:
                t = token_from(Token, data); s.tokens[tok] = t; s.curve2tok[t.curve] = tok; whole += 1
                if t.farm_key and ts >= now - 7200: s.farm[t.farm_key].append((t.ts, t.creator))
        for w, tok, blk in buyers:
            if tok in s.launch_meta: s.wallet_tokens[w][tok] = blk
        for m, nl, ng, nb, ns, ei, eo in minutes:
            s.minutes[m] = [nl, ng, nb, ns, ei, eo]
            if m < now - 3600: continue
            s.hour.extend([(m, "launch", 0)] * nl + [(m, "grad", 0)] * ng
                          + [(m, "buy", ei / nb if nb else 0.0)] * nb + [(m, "sell", eo / ns if ns else 0.0)] * ns)
        opened = closed = 0
        for sid, tok, open_ts, close_ts, p in paper:
            book = s.engine.books.get(sid)
            if not book: continue
            if close_ts is None:
                book.positions[tok] = p; opened += 1
            else:
                book.done.add(tok); s.saved_closed.add((sid, tok, open_ts))
                if len(book.closed) < book.closed.maxlen: book.closed.append(p)
                closed += 1
        cash = (cursor or {}).get("cash", {})
        for sid, book in s.engine.books.items():
            rows = [p for x, _, _, c, p in paper if x == sid]
            # the exact cash written with these rows; recomputing it from closed P&L would carry their 4-decimal rounding
            if sid in cash: book.cash = cash[sid]
            elif rows:
                book.cash = book.start + sum(p.get("pnl", 0.0) for p in rows if p.get("close_ts")) \
                    - sum(p["size"] - p.get("realized", 0.0) for p in rows if not p.get("close_ts"))
            if not rows: continue
            marks = [{"ts": p["open_ts"], "strategy": sid, "token": p["token"], "sym": p.get("sym", ""), "action": "BUY", "reason": p.get("why", "")} for p in rows]
            marks += [{"ts": p["close_ts"], "strategy": sid, "token": p["token"], "sym": p.get("sym", ""), "action": "SELL", "reason": p.get("close_reason", "")}
                      for p in rows if p.get("close_ts")]
            book.decisions.extend(sorted(marks, key=lambda d: -d["ts"])[:book.decisions.maxlen])
        if cursor:
            s.last_block = cursor["block"]
            for k in cursor.get("seen", []): key = tuple(k); s.seen_fills.add(key); s.seen_order.append(key)
    info = {"block": cursor["block"] if cursor else None, "cursor_age_s": now - cursor["at"] if cursor else None,
            "wallets": len(wallets), "creators": n_creators, "launches": len(launches), "whole": whole, "buyers": len(buyers),
            "open": opened, "closed": closed, "ms": int((time.time() - t0) * 1000)}
    s.db_status = {"restored": info}
    s.errors.append(f"db restore: {info}")
    return info


# ---- the writer ---------------------------------------------------------------------------------------------
_write_lock = threading.Lock()


def flush(s, conn=None):
    """One write round; returns the connection to reuse (None after a failure)."""
    with _write_lock:
        t_lock = time.time()
        with s.lock: b = collect(s)
        t0 = time.time(); collect_ms = int((t0 - t_lock) * 1000)       # includes the wait for the lock
        try:
            conn = conn if conn is not None and not conn.closed else _conn()
            write(conn, b)
        except Exception as e:
            try:
                if conn is not None: conn.close()
            except Exception: pass
            with s.lock: requeue(s, b)
            s.errors.append(f"{time.strftime('%H:%M:%S')} db write: {str(e)[:100]}")
            s.db_status = {**(s.db_status or {}), "last_error": str(e)[:100], "error_at": int(time.time())}
            return None
        for k in [k for k in s.saved_closed if k not in b["closed_keys"]]: s.saved_closed.discard(k)
        s.saved_closed.update(b["closed_keys"])
        s.db_status = {**(s.db_status or {}), "write_at": int(time.time()), "write_ms": int((time.time() - t0) * 1000), "collect_ms": collect_ms,
                       "rows": {k: len(b[k]) for k in ("launches", "buyers", "creators", "minutes", "paper")}, "block": b["cursor"]["block"]}
        return conn


def writer_loop(s):
    conn = None; last_roll = 0.0
    while True:
        time.sleep(FLUSH_S)
        if not s.backfilled: continue
        conn = flush(s, conn)
        if conn is None or time.time() - last_roll < 3600: continue
        cutoff = int(time.time()) - KEEP_S
        try:
            n = roll(conn, cutoff)
            with s.lock: m = prune_memory(s, cutoff)
            last_roll = time.time()
            s.db_status = {**(s.db_status or {}), "roll_at": int(last_roll), "rolled": n, "pruned": m}
        except Exception as e:
            s.errors.append(f"{time.strftime('%H:%M:%S')} db roll: {str(e)[:100]}")
            try: conn.rollback()
            except Exception: conn = None


def start(s):
    threading.Thread(target=writer_loop, args=(s,), daemon=True).start()
