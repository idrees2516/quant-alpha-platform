"""Resilient HTTP layer: timeouts, retries with exponential backoff + jitter,
per-call context logging and hard limits. All venue connectors go through
``http_json`` / ``http_get`` so retry semantics stay uniform and testable.
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, Optional

import requests

from .logging_setup import get_logger

log = get_logger("qa.net")

DEFAULT_UA = "quant-alpha/1.0 (+research; contact: ops)"


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status, self.url, self.body = status, url, body


def http_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 8.0,
    max_retries: int = 3,
    backoff_base_s: float = 0.4,
    expect: type = dict,
    session: Optional[requests.Session] = None,
) -> Any:
    """GET a JSON document with retries. Raises HttpError on final failure."""
    hdrs = {"User-Agent": DEFAULT_UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            resp = (session or requests).get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if expect is not None and not isinstance(data, expect):
                    if expect is dict and isinstance(data, list):
                        data = {"items": data}   # normalize list endpoints
                    else:
                        raise HttpError(200, url, "unexpected JSON shape")
                return data
            if resp.status_code in (403, 401, 404, 400):
                # non-retryable: blocked / bad request / not found
                raise HttpError(resp.status_code, url, resp.text)
            last_exc = HttpError(resp.status_code, url, resp.text)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        except HttpError:
            raise
        except ValueError as exc:                    # malformed JSON
            last_exc = exc
        if attempt < max_retries:
            sleep_s = backoff_base_s * (2 ** attempt) * (0.5 + random.random())
            log.debug("retrying %s in %.2fs (attempt %d)", url, sleep_s, attempt + 1)
            time.sleep(sleep_s)
    raise last_exc if last_exc else HttpError(0, url, "unknown failure")


def http_text(url: str, *, timeout: float = 8.0, max_retries: int = 2,
              headers: Optional[Dict[str, str]] = None) -> str:
    hdrs = {"User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, headers=hdrs, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            last_exc = HttpError(resp.status_code, url, resp.text[:200])
            if resp.status_code in (403, 401, 404):
                raise last_exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        if attempt < max_retries:
            time.sleep(0.3 * (2 ** attempt) * (0.5 + random.random()))
    raise last_exc if last_exc else HttpError(0, url, "unknown failure")
