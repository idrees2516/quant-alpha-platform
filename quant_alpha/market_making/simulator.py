"""MM fill simulator: honest microstructure simulation for backtests.

Per hourly step:
  1. the true fair (venue mid) follows the (fixture or recorded) series —
     including news jumps;
  2. the strategy quotes from its OWN information set (book + consensus);
  3. fill arrivals ~ Poisson(lambda(half-spread)) per side;
  4. ADVERSE SELECTION: with probability adverse_frac per fill, the fill
     was informed — the true mid moves sigma_p*jump_factor against us and
     we only realize edge - adverse_cost;
  5. rewards accrue per hour for reward-paying venues (Polymarket model);
  6. inventory and cash evolve; equity is marked to the true mid.

The simulator deliberately does NOT let the strategy see the future mid —
quotes are computed from the book state only (consensus == current mid
blended with the book micro-price).
"""
from __future__ import annotations
import math

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from ..core.config import MarketMakingConfig
from ..core.domain import BookLevel, OutcomeQuote, utcnow
from ..core.logging_setup import get_logger
from .inventory import Inventory, RiskGuard
from .microstructure import (FillIntensity, calibrate_intensity, micro_price,
                             sigmoid, logit)
from .rewards import reward_rate
from .strategies.base import MMStrategy, QuoteContext, StrategyQuote

log = get_logger("qa.mmsim")


@dataclass
class SimStep:
    t_h: float
    mid: float
    bid: Optional[float]
    ask: Optional[float]
    inventory: int
    equity: float
    fills: int
    reward_usd: float


@dataclass
class SimResult:
    final_equity: float
    pnl: float
    pnl_markout: float
    rewards_usd: float
    spread_capture_usd: float
    adverse_cost_usd: float
    fees_usd: float
    max_drawdown: float
    avg_abs_inventory: float
    total_fills: int
    sharpe: float
    steps: List[SimStep] = field(default_factory=list)

    def summary(self) -> Dict[str, float]:
        return {
            "pnl_usd": round(self.pnl, 2),
            "rewards_usd": round(self.rewards_usd, 2),
            "spread_capture_usd": round(self.spread_capture_usd, 2),
            "adverse_cost_usd": round(self.adverse_cost_usd, 2),
            "fees_usd": round(self.fees_usd, 2),
            "max_drawdown_usd": round(self.max_drawdown, 2),
            "avg_abs_inventory": round(self.avg_abs_inventory, 1),
            "total_fills": self.total_fills,
            "sharpe": round(self.sharpe, 2),
            "final_equity_usd": round(self.final_equity, 2),
        }


