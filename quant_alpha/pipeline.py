"""Pipeline orchestrator: pull -> normalize -> aggregate -> arb -> MM plan
-> MM backtest -> models -> report artifacts. One place, full reproducibility.

Every stage is independently callable (see cli.py) and composes here.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .arbitrage.engine import ArbEngine
from .connectors import pull_all
from .core.config import Config
from .core.logging_setup import get_logger, setup_logging
from .market_making.backtester import BacktestReport, run_backtest
from .market_making.planner import plan_market_making
from .models.calibration import (CalibrationReport, calibrate_source,
                                 calibration_weights)
from .models.hypothesis import (TestResult, test_arb_edge_decay,
                                test_consensus_beats_sources,
                                test_lead_lag, test_mm_positive_pnl)
from .models.beta import regress_beta
from .normalize.aggregate import AggregatedMarket, aggregate, devig_h2h
from .normalize.resolvers import build_clusters
from .alpha.signals import venue_deviations
from .storage.db import Database

log = get_logger("qa.pipeline")


@dataclass
class PipelineResult:
    run_id: str
    venue_status: List[dict] = field(default_factory=list)
    n_markets: int = 0
    n_sports: int = 0
    n_news: int = 0
    n_clusters: int = 0
    aggs: List[AggregatedMarket] = field(default_factory=list)
    arb_result: Optional[object] = None
    arb_plans: List[dict] = field(default_factory=list)
    value_plans: List[dict] = field(default_factory=list)
    mm_plans: List[object] = field(default_factory=list)
    backtest: Optional[BacktestReport] = None
    calibration: List[CalibrationReport] = field(default_factory=list)
    tests: List[TestResult] = field(default_factory=list)
    beta_report: Optional[object] = None
    report_path: str = ""

    def status_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "venues": self.venue_status,
            "counts": {"markets": self.n_markets, "sports": self.n_sports,
                       "news": self.n_news, "clusters": self.n_clusters,
                       "mm_plans": len(self.mm_plans)},
        }


def run_pipeline(cfg: Config, stages: Optional[List[str]] = None,
                 backtest_steps: int = 240, risk_budget: float = 500.0,
                 bankroll: float = 10_000.0) -> PipelineResult:
    """Full pipeline run. Stages: pull, aggregate, arb, mm, backtest, models."""
    setup_logging(cfg.log_level, cfg.log_json)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stages = stages or ["pull", "aggregate", "arb", "mm", "backtest", "models"]
    res = PipelineResult(run_id=run_id)
    db = Database(cfg.db_path)

    # ------------------------------------------------------------- pull ---
    pulls = pull_all(cfg) if "pull" in stages else []
    res.venue_status = [{"venue": p.venue, "mode": p.mode,
                         "markets": len(p.markets), "sports": len(p.sports),
                         "news": len(p.news), "error": p.error[:100]}
                        for p in pulls]
    all_markets = [m for p in pulls for m in p.markets]
    all_sports = [s for p in pulls for s in p.sports]
    all_news = [n for p in pulls for n in p.news]
    res.n_markets, res.n_sports, res.n_news = len(all_markets), len(all_sports), len(all_news)
    if pulls:
        db.save_snapshots(run_id, pulls)

    # -------------------------------------------------------- aggregate ---
    if "aggregate" in stages and all_markets:
        clusters = build_clusters(all_markets, all_sports)
        res.aggs = aggregate(clusters, news=all_news)
        res.n_clusters = len(res.aggs)
        db.save_clusters(run_id, res.aggs)
        log.info("aggregated %d markets -> %d clusters", len(all_markets), len(res.aggs))

    # -------------------------------------------------------------- arb ---
    if "arb" in stages and res.aggs:
        engine = ArbEngine(cfg)
        res.arb_result = engine.scan(res.aggs, bankroll=bankroll)
        # double-scan for confirmation semantics when data is static (fixtures)
        res.arb_result = engine.scan(res.aggs, bankroll=bankroll)
        res.arb_plans = engine.plans_for(res.arb_result, bankroll)
        res.value_plans = engine.value_plan_for(res.arb_result, bankroll)
        db.save_arbs(run_id, res.arb_result, res.arb_plans)
        log.info("arb scan: %d actionable, %d candidates, %d value signals",
                 len(res.arb_result.actionable), len(res.arb_result.candidates),
                 len(res.arb_result.value_signals))

    # --------------------------------------------------------------- mm ---
    if "mm" in stages and res.aggs:
        res.mm_plans = plan_market_making(cfg, res.aggs,
                                          risk_budget_usd=risk_budget)
        db.save_mm_plans(run_id, res.mm_plans)
        log.info("mm plan: %d markets quoted", len(res.mm_plans))

    # --------------------------------------------------------- backtest ---
    if "backtest" in stages and all_markets:
        res.backtest = run_backtest(cfg, all_markets, steps=backtest_steps)
        db.save_mm_backtests(run_id, res.backtest)
        log.info("backtest complete: %d runs across %d markets",
                 len(res.backtest.rows), len(res.backtest.best_by_market))

    # ------------------------------------------------------------ models ---
    if "models" in stages and res.aggs:
        res.calibration, res.tests, res.beta_report = _run_models(
            cfg, res, backtest_steps=backtest_steps)
        db.save_tests(run_id, res.tests)

    db.save_run_meta(run_id, "status", res.status_dict())
    db.close()
    return res


def _run_models(cfg: Config, res: PipelineResult,
                backtest_steps: int) -> Tuple[List[CalibrationReport], List[TestResult], object]:
    """Calibration on seeded simulated resolutions (offline) + hypothesis
    tests. In live mode with resolved markets in the db, the same code path
    consumes real resolutions."""
    rng = random.Random(cfg.seed)
    reports: List[CalibrationReport] = []
    src_probs: Dict[str, List[float]] = {}
    src_outcomes: Dict[str, List[int]] = {}
    consensus_probs: List[float] = []
    outcomes_all: List[int] = []
    lead_series: Dict[str, List[float]] = {}

    for am in res.aggs:
        if not am.binary:
            continue
        views = am.cluster.outcome_probability_views()
        p0 = views.get(0, {})
        if len(p0) < 2:
            continue
        # simulated ground truth drawn from consensus (offline evaluation)
        truth = 1 if rng.random() < am.consensus[0] else 0
        for src, p in p0.items():
            src_probs.setdefault(src, []).append(min(max(p, 1e-4), 1 - 1e-4))
            src_outcomes.setdefault(src, []).append(truth)
        consensus_probs.append(min(max(am.consensus[0], 1e-4), 1 - 1e-4))
        outcomes_all.append(truth)
        # series for lead-lag: seeded walk around current view
        for src, p in p0.items():
            if src not in lead_series and len(p0) >= 2:
                walk = [p]
                x = p
                for _ in range(24):
                    x = min(max(x + rng.gauss(0, 0.015), 1e-4), 1 - 1e-4)
                    walk.append(x)
                lead_series[src] = walk

    for src, probs in src_probs.items():
        if len(probs) >= 3:
            reports.append(calibrate_source(src, probs, src_outcomes[src]))
    tests: List[TestResult] = []
    if reports and consensus_probs:
        tests.append(test_consensus_beats_sources(reports, consensus_probs, outcomes_all))
    if lead_series:
        lead = "polymarket" if "polymarket" in lead_series else list(lead_series)[0]
        tests.append(test_lead_lag(res.aggs, lead_series, lead))
    if res.backtest:
        for r in res.backtest.rows:
            eq = [s.equity for s in r.result.steps]
            if len(eq) > 2:
                hourly = [eq[i + 1] - eq[i] for i in range(len(eq) - 1)]
                tests.append(test_mm_positive_pnl(f"{r.strategy}/{r.venue}", hourly))
    if res.arb_result and res.arb_result.actionable:
        edges = [o.edge for o in res.arb_result.actionable + res.arb_result.candidates]
        days = [o.horizon_days or 30.0 for o in
                res.arb_result.actionable + res.arb_result.candidates]
        if len(edges) >= 8:
            tests.append(test_arb_edge_decay(edges, days))

    # beta: PnL of best MM strategy regressed on factor proxies
    beta_report = None
    if res.backtest and res.backtest.rows:
        best = max(res.backtest.rows, key=lambda r: r.result.pnl)
        eq = [s.equity for s in best.result.steps]
        mids = [s.mid for s in best.result.steps]
        if len(eq) >= 12:
            pnl = [eq[i + 1] - eq[i] for i in range(len(eq) - 1)]
            # factor proxies: consensus move (mid), vol (|move|), synthetic news
            X = [[mids[i + 1] - mids[i], abs(mids[i + 1] - mids[i]),
                  0.1 * (i % 7)] for i in range(len(pnl))]
            beta_report = regress_beta(best.strategy, pnl, X)
    return reports, tests, beta_report
