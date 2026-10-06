/**
 * Canonical domain types for the live market-making fee engine.
 * Mirrors quant_alpha/core/domain.py + market_making/* (Python reference
 * implementation) so the web console and the offline backtester share the
 * exact same model semantics.
 *
 * Money conventions: contract prices live in [0, 1] USD per $1 payout.
 */

export interface BookLevel {
  price: number; // [0,1]
  size: number; // resting contracts
}

export interface OutcomeBook {
  venue: string;
  marketId: string;
  outcomeKey: string; // 'Yes' | 'YES' | ...
  title: string;
  url: string;
  category: string;
  tick: number;
  bids: BookLevel[]; // sorted best-first (desc price)
  asks: BookLevel[]; // sorted best-first (asc price)
  volume24h: number;
  probability: number | null; // gamma last / forecast
  consensus: number | null; // cross-venue forecast (Manifold) if matched
  closesAt: string | null;
  dataMode: 'live' | 'fixture';
}

export interface FillIntensity {
  /** fills/hour of a quote at zero distance */
  A: number;
  /** 1/price-units exponential decay */
  k: number;
}

export interface RewardParams {
  enabled: boolean;
  poolUsdPerHour: number;
  maxSpread: number;
  sizeCap: number;
  spreadPower: number;
  bookScore: number;
}

export interface QuoteContext {
  tick: number;
  fair: number;
  bookMid: number;
  microPrice: number | null;
  /** logit-space vol per sqrt-hour */
  sigma: number;
  /** hours to close (capped) */
  tauH: number;
  inventory: number;
  maxInventory: number;
  quoteSize: number;
  intensity: FillIntensity;
  makerFeeRate: number;
  rewardParams: RewardParams;
  /** competing book touch (what our quotes must beat to fill) */
  touchBid: number | null;
  touchAsk: number | null;
  touchBidSize: number;
  touchAskSize: number;
}

export interface StrategyQuote {
  bid: number | null;
  ask: number | null;
  size: number;
  meta: Record<string, number | string>;
}

export interface FillRecord {
  id: number;
  ts: number; // epoch ms
  simT: number; // simulated hours since start
  venue: string;
  marketId: string;
  title: string;
  side: 'bid' | 'ask'; // bid = we bought, ask = we sold
  price: number;
  qty: number;
  edge: number; // realized edge per contract (signed)
  adverse: boolean;
}

export type StrategyId = 'optimizer' | 'as' | 'adaptive';

export interface EngineParams {
  strategy: StrategyId;
  gamma: number;
  quoteSize: number;
  maxInventory: number;
  /** simulated hours per real second */
  speed: number;
  adverseFrac: number;
  perMarketCash: number;
}

export interface CurvePoint {
  t: number; // real seconds since start
  simT: number; // simulated hours
  rewards: number;
  spreadCapture: number;
  adverseCost: number;
  net: number; // rewards + capture - adverse - fees
  baselineNet: number; // naive static-spread shadow strategy
  equity: number; // marked equity of the engine bankroll
}

export interface MarketAgentState {
  venue: string;
  marketId: string;
  outcomeKey: string;
  title: string;
  url: string;
  dataMode: 'live' | 'fixture';
  fair: number;
  mid: number;
  sigma: number;
  bid: number | null;
  ask: number | null;
  halfSpread: number | null;
  optimalHalfSpread: number | null;
  inventory: number;
  avgCost: number;
  cash: number;
  equity: number;
  fills: number;
  buys: number;
  sells: number;
  rewards: number;
  spreadCapture: number;
  adverseCost: number;
  fees: number;
  expHourlyEarnings: number;
  expRewardsPerH: number;
  expCapturePerH: number;
  expAdversePerH: number;
  baselineNet: number;
  intensityA: number;
  intensityK: number;
  killed: boolean;
  killReason: string;
  enabled: boolean;
  bookTop: { bid: number | null; ask: number | null; bidSize: number; askSize: number };
}

export interface EngineStatus {
  running: boolean;
  startedAt: number | null;
  simClockH: number;
  ticks: number;
  dataMode: 'live' | 'fixture' | 'mixed';
  lastRefreshAt: number | null;
  lastRefreshError: string | null;
  globalKill: boolean;
  globalKillReason: string;
  params: EngineParams;
}

export interface EngineSnapshot {
  status: EngineStatus;
  totals: {
    rewards: number;
    spreadCapture: number;
    adverseCost: number;
    fees: number;
    netEarned: number; // rewards + spreadCapture - adverseCost - fees
    grossFeesEarned: number; // rewards + spreadCapture (gross income)
    baselineNet: number;
    upliftUsd: number;
    upliftPct: number;
    pnlMarked: number; // equity - allocated cash
    equity: number;
    allocatedCash: number;
    fills: number;
    buys: number;
    sells: number;
    avgInventory: number;
    maxDrawdown: number;
    marketsMaking: number;
  };
  curve: CurvePoint[];
  agents: MarketAgentState[];
  fills: FillRecord[];
  venueBreakdown: Array<{
    venue: string;
    markets: number;
    rewards: number;
    spreadCapture: number;
    adverseCost: number;
    fees: number;
    net: number;
    fills: number;
  }>;
}
