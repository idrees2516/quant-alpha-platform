"""Deterministic offline fixtures.

They simulate the *live* world closely enough to exercise every subsystem:
  * overlapping events across 5 prediction venues (for matching + aggregation);
  * planted cross-venue complementary arb and an in-venue Dutch-book arb;
  * sports books with a Dutch-book arb on a 2-way game;
  * full depth ladders so MM intensity calibration + reward scoring are real;
  * a seeded mid-price history generator for MM backtests.
"""
from __future__ import annotations

import math
import random
import zlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from .base import PullResult
from ..core.domain import (BookLevel, MarketQuote, NewsItem, OutcomeQuote,
                           SportsQuote, utcnow)

_SEED = 20261006


def _book(mid: float, tick: float, depth_usd: float, rng: random.Random
          ) -> Tuple[Tuple[BookLevel, ...], Tuple[BookLevel, ...]]:
    """Depth ladder with sizes ACCUMULATING away from the touch (real book
    shape: modest best level, deeper levels larger — cumulative depth grows
    roughly quadratically with distance, matching real queue structure)."""
    bids, asks = [], []
    base = depth_usd / 8.0
    for i in range(8):
        bp = round(max(tick, mid - tick * (i + 1)), 4)
        ap = round(min(1 - tick, mid + tick * (i + 1)), 4)
        size = base * ((i + 1) ** 1.3) * (0.8 + 0.4 * rng.random())
        if ap > bp:
            bids.append(BookLevel(bp, round(size, 1)))
            asks.append(BookLevel(ap, round(size, 1)))
    return tuple(bids), tuple(asks)


def _oq(key: str, mid: float, tick: float, depth: float, rng: random.Random,
        prob: bool = False) -> OutcomeQuote:
    if prob:
        return OutcomeQuote(key=key, label=key, probability=round(mid, 4),
                            volume=round(depth * 50, 1), tick=tick)
    bids, asks = _book(mid, tick, depth, rng)
    return OutcomeQuote(
        key=key, label=key,
        best_bid=bids[0].price if bids else None,
        best_ask=asks[0].price if asks else None,
        bid_size=bids[0].size if bids else None,
        ask_size=asks[0].size if asks else None,
        bids=bids, asks=asks, volume=round(depth * 40, 1), tick=tick)


# --------------------------------------------------------------------------
# Canonical fixture events. Tuples: (event_title, category, closes_offset_d,
#   {venue: (market_title, [(outcome_key, mid, depth), ...], tick)})
# Planted arbs:
#   A. cross-venue complementary: FED: YES ask 0.615 (poly) + NO ask 0.375 (kalshi) = 0.990
#   B. in-venue Dutch: PRES28 sum of asks on polymarket = 0.982
#   C. sports Dutch: best Lakers 2.10 + best Celtics 2.05 -> implied 0.964
# --------------------------------------------------------------------------
_T = timedelta(days=180)
EVENTS: List[Dict] = [
    dict(title="Fed cuts rates before July 2026?", category="economics",
         closes=timedelta(days=250),
         markets={
             "polymarket": ("Fed rate cut before July 2026?",
                            [("Yes", 0.608, 9000), ("No", 0.392, 9000)], 0.001),
             "kalshi": ("FED rate cut by July 2026", [("YES", 0.604, 7000), ("NO", 0.375, 7000)], 0.01),
             "predictit": ("Fed rate cut by July?", [("Yes", 0.615, 4000), ("No", 0.390, 4000)], 0.01),
         },
         forecasts={"manifold": 0.612, "metaculus": 0.618}),
    dict(title="2028 US presidential election winner", category="politics",
         closes=timedelta(days=790),
         markets={
             "polymarket": ("Presidential Election Winner 2028",
                            [("Vance", 0.295, 12000), ("Newsom", 0.152, 8000),
                             ("Obama", 0.105, 6000), ("Other", 0.430, 9000)], 0.001),
             "kalshi": ("US Pres 2028 winner", [("VANCE", 0.305, 5000), ("NEWSOM", 0.160, 4000),
                                                 ("OBAMA", 0.115, 3000), ("OTHER", 0.452, 5000)], 0.01),
             "predictit": ("2028 Presidential winner", [("Vance", 0.31, 2500), ("Newsom", 0.17, 2000)], 0.01),
         },
         forecasts={}),
    dict(title="Bitcoin above $150k at end of 2026?", category="crypto",
         closes=timedelta(days=440),
         markets={
             "polymarket": ("Bitcoin above $150,000 on Dec 31, 2026?",
                            [("Yes", 0.362, 5000), ("No", 0.638, 5000)], 0.001),
             "kalshi": ("BTC above 150k on Dec 31 2026", [("YES", 0.355, 3000), ("NO", 0.645, 3000)], 0.01),
         },
         forecasts={"manifold": 0.359, "metaculus": 0.365}),
    dict(title="Lakers beat Celtics (Oct 6 game)", category="sports",
         closes=timedelta(hours=9),
         markets={
             "kalshi": ("Lakers win vs Celtics Oct 6", [("YES", 0.470, 2500), ("NO", 0.540, 2500)], 0.01),
             "predictit": ("Lakers vs Celtics", [("Yes", 0.465, 1200), ("No", 0.545, 1200)], 0.01),
         },
         forecasts={"manifold": 0.472}),
    dict(title="US government shutdown ends before November?", category="politics",
         closes=timedelta(days=40),
         markets={
             "polymarket": ("Government shutdown ends before November?",
                            [("Yes", 0.632, 7000), ("No", 0.358, 7000)], 0.001),
             "kalshi": ("Shutdown ends before Nov", [("YES", 0.625, 4000), ("NO", 0.378, 4000)], 0.01),
         },
         forecasts={"metaculus": 0.640}),
]


