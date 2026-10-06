"""Inventory accounting + production risk guard.

The RiskGuard is shared by the live quote planner and the backtester, so the
exact same limits protect both. State is intentionally tiny and auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..core.config import MarketMakingConfig


@dataclass
class Inventory:
    contracts: int = 0            # signed: + long YES
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    rewards_earned: float = 0.0
    fills: int = 0
    buys: int = 0
    sells: int = 0

    def on_fill(self, side: str, price: float, qty: int, fee: float = 0.0) -> float:
        """Apply a fill; returns realized edge vs current avg cost."""
        self.fills += 1
        self.fees_paid += fee
        if side == "bid":                       # we bought
            total_cost = self.avg_cost * self.contracts + price * qty
            self.contracts += qty
            self.avg_cost = total_cost / self.contracts if self.contracts else 0.0
            self.buys += 1
            return (self.avg_cost - price) * qty
        else:                                    # we sold (from holdings only)
            edge = (price - self.avg_cost) * qty if self.contracts else 0.0
            self.contracts -= qty
            if self.contracts < 0:               # defensive: naked short forbidden
                self.contracts = 0
                self.avg_cost = 0.0
            elif self.contracts == 0:
                self.avg_cost = 0.0
            self.realized_pnl += edge
            self.sells += 1
            return edge

    @property
    def exposure(self) -> float:
        return abs(self.contracts)


@dataclass
class RiskState:
    killed: bool = False
    kill_reason: str = ""
    peak_equity: float = 0.0
    drawdown: float = 0.0


class RiskGuard:
    def __init__(self, cfg: MarketMakingConfig, news_widen_k: float = 1.5):
        self.cfg = cfg
        self.news_widen_k = news_widen_k
        self.state = RiskState()

    def update(self, equity: float, inventory: int, news_pressure: float = 0.0) -> None:
        if self.state.killed:
            return
        self.state.peak_equity = max(self.state.peak_equity, equity)
        self.state.drawdown = self.state.peak_equity - equity
        if self.state.drawdown >= self.cfg.kill_switch_drawdown:
            self.state.killed = True
            self.state.kill_reason = (
                f"drawdown {self.state.drawdown:.0f} >= {self.cfg.kill_switch_drawdown:.0f}")
        if abs(inventory) > self.cfg.max_inventory:
            self.state.killed = True
            self.state.kill_reason = f"inventory breach {inventory}"

    def quote_allowed(self, side: str, inventory: int) -> bool:
        if self.state.killed:
            return False
        cap = self.cfg.max_inventory
        if side == "bid" and inventory >= cap:
            return False
        if side == "ask" and inventory <= -cap:
            return False
        return True

    def news_multiplier(self, news_pressure: float) -> float:
        return 1.0 + min(2.0, self.news_widen_k * news_pressure)
