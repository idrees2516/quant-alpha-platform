"""Manifold Markets connector (forecast-only venue: AMM, no resting book).

  GET https://api.manifold.markets/v0/markets?limit=N
Binary markets carry `probability`; multi-choice are skipped (no clean per-
outcome probability at this endpoint).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from ...core.domain import MarketQuote, OutcomeQuote
from ...core.logging_setup import get_logger
from ...core.net import http_json
from ..base import BaseConnector, PullResult

log = get_logger("qa.manifold")
BASE = "https://api.manifold.markets/v0"


def _ts_ms(x) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(x) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


class ManifoldConnector(BaseConnector):
    venue = "manifold"

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        limit = self.vcfg.max_markets if self.vcfg else 60
        data = http_json(f"{BASE}/markets", params={"limit": min(limit, 500)},
                         timeout=self.vcfg.request_timeout_s)
        rows = data.get("items", data) if isinstance(data, dict) else data
        out: List[MarketQuote] = []
        for m in rows:
            try:
                if m.get("outcomeType") != "BINARY" or m.get("isResolved"):
                    continue
                p = m.get("probability")
                if p is None:
                    continue
                p = min(max(float(p), 0.001), 0.999)
                vol = m.get("volume24Hours") or m.get("volume") or 0
                oqs = (OutcomeQuote(key="Yes", label="Yes", probability=round(p, 4),
                                    volume=float(vol), tick=0.01),
                       OutcomeQuote(key="No", label="No", probability=round(1 - p, 4),
                                    volume=float(vol), tick=0.01))
                out.append(MarketQuote(
                    venue=self.venue, market_id=str(m.get("id")),
                    title=str(m.get("question", ""))[:200],
                    url=str(m.get("url", "")),
                    category=str(m.get("groupSlug") or "other"),
                    closes_at=_ts_ms(m.get("closeTime")),
                    outcomes=oqs, volume=float(vol), liquidity=None))
            except Exception as exc:  # noqa: BLE001
                log.debug("skip manifold row: %s", exc)
        out.sort(key=lambda m: -(m.volume or 0))
        res.markets = out[:limit]
        return res
