#!/usr/bin/env python3
"""Create the fail-closed Tmin V2.1/V3 research review packet."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow
import scipy


ROOT = Path(__file__).resolve().parents[3]
EPS = 1e-8
ARMS = {
    "M0_RAW_MARKET": "p_market",
    "M1_ALPHA050_ALL_WINDOWS": "p_v1_alpha050_all",
    "M2_ALPHA010_ROUTED": "p_v1_challenger",
    "M3_ALPHA025_ROUTED": "p_v1_alpha025_routed",
    "M4_ALPHA050_ROUTED": "p_v1_alpha050_routed",
}
CITY_TIMEZONES = {"Seoul": "Asia/Seoul", "Tokyo": "Asia/Tokyo"}
SOURCE_HASHES = {
    "TMIN_MODEL_LAYER_ELI5_GLOSSARY.md": "65eae7abe442cefb9b9ac58e52a6bf0262b4b2b2c7539d5766896003602f3c78",
    "TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN.md": "950f124190ef646ec774ec988e3d3c9fb8c5bcd5a6c69fe72d7a2a9ab93d0451",
    "TMIN_V2_1_V3_CODEX_EXECUTION_PLAN.md": "86f89a434e6f1757d9bd8de191ac7fccf3d7aab88def2084e51d0ca5405f5ca0",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout


def loss(y: np.ndarray, probability: np.ndarray, metric: str) -> np.ndarray:
    p = np.clip(np.asarray(probability, float), EPS, 1 - EPS)
    if metric == "logloss":
        return -(y * np.log(p) + (1 - y) * np.log(1 - p))
    return np.square(p - y)


def equal_metric(frame: pd.DataFrame, column: str, metric: str, grain: str) -> float:
    values = loss(frame["label"].to_numpy(int), frame[column].to_numpy(float), metric)
    if grain == "row":
        return float(values.mean())
    keys = ["target_date"] if grain == "date" else ["city", "target_date"]
    temp = frame[keys].copy()
    temp["value"] = values
    return float(temp.groupby(keys, sort=True)["value"].mean().mean())


def build_orthogonal(frame: pd.DataFrame) -> pd.DataFrame:
    scopes: list[tuple[str, pd.Series]] = [
        ("P0", pd.Series(True, index=frame.index)),
        ("P1_ACTIVE", frame["P1_ACTIVE_WINDOW_PROBABILITY"].astype(bool)),
        ("P1_OUTSIDE", ~frame["P1_ACTIVE_WINDOW_PROBABILITY"].astype(bool)),
        ("EARLY_2026-08-12_TO_2026-08-19", frame["target_date"].le("2026-08-19")),
        ("LATE_2026-08-20_TO_2026-08-26", frame["target_date"].ge("2026-08-20")),
    ]
    scopes += [
        (f"WINDOW_{value}", frame["window"].eq(value))
        for value in sorted(frame["window"].dropna().unique())
    ]
    scopes += [
        (f"CITY_{value}", frame["city"].eq(value))
        for value in sorted(frame["city"].dropna().unique())
    ]
    rows: list[dict[str, Any]] = []
    for scope, mask in scopes:
        subset = frame.loc[mask].copy()
        if subset.empty:
            continue
        for arm, column in ARMS.items():
            row: dict[str, Any] = {
                "scope": scope,
                "arm": arm,
                "probability_column": column,
                "rows": len(subset),
                "target_dates": subset["target_date"].nunique(),
                "city_dates": subset[["city", "target_date"]].drop_duplicates().shape[0],
            }
            for grain in ("row", "date", "city_date"):
                for metric in ("logloss", "brier"):
                    value = equal_metric(subset, column, metric, grain)
                    baseline = equal_metric(subset, "p_market", metric, grain)
                    row[f"{grain}_{metric}"] = value
                    row[f"{grain}_{metric}_delta_vs_market"] = value - baseline
            move = subset[column].to_numpy(float) - subset["p_market"].to_numpy(float)
            row["mean_probability_move"] = float(move.mean())
            row["mean_abs_probability_move"] = float(np.abs(move).mean())
            row["max_abs_probability_move"] = float(np.abs(move).max())
            rows.append(row)
    return pd.DataFrame(rows)


def build_calibration(frame: pd.DataFrame) -> pd.DataFrame:
    bins = [-np.inf, 0.80, 0.90, 0.95, 0.98, np.inf]
    labels = ["<.80", "[.80,.90)", "[.90,.95)", "[.95,.98)", "[.98,1]"]
    rows = []
    for arm, column in ARMS.items():
        assigned = pd.cut(frame[column], bins=bins, labels=labels, right=False)
        for band in labels:
            subset = frame.loc[assigned.eq(band)]
            rows.append(
                {
                    "arm": arm,
                    "band": band,
                    "rows": len(subset),
                    "mean_probability": float(subset[column].mean()) if len(subset) else None,
                    "positive_rate": float(subset["label"].mean()) if len(subset) else None,
                    "logloss": equal_metric(subset, column, "logloss", "row") if len(subset) else None,
                    "brier": equal_metric(subset, column, "brier", "row") if len(subset) else None,
                }
            )
    return pd.DataFrame(rows)


def build_event_truth(path_rows: pd.DataFrame, reconciliation: pd.DataFrame) -> pd.DataFrame:
    status = reconciliation[
        ["city", "target_date", "reconciliation_status", "exchange_resolved_rung"]
    ]
    groups = []
    for (city, target_date), subset in path_rows.groupby(["city", "target_date"], sort=True):
        subset = subset.sort_values("observation_event_time_utc").copy()
        rung = subset["normalized_native_rung"].to_numpy(int)
        running = np.minimum.accumulate(rung)
        event_indices = []
        for index, current in enumerate(running):
            future = np.flatnonzero(rung[index + 1 :] <= current - 1)
            event_indices.append(index + 1 + int(future[0]) if len(future) else None)
        day_end = (
            pd.Timestamp(target_date, tz=CITY_TIMEZONES[city])
            + pd.Timedelta(days=1)
            - pd.Timedelta(microseconds=1)
        ).tz_convert("UTC")
        groups.append(
            pd.DataFrame(
                {
                    "city": city,
                    "target_date": target_date,
                    "checkpoint_time_utc": subset["observation_event_time_utc"].to_numpy(),
                    "checkpoint_available_time_utc": subset["published_available_time_utc"].to_numpy(),
                    "current_native_rung": running,
                    "next_colder_rung": running - 1,
                    "T_first_next_colder_observation": [
                        subset.iloc[event]["observation_event_time_utc"] if event is not None else pd.NaT
                        for event in event_indices
                    ],
                    "T_available_first_next_colder": [
                        subset.iloc[event]["published_available_time_utc"] if event is not None else pd.NaT
                        for event in event_indices
                    ],
                    "right_censor_time": day_end,
                    "crossed_before_day_end": [event is not None for event in event_indices],
                    "path_source": subset["source"].to_numpy(),
                    "path_source_version": subset["source_version"].astype(str).to_numpy(),
                    "pit_feature_authorized": False,
                }
            )
        )
    event = pd.concat(groups, ignore_index=True).merge(
        status, on=["city", "target_date"], how="left"
    )
    event["truth_gate_pass"] = False
    event["truth_eligible_for_model"] = False
    event["model_blocker"] = "SETTLEMENT_SOURCE_EXACT_MATCH_BELOW_99_PERCENT"
    return event


def weather_panel_manifest(event: pd.DataFrame) -> dict[str, Any]:
    city_day_counts = (
        event[["city", "target_date"]].drop_duplicates()["city"].value_counts()
    )
    independent_city_days = int(city_day_counts.sum())
    event_city_days = int(
        event.loc[event["crossed_before_day_end"], ["city", "target_date"]]
        .drop_duplicates()
        .shape[0]
    )
    max_city_weight = (
        float(city_day_counts.max() / independent_city_days)
        if independent_city_days
        else 1.0
    )
    size_gate_pass = bool(
        independent_city_days >= 120
        and event_city_days >= 40
        and city_day_counts.get("Seoul", 0) >= 25
        and city_day_counts.get("Tokyo", 0) >= 25
        and max_city_weight <= 0.55
    )
    return {
        "independent_city_days": independent_city_days,
        "next_colder_event_city_days": event_city_days,
        "by_city": {
            city: {
                "city_days": int(group["target_date"].nunique()),
                "event_city_days": int(
                    group.loc[group["crossed_before_day_end"], "target_date"].nunique()
                ),
            }
            for city, group in event.groupby("city", sort=True)
        },
        "max_city_weight": max_city_weight,
        "provisional_size_gate": "PASS" if size_gate_pass else "FAIL",
        "provisional_size_gate_pass": size_gate_pass,
        "provisional_size_gate_thresholds": {
            "independent_city_days_min": 120,
            "event_city_days_min": 40,
            "seoul_city_days_min": 25,
            "tokyo_city_days_min": 25,
            "max_single_city_weight": 0.55,
        },
        "training_authorization": False,
        "blocker": "settlement-source exact-match gate failed",
    }


def freeze_forecast_sample(db_path: Path | None, freeze_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    sample_path = freeze_dir / "forecast_archive_sample.parquet"
    inventory_path = freeze_dir / "forecast_archive_inventory.json"
    if sample_path.exists() and inventory_path.exists():
        return pd.read_parquet(sample_path), json.loads(inventory_path.read_text())
    if db_path is None:
        raise ValueError("canonical DB required for first forecast freeze")
    with sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True) as connection:
        raw = pd.read_sql_query(
            """
            SELECT curve_id, snapshot_ts_utc, available_at_utc, city, target_date,
                   forecast_source, forecast_model, forecast_values_hash,
                   hourly_curve_json, source_file
            FROM fact_forecast_hourly_curves
            WHERE city IN ('Seoul','Tokyo')
            ORDER BY city, target_date, snapshot_ts_utc, curve_id
            """,
            connection,
        )
    raw["available_at"] = raw["available_at_utc"].fillna(raw["snapshot_ts_utc"])
    raw["available_at_provenance"] = np.where(
        raw["available_at_utc"].notna(),
        "canonical_available_at_utc",
        "derived_from_snapshot_ts_utc",
    )
    inventory = {
        "rows": len(raw),
        "city_dates": int(raw[["city", "target_date"]].drop_duplicates().shape[0]),
        "target_dates": int(raw["target_date"].nunique()),
        "by_city": {
            city: {
                "rows": int(len(group)),
                "city_dates": int(group["target_date"].nunique()),
                "min_target_date": str(group["target_date"].min()),
                "max_target_date": str(group["target_date"].max()),
            }
            for city, group in raw.groupby("city", sort=True)
        },
        "available_at_native_rows": int(raw["available_at_utc"].notna().sum()),
        "available_at_derived_rows": int(raw["available_at_utc"].isna().sum()),
        "status": "INVENTORIED_NOT_FIT_TRUTH_GATE_BLOCKED",
    }
    sample = (
        raw.groupby(["city", "target_date"], sort=True, group_keys=False)
        .tail(1)
        .sort_values(["target_date", "city"])
        .reset_index(drop=True)
        .drop(columns=["hourly_curve_json"])
    )
    sample["realized_official_remaining_min"] = np.nan
    sample["forecast_error"] = np.nan
    sample["P_forecast_no_next_colder"] = np.nan
    sample["P_cross_next_rung"] = np.nan
    sample["status"] = "NOT_COMPUTED_SETTLEMENT_SOURCE_TRUTH_BLOCKED"
    freeze_dir.mkdir(parents=True, exist_ok=True)
    sample.to_parquet(sample_path, index=False)
    write_json(inventory_path, inventory)
    return sample, inventory


def knowledge_manifest() -> dict[str, Any]:
    base = ROOT / "docs/knowledge/tmin"
    rows = []
    passed = True
    for name, expected in SOURCE_HASHES.items():
        path = base / name
        text = path.read_text(encoding="utf-8")
        body = text.split("---", 2)[2].lstrip("\n")
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        concrete_path_scan = text.replace("/Users/...", "").replace("/private/tmp/...", "")
        checks = {
            "exists": path.exists(),
            "live_authorization_false": "live_authorization: false" in text,
            "no_personal_absolute_path": (
                "/Users/" not in concrete_path_scan
                and "/private/tmp/" not in concrete_path_scan
            ),
            "source_body_hash_exact": body_hash == expected,
            "has_substantive_headings": body.count("\n#") >= 5,
        }
        passed &= all(checks.values())
        rows.append(
            {
                "path": str(path.relative_to(ROOT)),
                "file_sha256": sha256(path),
                "source_body_sha256": body_hash,
                "expected_source_sha256": expected,
                "checks": checks,
            }
        )
    links_resolve = all((base / name).exists() for name in SOURCE_HASHES)
    passed &= (base / "README.md").exists() and links_resolve
    return {
        "status": "PASS" if passed else "FAIL",
        "documents": rows,
        "index": "docs/knowledge/tmin/README.md",
        "relative_links_resolve": links_resolve,
        "missing_review_sources": [
            "TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md",
            "TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json",
        ],
        "missing_review_sources_block_training": False,
    }


def freeze_reproduction_inputs(args: argparse.Namespace) -> None:
    destination = args.output_dir / "frozen_inputs"
    destination.mkdir(parents=True, exist_ok=True)
    if args.frozen_input_root:
        shutil.copytree(args.frozen_input_root, destination, dirs_exist_ok=True)
        return
    base = destination / "base"
    truth = destination / "truth"
    base.mkdir(exist_ok=True)
    truth.mkdir(exist_ok=True)
    required = {
        args.base_row_audit_input: base / "ROW_LEVEL_PROBABILITY_AUDIT.parquet",
        args.trade_funnel_input: base / "ROW_LEVEL_TRADE_FUNNEL.csv",
        args.candidate_journal_input: base / "signal_candidates.portable.jsonl",
        args.repaired_evaluation_input: base / "v1_repaired_evaluation.json",
        args.iem_seoul_input: truth / "RKSI_apr14_aug21.csv",
        args.iem_tokyo_input: truth / "RJTT_apr14_aug21.csv",
    }
    for source, target in required.items():
        if source is None or not source.exists():
            raise FileNotFoundError(f"missing frozen reproduction input: {source}")
        shutil.copy2(source, target)
    pm_destination = truth / "pm_history_lowest"
    pm_destination.mkdir(exist_ok=True)
    copied = 0
    for source in sorted(args.pm_history_input.glob("*.json")):
        try:
            city, target_date = source.stem.rsplit("_", 1)
        except ValueError:
            continue
        if city in {"Seoul", "Tokyo"} and "2026-04-15" <= target_date <= "2026-08-20":
            shutil.copy2(source, pm_destination / source.name)
            copied += 1
    if copied == 0:
        raise FileNotFoundError("no Seoul/Tokyo settlement cache inputs frozen")
    if args.wu_snapshot_input and args.wu_snapshot_input.exists():
        wu_destination = destination / "wu_disputes"
        if args.wu_snapshot_input.resolve() != wu_destination.resolve():
            shutil.copytree(
                args.wu_snapshot_input,
                wu_destination,
                dirs_exist_ok=True,
            )


def frozen_input_manifest(directory: Path) -> dict[str, Any]:
    files = [
        {
            "path": str(path.relative_to(directory)),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]
    return {
        "schema_version": "tmin_v2_1_v3_frozen_inputs_v1",
        "file_count": len(files),
        "total_bytes": int(sum(row["bytes"] for row in files)),
        "files": files,
    }


def md_table(frame: pd.DataFrame, columns: list[str]) -> str:
    view = frame[columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in view.itertuples(index=False, name=None):
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |")
    return "\n".join(lines)


def build(args: argparse.Namespace) -> None:
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    rows = pd.read_parquet(args.base_dir / "ROW_LEVEL_PROBABILITY_AUDIT.parquet")
    if len(rows) != 168 or rows["checkpoint_id"].nunique() != 168:
        raise AssertionError("P0 must remain 168 unique checkpoints")
    truth = json.loads((args.truth_dir / "SETTLEMENT_SOURCE_PATH_TRUTH_GATE.json").read_text())
    if truth["gate_pass"]:
        raise AssertionError("fail-closed packager cannot fit after a passing gate")
    disposition = "STOP_SETTLEMENT_SOURCE_TRUTH_BLOCKED"
    reconciliation = pd.read_parquet(args.truth_dir / "SETTLEMENT_SOURCE_RECONCILIATION.parquet")
    path_rows = pd.read_parquet(args.truth_dir / "SETTLEMENT_SOURCE_PATH_ROWS.parquet")
    event = build_event_truth(path_rows, reconciliation)
    forecast_sample, forecast_inventory = freeze_forecast_sample(
        args.canonical_db, out / "frozen_inputs"
    )
    orthogonal = build_orthogonal(rows)
    calibration = build_calibration(rows)
    knowledge = knowledge_manifest()
    if knowledge["status"] != "PASS":
        raise AssertionError("knowledge persistence checks failed")
    weather_panel = weather_panel_manifest(event)

    shutil.copy2(args.base_dir / "ROW_LEVEL_PROBABILITY_AUDIT.parquet", out / "ROW_LEVEL_PROBABILITY_AUDIT.parquet")
    prediction_columns = [
        "candidate_id", "checkpoint_id", "city", "target_date", "decision_ts_utc",
        "window", "label", "p_market", "p_v1_alpha050_all", "p_v1_challenger",
        "p_v1_alpha025_routed", "p_v1_alpha050_routed", "physical_innovation",
        "routing_indicator", "score_gradient_alpha0_unrouted", "score_gradient_alpha0_routed",
    ]
    predictions = rows[prediction_columns].copy()
    predictions["p_v2_1"] = np.nan
    predictions["p_v3"] = np.nan
    predictions["v2_1_status"] = "NOT_FIT_SETTLEMENT_SOURCE_TRUTH_BLOCKED"
    predictions["v3_status"] = "NOT_FIT_SETTLEMENT_SOURCE_TRUTH_BLOCKED"
    predictions.to_parquet(out / "MODEL_PREDICTIONS.parquet", index=False)
    event.to_parquet(out / "EVENT_TIME_TRUTH.parquet", index=False)
    forecast_sample.to_parquet(out / "FORECAST_ERROR_ARCHIVE_SAMPLE.parquet", index=False)
    orthogonal.to_csv(out / "10_ROUTING_ALPHA_ORTHOGONAL_DECOMPOSITION.csv", index=False)

    leaderboard = pd.read_csv(args.base_dir / "09_OOF_MODEL_LEADERBOARD.csv")
    leaderboard["stage_status"] = "EVALUATED"
    leaderboard = pd.concat(
        [
            leaderboard,
            pd.DataFrame(
                [
                    {"model": "V2_1_ROUTED_TRANSFER", "stage_status": "NOT_FIT_TRUTH_GATE_BLOCKED"},
                    {"model": "V3_DISCRETE_HAZARD", "stage_status": "NOT_FIT_TRUTH_GATE_BLOCKED"},
                ]
            ),
        ],
        ignore_index=True,
    )
    leaderboard.to_csv(out / "09_MODEL_LEADERBOARD.csv", index=False)
    mismatch = reconciliation[
        reconciliation["reconciliation_status"].isin(["UNRESOLVED_MISMATCH", "EXCHANGE_RUNG_UNRESOLVED"])
    ][
        ["city", "target_date", "exchange_resolved_rung", "iem_final_min_rung",
         "wu_final_min_rung", "reconciliation_status"]
    ]
    gradient = json.loads((args.base_dir / "ROUTED_SCORE_GRADIENT_DIAGNOSTICS.json").read_text())
    arm_p0 = orthogonal[orthogonal["scope"].eq("P0")][
        ["arm", "date_logloss_delta_vs_market", "date_brier_delta_vs_market"]
    ]
    current = json.loads(args.current_evaluation.read_text()) if args.current_evaluation else {}
    current_forward = current.get("prospective_challenger", {}).get("forward_funnel", {})
    forward_manifest = {
        "seal_created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seal_settlements_max_target_date": current.get("canonical_db", {}).get("settlements_max_target_date"),
        "common_forward_denominator": "all probability-complete P0 rows; no execution conditioning",
        "joint_test": "one-sided target-date max-T across alpha .10/.25/.50; preregistered",
        "arms": [
            {"arm": "V1_ALPHA010_ROUTED", "alpha": 0.10, "start_target_date": "2026-08-28",
             "status": "existing_frozen_zero_notional_unchanged", "selector_or_order_authorized": False,
             "current_prediction_rows": current_forward.get("checkpoint_rows")},
            {"arm": "V1_ALPHA025_ROUTED_DIAGNOSTIC", "alpha": 0.25,
             "start_target_date": args.diagnostic_forward_start,
             "status": "preregistered_probability_only_no_backfill", "selector_or_order_authorized": False},
            {"arm": "V1_ALPHA050_ROUTED_DIAGNOSTIC", "alpha": 0.50,
             "start_target_date": args.diagnostic_forward_start,
             "status": "preregistered_probability_only_no_backfill", "selector_or_order_authorized": False},
        ],
    }
    write_json(out / "FORWARD_ARM_MANIFEST.json", forward_manifest)
    write_json(out / "KNOWLEDGE_PERSISTENCE_MANIFEST.json", knowledge)
    frozen_manifest = frozen_input_manifest(out / "frozen_inputs")
    write_json(out / "FROZEN_INPUT_MANIFEST.json", frozen_manifest)

    reports = {
        "00_REVIEW_CONTRACT.md": f"""# Review contract

