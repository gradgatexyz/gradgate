"""Pons launchpad on Robinhood Chain: addresses and event decoders (verified on FOMOBRAIN, 2026-09-12)."""
from .rpc import addr

FACTORY = "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"
ROUTER = "0xe33e9e479df8802cb0866d5d05258bec4cf62948"
HOOK = "0xe5e702641ea86f4ae6cc3cdaed2b886f976be044"
LOCKER = "0x267444d099b10fb5ed7c3cc7b7c767adca574952"
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
FOMO_ROUTER = "0xb92fe925dc43a0ecde6c8b1a2709c170ec4fff4f"
LONG_FACTORY = "0x1eef016f22a943abc7dd11422edee9d235942104"

TOKEN_LAUNCHED = "0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607"   # (token, curve, creator) indexed; data: ?, ?, threshold
POOL_GRADUATED = "0x0a44ef75df69c534f43cd6c1aa3ef8983065fe5fe79ef9e79f6494e6f258c259"   # token indexed; data: ?, tokensToPool, ethRaised
CURVE_BUY = "0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455"       # (caller, buyer); data: ethIn, tokensOut, fee, fee
CURVE_SELL = "0x8113d738abdcb6b38357e9d53a54a7157861a09031b453651f0fe7fe151f59df"      # (seller, seller); data: tokensIn, ethOut, fee, fee
SNIPE_TAX = "0x3bc39a5562b28f5fe8f36cecabfbaa12bb969acf05717994709225fc412a9934"       # (payer); data: amount
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
LONG_LAUNCH = "0x04c10fe2cc69507cbff4c84fc99414ca507db49d5c39eff84b67ea319f43ccf0"     # (token, creator)

SUPPLY = 10**27           # 1e9 * 1e18
GRAD_ETH = 4.2
SYSTEM = {FACTORY, ROUTER, HOOK, LOCKER, POOL_MANAGER, FOMO_ROUTER, "0x" + "0" * 40}


def words(data: str) -> list[int]:
    d = data[2:]
    return [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]


def decode_launch(l: dict) -> dict:
    w = words(l["data"])
    return {"token": addr(l["topics"][1]), "curve": addr(l["topics"][2]), "creator": addr(l["topics"][3]),
            "block": int(l["blockNumber"], 16), "tx": l["transactionHash"], "threshold_eth": (w[2] if len(w) > 2 else 0) / 1e18}


def decode_buy(l: dict) -> dict:
    w = words(l["data"])
    return {"kind": "buy", "block": int(l["blockNumber"], 16), "tx": l["transactionHash"], "idx": int(l["logIndex"], 16),
            "caller": addr(l["topics"][1]), "wallet": addr(l["topics"][2]), "eth": w[0] / 1e18, "tokens": w[1] / 1e18, "fee": (w[2] + w[3]) / 1e18}


def decode_sell(l: dict) -> dict:
    w = words(l["data"])
    return {"kind": "sell", "block": int(l["blockNumber"], 16), "tx": l["transactionHash"], "idx": int(l["logIndex"], 16),
            "wallet": addr(l["topics"][1]), "tokens": w[0] / 1e18, "eth": w[1] / 1e18, "fee": (w[2] + w[3]) / 1e18}