def simulate_market_making(
    strategy: MMStrategy,
    series: List[OutcomeQuote],
    *,
    venue: str,
    market_id: str,
    outcome_key: str,
    quote_size: int = 25,
    max_inventory: int = 120,
    gamma: float = 0.8,
    start_cash: float = 2_000.0,
    step_h: float = 1.0,
    seed: int = 7,
    adverse_frac: float = 0.35,
    maker_fee_rate: float = 0.0,
    reward_params: Optional[Dict[str, float]] = None,
    init_intensity: Optional[FillIntensity] = None,
    resolve: Optional[bool] = None,
) -> SimResult:
    """Run a strategy over a book series; settle at the final mid (or the
    supplied resolve flag) and return full PnL attribution."""
    rng = random.Random(seed)
    inv = Inventory()
    guard = RiskGuard(MarketMakingConfig(max_inventory=max_inventory,
                                         kill_switch_drawdown=1e9))
    cash = start_cash
    spread_capture = adverse_cost = rewards_total = 0.0
    steps: List[SimStep] = []
    equities: List[float] = []
    sigma = 0.08
    intensity = init_intensity or FillIntensity()
    book_vol_ewma = 0.0064
    prev_mid: Optional[float] = None

    for t_i, oq in enumerate(series):
        t_h = t_i * step_h
        mid = oq.mid
        if mid is None:
            continue
        # ---- strategy information set: current book only
        mp = micro_price(oq.bids, oq.asks) if (oq.bids and oq.asks) else mid
        fair = 0.6 * (mp if mp else mid) + 0.4 * mid
        intensity = calibrate_intensity(oq, fair, max(step_h, 24.0), intensity)
        if prev_mid is not None:
            dx = abs(logit(mid) - logit(prev_mid))
            book_vol_ewma = 0.94 * book_vol_ewma + 0.06 * dx * dx
        sigma = min(max(math.sqrt(book_vol_ewma) if book_vol_ewma > 0 else 0.08, 0.02), 1.5)
        prev_mid = mid

        ctx = QuoteContext(
            venue=venue, market_id=market_id, outcome_key=outcome_key,
            tick=oq.tick or 0.01, fair=fair, book_mid=mid, micro_price=mp,
            sigma=sigma, tau_h=max(1.0, len(series) - t_i) * step_h,
            inventory=inv.contracts, max_inventory=max_inventory,
            quote_size=quote_size, intensity=intensity,
            maker_fee_rate=maker_fee_rate, reward_params=reward_params)

        q = strategy.quotes(ctx)
        q = guard_filter(q, guard, inv.contracts)

        # ---- fills
        # Fill model: intensity decays with distance from the COMPETING touch,
        # not from fair — quoting behind a deep touch rarely fills; improving
        # the touch puts us at the front of the queue (rate = A, queue
        # penalty only for joining a very large resting size).
        fills = 0
        book_best_bid = oq.bids[0].price if oq.bids else None
        book_best_ask = oq.asks[0].price if oq.asks else None
        tick = oq.tick or 0.01

        def _eff_distance(our_px: float, touch: Optional[float], fair: float,
                          side: str) -> float:
            if touch is None:
                return max(0.0, (fair - our_px) if side == "bid" else (our_px - fair))
            if side == "bid":
                behind = max(0.0, touch - our_px)   # >0 if we quote below touch
                return behind
            behind = max(0.0, our_px - touch)
            return behind

        # Gaussian sweep: a quote behind the touch fills only when the mid
        # actually travels to our level within the step (sigma_sweep = the
        # hourly mid SD); quoting at/inside the touch joins/improves the
        # queue instead.
        p_fair = min(max(fair, 0.02), 0.98)
        sigma_sweep = max(sigma * p_fair * (1 - p_fair) * math.sqrt(step_h), 1.5 * tick)

        def _sweep_rate(d_behind: float, queue_pen: float) -> float:
            if d_behind <= 0:            # at/inside the touch
                return intensity.A * queue_pen
            return 0.5 * intensity.A * math.exp(
                -d_behind ** 2 / (2.0 * sigma_sweep ** 2)) * queue_pen

        if q.bid is not None and guard.quote_allowed("bid", inv.contracts):
            d = _eff_distance(q.bid, book_best_bid, fair, "bid")
            join_factor_b = 800.0 / (800.0 + (oq.bids[0].size if oq.bids else 0.0))
            lam = _sweep_rate(d, join_factor_b)
            n = _poisson(rng, lam * step_h)
            headroom = max(0, (max_inventory - inv.contracts) // quote_size)
            for _ in range(min(n, headroom)):
                informed = rng.random() < adverse_frac
                px = q.bid
                edge = fair - px
                if informed:
                    realized = edge - 1.6 * sigma * fair * (1 - fair)
                    adverse_cost += max(0.0, edge - realized) * quote_size
                    edge = realized
                inv.on_fill("bid", px, quote_size)
                cash -= px * quote_size
                spread_capture += max(edge, -0.5) * quote_size
                fills += 1
        if q.ask is not None and guard.quote_allowed("ask", inv.contracts):
            d = _eff_distance(q.ask, book_best_ask, fair, "ask")
            join_factor_a = 800.0 / (800.0 + (oq.asks[0].size if oq.asks else 0.0))
            lam = _sweep_rate(d, join_factor_a)
            n = _poisson(rng, lam * step_h)
            # venue reality: no naked shorting of YES on prediction-market CLOBs —
            # the ask side can only sell inventory we actually hold (going short
            # is done by buying NO, modeled as a separate quote line).
            headroom = max(0, inv.contracts // quote_size)
            for _ in range(min(n, headroom)):
                informed = rng.random() < adverse_frac
                px = q.ask
                edge = px - fair
                if informed:
                    realized = edge - 1.6 * sigma * fair * (1 - fair)
                    adverse_cost += max(0.0, edge - realized) * quote_size
                    edge = realized
                inv.on_fill("ask", px, quote_size)
                cash += px * quote_size
                spread_capture += max(edge, -0.5) * quote_size
                fills += 1

        # ---- rewards
        if reward_params and reward_params.get("enabled"):
            h = ((q.ask - q.bid) / 2) if (q.bid is not None and q.ask is not None) else None
            if h is not None:
                r = reward_rate(ctx, h)
                inv.rewards_earned += r
                rewards_total += r

        equity = cash + inv.contracts * mid
        guard.update(equity, inv.contracts)
        equities.append(equity)
        steps.append(SimStep(t_h, mid, q.bid, q.ask, inv.contracts, equity,
                             fills, rewards_total))

    # ---- settlement
    final_mid = series[-1].mid if series else 0.5
    won = resolve if resolve is not None else (rng.random() < final_mid)
    settle = inv.contracts * (1.0 if won else 0.0)
    final_equity = cash + settle

    pnl = final_equity - start_cash
    dd = _max_drawdown(equities)
    sharpe = _sharpe([e for e in equities])
    return SimResult(
        final_equity=final_equity, pnl=pnl, pnl_markout=equities[-1] - start_cash if equities else 0.0,
        rewards_usd=rewards_total, spread_capture_usd=spread_capture,
        adverse_cost_usd=adverse_cost, fees_usd=inv.fees_paid,
        max_drawdown=dd, avg_abs_inventory=sum(abs(s.inventory) for s in steps) / max(1, len(steps)),
        total_fills=inv.fills, sharpe=sharpe, steps=steps)


def guard_filter(q: StrategyQuote, guard: RiskGuard, inv: int) -> StrategyQuote:
    bid = q.bid if guard.quote_allowed("bid", inv) else None
    ask = q.ask if guard.quote_allowed("ask", inv) else None
    return StrategyQuote(bid, ask, q.size, q.meta)


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth for small lambda (lambda < ~10 here)."""
    if lam <= 0:
        return 0
    if lam > 15:
        return max(0, round(rng.gauss(lam, lam ** 0.5)))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1
        if k > 60:
            return k - 1


def _max_drawdown(equities: List[float]) -> float:
    peak, dd = -1e18, 0.0
    for e in equities:
        peak = max(peak, e)
        dd = max(dd, peak - e)
    return dd


def _sharpe(equities: List[float]) -> float:
    if len(equities) < 3:
        return 0.0
    rets = [equities[i] - equities[i - 1] for i in range(1, len(equities))]
    mu = sum(rets) / len(rets)
    if not rets:
        return 0.0
    var = sum((r - mu) ** 2 for r in rets) / len(rets)
    sd = var ** 0.5
    if sd < 1e-9:
        return 0.0
    return mu / sd * (24 * 365) ** 0.5   # annualized from hourly marks


