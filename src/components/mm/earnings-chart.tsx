'use client';

import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ChartContainer, ChartTooltipContent, type ChartConfig } from '@/components/ui/chart';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import type { CurvePoint } from '@/lib/mm/types';

const chartConfig = {
  rewards: { label: 'Liquidity rewards', color: '#10b981' },
  spreadCapture: { label: 'Spread capture', color: '#f59e0b' },
  net: { label: 'Net earnings', color: '#34d399' },
  baselineNet: { label: 'Naive baseline', color: '#71717a' },
  equity: { label: 'Equity', color: '#a1a1aa' },
} satisfies ChartConfig;

function fmtUsd(v: number): string {
  return `$${v.toFixed(2)}`;
}

export function EarningsChart({ curve, allocatedCash }: { curve: CurvePoint[]; allocatedCash: number }) {
  const data = curve.map((p) => ({ ...p, gross: Math.round((p.rewards + p.spreadCapture) * 100) / 100 }));
  const empty = data.length < 2;

  return (
    <Tabs defaultValue="earnings" className="w-full">
      <TabsList className="bg-zinc-900 text-zinc-400">
        <TabsTrigger value="earnings" className="data-[state=active]:bg-zinc-800 data-[state=active]:text-zinc-100">
          Fee earnings
        </TabsTrigger>
        <TabsTrigger value="equity" className="data-[state=active]:bg-zinc-800 data-[state=active]:text-zinc-100">
          Bankroll equity
        </TabsTrigger>
      </TabsList>

      <TabsContent value="earnings" className="mt-3">
        {empty ? (
          <div className="flex h-[280px] items-center justify-center text-sm text-zinc-500">
            warming up the engine…
          </div>
        ) : (
          <ChartContainer config={chartConfig} className="h-[280px] w-full">
            <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="fillRewards" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10b981" stopOpacity={0.7} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0.08} />
                </linearGradient>
                <linearGradient id="fillCapture" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.6} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.05} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="simT"
                tick={{ fill: '#71717a', fontSize: 11 }}
                tickLine={false}
                axisLine={{ stroke: '#3f3f46' }}
                tickFormatter={(v: number) => `${v.toFixed(0)}h`}
                minTickGap={48}
              />
              <YAxis
                tick={{ fill: '#71717a', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `$${v.toFixed(0)}`}
                width={44}
              />
              <Tooltip
                cursor={{ stroke: '#52525b', strokeDasharray: '4 4' }}
                content={<ChartTooltipContent indicator="line" labelFormatter={(_, payload) => {
                  const p = payload?.[0]?.payload as CurvePoint | undefined;
                  return p ? `sim t = ${p.simT.toFixed(1)}h (${p.t}s real)` : '';
                }} />}
              />
              <Area dataKey="rewards" type="monotone" stackId="income" stroke="#10b981" strokeWidth={1.5} fill="url(#fillRewards)" />
              <Area dataKey="spreadCapture" type="monotone" stackId="income" stroke="#f59e0b" strokeWidth={1.5} fill="url(#fillCapture)" />
              <Line dataKey="baselineNet" type="monotone" stroke="#71717a" strokeWidth={1.5} strokeDasharray="5 4" dot={false} />
              <Line dataKey="net" type="monotone" stroke="#34d399" strokeWidth={2} dot={false} />
            </AreaChart>
          </ChartContainer>
        )}
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-emerald-500" />rewards (stacked)</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-amber-500" />spread capture (stacked)</span>
          <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-emerald-400" />net after adverse+fees</span>
          <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-zinc-500" />naive static baseline</span>
        </div>
      </TabsContent>

      <TabsContent value="equity" className="mt-3">
        {empty ? (
          <div className="flex h-[280px] items-center justify-center text-sm text-zinc-500">warming up…</div>
        ) : (
          <ChartContainer config={chartConfig} className="h-[280px] w-full">
            <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="simT"
                tick={{ fill: '#71717a', fontSize: 11 }}
                tickLine={false}
                axisLine={{ stroke: '#3f3f46' }}
                tickFormatter={(v: number) => `${v.toFixed(0)}h`}
                minTickGap={48}
              />
              <YAxis
                domain={[Math.min(...data.map((d) => d.equity)) * 0.998, Math.max(...data.map((d) => d.equity)) * 1.002]}
                tick={{ fill: '#71717a', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`}
                width={44}
              />
              <Tooltip
                cursor={{ stroke: '#52525b', strokeDasharray: '4 4' }}
                content={<ChartTooltipContent indicator="line" labelFormatter={(_, payload) => {
                  const p = payload?.[0]?.payload as CurvePoint | undefined;
                  return p ? `sim t = ${p.simT.toFixed(1)}h` : '';
                }} />}
              />
              <Line dataKey="equity" type="monotone" stroke="#a1a1aa" strokeWidth={2} dot={false} />
            </LineChart>
          </ChartContainer>
        )}
        <p className="mt-2 text-[11px] text-zinc-500">
          marked equity (cash + inventory at mid) vs allocated bankroll {fmtUsd(allocatedCash)} — drawdown triggers the kill switch at -30% per market.
        </p>
      </TabsContent>
    </Tabs>
  );
}
