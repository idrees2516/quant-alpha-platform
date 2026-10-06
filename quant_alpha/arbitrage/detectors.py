"""Arbitrage detectors over aggregated cross-venue markets.

All edges are NET of venue fees (taker costs, settlement profit fees) and a
slippage buffer. Guaranteed (risk-free up to counterparty risk) structures:

  1. cross_venue_complement — buy YES on venue A + NO on venue B, total
     cost-after-fees < guaranteed payout.
  2. in_venue_complement    — same structure within one venue's order book.
  3. dutch_book             — buy every outcome of a mutually-exclusive set
     across venues / bookmakers; sum of net costs < payout.
  4. sports_dutch           — bookmaker Dutch book on an n-way game.

Also emits 'value' (non-guaranteed) deviations: venue price vs consensus,
z-scored — the statistical-arb / alpha feed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Dict, List, Optional, Tuple

from ..core.domain import MatchedCluster
from ..normalize.aggregate import AggregatedMarket, CompositeBook, devig_h2h
from .fees import FeeModel, fee_models


@dataclass
class ArbLeg:
    venue: str
    market_id: str
    outcome_key: str
    outcome_label: str
    side: str                # 'buy'
    raw_price: float
    eff_price: float         # fee-adjusted
    depth: float             # displayed size
    settlement_fee_note: str = ""


@dataclass
class ArbOpportunity:
    kind: str                       # complementary | dutch | sports_dutch | value
    cluster_id: str
    title: str
    legs: List[ArbLeg]
    net_cost: float                 # total eff cost per 1 unit of payout
    guaranteed_payout: float        # payout in worst settlement branch (fees incl.)
    edge: float                     # (payout - cost) / cost
    horizon_days: Optional[float] = None
    confidence: float = 1.0
    notes: str = ""
    signature: str = ""

    @property
    def risk_free(self) -> bool:
        return self.kind in ("complementary", "dutch", "sports_dutch")


# ---------------------------------------------------------------------
def _pair_legs(comp_yes: CompositeBook, comp_no: CompositeBook,
               fees: Dict[str, FeeModel]) -> Optional[Tuple[ArbLeg, ArbLeg, float, float]]:
    """Best YES ask on comp_yes crossed with best NO ask on comp_no (fee-aware)."""
    if not comp_yes.asks or not comp_no.asks:
        return None
    ya, na = comp_yes.asks[0], comp_no.asks[0]
    if ya.venue == na.venue and ya.market_id == na.market_id:
        return None    # same venue handled separately
    payoff_yes = 1.0   # worst settlement branch handled by caller
    return (ArbLeg(ya.venue, ya.market_id, ya.outcome_key, comp_yes.label, "buy",
                   ya.price, ya.eff_price, ya.size),
            ArbLeg(na.venue, na.market_id, na.outcome_key, comp_no.label, "buy",
                   na.price, na.eff_price, na.size),
            ya.eff_price, na.eff_price)


def detect_complementary(am: AggregatedMarket, min_edge: float = 0.0,
                         slippage: float = 0.0
                         ) -> List[ArbOpportunity]:
    """YES@A + NO@B < 1 (and in-venue YES+NO asks < 1)."""
    out: List[ArbOpportunity] = []
    if not am.binary:
        return out
    fees = fee_models()
    cy, cn = am.composites[0], am.composites[1]

    # in-venue: one market whose two outcome asks sum < 1 after fees.
    # ONLY valid for genuine YES/NO complements — two candidate contracts of
    # a multi-candidate race are NOT complements (other outcomes uncovered).
    for m in am.cluster.markets:
        if not m.binary:
            continue
        keys = {o.key.lower() for o in m.outcomes}
        if not (keys & {"yes", "no"} and len(keys) == 2):
            continue
        y, n = m.outcomes[0], m.outcomes[1]
        if y.best_ask is None or n.best_ask is None:
            continue
        fm = fees.get(m.venue)
        cost = (fm.taker_buy_cost(y.best_ask) if fm else y.best_ask) + \
               (fm.taker_buy_cost(n.best_ask) if fm else n.best_ask)
        payout = _worst_payout(m.venue, y.best_ask, n.best_ask, fees)
        edge = (payout - cost) / cost if cost > 0 else 0.0
        if edge > min_edge:
            out.append(ArbOpportunity(
                "complementary", am.cluster.cluster_id, am.title,
                legs=[ArbLeg(m.venue, m.market_id, y.key, "YES", "buy", y.best_ask,
                             fees[m.venue].taker_buy_cost(y.best_ask), y.ask_size or 0),
                      ArbLeg(m.venue, m.market_id, n.key, "NO", "buy", n.best_ask,
                             fees[m.venue].taker_buy_cost(n.best_ask), n.ask_size or 0)],
                net_cost=cost, guaranteed_payout=payout, edge=edge,
                notes="in-venue complement", signature=_sig(am, m.venue, m.market_id)))

    # cross-venue: best asks on opposite outcomes
    for a, b in ((cy, cn), (cn, cy)):
        res = _pair_legs(a, b, fees)
        if res is None:
            continue
        leg_a, leg_b, ca, cb = res
        cost = ca + cb + slippage
        payout = _worst_payout_pair(leg_a, leg_b, fees)
        edge = (payout - cost) / cost if cost > 0 else 0.0
        if edge > min_edge:
            out.append(ArbOpportunity(
                "complementary", am.cluster.cluster_id, am.title,
                legs=[leg_a, leg_b], net_cost=cost, guaranteed_payout=payout,
                edge=edge, notes="cross-venue complement",
                signature=_sig(am, leg_a.venue, leg_b.venue)))
    return out


def detect_dutch(am: AggregatedMarket, min_edge: float = 0.0,
                 slippage: float = 0.0) -> List[ArbOpportunity]:
    """Buy one contract of EVERY mutually-exclusive outcome across venues."""
    out: List[ArbOpportunity] = []
    if not am.cluster.outcomes or len(am.composites) < 3:
        return out          # binary clusters are covered by the complement detector
    fees = fee_models()
    legs: List[ArbLeg] = []
    cost = 0.0
    for comp in am.composites:
        if not comp.asks:
            return out
        best = comp.asks[0]
        legs.append(ArbLeg(best.venue, best.market_id, best.outcome_key,
                           comp.label, "buy", best.price, best.eff_price, best.size))
        cost += best.eff_price
    cost += slippage * len(legs)
    # worst-case settlement: the venue with the highest settlement fee wins
    payout = 1.0
    for leg in legs:
        fm = fees.get(leg.venue)
        if fm:
            payout = min(payout, fm.settlement_payout(leg.raw_price, 1.0, won=True))
    if cost <= 0:
        return out
    edge = (payout - cost) / cost
    if edge > min_edge:
        out.append(ArbOpportunity(
            "dutch", am.cluster.cluster_id, am.title, legs=legs,
            net_cost=cost, guaranteed_payout=payout, edge=edge,
            notes="multi-outcome Dutch book", signature=_sig(am, *(l.venue for l in legs))))
    return out


def detect_sports_dutch(am: AggregatedMarket, min_edge: float = 0.0
                        ) -> List[ArbOpportunity]:
    """Dutch book across bookmakers: best decimal odds per outcome, sum 1/o < 1."""
    out: List[ArbOpportunity] = []
    books = am.cluster.books
    if len(books) < 2:
        return out
    n = len(books[0].outcomes)
    if n < 2 or any(len(b.outcomes) != n for b in books):
        return out
    # best odds per outcome label
    best: Dict[str, Tuple[float, str]] = {}
    for sq in books:
        for label, odds in sq.outcomes:
            if label not in best or odds > best[label][0]:
                best[label] = (odds, sq.bookmaker)
    if len(best) != n:
        return out
    total_implied = sum(1.0 / o for o, _ in best.values())
    if total_implied >= 1.0:
        return out
    edge = (1.0 - total_implied) / total_implied
    if edge <= min_edge:
        return out
    legs = [ArbLeg(f"sports:{bm}", f"game:{am.cluster.cluster_id}", label,
                   label, "buy", 1.0 / odds, 1.0 / odds, 0.0,
                   settlement_fee_note=f"decimal {odds}")
            for label, (odds, bm) in best.items()]
    out.append(ArbOpportunity(
        "sports_dutch", am.cluster.cluster_id, am.title, legs=legs,
        net_cost=total_implied, guaranteed_payout=1.0, edge=edge,
        notes="bookmaker Dutch book", signature=_sig(am, *(l.venue for l in legs))))
    return out


def detect_value(am: AggregatedMarket, z_threshold: float = 2.0
                 ) -> List[ArbOpportunity]:
    """Statistical deviations from consensus (alpha feed, NOT risk-free)."""
    out: List[ArbOpportunity] = []
    n = len(am.consensus)
    for i, comp in enumerate(am.composites):
        for leg in comp.asks + comp.bids:
            p_fair = am.consensus[i]
            sigma = max(0.02, (comp.spread or 0.04) / 2 + 0.015)
            if leg.venue in ("manifold", "metaculus"):
                continue
            z = (leg.price - p_fair) / sigma
            if abs(z) >= z_threshold:
                out.append(ArbOpportunity(
                    "value", am.cluster.cluster_id, am.title,
                    legs=[ArbLeg(leg.venue, leg.market_id, leg.outcome_key,
                                 comp.label, "buy" if z < 0 else "sell",
                                 leg.price, leg.eff_price, leg.size)],
                    net_cost=leg.price, guaranteed_payout=0.0,
                    edge=abs(z), confidence=min(1.0, 0.5 + 0.1 * abs(z)),
                    notes=f"z={z:+.2f} vs consensus {p_fair:.3f}",
                    signature=_sig(am, leg.venue, leg.market_id, comp.label)))
    return out


# ---------------------------------------------------------------------
def _worst_payout(venue: str, yes_ask: float, no_ask: float,
                  fees: Dict[str, FeeModel]) -> float:
    fm = fees.get(venue)
    if fm is None:
        return 1.0
    # if YES wins, YES contract pays 1 (minus profit fee); same for NO
    return min(fm.settlement_payout(yes_ask, 1.0, won=True),
               fm.settlement_payout(no_ask, 1.0, won=True))


def _worst_payout_pair(leg_a: ArbLeg, leg_b: ArbLeg, fees: Dict[str, FeeModel]) -> float:
    pa = fees.get(leg_a.venue).settlement_payout(leg_a.raw_price, 1.0, won=True) \
        if fees.get(leg_a.venue) else 1.0
    pb = fees.get(leg_b.venue).settlement_payout(leg_b.raw_price, 1.0, won=True) \
        if fees.get(leg_b.venue) else 1.0
    # exactly one leg wins; guaranteed payout is the min of the two branches
    return min(max(pa, 0.0), max(pb, 0.0)) if pa or pb else 1.0


def _sig(am: AggregatedMarket, *parts: str) -> str:
    return "|".join([am.cluster.cluster_id, *(str(p) for p in parts)])
