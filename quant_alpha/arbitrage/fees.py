"""Per-venue fee models — the backbone of net-edge arithmetic.

Every detector and the MM planner price fees identically through these models,
so arb edges and MM earnings are computed on the same footing.

Venue economics (defaults, parameterized in config):
  * Kalshi    — maker fee 0, taker fee = ceil(0.07 * P * 100)/100 per contract
                (order-level rounding approximated per contract, conservative).
  * Polymarket— trading fees 0 (CTF exchange); liquidity *rewards* for makers
                (modeled in market_making/rewards.py, not here).
  * PredictIt — no trade fee, 10% fee on net winnings per market at settlement.
  * Manifold / Metaculus — forecast only, non-tradeable (infinite fee marker).
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Protocol


class FeeModel(Protocol):
    venue: str

    def taker_buy_cost(self, price: float, qty: float = 1.0) -> float: ...
    def maker_sell_proceeds(self, price: float, qty: float = 1.0) -> float: ...
    def settlement_payout(self, entry_price: float, qty: float = 1.0,
                          won: bool = True) -> float: ...


class ZeroFee:
    """Polymarket-style: no trading fees, $1 payout per winning contract."""
    venue = "polymarket"

    def taker_buy_cost(self, price: float, qty: float = 1.0) -> float:
        return price

    def maker_sell_proceeds(self, price: float, qty: float = 1.0) -> float:
        return price

    def settlement_payout(self, entry_price: float, qty: float = 1.0,
                          won: bool = True) -> float:
        return qty if won else 0.0


class KalshiFee:
    """Maker 0 / taker 0.07 * price * contracts, rounded up to the next cent."""
    venue = "kalshi"

    def __init__(self, rate: float = 0.07, maker_fee: float = 0.0):
        self.rate, self.maker_fee = rate, maker_fee

    def taker_fee(self, price: float, qty: float = 1.0) -> float:
        if price <= 0:
            return 0.0
        raw = self.rate * price * qty
        return math.ceil(raw * 100) / 100 if qty == 1 else raw  # per-contract ceil

    def taker_buy_cost(self, price: float, qty: float = 1.0) -> float:
        return price + self.taker_fee(price, qty)

    def maker_sell_proceeds(self, price: float, qty: float = 1.0) -> float:
        return price - self.maker_fee * price * qty

    def settlement_payout(self, entry_price: float, qty: float = 1.0,
                          won: bool = True) -> float:
        return qty if won else 0.0


class ProfitFeeOnSettlement:
    """PredictIt-style: fee charged on net winnings of the winning side only."""
    venue = "predictit"

    def __init__(self, profit_rate: float = 0.10):
        self.profit_rate = profit_rate

    def taker_buy_cost(self, price: float, qty: float = 1.0) -> float:
        return price

    def maker_sell_proceeds(self, price: float, qty: float = 1.0) -> float:
        return price

    def settlement_payout(self, entry_price: float, qty: float = 1.0,
                          won: bool = True) -> float:
        if not won:
            return 0.0
        gross_profit = max(0.0, 1.0 - entry_price) * qty
        return qty - self.profit_rate * gross_profit


class NonTradable:
    """Forecast-only venues: cannot execute, marked with prohibitive fees."""
    venue = "forecast-only"

    def taker_buy_cost(self, price: float, qty: float = 1.0) -> float:
        return 2.0   # never optimal

    def maker_sell_proceeds(self, price: float, qty: float = 1.0) -> float:
        return 0.0

    def settlement_payout(self, entry_price: float, qty: float = 1.0,
                          won: bool = True) -> float:
        return 0.0


def fee_models(kalshi_rate: float = 0.07, predictit_profit_rate: float = 0.10
               ) -> Dict[str, FeeModel]:
    return {
        "polymarket": ZeroFee(),
        "kalshi": KalshiFee(kalshi_rate),
        "predictit": ProfitFeeOnSettlement(predictit_profit_rate),
        "manifold": NonTradable(),
        "metaculus": NonTradable(),
    }
