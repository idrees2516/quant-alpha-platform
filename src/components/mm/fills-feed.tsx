'use client';

import { ArrowDownRight, ArrowUpRight, Radio } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { FillRecord } from '@/lib/mm/types';

function timeOf(ts: number): string {
  return new Date(ts).toLocaleTimeString('en-US', { hour12: false });
}

const VENUE_STYLE: Record<string, string> = {
  polymarket: 'border-emerald-800 bg-emerald-950/70 text-emerald-400',
  kalshi: 'border-amber-800 bg-amber-950/70 text-amber-300',
  predictit: 'border-rose-900 bg-rose-950/70 text-rose-300',
};

export function FillsFeed({ fills }: { fills: FillRecord[] }) {
  return (
    <div className="max-h-[520px] space-y-1.5 overflow-y-auto pr-1 [scrollbar-color:#3f3f46_transparent] [scrollbar-width:thin] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded [&::-webkit-scrollbar-thumb]:bg-zinc-700">
      {fills.map((f) => {
        const buy = f.side === 'bid';
        return (
          <div
            key={f.id}
            className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs ${
              f.adverse ? 'border-rose-900/60 bg-rose-950/20' : 'border-zinc-800/70 bg-zinc-900/40'
            }`}
          >
            <span className="w-[62px] shrink-0 font-mono text-[10px] tabular-nums text-zinc-500">{timeOf(f.ts)}</span>
            <Badge
              variant="outline"
              className={`shrink-0 gap-0.5 px-1.5 text-[10px] ${
                buy ? 'border-emerald-800 bg-emerald-950/70 text-emerald-400' : 'border-rose-800 bg-rose-950/70 text-rose-300'
              }`}
            >
              {buy ? <ArrowUpRight className="h-2.5 w-2.5" /> : <ArrowDownRight className="h-2.5 w-2.5" />}
              {buy ? 'BUY' : 'SELL'}
            </Badge>
            <span className="w-[52px] shrink-0 font-mono tabular-nums text-zinc-200">{(f.price * 100).toFixed(1)}¢</span>
            <span className="w-[36px] shrink-0 font-mono tabular-nums text-zinc-500">×{f.qty}</span>
            <span className="truncate text-[11px] text-zinc-400" title={f.title}>
              {f.title}
            </span>
            <span className="ml-auto shrink-0 font-mono tabular-nums">
              <span className={f.edge >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                {f.edge >= 0 ? '+' : ''}{(f.edge * 100).toFixed(2)}¢
              </span>
              {f.adverse && <span className="ml-1 text-[9px] text-rose-500">ADV</span>}
            </span>
          </div>
        );
      })}
      {!fills.length && (
        <div className="flex h-40 flex-col items-center justify-center gap-2 text-sm text-zinc-500">
          <Radio className="h-5 w-5 animate-pulse text-zinc-600" />
          waiting for the first fills…
        </div>
      )}
    </div>
  );
}