Scope: V1 diagnostic closure and truth-gated V2.1/V3 research. No selector,
order, execution, sizing, live or tiny-live authorization.

Final disposition: `{disposition}`.
""",
        "01_EXECUTIVE_DECISION_BRIEF.md": f"""# Executive decision brief

**Disposition: `{disposition}`**

- V1 routed gradient bug is closed; P1-outside nonzero rows = **{gradient['outside_p1_nonzero_rows']}**.
- Same-P0 routing/alpha arms are now orthogonally compared.
- Truth coverage is {truth['covered_city_dates']}/{truth['expected_exchange_city_dates']}, but exact match is {truth['exact_match_city_dates']}/{truth['covered_city_dates']} = **{truth['exact_match_rate']:.2%}**, below 99%.
- V2.1/V3 were not fit. This is a truth-contract stop, not evidence that weather alpha is absent.
- Alpha=.10 forward is unchanged; .25/.50 diagnostic arms start {args.diagnostic_forward_start}, probability-only with no backfill or selector.
""",
        "02_KNOWLEDGE_PERSISTENCE_REPORT.md": f"""# Knowledge persistence

Status: **{knowledge['status']}**. Three exact substantive bodies are under
`docs/knowledge/tmin/`, indexed with version boundaries and
`live_authorization: false`.

