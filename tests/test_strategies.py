"""Strategies are data: normalize() fills every rule from BASE and drops keys the engine does not know."""
import json
import os

import strategies as S


def test_normalize_fills_and_drops():
    s = S.normalize({"id": "a", "name": "a", "entry": {"min_buyers": 7, "made_up_rule": 1}, "exit": {"sl_pct": 30}})
    assert s["entry"]["min_buyers"] == 7
    assert "made_up_rule" not in s["entry"]
    assert set(s["entry"]) == set(S.BASE["entry"])
    assert set(s["exit"]) == set(S.BASE["exit"])
    assert s["exit"]["sl_pct"] == 30


def test_house_strategies_are_complete():
    house = json.load(open(os.path.join(os.path.dirname(S.__file__), "strategies.default.json")))
    assert [s["id"] for s in house] == ["early", "smart", "gradrun", "trusted"]
    for s in house:
        n = S.normalize(s)
        assert n["entry"] == {**S.BASE["entry"], **s["entry"]}, s["id"]
        assert s.get("mode", "paper") == "paper", "a house strategy must never ship live"


def test_book_pnl_counts_every_closed_trade():
    b = S.Book(2.0)
    b.cash = 1.5                                   # 0.5 lost across trades the 300-row window no longer holds
    assert b.view({}, 0)["pnl_pct"] == -25.0
