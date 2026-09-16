import asyncio, json, os, threading, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import indexer

HERE = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=indexer.loop, daemon=True).start()
    hub = asyncio.create_task(_hub())
    yield
    hub.cancel()
    # stopping: write what changed since the last round, so the next start has a gap of seconds
    if indexer.store.enabled() and indexer.STATE.backfilled:
        indexer.store.flush(indexer.STATE)


app = FastAPI(title="gradgate engine", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)   # /docs is the terminal's own page


# ---- who may change what ----------------------------------------------------------------------------------------------
# The engine is yours: it listens on 127.0.0.1 and everything that changes state (strategies, the kill switch) answers the
# machine it runs on only. Expose the port and the feed stays readable while the changes are refused.
LOOPBACK = {"127.0.0.1", "::1"}


def _local(req: Request) -> bool:
    # docker-compose publishes the port on the host's 127.0.0.1 only, while requests reach the container from its bridge
    if os.environ.get("GRADGATE_ALLOW_EDIT") == "1": return True
    return bool(req.client) and req.client.host in LOOPBACK


@app.get("/api/config")
def config(request: Request):
    """What the terminal may offer: editing strategies (only from this machine) and whether a key is set for live mode."""
    from strategies import live_allowed
    live, why = live_allowed()
    return JSONResponse({"local_edit": _local(request), "live": live, "live_reason": why, "database": indexer.store.enabled()})


@app.get("/api/state")
def state():
    return JSONResponse(indexer.STATE.snapshot())


_last = {}


def _cached(key, fn):
    """Serve the last good answer while the state lock is busy (a live order or a log batch can hold it for seconds)."""
    try:
        _last[key] = fn(); return _last[key]
    except indexer.Busy:
        return _last.get(key) or ({"strategy": None, "rows": [], "busy": True})


@app.get("/api/feed")
def feed(sid: str | None = None, limit: int = 80):
    return JSONResponse(_cached("feed", lambda: indexer.STATE.feed(sid, limit)))


_ttl_cache: dict[str, tuple[float, object]] = {}


def _ttl(key, ttl: float, fn):
    """Answer many visitors from one computation: reuse a result for `ttl` seconds; a busy state lock serves the last one."""
    hit = _ttl_cache.get(key)
    if hit and time.time() - hit[0] < ttl: return hit[1]
    try:
        val = fn()
    except indexer.Busy:
        return hit[1] if hit else None
    if len(_ttl_cache) > 3000: _ttl_cache.clear()
    _ttl_cache[key] = (time.time(), val)
    return val


@app.get("/api/feed/lists")
def feed_lists():
    """The feed's last-hour lists and counts (polled by every open page every 5 s; built at most once per 2 s)."""
    return JSONResponse(_ttl("lists", 2.0, indexer.STATE.lists) or {"hot": [], "near": [], "graduated": []})


@app.get("/api/pulse/day")
def pulse_day():
    """The last 24 h, read from stats_minute: the pulse itself only carries an hour, and memory only two. One query a
    minute serves every open page. Null without a database, so the caller falls back to the hour instead of zeros."""
    return JSONResponse(_ttl("day", 60.0, indexer.store.day_stats))


# ---- the feed stream: one producer, many listeners ------------------------------------------------
# every open page used to rebuild the same rows on its own; one hub builds the window, new launches,
# deltas and ticks once and fans each serialized event out to every connection's queue, so 200 open pages cost about
# what one does.
HUB: dict = {"subs": set(), "window": None, "tick": None}


def _ev(kind: str, payload) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _publish(kind: str, payload):
    msg = _ev(kind, payload)
    for q in list(HUB["subs"]):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:                  # a page that stopped reading: drop it, its browser reconnects
            HUB["subs"].discard(q)
            while not q.empty(): q.get_nowait()
            q.put_nowait(None)


async def _safe(fn):
    """A read that finds the state lock busy (live order, log batch) is skipped this round."""
    try: return await asyncio.to_thread(fn)
    except indexer.Busy: return None


