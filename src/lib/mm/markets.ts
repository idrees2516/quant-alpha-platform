/**
 * Market universe for the live MM engine: real order books from
 * Polymarket (gamma + CLOB), Kalshi (trade-api v2) and Manifold forecasts,
 * with a deterministic seeded fixture universe as offline fallback.
 * Mirrors the parsing of quant_alpha/connectors/prediction/*.py.
 */

import type { BookLevel, OutcomeBook } from './types';
import { crc32 } from './microstructure';

const GAMMA = 'https://gamma-api.polymarket.com';
const CLOB = 'https://clob.polymarket.com';
const KALSHI = 'https://api.elections.kalshi.com/trade-api/v2';
const MANIFOLD = 'https://api.manifold.markets/v0';

const FETCH_TIMEOUT_MS = 8000;

function num(x: unknown, dflt: number | null = null): number | null {
  const v = typeof x === 'string' ? Number.parseFloat(x) : typeof x === 'number' ? x : NaN;
  return Number.isFinite(v) ? v : dflt;
}

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    const res = await fetch(url, {
      signal: ctrl.signal,
      headers: { accept: 'application/json', 'user-agent': 'quant-alpha-console/1.0' },
      cache: 'no-store',
    });
    clearTimeout(timer);
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------ Polymarket
interface GammaRow {
  conditionId?: string;
  id?: string;
  question?: string;
  slug?: string;
  category?: string;
  outcomes?: string;
  outcomePrices?: string;
  clobTokenIds?: string;
  volume24hr?: string | number;
  liquidityNum?: string | number;
  orderPriceMinTickSize?: string | number;
  enableOrderBook?: string | boolean;
  endDate?: string;
}

interface ClobRow {
  price: string | number;
  size: string | number;
}

async function fetchPolymarket(count: number): Promise<OutcomeBook[]> {
  const data = await getJson<GammaRow[] | { items?: GammaRow[] }>(
    `${GAMMA}/markets?active=true&closed=false&limit=100&order=volume24hr&ascending=false`
  );
  const rows = Array.isArray(data) ? data : data?.items ?? [];
  const parsed: Array<{ row: GammaRow; tokens: string[]; tick: number; prob: number | null; vol: number; key: string }> = [];
  for (const m of rows) {
    if (String(m.enableOrderBook ?? 'false').toLowerCase() !== 'true') continue;
    let names: string[] = [];
    let tokens: string[] = [];
    try {
      names = JSON.parse(m.outcomes ?? '[]');
      tokens = JSON.parse(m.clobTokenIds ?? '[]');
    } catch {
      continue;
    }
    if (!names.length || tokens.length < names.length) continue;
    const vol = num(m.volume24hr, 0) ?? 0;
    if (vol <= 1000) continue; // only liquid books get depth-fetched
    const tick = num(m.orderPriceMinTickSize, 0.01) ?? 0.01;
    parsed.push({ row: m, tokens, tick, prob: null, vol, key: names[0] });
    if (parsed.length >= count) break;
  }
  // attach CLOB depth for the YES token of the most liquid subset
  const out: OutcomeBook[] = [];
  for (const p of parsed.slice(0, count)) {
    const token = p.tokens[0];
    if (!token) continue;
    const book = await getJson<{ bids?: ClobRow[]; asks?: ClobRow[] }>(`${CLOB}/book?token_id=${encodeURIComponent(token)}`);
    if (!book) continue;
    const bids = (book.bids ?? [])
      .map((b) => ({ price: num(b.price) ?? -1, size: num(b.size, 0) ?? 0 }))
      .filter((b) => b.price >= 0 && b.price <= 1)
      .sort((a, b) => b.price - a.price);
    const asks = (book.asks ?? [])
      .map((a) => ({ price: num(a.price) ?? -1, size: num(a.size, 0) ?? 0 }))
      .filter((a) => a.price >= 0 && a.price <= 1)
      .sort((a, b) => a.price - b.price);
    if (!bids.length || !asks.length) continue;
    const mid = (bids[0].price + asks[0].price) / 2;
    if (mid <= 0.03 || mid >= 0.97) continue; // skip degenerate extremes
    out.push({
      venue: 'polymarket',
      marketId: String(p.row.conditionId ?? p.row.id ?? ''),
      outcomeKey: p.key,
      title: String(p.row.question ?? '').slice(0, 120),
      url: `https://polymarket.com/market/${p.row.slug ?? ''}`,
      category: String(p.row.category ?? 'other'),
      tick: Math.max(p.tick, 0.001),
      bids: bids.slice(0, 8),
      asks: asks.slice(0, 8),
      volume24h: p.vol,
      probability: mid,
      consensus: null,
      closesAt: p.row.endDate ?? null,
      dataMode: 'live',
    });
  }
  return out;
}

