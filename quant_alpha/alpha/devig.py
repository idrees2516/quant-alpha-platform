"""De-vig methods for n-way books (sports and prediction markets) and
implied-probability utilities. Used by alpha signals and consensus."""
from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple


def implied_probs(decimal_odds: Sequence[float]) -> List[float]:
    return [1.0 / max(o, 1.000001) for o in decimal_odds]


def devig_multiplicative(probs: Iterable[float]) -> List[float]:
    p = list(probs)
    s = sum(p)
    return [x / s for x in p] if s > 0 else p


def devig_power(probs: Sequence[float], iterations: int = 50) -> List[float]:
    """Power method: find k such that sum(p_i^k) = 1 (Shin's alternative)."""
    p = list(probs)
    if not p or any(x <= 0 for x in p):
        return devig_multiplicative(p)
    lo, hi = 0.0, 1.0        # k<1 inflates small probs
    for _ in range(iterations):
        k = 0.5 * (lo + hi)
        s = sum(x ** k for x in p)
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = 0.5 * (lo + hi)
    raw = [x ** k for x in p]
    s = sum(raw)
    return [x / s for x in raw] if s > 0 else raw


def devig_shin_two_way(p1: float, p2: float, iterations: int = 60) -> Tuple[float, float]:
    """Shin (1993) for 2-way books: fixed-point on insider share z."""
    if p1 <= 0 or p2 <= 0:
        s = p1 + p2
        return (p1 / s, p2 / s) if s > 0 else (0.5, 0.5)
    lo, hi = 0.0, 0.999
    for _ in range(iterations):
        z = 0.5 * (lo + hi)
        def pp(x: float) -> float:
            root = (z * z + 4 * x * (1 - z)) ** 0.5
            return (root + z * (1 - 2 * x)) / (2 * (1 - z))
        if pp(p1) + pp(p2) > 1.0:
            lo = z
        else:
            hi = z
    z = 0.5 * (lo + hi)
    root = (z * z + 4 * p1 * (1 - z)) ** 0.5
    a = (root + z * (1 - 2 * p1)) / (2 * (1 - z))
    return a, 1 - a


def devig(probs: Sequence[float], method: str = "multiplicative") -> List[float]:
    if method == "power":
        return devig_power(probs)
    if method == "shin" and len(probs) == 2:
        a, _ = devig_shin_two_way(probs[0], probs[1])
        return [a, 1 - a]
    return devig_multiplicative(probs)


def book_margin(probs: Sequence[float]) -> float:
    return sum(probs) - 1.0


def edge_on_fair(price: float, fair: float) -> float:
    """Edge (in probability units) of buying `price` vs fair value."""
    return fair - price
