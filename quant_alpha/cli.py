"""CLI: python -m quant_alpha <command> [options]

Commands:
  pull       pull all venues (live-first, fixtures fallback) and persist
  aggregate  normalize + match + aggregate into composite books
  scan       arbitrage scan over aggregated state (with confirmations)
  plan       market-making fee-earning plan (quotes + allocation)
  backtest   strategy comparison backtest (AS vs GLFT vs adaptive)
  models     calibration + hypothesis tests + beta exposure
  report     write markdown + JSON report to download/
  run-all    full pipeline, end to end
  demo       offline deterministic end-to-end run (fixtures)
  venues     show venue connectivity status
"""
from __future__ import annotations

import argparse
import sys

from .core.config import Config
from .core.logging_setup import setup_logging
from .pipeline import run_pipeline
from .report import write_reports


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quant_alpha")
    parser.add_argument("command", choices=[
        "pull", "aggregate", "scan", "plan", "backtest", "models", "report",
        "run-all", "demo", "venues"])
    parser.add_argument("--offline", action="store_true",
                        help="force fixtures for all venues")
    parser.add_argument("--no-fixtures", action="store_true",
                        help="disable fixture fallback (live or fail)")
    parser.add_argument("--steps", type=int, default=240,
                        help="backtest length in hourly steps")
    parser.add_argument("--risk-budget", type=float, default=500.0,
                        help="total MM inventory risk budget (USD)")
    parser.add_argument("--bankroll", type=float, default=10_000.0,
                        help="arb sizing bankroll (USD)")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--log-json", action="store_true")
    args = parser.parse_args(argv)

    cfg = Config.load(overrides={
        "offline": args.offline or args.command == "demo",
        "fixtures_fallback": not args.no_fixtures,
        "log_level": args.log_level,
        "log_json": args.log_json,
    })
    setup_logging(cfg.log_level, cfg.log_json)

    if args.command == "venues":
        from .connectors import build_connectors
        for name, conn in build_connectors(cfg).items():
            state = "off" if not conn.vcfg.enabled else (
                "fixture-only" if cfg.offline else "live+fallback" if
                cfg.fixtures_fallback else "live-only")
            print(f"{name:<14} {state}")
        return 0

    stage_map = {
        "pull": ["pull"],
        "aggregate": ["pull", "aggregate"],
        "scan": ["pull", "aggregate", "arb"],
        "plan": ["pull", "aggregate", "mm"],
        "backtest": ["pull", "backtest"],
        "models": ["pull", "aggregate", "arb", "backtest", "models"],
        "report": ["pull", "aggregate", "arb", "mm", "backtest", "models"],
        "run-all": ["pull", "aggregate", "arb", "mm", "backtest", "models"],
        "demo": ["pull", "aggregate", "arb", "mm", "backtest", "models"],
    }
    res = run_pipeline(cfg, stages=stage_map[args.command],
                       backtest_steps=args.steps,
                       risk_budget=args.risk_budget, bankroll=args.bankroll)

    if args.command in ("report", "run-all", "demo", "models"):
        md, js = write_reports(cfg, res)
        print(f"\nreport: {md}")
        print(f"report: {js}")

    if args.command in ("scan", "run-all", "demo") and res.arb_result:
        print(f"\narb actionable: {len(res.arb_result.actionable)}, "
              f"candidates: {len(res.arb_result.candidates)}, "
              f"value signals: {len(res.arb_result.value_signals)}")
    if args.command in ("plan", "run-all", "demo") and res.mm_plans:
        total = sum(p.expected_hourly_earnings for p in res.mm_plans)
        print(f"\nmm plan: {len(res.mm_plans)} markets, "
              f"expected earnings {total:.2f} USD/hour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
