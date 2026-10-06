/**
 * Quoting strategies — direct TS ports of
 * quant_alpha/market_making/strategies/{avellaneda_stoikov,adaptive}.py,
 * plus the production "earnings optimizer" that drives the live console:
 * golden-section-tuned half-spread + inventory skew + fee floor.
 */

import type { QuoteContext, StrategyQuote } from './types';
import { logit, sigmoid } from './microstructure';
import { optimalRewardSpread } from './rewards';

function clip(v: number, tick: number): number {
  const clamped = Math.min(Math.max(v, tick), 1 - tick);
  return Math.round(clamped / tick) * tick;
}

function mkQuote(bid: number, ask: number, size: number, meta: Record<string, number | string>, tick: number): StrategyQuote {
  const b = clip(bid, tick);
  const a = clip(ask, tick);
  return { bid: b, ask: Math.max(a, b + tick), size, meta };
}

/** Avellaneda-Stoikov (2008) adapted to bounded contracts via logit space. */
export function avellanedaStoikov(ctx: QuoteContext): StrategyQuote {
  const tau = Math.min(ctx.tauH, 24);
  const gamma = 0.8;
  const maxSkew = 0.9;
  const qHat = ctx.inventory / Math.max(ctx.maxInventory, 1);

  const riskTerm = Math.min(gamma * ctx.sigma * ctx.sigma * tau, maxSkew);
  const xR = logit(ctx.fair) - qHat * riskTerm;

  const p = Math.min(Math.max(ctx.fair, 0.02), 0.98);
  const kX = ctx.intensity.k / (p * (1 - p));
  const flowTerm = (2 / gamma) * Math.log(1 + gamma / Math.max(kX, 1e-6));
  const delta = riskTerm + flowTerm;

  let bid = sigmoid(xR - delta / 2);
  let ask = sigmoid(xR + delta / 2);

  // fee floor: never quote a side whose edge cannot cover maker fees
  if (ctx.makerFeeRate > 0) {
    bid = Math.min(bid, ctx.fair - ctx.makerFeeRate);
    ask = Math.max(ask, ctx.fair + ctx.makerFeeRate);
  }
  if (bid >= ask) {
    bid = sigmoid(xR - delta);
    ask = sigmoid(xR + delta);
  }
  if (ctx.tick > 0 && ask - bid < 2 * ctx.tick) {
    const center = 0.5 * (bid + ask);
    bid = center - ctx.tick;
    ask = center + ctx.tick;
  }
  return mkQuote(bid, ask, ctx.quoteSize, {
    reservation_x: Math.round(xR * 10000) / 10000,
    spread_logits: Math.round(delta * 10000) / 10000,
    gamma,
  }, ctx.tick);
}

/** Adaptive spread: vol floor + earnings-optimal cap, inventory skew, hysteresis-free. */
export function adaptiveSpread(ctx: QuoteContext, adverseFrac: number): StrategyQuote {
  const volMultiple = 0.9;
  const skewCoef = 0.35;
  const consensusWeight = 0.4;

  const fair = ctx.microPrice !== null ? (1 - consensusWeight) * ctx.microPrice + consensusWeight * ctx.fair : ctx.fair;
  const p = Math.min(Math.max(ctx.fair, 0.02), 0.98);
  const sigmaP = ctx.sigma * p * (1 - p);

  let spread = Math.max(volMultiple * sigmaP, ctx.makerFeeRate, ctx.tick);
  if (ctx.rewardParams?.enabled) {
    const hStar = optimalRewardSpread(ctx, adverseFrac);
    if (hStar !== null) spread = Math.max(ctx.tick, Math.min(spread, hStar + 0.02));
  }

  const skew = skewCoef * (ctx.inventory / Math.max(ctx.maxInventory, 1)) * spread;
  const center = fair - skew;
  let bid = center - spread;
  let ask = center + spread;
  if (ctx.makerFeeRate > 0) {
    bid = Math.min(bid, fair - ctx.makerFeeRate);
    ask = Math.max(ask, fair + ctx.makerFeeRate);
  }
  return mkQuote(bid, ask, ctx.quoteSize, {
    half_spread: Math.round(spread * 10000) / 10000,
    skew: Math.round(skew * 10000) / 10000,
    sigma_p: Math.round(sigmaP * 100000) / 100000,
  }, ctx.tick);
}

/**
 * EARNINGS OPTIMIZER (production default): the half-spread is the exact
 * golden-section maximizer of expected hourly earnings (rewards + capture
 * - adverse - fees), skewed for inventory, floored at fees, widened under
 * vol. Re-optimized every tick from live book state — this is the strategy
 * that "harvests" the fee/reward pool most efficiently.
 */
export function earningsOptimizer(ctx: QuoteContext, adverseFrac: number): StrategyQuote {
  const hStar = optimalRewardSpread(ctx, adverseFrac);
  const p = Math.min(Math.max(ctx.fair, 0.02), 0.98);
  const sigmaP = ctx.sigma * p * (1 - p);
  const volFloor = 0.55 * sigmaP; // never quote inside noise
  const half = Math.max(hStar ?? ctx.tick, volFloor, ctx.makerFeeRate, ctx.tick);

  // inventory skew: shift BOTH quotes away from the side that adds exposure
  const qHat = ctx.inventory / Math.max(ctx.maxInventory, 1);
  const skew = 0.55 * qHat * half;
  const center = ctx.fair - skew;

  let bid = center - half;
  let ask = center + half;
  if (ctx.makerFeeRate > 0) {
    bid = Math.min(bid, ctx.fair - ctx.makerFeeRate);
    ask = Math.max(ask, ctx.fair + ctx.makerFeeRate);
  }
  return mkQuote(bid, ask, ctx.quoteSize, {
    half_spread: Math.round(half * 10000) / 10000,
    optimal_h: Math.round((hStar ?? 0) * 10000) / 10000,
    skew: Math.round(skew * 10000) / 10000,
    sigma_p: Math.round(sigmaP * 100000) / 100000,
  }, ctx.tick);
}

/** Naive static baseline (shadow strategy for uplift measurement). */
export function naiveBaseline(ctx: QuoteContext): StrategyQuote {
  const p = Math.min(Math.max(ctx.fair, 0.02), 0.98);
  const sigmaP = ctx.sigma * p * (1 - p);
  const half = Math.max(6 * ctx.tick, 2.4 * sigmaP);
  return mkQuote(ctx.fair - half, ctx.fair + half, ctx.quoteSize, {}, ctx.tick);
}

export function quoteWith(
  strategy: 'optimizer' | 'as' | 'adaptive',
  ctx: QuoteContext,
  adverseFrac: number
): StrategyQuote {
  switch (strategy) {
    case 'as':
      return avellanedaStoikov(ctx);
    case 'adaptive':
      return adaptiveSpread(ctx, adverseFrac);
    default:
      return earningsOptimizer(ctx, adverseFrac);
  }
}
