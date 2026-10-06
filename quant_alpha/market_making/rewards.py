"""Fee-earning engine: how much money a maker actually makes, per hour,
per market — and which spread maximizes it.

Income sources, per venue:
  * Polymarket — liquidity rewards. Approximation of the live program:
    each epoch, a daily USD pool is split by score
        score(order) = min(size, size_cap) * (1 - spread/max_spread)^power
    for resting orders within max_spread of the midpoint (both sides
    scored independently). We can measure the *current* book's score from
    the composite book, so our expected share is computable live:
        reward_rate = R_day/24 * our_score / (book_score + our_score)
  * Kalshi — makers trade at zero fees; earnings are pure spread capture
    (plus maker programs not modeled) minus taker fee paid when we
    defensively cross.
  * PredictIt — no resting-maker economics beyond spread capture, but a
    10% profit fee at settlement raises the required edge.

Costs, modeled explicitly:
  * adverse selection: a fraction of fills is informed; the mid moves
    against us by sigma_p * sqrt(dt) on those fills;
  * maker/taker fees per venue fee model;
  * inventory carrying (variance) cost, used for capital allocation.

expected_hourly_earnings(ctx, half_spread) = reward_rate
    + lambda_bid * (fair - bid) + lambda_ask * (ask - fair)     [spread capture]
    - adverse_selection_cost                                     [informed flow]
    - fees
optimal_reward_spread(ctx) maximizes it numerically (golden-section over
[tick, max_spread]).
"""
from __future__ import annotations

import math
from typing import Dict, Optional

from ..core.config import RewardsConfig
from ..core.domain import BookLevel
from ..core.logging_setup import get_logger
from .microstructure import book_score
from .strategies.base import QuoteContext

log = get_logger("qa.rewards")


def _sigma_p(ctx: QuoteContext) -> float:
    p = min(max(ctx.fair, 0.02), 0.98)
    return ctx.sigma * p * (1 - p)


def reward_rate(ctx: QuoteContext, half_spread: float) -> float:
    """USD/hour of Polymarket-style liquidity rewards at our quote."""
    rp = ctx.reward_params or {}
    if not rp.get("enabled"):
        return 0.0
    pool_per_hour = rp.get("pool_usd_per_hour", 0.0)
    if pool_per_hour <= 0:
        return 0.0
    max_spread = rp.get("max_spread", 0.03)
    size_cap = rp.get("size_cap", 300.0)
    power = rp.get("spread_power", 2.0)
    if half_spread > max_spread:
        return 0.0
    our_score = min(ctx.quote_size, size_cap) * (1 - half_spread / max_spread) ** power
    book_s = rp.get("book_score", 0.0)   # pre-measured per side, same units
    total = book_s + our_score
    return pool_per_hour * our_score / total if total > 0 else 0.0


def spread_capture_rate(ctx: QuoteContext, half_spread: float) -> float:
    """USD/hour of expected spread income (both sides, uninformed edge)."""
    q = ctx.quote_size
    lam_b = ctx.intensity.rate(half_spread)       # fills/hour per side
    lam_a = ctx.intensity.rate(half_spread)
    edge = half_spread - ctx.maker_fee_rate
    return (lam_b + lam_a) * max(edge, 0.0) * q


def adverse_selection_rate(ctx: QuoteContext, half_spread: float,
                           adverse_frac: float = 0.35) -> float:
    """USD/hour lost to informed flow: informed fills realize a loss of
    roughly sigma_p * sqrt(hour) * jump_multiplier against our quote."""
    sigma_p = _sigma_p(ctx)
    lam = ctx.intensity.rate(half_spread)
    informed = lam * adverse_frac * 2          # both sides
    loss_per_fill = sigma_p * ctx.quote_size * 1.6   # 1.6 = informed jump factor
    return informed * loss_per_fill


def expected_hourly_earnings(ctx: QuoteContext, half_spread: float,
                             adverse_frac: float = 0.35) -> float:
    return (reward_rate(ctx, half_spread)
            + spread_capture_rate(ctx, half_spread)
            - adverse_selection_rate(ctx, half_spread, adverse_frac))


def optimal_reward_spread(ctx: QuoteContext, strategy=None) -> Optional[float]:
    """Golden-section search of the earnings-maximizing half-spread."""
    rp = ctx.reward_params or {}
    lo = ctx.tick
    hi = rp.get("max_spread", 0.03)
    if hi <= lo:
        return lo
    if not rp.get("enabled"):
        hi = min(max(6 * ctx.tick, 5 * _sigma_p(ctx) * 0.9), 0.15)
        if hi <= lo:
            return lo
    gr = (math.sqrt(5) - 1) / 2

    def f(h: float) -> float:
        return expected_hourly_earnings(ctx, h, 0.35)

    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    for _ in range(40):
        if f(c) < f(d):
            a = c
        else:
            b = d
        c, d = b - gr * (b - a), a + gr * (b - a)
        if b - a < ctx.tick:
            break
    h_star = round((a + b) / 2 / ctx.tick) * ctx.tick
    return min(max(h_star, lo), hi)


# ------------------------------------------------------------- context ---
def reward_params_for(venue: str, cfg: RewardsConfig,
                      bids=(), asks=(), mid: Optional[float] = None,
                      pool_usd_day: Optional[float] = None) -> Dict[str, float]:
    """Build ctx.reward_params for a venue, measuring the live book score."""
    if venue == "polymarket" and cfg.polymarket_enabled:
        pool = pool_usd_day if pool_usd_day is not None else cfg.poly_daily_pool_usd
        book_s = 0.0
        if mid is not None and (bids or asks):
            bs, as_ = book_score(list(bids), list(asks), mid,
                                 cfg.poly_max_spread, cfg.poly_size_cap,
                                 cfg.poly_spread_power)
            book_s = max(bs, as_)
        return {
            "enabled": 1.0,
            "pool_usd_per_hour": pool / 24.0,
            "max_spread": cfg.poly_max_spread,
            "size_cap": cfg.poly_size_cap,
            "spread_power": cfg.poly_spread_power,
            "book_score": book_s,
        }
    # Kalshi / others: no rewards, only spread economics (kept for symmetry)
    return {"enabled": 0.0,
            "pool_usd_per_hour": 0.0,
            "max_spread": 0.15,
            "size_cap": 0.0,
            "spread_power": 2.0,
            "book_score": 0.0}


def venue_earnings_summary(ctx: QuoteContext, half_spread: float) -> Dict[str, float]:
    """Full decomposition — used by the planner's capital allocation."""
    return {
        "rewards_usd_h": round(reward_rate(ctx, half_spread), 4),
        "spread_capture_usd_h": round(spread_capture_rate(ctx, half_spread), 4),
        "adverse_cost_usd_h": round(adverse_selection_rate(ctx, half_spread), 4),
        "net_usd_h": round(expected_hourly_earnings(ctx, half_spread), 4),
        "fill_rate_h": round(2 * ctx.intensity.rate(half_spread), 3),
    }
