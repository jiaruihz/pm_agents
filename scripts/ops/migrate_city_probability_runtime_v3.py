#!/usr/bin/env python3
"""Public one-time migration entry for city probability runtime v3."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.materialize_city_decision_dual_run_v1 import main


if __name__ == "__main__":
    raise SystemExit(main())
