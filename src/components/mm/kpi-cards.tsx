'use client';

import { motion } from 'framer-motion';
import { Activity, ArrowDownRight, ArrowUpRight, Banknote, Coins, Gauge, ShieldAlert, Zap } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { EngineSnapshot } from '@/lib/mm/types';

function usd(v: number, digits = 2): string {
  const sign = v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function KpiShell({
  title,
  icon,
  children,
  accent,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  accent: string;
}) {
  return (
    <Card className="border-zinc-800 bg-zinc-900/60 backdrop-blur">
      <CardHeader className="pb-1">
        <CardTitle className="flex items-center justify-between text-xs font-medium tracking-wide text-zinc-400 uppercase">
          <span className="flex items-center gap-1.5">
            {icon}
            {title}
          </span>
          {accent}
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function KpiCards({ snap }: { snap: EngineSnapshot }) {
  const t = snap.totals;
  const rising = t.netEarned >= 0;
  const simH = snap.status.simClockH;

  return (
    <section aria-label="Key metrics" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <KpiShell
        title="Net Fees Earned"
        icon={<Banknote className="h-3.5 w-3.5 text-emerald-400" />}
        accent={
          t.upliftUsd > 0 ? (
            <Badge variant="outline" className="border-emerald-800 bg-emerald-950/60 text-[10px] text-emerald-400">
              <Zap className="mr-0.5 h-2.5 w-2.5" />
              {t.upliftPct > 0
                ? `+${t.upliftPct.toFixed(0)}% vs naive`
                : `+$${t.upliftUsd.toFixed(2)} vs naive`}
            </Badge>
          ) : null
        }
      >
        <motion.div
          key={Math.round(t.netEarned)}
          initial={{ opacity: 0.6 }}
          animate={{ opacity: 1 }}
          className={`font-mono text-2xl font-semibold tabular-nums ${rising ? 'text-emerald-400' : 'text-rose-400'}`}
        >
          {usd(t.netEarned)}
        </motion.div>
        <p className="mt-1 text-[11px] text-zinc-500">
          gross {usd(t.grossFeesEarned)} · after adverse &amp; fees
        </p>
        <p className="text-[11px] text-zinc-500">
          {t.marketsMaking} markets · {simH.toFixed(1)} sim-hours
        </p>
      </KpiShell>

      <KpiShell title="Liquidity Rewards" icon={<Coins className="h-3.5 w-3.5 text-emerald-400" />}>
        <div className="font-mono text-2xl font-semibold tabular-nums text-emerald-300">{usd(t.rewards)}</div>
        <p className="mt-1 text-[11px] text-zinc-500">Polymarket reward pool share</p>
      </KpiShell>

      <KpiShell title="Spread Capture" icon={<Gauge className="h-3.5 w-3.5 text-amber-400" />}>
        <div className="font-mono text-2xl font-semibold tabular-nums text-amber-300">{usd(t.spreadCapture)}</div>
        <p className="mt-1 text-[11px] text-zinc-500">bid-ask harvest across venues</p>
      </KpiShell>

      <KpiShell title="Adverse Selection" icon={<ShieldAlert className="h-3.5 w-3.5 text-rose-400" />}>
        <div className="font-mono text-2xl font-semibold tabular-nums text-rose-300">-{usd(t.adverseCost)}</div>
        <p className="mt-1 text-[11px] text-zinc-500">informed-flow cost modeled live</p>
      </KpiShell>

      <KpiShell
        title="Marked PnL"
        icon={t.pnlMarked >= 0 ? <ArrowUpRight className="h-3.5 w-3.5 text-emerald-400" /> : <ArrowDownRight className="h-3.5 w-3.5 text-rose-400" />}
      >
        <div className={`font-mono text-2xl font-semibold tabular-nums ${t.pnlMarked >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
          {usd(t.pnlMarked)}
        </div>
        <p className="mt-1 text-[11px] text-zinc-500">equity {usd(t.equity, 0)} of {usd(t.allocatedCash, 0)}</p>
      </KpiShell>

      <KpiShell title="Fills" icon={<Activity className="h-3.5 w-3.5 text-zinc-400" />}>
        <div className="font-mono text-2xl font-semibold tabular-nums text-zinc-100">
          {t.fills.toLocaleString('en-US')}
        </div>
        <p className="mt-1 text-[11px] text-zinc-500">
          {t.buys} buys · {t.sells} sells · avg inv {t.avgInventory.toFixed(0)}
        </p>
      </KpiShell>
    </section>
  );
}
