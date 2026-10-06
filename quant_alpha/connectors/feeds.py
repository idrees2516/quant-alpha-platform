"""Sports + news connectors.

Sports:
  * The Odds API (v4) — needs THE_ODDS_API_KEY; returns decimal-odds h2h quotes
    per bookmaker (key-gated -> fixture fallback otherwise).
  * ESPN public scoreboard — game state context (free, no key).

News:
  * Google News RSS (free) — parsed with stdlib xml.etree.
  * NewsAPI.org — needs NEWSAPI_KEY.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from ..core.config import Config
from ..core.domain import NewsItem, SportsQuote, utcnow
from ..core.logging_setup import get_logger
from ..core.net import http_json, http_text
from .base import BaseConnector, PullResult

log = get_logger("qa.connectors")


# ---------------------------------------------------------------- sports ---
class TheOddsApiConnector(BaseConnector):
    """https://api.the-odds-api.com/v4/sports/{sport}/odds?regions=us,eu&markets=h2h"""
    venue = "theoddsapi"
    BASE = "https://api.the-odds-api.com/v4"

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        key = self.cfg.api.get("THE_ODDS_API_KEY", "")
        if not key:
            raise RuntimeError("THE_ODDS_API_KEY not configured")
        out: List[SportsQuote] = []
        for sport in ("basketball_nba", "americanfootball_nfl", "soccer_epl"):
            try:
                data = http_json(f"{self.BASE}/sports/{sport}/odds", params={
                    "apiKey": key, "regions": "us,eu", "markets": "h2h",
                    "oddsFormat": "decimal", "dateFormat": "iso"},
                    timeout=self.vcfg.request_timeout_s, expect=list)
            except Exception as exc:  # noqa: BLE001
                log.info("odds-api %s unavailable: %s", sport, exc)
                continue
            for ev in data or []:
                starts = ev.get("commence_time")
                st = _iso(starts)
                for bm in ev.get("bookmakers", []):
                    for mk in bm.get("markets", []):
                        if mk.get("key") != "h2h":
                            continue
                        outcomes = tuple(
                            (str(o.get("label", "")), float(o.get("price", 0)))
                            for o in mk.get("outcomes", []) if o.get("price"))
                        if len(outcomes) >= 2:
                            out.append(SportsQuote(
                                venue=self.venue, bookmaker=str(bm.get("title", bm.get("key", ""))),
                                sport=sport, event_title=str(ev.get("home_team", "")) + " @ " +
                                str(ev.get("away_team", ev.get("home_team", ""))),
                                starts_at=st, market_type="h2h", outcomes=outcomes))
        res.sports = out
        return res


class EspnScoreboardConnector(BaseConnector):
    """Public ESPN scoreboard: live game state for news/alpha context."""
    venue = "espn"
    BASE = "https://site.api.espn.com/apis/site/v2/sports"

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        events: List[NewsItem] = []
        for league in ("basketball/nba", "football/nfl", "soccer/eng.1"):
            try:
                data = http_json(f"{self.BASE}/{league}/scoreboard", timeout=6.0)
            except Exception:  # noqa: BLE001
                continue
            for ev in (data or {}).get("events", [])[:10]:
                comps = ev.get("competitions", [{}])
                status = comps[0].get("status", {}).get("type", {}).get("shortDetail", "")
                events.append(NewsItem(
                    source="espn", title=f"{ev.get('name', '')} — {status}",
                    url=str(ev.get("links", [{}])[0].get("href", "")),
                    published_at=_iso(ev.get("date")), summary=str(ev.get("summary") or "")[:200]))
        res.news = events
        return res


# ------------------------------------------------------------------ news ---
class GoogleNewsConnector(BaseConnector):
    venue = "gnews"

    def __init__(self, cfg: Config, queries: Optional[List[str]] = None):
        super().__init__(cfg)
        self.queries = queries or ["election prediction market", "sports betting odds",
                                   "federal reserve rate decision", "kalshi polymarket"]

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        items: List[NewsItem] = []
        seen = set()
        for q in self.queries:
            url = (f"https://news.google.com/rss/search?q={q.replace(' ', '+')}"
                   f"&hl=en-US&gl=US&ceid=US:en")
            try:
                xml = http_text(url, timeout=8.0)
            except Exception as exc:  # noqa: BLE001
                log.info("gnews query '%s' failed: %s", q, exc)
                continue
            for item in _parse_rss(xml, "gnews")[:10]:
                if item.title not in seen:
                    seen.add(item.title)
                    items.append(item)
        res.news = items
        return res


class NewsApiConnector(BaseConnector):
    venue = "newsapi"
    BASE = "https://newsapi.org/v2"

    def fetch_live(self) -> PullResult:
        res = PullResult(self.venue, "live")
        key = self.cfg.api.get("NEWSAPI_KEY", "")
        if not key:
            raise RuntimeError("NEWSAPI_KEY not configured")
        data = http_json(f"{self.BASE}/everything", params={
            "q": "prediction market OR kalshi OR polymarket OR sportsbook odds",
            "language": "en", "sortBy": "publishedAt", "pageSize": 50,
            "apiKey": key}, timeout=self.vcfg.request_timeout_s)
        items = []
        for a in (data or {}).get("articles", []):
            ts = _iso(a.get("publishedAt"))
            if ts and ts < utcnow() - timedelta(days=2):
                continue
            items.append(NewsItem(source=a.get("source", {}).get("name", "newsapi"),
                                  title=str(a.get("title", ""))[:200],
                                  url=str(a.get("url", "")), published_at=ts,
                                  summary=str(a.get("description") or "")[:300]))
        res.news = items
        return res


# ---------------------------------------------------------------- helpers ---
def _parse_rss(xml: str, source: str) -> List[NewsItem]:
    out: List[NewsItem] = []
    try:
        root = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        pub = it.findtext("pubDate")
        ts = None
        if pub:
            try:
                ts = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except ValueError:
                ts = None
        out.append(NewsItem(source=source, title=it.findtext("title") or "",
                            url=it.findtext("link") or "", published_at=ts,
                            summary=(it.findtext("description") or "")[:300]))
    return out


def _iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
