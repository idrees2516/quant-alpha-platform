"""Strategy interface + shared quote context.

A strategy sees everything known about one market at one instant and returns
a two-sided quote (or one side suppressed by risk limits).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from ...core.domain import BookLevel, OutcomeQuote
from ..microstructure import FillIntensity


@dataclass
class QuoteContext:
    venue: str
    market_id: str
    outcome_key: str
    tick: float
    fair: float                       # consensus-anchored fair probability
    book_mid: Optional[float]         # raw venue mid (may be None)
    micro_price: Optional[float]
    sigma: float                      # logit-vol per sqrt(hour)
    tau_h: float                      # quoting horizon remaining (hours)
    inventory: int                    # signed contracts
    max_inventory: int
    quote_size: int
    intensity: FillIntensity
    news_pressure: float = 0.0
    maker_fee_rate: float = 0.0       # per-fill fee paid by maker
    reward_params: Optional[Dict] = None
    extra: Dict[str, float] = field(default_factory=dict)

    def q_hat(self) -> float:
        """Inventory normalized to [-1, 1]."""
        return self.inventory / max(1, self.max_inventory)


@dataclass
class StrategyQuote:
    bid: Optional[float]
    ask: Optional[float]
    size: int
    meta: Dict[str, float] = field(default_factory=dict)

    def clipped(self, tick: float) -> "StrategyQuote":
        def cl(p: Optional[float]) -> Optional[float]:
            if p is None:
                return None
            return round(min(max(p, tick), 1 - tick) / tick) * tick
        return StrategyQuote(cl(self.bid), cl(self.ask), self.size, self.meta)


class MMStrategy:
    name = "base"

    def quotes(self, ctx: QuoteContext) -> StrategyQuote:
        raise NotImplementedError
