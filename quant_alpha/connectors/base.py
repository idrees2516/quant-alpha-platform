"""Connector base: live-first with fixture fallback and per-venue status."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from ..core.config import Config
from ..core.domain import MarketQuote, NewsItem, SportsQuote
from ..core.logging_setup import get_logger

log = get_logger("qa.connectors")


@dataclass
class PullResult:
    venue: str
    mode: str                                   # 'live' | 'fixtures' | 'error' | 'off'
    markets: List[MarketQuote] = field(default_factory=list)
    sports: List[SportsQuote] = field(default_factory=list)
    news: List[NewsItem] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.mode in ("live", "fixtures")


class BaseConnector(ABC):
    venue: str = "base"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.vcfg = cfg.venues.get(self.venue)

    def pull(self) -> PullResult:
        """Live attempt -> graceful fixture fallback -> explicit error."""
        if self.vcfg and not self.vcfg.enabled:
            return PullResult(self.venue, "off")
        if not self.cfg.offline:
            try:
                res = self.fetch_live()
                res.mode = "live"
                if self.cfg.fixtures_fallback and not self._is_useful(res):
                    log.info("venue=%s live pull empty; falling back to fixtures", self.venue)
                    return self._fixtures()
                return res
            except Exception as exc:  # noqa: BLE001 - venue must never kill the pipeline
                log.warning("venue=%s live failed (%s); fallback=%s",
                            self.venue, exc, self.cfg.fixtures_fallback)
                if self.cfg.fixtures_fallback:
                    return self._fixtures()
                return PullResult(self.venue, "error", error=str(exc))
        return self._fixtures()

    def _is_useful(self, res: PullResult) -> bool:
        return bool(res.markets or res.sports or res.news)

    def _fixtures(self) -> PullResult:
        from . import fixtures
        try:
            return fixtures.pull_fixture(self.venue)
        except Exception as exc:  # noqa: BLE001
            return PullResult(self.venue, "error", error=f"fixtures: {exc}")

    @abstractmethod
    def fetch_live(self) -> PullResult:
        ...
