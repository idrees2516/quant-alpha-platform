'use client';

import { Building2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { EngineSnapshot } from '@/lib/mm/types';

const VENUE_COLOR: Record<string, string> = {
  polymarket: '#10b981',
  kalshi: '#f59e0b',
  predictit: '#fb7185',
};

function usd(v: number): string {
  const sign = v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

export function VenueBreakdown({ snap }: { snap: EngineSnapshot }) {
  const rows = [...snap.venueBreakdown].sort((a, b) => b.net - a.net);
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.net)));

  return (
    <Card className="border-zinc-800 bg-zinc-900/60 backdrop-blur">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
          <Building2 className="h-4 w-4 text-emerald-400" />
          Fee earnings by venue
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {rows.map((r) => (
          <div key={r.venue} className="space-y-1">
            <div className="flex items-baseline justify-between text-xs">
              <span className="font-medium capitalize text-zinc-200">
                {r.venue}
                <span className="ml-1.5 text-[10px] text-zinc-500">
                  {r.markets} market{r.markets > 1 ? 's' : ''} · {r.fills} fills
                </span>
              </span>
              <span className={`font-mono tabular-nums ${r.net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {usd(r.net)}
              </span>
            </div>
            <div className="flex h-2 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${(Math.abs(r.net) / maxAbs) * 100}%`,
                  backgroundColor: VENUE_COLOR[r.venue] ?? '#a1a1aa',
                }}
              />
            </div>
            <div className="flex justify-between text-[10px] text-zinc-500">
              <span>rewards {usd(r.rewards)}</span>
              <span>capture {usd(r.spreadCapture)}</span>
              <span className="text-rose-400/80">adverse -{usd(r.adverseCost)}</span>
            </div>
          </div>
        ))}
        {!rows.length && <p className="py-6 text-center text-sm text-zinc-500">no venue data yet</p>}
      </CardContent>
    </Card>
  );
}