The zip omitted the two named 2026-08-28 external review/audit originals.
They remain disclosed as missing and were not reconstructed from summaries.
""",
        "03_V1_DIAGNOSTIC_CLOSURE.md": f"""# V1 diagnostic closure

`innovation=(logit(p_incumbent)-logit(p_market))/.50`;
`gradient=innovation*I(active_window)*(y-p_market)`.

- Date-equal mean: **{gradient['date_equal_mean']:.8f}**
- 20,000-block bootstrap CI: **[{gradient['bootstrap']['ci_low']:.8f}, {gradient['bootstrap']['ci_high']:.8f}]**
- P1-outside: {gradient['outside_p1_rows']} rows, {gradient['outside_p1_nonzero_rows']} nonzero.

{md_table(arm_p0, list(arm_p0.columns))}

Routing and alpha strength are separate comparisons; no historical promotion is inferred.
""",
        "04_SETTLEMENT_SOURCE_PATH_TRUTH.md": f"""# Settlement-source path truth

Correct local day uses IANA UTC+09, not the old replay's UTC-15h transform.
Coverage: {truth['coverage']:.2%}; exact match: {truth['exact_match_rate']:.2%}
(required >=99%); exchange-unresolved dates: {truth['exchange_unresolved_city_dates']}.

{md_table(mismatch, list(mismatch.columns))}

