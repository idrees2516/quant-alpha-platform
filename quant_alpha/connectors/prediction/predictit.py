"""PredictIt connector (public marketdata API). Often Cloudflare-gated from
datacenters -> fixture fallback keeps the platform fully functional offline.

  GET https://www.predictit.org/api/marketdata/all/
Fields per contract: bestBuyYesCost / bestSellYesCost = ask / bid for YES;
bestBuyNoCost / bestSellNoCost = ask / bid for NO.
"""
from __future__ import annotations

from typing import List

from ...core.domain import MarketQuote, OutcomeQuote, parse_ts
from ...core.logging_setup import get_logger
from ...core.net import http_json
from ..base import BaseConnector, PullResult

log = get_logger("qa.predictit")
BASE = "https://www.predictit.org/api/marketdata"


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class PredictItConnector(BaseConnector):
    venue = "predictit"

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        limit = self.vcfg.max_markets if self.vcfg else 60
        data = http_json(f"{BASE}/all/", timeout=self.vcfg.request_timeout_s)
        rows = data.get("markets", []) if isinstance(data, dict) else []
        out: List[MarketQuote] = []
        for mk in rows[:limit]:
            try:
                oqs = []
                for c in mk.get("contracts", []):
                    oqs.append(OutcomeQuote(
                        key=str(c.get("name", c.get("id"))),
                        label=str(c.get("shortName") or c.get("name", "")),
                        best_bid=_f(c.get("bestSellYesCost")),
                        best_ask=_f(c.get("bestBuyYesCost")),
                        last_price=_f(c.get("lastTradePrice")),
                        volume=None, tick=0.01))
                if not oqs:
                    continue
                # NO side book: synthesize per-contract NO quotes as own markets are
                # per-candidate; complementary arb uses bestBuyNoCost downstream.
                for c, oq in zip(mk.get("contracts", []), oqs):
                    no_ask = _f(c.get("bestBuyNoCost"))
                    no_bid = _f(c.get("bestSellNoCost"))
                    oq2 = OutcomeQuote(
                        key=oq.key, label=oq.label, best_bid=oq.best_bid,
                        best_ask=oq.best_ask, bid_size=None, ask_size=None,
                        last_price=oq.last_price, volume=oq.volume, tick=oq.tick,
                        probability=oq.probability)
                    oqs[oqs.index(oq)] = oq2
                    oq_no = OutcomeQuote(
                        key=f"NO::{oq.key}", label=f"No {oq.label}",
                        best_bid=no_bid, best_ask=no_ask, last_price=None,
                        volume=None, tick=0.01)
                    oqs.append(oq_no)
                out.append(MarketQuote(
                    venue=self.venue, market_id=str(mk.get("id")),
                    title=str(mk.get("name", ""))[:200],
                    url=str(mk.get("url", "")),
                    category="politics",
                    closes_at=parse_ts(mk.get("dateEnd")),
                    outcomes=tuple(oqs), volume=None, liquidity=None))
            except Exception as exc:  # noqa: BLE001
                log.debug("skip predictit row: %s", exc)
        res.markets = out
        return res
