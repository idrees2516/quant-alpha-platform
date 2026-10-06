/**
 * LIVE market-making fee-earning engine (server-side singleton).
 *
 * Faithful TS port of quant_alpha/market_making/simulator.py running as a
 * continuous tick loop over real order books:
 *   * per-tick: sim-mid evolves (logit GBM + news jumps + anchor reversion),
 *     strategy quotes from its OWN information set (book + consensus),
 *     fills arrive Poisson with Gaussian sweep + adverse selection,
 *     Polymarket liquidity rewards accrue, inventory/cash/equity update;
 *   * the earnings optimizer re-solves the golden-section spread every tick;
 *   * a naive static-spread SHADOW strategy runs the same fill model so the
 *     console can show the optimizer's live uplift;
 *   * RiskGuard per market + global kill switch (same limits as backtester).
 */

import type {
  CurvePoint,
  EngineParams,
  EngineSnapshot,
  FillRecord,
  MarketAgentState,
  OutcomeBook,
  QuoteContext,
  RewardParams,
  StrategyQuote,
} from './types';
import {
  VolEstimator,
  bookScore,
  calibrateIntensity,
  crc32,
  gauss,
  logit,
  microPrice,
  mulberry32,
  poisson,
  sigmoid,
  spreadVolProxy,
} from './microstructure';
import { loadUniverse } from './markets';
import { optimalRewardSpread, rewardParamsFor, rewardRate, adverseSelectionRate, spreadCaptureRate, venueFees } from './rewards';
import { naiveBaseline, quoteWith } from './strategies';

const TICK_MS = 1000;
const CURVE_CAP = 720;
const FILLS_CAP = 250;
const REFRESH_EVERY_TICKS = 150; // ~2.5 min live book refresh
const POLY_POOL_OPTS = { dailyPoolUsd: 500, maxSpread: 0.03, sizeCap: 300, spreadPower: 2 };

interface BookShape {
  bidOffsets: number[]; // price - anchorMid
  askOffsets: number[];
  bidSizes: number[];
  askSizes: number[];
}

interface Agent {
  book: OutcomeBook;
  shape: BookShape;
  anchorMid: number;
  simMid: number;
  fair: number;
  /** generator vol (logit/sqrt-h), measured from the live book spread */
  sigmaGen: number;
  sigma: number;
  vol: VolEstimator;
  intensity: { A: number; k: number };
  rewardParams: RewardParams;
  quote: StrategyQuote | null;
  baselineQuote: StrategyQuote | null;
  optimalH: number | null;
  inventory: number;
  avgCost: number;
  cash: number;
  realizedPnl: number;
  rewards: number;
  spreadCapture: number;
  adverseCost: number;
  fees: number;
  fills: number;
  buys: number;
  sells: number;
  baseline: { rewards: number; capture: number; adverse: number; inv: number };
  peakEquity: number;
  drawdown: number;
  killed: boolean;
  killReason: string;
  enabled: boolean;
  rng: () => number;
  lastCalcTick: number;
}

export class MmEngine {
  private agents: Agent[] = [];
  private curve: CurvePoint[] = [];
  private fillLog: FillRecord[] = [];
  private fillSeq = 1;
  private timer: ReturnType<typeof setInterval> | null = null;
  private booting = true;
  private bootError: string | null = null;
  private destroyed = false;

  running = false;
  startedAt: number | null = null;
  simClockH = 0;
  ticks = 0;
  dataMode: 'live' | 'fixture' | 'mixed' = 'fixture';
  lastRefreshAt: number | null = null;
  lastRefreshError: string | null = null;
  globalKill = false;
  globalKillReason = '';
  params: EngineParams = {
    strategy: 'optimizer',
    gamma: 0.8,
    quoteSize: 25,
    maxInventory: 120,
    speed: 0.1, // sim-hours per real second → 6 sim minutes/s
    adverseFrac: 0.35,
    perMarketCash: 2000,
  };

  constructor() {
    void this.boot();
  }

  // ------------------------------------------------------------- boot
  private async boot(): Promise<void> {
    const uni = await loadUniverse(8, 5);
    if (this.destroyed) return;
    this.dataMode = uni.mode;
    this.lastRefreshError = uni.error;
    this.bootError = uni.error;
    this.lastRefreshAt = Date.now();
    this.buildAgents(uni.books);
    this.booting = false;
    this.start(); // auto-start: the engine earns from the first second
  }

