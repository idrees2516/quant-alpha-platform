"""quant_alpha — production-grade multi-venue alpha, aggregation, arbitrage and
market-making platform for prediction markets, sports books and news feeds.

Design principles:
  * Purely additive package: this code never mutates anything outside ``var/``
    and ``download/``.
  * Every network call is retried with exponential backoff + jitter, has a hard
    timeout, and degrades gracefully (live -> cache -> fixtures).
  * Deterministic: all randomness is seeded via ``config.seed`` unless
    explicitly overridden (live connectors never use randomness).
"""

__version__ = "1.0.0"
