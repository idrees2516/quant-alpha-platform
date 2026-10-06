# Quant Alpha Platform

Production-grade, multi-venue alpha, aggregation, arbitrage and **market-making
fee-earning** platform for prediction markets, sports books and news feeds.

Live venues (no key required): **Polymarket** (gamma + CLOB order books),
**Kalshi** (trade-api v2 + order books), **Manifold** (forecasts),
**Google News RSS**. Key-gated (fixtures until keys are set):
**PredictIt**, **Metaculus** (Cloudflare-gated from datacenters),
**The Odds API** (sports odds), **NewsAPI**, **ESPN** scoreboard.
Any unreachable venue transparently falls back to deterministic fixtures so
every subsystem stays exercisable end-to-end.

## Quick start

```bash
pip install -r requirements.txt            # requests + numpy (optional acceleration)

python -m quant_alpha demo                 # offline deterministic end-to-end run
python -m quant_alpha run-all              # LIVE pull -> full pipeline + reports
python -m quant_alpha scan                 # arbitrage scan only
python -m quant_alpha plan                 # market-making fee-earning plan only
python -m quant_alpha backtest             # strategy comparison backtest
python -m quant_alpha models               # calibration + hypothesis tests
python -m unittest discover -s tests -v    # test suite (25 tests)
```

Optional environment variables: `THE_ODDS_API_KEY`, `NEWSAPI_KEY`.
Optional `config.json` (see `config.example.json`) for strategy/fee parameters.

Reports land in `download/` (markdown + JSON); state persists to
`var/quant.db` (SQLite WAL).

## Architecture

```
quant_alpha/
├── core/            config · structured logging · retrying HTTP · domain model
├── connectors/      Polymarket, Kalshi, PredictIt, Manifold, Metaculus,
│                    The Odds API, ESPN, Google News RSS, NewsAPI (+ fixtures)
├── normalize/       entity resolution across venues · composite fee-adjusted
│                    books · liquidity/calibration-weighted consensus
├── arbitrage/       venue fee models · complementary + Dutch-book + sports
│                    Dutch detectors · staleness confirmation · Kelly sizing ·
│                    execution plans
├── market_making/   THE DEEP PART — see below
├── alpha/           de-vig (multiplicative/Shin/power) · venue-vs-consensus
│                    deviation signals · news pressure
├── models/          Brier/log-loss/Platt calibration · Welch t-test ·
│                    bootstrap CIs · OLS beta regression · hypothesis engine
├── storage/         SQLite WAL persistence (snapshots, arbs, quotes, tests)
├── pipeline.py      orchestrator (pull→aggregate→arb→MM→backtest→models)
└── cli.py           all commands above
```

## Market making — fee-earning setup (the core)

Three quoting strategies, one shared risk layer, one honest simulator:

1. **Avellaneda–Stoikov (2008)**, adapted to bounded digital contracts:
   work in **logit space** so quotes can never leave [tick, 1−tick].
   Reservation price `x_r = x − q̂·γ·σ²·τ` (inventory-normalized skew, capped),
   total spread `γσ²τ + (2/γ)·ln(1 + γ/k_x)` with `k_x` calibrated from the
   live book. Quotes = `sigmoid(x_r ± Δ/2)`.
2. **GLFT — Guéant–Lehalle–Fernandez-Tapia (2013)**: the HJB control problem
   with exponential fill intensity solved **numerically** (stable relative
   value iteration with per-step probability normalization, ΔV economic
   clamp, policy-stability convergence). Per-side optimum is closed-form
   given the value function: `δ_b*(q) = 1/k − (V(q+s)−V(q))/s`. Widens the
   side that adds inventory risk, tightens the side that reduces it, at any
   inventory level — the paper's closed forms are the small-γ limit of this
   solution.
3. **Adaptive spread** (production style): micro-price blended with
   cross-venue consensus, vol-scaled spread with fee floor and news
   widening, linear inventory skew, quote hysteresis.

**Fee-earning engine** (`market_making/rewards.py`):
- Polymarket **liquidity rewards**: score `min(size, cap)·(1 − spread/max)²`,
  expected share measured against the live book score, pool per hour →
  USD/hour reward rate;
- Kalshi maker-free / taker-fee economics (0.07·P ceil to cent);
- PredictIt 10%-on-profits settlement accounting;
- full decomposition per market: `rewards + spread capture − adverse
  selection − fees`, and a golden-section **spread optimizer** maximizing
  expected hourly earnings;
- **capital allocation**: every candidate market gets an earnings-efficiency
  score (net USD/day per USD of inventory risk); a greedy risk-budget
  allocator (default $500) turns that into a concrete quoting plan with
  exact tick-rounded, fee-floored bid/ask, size and per-market risk.

**Fill simulator / backtester** (`market_making/simulator.py`): Poisson
arrivals with book-calibrated intensity; **Gaussian sweep** fill model (a
quote behind the touch only fills when the mid actually travels to it);
queue-join penalty; adverse selection via informed-flow fraction; no naked
shorting (venue-realistic); hourly rewards accrual; kill switch and
inventory caps; settlement at the final mid; full PnL attribution (spread
capture vs adverse selection vs rewards vs fees) and drawdown/Sharpe stats.

## Arbitrage engine

- **cross-venue complementary**: buy YES on venue A + NO on venue B when
  fee-adjusted cost < worst-case settlement payout (PredictIt profit fees
  included);
- **in-venue complementary**: same, one venue's book (only for genuine
  YES/NO pairs);
- **Dutch books**: buy every mutually-exclusive outcome across venues
  (multi-candidate elections) or across bookmakers (sports, decimal odds);
- **staleness confirmation**: opportunities must survive N consecutive scans
  before becoming actionable; quote-age checks;
- **Kelly sizing** with liquidity haircuts, funding cost for locked capital,
  and full execution plans (IOC-limit legs, net-of-fee prices).

## Hypothesis testing / models

- H1 sharp-venue lead-lag vs consensus (cross-correlation);
- H2 calibration-weighted consensus beats the best single source (paired
  bootstrap);
- H3 MM PnL > 0 after fees (Welch t-test + bootstrap CI, per strategy);
- H4 arb-edge decay with time-to-close (OLS);
- per-source **calibration** (Brier, log loss, reliability bins, Platt
  scaling) feeding consensus weights — a closed learning loop;
- **beta exposure**: strategy PnL regressed on market/dispersion/news
  factors with t-stats.

Offline mode evaluates on seeded simulated resolutions (labeled as such in
every report); live runs consume the same machinery on real snapshots.

## Production properties

Retry-with-backoff + jitter on every HTTP call; hard timeouts; per-venue
circuit to fixtures; structured JSON logging option; SQLite WAL with lock
retries; idempotent upserts; deterministic seeds everywhere; 25-test unit
suite; pure-additive package (no mutation outside `var/` and `download/`).

## Disclaimer

Research/education software. No financial advice. Exchanges' terms, local
regulation and real execution latency/slippage are your responsibility.
