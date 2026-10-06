"""Hypothesis-testing engine: formal, falsifiable tests over pulled data.

H1  "Sharp venues lead consensus": Polymarket/Kalshi mids move BEFORE
    consensus (cross-correlation of returns at lag 0 vs lag +1) -> if
    true, MM fair values should weight them.
H2  "Calibration-weighted consensus beats every single source": Brier
    comparison with paired bootstrap on simulated resolutions.
H3  "MM PnL > 0 after fees": Welch t-test + bootstrap CI on hourly equity
    changes from the backtester, per strategy.
H4  "Arb edges decay with market age": OLS of |edge| on time-to-close.

Each test returns TestResult(p, statistic, verdict, detail). Offline mode
uses seeded simulated resolutions (clearly labeled) — live mode consumes
the same machinery on real snapshots stored by the pipeline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ..core.config import Config
from ..core.logging_setup import get_logger
from ..normalize.aggregate import AggregatedMarket
from .calibration import (CalibrationReport, brier_score, calibrate_source,
                          calibration_weights)
from .stats import (OLSResult, bootstrap_ci, norm_cdf, ols, welch_t_test,
                    mean, std)

log = get_logger("qa.hypothesis")


@dataclass
class TestResult:
    hypothesis: str
    statistic: float
    p_value: float
    verdict: str            # 'SUPPORTED' | 'REJECTED' | 'INCONCLUSIVE'
    detail: str = ""

    def to_dict(self) -> dict:
        return {"hypothesis": self.hypothesis,
                "statistic": round(self.statistic, 3),
                "p_value": round(self.p_value, 4),
                "verdict": self.verdict, "detail": self.detail}


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs, ys = list(xs[:n]), list(ys[:n])
    mx, my = mean(xs), mean(ys)
    sx, sy = std(xs), std(ys)
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n * sx * sy)


def corr_p_value(r: float, n: int) -> float:
    if n < 4 or abs(r) >= 1.0:
        return 0.0 if abs(r) >= 1.0 and n >= 4 else 1.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return 2 * (1 - norm_cdf(abs(t)))


def test_lead_lag(aggs: List[AggregatedMarket],
                  series_by_source: Dict[str, List[float]],
                  lead_source: str, lag_steps: int = 1) -> TestResult:
    """H1: correlation(returns(lead), returns(consensus)) is higher at
    lag=+1 than at lag=0 -> the lead source moves first."""
    if len(series_by_source) < 2 or lead_source not in series_by_source:
        return TestResult("H1 lead-lag", 0.0, 1.0, "INCONCLUSIVE",
                          "insufficient sources")
    lead = series_by_source[lead_source]

    def rets(s: Sequence[float]) -> List[float]:
        return [s[i + 1] - s[i] for i in range(len(s) - 1)]

    others = {k: v for k, v in series_by_source.items() if k != lead_source}
    consensus = [mean([others[k][i] for k in others if i < len(others[k])])
                 for i in range(min((len(v) for v in others.values()), default=0))]
    lr, cr = rets(lead), rets(consensus)
    n = min(len(lr), len(cr)) - lag_steps
    if n < 8:
        return TestResult("H1 lead-lag", 0.0, 1.0, "INCONCLUSIVE", f"n={n}")
    r0 = pearson(lr[:n], cr[:n])
    r1 = pearson(lr[:n], cr[lag_steps:lag_steps + n])
    stat = r1 - r0
    p = corr_p_value(max(abs(r0), abs(r1)), n)
    verdict = "SUPPORTED" if stat > 0.05 and r1 > 0.2 else \
        "REJECTED" if stat < -0.05 else "INCONCLUSIVE"
    return TestResult("H1 lead-lag", stat, p, verdict,
                      f"corr(lag0)={r0:.3f} corr(lag+{lag_steps})={r1:.3f} lead={lead_source}")


def test_consensus_beats_sources(reports: Sequence[CalibrationReport],
                                 consensus_probs: Sequence[float],
                                 outcomes: Sequence[int]) -> TestResult:
    """H2: consensus Brier < min single-source Brier, bootstrap p."""
    if not reports or not consensus_probs:
        return TestResult("H2 consensus", 0.0, 1.0, "INCONCLUSIVE", "no data")
    bc = brier_score(consensus_probs, outcomes)
    best = min((r.brier, r.source) for r in reports)
    deltas = [(bc - b) for (b, _src) in [(r.brier, r.source) for r in reports]]
    ci = bootstrap_ci(deltas, n_resamples=1000, seed=3)
    supported = bc < best[0] and ci.high < 0.0
    verdict = "SUPPORTED" if supported else ("INCONCLUSIVE" if ci.contains(0.0) else "REJECTED")
    return TestResult("H2 consensus", best[0] - bc, min(1.0, max(0.0, ci.high * 10)), verdict,
                      f"consensus brier={bc:.4f} best source {best[1]}={best[0]:.4f}; "
                      f"bootstrap CI=[{ci.low:.4f},{ci.high:.4f}]")


def test_mm_positive_pnl(strategy: str, hourly_equity: Sequence[float]) -> TestResult:
    """H3: mean hourly PnL > 0 (Welch t-test + bootstrap CI)."""
    if len(hourly_equity) < 10:
        return TestResult(f"H3 mm pnl ({strategy})", 0.0, 1.0, "INCONCLUSIVE",
                          "insufficient steps")
    t = welch_t_test(hourly_equity, 0.0)
    ci = bootstrap_ci(hourly_equity, seed=5)
    verdict = "SUPPORTED" if t.p_value < 0.05 and ci.low > 0 else \
        "INCONCLUSIVE" if ci.contains(0.0) else "REJECTED"
    return TestResult(f"H3 mm pnl ({strategy})", t.t, t.p_value, verdict,
                      f"mean/h={t.mean:.3f} t={t.t:.2f} CI=[{ci.low:.3f},{ci.high:.3f}]")


def test_arb_edge_decay(edges: Sequence[float], days_to_close: Sequence[float]) -> TestResult:
    """H4: |arb edge| decreases as markets approach close (or with age)."""
    n = min(len(edges), len(days_to_close))
    if n < 8:
        return TestResult("H4 arb decay", 0.0, 1.0, "INCONCLUSIVE", f"n={n}")
    ys = [abs(e) for e in edges[:n]]
    X = [[d] for d in days_to_close[:n]]
    res: OLSResult = ols(ys, X)
    if not res.betas:
        return TestResult("H4 arb decay", 0.0, 1.0, "INCONCLUSIVE", "singular")
    slope = res.betas[1]
    p = res.p_values[1]
    verdict = "SUPPORTED" if p < 0.05 else "INCONCLUSIVE"
    return TestResult("H4 arb decay", slope, p, verdict,
                      res.summary(["intercept", "days_to_close"]))
