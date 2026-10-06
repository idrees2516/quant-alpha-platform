"""Microstructure estimators: micro-price, EWMA volatility, fill-intensity
calibration (lambda(delta) = A * exp(-k * delta)) via Poisson MLE on book depth.

These estimators feed every quoting strategy: fair value, spread width and
expected fill rates all come from measured book properties, not guesses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from ..core.domain import BookLevel, OutcomeQuote


def micro_price(bids: Sequence[BookLevel], asks: Sequence[BookLevel]) -> Optional[float]:
    """Stoikov micro-price / weighted mid: weights each side by the *opposite*
    size — the corner of the book more likely to lift gets more weight."""
    if not bids or not asks:
        return None
    b, qb = bids[0].price, bids[0].size
    a, qa = asks[0].price, asks[0].size
    if qb + qa <= 0:
        return (a + b) / 2
    return (a * qb + b * qa) / (qb + qa)


def imbalance(bids: Sequence[BookLevel], asks: Sequence[BookLevel]) -> float:
    """Signed book imbalance in [-1, 1]: positive = bid-heavy (buy pressure)."""
    if not bids or not asks:
        return 0.0
    qb = sum(l.size for l in bids[:3])
    qa = sum(l.size for l in asks[:3])
    if qb + qa <= 0:
        return 0.0
    return (qb - qa) / (qb + qa)


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


@dataclass
class VolEstimator:
    """EWMA volatility of the logit mid, per sqrt(hour)."""
    half_life_h: float = 12.0
    floor: float = 0.02
    cap: float = 1.50
    _ewma_var: float = 0.01
    _last_x: Optional[float] = None
    _last_t: Optional[float] = None

    def update(self, mid: float, t_h: float) -> float:
        x = logit(mid)
        if self._last_x is None or self._last_t is None:
            self._last_x, self._last_t = x, t_h
            return self.sigma
        dt = max(1e-6, t_h - self._last_t)
        dx = (x - self._last_x) / math.sqrt(dt)
        alpha = 1 - math.exp(-math.log(2) * dt / self.half_life_h)
        self._ewma_var = (1 - alpha) * self._ewma_var + alpha * dx * dx
        self._last_x, self._last_t = x, t_h
        return self.sigma

    @property
    def sigma(self) -> float:
        return min(max(math.sqrt(max(self._ewma_var, 1e-8)), self.floor), self.cap)


def spread_vol_proxy(oq: OutcomeQuote) -> float:
    """One-shot sigma estimate when history is unavailable: quoted spread
    implies ~1 SD of price noise over the quoting session."""
    sp = oq.spread
    if sp is None:
        return 0.08
    p = oq.mid or 0.5
    return min(max(sp / max(p * (1 - p), 1e-6) * 0.35, 0.02), 1.5)


# ----------------------------------------------------------- intensity ----
@dataclass
class FillIntensity:
    """lambda(delta) = A * exp(-k * delta), delta = distance from fair in
    PRICE units. A: fills/hour at zero distance; k: 1/price-units decay."""
    A: float = 1.2
    k: float = 60.0

    def rate(self, delta: float) -> float:
        if delta <= 0:
            return self.A
        return self.A * math.exp(-self.k * delta)

    @property
    def expected_touch_edge(self) -> float:
        """Mean edge of a fill at the touch under exponential intensity."""
        return 1.0 / self.k


def calibrate_intensity(oq: OutcomeQuote, fair: Optional[float] = None,
                        hours_lookback: float = 24.0,
                        prior: Optional[FillIntensity] = None) -> FillIntensity:
    """MLE-style calibration of (A, k) from resting depth.

    Model: lambda(delta) = A * exp(-k * delta) is the fill intensity of a
    quote at distance delta from fair. Under market equilibrium, resting
    SIZE grows with distance to compensate lower fill probability
    (size * lambda ~ const), so k is estimated as the slope of
    log(level size) vs distance — a positive, increasing-book calibration.

    A: fills/hour at the touch, from traded volume rate (venue 'volume'
    fields are cumulative, so never divide by less than 24h), capped at a
    physically plausible rate.
    """
    prior = prior or FillIntensity()
    fair = fair if fair is not None else (oq.mid or 0.5)
    bids = [l for l in oq.bids if fair - l.price > 0]
    asks = [l for l in oq.asks if l.price - fair > 0]
    points = [(fair - l.price, l.size) for l in bids] + [(l.price - fair, l.size)
                                                          for l in asks]
    if len(points) < 3:
        return FillIntensity(prior.A, prior.k)

    # regress log(size) on LEVEL INDEX (stable: tick multiples are exact),
    # then convert to price units via the venue tick
    tick = max(oq.tick or 0.01, 1e-4)
    by_level: Dict[float, float] = {}
    for d, sz in points:
        lvl = round(d / tick)
        by_level[lvl] = by_level.get(lvl, 0.0) + sz
    levels = sorted(by_level)
    if len(levels) < 2 or levels[-1] == levels[0]:
        return FillIntensity(prior.A, prior.k)
    ys = [math.log(max(by_level[l], 1.0)) for l in levels]
    n = len(levels)
    mx, my = sum(levels) / n, sum(ys) / n
    sxx = sum((l - mx) ** 2 for l in levels)
    if sxx <= 1e-12:
        return FillIntensity(prior.A, prior.k)
    slope_per_level = sum((l - mx) * (y - my) for l, y in zip(levels, ys)) / sxx
    k_est = min(max(slope_per_level / tick, 20.0), 800.0)

    hours = max(hours_lookback, 24.0)
    vol_rate = (oq.volume or 0) / hours if oq.volume else 0.0
    A_est = min(max(0.5 * min(vol_rate, 4000.0) / 200.0 + 0.5 * prior.A, 0.05), 8.0)
    return FillIntensity(A=A_est, k=k_est)


def book_score(bids: Sequence[BookLevel], asks: Sequence[BookLevel], mid: float,
               max_spread: float, size_cap: float, power: float = 2.0
               ) -> Tuple[float, float]:
    """Polymarket-style liquidity-reward score of the CURRENT book:
    (bid_score, ask_score) = sum over resting orders within max_spread of mid."""
    def score(levels, dist_fn):
        s = 0.0
        for l in levels:
            d = dist_fn(l.price)
            if 0 <= d <= max_spread:
                s += min(l.size, size_cap) * (1 - d / max_spread) ** power
        return s
    bid_s = score(bids, lambda p: max(0.0, mid - p))
    ask_s = score(asks, lambda p: max(0.0, p - mid))
    return bid_s, ask_s
