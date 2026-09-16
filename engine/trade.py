"""Manual trades from the command line: look before you send.

Every trade is planned first — the factory record, the curve's own quote, the opening tax for your address, and an eth_call
of the exact transaction — and nothing is signed unless the caller asks to send. The planning half needs no key and no
funds: the simulation lends the sender the ETH it needs through an eth_call state override.

Manual buys take ETH-paired curves. Sells take any ETH-paired curve and any graduated launch's uniswap v4 pool (whatever
its pair). Manual trades do not enter a strategy's book.
"""
from __future__ import annotations

import os
import secrets
import time

from web3 import Web3

import exec_pons
import pons_pool

MAX_OPENING_TAX_BPS = 300
ZERO = "0x" + "0" * 40
_cs = Web3.to_checksum_address


def _erc20_str(token: str, sig: str) -> str:
    try:
        raw = bytes(exec_pons.w3.eth.call({"to": _cs(token), "data": "0x" + Web3.keccak(text=sig)[:4].hex().removeprefix("0x")}))
        n = int.from_bytes(raw[32:64], "big")
        return raw[64:64 + n].decode(errors="replace")
    except Exception:
        return ""


def sender() -> str:
    """Your address when a key is set, otherwise a throwaway one — planning never needs the key."""
    key = os.environ.get("RH_PRIVATE_KEY")
    return exec_pons.w3.eth.account.from_key(key).address if key else exec_pons.w3.eth.account.from_key("0x" + secrets.token_hex(32)).address


def opening_tax_bps(curve: str, addr: str) -> int:
    sel = Web3.keccak(text="currentSnipeTaxBps(address)")[:4].hex().removeprefix("0x")
    r = exec_pons.w3.eth.call({"to": _cs(curve), "data": "0x" + sel + exec_pons._a(addr)})
    return int(r.hex(), 16) if len(r) else 0


def inspect(token: str) -> dict:
    rec = pons_pool.record(token)
    return {**rec, "token": token.lower(), "symbol": _erc20_str(token, "symbol()"), "name": _erc20_str(token, "name()"),
            "phase_name": pons_pool.PHASES[rec["phase"]], "eth_pair": rec["pair"] == ZERO}


def balance(token: str, owner: str) -> float:
    return exec_pons.token_balance(token, owner)


def eth_balance(owner: str) -> float:
    return exec_pons.w3.eth.get_balance(_cs(owner)) / 1e18


# ---- buy --------------------------------------------------------------------------------------------------------------
def plan_buy(token: str, eth: float, slippage: float = 0.03) -> dict:
    info = inspect(token)
    who = sender()
    plan = {**info, "eth": eth, "slippage": slippage, "sender": who, "ok": False}
    if info["phase"] != 0:
        plan["error"] = f"the curve is closed ({info['phase_name']}): manual buys go to open curves only"; return plan
    if not info["eth_pair"]:
        plan["error"] = "this launch is paired with a token, not ETH: manual buys take ETH-paired curves"; return plan
    curve = info["curve"]
    plan["tax_bps"] = tax = opening_tax_bps(curve, who)
    tokens = exec_pons.quote_buy_onchain(curve, eth)
    plan["tokens_out"] = tokens
    plan["min_tokens_out"] = min_out = tokens * (1 - slippage)
    wei = int(eth * 1e18)
    tx = {"from": who, "to": _cs(curve), "value": wei, "data": exec_pons.buy_data(wei, int(min_out * 1e18), who)}
    lend = {who: {"balance": hex(wei + 10**17)}}          # the simulation lends the ETH, so a plan needs no funds
    t0 = time.time()
    try:
        exec_pons.w3.eth.call(tx, "latest", lend)
        plan["gas"] = exec_pons.w3.eth.estimate_gas(tx, "latest", lend)
        plan["simulated"] = True
    except Exception as e:
        plan["simulated"] = False; plan["error"] = f"simulation reverted: {str(e)[:160]}"
    plan["sim_ms"] = int((time.time() - t0) * 1000)
    if tax > MAX_OPENING_TAX_BPS:
        plan["error"] = f"opening tax is {tax / 100:g}% right now — wait a few seconds (the ceiling is {MAX_OPENING_TAX_BPS / 100:g}%)"
    plan["ok"] = plan.get("simulated", False) and tax <= MAX_OPENING_TAX_BPS
    return plan


