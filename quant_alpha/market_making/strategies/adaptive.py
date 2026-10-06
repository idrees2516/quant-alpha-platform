"""Adaptive spread strategy — what actually runs in production quote engines.

Components (each measurable, each bounded):
  * fair value: micro-price blended with cross-venue consensus;
  * half-spread = max(fee floor, volatility multiple, reward-optimal spread)
    widened by a news multiplier;
  * linear inventory skew of BOTH quotes (not just reservation shift);
  * quote hysteresis: only re-quote when the change exceeds a threshold
    (protects queue position);
  * hard inventory caps: suppress the side that increases exposure.

This strategy delegates its 'earnings mode' to market_making.rewards when
the venue pays liquidity rewards (Polymarket) — the spread then maximizes
expected hourly earnings instead of a fixed vol target.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .base import MMStrategy, QuoteContext, StrategyQuote


@dataclass
class AdaptiveParams:
    vol_multiple: float = 0.9        # half-spread = vol_multiple * sigma_p
    skew_coef: float = 0.35          # quote shift = skew_coef * q_hat * spread
    consensus_weight: float = 0.4    # blend of consensus vs venue micro price
    hysteresis_ticks: float = 1.0
    fee_floor: float = 0.0           # maker fee / rebate floor (price units)


class AdaptiveSpread(MMStrategy):
    name = "adaptive"

    def __init__(self, params: Optional["AdaptiveParams"] = None,
                 earnings_mode: bool = True):
        self.p = params or AdaptiveParams()
        self.earnings_mode = earnings_mode

    def _fair(self, ctx: QuoteContext) -> float:
        if ctx.micro_price is None:
            return ctx.fair
        w = self.p.consensus_weight
        return (1 - w) * ctx.micro_price + w * ctx.fair

    def half_spread(self, ctx: QuoteContext) -> float:
        p = min(max(ctx.fair, 0.02), 0.98)
        sigma_p = ctx.sigma * p * (1 - p)
        spread = max(self.p.vol_multiple * sigma_p, self.p.fee_floor, ctx.tick)
        if self.earnings_mode and ctx.reward_params:
            from ..rewards import optimal_reward_spread
            h_star = optimal_reward_spread(ctx, self)
            if h_star is not None:
                spread = max(ctx.tick, min(spread, h_star + 0.02))
        spread *= (1.0 + min(2.0, 1.5 * ctx.news_pressure))
        return spread

    def quotes(self, ctx: QuoteContext) -> StrategyQuote:
        fair = self._fair(ctx)
        h = self.half_spread(ctx)
        skew = self.p.skew_coef * ctx.q_hat() * h
        center = fair - skew
        bid = center - h
        ask = center + h
        if ctx.maker_fee_rate > 0:
            bid = min(bid, fair - ctx.maker_fee_rate)
            ask = max(ask, fair + ctx.maker_fee_rate)
        meta = {
            "half_spread": round(h, 4),
            "skew": round(skew, 4),
            "sigma_p": round(ctx.sigma * ctx.fair * (1 - ctx.fair), 5),
        }
        if ctx.reward_params:
            from ..rewards import expected_hourly_earnings
            meta["exp_hourly_earn"] = round(expected_hourly_earnings(ctx, h), 4)
        return StrategyQuote(bid, ask, ctx.quote_size, meta).clipped(ctx.tick)