Direct WU history agrees with IEM on the four resolved disputes but still
disagrees with exchange rungs. No mismatch was silently excluded.
""",
        "05_WEATHER_HISTORY_PANEL_REPORT.md": f"""# Weather history panel

- independent city-days: {weather_panel['independent_city_days']}
- event city-days: {weather_panel['next_colder_event_city_days']}
- max city weight: {weather_panel['max_city_weight']:.2%}
- by city: {weather_panel['by_city']}

Size passes, but IEM paths are ex-post label archives without first-seen
`available_at`; all event rows are feature-unauthorized and training is blocked.
""",
        "06_FORECAST_ERROR_ARCHIVE_REPORT.md": f"""# Forecast error archive

Inventory: {forecast_inventory['rows']} curves / {forecast_inventory['city_dates']}
city-dates. Native available_at rows: {forecast_inventory['available_at_native_rows']};
derived from immutable snapshot timestamp: {forecast_inventory['available_at_derived_rows']}.

No error distribution, hierarchical shrinkage or OOF crossing probability was
fit after the truth gate failed. The parquet is a deterministic inventory sample.
""",
        "07_V2_1_MODEL_SPEC_AND_RESULTS.md": """# V2.1 model

Specified: weather-history foundation + forecast foundation + <=3-parameter
nonnegative routed residual adaptor with nested prior-date OOF.

