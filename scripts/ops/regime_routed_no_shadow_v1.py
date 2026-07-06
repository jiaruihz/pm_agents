#!/usr/bin/env python3
"""Append regime-routed NO candidates to a zero-notional shadow journal.

This runner does not submit, sign, or place orders. It records the current
balanced candidate:

  day_marginal_runway/day_open_runway -> current-bracket NO
  day_forecast_capped                 -> d2 NO

with the fixed soft_balanced notional multiplier from the research report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools import regime_routed_no_stable as research  # noqa: E402
from weather_feature_layer.runtime_refs import attach_runtime_feature_frame_ref  # noqa: E402


STRATEGY_ID = "regime_routed_no_soft_balanced_shadow_v1"
RULE_ID = "routed_d2_relaxed70_best_ask_soft_balanced_v1"
STATE_ROWS_DEFAULT = research.ATLAS_ROWS
JOURNAL_DEFAULT = ROOT / "runtime/weather_edge_v1/regime_routed_no_shadow_v1/shadow_candidates.jsonl"
SUMMARY_DEFAULT = ROOT / "runtime/weather_edge_v1/regime_routed_no_shadow_v1/latest_summary.json"
FEATURE_STORE_DEFAULT = ROOT / os.environ.get("WEATHER_FEATURE_STORE_DIR", "runtime/weather_feature_store")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-rows", default=str(STATE_ROWS_DEFAULT))
    parser.add_argument("--journal", default=str(JOURNAL_DEFAULT))
    parser.add_argument("--summary", default=str(SUMMARY_DEFAULT))
    parser.add_argument("--min-target-date", default=None, help="Default: max target_date in state rows.")
    parser.add_argument("--max-target-date", default=None)
    parser.add_argument("--hypothetical-full-notional-usd", type=float, default=5.0)
    parser.add_argument("--max-candidates-per-run", type=int, default=500)
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_ready(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("shadow_decision_id"):
                ids.add(str(row["shadow_decision_id"]))
    return ids


def decision_id(row: pd.Series) -> str:
    raw = "|".join(
        [
            RULE_ID,
            str(row.get("target_date")),
            str(row.get("city")),
            str(row.get("decision_snapshot_ts_utc")),
            str(row.get("decision_hour_local")),
            str(row.get("expression")),
            str(row.get("current_bracket")),
            str(row.get("d2_no_bracket")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_candidates(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    states = pd.read_csv(args.state_rows, low_memory=False)
    states["target_date"] = states["target_date"].astype(str)
    states["decision_hour_local"] = pd.to_numeric(states["decision_hour_local"], errors="coerce")
    min_target_date = args.min_target_date or str(states["target_date"].max())

    selected = research.variant_frame(
        states,
        research.BALANCED_SOFT_CANDIDATE,
        research.ASK_CAPS["relaxed70"],
        "best_ask",
        require_payoff=False,
    )
    selected = research.add_soft_weights(selected)
    mask = selected["target_date"].astype(str).ge(min_target_date)
    if args.max_target_date:
        mask &= selected["target_date"].astype(str).le(args.max_target_date)
    selected = selected[mask].copy()
    selected = selected.sort_values(["target_date", "decision_snapshot_ts_utc", "city"]).head(
        args.max_candidates_per_run
    )
    meta = {
        "state_rows": int(len(states)),
        "state_min_target_date": str(states["target_date"].min()) if len(states) else None,
        "state_max_target_date": str(states["target_date"].max()) if len(states) else None,
        "effective_min_target_date": min_target_date,
        "effective_max_target_date": args.max_target_date,
        "selected_rows_before_dedupe": int(len(selected)),
    }
    return selected, meta


def journal_row(row: pd.Series, args: argparse.Namespace) -> dict[str, Any]:
    full_notional = float(args.hypothetical_full_notional_usd)
    multiplier = float(row.get("soft_balanced") or 0.0)
    ask = float(row["ask"])
    expression = str(row["expression"])
    bracket = row.get("d2_no_bracket") if expression == "d2_no" else row.get("current_bracket")
    payload = {
        "record_type": "regime_routed_no_shadow_candidate",
        "journal_schema_version": 1,
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "shadow_decision_id": decision_id(row),
        "created_at_utc": now_utc(),
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "city": json_ready(row.get("city")),
        "target_date": json_ready(row.get("target_date")),
        "decision_snapshot_ts_utc": json_ready(row.get("decision_snapshot_ts_utc")),
        "decision_hour_local": int(row["decision_hour_local"]),
        "decision_timezone": json_ready(row.get("timezone")),
        "side": "BUY_NO",
        "expression": expression,
        "bracket": json_ready(bracket),
        "current_bracket": json_ready(row.get("current_bracket")),
        "d2_no_bracket": json_ready(row.get("d2_no_bracket")),
        "no_ask": ask,
        "no_ask_size": json_ready(row.get("ask_size")),
        "ask_notional": json_ready(row.get("ask_notional")),
        "hypothetical_full_notional_usd": full_notional,
        "soft_balanced_multiplier": multiplier,
        "hypothetical_weighted_notional_usd": full_notional * multiplier,
        "hypothetical_weighted_shares": (full_notional * multiplier) / ask if ask > 0 else None,
        "day_regime": json_ready(row.get("day_regime")),
        "intraday_state": json_ready(row.get("intraday_state")),
        "moisture_cloud_regime": json_ready(row.get("moisture_cloud_regime")),
        "wind_regime": json_ready(row.get("wind_regime")),
        "running_max_state": json_ready(row.get("running_max_state")),
        "city_family": json_ready(row.get("city_family")),
        "current_native": json_ready(row.get("current_native")),
        "running_native": json_ready(row.get("running_native")),
        "forecast_source": json_ready(row.get("forecast_source")),
        "forecast_max_native": json_ready(row.get("forecast_max_native")),
        "forecast_peak_hour_local": json_ready(row.get("forecast_peak_hour_local")),
        "route_multiplier": json_ready(row.get("route_multiplier")),
        "price_multiplier": json_ready(row.get("price_multiplier")),
        "weather_multiplier": json_ready(row.get("weather_multiplier")),
        "day_multiplier": json_ready(row.get("day_multiplier")),
        "day_risk": json_ready(row.get("day_risk")),
        "config": {
            "variant": research.BALANCED_SOFT_CANDIDATE,
            "weight_policy": research.BALANCED_SOFT_POLICY,
            "ask_min": research.ASK_MIN,
            "ask_max": research.ASK_CAPS["relaxed70"],
            "decision_hours": sorted(research.DECISION_HOURS),
            "selector": "best_ask",
            "hypothetical_full_notional_usd": full_notional,
        },
        "source_state_rows": str(Path(args.state_rows).resolve().relative_to(ROOT)),
    }
    return attach_runtime_feature_frame_ref(
        payload,
        store_root=FEATURE_STORE_DEFAULT,
        feature_grain="regime_routed_no_shadow_candidate",
        source_profile_id=STRATEGY_ID,
        builder_version="regime_routed_no_shadow_feature_ref_v1",
        key_columns=(
            "strategy_id",
            "shadow_decision_id",
            "city",
            "target_date",
            "decision_snapshot_ts_utc",
            "expression",
            "bracket",
        ),
    )


def main() -> int:
    args = parse_args()
    journal_path = Path(args.journal)
    summary_path = Path(args.summary)
    selected, meta = load_candidates(args)
    seen = existing_ids(journal_path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    skipped_existing = 0
    entries: list[dict[str, Any]] = []
    with journal_path.open("a", encoding="utf-8") as fh:
        for _, row in selected.iterrows():
            entry = journal_row(row, args)
            entries.append(entry)
            if entry["shadow_decision_id"] in seen:
                skipped_existing += 1
                continue
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            seen.add(entry["shadow_decision_id"])
            appended += 1

    summary = {
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "journal": rel(journal_path),
        "appended": appended,
        "skipped_existing": skipped_existing,
        "selected_rows_before_dedupe": int(len(selected)),
        "feature_frame_ref_stored_count": sum(
            1 for entry in entries if str(entry.get("feature_frame_ref_status") or "") == "stored"
        ),
        "feature_frame_ref_error_count": sum(
            1 for entry in entries if str(entry.get("feature_frame_ref_status") or "") == "error"
        ),
        "selected_dates": sorted(selected["target_date"].astype(str).unique().tolist()) if len(selected) else [],
        "selected_cities": int(selected["city"].nunique()) if len(selected) else 0,
        "selected_by_regime": selected["day_regime"].value_counts(dropna=False).to_dict() if len(selected) else {},
        "meta": meta,
        "status": "shadow_rows_appended" if appended else "no_new_shadow_rows",
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
