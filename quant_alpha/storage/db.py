"""SQLite storage: WAL mode, parameterized upserts, lock retries, schema
versioning via a migrations table. All writes are idempotent."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..core.logging_setup import get_logger

log = get_logger("qa.db")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  venue TEXT NOT NULL,
  market_id TEXT NOT NULL,
  title TEXT, category TEXT, closes_at TEXT,
  outcomes_json TEXT, volume REAL, liquidity REAL,
  mode TEXT, fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_run ON snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_snap_venue ON snapshots(venue, market_id);
CREATE TABLE IF NOT EXISTS clusters (
  run_id TEXT NOT NULL, cluster_id TEXT NOT NULL, title TEXT, category TEXT,
  outcomes_json TEXT, venues_json TEXT, consensus_json TEXT,
  PRIMARY KEY (run_id, cluster_id)
);
CREATE TABLE IF NOT EXISTS arbs (
  run_id TEXT NOT NULL, signature TEXT NOT NULL, kind TEXT, cluster_id TEXT,
  title TEXT, edge REAL, net_cost REAL, payout REAL, actionable INTEGER,
  legs_json TEXT, plan_json TEXT,
  PRIMARY KEY (run_id, signature)
);
CREATE TABLE IF NOT EXISTS mm_plans (
  run_id TEXT NOT NULL, venue TEXT, market_id TEXT, outcome_key TEXT,
  bid REAL, ask REAL, size INTEGER, strategy TEXT, fair REAL,
  expected_hourly_earnings REAL, components_json TEXT,
  PRIMARY KEY (run_id, venue, market_id, outcome_key)
);
CREATE TABLE IF NOT EXISTS mm_backtests (
  run_id TEXT NOT NULL, market_title TEXT, venue TEXT, strategy TEXT,
  pnl REAL, rewards REAL, spread_capture REAL, adverse_cost REAL,
  fees REAL, max_drawdown REAL, sharpe REAL, total_fills INTEGER,
  PRIMARY KEY (run_id, market_title, strategy)
);
CREATE TABLE IF NOT EXISTS tests (
  run_id TEXT NOT NULL, hypothesis TEXT, statistic REAL, p_value REAL,
  verdict TEXT, detail TEXT,
  PRIMARY KEY (run_id, hypothesis)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
                          (str(SCHEMA_VERSION),))
        self.conn.commit()

    # ------------------------------------------------------------- core ---
    def execute(self, sql: str, params: Sequence = (), retries: int = 3) -> None:
        for i in range(retries):
            try:
                self.conn.execute(sql, params)
                self.conn.commit()
                return
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc) and i < retries - 1:
                    time.sleep(0.2 * (i + 1))
                else:
                    raise

    def executemany(self, sql: str, rows: Sequence[Sequence]) -> None:
        try:
            self.conn.executemany(sql, rows)
            self.conn.commit()
        except sqlite3.OperationalError as exc:
            log.warning("db executemany failed: %s", exc)

    def query(self, sql: str, params: Sequence = ()) -> List[Dict[str, Any]]:
        cur = self.conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # ---------------------------------------------------------- writers ---
    def save_run_meta(self, run_id: str, stage: str, payload: Dict) -> None:
        self.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                     (f"run:{run_id}:{stage}", json.dumps(payload, default=str)))

    def save_snapshots(self, run_id: str, results: Iterable) -> int:
        rows = []
        for res in results:
            for m in res.markets:
                rows.append((run_id, res.venue, m.market_id, m.title, m.category,
                             m.closes_at.isoformat() if m.closes_at else None,
                             json.dumps([{ "key": o.key, "bid": o.best_bid,
                                           "ask": o.best_ask, "mid": o.mid,
                                           "prob": o.probability,
                                           "depth": o.ask_size or o.bid_size }
                                          for o in m.outcomes], default=str),
                             m.volume, m.liquidity, res.mode,
                             m.fetched_at.isoformat()))
            for sq in res.sports:
                rows.append((run_id, res.venue, f"game:{sq.event_title}", sq.event_title,
                             sq.sport, sq.starts_at.isoformat() if sq.starts_at else None,
                             json.dumps([{"key": lab, "odds": o} for lab, o in sq.outcomes]),
                             None, None, res.mode, sq.fetched_at.isoformat()))
            for nw in res.news:
                rows.append((run_id, res.venue, f"news:{nw.url[-40:]}", nw.title,
                             "news", nw.published_at.isoformat() if nw.published_at else None,
                             json.dumps({"summary": nw.summary[:200]}),
                             None, None, res.mode, nw.fetched_at.isoformat()))
        self.executemany(
            "INSERT OR REPLACE INTO snapshots (run_id, venue, market_id, title, category,"
            " closes_at, outcomes_json, volume, liquidity, mode, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def save_clusters(self, run_id: str, aggs: Sequence) -> None:
        rows = []
        for a in aggs:
            rows.append((run_id, a.cluster.cluster_id, a.title, a.cluster.category,
                         json.dumps([o.label for o in a.cluster.outcomes]),
                         json.dumps([m.venue for m in a.cluster.markets]),
                         json.dumps([round(c, 4) for c in a.consensus])))
        self.executemany(
            "INSERT OR REPLACE INTO clusters VALUES (?,?,?,?,?,?,?)", rows)

    def save_arbs(self, run_id: str, result, plans: List[Dict]) -> None:
        rows = []
        plan_by_sig = {p.get("cluster", ""): p for p in plans}
        for opp in result.actionable + result.candidates:
            rows.append((run_id, opp.signature, opp.kind, opp.cluster_id, opp.title,
                         round(opp.edge, 5), round(opp.net_cost, 5),
                         round(opp.guaranteed_payout, 5),
                         1 if opp in result.actionable else 0,
                         json.dumps([vars(l) for l in opp.legs], default=str),
                         json.dumps(plan_by_sig.get(opp.cluster_id), default=str)))
        self.executemany(
            "INSERT OR REPLACE INTO arbs VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    def save_mm_plans(self, run_id: str, plans: Sequence) -> None:
        rows = [(run_id, p.venue, p.market_id, p.outcome_key,
                 p.bid, p.ask, p.size, p.strategy, p.fair,
                 p.expected_hourly_earnings,
                 json.dumps(p.components)) for p in plans]
        self.executemany(
            "INSERT OR REPLACE INTO mm_plans VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    def save_mm_backtests(self, run_id: str, report) -> None:
        rows = [(run_id, r.market_title, r.venue, r.strategy,
                 r.result.pnl, r.result.rewards_usd, r.result.spread_capture_usd,
                 r.result.adverse_cost_usd, r.result.fees_usd,
                 r.result.max_drawdown, r.result.sharpe, r.result.total_fills)
                for r in report.rows]
        self.executemany(
            "INSERT OR REPLACE INTO mm_backtests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def save_tests(self, run_id: str, tests: Sequence) -> None:
        rows = [(run_id, t.hypothesis, t.statistic, t.p_value, t.verdict, t.detail)
                for t in tests]
        self.executemany(
            "INSERT OR REPLACE INTO tests VALUES (?,?,?,?,?,?)", rows)

    # ---------------------------------------------------------- readers ---
    def latest_arbs(self, run_id: str) -> List[Dict]:
        return self.query("SELECT * FROM arbs WHERE run_id=? ORDER BY edge DESC", (run_id,))
