"""Polymarket connector: gamma API (markets) + CLOB book endpoint (depth).

Live endpoints (public, no key):
  * https://gamma-api.polymarket.com/markets?active=true&closed=false
  * https://clob.polymarket.com/book?token_id=<clobTokenId>
"""
from __future__ import annotations

import json
from typing import List

from ...core.domain import BookLevel, MarketQuote, OutcomeQuote
from ...core.logging_setup import get_logger
from ...core.net import http_json
from ..base import BaseConnector, PullResult

log = get_logger("qa.polymarket")
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class PolymarketConnector(BaseConnector):
    venue = "polymarket"

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        limit = self.vcfg.max_markets if self.vcfg else 60
        data = http_json(f"{GAMMA}/markets", params={
            "active": "true", "closed": "false", "limit": min(limit, 100),
            "order": "volume24hr", "ascending": "false"}, timeout=self.vcfg.request_timeout_s)
        rows = data.get("items", data) if isinstance(data, dict) else data
        markets: List[MarketQuote] = []
        for m in rows:
            try:
                mk = self._parse_market(m)
                if mk:
                    markets.append(mk)
            except Exception as exc:  # noqa: BLE001 - skip malformed rows
                log.debug("skip polymarket row: %s", exc)
        # attach depth for the most liquid subset (be gentle with public API)
        with_books = [m for m in markets if m.volume and m.volume > 1000][:12]
        for mk in with_books:
            self._attach_books(mk)
        res.markets = markets
        return res

    def _parse_market(self, m: dict) -> MarketQuote | None:
        if str(m.get("enableOrderBook", "false")).lower() != "true":
            return None
        try:
            names = json.loads(m.get("outcomes", "[]"))
            prices = json.loads(m.get("outcomePrices", "[]"))
            tokens = json.loads(m.get("clobTokenIds", "[]"))
        except (json.JSONDecodeError, TypeError):
            return None
        if not names or len(tokens) < len(names):
            return None
        tick = _f(m.get("orderPriceMinTickSize"), 0.01) or 0.01
        oqs = []
        for i, name in enumerate(names):
            p = _f(prices[i]) if i < len(prices) else None
            oqs.append(OutcomeQuote(
                key=str(name), label=str(name), probability=p,
                volume=_f(m.get("volume24hr")), tick=tick))
        return MarketQuote(
            venue=self.venue, market_id=str(m.get("conditionId") or m.get("id")),
            title=str(m.get("question", ""))[:200],
            url=f"https://polymarket.com/market/{m.get('slug', '')}",
            category=str(m.get("category") or "other"),
            closes_at=_parse_close(m),
            outcomes=tuple(oqs),
            volume=_f(m.get("volume24hr")) or _f(m.get("volumeNum")),
            liquidity=_f(m.get("liquidityNum")))

    def _attach_books(self, mk: MarketQuote) -> None:
        try:
            tokens = self._token_ids(mk)
            for oq, token in zip(mk.outcomes, tokens):
                book = http_json(f"{CLOB}/book", params={"token_id": token},
                                 timeout=self.vcfg.request_timeout_s, expect=None)
                if not isinstance(book, dict):
                    continue
                bids = [BookLevel(_f(b.get("price")), _f(b.get("size"), 0))
                        for b in book.get("bids", []) if _f(b.get("price")) is not None]
                asks = [BookLevel(_f(a.get("price")), _f(a.get("size"), 0))
                        for a in book.get("asks", []) if _f(a.get("price")) is not None]
                bids = sorted(bids, key=lambda b: -b.price)
                asks = sorted(asks, key=lambda a: a.price)
                if not bids or not asks:
                    continue
                oq2 = OutcomeQuote(
                    key=oq.key, label=oq.label,
                    best_bid=bids[0].price, best_ask=asks[0].price,
                    bid_size=bids[0].size, ask_size=asks[0].size,
                    last_price=oq.probability, probability=oq.probability,
                    volume=oq.volume, tick=oq.tick,
                    bids=tuple(bids[:8]), asks=tuple(asks[:8]))
                mk.outcomes = tuple(
                    oq2 if o.key == oq2.key else o for o in mk.outcomes)
        except Exception as exc:  # noqa: BLE001 - depth is optional
            log.debug("book attach failed %s: %s", mk.market_id, exc)

    def _token_ids(self, mk: MarketQuote) -> List[str]:
        # re-fetch token ids for this market (id may be conditionId)
        try:
            data = http_json(f"{GAMMA}/markets", params={"condition_ids": mk.market_id, "limit": 1},
                             timeout=self.vcfg.request_timeout_s)
            rows = data.get("items", data) if isinstance(data, dict) else data
            if rows:
                return json.loads(rows[0].get("clobTokenIds", "[]"))
        except Exception:  # noqa: BLE001
            pass
        return []


def _parse_close(m: dict):
    for k in ("endDate", "endDateIso"):
        raw = m.get(k)
        if raw:
            try:
                from datetime import datetime, timezone
                txt = str(raw).replace("Z", "+00:00")[:19]
                if len(txt) == 10:
                    txt += "T00:00:00"
                return datetime.fromisoformat(txt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None
