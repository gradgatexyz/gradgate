"""Graduated pons tokens: sell in the Uniswap v4 pool behind the pons hook, from a plain EOA.

Every call shape here was read off live router transactions and checked read-only on chain on 15.09.2026:
  - every graduated launch, ETH or a stock / USDG pair, trades in a pool keyed {pair, token, fee 0, tickSpacing from the
    factory record (200), hooks = the pons meme hook}; getSlot0 answers for that id and the v4 Quoter prices it;
  - the UniversalRouter takes execute(0x10 V4_SWAP, [06 SWAP_EXACT_IN_SINGLE · 0c SETTLE_ALL · 0f TAKE_ALL]); its
    ExactInputSingleParams carry minHopPriceX36 (6 of 7 recent router txs on a pons pool encode it); amountOutMinimum
    is enforced (a 200 % floor reverts);
  - ERC-20 input is pulled through Permit2: token.approve(Permit2) once, then Permit2.approve(token, router) once;
    a wallet without them reverts.
Factory phases: 0 curve · 1 swept (curve closed, pool not created yet: nothing to trade) · 2 pool · 3 rescued.

quote_* and simulate_sell use eth_call and need no key; send_sell signs through exec_pons (RH_PRIVATE_KEY).
"""
from __future__ import annotations
import time
from eth_abi import encode, decode
from web3 import Web3
import exec_pons

FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"
HOOK = "0xE5e702641Ea86F4ae6cC3cDaeD2B886f976Be044"
QUOTER = "0x8dc178efb8111bb0973dd9d722ebeff267c98f94"
ROUTER = "0x8876789976decbfcbbbe364623c63652db8c0904"          # UniswapUniversalRouter
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
ZERO = "0x" + "0" * 40
PHASES = ("curve", "swept", "pool", "rescued")
MAX160, MAX48 = (1 << 160) - 1, (1 << 48) - 1
KEY = "(address,address,uint24,int24,address)"
LAUNCHED = ["address", "address", "address", "address", "address", "uint256", "uint24", "int24", "uint16", "bool", "uint8", "uint256", "uint256", "uint256", "bool"]
_cs = Web3.to_checksum_address


def _sel(sig: str) -> bytes: return bytes(Web3.keccak(text=sig)[:4])


def _call(to: str, data: bytes, sender: str | None = None) -> bytes:
    tx = {"to": _cs(to), "data": "0x" + data.hex()}
    if sender: tx["from"] = _cs(sender)
    return bytes(exec_pons.w3.eth.call(tx))


def record(token: str) -> dict:
    """factory.getLaunchedToken: the pair the launch graduates into, the pool's tick spacing and the phase."""
    v = decode(LAUNCHED, _call(FACTORY, _sel("getLaunchedToken(address)") + encode(["address"], [_cs(token)])))
    if not v[14]: raise RuntimeError("not a pons v2 token")
    return {"curve": v[1].lower(), "pair": v[4].lower(), "tick_spacing": int(v[7]), "phase": int(v[10])}


def pool_key(token: str, pair: str = ZERO, tick_spacing: int = 200) -> tuple:
    a, b = sorted([token.lower(), pair.lower()], key=lambda x: int(x, 16))
    return (_cs(a), _cs(b), 0, int(tick_spacing), _cs(HOOK))


def quote_sell(token: str, tokens_wei: int, pair: str = ZERO, tick_spacing: int = 200) -> int:
    """Raw pair units the pool pays for `tokens_wei` right now, after the hook fee and price impact."""
    key = pool_key(token, pair, tick_spacing)
    zfo = key[0].lower() == token.lower()
    data = _sel(f"quoteExactInputSingle(({KEY},bool,uint128,bytes))") + encode([f"({KEY},bool,uint128,bytes)"], [(key, zfo, int(tokens_wei), b"")])
    return decode(["uint256", "uint256"], _call(QUOTER, data))[0]


def swap_data(key: tuple, zero_for_one: bool, amount_in: int, min_out: int, deadline: int | None = None) -> str:
    """UniversalRouter.execute calldata for one exact-input single-hop swap."""
    swap = encode([f"({KEY},bool,uint128,uint128,uint256,bytes)"], [(key, zero_for_one, int(amount_in), int(min_out), 0, b"")])
    c_in, c_out = (key[0], key[1]) if zero_for_one else (key[1], key[0])
    plan = encode(["bytes", "bytes[]"], [bytes([0x06, 0x0C, 0x0F]),
                                         [swap, encode(["address", "uint256"], [c_in, int(amount_in)]), encode(["address", "uint256"], [c_out, int(min_out)])]])
    deadline = deadline or int(time.time()) + 120
    return "0x" + (_sel("execute(bytes,bytes[],uint256)") + encode(["bytes", "bytes[]", "uint256"], [bytes([0x10]), [plan], deadline])).hex()


