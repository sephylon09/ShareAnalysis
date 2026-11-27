"""Paper-fill math. Every fill is $0.01 worse than the market price, as requested:

  LONG : buy  at price + 0.01, sell  at price - 0.01
  SHORT: sell at price - 0.01, cover at price + 0.01
"""
from __future__ import annotations

import math


def entry_fill(side: str, market_price: float, slip: float) -> float:
    return round(market_price + slip if side == "LONG" else market_price - slip, 4)


def exit_fill(side: str, market_price: float, slip: float) -> float:
    return round(market_price - slip if side == "LONG" else market_price + slip, 4)


def shares_for(budget_usd: float, price: float) -> int:
    return max(1, math.floor(budget_usd / price))


def pnl(side: str, entry: float, exit_: float, shares: int) -> tuple[float, float]:
    """(pnl_usd, pnl_pct) for a round trip at the given fill prices."""
    per_share = exit_ - entry if side == "LONG" else entry - exit_
    return round(per_share * shares, 2), round(per_share / entry * 100, 3)


def live_pnl_pct(side: str, entry: float, market_price: float, slip: float) -> float:
    """What we'd lock in if we exited right now (exit slippage included)."""
    return pnl(side, entry, exit_fill(side, market_price, slip), 1)[1]


def price_for_pnl(side: str, entry: float, pnl_pct: float, slip: float) -> float:
    """Market price at which live_pnl_pct would equal pnl_pct (used to show the plan as prices)."""
    if side == "LONG":
        return round(entry * (1 + pnl_pct / 100) + slip, 2)
    return round(entry * (1 - pnl_pct / 100) - slip, 2)
