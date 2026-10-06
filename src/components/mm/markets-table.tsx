'use client';

import { ExternalLink } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { MarketAgentState } from '@/lib/mm/types';

function cents(p: number | null): string {
  if (p === null || p === undefined) return '—';
  return `${(p * 100).toFixed(1)}¢`;
}

function usd(v: number): string {
  const sign = v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

const VENUE_STYLE: Record<string, string> = {
  polymarket: 'border-emerald-800 bg-emerald-950/70 text-emerald-400',
  kalshi: 'border-amber-800 bg-amber-950/70 text-amber-300',
  predictit: 'border-rose-900 bg-rose-950/70 text-rose-300',
};

export function MarketsTable({
  agents,
  maxInventory,
  onToggle,
}: {
  agents: MarketAgentState[];
  maxInventory: number;
  onToggle: (marketId: string, enabled: boolean) => void;
}) {
  return (
    <div className="max-h-[520px] overflow-y-auto overflow-x-auto rounded-md border border-zinc-800 [scrollbar-color:#3f3f46_transparent] [scrollbar-width:thin] [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded [&::-webkit-scrollbar-thumb]:bg-zinc-700 [&::-webkit-scrollbar-track]:bg-transparent">
      <Table className="min-w-[900px] border-collapse">
        <TableHeader className="sticky top-0 z-10 bg-zinc-900">
          <TableRow className="border-zinc-800 hover:bg-transparent">
            <TableHead className="w-[30%] text-zinc-400">Market (venue)</TableHead>
            <TableHead className="text-right text-zinc-400">Our bid</TableHead>
            <TableHead className="text-right text-zinc-400">Our ask</TableHead>
            <TableHead className="text-right text-zinc-400">Half-sprd</TableHead>
            <TableHead className="text-right text-zinc-400">Optimal h*</TableHead>
            <TableHead className="text-right text-zinc-400">Book</TableHead>
            <TableHead className="text-right text-zinc-400">Inventory</TableHead>
            <TableHead className="text-right text-zinc-400">Fills</TableHead>
            <TableHead className="text-right text-zinc-400">Net earned</TableHead>
            <TableHead className="text-right text-zinc-400">Exp $/h</TableHead>
            <TableHead className="text-center text-zinc-400">MM</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {agents.map((a) => {
            const invPct = Math.min(Math.abs(a.inventory) / Math.max(maxInventory, 1), 1);
            const net = a.rewards + a.spreadCapture - a.adverseCost - a.fees;
            const dead = a.killed;
            return (
              <TableRow
                key={`${a.venue}:${a.marketId}`}
                className={`border-zinc-800/70 ${dead ? 'opacity-50' : 'hover:bg-zinc-900/70'}`}
              >
                <TableCell className="max-w-[260px] py-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className={`shrink-0 text-[10px] ${VENUE_STYLE[a.venue] ?? 'border-zinc-700 bg-zinc-800 text-zinc-300'}`}>
                      {a.venue}
                    </Badge>
                    <a
                      href={a.url !== '#' ? a.url : undefined}
                      target="_blank"
                      rel="noreferrer"
                      className="truncate text-xs text-zinc-200 hover:text-emerald-300 hover:underline"
                      title={`${a.title} (${a.outcomeKey})`}
                    >
                      {a.title}
                      {a.url !== '#' && <ExternalLink className="ml-1 inline h-3 w-3 opacity-40" />}
                    </a>
                    {a.dataMode === 'fixture' && (
                      <span className="shrink-0 text-[10px] text-zinc-600">sim</span>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums text-emerald-300">{cents(a.bid)}</TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums text-rose-300">{cents(a.ask)}</TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums text-zinc-400">
                  {a.halfSpread !== null ? `${(a.halfSpread * 100).toFixed(2)}¢` : '—'}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums text-zinc-500">
                  {a.optimalHalfSpread !== null ? `${(a.optimalHalfSpread * 100).toFixed(2)}¢` : '—'}
                </TableCell>
                <TableCell className="text-right font-mono text-[11px] tabular-nums text-zinc-500">
                  {cents(a.bookTop.bid)} / {cents(a.bookTop.ask)}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-2">
                    <span className={`font-mono text-xs tabular-nums ${a.inventory > 0 ? 'text-amber-300' : a.inventory < 0 ? 'text-sky-300' : 'text-zinc-500'}`}>
                      {a.inventory > 0 ? '+' : ''}{a.inventory}
                    </span>
                    <div className="h-1.5 w-10 overflow-hidden rounded-full bg-zinc-800">
                      <div
                        className={`h-full rounded-full ${invPct > 0.7 ? 'bg-rose-500' : invPct > 0.4 ? 'bg-amber-500' : 'bg-emerald-600'}`}
                        style={{ width: `${invPct * 100}%` }}
                      />
                    </div>
                  </div>
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums text-zinc-400">{a.fills}</TableCell>
                <TableCell className={`text-right font-mono text-xs tabular-nums ${net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {usd(net)}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums text-zinc-300">
                  {a.expHourlyEarnings > 0 ? usd(a.expHourlyEarnings) : '—'}
                </TableCell>
                <TableCell className="text-center">
                  {dead ? (
                    <Badge variant="outline" className="border-rose-900 bg-rose-950/70 text-[10px] text-rose-400" title={a.killReason}>
                      killed
                    </Badge>
                  ) : (
                    <Switch
                      checked={a.enabled}
                      onCheckedChange={(v) => onToggle(a.marketId, v)}
                      aria-label={`Toggle market making on ${a.title}`}
                      className="data-[state=checked]:bg-emerald-600"
                    />
                  )}
                </TableCell>
              </TableRow>
            );
          })}
          {!agents.length && (
            <TableRow>
              <TableCell colSpan={11} className="py-8 text-center text-sm text-zinc-500">
                fetching live order books…
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}
