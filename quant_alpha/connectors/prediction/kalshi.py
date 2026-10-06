"""Kalshi connector: trade-api v2 public market data + order books.

  * GET /trade-api/v2/markets?status=open&limit=N  (cursor pagination)
  * GET /trade-api/v2/markets/{ticker}/orderbook   (public, no auth)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from ...core.domain import BookLevel, MarketQuote, OutcomeQuote, parse_ts
from ...core.logging_setup import get_logger
from ...core.net import http_json
from ..base import BaseConnector, PullResult

log = get_logger("qa.kalshi")
BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _vol_fp(m):
    return (_f(m.get("volume_24h_fp"), 0) or 0) or (_f(m.get("volume_fp"), 0) or 0)


class KalshiConnector(BaseConnector):
    venue = "kalshi"

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        limit = self.vcfg.max_markets if self.vcfg else 60
        markets: List[dict] = []
        cursor = ""
        for _ in range(3):  # up to 3 pages
            params = {"status": "open", "limit": min(200, max(limit, 50))}
            if cursor:
                params["cursor"] = cursor
            data = http_json(f"{BASE}/markets", params=params,
                             timeout=self.vcfg.request_timeout_s)
            page = (data or {}).get("markets", [])
            markets.extend(page)
            cursor = (data or {}).get("cursor") or ""
            if not cursor or len(markets) >= limit * 3:
                break
        # keep liquid binary markets (Kalshi volume lives in *_fp fields)
        def _vol(m):
            return (_f(m.get("volume_24h_fp"), 0) or 0) or (_f(m.get("volume_fp"), 0) or 0)
        markets = [m for m in markets if m.get("market_type") == "binary" and _vol(m) > 100]
        markets.sort(key=lambda m: -_vol(m))
        out: List[MarketQuote] = []
        for m in markets[:limit]:
            try:
                out.append(self._parse(m))
            except Exception as exc:  # noqa: BLE001
                log.debug("skip kalshi row: %s", exc)
        # orderbooks for the most liquid subset
        for mk in out[:12]:
            self._attach_book(mk)
        res.markets = out
        return res

    def _parse(self, m: dict) -> MarketQuote:
        tick = 0.01
        yes_bid, yes_ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
        no_bid, no_ask = _f(m.get("no_bid_dollars")), _f(m.get("no_ask_dollars"))
        title = str(m.get("title", "") or m.get("ticker", ""))[:200]
        oqs = [OutcomeQuote(key="YES", label="YES", best_bid=yes_bid, best_ask=yes_ask,
                            last_price=_f(m.get("last_price_dollars")),
                            volume=_vol_fp(m), tick=tick),
               OutcomeQuote(key="NO", label="NO", best_bid=no_bid, best_ask=no_ask,
                            last_price=_f(m.get("notional_value_dollars"), 1) and round(
                                1 - (_f(m.get("last_price_dollars"), 0) or 0), 4),
                            volume=_f(m.get("volume")), tick=tick)]
        return MarketQuote(venue=self.venue, market_id=str(m.get("ticker")),
                           title=title,
                           url=f"https://kalshi.com/markets/{str(m.get('event_ticker', '')).lower()}",
                           category=_category(str(m.get("event_ticker", ""))),
                           closes_at=parse_ts(m.get("close_time")),
                           outcomes=tuple(oqs),
                           volume=_vol_fp(m), liquidity=_f(m.get("open_interest_fp")))

    def _attach_book(self, mk: MarketQuote) -> None:
        try:
            book = http_json(f"{BASE}/markets/{mk.market_id}/orderbook",
                             timeout=self.vcfg.request_timeout_s, expect=None)
            if not isinstance(book, dict):
                return
            yes_rows, no_rows = _extract_rows(book)
            yes_bids = [BookLevel(p, s) for p, s in sorted(yes_rows, key=lambda r: -r[0]) if s > 0]
            yes_asks = [BookLevel(p, s) for p, s in sorted(yes_rows, key=lambda r: r[0]) if s > 0]
            no_bids = [BookLevel(p, s) for p, s in sorted(no_rows, key=lambda r: -r[0]) if s > 0]
            no_asks = [BookLevel(p, s) for p, s in sorted(no_rows, key=lambda r: r[0]) if s > 0]
            updates = {"YES": (yes_bids, yes_asks), "NO": (no_bids, no_asks)}
            for key, (bids, asks) in updates.items():
                if not bids or not asks:
                    continue
                oq = mk.find(key)
                if oq:
                    mk.outcomes = tuple(
                        OutcomeQuote(key=oq.key, label=oq.label,
                                     best_bid=bids[0].price, best_ask=asks[0].price,
                                     bid_size=bids[0].size, ask_size=asks[0].size,
                                     last_price=oq.last_price, probability=oq.probability,
                                     volume=oq.volume, tick=oq.tick,
                                     bids=tuple(bids[:8]), asks=tuple(asks[:8]))
                        if o.key == key else o for o in mk.outcomes)
        except Exception as exc:  # noqa: BLE001
            log.debug("kalshi book failed %s: %s", mk.market_id, exc)


def _extract_rows(book: dict) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Orderbook returns either {'yes': [[p,qty]], 'no': [...]} or
    {'orderbook_fp': {'yes_dollars': [...], 'no_dollars': [...]}}."""
    def rows(entries) -> List[Tuple[float, float]]:
        out = []
        for e in entries or []:
            if isinstance(e, dict):
                p = _f(e.get("price") or e.get("price_dollars") or e.get("dollars"))
                s = _f(e.get("quantity") or e.get("count") or e.get("qty"), 0)
            elif isinstance(e, (list, tuple)) and len(e) >= 2:
                p, s = _f(e[0]), _f(e[1], 0)
            else:
                continue
            if p is not None and s is not None:
                out.append((p, s))
        return out
    fp = book.get("orderbook_fp") or {}
    return rows(book.get("yes") or fp.get("yes_dollars")), rows(book.get("no") or fp.get("no_dollars"))


def _category(event_ticker: str) -> str:
    t = event_ticker.upper()
    for key, cat in [("FED", "economics"), ("ECON", "economics"), ("CPI", "economics"),
                     ("WEATHER", "weather"), ("CLIMATE", "weather"), ("HURR", "weather"),
                     ("BTC", "crypto"), ("ETH", "crypto"), ("CRYPTO", "crypto"),
                     ("NFL", "sports"), ("NBA", "sports"), ("NBAG", "sports"),
                     ("UFC", "sports"), ("MLB", "sports"), ("NHL", "sports"),
                     ("TEN", "sports"), ("ATP", "sports"), ("WTA", "sports"),
                     ("EPL", "sports"), ("UCL", "sports"), ("LOL", "sports"),
                     ("CSGO", "sports"), ("DOTA", "sports"), ("MVE", "multi"),
                     ("FLU", "health"), ("COVID", "health")]:
        if key in t:
            return cat
    return "other"
