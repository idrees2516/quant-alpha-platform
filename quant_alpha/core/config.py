"""Configuration: environment + optional JSON file, with safe defaults.

Precedence: explicit CLI flags > environment variables > config.json > defaults.
Secrets are only read from environment variables (never written to disk).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VAR_DIR = PROJECT_ROOT / "var"
DOWNLOAD_DIR = PROJECT_ROOT / "download"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


@dataclass
class VenueConfig:
    """Per-venue switches and limits used by connectors and the arb engine."""
    name: str
    enabled: bool = True
    max_markets: int = 60          # how many markets to pull per scan
    request_timeout_s: float = 8.0
    max_retries: int = 3


@dataclass
class MarketMakingConfig:
    # Strategy defaults (overridable per market in the planner)
    strategy: str = "adaptive"          # 'as' | 'glft' | 'adaptive'
    gamma: float = 0.8                  # risk aversion (Avellaneda-Stoikov / GLFT)
    horizon_h: float = 24.0             # quoting horizon in hours
    max_inventory: int = 120            # hard inventory cap (contracts)
    quote_size: int = 25                # contracts per resting quote
    # Fill model calibration priors (MLE refines from the live book)
    intensity_A_prior: float = 1.6      # fills/hour at the touch
    intensity_k_prior: float = 90.0     # 1/price-units decay of fill intensity
    adverse_selection_frac: float = 0.35  # fraction of flow that is informed
    # Production guards
    max_quote_update_hz: float = 4.0
    hysteresis_ticks: float = 1.0
    kill_switch_drawdown: float = 250.0  # USD simulated drawdown stop


@dataclass
class RewardsConfig:
    """Fee/liquidity-reward earning models (Polymarket program approximation)."""
    polymarket_enabled: bool = True
    poly_daily_pool_usd: float = 500.0        # default when market has no live pool figure
    poly_max_spread: float = 0.03             # max rewarded spread from midpoint
    poly_size_cap: float = 300.0              # size beyond cap earns nothing
    poly_spread_power: float = 2.0            # (1 - spread/max)^power weight
    kalshi_maker_fee: float = 0.0             # makers currently trade free on Kalshi
    kalshi_taker_fee_rate: float = 0.07       # 0.07 * price * contracts, ceil to cent
    predictit_profit_fee: float = 0.10        # 10% on net winnings at settlement
    funding_rate_annual: float = 0.045        # cost of locked capital


@dataclass
class ArbConfig:
    min_edge_after_fees: float = 0.005   # 0.5% net edge to consider an arb
    staleness_confirmations: int = 2     # must survive N consecutive scans
    max_quote_age_s: float = 90.0
    slippage_buffer: float = 0.001
    kelly_fraction: float = 0.25         # fractional Kelly on residual risk
    max_legs: int = 6


@dataclass
class Config:
    data_dir: Path = VAR_DIR
    download_dir: Path = DOWNLOAD_DIR
    db_path: Path = VAR_DIR / "quant.db"
    fixtures_fallback: bool = True       # use fixtures when a venue is unreachable
    offline: bool = False                # force fixtures for everything
    seed: int = 20261006
    log_level: str = "INFO"
    log_json: bool = False
    api: Dict[str, str] = field(default_factory=dict)   # keys from env
    mm: MarketMakingConfig = field(default_factory=MarketMakingConfig)
    rewards: RewardsConfig = field(default_factory=RewardsConfig)
    arb: ArbConfig = field(default_factory=ArbConfig)
    venues: Dict[str, VenueConfig] = field(default_factory=lambda: {
        v.name: v for v in [
            VenueConfig("polymarket", max_markets=80),
            VenueConfig("kalshi", max_markets=80),
            VenueConfig("predictit", max_markets=80),
            VenueConfig("manifold", max_markets=80),
            VenueConfig("metaculus", max_markets=60),
            VenueConfig("theoddsapi", max_markets=40),
            VenueConfig("espn", max_markets=40),
            VenueConfig("gnews", max_markets=40),
            VenueConfig("newsapi", max_markets=40),
        ]
    })

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None, overrides: Optional[Dict[str, Any]] = None) -> "Config":
        cfg = cls()
        p = Path(path or DEFAULT_CONFIG_PATH)
        if p.exists():
            try:
                raw = json.loads(p.read_text())
                for key in ("offline", "seed", "log_level", "log_json", "fixtures_fallback"):
                    if key in raw:
                        setattr(cfg, key, raw[key])
                for sect, klass in (("mm", MarketMakingConfig), ("rewards", RewardsConfig), ("arb", ArbConfig)):
                    if sect in raw:
                        base = asdict(getattr(cfg, sect))
                        base.update({k: v for k, v in raw[sect].items() if k in base})
                        setattr(cfg, sect, klass(**base))
                for v in raw.get("venues", []):
                    if v.get("name") in cfg.venues:
                        for k, val in v.items():
                            if k != "name":
                                setattr(cfg.venues[v["name"]], k, val)
            except (json.JSONDecodeError, OSError):
                pass  # malformed config must never crash the platform
        # Secrets: environment only
        cfg.api = {
            "THE_ODDS_API_KEY": os.environ.get("THE_ODDS_API_KEY", ""),
            "NEWSAPI_KEY": os.environ.get("NEWSAPI_KEY", ""),
        }
        if overrides:
            for k, v in overrides.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.download_dir.mkdir(parents=True, exist_ok=True)
        return cfg