// ---------------------------------------------------------------- Kalshi
interface KalshiRow {
  ticker?: string;
  event_ticker?: string;
  title?: string;
  market_type?: string;
  yes_bid_dollars?: number | string;
  yes_ask_dollars?: number | string;
  volume_24h_fp?: number | string;
  volume_fp?: number | string;
  open_interest_fp?: number | string;
  close_time?: string;
  last_price_dollars?: number | string;
}

interface KalshiEvent {
  event_ticker?: string;
  title?: string;
  markets?: KalshiRow[];
}

function kNum(x: unknown, dflt = 0): number {
  const v = typeof x === 'string' ? Number.parseFloat(x) : typeof x === 'number' ? x : NaN;
  return Number.isFinite(v) ? v : dflt;
}

function kalshiVol(m: KalshiRow): number {
  return kNum(m.volume_24h_fp) || kNum(m.volume_fp);
}

/**
 * Kalshi orderbook (v2): {"orderbook_fp": {"yes_dollars": [[p,s]...],
 * "no_dollars": [[p,s]...]}} — yes_dollars are resting YES BUY orders (bids);
 * no_dollars are resting NO BUY orders, which are YES ASKS at 1 - p.
 * Some responses use cents in plain "yes"/"no" arrays instead.
 */
async function fetchKalshi(count: number): Promise<OutcomeBook[]> {
  // page the events endpoint (nested markets) to find two-sided quoted markets
  const events: KalshiEvent[] = [];
  let cursor = '';
  for (let page = 0; page < 4; page++) {
    const url =
      `${KALSHI}/events?status=open&limit=60&with_nested_markets=true` +
      (cursor ? `&cursor=${encodeURIComponent(cursor)}` : '');
    const data = await getJson<{ events?: KalshiEvent[]; cursor?: string }>(url);
    const evs = data?.events ?? [];
    if (!evs.length) break;
    events.push(...evs);
    cursor = data?.cursor ?? '';
    const quoted = events.some((e) =>
      (e.markets ?? []).some(
        (m) => kNum(m.yes_bid_dollars) > 0.02 && kNum(m.yes_ask_dollars) > kNum(m.yes_bid_dollars) && kNum(m.yes_ask_dollars) < 1
      )
    );
    if (!cursor || (page >= 1 && quoted)) break;
  }
  const candidates: KalshiRow[] = [];
  const titleByTicker = new Map<string, string>();
  for (const e of events) {
    for (const m of e.markets ?? []) {
      const b = kNum(m.yes_bid_dollars);
      const a = kNum(m.yes_ask_dollars);
      if (b > 0.02 && a > b && a < 1 && (m.market_type ?? 'binary') === 'binary') {
        candidates.push(m);
        titleByTicker.set(String(m.ticker), String(e.title ?? m.title ?? m.ticker));
      }
    }
  }
  candidates.sort((x, y) => kalshiVol(y) - kalshiVol(x));
  const out: OutcomeBook[] = [];
  for (const m of candidates.slice(0, count * 2)) {
    const ticker = String(m.ticker ?? '');
    if (!ticker) continue;
    const book = await getJson<Record<string, unknown>>(
      `${KALSHI}/markets/${encodeURIComponent(ticker)}/orderbook`
    );
    let bids: BookLevel[] = [];
    let asks: BookLevel[] = [];
    if (book) {
      const fp = (book.orderbook_fp ?? {}) as Record<string, unknown>;
      const rawYes = (book.yes ?? fp.yes_dollars) as Array<[string | number, string | number]> | undefined;
      const rawNo = (book.no ?? fp.no_dollars) as Array<[string | number, string | number]> | undefined;
      const levels = (rows: Array<[string | number, string | number]> | undefined) =>
        (rows ?? [])
          .map((e) => {
            let p = kNum(Array.isArray(e) ? e[0] : NaN, -1);
            if (p > 1 && p <= 100) p = p / 100; // cents → dollars guard
            const s = kNum(Array.isArray(e) ? e[1] : NaN);
            return { price: p, size: s };
          })
          .filter((r) => r.price >= 0 && r.price <= 1 && r.size > 0);
      bids = levels(rawYes).sort((x, y) => y.price - x.price).slice(0, 8);
      // YES asks = complement of NO bids (buy NO at p == sell YES at 1 - p)
      asks = levels(rawNo)
        .map((l) => ({ price: 1 - l.price, size: l.size }))
        .filter((l) => l.price > 0 && l.price < 1)
        .sort((x, y) => x.price - y.price)
        .slice(0, 8);
    }
    // fallback to top-of-book quotes from the market row when depth is empty
    if (!bids.length || !asks.length) {
      const b = kNum(m.yes_bid_dollars);
      const a = kNum(m.yes_ask_dollars);
      if (b <= 0.02 || a <= b || a >= 1) continue;
      const mkLevel = (p: number, d: number): BookLevel => ({ price: p, size: 400 + d * 250 });
      bids = [0, 1, 2].map((i) => mkLevel(b - i * 0.01, i));
      asks = [0, 1, 2].map((i) => mkLevel(a + i * 0.01, i));
    }
    const mid = (bids[0].price + asks[0].price) / 2;
    if (mid <= 0.03 || mid >= 0.97) continue;
    out.push({
      venue: 'kalshi',
      marketId: ticker,
      outcomeKey: 'YES',
      title: (titleByTicker.get(ticker) || String(m.title ?? ticker)).slice(0, 120),
      url: `https://kalshi.com/markets/${String(m.event_ticker ?? '').toLowerCase()}`,
      category: 'other',
      tick: 0.01,
      bids,
      asks,
      volume24h: Math.max(kalshiVol(m), 200),
      probability: kNum(m.last_price_dollars, mid) || mid,
      consensus: null,
      closesAt: m.close_time ?? null,
      dataMode: 'live',
    });
    if (out.length >= count) break;
  }
  return out;
}

