"""Structured logging: JSON or human-readable, single init point."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in getattr(record, "ctx", {}).items():
            payload[str(k)] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def setup_logging(level: str = "INFO", json_mode: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_mode else logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-38s %(message)s", "%H:%M:%S"))
    root.handlers[:] = [handler]
    _CONFIGURED = True


def get_logger(name: str, **ctx) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger(name), {"ctx": ctx})
