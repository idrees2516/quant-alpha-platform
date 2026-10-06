# Quant Alpha Platform — Run 20261006T102357Z

## Venues

| venue | mode | markets | sports | news | status |
|---|---|---|---|---|---|
| polymarket | live | 80 | 0 | 0 | ok |
| kalshi | fixtures | 5 | 0 | 0 | ok |
| predictit | fixtures | 3 | 0 | 0 | ok |
| manifold | live | 73 | 0 | 0 | ok |
| metaculus | fixtures | 3 | 0 | 0 | ok |
| theoddsapi | fixtures | 0 | 2 | 0 | ok |
| espn | live | 0 | 0 | 21 | ok |
| gnews | live | 0 | 0 | 40 | ok |
| newsapi | fixtures | 0 | 0 | 5 | ok |

**Totals**: 164 markets, 2 sports books, 66 news items, aggregated into 65 matched events.

## Aggregated markets (consensus)

| event | venues | consensus | best bid/ask |
|---|---|---|---|
| LoL: HANJIN BRION vs Natus Vincere (BO3) - Demac | polymarket,polymarket,polymarket,polymarket,polymarket | 0.500/0.500 | YES: None/None; NO: None/None |
| FED rate cut by July 2026 | kalshi,predictit | 0.610/0.390 | YES: 0.605/0.614; NO: 0.38/0.385 |
| US Pres 2028 winner | kalshi,predictit | 0.295/0.155/0.111/0.438 | VANCE: 0.3/0.315; NEWSOM: 0.16/0.17; OBAMA: 0.105/0.125; OTHER: 0.442/0.462 |
| Will the Fed decrease interest rates by 25 bps a | polymarket,polymarket,polymarket,polymarket,polymarket | 0.004/0.997 | YES: None/None; NO: None/None |
| Bitcoin Up or Down on October 5? | polymarket | 0.500/0.500 | YES: None/None; NO: None/None |
| Will Luiz Inácio Lula da Silva win the 2026 Braz | polymarket | 0.165/0.835 | YES: None/None; NO: None/None |
| Shutdown ends before Nov | kalshi | 0.613/0.387 | YES: 0.615/0.635; NO: 0.368/0.388 |
| Will Flávio Bolsonaro win the 2026 Brazilian pre | polymarket | 0.839/0.162 | YES: None/None; NO: None/None |
| Lakers win vs Celtics Oct 6 | kalshi,predictit | 0.458/0.542 | YES: 0.46/0.475; NO: 0.535/0.55 |
| BTC above 150k on Dec 31 2026 | kalshi | 0.349/0.651 | YES: 0.345/0.365; NO: 0.635/0.655 |
| LoL: Team Vitality vs RED Canids - Game 1 Winner | polymarket,polymarket | 0.500/0.500 | YES: None/None; NO: None/None |
| China Open: Karolina Muchova vs Naomi Osaka | polymarket | 0.500/0.500 | YES: None/None; NO: None/None |
| Japan Open Tennis Championships: Carlos Alcaraz  | polymarket | 0.500/0.500 | YES: None/None; NO: None/None |
| Will Spain win on 2026-10-06? | polymarket,polymarket,polymarket | 0.285/0.715 | YES: None/None; NO: None/None |
| 2026 Balance of Power: R Senate, R House | polymarket,polymarket | 0.009/0.991 | YES: None/None; NO: None/None |

## Arbitrage

Actionable: **1** — candidates (pending confirmation): 0 — value signals: 0

| kind | event | edge | net cost | payout | legs |
|---|---|---|---|---|---|
| sports_dutch | Lakers win vs Celtics Oct 6 | 3.73% | 0.964 | 1.000 | sports:BookA Lakers@0.476 + sports:BookB Celtics@0.488 |

### Execution plans (top)
- **Lakers win vs Celtics Oct 6** edge 3.73%, capital $24.1, expected profit $0.9 — 2 legs: sports:BookA Lakers buy@0.4762, sports:BookB Celtics buy@0.4878

## Market making — fee earning plan

