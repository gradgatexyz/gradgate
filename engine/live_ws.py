"""Live ingestion over a WebSocket subscription (Alchemy): factory logs only (launches + graduations).

Alchemy bills subscription pushes at 0.04 CU/byte. newHeads (36k blocks/h × 3.2 KB) and the all-curves trade
stream (52k pushes/h) burned ~6.5M CU/h — the whole free tier in five hours — so both were dropped on
14.09.2026. The factory stream is ~1k pushes/h and gives launches the same second;
trades and the head come from the 1-second poller in indexer.loop, which also dedupes what the socket sent.
"""
from __future__ import annotations
import asyncio, json, os, time, threading
import websockets

from rhradar.pons import FACTORY, TOKEN_LAUNCHED, POOL_GRADUATED, CURVE_BUY, CURVE_SELL


def ws_url() -> str | None:
    u = os.environ.get("RH_WS") or os.environ.get("RH_RPC", "")
    if "alchemy.com" in u or u.startswith("wss://"):
        return u.replace("https://", "wss://")
    return None


def full_feed(url: str) -> bool:
    """publicnode's socket (wss://robinhood-rpc.publicnode.com) is free and unmetered: subscribe to every curve trade and
    newHeads too (found via Bodkin). A metered socket (Alchemy, 0.04 CU/byte) gets the
    factory only."""
    return "publicnode" in url or os.environ.get("RH_WS_FULL") == "1"


async def _run(state, url):
    async with websockets.connect(url, max_size=None, ping_interval=20) as ws:
        subs = {}
        wanted = [["logs", {"address": FACTORY, "topics": [[TOKEN_LAUNCHED, POOL_GRADUATED]]}]]
        if full_feed(url):
            wanted += [["logs", {"topics": [[CURVE_BUY, CURVE_SELL]]}], ["newHeads"]]
        for i, params in enumerate(wanted, 1):
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": "eth_subscribe", "params": params}))
        while True:
            m = json.loads(await ws.recv())
            if "id" in m and "result" in m:
                subs[m["result"]] = m["id"]; state.ws_status = f"subscribed {len(subs)}/{len(wanted)} ({'full' if len(wanted) > 1 else 'factory'})"; continue
            p = m.get("params") or {}
            kind = subs.get(p.get("subscription")); r = p.get("result")
            if not r: continue
            now = int(time.time())
            with state.lock:
                if kind == 1:
                    if r.get("removed"): continue
                    (state.add_launch if r["topics"][0] == TOKEN_LAUNCHED else state.add_grad)(r)
                    state.ws_events += 1
                elif kind == 2:
                    if r.get("removed"): continue
                    t = state.add_trade(r); state.ws_events += 1
                    if t: state.decide(t, now)
                elif kind == 3:
                    state.head = int(r["number"], 16); state.head_ts = int(r["timestamp"], 16); state.ws_blocks += 1
                state.last_ws = time.time()


def start(state):
    url = ws_url()
    if not url:
        state.ws_status = "off (no Alchemy URL)"; return False
    state.ws_status = "connecting"; state.ws_events = 0; state.ws_blocks = 0; state.last_ws = 0

    def runner():
        while True:
            try:
                asyncio.run(_run(state, url))
            except Exception as e:
                state.ws_status = f"reconnecting: {str(e)[:60]}"; time.sleep(3)
    threading.Thread(target=runner, daemon=True).start()
    return True