def balance_wei(token: str, owner: str) -> int:
    return decode(["uint256"], _call(token, _sel("balanceOf(address)") + encode(["address"], [_cs(owner)])))[0]


def permit2_allowance(token: str, owner: str) -> tuple[int, int]:
    amount, expiration, _ = decode(["uint160", "uint48", "uint48"], _call(PERMIT2, _sel("allowance(address,address,address)") + encode(["address", "address", "address"], [_cs(owner), _cs(token), _cs(ROUTER)])))
    return amount, expiration


def ensure_permit2(token: str, tokens_wei: int) -> list[str]:
    """token -> Permit2 (max ERC-20 approve) and Permit2 -> router (max amount, no expiry), each sent once per token."""
    acct = exec_pons._account(); sent = []
    h = exec_pons.ensure_allowance(token, PERMIT2, tokens_wei)
    if h: sent.append(h)
    amount, exp = permit2_allowance(token, acct.address)
    if amount < tokens_wei or exp < time.time() + 600:
        data = _sel("approve(address,address,uint160,uint48)") + encode(["address", "address", "uint160", "uint48"], [_cs(token), _cs(ROUTER), MAX160, MAX48])
        h = exec_pons._send({"to": _cs(PERMIT2), "value": 0, "data": "0x" + data.hex()})
        exec_pons.w3.eth.wait_for_transaction_receipt(h, timeout=120); sent.append(h)
    return sent


def simulate_sell(token: str, tokens_wei: int, sender: str, slippage: float = 0.03, rec: dict | None = None) -> dict:
    """eth_call of the exact sell we would send, from `sender` (it must hold the tokens and the Permit2 approvals)."""
    rec = rec or record(token)
    if rec["phase"] != 2: return {"ok": False, "error": f"phase {rec['phase']} ({PHASES[rec['phase']]}): no pool to trade"}
    key = pool_key(token, rec["pair"], rec["tick_spacing"]); zfo = key[0].lower() == token.lower()
    q = quote_sell(token, tokens_wei, rec["pair"], rec["tick_spacing"])
    try:
        exec_pons.w3.eth.call({"from": _cs(sender), "to": _cs(ROUTER), "value": 0, "data": swap_data(key, zfo, tokens_wei, int(q * (1 - slippage)))})
        return {"ok": True, "quote_raw": q}
    except Exception as e:
        return {"ok": False, "quote_raw": q, "error": str(e)[:160]}


def send_sell(token: str, tokens: float, slippage: float = 0.03, rec: dict | None = None) -> str:
    """Sell up to `tokens` (never more than the wallet holds) into the graduated pool; minOut from the Quoter."""
    rec = rec or record(token)
    if rec["phase"] != 2: raise RuntimeError(f"pool not open: phase {rec['phase']} ({PHASES[rec['phase']]})")
    wei = min(int(tokens * 1e18), balance_wei(token, exec_pons._account().address))
    if wei <= 0: raise RuntimeError("no token balance to sell")
    ensure_permit2(token, wei)
    key = pool_key(token, rec["pair"], rec["tick_spacing"]); zfo = key[0].lower() == token.lower()
    min_out = int(quote_sell(token, wei, rec["pair"], rec["tick_spacing"]) * (1 - slippage))
    return exec_pons._send({"to": _cs(ROUTER), "value": 0, "data": swap_data(key, zfo, wei, min_out)})


if __name__ == "__main__":
    import sys
    # read-only check: python pons_pool.py <token> [tokens] [sender to simulate from]
    tok = sys.argv[1]; n = float(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000
    r = record(tok); print("record", r)
    if r["phase"] == 2:
        print(f"quote: {n:,.0f} tokens -> {quote_sell(tok, int(n * 1e18), r['pair'], r['tick_spacing'])} raw pair units")
        if len(sys.argv) > 3: print("simulate", simulate_sell(tok, int(n * 1e18), sys.argv[3], rec=r))