| venue | market/outcome | strategy | bid | ask | fair | E[USD/h] | components |
|---|---|---|---|---|---|---|---|
| kalshi | OTHER | glft | 0.39 | 0.5 | 0.4464 | 4.8211 | rewards 0.0/h, capture 5.1902/h, adverse 0.3691/h, A=8.0, k=26.3 |
| predictit | Vance | avellaneda_stoikov | 0.29 | 0.31 | 0.3042 | 1.8571 | rewards 0.0/h, capture 3.034/h, adverse 1.1769/h, A=8.0, k=27.6 |
| predictit | Newsom | avellaneda_stoikov | 0.15 | 0.17 | 0.164 | 1.8375 | rewards 0.0/h, capture 2.9681/h, adverse 1.1306/h, A=8.0, k=29.8 |
| kalshi | NO | glft | 0.33 | 0.43 | 0.3811 | 4.1766 | rewards 0.0/h, capture 4.5343/h, adverse 0.3577/h, A=8.0, k=29.7 |
| kalshi | YES | glft | 0.41000000000000003 | 0.52 | 0.465 | 3.5755 | rewards 0.0/h, capture 3.8495/h, adverse 0.274/h, A=8.0, k=31.7 |
| kalshi | NO | glft | 0.49 | 0.59 | 0.541 | 3.7361 | rewards 0.0/h, capture 4.0538/h, adverse 0.3177/h, A=8.0, k=31.9 |
| predictit | No | avellaneda_stoikov | 0.38 | 0.4 | 0.3901 | 1.7426 | rewards 0.0/h, capture 2.8662/h, adverse 1.1236/h, A=8.0, k=33.3 |
| kalshi | NO | glft | 0.6 | 0.7000000000000001 | 0.6474 | 3.419 | rewards 0.0/h, capture 3.7089/h, adverse 0.2899/h, A=8.0, k=33.7 |
| kalshi | YES | glft | 0.3 | 0.4 | 0.3526 | 3.1832 | rewards 0.0/h, capture 3.4531/h, adverse 0.2699/h, A=8.0, k=35.1 |
| kalshi | YES | glft | 0.56 | 0.65 | 0.6063 | 3.2069 | rewards 0.0/h, capture 3.5122/h, adverse 0.3053/h, A=8.0, k=36.3 |
| kalshi | VANCE | glft | 0.25 | 0.35000000000000003 | 0.3012 | 2.954 | rewards 0.0/h, capture 3.2034/h, adverse 0.2494/h, A=8.0, k=36.6 |
| kalshi | YES | glft | 0.5700000000000001 | 0.67 | 0.6203 | 2.9511 | rewards 0.0/h, capture 3.2035/h, adverse 0.2524/h, A=8.0, k=36.6 |

Expected earnings decomposition per market is fully net: liquidity rewards + spread capture − adverse selection − fees.

## Market making backtest (strategy comparison)

| market | venue | strategy | PnL | rewards | capture | adverse | max DD | Sharpe | fills |
|---|---|---|---|---|---|---|---|---|---|
| FED rate cut by July 2026 | kalshi | avellaneda_stoikov | 297 | 0.0 | 185 | 129 | 1 | 226.02 | 1321 |
| FED rate cut by July 2026 | kalshi | glft | 45 | 0.0 | 39 | 6 | 0 | 34.08 | 58 |
| FED rate cut by July 2026 | kalshi | adaptive_plain | 304 | 0.0 | 172 | 135 | 1 | 193.79 | 1240 |
| Shutdown ends before Nov | kalshi | avellaneda_stoikov | 208 | 0.0 | 111 | 116 | 1 | 147.79 | 969 |
| Shutdown ends before Nov | kalshi | glft | 30 | 0.0 | 26 | 6 | 0 | 31.28 | 46 |
| Shutdown ends before Nov | kalshi | adaptive_plain | 252 | 0.0 | 96 | 120 | 1 | 147.51 | 913 |
| Fed rate cut by July? | predictit | avellaneda_stoikov | 215 | 0.0 | 118 | 115 | 0 | 160.2 | 997 |
| Fed rate cut by July? | predictit | glft | 48 | 0.0 | 40 | 7 | 0 | 37.92 | 62 |
| Fed rate cut by July? | predictit | adaptive_plain | 225 | 0.0 | 100 | 119 | 1 | 141.01 | 922 |
| BTC above 150k on Dec 31 2026 | kalshi | avellaneda_stoikov | 195 | 0.0 | 116 | 82 | 1 | 136.78 | 842 |
| BTC above 150k on Dec 31 2026 | kalshi | glft | 30 | 0.0 | 26 | 4 | 0 | 29.78 | 40 |
| BTC above 150k on Dec 31 2026 | kalshi | adaptive_plain | 202 | 0.0 | 111 | 91 | 1 | 150.21 | 862 |