async def _hub():
    st = indexer.STATE
    seq = st.launch_seq; held: dict[str, float] = {}         # new launch -> first seen: pushed once named (indexer.shown), ≤3 s
    last_delta = int(time.time()); last_tick = 0.0; last_window = 0.0
    while True:
        await asyncio.sleep(0.25)
        try:
            now_s = time.time(); now = int(now_s)
            if not st.backfilled:
                if now_s - last_tick >= 1:
                    last_tick = now_s; _publish("tick", {"warming": True, "phase": st.phase, "t": now})
                seq = st.launch_seq; last_delta = now
                continue
            if now_s - last_window >= 1:                       # the window a new page starts from, refreshed every second
                w = await _safe(lambda: st.feed_rows())
                if w is not None:
                    first = HUB["window"] is None
                    HUB["window"] = w; last_window = now_s
                    if first: _publish("hello", {**w, "t": now})   # pages that connected while the engine warmed up
            if st.launch_seq > seq:
                for q, tok in list(st.launch_log):
                    if q > seq: held.setdefault(tok, now_s)
                seq = st.launch_seq
            if held:
                ready = [tok for tok in held if tok in st.tokens and indexer.shown(st.tokens[tok], now_s)]
                ready += [tok for tok, at in held.items() if tok not in st.tokens and now_s - at > 10]
                if ready:
                    d = await _safe(lambda: st.feed_rows([tok for tok in ready if tok in st.tokens]))
                    if d is not None:
                        for tok in ready: held.pop(tok, None)
                        for r in d["rows"]: _publish("launch", r)
            if now > last_delta:                               # rows that traded or got named, once a second
                changed = await _safe(lambda: st.changed_since(last_delta - 1))
                if changed is not None:
                    last_delta = now
                    if changed:
                        d = await _safe(lambda: st.feed_rows(changed[:200]))
                        if d: _publish("rows", {"rows": d["rows"], "head": d.get("head"), "head_age_s": d.get("head_age_s"), "t": now})
            if now_s - last_tick >= 2:                        # the pulse rail: every 2 s (was 10 s, read as frozen)
                last_tick = now_s
                p = await _safe(lambda: st.feed(None, 1))
                HUB["tick"] = {"t": now, "head": st.head, "head_age_s": int(now_s - st.head_ts) if st.head_ts else None, "pulse": (p or {}).get("pulse")}
                _publish("tick", HUB["tick"])
        except Exception as e:
            st.errors.append(f"{time.strftime('%H:%M:%S')} feed hub: {str(e)[:80]}")


@app.get("/api/feed/stream")
async def feed_stream(sid: str | None = None):
    """The feed as server-sent events (the way Bodkin's /api/hunt works): `hello` with the window on connect, then a
    `launch` as each launch lands (named), `rows` deltas once a second and a `tick` every 2 s — all from the shared hub.
    `sid` is accepted and ignored: the feed is the same for everyone."""
    st = indexer.STATE
    q: asyncio.Queue = asyncio.Queue(maxsize=500)

    async def gen():
        HUB["subs"].add(q)                                     # subscribe first: nothing published after hello is missed
        try:
            if not st.backfilled or HUB["window"] is None:
                yield _ev("hello", {"warming": True, "phase": st.phase, "t": int(time.time())})
            else:
                yield _ev("hello", {**HUB["window"], "t": int(time.time())})
                if HUB["tick"]: yield _ev("tick", HUB["tick"])
            while True:
                msg = await q.get()
                if msg is None: break
                yield msg
        finally:
            HUB["subs"].discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


IMG_DIR = os.path.join(HERE, "static", "cache", "t64")     # 64 px thumbs; the old full-size cache stays in static/cache
# pons pins every launch image itself and serves it at once; the public gateways answer 429 or 404 for fresh CIDs
GATEWAYS = ["https://www.ponsfamily.com/api/ipfs/content/", "https://dweb.link/ipfs/", "https://ipfs.io/ipfs/", "https://w3s.link/ipfs/"]
_img_lock = threading.Lock()


_img_sem = threading.BoundedSemaphore(8)      # at most six gateway fetches at a time: a page with 80 fresh launches must not eat the threadpool
_img_miss: dict[str, float] = {}               # cid -> when every gateway failed; answered 404 instantly for ten minutes


THUMB = 64


def _fetch_img(cid: str):
    """Try the gateways in turn (pons first: it pins every launch image); the original is ~180 KB whatever the variant,
    so it is shrunk once to a 64 px WebP (~3 KB) for the feed. None when nobody has it yet."""
    import urllib.request, io
    for g in GATEWAYS:
        try:
            req = urllib.request.Request(g + cid + ("?variant=card" if "ponsfamily" in g else ""), headers={"user-agent": "gradgate/0.1"})
            with urllib.request.urlopen(req, timeout=6) as r:
                if r.status == 200 and r.headers.get_content_type().startswith("image/"):
                    data, ctype = r.read(4_000_000), r.headers.get_content_type()
                    try:
                        from PIL import Image, ImageOps
                        im = Image.open(io.BytesIO(data)); im.load()
                        im = ImageOps.fit(im.convert("RGBA"), (THUMB, THUMB), Image.LANCZOS)
                        out = io.BytesIO(); im.save(out, "WEBP", quality=82, method=4); return out.getvalue(), "image/webp"
                    except Exception:
                        return data, ctype            # unknown format (svg, animated): serve as is
        except Exception:
            continue
    return None


