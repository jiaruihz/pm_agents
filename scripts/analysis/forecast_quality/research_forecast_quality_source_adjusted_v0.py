#!/usr/bin/env python3
"""Rerun forecast-quality reliability with settlement-source classes attached.

This is opportunity-grain research. It uses fact_signal_candidates as the
canonical forecast/market/settlement input, joins the city-level settlement
source registry, and keeps all strategy overlays at decision-price proxy level.
No N100/live config is changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_forecast_quality_base_v0 as fq_base  # noqa: E402
from scripts.analysis.observed_max import research_settlement_source_registry_v0 as source_registry  # noqa: E402


DB_PATH = ROOT / "runtime/weather.db"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-15-forecast-quality-source-adjusted-v0.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-15-forecast-quality-source-adjusted-v0.md"
TARGET_METRIC = "forecast_quality_source_adjusted_reliability_v0"

LABEL_COLS = [
    "forecast_quality_medium_plus",
    "forecast_quality_low",
    "city_model_reliable",
    "city_model_unreliable",
    "model_market_disagreement_high",
    "tail_risk_high",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x * 100:+.1f}%"


def num(x: float | None, digits: int = 2) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x:.{digits}f}"


def source_bucket(cls: Any) -> str:
    value = str(cls or "unknown")
    if value == "default_wu_station_by_rules":
        return "default_wu"
    if value == "default_source_watchlist":
        return "default_watchlist"
    if value in {"official_station_diff_confirmed", "special_source_confirmed"}:
        return "source_sensitive_confirmed"
    if value == "blocked_unresolved_settlement_basis":
        return "blocked_unresolved"
    return "other_or_unknown"


def attach_source_registry(decision_sets: pd.DataFrame, registry: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "city",
        "settlement_source_class",
        "official_station_or_feed",
        "mapping_rule",
        "downstream_action",
    ]
    out = decision_sets.merge(registry[cols], on="city", how="left")
    out["settlement_source_class"] = out["settlement_source_class"].fillna("missing_registry")
    out["source_bucket"] = out["settlement_source_class"].map(source_bucket)
    return out


def attach_source_to_strats(strats: pd.DataFrame, decision_sets: pd.DataFrame) -> pd.DataFrame:
    if strats.empty:
        return strats
    cols = [
        "decision_set_id",
        "settlement_source_class",
        "source_bucket",
        "official_station_or_feed",
        "mapping_rule",
    ]
    return strats.merge(decision_sets[cols].drop_duplicates("decision_set_id"), on="decision_set_id", how="left")


def quality_slice(df: pd.DataFrame) -> dict[str, Any]:
    base = fq_base.summarize_quality(df)
    label_rates = {}
    for col in LABEL_COLS:
        label_rates[col] = None if df.empty else float(pd.to_numeric(df[col], errors="coerce").fillna(0).mean())
    return {
        **base,
        "label_rates": label_rates,
    }


def source_quality_summary(
    decision_sets: pd.DataFrame,
    group_col: str,
    train_dates: set[str],
    holdout_dates: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group_value, group in decision_sets.groupby(group_col, dropna=False):
        train = group[group["event_date"].astype(str).isin(train_dates)]
        holdout = group[group["event_date"].astype(str).isin(holdout_dates)]
        rows.append(
            {
                group_col: str(group_value),
                "all": quality_slice(group),
                "train": quality_slice(train),
                "holdout": quality_slice(holdout),
            }
        )
    return sorted(rows, key=lambda x: (x["all"]["rows"], x[group_col]), reverse=True)


def summarize_filtered_strategy(
    family: pd.DataFrame,
    train_dates: set[str],
    holdout_dates: set[str],
) -> list[dict[str, Any]]:
    filters = [
        ("no_quality_filter", None, None),
        ("forecast_quality_medium_plus", "forecast_quality_medium_plus", 1),
        ("exclude_forecast_quality_low", "forecast_quality_low", 0),
        ("city_model_reliable", "city_model_reliable", 1),
    ]
    out: list[dict[str, Any]] = []
    train_family = family[family["event_date"].astype(str).isin(train_dates)]
    holdout_family = family[family["event_date"].astype(str).isin(holdout_dates)]
    train_base = fq_base.summarize_strategy(train_family)
    holdout_base = fq_base.summarize_strategy(holdout_family)
    for filter_name, col, expected in filters:
        selected = family if col is None else family[family[col] == expected]
        train = selected[selected["event_date"].astype(str).isin(train_dates)]
        holdout = selected[selected["event_date"].astype(str).isin(holdout_dates)]
        train_sum = fq_base.summarize_strategy(train)
        holdout_sum = fq_base.summarize_strategy(holdout)
        out.append(
            {
                "filter": filter_name,
                "train": train_sum,
                "holdout": holdout_sum,
                "train_excess_roi_vs_bucket_family": None
                if train_sum["roi"] is None or train_base["roi"] is None
                else train_sum["roi"] - train_base["roi"],
                "holdout_excess_roi_vs_bucket_family": None
                if holdout_sum["roi"] is None or holdout_base["roi"] is None
                else holdout_sum["roi"] - holdout_base["roi"],
            }
        )
    return out


def overlay_by_source(
    strats: pd.DataFrame,
    group_col: str,
    train_dates: set[str],
    holdout_dates: set[str],
) -> list[dict[str, Any]]:
    if strats.empty:
        return []
    out: list[dict[str, Any]] = []
    for (group_value, algorithm), family in strats.groupby([group_col, "algorithm"], dropna=False):
        out.append(
            {
                group_col: str(group_value),
                "algorithm": str(algorithm),
                "filters": summarize_filtered_strategy(family, train_dates, holdout_dates),
            }
        )
    return sorted(out, key=lambda x: (x[group_col], x["algorithm"]))


def population_comparison(decision_sets: pd.DataFrame, train_dates: set[str], holdout_dates: set[str]) -> list[dict[str, Any]]:
    groups = [
        ("all_decision_sets", decision_sets),
        ("default_wu_only", decision_sets[decision_sets["source_bucket"] == "default_wu"]),
        (
            "default_wu_plus_watchlist",
            decision_sets[decision_sets["source_bucket"].isin(["default_wu", "default_watchlist"])],
        ),
        (
            "source_sensitive_confirmed",
            decision_sets[decision_sets["source_bucket"] == "source_sensitive_confirmed"],
        ),
        ("blocked_unresolved", decision_sets[decision_sets["source_bucket"] == "blocked_unresolved"]),
    ]
    out = []
    for name, group in groups:
        train = group[group["event_date"].astype(str).isin(train_dates)]
        holdout = group[group["event_date"].astype(str).isin(holdout_dates)]
        out.append({"population": name, "all": quality_slice(group), "train": quality_slice(train), "holdout": quality_slice(holdout)})
    return out


def selected_overlay_table(overlay_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    keep_algorithms = {
        "adjacent3_yes_cost085",
        "single_leg_buy_no_cost40_75_edge010_top1",
        "side_band_best_leg_mid_cost_e008_top4",
    }
    keep_filters = {"no_quality_filter", "forecast_quality_medium_plus", "exclude_forecast_quality_low", "city_model_reliable"}
    for item in overlay_rows:
        if item["algorithm"] not in keep_algorithms:
            continue
        for filt in item["filters"]:
            if filt["filter"] not in keep_filters:
                continue
            holdout = filt["holdout"]
            if holdout["rows"] == 0:
                continue
            out.append(
                {
                    "source_bucket": item.get("source_bucket"),
                    "algorithm": item["algorithm"],
                    "filter": filt["filter"],
                    "train_rows": filt["train"]["rows"],
                    "train_roi": filt["train"]["roi"],
                    "holdout_rows": holdout["rows"],
                    "holdout_dates": holdout["event_dates"],
                    "holdout_roi": holdout["roi"],
                    "holdout_excess": filt["holdout_excess_roi_vs_bucket_family"],
                    "top5_removed": holdout["top5_removed_roi"],
                }
            )
    return sorted(out, key=lambda x: (str(x["source_bucket"]), str(x["algorithm"]), str(x["filter"])))


def table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def source_quality_table(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        holdout = row["holdout"]
        all_rows = row["all"]
        rates = holdout["label_rates"]
        out.append(
            {
                key: row[key],
                "all_rows": all_rows["rows"],
                "cities": all_rows["cities"],
                "holdout_rows": holdout["rows"],
                "holdout_dates": holdout["event_dates"],
                "holdout_adj3": pct(holdout["adjacent3_hit"]),
                "holdout_tail_miss": pct(holdout["tail_miss"]),
                "mp_rate": pct(rates["forecast_quality_medium_plus"]),
                "low_rate": pct(rates["forecast_quality_low"]),
                "reliable_rate": pct(rates["city_model_reliable"]),
            }
        )
    return out


def overlay_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append(
            {
                "bucket": row["source_bucket"],
                "algorithm": row["algorithm"],
                "filter": row["filter"],
                "train_rows": row["train_rows"],
                "train_roi": pct(row["train_roi"]),
                "holdout_rows": row["holdout_rows"],
                "holdout_dates": row["holdout_dates"],
                "holdout_roi": pct(row["holdout_roi"]),
                "holdout_excess": pct(row["holdout_excess"]),
                "top5_removed": pct(row["top5_removed"]),
            }
        )
    return out


def render_md(payload: dict[str, Any]) -> str:
    class_rows = source_quality_table(payload["source_class_quality"], "settlement_source_class")
    bucket_rows = source_quality_table(payload["source_bucket_quality"], "source_bucket")
    population_rows = source_quality_table(payload["population_comparison"], "population")
    overlay_rows = overlay_table(payload["selected_overlay_rows"])
    lines = [
        "# Forecast Quality Source-Adjusted v0",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{payload['db_path']}`",
        "> Scope: opportunity-grain reliability research only; no N100/live config changed; no live action.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates` for decision-set reliability; settlement source classes from `settlement_source_registry_v0`.",
        f"- DB last_modified: `{payload['db_last_modified_utc']}`.",
        f"- fact_signal_candidates rows: `{payload['self_check']['candidate_coverage']['rows']}`.",
        f"- decision_sets used: `{payload['funnel']['decision_sets']}` settled city/event/model/snapshot distributions.",
        f"- source-matched decision_sets: `{payload['funnel']['source_matched_decision_sets']}`.",
        f"- strategy overlay rows: `{payload['funnel']['strategy_rows']}`.",
        f"- train: `{payload['split']['train_start']}` -> `{payload['split']['train_end']}` ({payload['split']['train_dates']} event_dates).",
        f"- holdout: `{payload['split']['holdout_start']}` -> `{payload['split']['holdout_end']}` ({payload['split']['holdout_dates']} event_dates).",
        "- 本报告不发布 `live_real` PnL/ROI/rank/curve，因此不使用 CLOB coverage gate 作为结论来源。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(payload["self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Target Metric",
        "",
        "`forecast_quality_source_adjusted_reliability_v0` = whether the shared forecast-quality labels remain useful after city decision sets are stratified by Polymarket settlement source class.",
        "",
        "The key distinction is source alignment, not payout truth: `pm_history` / `final_yes` remains the settlement label, while `settlement_source_class` says whether local forecast/observed features are aligned with the station/feed/rule Polymarket settles against.",
        "",
        "## Population Comparison",
        "",
        table(
            population_rows,
            [
                "population",
                "all_rows",
                "cities",
                "holdout_rows",
                "holdout_dates",
                "holdout_adj3",
                "holdout_tail_miss",
                "mp_rate",
                "low_rate",
                "reliable_rate",
            ],
        ),
        "",
        "## Source Bucket Quality",
        "",
        table(
            bucket_rows,
            [
                "source_bucket",
                "all_rows",
                "cities",
                "holdout_rows",
                "holdout_dates",
                "holdout_adj3",
                "holdout_tail_miss",
                "mp_rate",
                "low_rate",
                "reliable_rate",
            ],
        ),
        "",
        "## Settlement Source Class Quality",
        "",
        table(
            class_rows,
            [
                "settlement_source_class",
                "all_rows",
                "cities",
                "holdout_rows",
                "holdout_dates",
                "holdout_adj3",
                "holdout_tail_miss",
                "mp_rate",
                "low_rate",
                "reliable_rate",
            ],
        ),
        "",
        "## Strategy Overlay By Source Bucket",
        "",
        "Rows are decision-price proxy overlays. They answer whether a reliability tag behaves similarly across settlement-source buckets, not whether it is executable.",
        "",
        table(
            overlay_rows,
            [
                "bucket",
                "algorithm",
                "filter",
                "train_rows",
                "train_roi",
                "holdout_rows",
                "holdout_dates",
                "holdout_roi",
                "holdout_excess",
                "top5_removed",
            ],
        ),
        "",
        "## Findings",
        "",
        f"- Default WU cities remain the largest generic denominator: `{payload['headline']['default_wu_decision_sets']}` decision sets across `{payload['headline']['default_wu_cities']}` cities. This should be the default denominator for generic forecast-reliability claims.",
        f"- Source-sensitive confirmed cities are not garbage data, but they are a different feature-source problem: `{payload['headline']['source_sensitive_decision_sets']}` decision sets across `{payload['headline']['source_sensitive_cities']}` cities. HK must use HKO semantics; station-diff cities must use the official station/feed.",
        f"- Blocked unresolved cities are small but should not train or validate generic source-sensitive claims: `{payload['headline']['blocked_decision_sets']}` decision sets across `{payload['headline']['blocked_cities']}` cities.",
        "- The shared forecast-quality base is still useful, but the reusable contract must include `settlement_source_class`. A city-model reliability tag that mixes default, special-source, and blocked settlement bases is too easy to misread.",
        "- HK/Jakarta follow the earlier registry conclusion: HK is HKO Daily Extract + floor mapping, Jakarta is WIHH/Halim. They should be separate source adapters, not generic VHHH/WIII forecast-quality rows.",
        "",
        "## Reuse Contract Update",
        "",
        "- Forecast-quality sidecar grain stays `city + event_date + forecast_source/model_version + decision_snapshot_ts_utc`, but it must carry `settlement_source_class`, `official_station_or_feed`, and `mapping_rule`.",
        "- Generic model-reliability reporting should default to `default_wu` plus an explicit optional watchlist column. Do not silently pool `blocked_unresolved` into generic training/holdout summaries.",
        "- Source-sensitive cities can be used in broad strategy overlays only as tagged covariates. For feature-source claims, rebuild features from official sources first: HKO for HongKong, WIHH for Jakarta, official station for station-diff cities.",
        "- Range RV / adjacent3 / side-band / BUY_NO / basket consumers should report no-quality baseline, forecast-quality filter, and source bucket side by side.",
        "- Any orderbook/execution claim remains subject to `snapshot_ts_utc <= decision_snapshot_ts_utc`; this report does not make executable claims.",
        "",
        "## Next Research Directions",
        "",
        "1. Materialize `settlement_source_class`, `official_station_or_feed`, and `mapping_rule` into a generated forecast-quality sidecar or fact-table join artifact.",
        "2. Build HK HKO and Jakarta WIHH source adapters, then rerun source-sensitive reliability using official source features rather than generic configured-station features.",
        "3. Rerun BUY_NO single-leg and side-band consumers with source buckets shown as a required table; if a tag only works in source-sensitive cities, it is not a generic forecast-quality base.",
        "4. Keep `blocked_unresolved` cities out of source-sensitive feature research until Moscow/Seoul/Shenzhen root causes are resolved.",
        "5. After more forward settlements, redo event-date cluster bootstrap and top-date stress inside each source bucket before promoting any tag beyond shadow telemetry.",
        "",
        "## Three-Gate Verdict",
        "",
        "| gate | status | reason |",
        "| --- | --- | --- |",
        "| significance | FAIL | This is a source-stratified reliability audit; bucket samples are thin and no excess ROI CI is durable enough for live action. |",
        "| baseline | FAIL | Decision-price overlays are not a time-aligned executable baseline and do not uniformly beat family baselines across buckets. |",
        "| forward | FAIL | Holdout exists, but source-bucket support is too small for confirmed generalization. |",
        "",
        "`significance=FAIL`, `baseline=FAIL`, `forward=FAIL`, `conclusion=inconclusive` for live action.",
        "",
        "## Plain-English Conclusion",
        "",
        "The forecast-quality base should survive as a reusable reliability layer, but it must become source-aware. HK/Jakarta/station-diff cities are not ordinary default-station rows; they need official-source feature adapters or explicit source-sensitive treatment. Blocked unresolved cities should stay out of source-sensitive claims.",
        "",
    ]
    return "\n".join(lines)


def json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if not np.isfinite(value):
            return None
        return float(value)
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    if pd.isna(value):
        return None
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    args = parser.parse_args()

    db_path = Path(args.db_path)
    conn = fq_base.connect_ro(db_path)
    try:
        self_check = fq_base.mandatory_self_check(conn)
        candidates = fq_base.load_candidates(conn)
        city_counts = source_registry.fact_city_counts(conn)
    finally:
        conn.close()

    registry = source_registry.add_fact_counts(source_registry.build_registry(), city_counts)
    decision_sets = fq_base.build_decision_sets(candidates)
    train_dates, holdout_dates, split = fq_base.split_dates(decision_sets)
    decision_sets = fq_base.add_historical_features(fq_base.add_cross_model_features(decision_sets))
    decision_sets, thresholds = fq_base.add_quality_labels(decision_sets, train_dates)
    decision_sets = attach_source_registry(decision_sets, registry)
    strats = attach_source_to_strats(fq_base.strategy_rows(decision_sets), decision_sets)

    source_bucket_quality = source_quality_summary(decision_sets, "source_bucket", train_dates, holdout_dates)
    source_class_quality = source_quality_summary(decision_sets, "settlement_source_class", train_dates, holdout_dates)
    source_bucket_overlay = overlay_by_source(strats, "source_bucket", train_dates, holdout_dates)
    selected_rows = selected_overlay_table(source_bucket_overlay)

    def group_stat(bucket: str) -> tuple[int, int]:
        g = decision_sets[decision_sets["source_bucket"] == bucket]
        return int(len(g)), int(g["city"].nunique())

    default_rows, default_cities = group_stat("default_wu")
    source_rows, source_cities = group_stat("source_sensitive_confirmed")
    blocked_rows, blocked_cities = group_stat("blocked_unresolved")

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "self_check": self_check,
        "split": split,
        "thresholds": thresholds,
        "funnel": {
            "fact_signal_candidates_loaded": int(len(candidates)),
            "settled_candidate_rows_loaded": int(candidates["final_yes"].notna().sum()),
            "decision_sets": int(len(decision_sets)),
            "source_matched_decision_sets": int((decision_sets["settlement_source_class"] != "missing_registry").sum()),
            "strategy_rows": int(len(strats)),
            "strategy_algorithms": int(strats["algorithm"].nunique()) if not strats.empty else 0,
        },
        "registry_class_summary": source_registry.class_summary(registry),
        "population_comparison": population_comparison(decision_sets, train_dates, holdout_dates),
        "source_bucket_quality": source_bucket_quality,
        "source_class_quality": source_class_quality,
        "source_bucket_overlay": source_bucket_overlay,
        "selected_overlay_rows": selected_rows,
        "headline": {
            "default_wu_decision_sets": default_rows,
            "default_wu_cities": default_cities,
            "source_sensitive_decision_sets": source_rows,
            "source_sensitive_cities": source_cities,
            "blocked_decision_sets": blocked_rows,
            "blocked_cities": blocked_cities,
        },
        "reuse_contract_update": {
            "grain": "city + event_date + forecast_source/model_version + decision_snapshot_ts_utc",
            "required_source_fields": ["settlement_source_class", "official_station_or_feed", "mapping_rule"],
            "generic_denominator": "default_wu, optionally default_watchlist if explicitly reported",
            "blocked_rule": "exclude blocked_unresolved from source-sensitive feature claims",
            "source_sensitive_rule": "rebuild source features from HKO/WIHH/official station before feature-source claims",
        },
        "limitations": [
            "Decision-price proxy only; no time-aligned orderbook execution claim.",
            "Source buckets become sample-thin in holdout.",
            "The registry is city-level; future market-level rule changes still need pre-entry checks.",
            "Forecast issue/run timestamp is still not materialized in fact_signal_candidates.",
        ],
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "allowed_action": "research/shadow instrumentation only; no live config change",
        },
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default) + "\n")
    out_md.write_text(render_md(payload))
    print(
        json.dumps(
            {
                "out_json": str(out_json),
                "out_md": str(out_md),
                "funnel": payload["funnel"],
                "headline": payload["headline"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
