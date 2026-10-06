"""Statistics engine (pure stdlib): Welch t-test, bootstrap CI, OLS with
t-stats, normal CDF via erf. Used by hypothesis + beta modules."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import List, Sequence, Tuple


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_sf(x: float) -> float:
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def std(xs: Sequence[float], ddof: int = 1) -> float:
    n = len(xs)
    if n <= ddof:
        return 0.0
    mu = mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - ddof))


@dataclass
class TTest:
    t: float
    df: float
    p_value: float
    mean: float
    note: str = ""

    @property
    def significant_5pct(self) -> bool:
        return self.p_value < 0.05


def welch_t_test(xs: Sequence[float], mu0: float = 0.0) -> TTest:
    """One-sample t-test against a constant (normal-approx p, df>=5)."""
    n = len(xs)
    if n < 3:
        return TTest(0.0, 0.0, 1.0, mean(xs), "n<3")
    m = mean(xs)
    s = std(xs)
    if s < 1e-12:
        return TTest(float("inf") if m != mu0 else 0.0, n - 1,
                     0.0 if m != mu0 else 1.0, m, "degenerate variance")
    se = s / math.sqrt(n)
    t = (m - mu0) / se
    p = 2 * norm_sf(abs(t))          # normal approx; conservative for n>=30
    return TTest(t, n - 1, min(1.0, p), m)


@dataclass
class BootstrapCI:
    low: float
    high: float
    point: float
    n_resamples: int

    def contains(self, v: float) -> bool:
        return self.low <= v <= self.high


def bootstrap_ci(xs: Sequence[float], statistic=mean, alpha: float = 0.05,
                 n_resamples: int = 2000, seed: int = 1) -> BootstrapCI:
    rng = random.Random(seed)
    n = len(xs)
    if n == 0:
        return BootstrapCI(0.0, 0.0, 0.0, 0)
    stats = []
    for _ in range(n_resamples):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        stats.append(statistic(sample))
    stats.sort()
    lo = stats[int((alpha / 2) * n_resamples)]
    hi = stats[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))]
    return BootstrapCI(lo, hi, statistic(xs), n_resamples)


@dataclass
class OLSResult:
    betas: List[float]
    se: List[float]
    t_stats: List[float]
    p_values: List[float]
    r_squared: float
    n: int

    def summary(self, names: Sequence[str]) -> str:
        lines = []
        for i, b in enumerate(self.betas):
            nm = names[i] if i < len(names) else f"x{i}"
            star = "*" if (self.p_values[i] < 0.05) else " "
            lines.append(f"  {nm:<18} beta={b:>10.4f}  t={self.t_stats[i]:>6.2f}  p={self.p_values[i]:.4f}{star}")
        return "\n".join(lines)


def ols(y: Sequence[float], X: Sequence[Sequence[float]],
        add_intercept: bool = True) -> OLSResult:
    """Ordinary least squares via normal equations (pure Python, n small)."""
    n = len(y)
    k = len(X[0]) + (1 if add_intercept else 0) if X else 0
    if n < k + 2 or k == 0:
        return OLSResult([], [], [], [], 0.0, n)
    rows = [[1.0] + list(row) for row in X] if add_intercept else [list(r) for r in X]
    k = len(rows[0])
    # X'X and X'y
    xtx = [[sum(rows[i][a] * rows[i][b] for i in range(n)) for b in range(k)]
           for a in range(k)]
    xty = [sum(rows[i][a] * y[i] for i in range(n)) for a in range(k)]
    beta = _solve(xtx, xty)
    if beta is None:
        return OLSResult([0.0] * k, [0.0] * k, [0.0] * k, [1.0] * k, 0.0, n)
    yhat = [sum(rows[i][j] * beta[j] for j in range(k)) for i in range(n)]
    resid = [y[i] - yhat[i] for i in range(n)]
    ss_res = sum(r * r for r in resid)
    mu = mean(y)
    ss_tot = sum((v - mu) ** 2 for v in y)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = n - k
    sigma2 = ss_res / dof if dof > 0 else 1e-12
    xtx_inv = _invert(xtx)
    se = [math.sqrt(max(sigma2 * xtx_inv[j][j], 0)) if xtx_inv else 0.0
          for j in range(k)]
    ts = [beta[j] / se[j] if se[j] > 1e-12 else 0.0 for j in range(k)]
    ps = [2 * norm_sf(abs(t)) for t in ts]
    return OLSResult(beta, se, ts, ps, r2, n)


def _solve(A: List[List[float]], b: List[float]) -> List[float] | None:
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        M[col] = [v / pv for v in M[col]]
        for r in range(n):
            if r != col and M[r][col] != 0:
                factor = M[r][col]
                M[r] = [rv - factor * cv for rv, cv in zip(M[r], M[col])]
    return [M[i][n] for i in range(n)]


def _invert(A: List[List[float]]) -> List[List[float]] | None:
    n = len(A)
    M = [row[:] + [1.0 if i == j else 0.0 for j in range(n)]
         for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        M[col] = [v / pv for v in M[col]]
        for r in range(n):
            if r != col and M[r][col] != 0:
                f = M[r][col]
                M[r] = [rv - f * cv for rv, cv in zip(M[r], M[col])]
    return [row[n:] for row in M]