Best strategy per market: FED rate cut by July 2026→adaptive_plain, Shutdown ends before Nov→adaptive_plain, Fed rate cut by July?→adaptive_plain, BTC above 150k on Dec 31 202→adaptive_plain

## Calibration (probability sources)

| source | n | Brier | log loss | consensus weight |
|---|---|---|---|---|
| metaculus | 3 | 0.1362 | 0.4606 | 5.369 |
| kalshi | 4 | 0.1761 | 0.5419 | 4.423 |

## Hypothesis tests

- **H2 consensus** — INCONCLUSIVE (stat -0.007, p=0.0747). consensus brier=0.1437 best source metaculus=0.1362; bootstrap CI=[-0.0324,0.0075]
- **H1 lead-lag** — REJECTED (stat -0.238, p=0.4398). corr(lag0)=0.166 corr(lag+1)=-0.072 lead=polymarket
- **H3 mm pnl (avellaneda_stoikov/kalshi)** — SUPPORTED (stat 37.256, p=0.0). mean/h=1.322 t=37.26 CI=[1.254,1.392]
- **H3 mm pnl (glft/kalshi)** — SUPPORTED (stat 5.617, p=0.0). mean/h=0.188 t=5.62 CI=[0.124,0.258]
- **H3 mm pnl (adaptive_plain/kalshi)** — SUPPORTED (stat 31.943, p=0.0). mean/h=1.272 t=31.94 CI=[1.194,1.347]
- **H3 mm pnl (avellaneda_stoikov/kalshi)** — SUPPORTED (stat 24.361, p=0.0). mean/h=0.909 t=24.36 CI=[0.836,0.981]
- **H3 mm pnl (glft/kalshi)** — SUPPORTED (stat 5.156, p=0.0). mean/h=0.128 t=5.16 CI=[0.083,0.178]
- **H3 mm pnl (adaptive_plain/kalshi)** — SUPPORTED (stat 24.314, p=0.0). mean/h=0.881 t=24.31 CI=[0.810,0.954]
- **H3 mm pnl (avellaneda_stoikov/predictit)** — SUPPORTED (stat 26.405, p=0.0). mean/h=0.965 t=26.41 CI=[0.893,1.036]
- **H3 mm pnl (glft/predictit)** — SUPPORTED (stat 6.251, p=0.0). mean/h=0.202 t=6.25 CI=[0.143,0.269]
- **H3 mm pnl (adaptive_plain/predictit)** — SUPPORTED (stat 23.243, p=0.0). mean/h=0.942 t=23.24 CI=[0.864,1.021]
- **H3 mm pnl (avellaneda_stoikov/kalshi)** — SUPPORTED (stat 22.545, p=0.0). mean/h=0.811 t=22.55 CI=[0.737,0.881]
- **H3 mm pnl (glft/kalshi)** — SUPPORTED (stat 4.909, p=0.0). mean/h=0.127 t=4.91 CI=[0.081,0.182]
- **H3 mm pnl (adaptive_plain/kalshi)** — SUPPORTED (stat 24.76, p=0.0). mean/h=0.846 t=24.76 CI=[0.781,0.912]

## Beta exposure

Strategy `adaptive_plain`: intercept=1.4026, consensus_move=21.7933, dispersion_move=-6.5361, news_volume=-0.3769 — R²=0.072

_Offline-mode note: where venues were unreachable, deterministic fixtures were used; resolutions in model evaluation are simulated (seeded) and labeled as such. Live runs consume the same machinery against real snapshots._
