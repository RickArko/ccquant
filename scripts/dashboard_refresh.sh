#!/usr/bin/env bash
# Lean tail-sync of dashboard domains, then publish HTML to Fly.
# Skips wallets / tweets / depth / MEV. On-chain stays on (freshness-gated;
# BID is not re-pulled unless stale). See documentation/Dashboard_Deploy.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.fly/bin:/usr/bin:/bin:${PATH:-}"

LOG_DIR="${CCQUANT_REFRESH_LOG_DIR:-$ROOT/data/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/dashboard-refresh.log"
exec >>"$LOG" 2>&1

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) dashboard.refresh start ==="
uv run ccquant sync all --no-wallets --no-tweets --no-depth --no-mev
make dashboard.deploy
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) dashboard.refresh done ==="
