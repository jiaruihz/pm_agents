#!/usr/bin/env bash
set -euo pipefail

# Compatibility wrapper retained for old operator references. Production owns
# one canonical market-book collector, not a separate full-ladder collector.
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
exec "$PROJECT_DIR/scripts/ops/start_weather_market_books.sh" "$@"
