"""Buy / sell on a Pons bonding curve from a plain EOA.

Call shapes were read off live transactions:
  buy (uint256 ethAmount, uint256 minTokensOut, address referrer)   selector 0x59a87bc1, payable, value == ethAmount
  sell(uint256 tokenAmount, uint256 minEthOut,  address referrer)   selector 0xd04c6983

simulate_* use eth_call and need no key.  send_* sign with RH_PRIVATE_KEY from the environment and
broadcast through RH_RPC. Nothing here is called by the terminal or the paper engine.
"""
from __future__ import annotations
import os, threading, time
from web3 import Web3
import curve

RPC = os.environ.get("RH_RPC", "https://rpc.mainnet.chain.robinhood.com")
BUY_SEL, SELL_SEL = "59a87bc1", "d04c6983"
REFERRER = "0x0000000000000000000000000000000000000000"   # the curve rejects the zero address (ZeroAddress()); pass the sender
w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))


def _u(x: int) -> str: return format(int(x), "064x")
def _a(addr: str) -> str: return addr[2:].lower().rjust(64, "0")


def buy_data(eth_wei: int, min_tokens_wei: int, referrer: str = REFERRER) -> str:
    return "0x" + BUY_SEL + _u(eth_wei) + _u(min_tokens_wei) + _a(referrer)


def sell_data(tokens_wei: int, min_eth_wei: int, referrer: str = REFERRER) -> str:
    return "0x" + SELL_SEL + _u(tokens_wei) + _u(min_eth_wei) + _a(referrer)


def quote_buy(reserve_eth: float, eth: float) -> float:
    return curve.buy(reserve_eth, eth)[0]


def quote_sell(reserve_eth: float, tokens: float) -> float:
    return curve.sell(reserve_eth, tokens)[0]


PROBE_ADDR = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"   # WETH contract: always funded, so eth_call with value passes


def opening_tax_bps(curve_addr: str) -> int:
    """The opening tax this curve would charge our buy right now (0 once the window has passed, or for an exempt address)."""
    sel = Web3.keccak(text="currentSnipeTaxBps(address)")[:4].hex().removeprefix("0x")
    r = w3.eth.call({"to": Web3.to_checksum_address(curve_addr), "data": "0x" + sel + _a(_account().address)})
    return int(r.hex(), 16) if len(r) else 0


def quote_buy_onchain(curve_addr: str, eth: float, sender: str = PROBE_ADDR) -> float:
    """Exact tokens out for `eth`, as the curve itself computes it: buy() returns amountOut in eth_call."""
    wei = int(eth * 1e18)
    r = w3.eth.call({"from": Web3.to_checksum_address(sender), "to": Web3.to_checksum_address(curve_addr), "value": wei, "data": buy_data(wei, 0, sender)})
    return int(r.hex(), 16) / 1e18


def curve_fee(curve_addr: str) -> float:
    """Everything the curve takes from a trade, as a fraction: its own fee plus the creator's tax (e.g. 0.01 + 0.02)."""
    def u(sig: str) -> int:
        return int(w3.eth.call({"to": Web3.to_checksum_address(curve_addr), "data": "0x" + Web3.keccak(text=sig)[:4].hex().removeprefix("0x")}).hex(), 16)
    try:
        return (u("feeBps()") + u("creatorTaxBps()")) / 1e4
    except Exception:
        return curve.FEE


def quote_sell_onchain(curve_addr: str, tokens_wei: int, owner: str) -> int:
    """Exact pair units a sell pays out, as the curve computes it: sell() returns the amount in eth_call. The owner must hold
    the tokens and have approved the curve, so this works right before a send, after ensure_allowance."""
    r = w3.eth.call({"from": Web3.to_checksum_address(owner), "to": Web3.to_checksum_address(curve_addr), "value": 0,
                     "data": sell_data(int(tokens_wei), 0, owner)})
    return int(r.hex(), 16)


def reserve_from_quote(curve_addr: str, probe_eth: float = 0.001, fee: float | None = None) -> float:
    """Back out the curve's ETH reserve (net-of-fee model) from one on-chain quote — the reserve is not readable directly.
    `fee` is the curve's whole take (curve_fee); the creator tax above the 1 % base shrinks what a buy puts in."""
    tokens = quote_buy_onchain(curve_addr, probe_eth)
    extra = max((fee if fee is not None else curve.FEE) - curve.FEE, 0.0)
    lo, hi = 0.0, 100.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if curve.buy(mid, probe_eth, extra)[0] > tokens: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def simulate_buy(curve_addr: str, eth: float, sender: str, slippage: float = 0.03, reserve_eth: float | None = None) -> dict:
    """eth_call of the exact buy we would send. `sender` must hold enough ETH for the call to pass."""
    wei = int(eth * 1e18)
    min_out = int(quote_buy(reserve_eth, eth) * (1 - slippage) * 1e18) if reserve_eth is not None else 0
    tx = {"from": Web3.to_checksum_address(sender), "to": Web3.to_checksum_address(curve_addr), "value": wei, "data": buy_data(wei, min_out, sender)}
    t0 = time.time()
    try:
        w3.eth.call(tx); gas = w3.eth.estimate_gas(tx)
        return {"ok": True, "gas": gas, "min_tokens_out": min_out / 1e18, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "ms": int((time.time() - t0) * 1000)}


