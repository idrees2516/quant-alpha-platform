"""ArbEngine: stateful scanner with staleness confirmation.

Production semantics:
  * an opportunity only becomes *actionable* after it survives
    ``staleness_confirmations`` consecutive scans (stale-quote/false-signal
    protection);
  * every opportunity is age-checked against ``max_quote_age_s`` of the
    underlying snapshot;
  * results carry full execution plans (venue legs, net-of-fee prices,
    Kelly-sized);
  * the engine is pure w.r.t. inputs (idempotent scan), all state is
    confirmation counts keyed by opportunity signature.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from ..core.config import Config
from ..core.domain import utcnow
from ..core.logging_setup import get_logger
from ..normalize.aggregate import AggregatedMarket
from .detectors import (ArbOpportunity, detect_complementary,
                        detect_dutch, detect_sports_dutch, detect_value)
from .sizing import SizingPlan, execution_plan, size_arb

log = get_logger("qa.arb")


@dataclass
class ScanRecord:
    signature: str
    seen_count: int = 1
    last_seen_ts: object = None


@dataclass
class ArbScanResult:
    actionable: List[ArbOpportunity] = field(default_factory=list)
    candidates: List[ArbOpportunity] = field(default_factory=list)
    value_signals: List[ArbOpportunity] = field(default_factory=list)
    plans: List[dict] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


class ArbEngine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.memory: Dict[str, ScanRecord] = {}

    def scan(self, aggs: List[AggregatedMarket], bankroll: float = 10_000.0
             ) -> ArbScanResult:
        res = ArbScanResult()
        min_edge = self.cfg.arb.min_edge_after_fees
        slip = self.cfg.arb.slippage_buffer

        for am in aggs:
            age_s = 0.0
            for m in am.cluster.markets:
                if m.fetched_at:
                    age_s = max(age_s, (utcnow() - m.fetched_at).total_seconds())
            stale = age_s > self.cfg.arb.max_quote_age_s

            for opp in detect_complementary(am, min_edge, slip):
                self._record(opp, am, stale, res, bankroll)
            for opp in detect_dutch(am, min_edge, slip):
                self._record(opp, am, stale, res, bankroll)
            for opp in detect_sports_dutch(am, min_edge):
                self._record(opp, am, stale, res, bankroll)
            res.value_signals.extend(detect_value(am, 2.0))

        res.stats = {
            "actionable": len(res.actionable),
            "candidates": len(res.candidates),
            "value_signals": len(res.value_signals),
            "tracked_signatures": len(self.memory),
        }
        res.actionable.sort(key=lambda o: -o.edge)
        res.candidates.sort(key=lambda o: -o.edge)
        res.value_signals.sort(key=lambda o: -o.edge)
        return res

    def _record(self, opp: ArbOpportunity, am: AggregatedMarket, stale: bool,
                res: ArbScanResult, bankroll: float) -> None:
        opp.horizon_days = None
        if am.cluster.closes_at:
            opp.horizon_days = max(0.02, (am.cluster.closes_at - utcnow()).total_seconds()
                                   / 86400.0)
        if stale:
            log.info("dropping stale opportunity %s %s", opp.kind, opp.signature)
            return
        rec = self.memory.get(opp.signature)
        if rec is None:
            self.memory[opp.signature] = ScanRecord(opp.signature, 1, utcnow())
            res.candidates.append(opp)
            return
        rec.seen_count += 1
        rec.last_seen_ts = utcnow()
        if rec.seen_count >= self.cfg.arb.staleness_confirmations:
            opp.confidence = min(1.0, 0.6 + 0.2 * rec.seen_count)
            res.actionable.append(opp)
        else:
            res.candidates.append(opp)

    def plans_for(self, result: ArbScanResult, bankroll: float = 10_000.0,
                  top: int = 12) -> List[dict]:
        out: List[dict] = []
        for opp in result.actionable[:top]:
            sizing: SizingPlan = size_arb(opp, self.cfg, bankroll)
            if sizing.contracts_per_leg <= 0:
                continue
            out.append(execution_plan(opp, sizing))
        return out

    def value_plan_for(self, result: ArbScanResult, bankroll: float = 10_000.0,
                       top: int = 15) -> List[dict]:
        out = []
        for opp in result.value_signals[:top]:
            sizing = size_arb(opp, self.cfg, bankroll)
            if sizing.contracts_per_leg <= 0:
                continue
            out.append(execution_plan(opp, sizing))
        return out
