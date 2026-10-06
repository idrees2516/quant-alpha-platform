"""Report generator: markdown + JSON artifacts into download/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .core.config import Config
from .core.logging_setup import get_logger

log = get_logger("qa.report")


def write_reports(cfg: Config, res) -> tuple:
    """Returns (md_path, json_path)."""
    stamp = res.run_id
    md_path = cfg.download_dir / f"quant_alpha_report_{stamp}.md"
    json_path = cfg.download_dir / f"quant_alpha_report_{stamp}.json"
    payload = _json_payload(res)
    json_path.write_text(json.dumps(payload, indent=1, default=str))
    md_path.write_text(_markdown(res, payload))
    log.info("reports written: %s, %s", md_path.name, json_path.name)
    return md_path, json_path


def _json_payload(res) -> dict:
    arb = res.arb_result
    return {
        "run_id": res.run_id,
        "venue_status": res.venue_status,
        "counts": {"markets": res.n_markets, "sports": res.n_sports,
                   "news": res.n_news, "clusters": res.n_clusters,
                   "mm_plans": len(res.mm_plans)},
        "aggregated_markets": [{
            "cluster": a.cluster.cluster_id, "title": a.title,
            "category": a.cluster.category,
            "venues": [m.venue for m in a.cluster.markets],
            "outcomes": [o.label for o in a.cluster.outcomes],
            "consensus": [round(c, 4) for c in a.consensus],
            "best_bid_ask": [{"outcome": c.label,
                              "best_bid": round(c.best_bid, 4) if c.best_bid else None,
                              "best_ask": round(c.best_ask, 4) if c.best_ask else None}
                             for c in a.composites],
            "news_pressure": a.news_pressure,
        } for a in res.aggs],
        "arbitrage": {
            "actionable": [_arb_dict(o) for o in (arb.actionable if arb else [])],
            "candidates": [_arb_dict(o) for o in (arb.candidates if arb else [])][:10],
            "value_signals": [_arb_dict(o) for o in (arb.value_signals if arb else [])][:12],
            "execution_plans": res.arb_plans,
        },
        "market_making_plans": [{
            "venue": p.venue, "market": p.market_id, "outcome": p.outcome_key,
            "bid": p.bid, "ask": p.ask, "size": p.size, "strategy": p.strategy,
            "fair": p.fair,
            "expected_usd_per_hour": round(p.expected_hourly_earnings, 4),
            "components": {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in p.components.items()},
        } for p in res.mm_plans],
        "market_making_backtests": res.backtest.table() if res.backtest else [],
        "best_strategy_by_market": res.backtest.best_by_market if res.backtest else {},
        "calibration": [r.to_dict() for r in res.calibration],
        "hypothesis_tests": [t.to_dict() for t in res.tests],
        "beta_exposure": ({"strategy": res.beta_report.strategy,
                           "betas": res.beta_report.betas,
                           "t_stats": res.beta_report.t_stats,
                           "r_squared": res.beta_report.r_squared}
                          if res.beta_report else None),
    }


def _arb_dict(o) -> dict:
    return {"kind": o.kind, "title": o.title, "edge": round(o.edge, 4),
            "net_cost": round(o.net_cost, 4), "payout": round(o.guaranteed_payout, 4),
            "risk_free": o.risk_free, "confidence": o.confidence,
            "horizon_days": round(o.horizon_days, 1) if o.horizon_days else None,
            "notes": o.notes,
            "legs": [{"venue": l.venue, "market": l.market_id,
                      "outcome": l.outcome_key, "side": l.side,
                      "price": round(l.raw_price, 4),
                      "eff_price": round(l.eff_price, 4)} for l in o.legs]}


def _markdown(res, payload) -> str:
    lines: List[str] = []
    a = lines.append
    a(f"# Quant Alpha Platform — Run {res.run_id}")
    a("")
    a("## Venues")
    a("")
    a("| venue | mode | markets | sports | news | status |")
    a("|---|---|---|---|---|---|")
    for v in res.venue_status:
        a(f"| {v['venue']} | {v['mode']} | {v['markets']} | {v['sports']} | "
          f"{v['news']} | {v['error'] or 'ok'} |")
    a("")
    a(f"**Totals**: {res.n_markets} markets, {res.n_sports} sports books, "
      f"{res.n_news} news items, aggregated into {res.n_clusters} matched events.")
    a("")
    a("## Aggregated markets (consensus)")
    a("")
    a("| event | venues | consensus | best bid/ask |")
    a("|---|---|---|---|")
    for m in payload["aggregated_markets"][:15]:
        cons = "/".join(f"{c:.3f}" for c in m["consensus"])
        ba = "; ".join(f"{b['outcome']}: {b['best_bid']}/{b['best_ask']}"
                       for b in m["best_bid_ask"])
        a(f"| {m['title'][:48]} | {','.join(m['venues'])} | {cons} | {ba} |")
    a("")
    a("## Arbitrage")
    a("")
    arb = payload["arbitrage"]
    a(f"Actionable: **{len(arb['actionable'])}** — candidates (pending "
      f"confirmation): {len(arb['candidates'])} — value signals: "
      f"{len(arb['value_signals'])}")
    a("")
    if arb["actionable"]:
        a("| kind | event | edge | net cost | payout | legs |")
        a("|---|---|---|---|---|---|")
        for o in arb["actionable"][:10]:
            legs = " + ".join(f"{l['venue']} {l['outcome']}@{l['price']:.3f}"
                              for l in o["legs"])
            a(f"| {o['kind']} | {o['title'][:38]} | {o['edge']:.2%} | "
              f"{o['net_cost']:.3f} | {o['payout']:.3f} | {legs} |")
        a("")
        a("### Execution plans (top)")
        for p in arb["execution_plans"][:5]:
            a(f"- **{p['title'][:50]}** edge {p['edge']:.2%}, "
              f"capital ${p['sizing']['capital_usd']}, "
              f"expected profit ${p['sizing']['expected_profit_usd']} — "
              f"{len(p['legs'])} legs: " +
              ", ".join(f"{l['venue']} {l['outcome']} {l['side']}@{l['limit_price']}"
                        for l in p["legs"]))
        a("")
    a("## Market making — fee earning plan")
    a("")
    if payload["market_making_plans"]:
        a("| venue | market/outcome | strategy | bid | ask | fair | E[USD/h] | components |")
        a("|---|---|---|---|---|---|---|---|")
        for p in payload["market_making_plans"]:
            comp = p["components"]
            a(f"| {p['venue']} | {p['outcome']} | {p['strategy']} | {p['bid']} | "
              f"{p['ask']} | {p['fair']} | {p['expected_usd_per_hour']} | "
              f"rewards {comp.get('rewards_usd_h', 0)}/h, capture "
              f"{comp.get('spread_capture_usd_h', 0)}/h, adverse "
              f"{comp.get('adverse_cost_usd_h', 0)}/h, A={comp.get('A')}, "
              f"k={comp.get('k')} |")
        a("")
        a("Expected earnings decomposition per market is fully net: liquidity "
          "rewards + spread capture − adverse selection − fees.")
    a("")
    a("## Market making backtest (strategy comparison)")
    a("")
    if payload["market_making_backtests"]:
        a("| market | venue | strategy | PnL | rewards | capture | adverse | "
          "max DD | Sharpe | fills |")
        a("|---|---|---|---|---|---|---|---|---|---|")
        for r in payload["market_making_backtests"]:
            a(f"| {r['market'][:34]} | {r['venue']} | {r['strategy']} | "
              f"{r['pnl_usd']:.0f} | {r['rewards_usd']:.1f} | "
              f"{r['spread_capture_usd']:.0f} | {r['adverse_cost_usd']:.0f} | "
              f"{r['max_drawdown_usd']:.0f} | {r['sharpe']} | {r['total_fills']} |")
        a("")
        a(f"Best strategy per market: " + ", ".join(
            f"{k[:28]}→{v}" for k, v in list(payload["best_strategy_by_market"].items())[:8]))
    a("")
    a("## Calibration (probability sources)")
    a("")
    if payload["calibration"]:
        a("| source | n | Brier | log loss | consensus weight |")
        a("|---|---|---|---|---|")
        for c in payload["calibration"]:
            a(f"| {c['source']} | {c['n']} | {c['brier']} | {c['log_loss']} | "
              f"{c['consensus_weight']} |")
    a("")
    a("## Hypothesis tests")
    a("")
    for t in payload["hypothesis_tests"]:
        a(f"- **{t['hypothesis']}** — {t['verdict']} "
          f"(stat {t['statistic']}, p={t['p_value']}). {t['detail']}")
    a("")
    if payload["beta_exposure"]:
        b = payload["beta_exposure"]
        a("## Beta exposure")
        a("")
        a(f"Strategy `{b['strategy']}`: " + ", ".join(
            f"{k}={v}" for k, v in b["betas"].items()) +
          f" — R²={b['r_squared']}")
        a("")
    a("_Offline-mode note: where venues were unreachable, deterministic "
      "fixtures were used; resolutions in model evaluation are simulated "
      "(seeded) and labeled as such. Live runs consume the same machinery "
      "against real snapshots._")
    return "\n".join(lines) + "\n"