def simulate_sell(curve_addr: str, tokens: float, sender: str, slippage: float = 0.03, reserve_eth: float | None = None) -> dict:
    wei = int(tokens * 1e18)
    min_out = int(quote_sell(reserve_eth, tokens) * (1 - slippage) * 1e18) if reserve_eth is not None else 0
    tx = {"from": Web3.to_checksum_address(sender), "to": Web3.to_checksum_address(curve_addr), "value": 0, "data": sell_data(wei, min_out, sender)}
    try:
        w3.eth.call(tx); gas = w3.eth.estimate_gas(tx)
        return {"ok": True, "gas": gas, "min_eth_out": min_out / 1e18}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _account():
    key = os.environ.get("RH_PRIVATE_KEY")
    if not key: raise RuntimeError("RH_PRIVATE_KEY is not set")
    return w3.eth.account.from_key(key)


_nonce_lock = threading.Lock()
_nonce = {"next": None, "at": 0.0}


def _send(tx: dict) -> str:
    """Sign and broadcast. One sender at a time: the buy, the exit and the background approval all share one wallet, and two
    sends reading the same transaction count collide ("nonce too low", seen in the first live run, 16.09.2026). The next
    nonce is kept locally and reconciled with the chain's pending count; a local count older than 60 s yields to the chain,
    so a transaction the node dropped cannot leave a gap forever."""
    acct = _account()
    with _nonce_lock:
        chain = w3.eth.get_transaction_count(acct.address, "pending")
        local = _nonce["next"]
        nonce = max(chain, local) if local is not None and time.time() - _nonce["at"] < 60 else chain
        tx.update({"from": acct.address, "nonce": nonce, "chainId": 4663})
        fee = w3.eth.get_block("latest").get("baseFeePerGas", 0)
        tx.setdefault("maxFeePerGas", int(fee * 2) + 10**7); tx.setdefault("maxPriorityFeePerGas", 10**7)
        tx.setdefault("gas", int(w3.eth.estimate_gas(tx) * 1.3))
        signed = acct.sign_transaction(tx)
        try:
            h = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
        except Exception as e:
            if "nonce" in str(e).lower(): _nonce["next"] = None          # out of step with the chain: re-read it next time
            raise
        _nonce["next"] = nonce + 1; _nonce["at"] = time.time()
    return h if h.startswith("0x") else "0x" + h


TRANSFER = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def receipt(txh: str) -> dict | None:
    """The mined receipt, or None while the transaction is still pending (or unknown to the node)."""
    from web3.exceptions import TransactionNotFound
    try:
        return w3.eth.get_transaction_receipt(txh if txh.startswith("0x") else "0x" + txh)
    except TransactionNotFound:
        return None                              # any other error (the RPC is down) raises: an outage must not read as "dropped"


def tokens_received(rcpt, token: str, owner: str) -> float:
    """Tokens `owner` received from `token` in one transaction: the ERC-20 Transfer logs, exact."""
    got = 0
    for log in rcpt["logs"]:
        topics = [t.hex().removeprefix("0x") if hasattr(t, "hex") else str(t).removeprefix("0x") for t in log["topics"]]
        if log["address"].lower() == token.lower() and len(topics) == 3 and topics[0] == TRANSFER and topics[2][-40:] == owner.lower()[2:]:
            data = log["data"]; data = data.hex() if hasattr(data, "hex") else str(data)
            got += int(data.removeprefix("0x") or "0", 16)
    return got / 1e18


def send_buy(curve_addr: str, eth: float, reserve_eth: float | None = None, slippage: float = 0.03, pair: str | None = None, eq: float = 1.0) -> str:
    """minTokensOut comes from the curve's own quote (eth_call), never from a tape estimate of the reserve.
    A non-ETH pair (USDG, stock tokens) pays in the pair token: the same buy() straight to the curve with value 0 (read off
    live NVDA-pair buys, 15.09.2026), the curve pulls the pair token, so it needs an allowance. `eth` and `reserve_eth` are
    ETH-equivalent (indexer Token.eq); minTokensOut comes from the curve formula, since buy() cannot be quoted by eth_call
    without holding the pair token."""
    if pair:
        raw = int(eth / eq * 1e18)            # the indexer decodes pair amounts as raw / 1e18 whatever the decimals, eq included
        held = balance_raw(pair, _account().address)
        if held < raw: raise RuntimeError(f"wallet holds {held} raw units of the pair token, the buy needs {raw}")
        ensure_allowance(pair, curve_addr, raw)
        min_out = int(quote_buy(reserve_eth, eth) * max(0.0, 1 - 2 * slippage) * 1e18) if reserve_eth is not None else 0
        return _send({"to": Web3.to_checksum_address(curve_addr), "value": 0, "data": buy_data(raw, min_out, _account().address)})
    wei = int(eth * 1e18)
    try:
        min_out = int(quote_buy_onchain(curve_addr, eth) * (1 - slippage) * 1e18)
    except Exception:
        min_out = int(quote_buy(reserve_eth, eth) * max(0.0, 1 - 2 * slippage) * 1e18) if reserve_eth is not None else 0
    return _send({"to": Web3.to_checksum_address(curve_addr), "value": wei, "data": buy_data(wei, min_out, _account().address)})


