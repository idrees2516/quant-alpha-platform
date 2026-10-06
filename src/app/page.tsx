'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { AlertTriangle, CircleDot, Clock, Database, LineChart, RefreshCw, TrendingUp } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { KpiCards } from '@/components/mm/kpi-cards';
import { EarningsChart } from '@/components/mm/earnings-chart';
import { MarketsTable } from '@/components/mm/markets-table';
import { FillsFeed } from '@/components/mm/fills-feed';
import { EngineControls } from '@/components/mm/engine-controls';
import { VenueBreakdown } from '@/components/mm/venue-breakdown';
import type { EngineParams, EngineSnapshot } from '@/lib/mm/types';

const POLL_MS = 1000;

function uptime(from: number | null): string {
  if (!from) return '—';
  const s = Math.max(0, Math.floor((Date.now() - from) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0 ? `${h}h ${m}m ${sec}s` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

export default function Home() {
  const [snap, setSnap] = useState<EngineSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connLost, setConnLost] = useState(0);
  const aliveRef = useRef(true);

  const poll = useCallback(async () => {
    try {
      const res = await fetch('/api/mm/state', { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as EngineSnapshot;
      if (aliveRef.current) {
        setSnap(data);
        setError(null);
        setConnLost(0);
      }
    } catch (e) {
      if (aliveRef.current) {
        setConnLost((n) => n + 1);
        setError(e instanceof Error ? e.message : 'engine unreachable');
      }
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    void poll();
    const t = setInterval(() => void poll(), POLL_MS);
    return () => {
      aliveRef.current = false;
      clearInterval(t);
    };
  }, [poll]);

  const control = useCallback(
    async (body: Record<string, unknown>) => {
      setBusy(true);
      try {
        await fetch('/api/mm/control', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        });
        await poll();
      } finally {
        setBusy(false);
      }
    },
    [poll]
  );

  const onAction = useCallback(
    (action: 'start' | 'pause' | 'reset' | 'clearKill') => void control({ action }),
    [control]
  );
  const onParams = useCallback(
    (patch: Partial<EngineParams>) => void control({ action: 'setParams', params: patch }),
    [control]
  );
  const onToggle = useCallback(
    (marketId: string, enabled: boolean) => void control({ action: 'toggleMarket', marketId, enabled }),
    [control]
  );

  const st = snap?.status;
  const booting = !snap || (snap.status.ticks === 0 && snap.agents.length === 0);

  return (
    <div className="flex min-h-screen flex-col bg-zinc-950 text-zinc-100">
      {/* ------------------------------------------------------------ header */}
      <header className="sticky top-0 z-20 border-b border-zinc-800/80 bg-zinc-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-3 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-emerald-800 bg-emerald-950/80">
              <TrendingUp className="h-5 w-5 text-emerald-400" />
            </div>
            <div>
              <h1 className="text-base leading-tight font-semibold tracking-tight">QuantAlpha · Market-Making Fee Engine</h1>
              <p className="text-[11px] leading-tight text-zinc-500">
                Avellaneda-Stoikov + golden-section earnings optimizer · Polymarket / Kalshi / PredictIt
              </p>
            </div>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            {st?.running ? (
              <Badge variant="outline" className="gap-1.5 border-emerald-800 bg-emerald-950/70 text-emerald-400">
                <motion.span
                  className="h-1.5 w-1.5 rounded-full bg-emerald-400"
                  animate={{ opacity: [1, 0.25, 1] }}
                  transition={{ repeat: Infinity, duration: 1.6 }}
                />
                RUNNING
              </Badge>
            ) : (
              <Badge variant="outline" className="gap-1.5 border-zinc-700 bg-zinc-900 text-zinc-400">
                <CircleDot className="h-2.5 w-2.5" /> PAUSED
              </Badge>
            )}
            <Badge
              variant="outline"
              className={`gap-1.5 ${
                st?.dataMode === 'live'
                  ? 'border-emerald-800 bg-emerald-950/70 text-emerald-400'
                  : 'border-amber-800 bg-amber-950/70 text-amber-300'
              }`}
              title={st?.lastRefreshError ?? 'live order books from venue APIs'}
            >
              <Database className="h-2.5 w-2.5" />
              {st?.dataMode === 'live' ? 'LIVE BOOKS' : st?.dataMode === 'mixed' ? 'MIXED FEED' : 'FIXTURE MODE'}
            </Badge>
            <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
              <Clock className="h-3 w-3" />
              <span className="font-mono tabular-nums">{uptime(st?.startedAt ?? null)}</span>
              <span className="text-zinc-700">|</span>
              <span className="font-mono tabular-nums text-zinc-400">{st?.simClockH.toFixed(1) ?? '0.0'} sim-h</span>
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void poll()}
              className="h-7 border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800"
            >
              <RefreshCw className={`h-3 w-3 ${busy ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>
      </header>

      {/* ------------------------------------------------------------- banner */}
      {(st?.globalKill || error) && (
        <div className="mx-auto w-full max-w-[1440px] px-4 pt-4">
          {st?.globalKill && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-rose-900 bg-rose-950/50 px-4 py-2.5 text-sm text-rose-300">
              <AlertTriangle className="h-4 w-4" />
              <span className="font-medium">Kill switch triggered:</span>
              <span className="text-rose-400/90">{st.globalKillReason}</span>
              <Button
                size="sm"
                variant="outline"
                className="ml-auto border-rose-800 bg-rose-950/60 text-rose-300 hover:bg-rose-900/60"
                disabled={busy}
                onClick={() => onAction('clearKill')}
              >
                Clear &amp; resume
              </Button>
            </div>
          )}
          {error && connLost >= 2 && (
            <div className="mt-2 rounded-lg border border-amber-900 bg-amber-950/40 px-4 py-2 text-xs text-amber-300">
              engine API unreachable ({error}) — retrying each second…
            </div>
          )}
        </div>
      )}

      {/* -------------------------------------------------------------- main */}
      <main className="mx-auto w-full max-w-[1440px] flex-1 space-y-5 px-4 py-5">
        {booting && !snap && (
          <Card className="border-zinc-800 bg-zinc-900/60">
            <CardContent className="flex items-center gap-3 py-10 text-sm text-zinc-400">
              <RefreshCw className="h-4 w-4 animate-spin text-emerald-400" />
              booting engine — fetching live order books from Polymarket / Kalshi / Manifold…
            </CardContent>
          </Card>
        )}

        {snap && (
          <>
            <KpiCards snap={snap} />

            <div className="grid gap-4 lg:grid-cols-3">
              <Card className="border-zinc-800 bg-zinc-900/60 backdrop-blur lg:col-span-2">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
                    <LineChart className="h-4 w-4 text-emerald-400" />
                    Cumulative fee earnings
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <EarningsChart curve={snap.curve} allocatedCash={snap.totals.allocatedCash} />
                </CardContent>
              </Card>

              <EngineControls status={snap.status} onAction={onAction} onParams={onParams} busy={busy} />
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              <Card className="border-zinc-800 bg-zinc-900/60 backdrop-blur lg:col-span-2">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
                    <span className="h-2 w-2 rounded-full bg-emerald-400" />
                    Live quotes · every market the engine is making
                  </CardTitle>
                  <p className="text-[11px] text-zinc-500">
                    quotes re-solved each tick from measured book microstructure — spread maximizes expected hourly
                    fee earnings, inventory-skewed, fee-floored
                  </p>
                </CardHeader>
                <CardContent className="pb-3">
                  <MarketsTable agents={snap.agents} maxInventory={snap.status.params.maxInventory} onToggle={onToggle} />
                </CardContent>
              </Card>

              <Card className="flex flex-col border-zinc-800 bg-zinc-900/60 backdrop-blur">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
                    <span className="relative flex h-2 w-2">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                    </span>
                    Fill tape
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex-1">
                  <FillsFeed fills={snap.fills} />
                </CardContent>
              </Card>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <VenueBreakdown snap={snap} />
              <Card className="border-zinc-800 bg-zinc-900/60 backdrop-blur">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-semibold text-zinc-200">How the fee engine earns</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2.5 text-xs leading-relaxed text-zinc-400">
                  <p>
                    <span className="text-emerald-300">Liquidity rewards</span> — on Polymarket the daily USD pool is
                    split by score <span className="font-mono text-zinc-300">min(size,cap)·(1−spread/max)²</span>;
                    the optimizer measures the live book score and solves for the reward-maximizing spread by
                    golden-section search each tick.
                  </p>
                  <p>
                    <span className="text-amber-300">Spread capture</span> — resting quotes harvest the bid-ask
                    spread; fill intensity is calibrated from resting depth (<span className="font-mono text-zinc-300">λ(δ)=A·e^(−kδ)</span>),
                    and quotes behind the touch fill only when the mid sweeps to them (Gaussian sweep model).
                  </p>
                  <p>
                    <span className="text-rose-300">Adverse selection</span> — a fraction of fills is informed and
                    realizes a loss of ~1.6σ·p(1−p) against us; inventory skew, fee floors, hard inventory caps and
                    the kill switch bound the cost.
                  </p>
                  <p>
                    The <span className="text-zinc-300">naive baseline</span> (dashed) runs the identical fill model
                    with a static wide spread and no tuning — the gap between it and the net line is the
                    optimizer&apos;s live edge.
                  </p>
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </main>

      <footer className="mt-auto border-t border-zinc-800/80 bg-zinc-950">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-[11px] text-zinc-600">
          <span>quant-alpha-platform · web console</span>
          <span className="text-zinc-700">|</span>
          <span>fills, adverse selection and rewards simulated on the venue microstructure model shared with the offline backtester</span>
          <span className="text-zinc-700">|</span>
          <span>not financial advice · simulated execution</span>
        </div>
      </footer>
    </div>
  );
}
