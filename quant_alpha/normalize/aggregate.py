"""Aggregation: composite cross-venue books + consensus fair probabilities.

CompositeBook — for one outcome of a matched cluster, the venue-tagged union
of resting liquidity with fee-adjusted effective prices, so downstream arb
and MM both work on one coherent object.

Consensus — liquidity- and calibration-weighted blend of venue mids, de-vigged
sports odds and forecast-venue probabilities; normalized over the outcome
partition. This is the MM 'fair value' anchor and the alpha benchmark.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..arbitrage.fees import FeeModel, fee_models
from ..core.domain import (BookLevel, MarketQuote, MatchedCluster,
                           OutcomeQuote, SportsQuote)
from ..normalize.resolvers import FORECAST_VENUES
from .resolvers import jaccard, norm_title


@dataclass
class VenueLeg:
    venue: str
    market_id: str
    outcome_key: str
    price: float          # raw venue price
    size: float           # displayed liquidity
    eff_price: float      # price after taker fees (buy) / maker net (sell)
    book: Tuple[BookLevel, ...] = ()


@dataclass
class CompositeBook:
    cluster_id: str
    outcome_index: int
    label: str
    bids: List[VenueLeg] = field(default_factory=list)   # sorted by eff price desc
    asks: List[VenueLeg] = field(default_factory=list)   # sorted by eff price asc

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def best_eff_ask(self) -> Optional[float]:
        return self.asks[0].eff_price if self.asks else None

    @property
    def best_eff_bid(self) -> Optional[float]:
        """Net proceeds of selling (best bid minus sell-side fees)."""
        return self.bids[0].eff_price if self.bids else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    def depth_at(self, side: str, limit: float) -> float:
        legs = self.asks if side == "ask" else self.bids
        total = 0.0
        for leg in legs:
            if side == "ask" and leg.eff_price <= limit:
                total += leg.size
            elif side == "bid" and leg.eff_price >= limit:
                total += leg.size
        return total


@dataclass
class AggregatedMarket:
    cluster: MatchedCluster
    composites: List[CompositeBook] = field(default_factory=list)   # per outcome
    consensus: List[float] = field(default_factory=list)            # per outcome
    news_pressure: float = 0.0
    venue_views: Dict[str, List[float]] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.cluster.title

    @property
    def binary(self) -> bool:
        return len(self.consensus) == 2


# ------------------------------------------------------------- sports ----
def devig_h2h(odds: Tuple[Tuple[str, float], ...], method: str = "multiplicative"
              ) -> Dict[str, float]:
    """De-vig a 2-way (or n-way) book to true probabilities."""
    implied = {label: 1.0 / o for label, o in odds if o > 1}
    total = sum(implied.values())
    if total <= 0:
        return {}
    if method == "multiplicative" or len(implied) != 2:
        return {k: v / total for k, v in implied.items()}
    # Shin's method (2-way): solve p1 = z*... fixed point
    a, b = list(implied.values())
    lo, hi = 0.0, 0.999
    for _ in range(60):
        z = 0.5 * (lo + hi)
        def shin_prob(x: float, zz: float) -> float:
            r = zz * (zz * zz + 4 * x * (1 - zz)) ** 0.5
            den = 2 * (1 - zz)
            return (r + zz * (1 - 2 * x)) / den if den else x
        p1, p2 = shin_prob(a, z), shin_prob(b, z)
        if p1 + p2 > 1.0:
            lo = z
        else:
            hi = z
    z = 0.5 * (lo + hi)
    p1 = shin_prob(a, z)
    return {list(implied.keys())[0]: p1, list(implied.keys())[1]: 1.0 - p1}


def sports_implied(sports: List[SportsQuote]) -> Dict[str, Dict[str, float]]:
    """bookmaker -> {label: de-vigged probability}."""
    out: Dict[str, Dict[str, float]] = {}
    for sq in sports:
        probs = devig_h2h(sq.outcomes)
        if probs:
            out[sq.bookmaker] = probs
    return out


# ---------------------------------------------------------- composite ----
def build_composites(cluster: MatchedCluster,
                     fees: Optional[Dict[str, FeeModel]] = None) -> List[CompositeBook]:
    fees = fees or fee_models()
    composites: List[CompositeBook] = []
    for idx, mo in enumerate(cluster.outcomes):
        comp = CompositeBook(cluster_id=cluster.cluster_id, outcome_index=idx, label=mo.label)
        for m in cluster.markets:
            key = mo.keys_by_venue.get(m.venue)
            if not key:
                continue
            oq = m.find(key)
            if oq is None:
                continue
            fm = fees.get(m.venue)
            if oq.best_ask is not None and oq.best_ask < 1.0:
                eff = fm.taker_buy_cost(oq.best_ask) if fm else oq.best_ask
                if eff < 1.0:
                    comp.asks.append(VenueLeg(m.venue, m.market_id, key,
                                              oq.best_ask, oq.ask_size or 0, eff,
                                              oq.asks))
            if oq.best_bid is not None and oq.best_bid > 0.0:
                eff = fm.maker_sell_proceeds(oq.best_bid) if fm else oq.best_bid
                if eff > 0.0:
                    comp.bids.append(VenueLeg(m.venue, m.market_id, key,
                                              oq.best_bid, oq.bid_size or 0, eff,
                                              oq.bids))
        comp.bids.sort(key=lambda l: -l.price)
        comp.asks.sort(key=lambda l: l.price)
        composites.append(comp)
    return composites


# ---------------------------------------------------------- consensus ----
def consensus_probabilities(cluster: MatchedCluster,
                            composites: List[CompositeBook],
                            calibration_weights: Optional[Dict[str, float]] = None
                            ) -> Tuple[List[float], Dict[str, List[float]]]:
    """Weighted blend of venue views, de-vigged sports and forecasts.

    Venue weight ~ sqrt(liquidity) (diminishing scale advantage) times a
    calibration factor (1 = neutral). Forecasts get flat calibration weight.
    Returns (consensus per outcome, per-source views per outcome).
    """
    cal = calibration_weights or {}
    n = len(cluster.outcomes)
    views: Dict[str, List[float]] = {}
    weights: Dict[str, float] = {}

    for m in cluster.markets:
        probs: List[float] = []
        for mo in cluster.outcomes:
            key = mo.keys_by_venue.get(m.venue)
            oq = m.find(key) if key else None
            mid = oq.mid if oq else None
            if mid is None and key:
                # multi-outcome venue price *is* the probability
                mid = oq.probability if oq else None
            probs.append(min(max(mid, 1e-4), 1 - 1e-4) if mid is not None else None)
        if all(p is not None for p in probs):
            views[m.venue] = [float(p) for p in probs]          # type: ignore[arg-type]
            liq = m.liquidity or m.volume or 0
            weights[m.venue] = (max(liq, 1.0) ** 0.5) * cal.get(m.venue, 1.0)

    # composites carry better books when venue mids were missing
    for comp in composites:
        if comp.best_bid is not None and comp.best_ask is not None:
            src = f"book:{comp.label}"
            views.setdefault(src, [0.0] * n)[comp.outcome_index] = \
                (comp.best_bid + comp.best_ask) / 2
    # normalize book views into the partition (binary only)
    for src in list(views):
        if src.startswith("book:"):
            if n == 2:
                p0 = views[src][0]
                views[src] = [p0, 1 - p0]
            else:
                del views[src]

    for src, p in cluster.forecasts:
        if n == 2:
            views.setdefault(src, [float(p), 1 - float(p)])
            weights[src] = 8.0 * cal.get(src, 1.0)   # forecasts: flat-ish weight

    for sq in cluster.books:  # de-vigged sports bookmakers
        probs = devig_h2h(sq.outcomes)
        if len(probs) == n:
            views[f"sports:{sq.bookmaker}"] = [probs.get(mo.label, 0.0) for mo in cluster.outcomes]
            weights[f"sports:{sq.bookmaker}"] = 6.0 * cal.get(f"sports:{sq.bookmaker}", 1.0)

    if not views:
        return [1.0 / n] * n, views

    blended = [0.0] * n
    wsum = 0.0
    for src, probs in views.items():
        w = weights.get(src, 4.0)
        wsum += w
        for i in range(n):
            blended[i] += w * probs[i]
    if wsum > 0:
        blended = [b / wsum for b in blended]
    total = sum(blended)
    if total > 0:
        blended = [b / total for b in blended]
    return blended, views


def aggregate(clusters: List[MatchedCluster],
              calibration_weights: Optional[Dict[str, float]] = None,
              news: Optional[List] = None) -> List[AggregatedMarket]:
    """Full aggregation pass over all matched clusters."""
    out: List[AggregatedMarket] = []
    for cl in clusters:
        comps = build_composites(cl)
        consensus, views = consensus_probabilities(cl, comps, calibration_weights)
        am = AggregatedMarket(cluster=cl, composites=comps,
                              consensus=consensus, venue_views=views)
        if news:
            am.news_pressure = news_pressure(cl.title, news)
        out.append(am)
    out.sort(key=lambda a: -sum(m.volume or 0 for m in a.cluster.markets))
    return out


def news_pressure(title: str, news: List) -> float:
    """Recency-decayed count of news items overlapping the event title."""
    from ..core.domain import utcnow
    toks = set(norm_title(title))
    if not toks:
        return 0.0
    score = 0.0
    now = utcnow()
    for item in news:
        if not item.published_at:
            continue
        hours = (now - item.published_at).total_seconds() / 3600
        if hours < 0 or hours > 48:
            continue
        overlap = jaccard(toks, norm_title(item.title))
        if overlap > 0.12:
            score += overlap * (2 ** (-hours / 24))
    return round(score, 4)
