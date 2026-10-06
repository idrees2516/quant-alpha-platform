"""Calibration scoring + Platt scaling for every probability source.

Brier score, log loss, reliability bins, and a Platt (logistic) recalibration
fitted per source. Calibration weights feed back into the consensus blender,
closing the loop: sources that predict better get more consensus weight.
"""
from __future__ import annotations

import math

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .stats import ols


@dataclass
class CalibrationReport:
    source: str
    n: int
    brier: float
    log_loss: float
    reliability: List[Tuple[float, float]]     # (bin_mid, empirical_freq)
    platt_a: float = 1.0
    platt_b: float = 0.0
    weight: float = 1.0

    def to_dict(self) -> dict:
        return {
            "source": self.source, "n": self.n,
            "brier": round(self.brier, 4), "log_loss": round(self.log_loss, 4),
            "reliability": [(round(a, 2), round(b, 3)) for a, b in self.reliability],
            "platt": [round(self.platt_a, 3), round(self.platt_b, 3)],
            "consensus_weight": round(self.weight, 3),
        }


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    if not probs:
        return 0.5
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def log_loss(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    eps = 1e-7
    if not probs:
        return 1.0
    return -sum(o * math.log(max(p, eps)) + (1 - o) * math.log(max(1 - p, eps))
                for p, o in zip(probs, outcomes)) / len(probs)


def reliability_curve(probs: Sequence[float], outcomes: Sequence[int],
                      n_bins: int = 5) -> List[Tuple[float, float]]:
    bins: List[List[int]] = [[] for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(n_bins - 1, int(p * n_bins))
        bins[idx].append(o)
    curve = []
    for i, grp in enumerate(bins):
        if grp:
            curve.append(((i + 0.5) / n_bins, sum(grp) / len(grp)))
    return curve


def fit_platt(probs: Sequence[float], outcomes: Sequence[int]
              ) -> Tuple[float, float]:
    """Logistic recalibration p' = sigmoid(a * logit(p) + b) via IRLS-lite:
    we regress outcome on logit(p) with logistic loss using a few Newton
    steps (robust to separation with tiny ridge)."""
    if len(probs) < 8:
        return 1.0, 0.0
    xs = [max(-8.0, min(8.0, _logit(max(1e-4, min(1 - 1e-4, p))))) for p in probs]
    ys = list(outcomes)
    a, b = 1.0, 0.0
    ridge = 1e-3
    for _ in range(60):
        # gradient + Hessian of logistic loss
        g_a = g_b = 0.0
        h_aa = h_ab = h_bb = 0.0
        for x, y in zip(xs, ys):
            z = a * x + b
            p = _sigmoid(z)
            dz = p - y
            g_a += dz * x
            g_b += dz
            w = p * (1 - p) + 1e-9
            h_aa += w * x * x + ridge
            h_ab += w * x
            h_bb += w + ridge
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (g_a * h_bb - g_b * h_ab) / det
        db = (g_b * h_aa - g_a * h_ab) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-8:
            break
    return float(min(max(a, -20), 20)), float(min(max(b, -20), 20))


def calibrate_source(source: str, probs: Sequence[float],
                     outcomes: Sequence[int]) -> CalibrationReport:
    rep = CalibrationReport(
        source=source, n=len(probs),
        brier=brier_score(probs, outcomes),
        log_loss=log_loss(probs, outcomes),
        reliability=reliability_curve(probs, outcomes))
    rep.platt_a, rep.platt_b = fit_platt(probs, outcomes)
    rep.weight = 1.0 / (rep.brier + 0.05)
    return rep


def calibration_weights(reports: Sequence[CalibrationReport],
                        floor: float = 0.2) -> Dict[str, float]:
    """Precision weights (1 / (brier + eps)), floored and normalized to mean 1."""
    if not reports:
        return {}
    raw = {r.source: max(floor, r.weight) for r in reports}
    m = sum(raw.values()) / len(raw)
    return {k: round(v / m, 3) for k, v in raw.items()}




def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)