  private buildAgents(books: OutcomeBook[]): void {
    this.agents = books.map((b) => {
      const mid =
        b.bids.length && b.asks.length ? (b.bids[0].price + b.asks[0].price) / 2 : (b.probability ?? 0.5);
      const seed = crc32(`${b.venue}:${b.marketId}:${b.outcomeKey}`);
      const book = this.reshaped(b, mid);
      const rp = this.buildRewardParams(book, mid);
      // generator vol measured from the quoted spread (Python: spread_vol_proxy,
      // fixture calibration 0.035 logit/sqrt-h) — NOT fed back from the estimator
      const sigmaGen = Math.min(Math.max(spreadVolProxy(book), 0.03), 0.15);
      return {
        book,
        shape: this.shapeOf(b, mid),
        anchorMid: mid,
        simMid: mid,
        fair: mid,
        sigmaGen,
        sigma: sigmaGen,
        vol: new VolEstimator(12, 0.02, 1.5, sigmaGen),
        intensity: { A: 1.2, k: 60 },
        rewardParams: rp,
        quote: null,
        baselineQuote: null,
        optimalH: null,
        inventory: 0,
        avgCost: 0,
        cash: this.params.perMarketCash,
        realizedPnl: 0,
        rewards: 0,
        spreadCapture: 0,
        adverseCost: 0,
        fees: 0,
        fills: 0,
        buys: 0,
        sells: 0,
        baseline: { rewards: 0, capture: 0, adverse: 0, inv: 0 },
        peakEquity: this.params.perMarketCash,
        drawdown: 0,
        killed: false,
        killReason: '',
        enabled: true,
        rng: mulberry32(seed),
        lastCalcTick: -99,
      } satisfies Agent;
    });
  }

  private shapeOf(b: OutcomeBook, mid: number): BookShape {
    return {
      bidOffsets: b.bids.map((l) => l.price - mid),
      askOffsets: b.asks.map((l) => l.price - mid),
      bidSizes: b.bids.map((l) => l.size),
      askSizes: b.asks.map((l) => l.size),
    };
  }

  /** Rebuild the visible book around a (possibly moved) mid. */
  private reshaped(b: OutcomeBook, mid: number): OutcomeBook {
    const tick = Math.max(b.tick, 0.001);
    const clampP = (v: number) => Math.min(Math.max(Math.round(v / tick) * tick, tick), 1 - tick);
    const shape = this.shapeOf(b, mid);
    return {
      ...b,
      bids: shape.bidOffsets.map((off, i) => ({ price: clampP(mid + off), size: shape.bidSizes[i] })).sort((x, y) => y.price - x.price),
      asks: shape.askOffsets.map((off, i) => ({ price: clampP(mid + off), size: shape.askSizes[i] })).sort((x, y) => x.price - y.price),
    };
  }

  private buildRewardParams(book: OutcomeBook, mid: number): RewardParams {
    if (book.venue !== 'polymarket') return rewardParamsFor(book.venue, 0, mid, POLY_POOL_OPTS);
    const [bs, as_] = bookScore(book.bids, book.asks, mid, POLY_POOL_OPTS.maxSpread, POLY_POOL_OPTS.sizeCap, POLY_POOL_OPTS.spreadPower);
    return rewardParamsFor(book.venue, Math.max(bs, as_), mid, POLY_POOL_OPTS);
  }

  // ------------------------------------------------------- lifecycle
  start(): void {
    if (this.booting) return;
    if (this.timer) return;
    this.running = true;
    if (!this.startedAt) this.startedAt = Date.now();
    this.timer = setInterval(() => this.tick(), TICK_MS);
    if (this.timer && typeof this.timer === 'object' && 'unref' in this.timer) {
      (this.timer as unknown as { unref: () => void }).unref();
    }
  }