// -------------------------------------------------------------- Manifold
interface ManifoldRow {
  question?: string;
  probability?: number;
  volume24Hours?: number;
  closeTime?: number;
  outcomeType?: string;
}

/** Top Manifold binary forecasts — cross-venue consensus input. */
export async function fetchManifoldForecasts(count = 40): Promise<Array<{ title: string; p: number }>> {
  const data = await getJson<ManifoldRow[]>(`${MANIFOLD}/markets?limit=100`);
  if (!Array.isArray(data)) return [];
  return data
    .filter((m) => m.outcomeType === 'BINARY' && typeof m.probability === 'number')
    .sort((a, b) => (b.volume24Hours ?? 0) - (a.volume24Hours ?? 0))
    .slice(0, count)
    .map((m) => ({ title: String(m.question ?? ''), p: m.probability as number }));
}

// -------------------------------------------------- consensus title match
function tokenize(s: string): Set<string> {
  return new Set(
    s
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, ' ')
      .split(/\s+/)
      .filter((w) => w.length > 2 && !STOP.has(w))
  );
}

const STOP = new Set(['the', 'and', 'for', 'will', 'who', 'what', 'when', 'how', 'many', 'much', 'does', 'that', 'this', 'with', 'from', 'have', 'than', 'into', 'over', 'before', 'after', 'during', 'between', 'against', 'about', 'again', 'further', 'once', 'here', 'there', 'all', 'any', 'both', 'each', 'few', 'more', 'most', 'other', 'some', 'such', 'only', 'own', 'same', 'very', 'just', '2026', '2027', '2028']);

/** Jaccard similarity of meaningful tokens. */
export function titleSimilarity(a: string, b: string): number {
  const sa = tokenize(a);
  const sb = tokenize(b);
  if (!sa.size || !sb.size) return 0;
  let inter = 0;
  for (const t of sa) if (sb.has(t)) inter += 1;
  return inter / (sa.size + sb.size - inter);
}

