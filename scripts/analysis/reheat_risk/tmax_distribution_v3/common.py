"""Shared paths, constants and helpers for the tmax_distribution_v3 replay.

Reuses the single-snapshot lineage replay v2 module (denominator producer)
instead of starting a parallel data chain.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402
from weather_data_feed.city_family import CITY_FAMILY_ATLAS_V1  # noqa: E402
from weather_data_feed.production_paths import (  # noqa: E402
    current_forecast_curves,
)

V2_SCRIPT = ROOT / "scripts/analysis/reheat_risk/research_tmax_single_snapshot_lineage_replay_v2.py"
V2_OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_single_snapshot_lineage_replay_v2"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_v3"
REPORT_MD = ROOT / "docs/analysis/2026-07/2026-07-12-tmax-distribution-v3-model-v1.md"
REPORT_JSON = ROOT / "docs/analysis/2026-07/2026-07-12-tmax-distribution-v3-model-v1.json"
ATLAS_CSV = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
CURVE_ROOTS = [
    current_forecast_curves(),
]

# Execution policy frozen BEFORE any v3 model comparison; inherited verbatim
# from the replay-v2 frozen policy plus the target-book rebalance-v1
# precedent.  Never tuned against v3 results.
FROZEN_POLICY: dict[str, Any] = {
    "ask_floor": 0.40,
    "ask_ceiling": 0.99,
    "edge_threshold": 0.02,
    "fee_formula": "shares * 0.05 * price * (1 - price)",
    "shares": 5,
    "min_ask_size": 5.0,
    "expressions": ("current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"),
    "selection": "first_eligible_city_day",
    "rebalance_buffer": 0.02,
    "adverse_selection_buffer": 0.01,
    "frozen_source": "replay_v2_frozen_policy + target_book_rebalance_v1",
}

MIN_TRAIN_DATES_INNER = 3
MIN_TRAIN_DATES_OUTER = 5
INNER_SELECT_DATES = 5
TRAILING_WINDOW_DATES = 10
BOOTSTRAP_SAMPLES = 5000
BOOTSTRAP_SEED = 20260712
SCOPE_RETRO = "retrospective_diagnostic"


def load_v2_module():
    spec = importlib.util.spec_from_file_location("tmax_ssr_v2", V2_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("tmax_ssr_v2", module)
    spec.loader.exec_module(module)
    return module


V2 = load_v2_module()
finite = V2.finite
parse_utc = V2.parse_utc
yes_quote = V2.yes_quote
bracket_key = V2.bracket_key
bracket_sort_key = V2.bracket_sort_key
interval_contains = V2.interval_contains
official_fee = V2.official_fee
load_settlement_winners = V2.load_settlement_winners


def bracket_interval(label: str) -> tuple[float | None, float | None, bool, bool]:
    """Return (low, high, bottom_open, top_open) for a bracket label."""
    parsed = parse_market_bracket(str(label or ""), "")
    if parsed is None:
        return None, None, False, False
    low = None if parsed.bottom else (float(parsed.low) if parsed.low is not None else None)
    high = None if parsed.top else (float(parsed.high) if parsed.high is not None else None)
    return low, high, bool(parsed.bottom), bool(parsed.top)


def bracket_mid_native(label: str) -> float | None:
    low, high, bottom, top = bracket_interval(label)
    if low is not None and high is not None:
        return (low + high) / 2.0
    if bottom and high is not None:
        return high - 0.5
    if top and low is not None:
        return low + 0.5
    return None


def bracket_step_shift(prev_label: str, cur_label: str) -> int:
    """Rungs the anchor advanced between two decisions (0 when unchanged)."""
    if str(prev_label) == str(cur_label):
        return 0
    p_low, p_high, _, _ = bracket_interval(prev_label)
    c_low, c_high, _, _ = bracket_interval(cur_label)
    if p_low is None or c_low is None:
        return 1
    width = 1.0
    if p_high is not None and p_high > p_low:
        width = (p_high - p_low) + 1.0
    shift = int(round((c_low - p_low) / max(width, 1e-9)))
    return max(shift, 0) if shift != 0 else 1


def to_f_delta(unit: str) -> float:
    return 1.0 if str(unit).upper() == "F" else 9.0 / 5.0


def native_to_f(value: float, unit: str) -> float:
    return float(value) if str(unit).upper() == "F" else float(value) * 9.0 / 5.0 + 32.0


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def markdown_table(frame: pd.DataFrame, columns: list[str] | None = None) -> list[str]:
    if frame is None or frame.empty:
        return ["_No rows._"]
    columns = columns or list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for record in frame.reindex(columns=columns).to_dict("records"):
        cells = []
        for value in record.values():
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                cells.append("NA")
            elif isinstance(value, (float, np.floating)):
                cells.append(f"{float(value):.4f}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def date_block_bootstrap_ci(
    daily_values: pd.Series, seed: int = BOOTSTRAP_SEED, samples: int = BOOTSTRAP_SAMPLES
) -> tuple[float, float]:
    values = daily_values.to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 3:
        return math.nan, math.nan
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(samples, len(values)))
    means = values[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def roi_date_block_ci(
    daily: pd.DataFrame, seed: int = BOOTSTRAP_SEED, samples: int = BOOTSTRAP_SAMPLES
) -> tuple[float, float, float]:
    """ROI point + CI where daily has pnl/cost columns (one row per date)."""
    if daily.empty or daily["cost"].sum() <= 0:
        return math.nan, math.nan, math.nan
    point = float(daily["pnl"].sum() / daily["cost"].sum())
    if len(daily) < 3:
        return point, math.nan, math.nan
    arr = daily[["pnl", "cost"]].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(samples):
        sample = arr[rng.integers(0, len(arr), len(arr))]
        cost = sample[:, 1].sum()
        out.append(sample[:, 0].sum() / cost if cost > 0 else math.nan)
    out = np.asarray(out, dtype=float)
    out = out[np.isfinite(out)]
    return point, float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def city_family(city: str) -> str:
    return CITY_FAMILY_ATLAS_V1.get(str(city), "unknown")
