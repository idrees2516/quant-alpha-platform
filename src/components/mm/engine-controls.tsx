'use client';

import { useState } from 'react';
import { AlertTriangle, Cpu, Pause, Play, RotateCcw, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import type { EngineParams, EngineStatus } from '@/lib/mm/types';

const STRATEGIES: Array<{ id: EngineParams['strategy']; label: string; blurb: string }> = [
  { id: 'optimizer', label: 'Earnings Optimizer', blurb: 'golden-section fee-max spread, re-solved every tick' },
  { id: 'adaptive', label: 'Adaptive Spread', blurb: 'vol floor + earnings-optimal cap + inventory skew' },
  { id: 'as', label: 'Avellaneda-Stoikov', blurb: 'logit-space reservation price + optimal spread' },
];

export function EngineControls({
  status,
  onAction,
  onParams,
  busy,
}: {
  status: EngineStatus;
  onAction: (action: 'start' | 'pause' | 'reset' | 'clearKill') => void;
  onParams: (patch: Partial<EngineParams>) => void;
  busy: boolean;
}) {
  const [local, setLocal] = useState(status.params);
  const p = { ...status.params, ...local };

  const upd = (patch: Partial<EngineParams>) => {
    setLocal((prev) => ({ ...prev, ...patch }));
    onParams(patch);
  };

  return (
    <Card className="border-zinc-800 bg-zinc-900/60 backdrop-blur">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
          <SlidersHorizontal className="h-4 w-4 text-emerald-400" />
          Engine controls
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {status.running ? (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => onAction('pause')}
              className="border-zinc-700 bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
            >
              <Pause className="h-3.5 w-3.5" /> Pause
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={busy}
              onClick={() => onAction('start')}
              className="bg-emerald-600 text-white hover:bg-emerald-500"
            >
              <Play className="h-3.5 w-3.5" /> Start
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => onAction('reset')}
            className="border-zinc-700 bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Reset
          </Button>
          {status.globalKill && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => onAction('clearKill')}
              className="border-rose-800 bg-rose-950/60 text-rose-300 hover:bg-rose-900/60"
            >
              <AlertTriangle className="h-3.5 w-3.5" /> Clear kill switch
            </Button>
          )}
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs text-zinc-400">Strategy</Label>
          <Select value={p.strategy} onValueChange={(v) => upd({ strategy: v as EngineParams['strategy'] })}>
            <SelectTrigger className="border-zinc-700 bg-zinc-900 text-zinc-200">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="border-zinc-700 bg-zinc-900 text-zinc-200">
              {STRATEGIES.map((s) => (
                <SelectItem key={s.id} value={s.id} className="focus:bg-zinc-800">
                  <span className="flex flex-col">
                    <span>{s.label}</span>
                    <span className="text-[10px] text-zinc-500">{s.blurb}</span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <Label className="text-zinc-400">Risk aversion γ</Label>
            <span className="font-mono tabular-nums text-zinc-300">{p.gamma.toFixed(2)}</span>
          </div>
          <Slider
            value={[p.gamma]}
            min={0.1}
            max={3}
            step={0.05}
            onValueCommit={(v) => upd({ gamma: v[0] })}
            aria-label="Risk aversion gamma"
          />
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <Label className="text-zinc-400">Quote size (contracts)</Label>
            <span className="font-mono tabular-nums text-zinc-300">{p.quoteSize}</span>
          </div>
          <Slider
            value={[p.quoteSize]}
            min={5}
            max={100}
            step={5}
            onValueCommit={(v) => upd({ quoteSize: Math.round(v[0]) })}
            aria-label="Quote size"
          />
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <Label className="text-zinc-400">Max inventory (contracts)</Label>
            <span className="font-mono tabular-nums text-zinc-300">{p.maxInventory}</span>
          </div>
          <Slider
            value={[p.maxInventory]}
            min={30}
            max={300}
            step={10}
            onValueCommit={(v) => upd({ maxInventory: Math.round(v[0]) })}
            aria-label="Max inventory"
          />
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <Label className="text-zinc-400">Adverse-selection fraction</Label>
            <span className="font-mono tabular-nums text-zinc-300">{(p.adverseFrac * 100).toFixed(0)}%</span>
          </div>
          <Slider
            value={[p.adverseFrac]}
            min={0.05}
            max={0.6}
            step={0.05}
            onValueCommit={(v) => upd({ adverseFrac: v[0] })}
            aria-label="Adverse selection fraction"
          />
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs">
            <Label className="text-zinc-400">Clock speed (sim-hours / real sec)</Label>
            <span className="font-mono tabular-nums text-zinc-300">{p.speed.toFixed(2)}×</span>
          </div>
          <Slider
            value={[p.speed]}
            min={0.02}
            max={0.5}
            step={0.02}
            onValueCommit={(v) => upd({ speed: v[0] })}
            aria-label="Clock speed"
          />
          <p className="text-[10px] leading-relaxed text-zinc-600">
            <Cpu className="mr-1 inline h-3 w-3" />
            Fill arrivals, reward accrual and inventory risk all run on the simulated clock; fills, books and
            adverse selection are modeled exactly like the offline backtester.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