// ------------------------------------------------------------- fixture
/** Deterministic offline universe (same shapes as Python fixtures). */
export function fixtureUniverse(): OutcomeBook[] {
  const specs: Array<[string, string, string, number, number, number]> = [
    ['polymarket', 'Will the Fed cut rates at the next FOMC meeting?', 'economics', 0.62, 64000, 0.01],
    ['polymarket', 'Will Bitcoin close above $120,000 in 2026?', 'crypto', 0.38, 41000, 0.01],
    ['polymarket', 'Will the US pass a federal AI safety bill in 2026?', 'politics', 0.21, 28000, 0.01],
    ['polymarket', 'Will SpaceX Starship reach orbit on its next launch?', 'tech', 0.66, 33000, 0.01],
    ['polymarket', 'Will Argentina win the next Copa America?', 'sports', 0.17, 19000, 0.01],
    ['kalshi', 'Fed rate decision: cut at next meeting', 'economics', 0.60, 31000, 0.01],
    ['kalshi', 'Bitcoin price above $115k on Dec 31', 'crypto', 0.35, 22000, 0.01],
    ['kalshi', 'US CPI YoY prints above 2.5%', 'economics', 0.44, 15000, 0.01],
    ['kalshi', ' Knicks vs. Celtics: Knicks win', 'sports', 0.41, 12000, 0.01],
    ['predictit', 'Will the Fed cut rates by March?', 'economics', 0.57, 9000, 0.01],
  ];
  return specs.map(([venue, title, category, mid, vol, tick], i) => {
    const seed = crc32(title);
    const rnd = (k: number) => ((Math.sin(seed * 0.001 + k * 12.9898) * 43758.5453) % 1 + 1) % 1;
    const spread = 0.02 + rnd(1) * 0.03;
    const bids: BookLevel[] = [];
    const asks: BookLevel[] = [];
    for (let lvl = 0; lvl < 6; lvl++) {
      const growth = 1 + 0.85 * lvl;
      bids.push({
        price: Math.max(mid - spread / 2 - lvl * tick, tick),
        size: Math.round((150 + rnd(lvl + 2) * 400) * growth),
      });
      asks.push({
        price: Math.min(mid + spread / 2 + lvl * tick, 1 - tick),
        size: Math.round((150 + rnd(lvl + 10) * 400) * growth),
      });
    }
    return {
      venue,
      marketId: `fixture-${i}`,
      outcomeKey: venue === 'kalshi' ? 'YES' : 'Yes',
      title,
      url: '#',
      category,
      tick,
      bids,
      asks,
      volume24h: vol,
      probability: mid,
      consensus: null,
      closesAt: null,
      dataMode: 'fixture' as const,
    };
  });
}

// ------------------------------------------------------------ top level
export interface UniverseResult {
  books: OutcomeBook[];
  forecasts: Array<{ title: string; p: number }>;
  mode: 'live' | 'fixture' | 'mixed';
  error: string | null;
}

export async function loadUniverse(
  polymarketCount = 6,
  kalshiCount = 4
): Promise<UniverseResult> {
  const [poly, kal, forecasts] = await Promise.all([
    fetchPolymarket(polymarketCount).catch(() => [] as OutcomeBook[]),
    fetchKalshi(kalshiCount).catch(() => [] as OutcomeBook[]),
    fetchManifoldForecasts(60).catch(() => [] as Array<{ title: string; p: number }>),
  ]);
  const books = [...poly, ...kal];
  const liveCount = books.length;
  if (liveCount < 4) {
    const fix = fixtureUniverse();
    return {
      books: liveCount > 0 ? [...books, ...fix].slice(0, 10) : fix,
      forecasts,
      mode: liveCount > 0 ? 'mixed' : 'fixture',
      error: liveCount > 0 ? null : 'live feeds unreachable — deterministic fixture universe engaged',
    };
  }
  // attach consensus where a Manifold forecast matches
  for (const b of books) {
    let best = 0;
    let bestP: number | null = null;
    for (const f of forecasts) {
      const sim = titleSimilarity(b.title, f.title);
      if (sim > best) {
        best = sim;
        bestP = f.p;
      }
    }
    if (best >= 0.5 && bestP !== null) b.consensus = bestP;
  }
  return { books, forecasts, mode: 'live', error: null };
}