def balance_raw(token: str, owner: str) -> int:
    r = w3.eth.call({"to": Web3.to_checksum_address(token), "data": "0x70a08231" + _a(owner)})
    return int(r.hex(), 16) if len(r) else 0


def allowance(token: str, owner: str, spender: str) -> int:
    r = w3.eth.call({"to": Web3.to_checksum_address(token), "data": "0xdd62ed3e" + _a(owner) + _a(spender)})
    return int(r.hex(), 16) if len(r) else 0


def token_balance(token: str, owner: str) -> float:
    r = w3.eth.call({"to": Web3.to_checksum_address(token), "data": "0x70a08231" + _a(owner)})
    return (int(r.hex(), 16) if len(r) else 0) / 1e18


def ensure_allowance(token: str, spender: str, tokens_wei: int) -> str | None:
    """Tokens sit in our wallet (ERC-20); the curve pulls them on sell, so it needs an allowance. One max approve per token."""
    acct = _account()
    if allowance(token, acct.address, spender) >= tokens_wei: return None
    txh = _send({"to": Web3.to_checksum_address(token), "value": 0, "data": "0x095ea7b3" + _a(spender) + "f" * 64})
    w3.eth.wait_for_transaction_receipt(txh, timeout=120)
    return txh


def send_sell(curve_addr: str, tokens: float, reserve_eth: float | None = None, slippage: float = 0.03, token: str | None = None,
              pair: str | None = None, eq: float = 1.0) -> str:
    """minEthOut from the formula at the reserve implied by a fresh on-chain quote (sell() cannot be quoted without holding tokens).
    A non-ETH pair is paid out in the pair token (same sell(), read off live USDG-pair sells): the reserve cannot be backed out
    of a payable ETH quote there, so minOut comes from the formula at the tape reserve (ETH-equivalent) turned back into raw
    pair units."""
    wei = int(tokens * 1e18)
    if token:
        held = int(token_balance(token, _account().address) * 1e18)
        bal_wei = int(w3.eth.call({"to": Web3.to_checksum_address(token), "data": "0x70a08231" + _a(_account().address)}).hex(), 16)
        wei = min(wei, bal_wei)                      # never ask the curve for more than the wallet holds (float rounding)
        if wei <= 0: raise RuntimeError("no token balance to sell")
        tokens = wei / 1e18
        ensure_allowance(token, curve_addr, wei)
    me = _account().address
    if token:
        # the curve's own answer, fee and creator tax included: measured equal to the proceeds of a real sell (16.09.2026)
        try:
            min_out = int(quote_sell_onchain(curve_addr, wei, me) * max(0.0, 1 - slippage))
            return _send({"to": Web3.to_checksum_address(curve_addr), "value": 0, "data": sell_data(wei, min_out, me)})
        except Exception:
            pass
    if pair:
        min_out = int(quote_sell(reserve_eth, tokens) / eq * max(0.0, 1 - 2 * slippage) * 1e18) if reserve_eth is not None else 0
        return _send({"to": Web3.to_checksum_address(curve_addr), "value": 0, "data": sell_data(wei, min_out, me)})
    try:
        fee = curve_fee(curve_addr)
        r = reserve_from_quote(curve_addr, fee=fee)
        min_out = int(curve.sell(r, tokens, fee)[0] * max(0.0, 1 - slippage) * 1e18)
    except Exception:
        min_out = int(quote_sell(reserve_eth, tokens) * max(0.0, 1 - 2 * slippage) * 1e18) if reserve_eth is not None else 0
    return _send({"to": Web3.to_checksum_address(curve_addr), "value": 0, "data": sell_data(wei, min_out, _account().address)})


if __name__ == "__main__":
    import sys, sqlite3
    # smoke test: simulate a 0.01 ETH buy on the most recently traded live curve, from a rich address (no key needed)
    db = sqlite3.connect(os.path.join(os.path.dirname(__file__), "history.db"))
    cv, = db.execute("select curve from trades where kind='buy' order by block desc limit 1").fetchone()
    rich = sys.argv[1] if len(sys.argv) > 1 else "0x4d263394eae964af9294e6d584f1ae1b153b8b9f"
    print("curve", cv, "simulate 0.01 ETH buy from", rich[:10], "->", simulate_buy(cv, 0.01, rich))
