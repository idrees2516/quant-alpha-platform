#!/usr/bin/env bash
# Full LIVE pipeline: pull all venues -> aggregate -> arb -> MM plan ->
# backtest -> models -> reports in download/
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m quant_alpha run-all "$@"
