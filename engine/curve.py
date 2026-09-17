"""Pons bonding curve as a formula: constant product with virtual reserves, fitted to history.

tokens_sold(R) = T0 * R / (V0 + R), where R is net ETH in the curve (after fees).
Fill for a buy of e ETH at reserve R: tokens = sold(R+e) - sold(R).
Fill for a sell of q tokens at reserve R: find R' with sold(R') = sold(R) - q; eth = R - R'.
Parameters live in curve_params.json (written by the fit); defaults are the first fit.
"""
from __future__ import annotations
import json, os

FEE = 0.01
_p = {"V0": 2.0, "T0": 1.07e9}
try:
    _p.update(json.load(open(os.path.join(os.path.dirname(__file__), "curve_params.json"), encoding="utf-8")))
except Exception:
    pass
V0, T0 = float(_p["V0"]), float(_p["T0"])


def sold(R: float) -> float:
    return T0 * R / (V0 + R)


def reserve_for_sold(s: float) -> float:
    return V0 * s / (T0 - s) if s < T0 else float("inf")


def price(R: float) -> float:
    """Marginal price (ETH per token) at reserve R."""
    return (V0 + R) ** 2 / (T0 * V0)


def buy(R: float, eth_gross: float, extra_tax: float = 0.0) -> tuple[float, float]:
    """Spend eth_gross (fee and any snipe tax come out of it). Returns (tokens, new_R)."""
    e = eth_gross * (1 - FEE - extra_tax)
    return sold(R + e) - sold(R), R + e


def sell(R: float, tokens: float, fee: float = FEE) -> tuple[float, float]:
    """Sell tokens at reserve R. Returns (eth_net_of_fee, new_R). `fee` is the curve's own rate (1–5%)."""
    s = sold(R) - tokens
    if s <= 0: return R * (1 - fee), 0.0
    R2 = reserve_for_sold(s)
    return (R - R2) * (1 - fee), R2