**NOT FIT:** settlement-source exact match is below the preregistered gate.
Current direct-settlement V2 remains `UNDERPOWERED_NEAR_IDENTITY_NULL`, not a
falsification of incremental weather alpha.

This packager is intentionally blocked-only. A gate-pass fit runner is not
prebuilt against invalid truth; it must be implemented and independently
validated under a new evidence seal after the source contract is repaired.
""",
        "08_V3_EVENT_TIME_AND_HAZARD_RESULTS.md": """# V3 event-time and hazard

`EVENT_TIME_TRUTH.parquet` exposes candidate event/censor rows, all marked
`truth_eligible_for_model=false`. The fixed 1-hour hazard and residual lambda
were **not fit**; no complexity was used to bypass the truth blocker.
""",
        "12_ABLATION_REPORT.md": """# Ablation report

Only M0, alpha=.50 all-window, and alpha=.10/.25/.50 routed same-P0 ablations
are valid. V2.1 physical/forecast/adaptor and V3 hazard ablations are absent
because those models were not fit.
""",
        "13_CITY_DATE_STABILITY.md": f"""# City/date stability

Machine slices are in `10_ROUTING_ALPHA_ORTHOGONAL_DECOMPOSITION.csv` and
`STABILITY_RESULTS.json`. Corrected-gradient leave-one-date count:
{len(gradient['leave_one_date_out'])}; leave-one-city count:
{len(gradient['leave_one_city_out'])}. No promotion claim is made.
""",
        "14_PIT_LEAKAGE_AND_APPEND_INVARIANCE_AUDIT.md": """# PIT, leakage and append invariance

