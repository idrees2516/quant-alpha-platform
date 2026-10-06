import { NextResponse } from 'next/server';
import { getEngine } from '@/lib/mm/engine';
import type { EngineSnapshot } from '@/lib/mm/types';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

/** Live snapshot of the market-making fee engine (polled by the console). */
export async function GET(): Promise<NextResponse<EngineSnapshot>> {
  const engine = getEngine();
  return NextResponse.json(engine.snapshot(), {
    headers: { 'cache-control': 'no-store' },
  });
}
