"""Avellaneda-Stoikov (2008) optimal market making, adapted to bounded
digital contracts (prediction markets) via the logit transform.

Original (unbounded mid s, horizon T):
    reservation price  r = s - q * gamma * sigma^2 * (T - t)
    total spread       Delta = gamma*sigma^2*(T-t) + (2/gamma)*ln(1 + gamma/k)

Adaptation for prices in [0, 1]:
  1. Work in logit space x = logit(p): prices stay in (0, 1) under any shift,
     and volatility is multiplicative in probability space (empirically right
     for prediction markets).
  2. Reservation x_r = x - q_hat * gamma * sigma_x^2 * tau, with q_hat in
     [-1, 1] (inventory normalized by the risk cap) so the skew term is
     bounded and interpretable: at full inventory the reservation shifts by
     gamma*sigma_x^2*tau logits.
  3. Spread uses intensity decay k calibrated in logit distance from the
     live book (see microstructure.calibrate_intensity).
  4. Quotes: bid = sigmoid(x_r - Delta/2), ask = sigmoid(x_r + Delta/2),
     rounded to the venue tick and clipped to [tick, 1 - tick].
"""
from __future__ import annotations

import math

from .base import MMStrategy, QuoteContext, StrategyQuote
from ..microstructure import logit, sigmoid


class AvellanedaStoikov(MMStrategy):
    name = "as"

    def __init__(self, gamma: float = 0.8, max_skew_logits: float = 0.9,
                 tau_cap_h: float = 24.0):
        self.gamma = gamma
        self.max_skew = max_skew_logits
        self.tau_cap = tau_cap_h

    def reservation_x(self, ctx: QuoteContext) -> float:
        tau = min(ctx.tau_h, self.tau_cap)
        skew = min(self.gamma * ctx.sigma ** 2 * tau, self.max_skew)
        return logit(ctx.fair) - ctx.q_hat() * skew

    def total_spread_logits(self, ctx: QuoteContext) -> float:
        tau = min(ctx.tau_h, self.tau_cap)
        risk_term = min(self.gamma * ctx.sigma ** 2 * tau, self.max_skew)
        # intensity decay in logit distance: k_x = k_p * p(1-p)^-1
        p = min(max(ctx.fair, 0.02), 0.98)
        k_x = ctx.intensity.k / (p * (1 - p))
        flow_term = (2.0 / self.gamma) * math.log(1.0 + self.gamma / max(k_x, 1e-6))
        return risk_term + flow_term

    def quotes(self, ctx: QuoteContext) -> StrategyQuote:
        x_r = self.reservation_x(ctx)
        delta = self.total_spread_logits(ctx)
        bid = sigmoid(x_r - delta / 2)
        ask = sigmoid(x_r + delta / 2)
        # fee floor: never quote a side whose edge cannot cover maker fees
        if ctx.maker_fee_rate > 0:
            fee_edge = ctx.maker_fee_rate
            bid = min(bid, ctx.fair - fee_edge)
            ask = max(ask, ctx.fair + fee_edge)
        if bid >= ask:  # pathological (fee floor inverted) -> quote wide
            bid, ask = sigmoid(x_r - delta), sigmoid(x_r + delta)
        # never quote a sub-tick spread (degenerate at coarse ticks)
        if ctx.tick > 0 and (ask - bid) < 2 * ctx.tick:
            center = 0.5 * (bid + ask)
            bid, ask = center - ctx.tick, center + ctx.tick
        return StrategyQuote(bid, ask, ctx.quote_size, meta={
            "reservation_x": round(x_r, 4),
            "spread_logits": round(delta, 4),
            "gamma": self.gamma,
        }).clipped(ctx.tick)