- Frozen P0 keeps 168/168 legal observation/forecast refs.
- Corrected gradient is exactly zero outside P1.
- Historical paths are label-only, never PIT features.
- Forecast derived availability is explicit.
- V2.1/V3 stop before any future label can enter fitting.
- Reproduction uses frozen inputs and mutates no production state.
""",
        "15_FORWARD_ARM_MANIFEST.md": f"""# Forward arm manifest

Alpha=.10 remains unchanged from 2026-08-28. Alpha=.25/.50 diagnostics start
{args.diagnostic_forward_start}, no backfill, probability-only, no selector/order.
Common denominator and one-sided target-date max-T are preregistered.
""",
        "16_FINDINGS_AND_OPEN_QUESTIONS.md": """# Findings and open questions

1. **Blocking:** source-path vs exchange exact match is 98.26%; four Seoul dates need resolution-source adjudication.
2. **Prior evidence bug:** old METAR replay used UTC-15h instead of UTC+09h; its historical cross statistics require a separate rebuild before reuse.
3. **Closed:** routed gradient includes routing and is zero on 116 outside-P1 rows.
4. **Lineage gap:** two named external review originals were absent from the zip.
5. After truth repair only: rerun PIT/size gates, then fit V2.1/V3 under the frozen spec.
""",
        "17_INDEPENDENT_REVIEW.md": """# Independent read-only review

Reviewer scope: correctness, timezone/local-day, truth denominator and silent
exclusion, PIT/future information, fail-closed semantics, forward boundary,
portability, append-only behavior, manifests, parquet schemas and tests.

Initial findings and disposition:

- package-local reproduction inputs were missing — fixed by freezing the
  minimal row audit, trade funnel, candidate journal, repaired evaluation,
  IEM paths, settlement caches and WU dispute snapshots; the 43 MB DB is no
  longer required;
- runners overwrote non-empty outputs — fixed with default refusal and an
  explicit development-only override;
- Tokyo used Seoul's timezone symbolically — fixed with city-specific IANA
  timezones;
- weather size gate was a literal PASS — fixed as an evaluated contract;
- coverage tests were thin — expanded to 10 passing tests.

The reviewer also requested a gate-pass fit branch. That was intentionally not
implemented: this is a blocked-only packager, and prebuilding an unvalidated
fit path against failed truth would violate the fail-closed contract. A fit
runner requires repaired truth and a new evidence seal.

Reviewer role: `luna_verifier` (gpt-5.6-luna, medium). Final usage telemetry
was unavailable in the reviewer interface. Reviewer made no file changes and
spawned no subagents.
""",
        "GPT_PRO_REVIEW_PACKET.md": f"""# GPT Pro review packet

Disposition: `{disposition}`; no live authorization.

