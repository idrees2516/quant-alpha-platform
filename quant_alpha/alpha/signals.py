"""Alpha signals computed from aggregated multi-venue state.

  * venue-vs-consensus deviations (z-scored) — the value/alpha feed;
  * cross-venue lead-lag: does the sharpest venue (tightest spread) lead the
    consensus? (hypothesis H1 machinery);
  * news pressure per cluster — event-risk tilt for MM quoting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.domain import utcnow
from ..normalize.aggregate import AggregatedMarket


@dataclass
class AlphaSignal:
    cluster_id: str
    title: str
    kind: str                    # 'venue_deviation' | 'lead_lag' | 'news'
    source: str
    outcome_index: int
    value: float
    z: float
    detail: str = ""
    ts: object = None


def venue_deviations(am: AggregatedMarket, z_threshold: float = 1.5
                     ) -> List[AlphaSignal]:
    out: List[AlphaSignal] = []
    for src, probs in am.venue_views.items():
        if not probs:
            continue
        for i, p in enumerate(probs):
            fair = am.consensus[i] if i < len(am.consensus) else None
            if fair is None:
                continue
            # sigma from cross-source dispersion (robust)
            others = [probs[j][i] for j, probs2 in am.venue_views.items()
                      if j != src and i < len(probs2)]
            sigma = _std(others + [fair]) if len(others) >= 2 else max(0.02, fair * 0.1)
            sigma = max(sigma, 0.015)
            z = (p - fair) / sigma
            if abs(z) >= z_threshold:
                out.append(AlphaSignal(
                    cluster_id=am.cluster.cluster_id, title=am.title,
                    kind="venue_deviation", source=src, outcome_index=i,
                    value=round(p, 4), z=round(z, 2),
                    detail=f"{src} {p:.3f} vs consensus {fair:.3f}",
                    ts=utcnow()))
    out.sort(key=lambda s: -abs(s.z))
    return out


def consensus_tilt(aggs: List[AggregatedMarket]) -> List[AlphaSignal]:
    """Cross-venue agreement: when every source agrees on direction vs the
    previous consensus snapshot, tilt the ensemble (momentum of beliefs)."""
    out: List[AlphaSignal] = []
    for am in aggs:
        if not am.binary:
            continue
        p_yes = am.consensus[0]
        views_yes = [probs[0] for probs in am.venue_views.values()
                     if probs and len(probs) > 0]
        if len(views_yes) < 2:
            continue
        disp = _std(views_yes)
        out.append(AlphaSignal(
            cluster_id=am.cluster.cluster_id, title=am.title, kind="dispersion",
            source="ensemble", outcome_index=0, value=round(p_yes, 4),
            z=round(disp / max(1e-9, p_yes * (1 - p_yes)), 3),
            detail=f"cross-source sd={disp:.4f}"))
    return out


def news_tilt(aggs: List[AggregatedMarket]) -> List[AlphaSignal]:
    out = []
    for am in aggs:
        if am.news_pressure > 0.15:
            out.append(AlphaSignal(
                cluster_id=am.cluster.cluster_id, title=am.title, kind="news",
                source="news", outcome_index=0, value=am.news_pressure,
                z=round(am.news_pressure * 4, 2),
                detail=f"news pressure {am.news_pressure:.2f}"))
    return out


def _std(xs: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    return (sum((x - mu) ** 2 for x in xs) / (n - 1)) ** 0.5
