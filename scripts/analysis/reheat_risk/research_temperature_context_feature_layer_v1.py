#!/usr/bin/env python3
"""Build a general temperature-context feature layer from reheat_feature_factory_v1.

Despite the historical name, reheat_feature_factory_v1 is the shared intraday
temperature state table.  This script adds mechanism labels that are useful for
current YES, current-bracket NO, d1/d2 NO, and timing studies.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.weather_context import temperature_context_features  # noqa: E402


IN_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/temperature_context_feature_layer_v1"
OUT_STATE_ROWS = OUT_DIR / "temperature_context_state_rows.csv"
OUT_SUMMARY = OUT_DIR / "temperature_context_summary.csv"
OUT_EXPR = OUT_DIR / "temperature_context_expression_matrix.csv"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-temperature-context-feature-layer-v1.md"

STATE_KEYS = ["city", "target_date", "decision_hour_local"]
CONTEXT_COLS = [
    "geo_context",
    "coastal_flow_state",
    "marine_thermal_state",
    "wind_thermal_state",
    "sky_state",
    "moisture_state",
    "warming_state",
    "cloud_warming_interaction",
    "moisture_cloud_interaction",
    "forecast_peak_clock_state",
    "temperature_context_regime",
]


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_No rows._"
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        values = []
        for col in cols:
            value = row.get(col)
            if isinstance(value, float):
                if col.startswith("p_") or col.endswith("_rate") or col.endswith("_roi"):
                    values.append(pct(value))
                else:
                    values.append(f"{value:+.2f}")
            else:
                values.append(str(value))
        out.append("| " + " | ".join(values) + " |")
    return "\n".join(out)


def load_state_rows() -> pd.DataFrame:
    raw = pd.read_csv(IN_ROWS, low_memory=False)
    state_cols = [
        *STATE_KEYS,
        "decision_snapshot_ts_utc",
        "timezone",
        "icao",
        "unit",
        "current_temp_c",
        "current_temp_f",
        "running_max_c",
        "running_max_f",
        "decline_native",
        "current_bracket_held",
        "d1_hit",
        "d2_hit",
        "skip_over_d1",
        "forecast_max_native",
        "forecast_peak_hour_local",
        "forecast_peak_delta_hours_local",
        "forecast_peak_hour_spread",
        "forecast_peak_models_agree_le_1h",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "relative_humidity_pct",
        "dewpoint_depression_f",
        "sky_cover_code",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "wind_speed_kt",
        "minutes_since_running_max",
    ]
    cols = [col for col in state_cols if col in raw.columns]
    states = raw[cols].drop_duplicates(STATE_KEYS).copy()
    labels = states.apply(lambda row: temperature_context_features(row.to_dict()), axis=1, result_type="expand")
    return pd.concat([states.reset_index(drop=True), labels.reset_index(drop=True)], axis=1)


def summarize_context(states: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in CONTEXT_COLS:
        for value, group in states.groupby(col, dropna=False):
            rows.append(
                {
                    "feature": col,
                    "bucket": str(value),
                    "rows": int(len(group)),
                    "dates": int(group["target_date"].nunique()),
                    "cities": int(group["city"].nunique()),
                    "p_current_bracket_held": float(pd.to_numeric(group["current_bracket_held"], errors="coerce").mean()),
                    "p_d1_hit": float(pd.to_numeric(group["d1_hit"], errors="coerce").mean()),
                    "p_d2_hit": float(pd.to_numeric(group["d2_hit"], errors="coerce").mean()),
                    "avg_temp_trend_1h_f": float(pd.to_numeric(group["temp_trend_1h_f"], errors="coerce").mean()),
                    "avg_wind_speed_kt": float(pd.to_numeric(group["wind_speed_kt"], errors="coerce").mean()),
                }
            )
    return pd.DataFrame(rows).sort_values(["feature", "rows"], ascending=[True, False])


def expression_matrix(states: pd.DataFrame) -> pd.DataFrame:
    label_cols = [
        "cloud_warming_interaction",
        "moisture_cloud_interaction",
        "marine_thermal_state",
        "forecast_peak_clock_state",
    ]
    rows = []
    for col in label_cols:
        for value, group in states.groupby(col, dropna=False):
            if len(group) < 20:
                continue
            rows.append(
                {
                    "context_feature": col,
                    "context_bucket": str(value),
                    "state_rows": int(len(group)),
                    "dates": int(group["target_date"].nunique()),
                    "cities": int(group["city"].nunique()),
                    "current_yes_win_rate": float(pd.to_numeric(group["current_bracket_held"], errors="coerce").mean()),
                    "current_no_pass_through_rate": 1.0
                    - float(pd.to_numeric(group["current_bracket_held"], errors="coerce").mean()),
                    "d1_hit_rate": float(pd.to_numeric(group["d1_hit"], errors="coerce").mean()),
                    "d2_hit_rate": float(pd.to_numeric(group["d2_hit"], errors="coerce").mean()),
                    "avg_decline_native": float(pd.to_numeric(group["decline_native"], errors="coerce").mean()),
                    "avg_trend1_f": float(pd.to_numeric(group["temp_trend_1h_f"], errors="coerce").mean()),
                }
            )
    return pd.DataFrame(rows).sort_values(["context_feature", "state_rows"], ascending=[True, False])


def main() -> int:
    if not IN_ROWS.exists():
        raise FileNotFoundError(f"missing input: {IN_ROWS}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    states = load_state_rows()
    summary = summarize_context(states)
    matrix = expression_matrix(states)
    states.to_csv(OUT_STATE_ROWS, index=False)
    summary.to_csv(OUT_SUMMARY, index=False)
    matrix.to_csv(OUT_EXPR, index=False)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": str(IN_ROWS.relative_to(ROOT)),
        "state_rows": int(len(states)),
        "dates": int(states["target_date"].nunique()),
        "cities": int(states["city"].nunique()),
        "date_min": str(states["target_date"].min()),
        "date_max": str(states["target_date"].max()),
        "outputs": {
            "state_rows": str(OUT_STATE_ROWS.relative_to(ROOT)),
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "expression_matrix": str(OUT_EXPR.relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    interesting = matrix.sort_values("state_rows", ascending=False).groupby("context_feature").head(6).to_dict("records")
    text = "\n".join(
        [
            "# Temperature Context Feature Layer V1",
            "",
            f"Generated: `{payload['generated_at_utc']}`",
            "",
            "## Verdict",
            "",
            "`reheat_feature_factory_v1` is already the shared intraday temperature state layer, not just a reheat-specific table.  This pass adds general mechanism labels for cloud/warming, moisture/cloud, wind/ocean/geography, and forecast peak clock so the same context can be reused by current YES, current-bracket NO, d1/d2 NO, Range RV, and timing studies.",
            "",
            "This is a feature/context layer, not a trading rule.  The labels are meant to explain and calibrate probability heads separately: current YES survive, current-bracket NO pass-through, and d1/d2 NO escape.",
            "",
            f"Coverage: `{payload['state_rows']}` city-date-hour state rows, `{payload['cities']}` cities, `{payload['date_min']}`..`{payload['date_max']}`.",
            "",
            "## Expression Matrix",
            "",
            md_table(
                interesting,
                [
                    "context_feature",
                    "context_bucket",
                    "state_rows",
                    "dates",
                    "cities",
                    "current_yes_win_rate",
                    "current_no_pass_through_rate",
                    "d1_hit_rate",
                    "d2_hit_rate",
                    "avg_decline_native",
                    "avg_trend1_f",
                ],
            ),
            "",
            "## Feature Families",
            "",
            "- `cloud_warming_interaction`: distinguishes clear solar warming, warming through cloud, cloud-limited flat/cooling, and mixed-sky regimes.",
            "- `moisture_cloud_interaction`: separates humid cloud suppression, humid convective risk, dry heat inertia, and generic cloud suppression.",
            "- `marine_thermal_state`: combines city geography, wind speed, and wind direction when available; if wind direction is missing it explicitly marks coastal direction unknown.",
            "- `forecast_peak_clock_state`: turns forecast peak timing into reusable context rather than a hard gate.",
            "",
            "## Boundary",
            "",
            "- Wind direction is not yet broadly present in `reheat_feature_factory_v1`; this layer can consume it when present, otherwise it keeps `flow_unknown` instead of backfilling future archive data.",
            "- The next production-quality step is to rename/promote the factory conceptually to `temperature_state_feature_factory`, while keeping old paths as compatibility aliases.",
            "",
        ]
    )
    OUT_MD.write_text(text, encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