@app.get("/img/{cid}")
async def img(cid: str):
    """Token image by IPFS CID, fetched once through public gateways and cached on disk (gateways rate-limit browsers).
    Async: the slow gateway walk runs in a thread behind a semaphore, so the API stays responsive while icons load."""
    import re, asyncio, time
    from fastapi.responses import Response
    if not re.fullmatch(r"[A-Za-z0-9]{20,100}", cid): return Response(status_code=400)
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, cid)
    if not os.path.exists(path):
        if time.time() - _img_miss.get(cid, 0) < 600: return Response(status_code=404, headers={"cache-control": "public, max-age=300"})
        def work():
            with _img_sem:
                if os.path.exists(path): return True
                got = _fetch_img(cid)
                if not got: return False
                data, ctype = got
                with _img_lock:
                    open(path + ".tmp", "wb").write(data); open(path + ".type", "w").write(ctype); os.replace(path + ".tmp", path)
                return True
        ok = await asyncio.to_thread(work)
        if not ok:
            _img_miss[cid] = time.time()
            return Response(status_code=404, headers={"cache-control": "public, max-age=300"})
    ctype = open(path + ".type").read() if os.path.exists(path + ".type") else "image/png"
    return Response(open(path, "rb").read(), media_type=ctype, headers={"cache-control": "public, max-age=604800, immutable"})


# ---- strategies ------------------------------------------------------------------------------------------------------
@app.get("/api/strategies")
def strategies():
    return JSONResponse(indexer.STATE.engine.strats)


def _refuse(req: Request):
    return None if _local(req) else JSONResponse({"error": "strategies change only from the machine the engine runs on"}, status_code=403)


@app.post("/api/strategies")
async def upsert_strategy(req: Request):
    refused = _refuse(req)
    if refused: return refused
    body = await req.json()
    with indexer.STATE.lock:
        return JSONResponse(indexer.STATE.engine.upsert(body))


@app.post("/api/strategies/preview")
async def preview_strategy(req: Request):
    """Pass count of a draft strategy over the feed window; nothing is saved."""
    body = await req.json()
    try:
        return JSONResponse(indexer.STATE.preview(body))
    except indexer.Busy:
        return JSONResponse({"busy": True}, status_code=503)


@app.delete("/api/strategies/{sid}")
def delete_strategy(sid: str, request: Request):
    refused = _refuse(request)
    if refused: return refused
    with indexer.STATE.lock:
        if not any(s["id"] == sid for s in indexer.STATE.engine.strats): return JSONResponse({"error": "no such strategy"}, status_code=404)
        indexer.STATE.engine.delete(sid)
    return JSONResponse({"ok": True})


@app.post("/api/strategies/{sid}/reset")
def reset_strategy(sid: str, request: Request):
    refused = _refuse(request)
    if refused: return refused
    with indexer.STATE.lock:
        indexer.STATE.engine.reset(sid)
    return JSONResponse({"ok": True})


@app.post("/api/kill")
def kill(request: Request):
    import strategies
    if not _local(request): return JSONResponse({"error": "the kill switch works from the engine's own machine"}, status_code=403)
    open(strategies.KILL, "w").write(str(time.time())); return JSONResponse({"kill": True})


@app.delete("/api/kill")
def unkill(request: Request):
    import strategies
    if not _local(request): return JSONResponse({"error": "the kill switch works from the engine's own machine"}, status_code=403)
    if os.path.exists(strategies.KILL): os.remove(strategies.KILL)
    return JSONResponse({"kill": False})


@app.get("/api/token/{tok}")
def token(tok: str):
    # every page with this coin open polls it every 4 s: build it at most once per 1.5 s
    d = _ttl("token:" + tok.lower(), 1.5, lambda: indexer.STATE.token_detail(tok))
    return JSONResponse(d if d else {"error": "not tracked (launched before backfill window)"}, status_code=200 if d else 404)


# ---- the terminal: the built UI (ui/dist), served by the engine itself ------------------------------------------------
UI = os.path.join(HERE, "..", "ui", "dist")


@app.get("/{path:path}")
def ui(path: str):
    if path.startswith("api/"): raise HTTPException(status_code=404)
    f = os.path.normpath(os.path.join(UI, path))
    if path and f.startswith(os.path.normpath(UI)) and os.path.isfile(f):
        return FileResponse(f, headers={"cache-control": "public, max-age=31536000, immutable"} if "/assets/" in f else None)
    if path.startswith("assets/"): raise HTTPException(status_code=404)
    index = os.path.join(UI, "index.html")
    if os.path.isfile(index): return FileResponse(index, headers={"cache-control": "no-cache"})
    return JSONResponse({"engine": "running", "ui": "not built — run `npm --prefix ui install && npm --prefix ui run build`"})
