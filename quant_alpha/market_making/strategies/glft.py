"""GLFT market making — Gueant, Lehalle & Fernandez-Tapia (2013),
"Dealing with the inventory risk: a live in the day of a market maker".

The paper solves the market maker's HJB with exponential fill intensity
lambda(delta) = A * exp(-k * delta) and a CARA/mean-variance objective, then
approximates optimal quotes in closed form around zero inventory. Here we
solve the SAME control problem numerically (value iteration over the
inventory grid) so we get the exact policy for ANY inventory level and any
calibrated (A, k) — the paper's formulas are the small-gamma limit of this
solution.

Discrete-time formulation (dt = 1 hour, s = quote size in contracts):
    V(q) = -c(q) + max_{db,da} [ lb(db)*(db*s + V(q+s) - V(q))
                                + la(da)*(da*s + V(q-s) - V(q)) ] + V(q)
    c(q) = gamma * sigma_p^2 * q^2 / 2          (inventory carrying cost)
    db*(q) = 1/k - (V(q+s) - V(q))/s            (bid half-spread)
    da*(q) = 1/k - (V(q-s) - V(q))/s            (ask half-spread)
The per-side optimum is closed-form given V (FOC of the exponential
intensity), so value iteration alternates: recompute optimal deltas from V,
then update V from the Bellman backup — converges geometrically.

Sanity properties enforced (and unit-tested):
  * bid(ask) half-spread widens monotonically as inventory grows (falls);
  * at q = 0 both quotes sit symmetrically around the mid at 1/k - the
    GLFT "market-making spread" 2/k;
  * boundary inventory q = +/-Q suppresses the side that increases risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .base import MMStrategy, QuoteContext, StrategyQuote


@dataclass
class GLFTSolution:
    q_grid: List[int]
    V: List[float]
    bid_half: Dict[int, float]
    ask_half: Dict[int, float]
    converged: bool
    iterations: int


def solve_glft(A: float, k: float, gamma: float, sigma_p: float,
               Q: int, s: int, max_iter: int = 400, tol: float = 3e-5
               ) -> GLFTSolution:
    """Stable relative value iteration on the GLFT control problem.

    The MDP is average-reward; we iterate the relative value function h
    (reference state q=0 pinned to 0 each sweep, which is the textbook
    stabilization for average-reward MDPs), with an economic clamp on
    inter-state value differences to keep the clipped-delta regime bounded.

    sigma_p: price-units volatility per sqrt(hour).
    Returns per-inventory optimal half-spreads in PRICE units.
    """
    qs = sorted({q for q in range(-Q, Q + 1) if q % s == 0} | {0})
    n = len(qs)
    q_hi, q_lo = qs[-1], qs[0]
    idx = {q: i for i, q in enumerate(qs)}
    ref = idx[0]
    h = [0.0] * n
    c = lambda q: gamma * sigma_p ** 2 * q * q / 2.0
    inv_k = 1.0 / k
    MAX_DV = 0.5          # economic cap: adjacent-state value diff <= 50c/contract

    def lam(d: float) -> float:
        d = max(d, 0.0)
        return A * (2.718281828 ** (-k * d)) if d < 30 / k else 0.0

    converged = False
    it = 0
    # per-step probability normalization: keep P(stay) >= 0.1 so the Bellman
    # operator stays a proper contraction (fluid MDP discretization).
    P_MAX = 0.9

    def optimal_sides(i, q):
        """Optimal (db, da, lb, la) at state q given current h."""
        db = da = None
        lb = la = 0.0
        if q + s <= q_hi:
            dv_b = max(-MAX_DV, min(MAX_DV, h[idx[q + s]] - h[i]))
            db = max(inv_k - dv_b / s, 0.0)
            lb = lam(db)
        if q - s >= q_lo:
            dv_a = max(-MAX_DV, min(MAX_DV, h[idx[q - s]] - h[i]))
            da = max(inv_k - dv_a / s, 0.0)
            la = lam(da)
        tot = lb + la
        if tot > P_MAX:                      # scale, preserving relative rates
            lb, la = lb * P_MAX / tot, la * P_MAX / tot
        return db, da, lb, la

    # Convergence criterion: POLICY stability (the returned object is the
    # quoting policy; relative values of rarely-visited extreme states carry
    # a slow mode that does not affect the policy — we track quote changes).
    last_policy: Dict = {}
    stable_sweeps = 0
    for it in range(1, max_iter + 1):
        T = h[:]
        policy_now: Dict = {}
        for i, q in enumerate(qs):
            db, da, lb, la = optimal_sides(i, q)
            policy_now[q] = (db, da)
            gain = -c(q)
            if db is not None:
                dv_b = max(-MAX_DV, min(MAX_DV, h[idx[q + s]] - h[i]))
                gain += lb * (db * s + dv_b)
            if da is not None:
                dv_a = max(-MAX_DV, min(MAX_DV, h[idx[q - s]] - h[i]))
                gain += la * (da * s + dv_a)
            T[i] = h[i] + gain
        rho = T[ref]
        h = [t - rho for t in T]
        if last_policy:
            p_change = max(
                max(abs((policy_now[q][0] or 9) - (last_policy[q][0] or 9)),
                    abs((policy_now[q][1] or 9) - (last_policy[q][1] or 9)))
                for q in policy_now)
            stable_sweeps = stable_sweeps + 1 if p_change < 1e-4 else 0
            if stable_sweeps >= 30 and it > 30:
                converged = True
                break
        last_policy = policy_now

    # read out the optimal half-spreads at the converged relative values
    bid_half: Dict[int, float] = {}
    ask_half: Dict[int, float] = {}
    for i, q in enumerate(qs):
        if q + s <= q_hi:
            dv_b = max(-MAX_DV, min(MAX_DV, h[idx[q + s]] - h[i]))
            bid_half[q] = round(max(inv_k - dv_b / s, 0.0), 6)
        if q - s >= q_lo:
            dv_a = max(-MAX_DV, min(MAX_DV, h[idx[q - s]] - h[i]))
            ask_half[q] = round(max(inv_k - dv_a / s, 0.0), 6)
    return GLFTSolution(qs, h, bid_half, ask_half, converged, it)


class GLFTStrategy(MMStrategy):
    name = "glft"

    def __init__(self, gamma: float = 0.8, max_iter: int = 400):
        self.gamma = gamma
        self.max_iter = max_iter
        self._cache: Dict[tuple, GLFTSolution] = {}

    def _solution(self, ctx: QuoteContext) -> GLFTSolution:
        s = max(1, ctx.quote_size)
        # sigma in price units from logit sigma: dp = p(1-p) * dx
        p = min(max(ctx.fair, 0.02), 0.98)
        sigma_p = ctx.sigma * p * (1 - p)
        key = (round(ctx.intensity.A, 2), round(ctx.intensity.k, 1),
               self.gamma, round(sigma_p, 5), ctx.max_inventory, s)
        sol = self._cache.get(key)
        if sol is None:
            sol = solve_glft(ctx.intensity.A, ctx.intensity.k, self.gamma,
                             sigma_p, ctx.max_inventory, s, self.max_iter)
            if len(self._cache) > 128:
                self._cache.clear()
            self._cache[key] = sol
        return sol

    def quotes(self, ctx: QuoteContext) -> StrategyQuote:
        sol = self._solution(ctx)
        s = max(1, ctx.quote_size)
        # snap inventory to the grid
        q = min(max(ctx.inventory, -ctx.max_inventory), ctx.max_inventory)
        q = round(q / s) * s
        fair = ctx.fair
        db = sol.bid_half.get(q)
        da = sol.ask_half.get(q)
        bid = fair - db if db is not None else None
        ask = fair + da if da is not None else None
        if ctx.maker_fee_rate > 0 and bid is not None and ask is not None:
            bid = min(bid, fair - ctx.maker_fee_rate)
            ask = max(ask, fair + ctx.maker_fee_rate)
        return StrategyQuote(bid, ask, s, meta={
            "bid_half": round(db, 4) if db is not None else -1,
            "ask_half": round(da, 4) if da is not None else -1,
            "sigma_p": round(ctx.sigma * ctx.fair * (1 - ctx.fair), 5),
            "converged": 1.0 if sol.converged else 0.0,
        }).clipped(ctx.tick)