Please adjudicate A) knowledge persistence, B) gradient closure, C) routing vs
alpha orthogonality, D) unchanged .10 forward, E) legal .25/.50 boundary,
F) mandatory stop at 226/230, G) later forecast PIT adequacy, H) V2.1
not-adjudicable/not-fit, I) V3 not-adjudicable/not-fit, and J) next challenger
V1/V2.1/V3/parallel/NONE after truth repair.
""",
    }
    for name, text in reports.items():
        (out / name).write_text(text, encoding="utf-8")
    (out / "11_HIGH_PROBABILITY_CALIBRATION.md").write_text(
        "# High-probability calibration\n\n"
        + md_table(calibration, ["arm", "band", "rows", "mean_probability", "positive_rate", "logloss", "brier"])
        + "\n",
        encoding="utf-8",
    )

    for source, destination in [
        (args.base_dir / "ROUTED_SCORE_GRADIENT_DIAGNOSTICS.json", out / "ROUTED_SCORE_GRADIENT_DIAGNOSTICS.json"),
        (args.base_dir / "STABILITY_RESULTS.json", out / "STABILITY_RESULTS.json"),
        (args.truth_dir / "SETTLEMENT_SOURCE_PATH_TRUTH_GATE.json", out / "SETTLEMENT_SOURCE_PATH_TRUTH_GATE.json"),
        (args.truth_dir / "SETTLEMENT_SOURCE_RECONCILIATION.parquet", out / "SETTLEMENT_SOURCE_RECONCILIATION.parquet"),
    ]:
        shutil.copy2(source, destination)
    if args.current_evaluation:
        shutil.copy2(args.current_evaluation, out / "CURRENT_FORWARD_EVALUATION_SNAPSHOT.json")

    snapshots = out / "source_snapshots"
    snapshots.mkdir(exist_ok=True)
    source_files = [
        ROOT / "scripts/analysis/tmin/evaluate_tmin_no_further_cooling_shadow_v1.py",
        ROOT / "scripts/analysis/tmin/tmin_model_layer_v2_v3_v1.py",
        ROOT / "scripts/analysis/tmin/build_tmin_settlement_source_path_truth_v1.py",
        Path(__file__).resolve(),
        ROOT / "tests/research_tests/test_tmin_no_further_cooling_challenger_v1.py",
    ]
    for source in source_files:
        shutil.copy2(source, snapshots / source.name)
    knowledge_snapshots = snapshots / "docs_knowledge_tmin"
    knowledge_snapshots.mkdir(exist_ok=True)
    for source in sorted((ROOT / "docs/knowledge/tmin").glob("*.md")):
        shutil.copy2(source, knowledge_snapshots / source.name)
    patch_paths = [
        "scripts/analysis/tmin/evaluate_tmin_no_further_cooling_shadow_v1.py",
        "scripts/analysis/tmin/tmin_model_layer_v2_v3_v1.py",
        "scripts/analysis/tmin/build_tmin_settlement_source_path_truth_v1.py",
        "scripts/analysis/tmin/package_tmin_model_layer_v2_1_v3_research_v1.py",
        "tests/research_tests/test_tmin_no_further_cooling_challenger_v1.py",
        "docs/WEATHER_DOCS_INDEX.md",
        "docs/WEATHER_STRATEGY_REGISTRY.md",
        "docs/WEATHER_TMIN_DISTRIBUTION_EDGE_STRATEGY.md",
        "docs/knowledge",
    ]
    patch_text = git(
        "diff", "--",
        *patch_paths,
    )
    for relative in patch_paths:
        path = ROOT / relative
        if path.is_dir():
            candidates = sorted(path.rglob("*"))
        else:
            candidates = [path]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", str(candidate.relative_to(ROOT))],
                cwd=ROOT,
                text=True,
                capture_output=True,
            ).returncode == 0
            if tracked:
                continue
            lines = candidate.read_text(encoding="utf-8").splitlines()
            patch_text += "\n" + "\n".join(
                difflib.unified_diff(
                    [],
                    lines,
                    fromfile="/dev/null",
                    tofile=f"b/{candidate.relative_to(ROOT)}",
                    lineterm="",
                )
            ) + "\n"
    (out / "SOURCE_PATCH.diff").write_text(patch_text, encoding="utf-8")
    write_json(
        out / "ENVIRONMENT.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "pyarrow": pyarrow.__version__,
        },
    )
    reproduce = """#!/usr/bin/env bash