def buy(token: str, eth: float, slippage: float = 0.03) -> dict:
    plan = plan_buy(token, eth, slippage)
    if not plan["ok"]: raise RuntimeError(plan.get("error", "the plan did not pass"))
    me = exec_pons._account().address
    before = balance(token, me)
    txh = exec_pons.send_buy(plan["curve"], eth, slippage=slippage)
    rcpt = exec_pons.w3.eth.wait_for_transaction_receipt(txh, timeout=120)
    return {"tx": txh if txh.startswith("0x") else "0x" + txh, "status": rcpt["status"], "tokens": balance(token, me) - before}


# ---- sell -------------------------------------------------------------------------------------------------------------
def plan_sell(token: str, pct: float = 100.0, slippage: float = 0.05, owner: str | None = None) -> dict:
    info = inspect(token)
    who = owner or sender()
    held = balance(token, who)
    tokens = held * pct / 100
    plan = {**info, "sender": who, "held": held, "tokens": tokens, "pct": pct, "slippage": slippage, "ok": False}
    if tokens <= 0:
        plan["error"] = "this wallet holds none of it"; return plan
    if info["phase"] == 0:
        if not info["eth_pair"]:
            plan["error"] = "a token-paired curve: sell it from a strategy or in the pool after graduation"; return plan
        plan["venue"] = "curve"; plan["out_unit"] = "ETH"
        wei = int(tokens * 1e18)
        try:                                   # exact once the curve is approved: sell() answers in eth_call
            plan["out"] = exec_pons.quote_sell_onchain(info["curve"], wei, who) / 1e18
            sim = {"ok": True}
        except Exception:                      # before the approval: the curve formula with this curve's fee and creator tax
            fee = exec_pons.curve_fee(info["curve"])
            plan["out"] = exec_pons.curve.sell(exec_pons.reserve_from_quote(info["curve"], fee=fee), tokens, fee)[0]
            sim = {"ok": False}
        plan["min_out"] = plan["out"] * (1 - slippage)
    elif info["phase"] == 2:
        wei = int(tokens * 1e18)
        raw = pons_pool.quote_sell(token, wei, info["pair"], info["tick_spacing"])
        plan["venue"] = "pool"; plan["out"] = raw / 1e18
        plan["out_unit"] = "ETH" if info["eth_pair"] else (_erc20_str(info["pair"], "symbol()") or info["pair"][:10])
        plan["min_out"] = plan["out"] * (1 - slippage)
        sim = pons_pool.simulate_sell(token, wei, who, slippage, rec=info)
    else:
        plan["error"] = f"nothing to trade right now: the launch is {info['phase_name']} (the pool is not open yet)"; return plan
    plan["simulated"] = sim.get("ok", False)
    if not plan["simulated"]:
        # a first sell needs an approval the simulation cannot see yet; sending approves first
        plan["note"] = "the simulation reverted — usually the one-time approval this token still needs; sending approves first"
    plan["ok"] = True
    return plan


def sell(token: str, pct: float = 100.0, slippage: float = 0.05) -> dict:
    me = exec_pons._account().address
    plan = plan_sell(token, pct, slippage, owner=me)
    if not plan["ok"]: raise RuntimeError(plan.get("error", "the plan did not pass"))
    before = eth_balance(me)
    # the senders cap the amount at the wallet's exact balance in wei; a whole sell asks for a hair more than the float
    # holds, so the cap picks the exact balance and no dust is left behind
    tokens = plan["tokens"] * (1 + 1e-9) if pct >= 100 else plan["tokens"]
    if plan["venue"] == "curve":
        txh = exec_pons.send_sell(plan["curve"], tokens, slippage=slippage, token=token)
    else:
        txh = pons_pool.send_sell(token, tokens, slippage=slippage, rec={k: plan[k] for k in ("curve", "pair", "tick_spacing", "phase")})
    rcpt = exec_pons.w3.eth.wait_for_transaction_receipt(txh, timeout=120)
    return {"tx": txh if txh.startswith("0x") else "0x" + txh, "status": rcpt["status"], "eth_change": eth_balance(me) - before}
