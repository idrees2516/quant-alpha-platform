"""Canonical domain model shared by connectors, aggregation, arb and MM.

Everything is a frozen dataclass -> hashable, immutable, safe to persist.
Money conventions:
  * prediction-market contract prices live in [0, 1] (USD per $1 payout);
  * sports odds are decimal (>= 1.01) and converted at the connector boundary;
  * timestamps are timezone-aware UTC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Venue(str, Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"
    PREDICTIT = "predictit"
    MANIFOLD = "manifold"
    METACULUS = "metaculus"
    THEODDSAPI = "theoddsapi"
    ESPN = "espn"
    GNEWS = "gnews"
    NEWSAPI = "newsapi"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        txt = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.price <= 1.0):
            raise ValueError(f"book price out of [0,1]: {self.price}")


@dataclass(frozen=True)
class OutcomeQuote:
    """One tradable outcome (YES leg / candidate / team) on one venue."""
    key: str                          # venue-native outcome key ('Yes','No',candidate…)
    label: str = ""                   # human label (defaults to key)
    best_bid: Optional[float] = None  # best resting bid for this outcome
    best_ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    last_price: Optional[float] = None
    volume: Optional[float] = None
    probability: Optional[float] = None   # forecast-only venues (Manifold/Metaculus)
    bids: Tuple[BookLevel, ...] = ()
    asks: Tuple[BookLevel, ...] = ()
    tick: float = 0.01

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return 0.5 * (self.best_bid + self.best_ask)
        if self.probability is not None:
            return self.probability
        return self.best_bid or self.best_ask or self.probability

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None


@dataclass(frozen=True)
class MarketQuote:
    """A normalized market from one venue (arbitrary n outcomes)."""
    venue: str
    market_id: str
    title: str
    url: str = ""
    category: str = "other"
    closes_at: Optional[datetime] = None
    outcomes: Tuple[OutcomeQuote, ...] = ()
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    mutually_exclusive: bool = True    # outcomes partition the event space
    fetched_at: datetime = field(default_factory=utcnow)

    def find(self, key: str) -> Optional[OutcomeQuote]:
        for o in self.outcomes:
            if o.key.lower() == key.lower() or o.label.lower() == key.lower():
                return o
        return None

    @property
    def binary(self) -> bool:
        return len(self.outcomes) == 2


@dataclass(frozen=True)
class SportsQuote:
    """Sports book quote: one bookmaker's prices on one game, decimal odds."""
    venue: str                         # 'theoddsapi'
    bookmaker: str
    sport: str
    event_title: str
    starts_at: Optional[datetime]
    market_type: str = "h2h"           # h2h | totals | spreads
    outcomes: Tuple[Tuple[str, float], ...] = ()   # (label, decimal odds)
    fetched_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class NewsItem:
    source: str
    title: str
    url: str
    published_at: Optional[datetime]
    summary: str = ""
    fetched_at: datetime = field(default_factory=utcnow)


@dataclass
class MatchedOutcome:
    """Consensus-mapped outcome across venues (index-aligned)."""
    label: str
    keys_by_venue: Dict[str, str] = field(default_factory=dict)   # venue -> outcome key


@dataclass
class MatchedCluster:
    """One real-world event resolved across venues + forecast sources."""
    cluster_id: str
    title: str
    category: str
    closes_at: Optional[datetime]
    outcomes: List[MatchedOutcome] = field(default_factory=list)
    markets: List[MarketQuote] = field(default_factory=list)      # tradable venues
    forecasts: List[Tuple[str, float]] = field(default_factory=list)  # (source, p_outcome0..) probabilities per outcome
    books: List[SportsQuote] = field(default_factory=list)        # sports books on same event

    @property
    def has_tradable(self) -> bool:
        return bool(self.markets)

    def outcome_probability_views(self) -> Dict[int, Dict[str, float]]:
        """outcome_index -> {source: implied probability} (books are de-vigged upstream)."""
        views: Dict[int, Dict[str, float]] = {i: {} for i in range(len(self.outcomes))}
        for src, probs in self.forecasts:
            if isinstance(probs, float):
                if len(self.outcomes) == 2:
                    views[0][src] = probs
                    views[1][src] = 1.0 - probs
            else:
                for i, p in enumerate(probs):
                    if i < len(views):
                        views[i][src] = float(p)
        for m in self.markets:
            for i, mo in enumerate(self.outcomes):
                oq = m.find(mo.keys_by_venue.get(m.venue, mo.label))
                if oq and oq.mid is not None:
                    views[i][f"{m.venue}"] = float(min(max(oq.mid, 0.001), 0.999))
        return views


@dataclass(frozen=True)
class Fill:
    ts: datetime
    side: str                    # 'bid' (we bought) | 'ask' (we sold)
    price: float
    qty: float
    realized_edge: float         # signed edge vs fair at fill time
    mid_before: float


@dataclass
class QuotePlan:
    venue: str
    market_id: str
    outcome_key: str
    bid: Optional[float]
    ask: Optional[float]
    size: int
    strategy: str
    fair: float
    inventory: int = 0
    expected_hourly_earnings: float = 0.0
    components: Dict[str, float] = field(default_factory=dict)
