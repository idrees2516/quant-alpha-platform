"""Beta / factor exposure of strategy PnL.

Factors (per rebalance day): consensus-avg probability move (market factor),
cross-source dispersion change (uncertainty factor), news-volume index and
a constant. Betas are OLS-estimated with t-stats so we can *prove* which
risks the strategies actually carry (goal: near-zero beta, positive alpha).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .stats import OLSResult, ols


@dataclass
class BetaReport:
    strategy: str
    betas: Dict[str, float]
    t_stats: Dict[str, float]
    r_squared: float
    n: int
    summary: str = ""


FACTOR_NAMES = ["intercept", "consensus_move", "dispersion_move", "news_volume"]


def factor_series(pnl: Sequence[float], consensus_paths: Sequence[Sequence[float]],
                  dispersion_paths: Sequence[Sequence[float]],
                  news_counts: Sequence[float]) -> Tuple[List[List[float]], List[str]]:
    """Build the design matrix from per-step factor paths."""
    def daily_avg(path: Sequence[float]) -> List[float]:
        if not path:
            return []
        return [path[i + 1] - path[i] for i in range(len(path) - 1)]

    X: List[List[float]] = []
    used: List[str] = []
    n = min(len(pnl), len(consensus_paths), len(dispersion_paths), len(news_counts))
    for i in range(n):
        cm = daily_avg(consensus_paths[i])
        dm = daily_avg(dispersion_paths[i])
        L = min(len(cm), len(dm))
        if L == 0:
            continue
        X.append([sum(cm[:L]) / L, sum(dm[:L]) / L, news_counts[i]])
        used.append(f"day{i}")
    return X, used


def regress_beta(strategy: str, pnl_daily: Sequence[float],
                 X: Sequence[Sequence[float]]) -> BetaReport:
    res: OLSResult = ols(list(pnl_daily), list(X))
    if not res.betas:
        return BetaReport(strategy, {}, {}, 0.0, 0)
    names = FACTOR_NAMES
    betas = {names[i]: round(res.betas[i], 4) for i in range(len(res.betas))}
    ts = {names[i]: round(res.t_stats[i], 2) for i in range(len(res.t_stats))}
    return BetaReport(strategy=strategy, betas=betas, t_stats=ts,
                      r_squared=round(res.r_squared, 3), n=res.n,
                      summary=res.summary(names))