def fixture_markets() -> List[MarketQuote]:
    rng = random.Random(_SEED)
    out: List[MarketQuote] = []
    for i, ev in enumerate(EVENTS):
        closes = utcnow() + ev["closes"]
        for venue, (title, outcomes, tick) in ev["markets"].items():
            oqs = tuple(_oq(k, mid, tick, depth, rng) for k, mid, depth in outcomes)
            out.append(MarketQuote(
                venue=venue, market_id=f"fx-{venue}-{i}",
                title=title, url=f"https://fixture.local/{venue}/{i}",
                category=ev["category"], closes_at=closes, outcomes=oqs,
                volume=sum(o.volume or 0 for o in oqs) * 3,
                liquidity=sum((o.bid_size or 0) + (o.ask_size or 0) for o in oqs) * 25))
        # forecast-only venues
        for src, p in ev["forecasts"].items():
            if len(ev["markets"]) == 1 and "polymarket" not in ev["markets"]:
                continue
            main = list(ev["markets"].values())[0]
            n_out = len(main[1])
            if n_out == 2:
                probs = [p, 1 - p]
            else:
                probs = None
            if probs:
                oqs = tuple(OutcomeQuote(key=("Yes" if j == 0 else "No"),
                                         label=("Yes" if j == 0 else "No"),
                                         probability=round(probs[j], 4),
                                         volume=800.0 + 400 * j, tick=0.01)
                            for j in range(2))
            else:  # politics multi: assign forecast to Vance only via single-prob market
                continue
            out.append(MarketQuote(
                venue=src, market_id=f"fx-{src}-{i}", title=main[0],
                url=f"https://fixture.local/{src}/{i}", category=ev["category"],
                closes_at=closes, outcomes=oqs,
                volume=1200.0, liquidity=None))
    return out


def fixture_sports() -> List[SportsQuote]:
    starts = utcnow() + timedelta(hours=7)
    game = [("Lakers", 2.10), ("Celtics", 1.72)]
    game_b = [("Lakers", 1.85), ("Celtics", 2.05)]
    return [
        SportsQuote(venue="theoddsapi", bookmaker="BookA", sport="basketball_nba",
                    event_title="Lakers @ Celtics", starts_at=starts,
                    outcomes=tuple(game)),
        SportsQuote(venue="theoddsapi", bookmaker="BookB", sport="basketball_nba",
                    event_title="LA Lakers at Boston Celtics", starts_at=starts,
                    outcomes=tuple(game_b)),
    ]


def fixture_news() -> List[NewsItem]:
    now = utcnow()
    items = [
        ("Reuters", "Fed officials signal openness to July rate cut as inflation cools",
         now - timedelta(hours=2)),
        ("AP", "Powell: policy is 'well positioned', market pricing ~60% cut odds",
         now - timedelta(hours=5)),
        ("Bloomberg", "Lakers rule out starting center for Celtics game",
         now - timedelta(hours=1)),
        ("Politico", "Shutdown talks stall as Senate recess looms",
         now - timedelta(hours=9)),
        ("CNBC", "Bitcoin consolidates below $120k ahead of year-end",
         now - timedelta(hours=12)),
    ]
    return [NewsItem(source=s, title=t, url=f"https://fixture.local/news/{i}",
                     published_at=ts) for i, (s, t, ts) in enumerate(items)]


def book_history(market: MarketQuote, outcome_key: str, steps: int = 240,
                 step_h: float = 1.0, seed: int = _SEED) -> List[OutcomeQuote]:
    """Seeded mid random-walk (logit space) with sticky depth, for MM backtests."""
    rng = random.Random(seed + zlib.crc32(f"{market.market_id}:{outcome_key}".encode()) % 10_000)
    o = market.find(outcome_key)
    if not o or o.mid is None:
        return []
    x = math.log(o.mid / (1 - o.mid))
    # per-step vol in logit space: 0.035/sqrt(h) ~= 0.9c per hour on a 50c
    # contract — lively but realistic for an active prediction market
    sigma = 0.035 * math.sqrt(step_h)
    series: List[OutcomeQuote] = []
    depth = float(o.bid_size or o.ask_size or 500)
    for _ in range(steps):
        x += rng.gauss(0, sigma)
        p = 1 / (1 + math.exp(-x))
        p = min(max(p, 0.02), 0.98)
        nq = _oq(outcome_key, p, o.tick, depth * (0.85 + 0.3 * rng.random()), rng)
        series.append(nq)
    return series


def pull_fixture(venue: str) -> PullResult:
    res = PullResult(venue, "fixtures")
    mkts = fixture_markets()
    res.markets = [m for m in mkts if m.venue == venue]
    if venue == "theoddsapi":
        res.sports = fixture_sports()
    if venue in ("gnews", "newsapi"):
        res.news = fixture_news()
    return res
