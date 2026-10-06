"""Cross-venue entity resolution: match the same real-world event and align
its outcomes across Polymarket / Kalshi / PredictIt / Manifold / Metaculus /
sports books.

Pipeline: normalize titles -> block by category+time -> pairwise similarity
(Jaccard on informative tokens + containment for sports team names) -> union-
find clusters -> outcome alignment via YES/NO heuristics, alias table and
fuzzy labels.
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Tuple

from ..core.domain import (MarketQuote, MatchedCluster, MatchedOutcome,
                           SportsQuote, utcnow)

STOPWORDS = {
    "will", "the", "a", "an", "of", "in", "on", "at", "by", "before", "after",
    "vs", "versus", "to", "for", "and", "or", "is", "are", "does", "do",
    "did", "end", "close", "game", "match", "us",
    # NOTE: months / years / numbers are KEPT as tokens — they discriminate
    # time-differentiated markets ("Putin out by Dec 2026" vs "Mar 2027",
    # daily "Bitcoin up or down on Oct 5" vs "Oct 6").
}
TOKEN_RE = re.compile(r"[a-z0-9]+")

ALIASES: Dict[str, str] = {
    "fed": "federal reserve", "fomc": "federal reserve", "powell": "federal reserve",
    "democrats": "democrat", "democratic": "democrat", "republicans": "republican",
    "gop": "republican", "lakers": "lakers", "la": "lakers", "los angeles": "lakers",
    "celtics": "celtics", "boston": "celtics", "nba": "basketball",
    "btc": "bitcoin", "eth": "ethereum", "shutdown": "government shutdown",
}

YES_TOKENS = {"yes", "true", "win", "over", "above", "happens"}
NO_TOKENS = {"no", "false", "lose", "under", "below", "not"}


def norm_title(title: str) -> List[str]:
    toks = TOKEN_RE.findall(title.lower())
    out = []
    for t in toks:
        if t in STOPWORDS or len(t) < 2 or t.isdigit():
            continue
        out.append(ALIASES.get(t, t))
    return out


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def containment(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}
DATE_RE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+(\d{1,2})?(?:\s*,?\s*(\d{4}))?",
    re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20\d{2})\b")


def date_signature(title: str) -> set:
    """Set of (month, day, year) tuples found in the title; used to HARD-SPLIT
    time-differentiated markets (different cutoffs are different events)."""
    sig = set()
    for mo, day, yr in DATE_RE.findall(title):
        sig.add((MONTHS[mo.lower()], int(day) if day else None,
                 int(yr) if yr else None))
    if not sig:
        for yr in YEAR_RE.findall(title):
            sig.add((None, None, int(yr)))
    return sig


def dates_compatible(a: set, b: set) -> bool:
    if not a or not b:
        return True
    for sa in a:
        for sb in b:
            if sa == sb:
                return True
            # same (month, year) with day unspecified on one side -> compatible
            if sa[0] == sb[0] and sa[2] == sb[2] and (sa[1] is None or sb[1] is None):
                return True
    return False


def proper_nouns(title: str) -> set:
    """Capitalized mid-title tokens (names, teams, candidates), lowercased;
    months and common words excluded. First word excluded (sentence case)."""
    raw_tokens = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*", title)
    pn = set()
    for i, t in enumerate(raw_tokens):
        if i == 0 or not t[0].isupper():
            continue
        low = t.lower().strip(".'’-")
        if low in MONTHS or len(low) < 3:
            continue
        pn.add(low)
    return pn


def subject_nouns(title: str) -> set:
    """Proper nouns within the subject half (first 60%) of the title —
    for 'Will <NAME> win ...' questions the name lives there."""
    raw_tokens = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*", title)
    cut = max(1, int(len(raw_tokens) * 0.6))
    pn = set()
    for i, t in enumerate(raw_tokens[:cut]):
        if i == 0 or not t[0].isupper():
            continue
        low = t.lower().strip(".'’-")
        if low in MONTHS or len(low) < 3:
            continue
        pn.add(low)
    return pn


def match_sides(title: str) -> list:
    """For 'A vs B' / 'A @ B' titles, return the two side-name token sets."""
    m = re.search(r"(.+?)\s+(?:vs\.?|versus|@)\s+(.+)$", title, re.IGNORECASE)
    if not m:
        return []
    left = {t.lower() for t in TOKEN_RE.findall(m.group(1)) if len(t) > 2}
    right = {t.lower() for t in TOKEN_RE.findall(m.group(2)) if len(t) > 2}
    return [left, right] if left and right else []


def market_similarity(a: MarketQuote, b: MarketQuote) -> float:
    da, db = date_signature(a.title), date_signature(b.title)
    if not dates_compatible(da, db):
        return 0.03          # hard split: different cutoffs = different events
    # subject discrimination: disjoint proper nouns => different subjects
    pa, pb = proper_nouns(a.title), proper_nouns(b.title)
    if pa and pb and not (pa & pb):
        return 0.03          # e.g. 'Will Lula win...' vs 'Will Bolsonaro win...'
    # subject-PHRASE discrimination: the proper nouns in the subject half of
    # the question ('Will <SUBJECT> ...') must overlap, even if generic words
    # ('Brazilian presidential election') coincide.
    sa, sb = subject_nouns(a.title), subject_nouns(b.title)
    if sa and sb and not (sa & sb):
        return 0.03
    # match-pair discrimination: 'A vs B' titles must share BOTH sides
    sa, sb = match_sides(a.title), match_sides(b.title)
    if sa and sb:
        for i in range(2):
            if not (sa[i] & sb[i]):
                return 0.03  # e.g. Muchova vs Osaka != Sabalenka vs Osaka
    ta, tb = norm_title(a.title), norm_title(b.title)
    sim = max(jaccard(ta, tb), 0.8 * containment(ta, tb))
    # same-category bonus, closes-time proximity bonus
    if a.category and a.category == b.category:
        sim += 0.05
    if a.closes_at and b.closes_at:
        dh = abs((a.closes_at - b.closes_at).total_seconds()) / 3600
        if dh < 24:
            sim += 0.05
        elif dh > 24 * 400:
            sim -= 0.15
    return sim


def sport_similarity(sq: SportsQuote, mq: MarketQuote) -> float:
    toks_m = set(norm_title(mq.title))
    teams = set(norm_title(sq.event_title))
    team_hits = sum(1 for t in teams if t in toks_m)
    if team_hits == 0:
        return 0.0
    base = 0.35 * team_hits / max(1, len(teams))
    if sq.starts_at and mq.closes_at:
        dh = abs((sq.starts_at - mq.closes_at).total_seconds()) / 3600
        if dh < 36:
            base += 0.35
    return base


# ---------------------------------------------------------------------
class UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _align_binary(mkts: List[MarketQuote]) -> List[MatchedOutcome]:
    """Align binary YES/NO: index 0 = affirmative side of the reference market."""
    ref = mkts[0]
    ref_yes = _yes_key(ref) or ref.outcomes[0].key
    ref_no = _no_key(ref) or (ref.outcomes[1].key if len(ref.outcomes) > 1 else "")
    outcomes = [MatchedOutcome(label="YES"), MatchedOutcome(label="NO")]
    for m in mkts:
        yes_k = _yes_key(m) or (m.outcomes[0].key if m.outcomes[0].key != ref_no else None)
        no_k = _no_key(m)
        if not yes_k and m.binary:
            yes_k, no_k = m.outcomes[0].key, m.outcomes[1].key
        if yes_k:
            outcomes[0].keys_by_venue[m.venue] = yes_k
        if no_k:
            outcomes[1].keys_by_venue[m.venue] = no_k
    return outcomes


def _yes_key(m: MarketQuote) -> Optional[str]:
    for o in m.outcomes:
        if o.key.lower() in YES_TOKENS:
            return o.key
    return None


def _no_key(m: MarketQuote) -> Optional[str]:
    for o in m.outcomes:
        if o.key.lower() in NO_TOKENS:
            return o.key
    return None


def _align_multi(mkts: List[MarketQuote]) -> List[MatchedOutcome]:
    """Align multi-outcome markets by fuzzy label matching against reference."""
    ref = max(mkts, key=lambda m: len(m.outcomes))
    outcomes = [MatchedOutcome(label=o.key) for o in ref.outcomes]
    for m in mkts:
        for mo in outcomes:
            best, best_sim = None, 0.4
            for o in m.outcomes:
                sim = max(jaccard(norm_title(o.key), norm_title(mo.label)),
                          containment(norm_title(o.key), norm_title(mo.label)))
                if sim > best_sim:
                    best, best_sim = o, sim
            if best is not None:
                mo.keys_by_venue[m.venue] = best.key
    return outcomes


FORECAST_VENUES = {"manifold", "metaculus"}


def _is_forecast_only(m: MarketQuote) -> bool:
    # Venue-based: Polymarket/Kalshi/PredictIt are TRADEABLE venues even when
    # a particular snapshot carries no depth (books attach to top markets only).
    return m.venue in FORECAST_VENUES


def build_clusters(markets: List[MarketQuote], sports: List[SportsQuote],
                   min_sim: float = 0.42) -> List[MatchedCluster]:
    """Cluster markets by event (tradable + forecast-only), attach sports books."""
    live = [m for m in markets if m.outcomes]
    uf = UnionFind()
    ids = [f"{m.venue}:{m.market_id}" for m in live]
    for (ma, ia), (mb, ib) in combinations(zip(live, ids), 2):
        if market_similarity(ma, mb) >= min_sim:
            uf.union(ia, ib)

    groups: Dict[str, List[MarketQuote]] = {}
    for m, mid in zip(live, ids):
        groups.setdefault(uf.find(mid), []).append(m)

    clusters: List[MatchedCluster] = []
    for i, members in enumerate(groups.values()):
        members.sort(key=lambda m: -(m.volume or 0) or -1e9)
        tradable = [m for m in members if not _is_forecast_only(m)]
        forecasts = [m for m in members if _is_forecast_only(m)]
        if not tradable:
            continue  # forecast-only cluster cannot be traded or arbed
        n_out = max(len(m.outcomes) for m in tradable)
        outcomes = _align_binary(tradable) if n_out <= 2 else _align_multi(tradable)
        closes = min((m.closes_at for m in tradable if m.closes_at), default=None)
        cat = tradable[0].category
        cl = MatchedCluster(
            cluster_id=f"cl-{i:03d}", title=tradable[0].title, category=cat,
            closes_at=closes, outcomes=outcomes, markets=tradable,
            forecasts=[(m.venue, m.outcomes[0].probability or 0.5) for m in forecasts
                       if m.binary])
        clusters.append(cl)

    # attach sports books (binary sports clusters only)
    for sq in sports:
        best, best_sim = None, 0.45
        for cl in clusters:
            for m in cl.markets:
                sim = sport_similarity(sq, m)
                if sim > best_sim:
                    best, best_sim = cl, sim
        if best is not None and best.books is not None:
            # align h2h outcomes to YES/NO by team-name matching
            yes = _yes_key(best.markets[0])
            if yes:
                teams = set(norm_title(sq.event_title))
                ytok = set(norm_title(best.markets[0].find(yes).key or ""))
                for label, _odds in sq.outcomes:
                    lt = set(norm_title(label))
                    mo = best.outcomes[0] if (lt & teams & ytok or lt & ytok) else best.outcomes[1]
                    mo.keys_by_venue.setdefault(f"sports:{sq.bookmaker}", label)
            best.books.append(sq)

    clusters.sort(key=lambda c: -sum(m.volume or 0 for m in c.markets))
    return clusters
