"""MM planner: which markets to make, on which venue, at what spread, with
how much risk — the capital allocation layer of the fee-earning setup.

Algorithm:
  1. Candidate set: every (venue, market, outcome) with a two-sided book,
     matched into a cluster (so we have a consensus fair value).
  2. Per candidate, measure: intensity (A,k) from depth, sigma from spread
     proxy, book reward score (Polymarket), then compute the
     earnings-optimal half-spread h* and the FULL earnings decomposition
     (rewards + spread capture - adverse selection).
  3. Risk per candidate: inventory cap x sigma_p x sqrt(tau_cap) — the USD
     1-sigma swing of a full position over the quoting horizon.
  4. Rank by earnings efficiency = net_usd_per_day / risk_usd. Allocate
     greedily under a total risk budget (risk parity across the top slice).
  5. Emit QuotePlans: exact resting bid/ask (tick-rounded, fee-floored),
     size, strategy, and the earnings decomposition for audit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.config import Config
from ..core.domain import QuotePlan, utcnow
from ..core.logging_setup import get_logger
from ..normalize.aggregate import AggregatedMarket
from .microstructure import (FillIntensity, calibrate_intensity, micro_price,
                             spread_vol_proxy)
from .rewards import (optimal_reward_spread, reward_params_for,
                      venue_earnings_summary)
from .strategies.adaptive import AdaptiveSpread
from .strategies.avellaneda_stoikov import AvellanedaStoikov
from .strategies.base import QuoteContext
from .strategies.glft import GLFTStrategy

log = get_logger("qa.planner")


@dataclass
class MarketCandidate:
    venue: str
    market_id: str
    title: str
    outcome_key: str
    outcome_index: int
    tick: float
    book_mid: float
    consensus: float
    intensity: FillIntensity
    sigma: float
    reward_params: Dict[str, float]
    net_usd_h: float = 0.0
    risk_usd: float = 0.0
    efficiency: float = 0.0
    plan: Optional[QuotePlan] = None


def strategy_for(venue: str, cfg: Config):
    if venue == "kalshi":
        return "glft", GLFTStrategy(gamma=cfg.mm.gamma)
    if venue == "predictit":
        return "avellaneda_stoikov", AvellanedaStoikov(gamma=cfg.mm.gamma)
    return "adaptive", AdaptiveSpread()


def plan_market_making(cfg: Config, aggs: List[AggregatedMarket],
                       risk_budget_usd: float = 500.0,
                       max_markets: int = 12) -> List[QuotePlan]:
    cands = _candidates(cfg, aggs)
    seen: set = set()
    deduped = []
    for c in cands:
        key = (c.venue, c.market_id, c.outcome_key)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    cands = [c for c in deduped if c.net_usd_h > 0.02]   # quote only positive-EV markets
    cands.sort(key=lambda c: -c.efficiency)
    plans: List[QuotePlan] = []
    budget = risk_budget_usd
    for c in cands:
        if budget <= 0 or len(plans) >= max_markets:
            break
        spend = min(c.risk_usd, budget)
        if spend <= 0:
            continue
        scale = spend / c.risk_usd if c.risk_usd > 0 else 1.0
        cands_q = max(1, int(cfg.mm.max_inventory * scale))
        plan = _quote_plan(cfg, c, cands_q)
        if plan:
            plans.append(plan)
            budget -= spend
    log.info("mm plan: %d candidates scored, %d markets allocated, "
             "risk deployed %.0f/%.0f USD", len(cands), len(plans),
             risk_budget_usd - budget, risk_budget_usd)
    return plans


def _candidates(cfg: Config, aggs: List[AggregatedMarket]) -> List[MarketCandidate]:
    out: List[MarketCandidate] = []
    for am in aggs:
        for comp in am.composites:
            for leg in comp.asks + comp.bids:
                if leg.venue in ("manifold", "metaculus"):
                    continue
                m = next((m for m in am.cluster.markets
                          if m.venue == leg.venue and m.market_id == leg.market_id), None)
                if m is None:
                    continue
                oq = m.find(leg.outcome_key)
                if oq is None or not (oq.bids and oq.asks):
                    continue
                mp = micro_price(oq.bids, oq.asks) or oq.mid
                fair = 0.6 * (mp or 0.5) + 0.4 * am.consensus[comp.outcome_index]
                intensity = calibrate_intensity(
                    oq, fair, 24.0,
                    FillIntensity(cfg.mm.intensity_A_prior, cfg.mm.intensity_k_prior))
                sigma = spread_vol_proxy(oq)
                rp = reward_params_for(m.venue, cfg.rewards, oq.bids, oq.asks, oq.mid)
                ctx = QuoteContext(
                    venue=m.venue, market_id=m.market_id, outcome_key=leg.outcome_key,
                    tick=oq.tick or 0.01, fair=fair, book_mid=oq.mid, micro_price=mp,
                    sigma=sigma, tau_h=min(cfg.mm.horizon_h, 24.0), inventory=0,
                    max_inventory=cfg.mm.max_inventory,
                    quote_size=cfg.mm.quote_size, intensity=intensity,
                    reward_params=rp)
                h_star = optimal_reward_spread(ctx)
                h = h_star if h_star else max(2 * ctx.tick, sigma * 0.5)
                summary = venue_earnings_summary(ctx, h)
                p = min(max(fair, 0.02), 0.98)
                sigma_p = sigma * p * (1 - p)
                risk_usd = cfg.mm.max_inventory * (
                    sigma_p * (min(cfg.mm.horizon_h, 24.0) ** 0.5) + 0.05)  # +5c jump floor
                net_day = summary["net_usd_h"] * 24
                out.append(MarketCandidate(
                    venue=m.venue, market_id=m.market_id, title=am.title,
                    outcome_key=leg.outcome_key, outcome_index=comp.outcome_index,
                    tick=oq.tick or 0.01, book_mid=oq.mid or fair, consensus=fair,
                    intensity=intensity, sigma=sigma, reward_params=rp,
                    net_usd_h=summary["net_usd_h"], risk_usd=risk_usd,
                    efficiency=(net_day / risk_usd) if risk_usd > 1 else 0.0))
    return out


def _quote_plan(cfg: Config, c: MarketCandidate, max_inv: int) -> Optional[QuotePlan]:
    name, strat = strategy_for(c.venue, cfg)
    ctx = QuoteContext(
        venue=c.venue, market_id=c.market_id, outcome_key=c.outcome_key,
        tick=c.tick, fair=c.consensus, book_mid=c.book_mid,
        micro_price=c.book_mid, sigma=c.sigma, tau_h=min(cfg.mm.horizon_h, 24.0),
        inventory=0, max_inventory=max_inv, quote_size=cfg.mm.quote_size,
        intensity=c.intensity, reward_params=c.reward_params)
    q = strat.quotes(ctx)
    if q.bid is None and q.ask is None:
        return None
    summary = venue_earnings_summary(ctx, ((q.ask - q.bid) / 2) if (q.bid is not None and q.ask is not None) else 0.0)
    return QuotePlan(
        venue=c.venue, market_id=c.market_id, outcome_key=c.outcome_key,
        bid=q.bid, ask=q.ask, size=cfg.mm.quote_size, strategy=name,
        fair=round(c.consensus, 4), inventory=0,
        expected_hourly_earnings=summary["net_usd_h"],
        components={
            **summary, "risk_usd": round(c.risk_usd, 2),
            "A": round(c.intensity.A, 3), "k": round(c.intensity.k, 1),
            "sigma": round(c.sigma, 4),
            **{k: v for k, v in q.meta.items()},
        })
