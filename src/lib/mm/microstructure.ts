/**
 * Microstructure estimators — direct TS port of
 * quant_alpha/market_making/microstructure.py.
 *
 * Every quoting decision (fair value, spread width, expected fill rate,
 * reward share) comes from measured book properties, not guesses.
 */

import type { BookLevel, FillIntensity, OutcomeBook } from './types';

export function logit(p: number): number {
  const q = Math.min(Math.max(p, 1e-6), 1 - 1e-6);
  return Math.log(q / (1 - q));
}

export function sigmoid(x: number): number {
  if (x >= 0) return 1 / (1 + Math.exp(-x));
  const e = Math.exp(x);
  return e / (1 + e);
}

/** Stoikov micro-price: weight each side by the OPPOSITE resting size. */
export function microPrice(bids: BookLevel[], asks: BookLevel[]): number | null {
  if (!bids.length || !asks.length) return null;
  const b = bids[0].price;
  const qb = bids[0].size;
  const a = asks[0].price;
  const qa = asks[0].size;
  if (qb + qa <= 0) return (a + b) / 2;
  return (a * qb + b * qa) / (qb + qa);
}

/** Signed book imbalance in [-1, 1] (positive = bid-heavy). */
export function imbalance(bids: BookLevel[], asks: BookLevel[]): number {
  if (!bids.length || !asks.length) return 0;
  const qb = bids.slice(0, 3).reduce((s, l) => s + l.size, 0);
  const qa = asks.slice(0, 3).reduce((s, l) => s + l.size, 0);
  if (qb + qa <= 0) return 0;
  return (qb - qa) / (qb + qa);
}

/** EWMA volatility of the logit mid, per sqrt-hour. */
export class VolEstimator {
  private ewmaVar: number;
  private lastX: number | null = null;
  private lastT: number | null = null;

  constructor(
    public halfLifeH = 12,
    public floor = 0.02,
    public cap = 1.5,
    initSigma = 0.08
  ) {
    this.ewmaVar = initSigma * initSigma;
  }

  update(mid: number, tH: number): number {
    const x = logit(mid);
    if (this.lastX === null || this.lastT === null) {
      this.lastX = x;
      this.lastT = tH;
      return this.sigma;
    }
    const dt = Math.max(1e-6, tH - this.lastT);
    const dx = (x - this.lastX) / Math.sqrt(dt);
    const alpha = 1 - Math.exp(-Math.LN2 * dt / this.halfLifeH);
    this.ewmaVar = (1 - alpha) * this.ewmaVar + alpha * dx * dx;
    this.lastX = x;
    this.lastT = tH;
    return this.sigma;
  }

  get sigma(): number {
    return Math.min(Math.max(Math.sqrt(Math.max(this.ewmaVar, 1e-8)), this.floor), this.cap);
  }
}

/** One-shot sigma from the quoted spread when no history exists yet. */
export function spreadVolProxy(book: OutcomeBook): number {
  const best = book.bids[0]?.price;
  const ask = book.asks[0]?.price;
  if (best === undefined || ask === undefined) return 0.08;
  const mid = (best + ask) / 2;
  const sp = ask - best;
  return Math.min(Math.max((sp / Math.max(mid * (1 - mid), 1e-6)) * 0.35, 0.02), 1.5);
}

export function intensityRate(int: FillIntensity, delta: number): number {
  if (delta <= 0) return int.A;
  return int.A * Math.exp(-int.k * delta);
}

/**
 * MLE-style calibration of lambda(delta) = A*exp(-k*delta) from resting
 * depth: under market equilibrium resting SIZE grows with distance, so k
 * is the slope of log(level size) vs level index; A from traded volume rate.
 */
