#!/usr/bin/env python3
"""Replay D-1 extreme-NO baskets on the full captured snapshot history.

Older v1-v3 work used the newer Tmax V2 canonical ladder/curve intersection,
which starts in July.  This replay recovers the earlier, immutable paper
snapshot boundary directly.  Since 2026-05-19 those snapshots contain direct
YES/NO books and a capture-time ``forecast_max_f``.  The forecast value is PIT
at the snapshot boundary; its run timestamp remains estimated and is not
mislabelled as verified hourly-curve lineage.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_d1_extreme_no_basket_v1 as base  # noqa: E402
import research_d1_extreme_no_tail_probability_v3 as probability  # noqa: E402
from weather_data_feed import city_timezone_name  # noqa: E402
from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402


DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_SNAPSHOTS = historical_strategy_snapshots()
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-27-d1-extreme-no-snapshot-history-v4.md"
)
DIRECT_FIELDS = tuple(
    f"{side}_{field}"
    for side in ("yes", "no")
    for field in (
        "token_id",
        "best_bid",
        "best_ask",
        "bid_size",
        "ask_size",
        "depth_bid_5c",
        "depth_ask_5c",
        "depth_bid_10c",
        "depth_ask_10c",
        "book_status",
    )
)
START_CAPTURE_DATE = "20260519"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOTS
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def finite_number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def valid_price(value: Any) -> bool:
    number = finite_number(value)
    return math.isfinite(number) and 0.001 <= number <= 0.999


def canonical_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def first_present(records: list[dict[str, Any]], field: str) -> Any:
    for record in records:
        value = record.get(field)
        if value is not None and str(value).strip():
            return value
    return None


def complete_direct_ladder(records: list[dict[str, Any]]) -> bool:
    if not records:
        return False
    return all(
        record.get("condition_id")
        and record.get("market_id")
        and record.get("bracket") is not None
        and all(field in record for field in DIRECT_FIELDS)
        for record in records
    )


def snapshot_groups(path_text: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    path = Path(path_text)
    counts: Counter[str] = Counter(files_read=1)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], {"files_read": 1, "files_invalid": 1}
    snapshot_dt = canonical_utc(payload.get("ts_utc"))
    records = payload.get("records")
    if snapshot_dt is None or not isinstance(records, list):
        return [], {"files_read": 1, "files_invalid": 1}
    counts["record_rows"] += len(records)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for record in records:
        if not isinstance(record, dict):
            continue
        city = str(record.get("city") or "").strip()
        target_date = str(
            record.get("target_date") or record.get("event_date") or ""
        ).strip()
        bracket = str(record.get("bracket") or "").strip()
        if not city or not target_date or not bracket:
            continue
        event = str(record.get("event_slug") or f"{city}|{target_date}")
        grouped[(city, target_date, event)].append(record)

    outputs: list[dict[str, Any]] = []
    for (city, target_date, event), group in grouped.items():
        counts["ladder_groups"] += 1
        tz_name = city_timezone_name(city)
        if not tz_name:
            counts["missing_timezone_groups"] += 1
            continue
        try:
            local_dt = snapshot_dt.astimezone(ZoneInfo(tz_name))
            target_midnight = datetime.fromisoformat(target_date)
        except (ValueError, KeyError):
            counts["invalid_clock_groups"] += 1
            continue
        local_hour = (
            local_dt.replace(tzinfo=None) - target_midnight
        ).total_seconds() / 3600.0
        if not -24.0 <= local_hour < 0.0:
            continue
        counts["d1_ladder_groups"] += 1
        if not complete_direct_ladder(group):
            counts["d1_incomplete_direct_groups"] += 1
            continue
        by_bracket: dict[str, dict[str, Any]] = {}
        duplicate = False
        for record in group:
            bracket = str(record["bracket"]).strip()
            if bracket in by_bracket:
                duplicate = True
                break
            by_bracket[bracket] = record
        if duplicate or len(by_bracket) < 3:
            counts["d1_invalid_ladder_groups"] += 1
            continue
        ordered = sorted(
            by_bracket.values(),
            key=lambda record: (
                base.bracket_center(record.get("bracket")),
                str(record.get("bracket")),
            ),
        )
        if not all(
            math.isfinite(base.bracket_center(record.get("bracket")))
            for record in ordered
        ):
            counts["d1_invalid_ladder_groups"] += 1
            continue
        low, high = ordered[0], ordered[-1]
        yes_two_sided = [
            valid_price(record.get("yes_best_bid"))
            and valid_price(record.get("yes_best_ask"))
            and finite_number(record.get("yes_best_ask"))
            >= finite_number(record.get("yes_best_bid"))
            for record in ordered
        ]
        quote_fraction = sum(yes_two_sided) / len(ordered)
        quoted_mid_mass = sum(
            (
                finite_number(record.get("yes_best_bid"))
                + finite_number(record.get("yes_best_ask"))
            )
            / 2.0
            for record, valid in zip(ordered, yes_two_sided)
            if valid
        )
        low_yes_mid = (
            finite_number(low.get("yes_best_bid"))
            + finite_number(low.get("yes_best_ask"))
        ) / 2.0
        high_yes_mid = (
            finite_number(high.get("yes_best_bid"))
            + finite_number(high.get("yes_best_ask"))
        ) / 2.0
        market_tail_probability = (
            (low_yes_mid + high_yes_mid) / quoted_mid_mass
            if quote_fraction >= 0.80
            and quoted_mid_mass > 0
            and math.isfinite(low_yes_mid)
            and math.isfinite(high_yes_mid)
            else math.nan
        )
        low_no_ask = finite_number(low.get("no_best_ask"))
        high_no_ask = finite_number(high.get("no_best_ask"))
        low_no_ask_size = finite_number(low.get("no_ask_size"))
        high_no_ask_size = finite_number(high.get("no_ask_size"))
        low_no_executable = (
            valid_price(low.get("no_best_bid"))
            and valid_price(low.get("no_best_ask"))
            and low_no_ask >= finite_number(low.get("no_best_bid"))
            and low_no_ask_size >= 1.0
        )
        high_no_executable = (
            valid_price(high.get("no_best_bid"))
            and valid_price(high.get("no_best_ask"))
            and high_no_ask >= finite_number(high.get("no_best_bid"))
            and high_no_ask_size >= 1.0
        )
        forecast_max_f = finite_number(
            first_present(group, "forecast_max_f")
            or first_present(group, "gfs_forecast_f")
        )
        unit = str(first_present(group, "unit") or "F").upper()
        low_fee = (
            float(base.fee_per_share(low_no_ask))
            if valid_price(low_no_ask)
            else math.nan
        )
        high_fee = (
            float(base.fee_per_share(high_no_ask))
            if valid_price(high_no_ask)
            else math.nan
        )
        cost = low_no_ask + high_no_ask + low_fee + high_fee
        outputs.append(
            {
                "snapshot_key": (
                    f"{path.name}|{city}|{target_date}|{event}"
                ),
                "source_path": str(path),
                "city": city,
                "target_date": target_date,
                "decision_ts_utc": snapshot_dt.isoformat().replace(
                    "+00:00", "Z"
                ),
                "decision_local": local_dt.isoformat(),
                "local_hours_from_target_midnight": local_hour,
                "market_timezone": tz_name,
                "market_unit": unit,
                "rung_count": len(ordered),
                "quote_fraction": quote_fraction,
                "low_bracket": str(low.get("bracket")),
                "high_bracket": str(high.get("bracket")),
                "low_question": low.get("question"),
                "high_question": high.get("question"),
                "low_no_ask": low_no_ask,
                "high_no_ask": high_no_ask,
                "low_no_ask_size": low_no_ask_size,
                "high_no_ask_size": high_no_ask_size,
                "low_no_executable": low_no_executable,
                "high_no_executable": high_no_executable,
                "market_tail_probability": market_tail_probability,
                "fees": low_fee + high_fee,
                "cost": cost,
                "break_even_tail_probability": 2.0 - cost,
                "forecast_source": first_present(group, "forecast_source"),
                "forecast_model": first_present(group, "forecast_model"),
                "forecast_max_f": forecast_max_f,
                "forecast_pit_valid": math.isfinite(forecast_max_f),
                "forecast_available_at_utc": snapshot_dt.isoformat().replace(
                    "+00:00", "Z"
                ),
                "forecast_lineage_status": (
                    "pit_snapshot_capture_run_estimated"
                    if math.isfinite(forecast_max_f)
                    else "missing_snapshot_forecast"
                ),
                "model_init_utc_estimated": first_present(
                    group, "model_init_utc_estimated"
                ),
            }
        )
        counts["d1_complete_direct_groups"] += 1
    return outputs, dict(counts)


def load_outcomes(db: Path) -> pd.DataFrame:
    query = """
    SELECT
        city,
        target_date,
        bracket,
        MAX(
            CASE
                WHEN final_price >= 0.999 THEN 1.0
                WHEN final_price <= 0.001 THEN 0.0
                ELSE final_price
            END
        ) AS win
    FROM settlement_outcomes
    WHERE settlement_status = 'settled'
    GROUP BY city, target_date, bracket
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def attach_outcomes(rows: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    low = outcomes.rename(
        columns={"bracket": "low_bracket", "win": "low_win"}
    )
    high = outcomes.rename(
        columns={"bracket": "high_bracket", "win": "high_win"}
    )
    out = rows.merge(
        low,
        on=["city", "target_date", "low_bracket"],
        how="left",
        validate="many_to_one",
    ).merge(
        high,
        on=["city", "target_date", "high_bracket"],
        how="left",
        validate="many_to_one",
    )
    out = out[out["low_win"].notna() & out["high_win"].notna()].copy()
    out["tail_hit"] = out["low_win"] + out["high_win"]
    out["payout"] = 2.0 - out["tail_hit"]
    out["pnl"] = out["payout"] - out["cost"]
    return out


def scan_snapshots(
    snapshot_dir: Path, workers: int
) -> tuple[pd.DataFrame, dict[str, int], int]:
    paths = [
        path
        for path in sorted(snapshot_dir.glob("snapshot_*.json"))
        if path.name[9:17] >= START_CAPTURE_DATE
    ]
    all_rows: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for rows, counts in executor.map(
            snapshot_groups, map(str, paths), chunksize=8
        ):
            all_rows.extend(rows)
            totals.update(counts)
    return pd.DataFrame(all_rows), dict(totals), len(paths)


def report_probability_table(frame: pd.DataFrame) -> list[str]:
    return probability.probability_table(frame)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    raw, scan_funnel, scanned_files = scan_snapshots(
        args.snapshot_dir, args.workers
    )
    settled = attach_outcomes(raw, load_outcomes(args.db))
    executable = base.select_policies(settled)
    calibration = probability.calibration_universe(
        probability.add_model_features(settled)
    )
    evaluation = probability.add_model_features(executable)
    evaluation = evaluation[
        probability.forecast_ready_mask(evaluation)
    ].copy()
    scored, training_audit = probability.expanding_oof(
        calibration, evaluation
    )
    if scored.empty:
        raise RuntimeError("no expanding-OOF rows could be scored")

    probability_rows = [
        probability.probability_summary(
            scored,
            policy,
            draws=args.draws,
            seed=probability.SEED + 100 + index * 10,
        )
        for index, policy in enumerate(sorted(scored["policy"].unique()))
    ]
    probability_frame = pd.DataFrame(probability_rows)
    trade_frame, selected_frame = probability.trade_summaries(
        scored, draws=args.draws
    )
    baseline_rows = [
        base.summarize(
            group,
            policy,
            draws=args.draws,
            seed=probability.SEED + 200 + index * 10,
        )
        for index, (policy, group) in enumerate(
            executable.groupby("policy")
        )
    ]
    baseline_frame = pd.DataFrame(baseline_rows)

    probability_frame.to_csv(
        args.output_dir / "probability_summary.csv", index=False
    )
    trade_frame.to_csv(args.output_dir / "trade_summary.csv", index=False)
    baseline_frame.to_csv(
        args.output_dir / "baseline_summary.csv", index=False
    )
    executable.to_csv(
        args.output_dir / "executable_baskets.csv", index=False
    )
    training_audit.to_csv(
        args.output_dir / "training_audit.csv", index=False
    )
    scored.to_csv(args.output_dir / "oof_scored_baskets.csv", index=False)
    selected_frame.to_csv(
        args.output_dir / "selected_baskets.csv", index=False
    )

    probability_pass = bool(
        (probability_frame["logloss_delta_ci_high"] < 0).all()
        and (probability_frame["brier_delta_ci_high"] < 0).all()
    )
    primary = trade_frame[
        trade_frame["selector"].eq("forecast_ev_buffer_50bp")
    ]
    trade_pass = bool(
        not primary.empty
        and primary["baskets"].gt(0).all()
        and primary["roi_ci_low"].gt(0).all()
        and primary["excess_ci_low"].gt(0).all()
    )
    verdict = (
        "shadow_candidate"
        if probability_pass and trade_pass
        else "inconclusive"
    )
    funnel = {
        "all_snapshot_files": len(
            list(args.snapshot_dir.glob("snapshot_*.json"))
        ),
        "direct_schema_window_files": scanned_files,
        **scan_funnel,
        "settled_d1_complete_direct_snapshots": int(len(settled)),
        "settled_target_dates": int(settled["target_date"].nunique()),
        "settled_city_dates": int(
            settled[["city", "target_date"]].drop_duplicates().shape[0]
        ),
        "base_executable_baskets": int(len(executable)),
        "base_executable_dates": int(
            executable["target_date"].nunique()
        ),
        "calibration_rows": int(len(calibration)),
        "calibration_dates": int(calibration["target_date"].nunique()),
        "calibration_tail_hits": int(calibration["tail_hit"].sum()),
        "oof_scored_baskets": int(len(scored)),
        "oof_scored_dates": int(scored["target_date"].nunique()),
        "oof_tail_hits": int(scored["tail_hit"].sum()),
    }
    payload = {
        "contract": {
            "grain": "first D-1 captured direct-book ladder per city-target_date-policy",
            "capture_window": "snapshot files from 2026-05-19; target-date local D-1 12-18 and 18-24",
            "legs": "one share direct-ask NO on lowest and highest absolute listed brackets",
            "forecast": "forecast_max_f present in the immutable snapshot at decision time",
            "forecast_lineage": "PIT capture verified; forecast run timestamp estimated, not full hourly-curve lineage",
            "model": (
                f"strict earlier-target-date expanding OOF "
                f"L2 logistic C={probability.MODEL_C}"
            ),
            "market_baseline": "same-snapshot normalized full-ladder YES midpoint tail mass",
            "fee": "official Weather feeRate 0.05 on both NO legs",
            "trade_class": "research_replay",
        },
        "funnel": funnel,
        "baseline_summary": baseline_rows,
        "probability_summary": probability_rows,
        "trade_summary": trade_frame.to_dict("records"),
        "gates": {
            "probability_vs_market": (
                "PASS" if probability_pass else "FAIL"
            ),
            "trade_significance": "PASS" if trade_pass else "FAIL",
            "frozen_forward": "NA_post_hoc_expanding_oof",
            "conclusion": verdict,
        },
        "verdict": verdict,
        "action": (
            "zero_notional_shadow_only"
            if verdict == "shadow_candidate"
            else "do_not_shadow_or_live"
        ),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            probability.json_ready(payload),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# D-1 Extreme NO Full Snapshot History v4",
        "",
        "> 2026-07-27；research replay；zero notional；不改 live。",
        "",
        "## 结论",
        "",
        "此前 6–12 天是 `tmax_v2` ladder 与严格 hourly-curve lineage 的交集，"
        "不是历史数据总量。本版回到 immutable paper snapshot：5 月 19 日起已有"
        " direct YES/NO book，同时保留 decision-time `forecast_max_f`，因此可在"
        "更长的同分母上重跑。",
        "",
        "### 不加 forecast 的机械两端 NO",
        "",
    ]
    lines.extend(base.report_table(baseline_frame))
    lines += [
        "",
        "### Forecast expanding-OOF 概率层",
        "",
    ]
    lines.extend(report_probability_table(probability_frame))
    lines += [
        "",
        "负 delta 才表示 forecast 模型在同 rows 上优于 market tail probability。",
        "",
        "### Forecast EV 交易层",
        "",
    ]
    lines.extend(probability.trade_table(trade_frame))
    lines += [
        "",
        f"裁决：`{verdict}`。probability-vs-market="
        f"`{'PASS' if probability_pass else 'FAIL'}`；trade significance="
        f"`{'PASS' if trade_pass else 'FAIL'}`；frozen forward=`NA`。",
        "",
        "## 数据与血缘",
        "",
        f"- snapshot 文件：总计 {funnel['all_snapshot_files']:,}；direct schema "
        f"窗口扫描 {funnel['direct_schema_window_files']:,}。",
        f"- settled D-1 complete-direct snapshots："
        f"{funnel['settled_d1_complete_direct_snapshots']:,}；"
        f"{funnel['settled_target_dates']} target dates；"
        f"{funnel['settled_city_dates']:,} city-days。",
        f"- executable first-window baskets：{len(executable):,}；"
        f"{funnel['base_executable_dates']} target dates。",
        f"- expanding-OOF：{len(scored):,} baskets；"
        f"{funnel['oof_scored_dates']} target dates；"
        f"{funnel['oof_tail_hits']} tail hits。",
        "- ladder/book/forecast 值来自同一 immutable snapshot；snapshot ts 是"
        " capture availability boundary。",
        "- `model_init_utc_estimated` 仍只是 estimated run lineage；本报告没有把"
        " point forecast 冒充 verified hourly curve。",
        "- settlement 只在 PIT 候选生成后从 canonical `settlement_outcomes` join。",
        "- significance 以 target_date block bootstrap；actual fill=0。",
        "",
        "Artifacts:",
        "",
        "- `scripts/analysis/market_structure_edge/research_d1_extreme_no_snapshot_history_v4.py`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/summary.json`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/baseline_summary.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/probability_summary.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/trade_summary.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/oof_scored_baskets.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/training_audit.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_snapshot_history_v4/selected_baskets.csv`",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            probability.json_ready(payload),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
