"""Robinhood Chain (4663) JSON-RPC, keyless. Retries on 429/timeouts, chunks wide log queries."""
from __future__ import annotations
import json, time, urllib.request, urllib.error

import os
PUBLIC = "https://rpc.mainnet.chain.robinhood.com"
RPC = os.environ.get("RH_RPC", PUBLIC)              # fast provider (Alchemy) for calls, heads, receipts
RPC_LOGS = os.environ.get("RH_RPC_LOGS", PUBLIC)    # wide-range eth_getLogs: Alchemy free caps at 10 blocks, the public node serves ~200k
# fresh-window eth_getLogs (≤FAST_SPAN blocks per call, polled every second by the terminal). Alchemy bills getLogs a flat
# 60 CU whatever the log count, so ~1.5 calls/s ≈ 0.4M CU/h; WebSocket pushes cost 0.04 CU/byte and were 15× dearer.
FAST = os.environ.get("RH_RPC_FAST") or (RPC if "alchemy" in RPC else "")
FAST_SPAN = int(os.environ.get("RH_FAST_SPAN", "10"))
# rough Alchemy CU prices, to watch the key burn from /api/state (docs.alchemy.com/reference/compute-unit-costs)
CU_COST = {"eth_call": 26, "eth_getLogs": 60, "eth_blockNumber": 10, "eth_getBlockByNumber": 20, "eth_getTransactionByHash": 20,
           "eth_getTransactionReceipt": 20, "eth_getBalance": 20, "eth_estimateGas": 20, "eth_sendRawTransaction": 40, "eth_getTransactionCount": 20}
CU = {"total": 0, "paid": 0}          # paid = calls that went to a non-public URL
UA = "gradgate/0.1"
BLOCK_S = 0.1013          # measured 2026-09-13 over 10k blocks
# eth_getLogs span per call. The official node took ~200k blocks until mid-September; on 15.09.2026 it answered
# "internal server errror" above ~5-20k (5k: 0.3 s, 20k: error) and publicnode refuses HTTP getLogs outright (403).
MAX_SPAN = int(os.environ.get("RH_LOGS_SPAN", "5000"))


class RpcError(RuntimeError):
    pass


_last = [0.0]
MIN_GAP = 0.15


def _throttle():
    wait = _last[0] + MIN_GAP - time.time()
    if wait > 0: time.sleep(wait)
    _last[0] = time.time()


def _bill(url: str, cu: int):
    CU["total"] += cu
    if "alchemy" in url: CU["paid"] += cu          # publicnode and the official node are free


def call(method: str, params: list, retries: int = 4, timeout: int = 45, url: str | None = None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    url = url or (RPC_LOGS if method == "eth_getLogs" else RPC)
    _bill(url, CU_COST.get(method, 20))
    for i in range(retries):
        _throttle()
        req = urllib.request.Request(url, data=body, headers={"content-type": "application/json", "user-agent": UA})
        try:
            j = json.load(urllib.request.urlopen(req, timeout=timeout))
        except urllib.error.HTTPError as e:
            last = e; time.sleep((1.0 if e.code == 429 else 1.5) * (i + 1)); continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e; time.sleep(1.5 * (i + 1)); continue
        if "error" in j:
            msg = str(j["error"])
            if "429" in msg or "limit" in msg.lower() or "timed out" in msg:
                last = RpcError(msg); time.sleep(2 * (i + 1)); continue
            raise RpcError(f"{method}: {msg}")
        return j["result"]
    raise RpcError(f"{method}: gave up ({last})")


def batch(calls: list[tuple[str, list]], timeout: int = 60) -> list:
    """One HTTP round trip for many calls. Returns results in order; raises on any error."""
    body = json.dumps([{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]).encode()
    _bill(RPC, sum(CU_COST.get(m, 20) for m, _ in calls))
    j = None
    for i in range(3):
        _throttle()
        req = urllib.request.Request(RPC, data=body, headers={"content-type": "application/json", "user-agent": UA})
        try:
            j = json.load(urllib.request.urlopen(req, timeout=timeout)); break
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(1.0 * (i + 1)); continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 * (i + 1))
    if j is None: raise RpcError("batch: gave up after 429s")
    out = [None] * len(calls)
    for r in j:
        if "error" in r:
            raise RpcError(f"batch[{r['id']}]: {r['error']}")
        out[r["id"]] = r["result"]
    return out


def head() -> int:
    return int(call("eth_blockNumber", []), 16)


def block_ts(n: int) -> int:
    return int(call("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)


def topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


def addr(t: str) -> str:
    return "0x" + t[-40:].lower()


def logs(from_block: int, to_block: int, address=None, topics=None, span: int = MAX_SPAN, url: str | None = None) -> list:
    """getLogs over any range, split into spans the node accepts; halves a span that times out."""
    out = []
    a = from_block
    while a <= to_block:
        b = min(a + span - 1, to_block)
        f = {"fromBlock": hex(a), "toBlock": hex(b)}
        if address: f["address"] = address
        if topics: f["topics"] = topics
        try:
            out += call("eth_getLogs", [f], url=url, retries=2 if url else 4, timeout=15 if url else 45)
        except RpcError as e:
            # the official node words a slow range as "context deadline exceeded", not "timed out": halve it too
            if span > 500 and any(w in str(e).lower() for w in ("timed out", "gave up", "deadline", "exceeded", "internal server")):
                out += logs(a, b, address, topics, span // 2, url); a = b + 1; continue
            raise
        a = b + 1
    return out


def fast_logs(from_block: int, to_block: int, topics=None) -> list:
    """Fresh-window logs through the fast provider in FAST_SPAN-block chunks. Caller falls back to logs() on RpcError."""
    if not FAST: raise RpcError("no fast provider (RH_RPC_FAST / alchemy RH_RPC)")
    return logs(from_block, to_block, None, topics, span=FAST_SPAN, url=FAST)
