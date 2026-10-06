"""MM backtester: run every quoting strategy over comparable book histories
and produce an attribution table (spread capture vs adverse selection vs
rewards vs fees).

History sources:
  * offline: seeded logit random walk from the fixture market (fixtures.
    book_history) — deterministic, reproducible;
  * live-pulled markets: a seeded stress path around the observed mid
    (documented; used to compare strategies under identical conditions,
    NOT to claim live PnL).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..connectors import fixtures as fx
from ..core.config import Config
from ..core.domain import MarketQuote, OutcomeQuote
from ..core.logging_setup import get_logger
from .inventory import Inventory, RiskGuard
from .microstructure import FillIntensity, calibrate_intensity
from .rewards import reward_params_for
from .simulator import SimResult, simulate_market_making
from .strategies.adaptive import AdaptiveSpread
from .strategies.avellaneda_stoikov import AvellanedaStoikov
from .strategies.base import MMStrategy
from .strategies.glft import GLFTStrategy

log = get_logger("qa.mmbacktest")


@dataclass
class StrategyComparison:
    market_title: str
    venue: str
    strategy: str
    result: SimResult
    note: str = ""


@dataclass
class BacktestReport:
    rows: List[StrategyComparison] = field(default_factory=list)
    best_by_market: Dict[str, str] = field(default_factory=dict)

    def table(self) -> List[Dict]:
        out = []
        for r in self.rows:
            out.append({
                "market": r.market_title[:60], "venue": r.venue,
                "strategy": r.strategy, **r.result.summary(),
                "note": r.note})
        return out


def build_strategies(cfg: Config) -> List[Tuple[str, MMStrategy]]:
    return [
        ("avellaneda_stoikov", AvellanedaStoikov(gamma=cfg.mm.gamma)),
        ("glft", GLFTStrategy(gamma=cfg.mm.gamma)),
        ("adaptive+rewards", AdaptiveSpread()),
        ("adaptive_plain", AdaptiveSpread()),
    ]


def run_backtest(cfg: Config, markets: List[MarketQuote], steps: int = 240,
                 per_market: int = 4, seed: int = 11) -> BacktestReport:
    """Compare strategies on the top-N liquid binary markets."""
    report = BacktestReport()
    cands = [m for m in markets if m.binary and any(
        o.bids for o in m.outcomes)]
    cands.sort(key=lambda m: -(m.volume or 0))
    for mk in cands[:per_market]:
        oq = mk.outcomes[0]
        series = fx.book_history(mk, oq.key, steps=steps, seed=seed)
        if len(series) < 20:
            continue
        rp_reward = reward_params_for(mk.venue, cfg.rewards,
                                      bids=oq.bids, asks=oq.asks,
                                      mid=oq.mid)
        rp_none = None
        for name, strat in build_strategies(cfg):
            reward_params = rp_reward if name == "adaptive+rewards" else rp_none
            if name == "adaptive+rewards" and mk.venue != "polymarket":
                continue     # rewards only modeled for polymarket
            res = simulate_market_making(
                strat, series, venue=mk.venue, market_id=mk.market_id,
                outcome_key=oq.key, quote_size=cfg.mm.quote_size,
                max_inventory=cfg.mm.max_inventory, gamma=cfg.mm.gamma,
                seed=seed + hash(name) % 100,
                adverse_frac=cfg.mm.adverse_selection_frac,
                reward_params=reward_params)
            note = "reward-earning mode" if reward_params and reward_params.get("enabled") \
                else "spread capture only"
            report.rows.append(StrategyComparison(mk.title, mk.venue, name, res, note))
        # pick winner per market
        rows = [r for r in report.rows if r.market_title == mk.title]
        if rows:
            best = max(rows, key=lambda r: r.result.pnl + r.result.rewards_usd)
            report.best_by_market[mk.title] = best.strategy
    return report
