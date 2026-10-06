"""Arb sizing: fractional Kelly on residual (execution/counterparty) risk,
liquidity-capped, funding-cost-aware for capital locked until settlement."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ..core.config import Config
from .detectors import ArbLeg, ArbOpportunity


@dataclass
class SizingPlan:
    contracts_per_leg: float
    capital_usd: float
    expected_profit_usd: float
    funding_cost_usd: float
    liquidity_capped: bool
    kelly_used: float


def size_arb(opp: ArbOpportunity, cfg: Config,
             bankroll: float = 10_000.0) -> SizingPlan:
    if not opp.risk_free:
        # value trades: fractional Kelly on z-score edge with conservative view
        edge = min(0.25, opp.edge * 0.02)     # translate z to a modest edge
        b = max(edge, 1e-4)
        kelly = max(0.0, b / (1 + b)) * cfg.arb.kelly_fraction * 0.25
        depth = min((leg.depth for leg in opp.legs), default=50.0)
        contracts = min(bankroll * kelly / max(opp.net_cost, 1e-4), depth * 0.5, 25.0)
    else:
        edge = opp.edge
        b = edge
        # residual risk model: execution slippage + settlement failure ~ small
        residual = 0.01
        kelly_raw = (b - residual) / (1 + b) if b > residual else 0.0
        kelly = max(0.0, kelly_raw) * cfg.arb.kelly_fraction / max(cfg.arb.kelly_fraction, 1e-9)
        kelly = min(kelly, cfg.arb.kelly_fraction)   # cap at configured fraction
        depth = min((leg.depth for leg in opp.legs if leg.depth > 0), default=50.0)
        if depth <= 0:
            depth = 10.0
        contracts = min(bankroll * kelly / max(opp.net_cost, 1e-4),
                        depth * 0.5,   # half displayed depth: slippage haircut
                        250.0)
    capital = contracts * opp.net_cost
    funding = capital * (cfg.rewards.funding_rate_annual / 365.0) * \
        max(1.0, opp.horizon_days or 30.0)
    profit = contracts * (opp.guaranteed_payout - opp.net_cost) if opp.risk_free \
        else contracts * opp.net_cost * 0.02
    return SizingPlan(
        contracts_per_leg=max(0.0, round(contracts, 1)),
        capital_usd=round(capital, 2),
        expected_profit_usd=round(profit - funding, 2),
        funding_cost_usd=round(funding, 2),
        liquidity_capped=contracts >= depth * 0.5 - 1e-9 if opp.legs else False,
        kelly_used=round(kelly, 4))


def execution_plan(opp: ArbOpportunity, sizing: SizingPlan,
                   leg_fee_note: Optional[str] = None) -> dict:
    """Order-router-ready plan: legs ordered cheapest-eff-cost first."""
    legs = sorted(opp.legs, key=lambda l: l.eff_price)
    plan = {
        "opportunity": opp.kind,
        "cluster": opp.cluster_id,
        "title": opp.title,
        "edge": round(opp.edge, 4),
        "risk_free": opp.risk_free,
        "net_cost_per_unit": round(opp.net_cost, 4),
        "guaranteed_payout": round(opp.guaranteed_payout, 4),
        "sizing": {
            "contracts_per_leg": sizing.contracts_per_leg,
            "capital_usd": sizing.capital_usd,
            "expected_profit_usd": sizing.expected_profit_usd,
            "funding_cost_usd": sizing.funding_cost_usd,
        },
        "legs": [{
            "venue": l.venue, "market": l.market_id, "outcome": l.outcome_key,
            "side": l.side, "limit_price": round(l.raw_price, 4),
            "eff_price_incl_fees": round(l.eff_price, 4),
            "displayed_depth": round(l.depth, 1),
            "order_type": "IOC-limit",
            "note": l.settlement_fee_note,
        } for l in legs],
        "notes": opp.notes,
    }
    return plan
