import { NextRequest, NextResponse } from 'next/server';
import { getEngine } from '@/lib/mm/engine';
import type { EngineParams } from '@/lib/mm/types';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

interface ControlBody {
  action?: 'start' | 'pause' | 'reset' | 'setParams' | 'toggleMarket' | 'clearKill';
  params?: Partial<EngineParams>;
  marketId?: string;
  enabled?: boolean;
}

/** Control the live engine: start/pause/reset, tune params, toggle markets. */
export async function POST(req: NextRequest): Promise<NextResponse<{ ok: boolean; error?: string }>> {
  let body: ControlBody;
  try {
    body = (await req.json()) as ControlBody;
  } catch {
    return NextResponse.json({ ok: false, error: 'invalid JSON body' }, { status: 400 });
  }
  const engine = getEngine();
  try {
    switch (body.action) {
      case 'start':
        engine.start();
        break;
      case 'pause':
        engine.pause();
        break;
      case 'reset':
        await engine.reset();
        break;
      case 'setParams':
        if (body.params) engine.setParams(body.params);
        break;
      case 'toggleMarket':
        if (body.marketId) engine.toggleMarket(body.marketId, body.enabled !== false);
        break;
      case 'clearKill':
        engine.clearKill();
        break;
      default:
        return NextResponse.json({ ok: false, error: 'unknown action' }, { status: 400 });
    }
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: err instanceof Error ? err.message : 'engine error' },
      { status: 500 }
    );
  }
}