  pause(): void {
    this.running = false;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Stop everything permanently (used when a newer engine build replaces this instance). */
  destroy(): void {
    this.destroyed = true;
    this.pause();
  }

  async reset(): Promise<void> {
    this.pause();
    this.curve = [];
    this.fillLog = [];
    this.simClockH = 0;
    this.ticks = 0;
    this.startedAt = null;
    this.globalKill = false;
    this.globalKillReason = '';
    this.booting = true;
    const uni = await loadUniverse(8, 5);
    this.dataMode = uni.mode;
    this.lastRefreshError = uni.error;
    this.lastRefreshAt = Date.now();
    this.buildAgents(uni.books);
    this.booting = false;
    this.start();
  }

  setParams(patch: Partial<EngineParams>): void {
    const p = this.params;
    if (patch.strategy && ['optimizer', 'as', 'adaptive'].includes(patch.strategy)) p.strategy = patch.strategy;
    if (typeof patch.gamma === 'number') p.gamma = Math.min(Math.max(patch.gamma, 0.1), 3);
    if (typeof patch.quoteSize === 'number') p.quoteSize = Math.round(Math.min(Math.max(patch.quoteSize, 5), 200));
    if (typeof patch.maxInventory === 'number') p.maxInventory = Math.round(Math.min(Math.max(patch.maxInventory, 30), 500));
    if (typeof patch.speed === 'number') p.speed = Math.min(Math.max(patch.speed, 0.02), 0.5);
    if (typeof patch.adverseFrac === 'number') p.adverseFrac = Math.min(Math.max(patch.adverseFrac, 0.05), 0.7);
    if (typeof patch.perMarketCash === 'number') p.perMarketCash = Math.min(Math.max(patch.perMarketCash, 500), 20000);
  }

  toggleMarket(marketId: string, enabled: boolean): void {
    for (const a of this.agents) if (a.book.marketId === marketId) a.enabled = enabled;
  }

  clearKill(): void {
    this.globalKill = false;
    this.globalKillReason = '';
    for (const a of this.agents) {
      a.killed = false;
      a.killReason = '';
      a.drawdown = 0;
    }
  }

  // ------------------------------------------------------------- tick
  private tick(): void {
    if (this.destroyed) return;
    const dtH = this.params.speed;
    this.simClockH += dtH;
    this.ticks += 1;

    // periodic live-book refresh (books stay anchored to reality)
    if (this.ticks % REFRESH_EVERY_TICKS === 0) {
      void this.refreshBooks();
    }

    let totRewards = 0;
    let totCapture = 0;
    let totAdverse = 0;
    let totFees = 0;
    let totBaseline = 0;
    let equity = 0;
    let allocated = 0;
    let invAbs = 0;
    let making = 0;

    for (const a of this.agents) {
      this.stepAgent(a, dtH);
      totRewards += a.rewards;
      totCapture += a.spreadCapture;
      totAdverse += a.adverseCost;
      totFees += a.fees;
      totBaseline += a.baseline.rewards + a.baseline.capture - a.baseline.adverse;
      equity += a.cash + a.inventory * a.simMid;
      allocated += this.params.perMarketCash;
      invAbs += Math.abs(a.inventory);
      if (a.enabled && !a.killed) making += 1;
    }

    // global kill switch: bankroll drawdown
    if (!this.globalKill) {
      const dd = allocated - equity;
      if (dd > 0.2 * allocated) {
        this.globalKill = true;
        this.globalKillReason = `bankroll drawdown $${dd.toFixed(0)} >= 20% of $${allocated.toFixed(0)}`;
        for (const a of this.agents) {
          a.killed = true;
          a.killReason = a.killReason || 'global kill switch';
        }
      }
    }

    const net = totRewards + totCapture - totAdverse - totFees;
    this.curve.push({
      t: Math.round((Date.now() - (this.startedAt ?? Date.now())) / 1000),
      simT: Math.round(this.simClockH * 100) / 100,
      rewards: r2(totRewards),
      spreadCapture: r2(totCapture),
      adverseCost: r2(totAdverse),
      net: r2(net),
      baselineNet: r2(totBaseline),
      equity: r2(equity),
    });
    if (this.curve.length > CURVE_CAP) this.curve.splice(0, this.curve.length - CURVE_CAP);
  }

  private async refreshBooks(): Promise<void> {
    try {
      const uni = await loadUniverse(8, 5);
      if (this.destroyed || !uni.books.length) return;
      this.dataMode = uni.mode;
      this.lastRefreshError = uni.error;
      this.lastRefreshAt = Date.now();
      // merge refreshed books into agents by id; new markets get new agents
      const byId = new Map(this.agents.map((a) => [`${a.book.venue}:${a.book.marketId}`, a]));
      for (const nb of uni.books) {
        const key = `${nb.venue}:${nb.marketId}`;
        const existing = byId.get(key);
        if (existing) {
          const mid = nb.bids.length && nb.asks.length ? (nb.bids[0].price + nb.asks[0].price) / 2 : existing.simMid;
          existing.book = this.reshapedKeepSim(nb, existing.simMid > 0 ? existing.simMid : mid);
          existing.shape = this.shapeOf(nb, mid);
          existing.anchorMid = mid;
          existing.book.volume24h = nb.volume24h;
          existing.rewardParams = this.buildRewardParams(existing.book, mid);
          existing.sigmaGen = Math.min(Math.max(spreadVolProxy(nb), 0.03), 0.15);
        }
      }
    } catch {
      /* keep trading on current anchors */
    }
  }

  /** Refresh book shape but keep the agent's simulated mid trajectory. */
  private reshapedKeepSim(nb: OutcomeBook, simMid: number): OutcomeBook {
    const tick = Math.max(nb.tick, 0.001);
    const clampP = (v: number) => Math.min(Math.max(Math.round(v / tick) * tick, tick), 1 - tick);
    const baseMid = nb.bids.length && nb.asks.length ? (nb.bids[0].price + nb.asks[0].price) / 2 : simMid;
    const shift = simMid - baseMid;
    return {
      ...nb,
      bids: nb.bids.map((l) => ({ price: clampP(l.price + shift), size: l.size })).sort((x, y) => y.price - x.price),
      asks: nb.asks.map((l) => ({ price: clampP(l.price + shift), size: l.size })).sort((x, y) => x.price - y.price),
    };
  }

  // ------------------------------------------------------- agent step
  private stepAgent(a: Agent, dtH: number): void {
    // 1) sim-mid evolution: logit GBM at the BOOK-MEASURED generator vol
    //    (decoupled from the estimator — no self-fulfilling vol loop),
    //    plus rare news jumps and anchor reversion
    const x = logit(a.simMid);
    const anchorX = logit(Math.min(Math.max(a.anchorMid, 0.05), 0.95));
    const revert = 0.05 * (anchorX - x) * dtH;
    let dx = a.sigmaGen * Math.sqrt(dtH) * gauss(a.rng) + revert;
    if (a.rng() < 0.015) dx += 3.2 * a.sigmaGen * Math.sqrt(dtH) * (a.rng() < 0.5 ? -1 : 1); // news jump
    a.simMid = Math.min(Math.max(sigmoid(x + dx), 0.02), 0.98);

    // 2) refresh visible book around the moved mid (shape from live book)
    if (this.ticks % 5 === 1 || !a.quote) {
      a.book = this.reshaped(a.book, a.simMid);
    } else {
      // light shift: move only the touch to keep micro-price honest
      const tick = Math.max(a.book.tick, 0.001);
      const clampP = (v: number) => Math.min(Math.max(Math.round(v / tick) * tick, tick), 1 - tick);
      const shift = a.simMid - ((a.book.bids[0]?.price + a.book.asks[0]?.price) / 2 || a.simMid);
      if (Math.abs(shift) > tick) {
        a.book = {
          ...a.book,
          bids: a.book.bids.map((l) => ({ ...l, price: clampP(l.price + shift) })).sort((p, q) => q.price - p.price),
          asks: a.book.asks.map((l) => ({ ...l, price: clampP(l.price + shift) })).sort((p, q) => p.price - q.price),
        };
      }
    }

    // 3) estimators: vol + intensity + fair (information set = book + consensus)
    a.sigma = Math.min(Math.max(a.vol.update(a.simMid, this.simClockH), a.sigmaGen * 0.5), a.sigmaGen * 2.5);
    const mp = microPrice(a.book.bids, a.book.asks);
    const bookMid = (a.book.bids[0]?.price + a.book.asks[0]?.price) / 2 || a.simMid;
    const consensus = a.book.consensus;
    const fairRaw = 0.6 * (mp ?? bookMid) + 0.4 * bookMid;
    a.fair = consensus !== null ? 0.75 * fairRaw + 0.25 * consensus : fairRaw;
    if (this.ticks - a.lastCalcTick >= 5) {
      a.intensity = calibrateIntensity(a.book, a.fair, a.book.volume24h, a.intensity);
      a.lastCalcTick = this.ticks;
    }

    const tauH = Math.max(1, 48 - this.simClockH); // long-dated default
    const ctx: QuoteContext = {
      tick: Math.max(a.book.tick, 0.001),
      fair: a.fair,
      bookMid,
      microPrice: mp,
      sigma: a.sigma,
      tauH,
      inventory: a.inventory,
      maxInventory: this.params.maxInventory,
      quoteSize: this.params.quoteSize,
      intensity: a.intensity,
      makerFeeRate: venueFees(a.book.venue).makerFeeRate,
      rewardParams: a.rewardParams,
      touchBid: a.book.bids[0]?.price ?? null,
      touchAsk: a.book.asks[0]?.price ?? null,
      touchBidSize: a.book.bids[0]?.size ?? 0,
      touchAskSize: a.book.asks[0]?.size ?? 0,
    };

    // 4) quote (strategy) + naive shadow quote (same fill model)
    const active = a.enabled && !a.killed && !this.globalKill;
    const q = active ? quoteWith(this.params.strategy, ctx, this.params.adverseFrac) : { bid: null, ask: null, size: 0, meta: {} };
    a.quote = q;
    a.baselineQuote = naiveBaseline(ctx);
    a.optimalH = optimalRewardSpread(ctx, this.params.adverseFrac);

    // 5) fills: Gaussian sweep vs the competing touch, adverse selection
    if (active) this.processFills(a, ctx, q, dtH, false);

    // 6) shadow baseline accounting (always runs — it is the benchmark)
    const bq = a.baselineQuote;
    const shadowCtx: QuoteContext = { ...ctx, inventory: a.baseline.inv, maxInventory: this.params.maxInventory };
    this.processFills(a, shadowCtx, bq, dtH, true);

    // 7) rewards accrual (Polymarket program) — engine + shadow
    if (a.rewardParams.enabled) {
      const h = q.bid !== null && q.ask !== null ? (q.ask - q.bid) / 2 : null;
      if (h !== null) a.rewards += rewardRate(ctx, h) * dtH;
      const bh = bq.bid !== null && bq.ask !== null ? (bq.ask - bq.bid) / 2 : null;
      if (bh !== null) a.baseline.rewards += rewardRate(shadowCtx, bh) * dtH;
    }

    // 8) risk guard
    const equity = a.cash + a.inventory * a.simMid;
    a.peakEquity = Math.max(a.peakEquity, equity);
    a.drawdown = a.peakEquity - equity;
    if (!a.killed && a.drawdown > 0.3 * this.params.perMarketCash) {
      a.killed = true;
      a.killReason = `drawdown $${a.drawdown.toFixed(0)} >= 30% of allocated cash`;
    }
    if (Math.abs(a.inventory) > this.params.maxInventory * 1.5) {
      a.killed = true;
      a.killReason = a.killReason || 'inventory breach';
    }
  }

  /** Gaussian-sweep fill engine — mirror of simulator.py. shadow=true → baseline books. */
  private processFills(a: Agent, ctx: QuoteContext, q: StrategyQuote, dtH: number, shadow: boolean): void {
    const tick = Math.max(a.book.tick, 0.001);
    const qs = this.params.quoteSize;
    const inv = shadow ? a.baseline.inv : a.inventory;
    const bookBestBid = a.book.bids[0]?.price ?? null;
    const bookBestAsk = a.book.asks[0]?.price ?? null;
    const pFair = Math.min(Math.max(a.fair, 0.02), 0.98);
    const sigmaSweep = Math.max(a.sigma * pFair * (1 - pFair) * Math.sqrt(dtH), 1.5 * tick);
    const A = a.intensity.A;

    const sweepRate = (dBehind: number, queuePen: number): number => {
      if (dBehind <= 0) return A * queuePen;
      return 0.5 * A * Math.exp(-(dBehind * dBehind) / (2 * sigmaSweep * sigmaSweep)) * queuePen;
    };
    const joinFactor = (size: number) => 800 / (800 + size);

    // ---- bid side (we buy)
    if (q.bid !== null && inv < this.params.maxInventory) {
      const d = bookBestBid !== null ? Math.max(0, bookBestBid - q.bid) : Math.max(0, a.fair - q.bid);
      const lam = sweepRate(d, joinFactor(a.book.bids[0]?.size ?? 0));
      let n = poisson(a.rng, lam * dtH);
      const headroom = Math.max(0, Math.floor((this.params.maxInventory - inv) / qs));
      n = Math.min(n, headroom);
      for (let i = 0; i < n; i++) {
        const informed = a.rng() < this.params.adverseFrac;
        const px = q.bid;
        let edge = a.fair - px;
        if (informed) {
          const realized = edge - 1.6 * a.sigma * a.fair * (1 - a.fair);
          const adv = Math.max(0, edge - realized) * qs;
          if (shadow) a.baseline.adverse += adv;
          else a.adverseCost += adv;
          edge = realized;
        }
        const fee = venueFees(a.book.venue).makerFeeRate * px * qs;
        this.applyBid(a, px, qs, fee, edge, informed, shadow);
      }
    }

    // ---- ask side (we sell from holdings — no naked shorts)
    if (q.ask !== null && inv > 0) {
      const d = bookBestAsk !== null ? Math.max(0, q.ask - bookBestAsk) : Math.max(0, q.ask - a.fair);
      const lam = sweepRate(d, joinFactor(a.book.asks[0]?.size ?? 0));
      let n = poisson(a.rng, lam * dtH);
      const headroom = Math.max(0, Math.floor(inv / qs));
      n = Math.min(n, headroom);
      for (let i = 0; i < n; i++) {
        const informed = a.rng() < this.params.adverseFrac;
        const px = q.ask;
        let edge = px - a.fair;
        if (informed) {
          const realized = edge - 1.6 * a.sigma * a.fair * (1 - a.fair);
          const adv = Math.max(0, edge - realized) * qs;
          if (shadow) a.baseline.adverse += adv;
          else a.adverseCost += adv;
          edge = realized;
        }
        const fee = venueFees(a.book.venue).makerFeeRate * px * qs;
        this.applyAsk(a, px, qs, fee, edge, informed, shadow);
      }
    }
  }

  private applyBid(a: Agent, px: number, qty: number, fee: number, edge: number, informed: boolean, shadow: boolean): void {
    if (shadow) {
      a.baseline.inv += qty;
      a.baseline.capture += Math.max(edge, -0.5) * qty;
      return;
    }
    const totalCost = a.avgCost * a.inventory + px * qty;
    a.inventory += qty;
    a.avgCost = a.inventory > 0 ? totalCost / a.inventory : 0;
    a.cash -= px * qty + fee;
    a.fees += fee;
    a.spreadCapture += Math.max(edge, -0.5) * qty;
    a.fills += 1;
    a.buys += 1;
    this.pushFill(a, 'bid', px, qty, edge, informed);
  }

  private applyAsk(a: Agent, px: number, qty: number, fee: number, edge: number, informed: boolean, shadow: boolean): void {
    if (shadow) {
      a.baseline.inv = Math.max(0, a.baseline.inv - qty);
      a.baseline.capture += Math.max(edge, -0.5) * qty;
      return;
    }
    const profitFeeRate = venueFees(a.book.venue).settlementProfitRate;
    const profit = Math.max(0, px - a.avgCost) * qty;
    const profitFee = profitFeeRate * profit; // PredictIt-style fee on winnings
    const edgeSell = (px - a.avgCost) * qty;
    a.inventory -= qty;
    if (a.inventory < 0) a.inventory = 0;
    if (a.inventory === 0) a.avgCost = 0;
    a.realizedPnl += edgeSell;
    a.cash += px * qty - fee - profitFee;
    a.fees += fee + profitFee;
    a.spreadCapture += Math.max(edge, -0.5) * qty;
    a.fills += 1;
    a.sells += 1;
    this.pushFill(a, 'ask', px, qty, edge, informed);
  }

  private pushFill(a: Agent, side: 'bid' | 'ask', px: number, qty: number, edge: number, adverse: boolean): void {
    this.fillLog.unshift({
      id: this.fillSeq++,
      ts: Date.now(),
      simT: Math.round(this.simClockH * 100) / 100,
      venue: a.book.venue,
      marketId: a.book.marketId,
      title: a.book.title,
      side,
      price: px,
      qty,
      edge: Math.round(edge * 10000) / 10000,
      adverse,
    });
    if (this.fillLog.length > FILLS_CAP) this.fillLog.length = FILLS_CAP;
  }

  // ---------------------------------------------------------- snapshot
  snapshot(): EngineSnapshot {
    let rewards = 0;
    let capture = 0;
    let adverse = 0;
    let fees = 0;
    let baselineNet = 0;
    let equity = 0;
    let allocated = 0;
    let fills = 0;
    let buys = 0;
    let sells = 0;
    let invAbs = 0;
    let making = 0;
    const agents: MarketAgentState[] = [];
    const venueAgg = new Map<string, { markets: number; rewards: number; spreadCapture: number; adverseCost: number; fees: number; fills: number }>();

    for (const a of this.agents) {
      const ctxFair = a.fair;
      const h = a.quote?.bid !== null && a.quote?.ask !== null && a.quote ? (a.quote.ask - a.quote.bid) / 2 : null;
      const expR = h !== null ? rewardRate(this.ctxOf(a), h) : 0;
      const expC = h !== null ? spreadCaptureRate(this.ctxOf(a), h) : 0;
      const expA = h !== null ? adverseSelectionRate(this.ctxOf(a), h, this.params.adverseFrac) : 0;
      const aEq = a.cash + a.inventory * a.simMid;
      rewards += a.rewards;
      capture += a.spreadCapture;
      adverse += a.adverseCost;
      fees += a.fees;
      baselineNet += a.baseline.rewards + a.baseline.capture - a.baseline.adverse;
      equity += aEq;
      allocated += this.params.perMarketCash;
      fills += a.fills;
      buys += a.buys;
      sells += a.sells;
      invAbs += Math.abs(a.inventory);
      if (a.enabled && !a.killed) making += 1;

      agents.push({
        venue: a.book.venue,
        marketId: a.book.marketId,
        outcomeKey: a.book.outcomeKey,
        title: a.book.title,
        url: a.book.url,
        dataMode: a.book.dataMode,
        fair: r4(ctxFair),
        mid: r4(a.simMid),
        sigma: r4(a.sigma),
        bid: a.quote?.bid !== null && a.quote?.bid !== undefined ? r4(a.quote.bid) : null,
        ask: a.quote?.ask !== null && a.quote?.ask !== undefined ? r4(a.quote.ask) : null,
        halfSpread: h !== null ? r4(h) : null,
        optimalHalfSpread: a.optimalH !== null ? r4(a.optimalH) : null,
        inventory: a.inventory,
        avgCost: r4(a.avgCost),
        cash: r2(a.cash),
        equity: r2(aEq),
        fills: a.fills,
        buys: a.buys,
        sells: a.sells,
        rewards: r2(a.rewards),
        spreadCapture: r2(a.spreadCapture),
        adverseCost: r2(a.adverseCost),
        fees: r2(a.fees),
        expHourlyEarnings: r2(expR + expC - expA),
        expRewardsPerH: r2(expR),
        expCapturePerH: r2(expC),
        expAdversePerH: r2(expA),
        baselineNet: r2(a.baseline.rewards + a.baseline.capture - a.baseline.adverse),
        intensityA: r2(a.intensity.A),
        intensityK: Math.round(a.intensity.k),
        killed: a.killed,
        killReason: a.killReason,
        enabled: a.enabled,
        bookTop: {
          bid: a.book.bids[0]?.price !== undefined ? r4(a.book.bids[0].price) : null,
          ask: a.book.asks[0]?.price !== undefined ? r4(a.book.asks[0].price) : null,
          bidSize: Math.round(a.book.bids[0]?.size ?? 0),
          askSize: Math.round(a.book.asks[0]?.size ?? 0),
        },
      });

      const v = venueAgg.get(a.book.venue) ?? { markets: 0, rewards: 0, spreadCapture: 0, adverseCost: 0, fees: 0, fills: 0 };
      v.markets += 1;
      v.rewards += a.rewards;
      v.spreadCapture += a.spreadCapture;
      v.adverseCost += a.adverseCost;
      v.fees += a.fees;
      v.fills += a.fills;
      venueAgg.set(a.book.venue, v);
    }

    const net = rewards + capture - adverse - fees;
    const gross = rewards + capture;
    let maxDd = 0;
    let peak = -Infinity;
    for (const pt of this.curve) {
      peak = Math.max(peak, pt.equity);
      maxDd = Math.max(maxDd, peak - pt.equity);
    }

    return {
      status: {
        running: this.running,
        startedAt: this.startedAt,
        simClockH: Math.round(this.simClockH * 10) / 10,
        ticks: this.ticks,
        dataMode: this.booting ? 'fixture' : this.dataMode,
        lastRefreshAt: this.lastRefreshAt,
        lastRefreshError: this.lastRefreshError,
        globalKill: this.globalKill,
        globalKillReason: this.globalKillReason,
        params: { ...this.params },
      },
      totals: {
        rewards: r2(rewards),
        spreadCapture: r2(capture),
        adverseCost: r2(adverse),
        fees: r2(fees),
        netEarned: r2(net),
        grossFeesEarned: r2(gross),
        baselineNet: r2(baselineNet),
        upliftUsd: r2(net - baselineNet),
        upliftPct: baselineNet > 1 ? r2(((net - baselineNet) / baselineNet) * 100) : 0,
        pnlMarked: r2(equity - allocated),
        equity: r2(equity),
        allocatedCash: r2(allocated),
        fills,
        buys,
        sells,
        avgInventory: agents.length ? r1(invAbs / agents.length) : 0,
        maxDrawdown: r2(maxDd),
        marketsMaking: making,
      },
      curve: this.curve.slice(-360),
      agents,
      fills: this.fillLog.slice(0, 80),
      venueBreakdown: [...venueAgg.entries()].map(([venue, v]) => ({
        venue,
        markets: v.markets,
        rewards: r2(v.rewards),
        spreadCapture: r2(v.spreadCapture),
        adverseCost: r2(v.adverseCost),
        fees: r2(v.fees),
        net: r2(v.rewards + v.spreadCapture - v.adverseCost - v.fees),
        fills: v.fills,
      })),
    };
  }

  private ctxOf(a: Agent): QuoteContext {
    const bookMid = (a.book.bids[0]?.price + a.book.asks[0]?.price) / 2 || a.simMid;
    return {
      tick: Math.max(a.book.tick, 0.001),
      fair: a.fair,
      bookMid,
      microPrice: microPrice(a.book.bids, a.book.asks),
      sigma: a.sigma,
      tauH: Math.max(1, 48 - this.simClockH),
      inventory: a.inventory,
      maxInventory: this.params.maxInventory,
      quoteSize: this.params.quoteSize,
      intensity: a.intensity,
      makerFeeRate: venueFees(a.book.venue).makerFeeRate,
      rewardParams: a.rewardParams,
      touchBid: a.book.bids[0]?.price ?? null,
      touchAsk: a.book.asks[0]?.price ?? null,
      touchBidSize: a.book.bids[0]?.size ?? 0,
      touchAskSize: a.book.asks[0]?.size ?? 0,
    };
  }

  get bootingState(): { booting: boolean; error: string | null } {
    return { booting: this.booting, error: this.bootError };
  }
}

function r2(v: number): number {
  return Math.round(v * 100) / 100;
}
function r1(v: number): number {
  return Math.round(v * 10) / 10;
}
function r4(v: number): number {
  return Math.round(v * 10000) / 10000;
}

// -------------------------------------------------------- singleton
const ENGINE_VERSION = 3; // bump to hot-swap engines after code changes
const G = globalThis as unknown as { __qaMmEngine?: MmEngine; __qaMmEngineVersion?: number };

export function getEngine(): MmEngine {
  const existing = G.__qaMmEngine;
  if (existing && G.__qaMmEngineVersion === ENGINE_VERSION) {
    return existing;
  }
  if (existing) {
    // defensive: a previous engine build may lack destroy() — pause() suffices
    try {
      (existing as Partial<MmEngine>).destroy?.();
    } catch {
      /* best effort */
    }
    try {
      (existing as Partial<MmEngine>).pause?.();
    } catch {
      /* best effort */
    }
    G.__qaMmEngine = undefined;
  }
  const engine = new MmEngine();
  G.__qaMmEngine = engine;
  G.__qaMmEngineVersion = ENGINE_VERSION;
  return engine;
}
