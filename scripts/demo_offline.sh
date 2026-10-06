#!/usr/bin/env bash
# Deterministic offline end-to-end run (fixtures everywhere)
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m quant_alpha demo "$@"