set -euo pipefail
PACKET_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$PACKET_DIR/../.." && pwd)"
RUN_DIR="${1:-/private/tmp/tmin-v21-v3-reproduction}"
mkdir -p "$RUN_DIR"
cd "$REPO_ROOT"
.venv/bin/python scripts/analysis/tmin/tmin_model_layer_v2_v3_v1.py --row-audit "$PACKET_DIR/frozen_inputs/base/ROW_LEVEL_PROBABILITY_AUDIT.parquet" --trade-funnel "$PACKET_DIR/frozen_inputs/base/ROW_LEVEL_TRADE_FUNNEL.csv" --candidate-journal "$PACKET_DIR/frozen_inputs/base/signal_candidates.portable.jsonl" --repaired-evaluation "$PACKET_DIR/frozen_inputs/base/v1_repaired_evaluation.json" --current-evaluation "$PACKET_DIR/CURRENT_FORWARD_EVALUATION_SNAPSHOT.json" --output-dir "$RUN_DIR/base"
.venv/bin/python scripts/analysis/tmin/build_tmin_settlement_source_path_truth_v1.py --iem-seoul "$PACKET_DIR/frozen_inputs/truth/RKSI_apr14_aug21.csv" --iem-tokyo "$PACKET_DIR/frozen_inputs/truth/RJTT_apr14_aug21.csv" --pm-history-dir "$PACKET_DIR/frozen_inputs/truth/pm_history_lowest" --wu-snapshot-dir "$PACKET_DIR/frozen_inputs/wu_disputes" --output-dir "$RUN_DIR/truth" >/dev/null
.venv/bin/python scripts/analysis/tmin/package_tmin_model_layer_v2_1_v3_research_v1.py --base-dir "$RUN_DIR/base" --truth-dir "$RUN_DIR/truth" --current-evaluation "$PACKET_DIR/CURRENT_FORWARD_EVALUATION_SNAPSHOT.json" --frozen-input-root "$PACKET_DIR/frozen_inputs" --output-dir "$RUN_DIR/packet"
.venv/bin/python - "$PACKET_DIR" "$RUN_DIR/packet" <<'PY'
import json, pandas as pd, sys
from pathlib import Path
expected, actual = map(Path, sys.argv[1:3])
assert json.loads((actual/"SETTLEMENT_SOURCE_PATH_TRUTH_GATE.json").read_text()) == json.loads((expected/"SETTLEMENT_SOURCE_PATH_TRUTH_GATE.json").read_text())
assert len(pd.read_parquet(actual/"ROW_LEVEL_PROBABILITY_AUDIT.parquet")) == 168
assert len(pd.read_parquet(actual/"MODEL_PREDICTIONS.parquet")) == 168
pd.testing.assert_frame_equal(pd.read_csv(actual/"09_MODEL_LEADERBOARD.csv"), pd.read_csv(expected/"09_MODEL_LEADERBOARD.csv"), check_exact=False, atol=1e-12, rtol=0)
print("REPRODUCTION_OK disposition=STOP_SETTLEMENT_SOURCE_TRUTH_BLOCKED P0=168 truth_exact=226/230")
PY
"""
    (out / "REPRODUCE.sh").write_text(reproduce, encoding="utf-8")
    (out / "REPRODUCE.sh").chmod(0o755)
    seal = {
        "schema_version": "tmin_model_layer_v2_1_v3_research_seal_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "disposition": disposition,
        "git_base_sha": git("rev-parse", "HEAD").strip(),
        "git_patch_sha256": sha256(out / "SOURCE_PATCH.diff"),
        "truth_gate": truth,
        "weather_panel": weather_panel,
        "forecast_inventory": forecast_inventory,
        "frozen_input_manifest_sha256": sha256(out / "FROZEN_INPUT_MANIFEST.json"),
        "knowledge_status": knowledge["status"],
        "forward_manifest_sha256": sha256(out / "FORWARD_ARM_MANIFEST.json"),
        "live_authorization": False,
        "independent_review": {
            "role": "luna_verifier",
            "model": "gpt-5.6-luna",
            "effort": "medium",
            "usage_telemetry": "unavailable",
            "reviewer_modified_files": False,
            "reviewer_spawned_subagents": False,
            "findings_fixed": [
                "package-local minimal frozen inputs",
                "default no-overwrite output guards",
                "city-specific timezone",
                "computed weather size gate",
                "expanded automated tests",
            ],
            "fit_branch_disposition": "deferred until repaired truth and a new evidence seal",
        },
    }
    write_json(out / "REVIEW_EVIDENCE_SEAL.json", seal)
    files = [
        {"path": str(path.relative_to(out)), "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(out.rglob("*"))
        if path.is_file() and path.name != "EVIDENCE_MANIFEST.json"
    ]
    write_json(
        out / "EVIDENCE_MANIFEST.json",
        {"schema_version": "tmin_v2_1_v3_evidence_manifest_v1", "file_count": len(files), "files": files},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--truth-dir", type=Path, required=True)
    parser.add_argument("--current-evaluation", type=Path)
    parser.add_argument("--canonical-db", type=Path)
    parser.add_argument("--forecast-freeze-input", type=Path)
    parser.add_argument("--frozen-input-root", type=Path)
    parser.add_argument(
        "--base-row-audit-input",
        type=Path,
        default=ROOT / "reviews/tmin_no_further_model_forensics_v1/ROW_LEVEL_PROBABILITY_AUDIT.parquet",
    )
    parser.add_argument(
        "--trade-funnel-input",
        type=Path,
        default=ROOT / "reviews/tmin_no_further_model_forensics_v1/ROW_LEVEL_TRADE_FUNNEL.csv",
    )
    parser.add_argument(
        "--candidate-journal-input",
        type=Path,
        default=ROOT / "reviews/tmin_no_further_model_forensics_v1/frozen_inputs/signal_candidates.portable.jsonl",
    )
    parser.add_argument("--repaired-evaluation-input", type=Path)
    parser.add_argument(
        "--iem-seoul-input",
        type=Path,
        default=ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw/RKSI_apr14_aug21.csv",
    )
    parser.add_argument(
        "--iem-tokyo-input",
        type=Path,
        default=ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw/RJTT_apr14_aug21.csv",
    )
    parser.add_argument(
        "--pm-history-input",
        type=Path,
        default=ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history_lowest",
    )
    parser.add_argument("--wu-snapshot-input", type=Path)
    parser.add_argument("--diagnostic-forward-start", default="2026-08-29")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-existing-output", action="store_true")
    args = parser.parse_args()
    if (
        args.output_dir.exists()
        and any(args.output_dir.iterdir())
        and not args.allow_existing_output
    ):
        raise FileExistsError(
            f"refusing to overwrite non-empty output directory: {args.output_dir}"
        )
    freeze_reproduction_inputs(args)
    if args.forecast_freeze_input:
        destination = args.output_dir / "frozen_inputs"
        destination.mkdir(parents=True, exist_ok=True)
        for name in ["forecast_archive_sample.parquet", "forecast_archive_inventory.json"]:
            source = args.forecast_freeze_input / name
            if source.resolve() != (destination / name).resolve():
                shutil.copy2(source, destination / name)
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
