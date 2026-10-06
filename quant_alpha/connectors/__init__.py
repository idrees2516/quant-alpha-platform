"""Venue registry: one place that knows every connector class."""
from __future__ import annotations

from typing import Dict, List

from ..core.config import Config
from ..core.logging_setup import get_logger
from .base import BaseConnector, PullResult
from .feeds import (EspnScoreboardConnector, GoogleNewsConnector, NewsApiConnector,
                    TheOddsApiConnector)
from .prediction.kalshi import KalshiConnector
from .prediction.manifold import ManifoldConnector
from .prediction.metaculus import MetaculusConnector
from .prediction.polymarket import PolymarketConnector
from .prediction.predictit import PredictItConnector

log = get_logger("qa.registry")

PREDICTION_VENUES = ["polymarket", "kalshi", "predictit", "manifold", "metaculus"]


def build_connectors(cfg: Config) -> Dict[str, BaseConnector]:
    return {
        "polymarket": PolymarketConnector(cfg),
        "kalshi": KalshiConnector(cfg),
        "predictit": PredictItConnector(cfg),
        "manifold": ManifoldConnector(cfg),
        "metaculus": MetaculusConnector(cfg),
        "theoddsapi": TheOddsApiConnector(cfg),
        "espn": EspnScoreboardConnector(cfg),
        "gnews": GoogleNewsConnector(cfg),
        "newsapi": NewsApiConnector(cfg),
    }


def pull_all(cfg: Config, venues: List[str] | None = None) -> List[PullResult]:
    connectors = build_connectors(cfg)
    wanted = venues or list(connectors)
    results: List[PullResult] = []
    for name in wanted:
        conn = connectors.get(name)
        if conn is None:
            log.warning("unknown venue %s", name)
            continue
        res = conn.pull()
        results.append(res)
        log.info("venue=%-12s mode=%-9s markets=%-4d sports=%-3d news=%-3d %s",
                 res.venue, res.mode, len(res.markets), len(res.sports),
                 len(res.news), res.error[:80] if res.error else "")
    return results
