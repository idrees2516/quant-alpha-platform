"""Metaculus connector (forecast-only: community median forecasts).

  GET https://www.metaculus.com/api2/questions/?limit=N&status=open
Cloudflare-gated from many datacenters -> fixture fallback enabled by default.
"""
from __future__ import annotations

from typing import List

from ...core.domain import MarketQuote, OutcomeQuote, parse_ts
from ...core.logging_setup import get_logger
from ...core.net import http_json
from ..base import BaseConnector, PullResult

log = get_logger("qa.metaculus")
BASE = "https://www.metaculus.com/api2"


class MetaculusConnector(BaseConnector):
    venue = "metaculus"

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        limit = self.vcfg.max_markets if self.vcfg else 60
        data = http_json(f"{BASE}/questions/", params={
            "limit": min(limit, 100), "status": "open", "order_by": "-activity"},
            timeout=self.vcfg.request_timeout_s)
        rows = data.get("results", data.get("questions", [])) if isinstance(data, dict) \
            else (data if isinstance(data, list) else [])
        out: List[MarketQuote] = []
        for q in rows:
            try:
                p = self._community_probability(q)
                if p is None:
                    continue
                p = min(max(float(p), 0.001), 0.999)
                title = str(q.get("title", ""))[:200]
                oqs = (OutcomeQuote(key="Yes", label="Yes", probability=round(p, 4),
                                    volume=None, tick=0.01),
                       OutcomeQuote(key="No", label="No", probability=round(1 - p, 4),
                                    volume=None, tick=0.01))
                out.append(MarketQuote(
                    venue=self.venue, market_id=str(q.get("id")),
                    title=title, url=f"https://www.metaculus.com/questions/{q.get('id')}/",
                    category="forecast", closes_at=parse_ts(q.get("resolve_time")),
                    outcomes=oqs, volume=None, liquidity=None))
            except Exception as exc:  # noqa: BLE001
                log.debug("skip metaculus row: %s", exc)
        res.markets = out
        return res

    @staticmethod
    def _community_probability(q: dict) -> float | None:
        cp = q.get("community_prediction") or {}
        hist = cp.get("history") if isinstance(cp, dict) else None
        if hist:
            latest = hist[-1] if isinstance(hist, list) else None
            if isinstance(latest, dict):
                x = latest.get("x2") or latest.get("x1")
                if x is not None:
                    return float(x)
        poss = q.get("possibilities")
        if isinstance(poss, dict):
            low, high = poss.get("low"), poss.get("high")
            if low is not None and high is not None and 0 <= float(low) <= 1 and float(high) >= 1:
                return None
        for key in ("median", "prediction", "forecast"):
            val = q.get(key)
            if isinstance(val, (int, float)):
                return float(val)
        return None
