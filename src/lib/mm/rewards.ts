/**
 * Fee-earning economics — direct TS port of
 * quant_alpha/market_making/rewards.py + arbitrage/fees.py.
 *
 * Income sources per venue:
 *   * Polymarket — liquidity rewards: daily USD pool split by score
 *       score(order) = min(size, cap) * (1 - spread/max_spread)^power
 *     reward_rate = pool/h * our_score / (book_score + our_score)
 *   * Kalshi — maker fee 0: pure spread capture
 *   * PredictIt — spread capture minus 10% profit fee at settlement
 *
 * expected_hourly_earnings = rewards + spread_capture - adverse - fees,
 * and optimal_reward_spread() maximizes it by golden-section search.
 */

import type { FillIntensity, QuoteContext, RewardParams } from './types';
import { intensityRate } from './microstructure';

const NO_REWARDS: RewardParams = {
  enabled: false,
  poolUsdPerHour: 0,
  maxSpread: 0.15,
  sizeCap: 0,
  spreadPower: 2,
  bookScore: 0,
};

/** Build reward params for a venue, measuring the live book score. */
export function rewardParamsFor(
  venue: string,
  bookScoreSide: number,
  mid: number | null,
  opts: { dailyPoolUsd: number; maxSpread: number; sizeCap: number; spreadPower: number }
): RewardParams {
  if (venue === 'polymarket') {
    return {
      enabled: true,
      poolUsdPerHour: opts.dailyPoolUsd / 24,
      maxSpread: opts.maxSpread,
      sizeCap: opts.sizeCap,
      spreadPower: opts.spreadPower,
      bookScore: bookScoreSide,
    };
  }
  return { ...NO_REWARDS };
}

function sigmaP(ctx: QuoteContext): number {
  const p = Math.min(Math.max(ctx.fair, 0.02), 0.98);
  return ctx.sigma * p * (1 - p);
}

/**
 * EFFECTIVE bid fill rate (fills/hour) of a quote at `price`, mirroring the
 * fill engine exactly: at/inside the competing touch the rate is the touch
 * intensity times the queue factor (we join/improve the front of the queue);
 * behind the touch the mid must sweep to our level within the hour —
 * Gaussian sweep with SD = sigma_p per sqrt-hour.
 */
export function effectiveRateBid(ctx: QuoteContext, price: number): number {
  const queuePen = 800 / (800 + ctx.touchBidSize);
  if (ctx.touchBid === null) return intensityRate(ctx.intensity, Math.max(0, ctx.fair - price));
  const d = ctx.touchBid - price;
  if (d <= ctx.tick) return ctx.intensity.A * queuePen;
  const sp = sigmaP(ctx);
  return 0.5 * ctx.intensity.A * Math.exp(-(d * d) / (2 * sp * sp)) * queuePen;
}

/** Effective ask fill rate — symmetric to the bid side. */
export function effectiveRateAsk(ctx: QuoteContext, price: number): number {
  const queuePen = 800 / (800 + ctx.touchAskSize);
  if (ctx.touchAsk === null) return intensityRate(ctx.intensity, Math.max(0, price - ctx.fair));
  const d = price - ctx.touchAsk;
  if (d <= ctx.tick) return ctx.intensity.A * queuePen;
  const sp = sigmaP(ctx);
  return 0.5 * ctx.intensity.A * Math.exp(-(d * d) / (2 * sp * sp)) * queuePen;
}

/** USD/hour of Polymarket-style liquidity rewards at our quote. */
export function rewardRate(ctx: QuoteContext, halfSpread: number): number {
  const rp = ctx.rewardParams;
  if (!rp.enabled || rp.poolUsdPerHour <= 0) return 0;
  if (halfSpread > rp.maxSpread) return 0;
  const ourScore = Math.min(ctx.quoteSize, rp.sizeCap) * (1 - halfSpread / rp.maxSpread) ** rp.spreadPower;
  const total = rp.bookScore + ourScore;
  return total > 0 ? rp.poolUsdPerHour * (ourScore / total) : 0;
}

/** USD/hour of expected spread income (both sides, touch-aware rates). */
export function spreadCaptureRate(ctx: QuoteContext, halfSpread: number): number {
  const q = ctx.quoteSize;
  const lamB = effectiveRateBid(ctx, ctx.fair - halfSpread);
  const lamA = effectiveRateAsk(ctx, ctx.fair + halfSpread);
  const edge = halfSpread - ctx.makerFeeRate;
  return (lamB + lamA) * Math.max(edge, 0) * q;
}

/** USD/hour lost to informed flow (jump factor 1.6, touch-aware rates). */
export function adverseSelectionRate(ctx: QuoteContext, halfSpread: number, adverseFrac = 0.35): number {
  const sp = sigmaP(ctx);
  const lamB = effectiveRateBid(ctx, ctx.fair - halfSpread);
  const lamA = effectiveRateAsk(ctx, ctx.fair + halfSpread);
  const informed = (lamB + lamA) * adverseFrac;
  const lossPerFill = sp * ctx.quoteSize * 1.6;
  return informed * lossPerFill;
}

export function expectedHourlyEarnings(ctx: QuoteContext, halfSpread: number, adverseFrac = 0.35): number {
  return (
    rewardRate(ctx, halfSpread) +
    spreadCaptureRate(ctx, halfSpread) -
    adverseSelectionRate(ctx, halfSpread, adverseFrac)
  );
}

/** Golden-section search of the earnings-maximizing half-spread. */
export function optimalRewardSpread(ctx: QuoteContext, adverseFrac = 0.35): number | null {
  const rp = ctx.rewardParams;
  const lo = ctx.tick;
  let hi = rp.maxSpread ?? 0.03;
  if (hi <= lo) return lo;
  if (!rp.enabled) {
    hi = Math.min(Math.max(6 * ctx.tick, 5 * sigmaP(ctx) * 0.9), 0.15);
    if (hi <= lo) return lo;
  }
  const gr = (Math.sqrt(5) - 1) / 2;
  const f = (h: number) => expectedHourlyEarnings(ctx, h, adverseFrac);
  let a = lo;
  let b = hi;
  let c = b - gr * (b - a);
  let d = a + gr * (b - a);
  for (let i = 0; i < 40; i++) {
    if (f(c) < f(d)) a = c;
    else b = d;
    c = b - gr * (b - a);
    d = a + gr * (b - a);
    if (b - a < ctx.tick) break;
  }
  const hStar = Math.round(((a + b) / 2) / ctx.tick) * ctx.tick;
  return Math.min(Math.max(hStar, lo), hi);
}

// ------------------------------------------------------ venue fee models
export interface VenueFees {
  venue: string;
  makerFeeRate: number; // fraction of price charged to makers
  /** PredictIt-style: fee on profit at settlement. */
  settlementProfitRate: number;
}

export function venueFees(venue: string): VenueFees {
  switch (venue) {
    case 'polymarket':
      return { venue, makerFeeRate: 0, settlementProfitRate: 0 };
    case 'kalshi':
      return { venue, makerFeeRate: 0, settlementProfitRate: 0 };
    case 'predictit':
      return { venue, makerFeeRate: 0, settlementProfitRate: 0.1 };
    default:
      return { venue, makerFeeRate: 0, settlementProfitRate: 0 };
  }
}