export function calibrateIntensity(
  book: OutcomeBook,
  fair: number | null,
  volume24h: number,
  prior: FillIntensity,
  hoursLookback = 24
): FillIntensity {
  const f = fair ?? midOf(book) ?? 0.5;
  const points: Array<[number, number]> = [];
  for (const l of book.bids) if (f - l.price > 0) points.push([f - l.price, l.size]);
  for (const l of book.asks) if (l.price - f > 0) points.push([l.price - f, l.size]);
  if (points.length < 3) return { A: prior.A, k: prior.k };

  const tick = Math.max(book.tick || 0.01, 1e-4);
  const byLevel = new Map<number, number>();
  for (const [d, sz] of points) {
    const lvl = Math.round(d / tick);
    byLevel.set(lvl, (byLevel.get(lvl) ?? 0) + sz);
  }
  const levels = [...byLevel.keys()].sort((a, b) => a - b);
  if (levels.length < 2 || levels[levels.length - 1] === levels[0]) {
    return { A: prior.A, k: prior.k };
  }
  const ys = levels.map((l) => Math.log(Math.max(byLevel.get(l)!, 1)));
  const n = levels.length;
  const mx = levels.reduce((s, l) => s + l, 0) / n;
  const my = ys.reduce((s, y) => s + y, 0) / n;
  let sxx = 0;
  let sxy = 0;
  for (let i = 0; i < n; i++) {
    sxx += (levels[i] - mx) ** 2;
    sxy += (levels[i] - mx) * (ys[i] - my);
  }
  if (sxx <= 1e-12) return { A: prior.A, k: prior.k };
  const slopePerLevel = sxy / sxx;
  const kEst = Math.min(Math.max(slopePerLevel / tick, 20), 800);

  const hours = Math.max(hoursLookback, 24);
  const volRate = volume24h ? volume24h / hours : 0;
  const AEst = Math.min(Math.max(0.5 * Math.min(volRate, 4000) / 200 + 0.5 * prior.A, 0.05), 8);
  return { A: AEst, k: kEst };
}

/** Polymarket-style liquidity-reward score of the CURRENT book. */
export function bookScore(
  bids: BookLevel[],
  asks: BookLevel[],
  mid: number,
  maxSpread: number,
  sizeCap: number,
  power = 2
): [number, number] {
  const score = (levels: BookLevel[], dist: (p: number) => number) => {
    let s = 0;
    for (const l of levels) {
      const d = dist(l.price);
      if (d >= 0 && d <= maxSpread) s += Math.min(l.size, sizeCap) * (1 - d / maxSpread) ** power;
    }
    return s;
  };
  return [
    score(bids, (p) => Math.max(0, mid - p)),
    score(asks, (p) => Math.max(0, p - mid)),
  ];
}

export function midOf(book: OutcomeBook): number | null {
  const b = book.bids[0]?.price;
  const a = book.asks[0]?.price;
  if (b !== undefined && a !== undefined) return (b + a) / 2;
  return book.probability;
}

// ----------------------------------------------------------- RNG utilities
/** Deterministic 32-bit RNG (mulberry32) — reproducible sessions. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Standard-normal draw via Box-Muller. */
export function gauss(rng: () => number): number {
  const u = Math.max(rng(), 1e-12);
  const v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/** Knuth Poisson sampler (lambda < ~15). */
export function poisson(rng: () => number, lam: number): number {
  if (lam <= 0) return 0;
  if (lam > 15) return Math.max(0, Math.round(gauss(rng) * Math.sqrt(lam) + lam));
  const L = Math.exp(-lam);
  let k = 0;
  let p = 1;
  for (;;) {
    k += 1;
    p *= rng();
    if (p <= L) return k - 1;
    if (k > 60) return k - 1;
  }
}

/** CRC32 — stable hash for seeding (PYTHONHASHSEED-proof, per Python port). */
export function crc32(s: string): number {
  let crc = 0xffffffff;
  for (let i = 0; i < s.length; i++) {
    crc ^= s.charCodeAt(i);
    for (let j = 0; j < 8; j++) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}
