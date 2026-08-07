#!/usr/bin/env python3
"""Audit PIT ladder coverage and map conditional temperature-market residuals.

This is P0/P1 of market_implied_tail_residual_v1.  It does not fit a weather
model or select a live strategy.  The primary row is the first executable
direct YES quote in a deterministic two-hour local lifecycle bin for one
(city, target_date, exact bracket).  Full-ladder geometry is reported only
when at least 80% of the snapshot's rungs have direct two-sided quotes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/market_implied_tail_residual_v1"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-24-market-implied-tail-residual-p0-p1-v1.md"
)
FEE_RATE = 0.05
RECENT_START = "2026-07-19"
ASK_EDGES = [0.0, 0.03, 0.05, 0.10, 0.20, 0.40, 0.70, 1.001]
ASK_LABELS = ["0-3c", "3-5c", "5-10c", "10-20c", "20-40c", "40-70c", "70c+"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=3000)
    return parser.parse_args()


def lifecycle_bin(local_hours: pd.Series) -> pd.Series:
    edges = [-math.inf, -24, -12, 0, 6, 10, 14, 18, 24, math.inf]
    labels = [
        "D-2_or_earlier",
        "D-1_early",
        "D-1_late",
        "D0_00_06",
        "D0_06_10",
        "D0_10_14",
        "D0_14_18",
        "D0_18_24",
        "post_D0",
    ]
    return pd.cut(local_hours, edges, labels=labels, right=False)


def fee_per_share(price: pd.Series | float) -> pd.Series | float:
    return FEE_RATE * price * (1.0 - price)


def block_ci(
    rows: pd.DataFrame,
    value_columns: tuple[str, str],
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    if rows.empty or rows["target_date"].nunique() < 3:
        return (float("nan"), float("nan"))
    numerator, denominator = value_columns
    daily = rows.groupby("target_date")[[numerator, denominator]].sum()
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for idx in range(draws):
        sampled = daily.loc[rng.choice(dates, size=len(dates), replace=True)].sum()
        samples[idx] = sampled[numerator] / sampled[denominator]
    low, high = np.quantile(samples, [0.025, 0.975])
    return float(low), float(high)


def summarize(rows: pd.DataFrame, label: str, draws: int, seed: int) -> dict[str, Any]:
    if rows.empty:
        return {"slice": label, "rows": 0, "dates": 0, "cities": 0}
    work = rows.copy()
    work["calibration_numerator"] = work["win"] - work["yes_mid"]
    work["one"] = 1.0
    work["cost"] = work["yes_ask"] + fee_per_share(work["yes_ask"])
    work["pnl"] = work["win"] - work["cost"]
    calibration_ci = block_ci(
        work,
        ("calibration_numerator", "one"),
        draws=draws,
        seed=seed,
    )
    roi_ci = block_ci(work, ("pnl", "cost"), draws=draws, seed=seed + 1)
    daily = work.groupby("target_date")[["pnl", "cost"]].sum()
    daily["roi"] = daily["pnl"] / daily["cost"]
    return {
        "slice": label,
        "rows": int(len(work)),
        "dates": int(work["target_date"].nunique()),
        "cities": int(work["city"].nunique()),
        "city_days": int(work[["city", "target_date"]].drop_duplicates().shape[0]),
        "win_rate": float(work["win"].mean()),
        "avg_ask": float(work["yes_ask"].mean()),
        "avg_mid": float(work["yes_mid"].mean()),
        "calibration_residual": float((work["win"] - work["yes_mid"]).mean()),
        "calibration_ci_low": calibration_ci[0],
        "calibration_ci_high": calibration_ci[1],
        "fee_adjusted_roi": float(work["pnl"].sum() / work["cost"].sum()),
        "roi_ci_low": roi_ci[0],
        "roi_ci_high": roi_ci[1],
        "losing_days": int((daily["pnl"] < 0).sum()),
        "days_le_minus_50pct": int((daily["roi"] <= -0.50).sum()),
        "max_daily_loss": float(daily["pnl"].min()),
    }


def load_rows(db: Path) -> pd.DataFrame:
    query = """
    WITH outcomes AS (
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
    ),
    snapshot_quality AS (
        SELECT
            s.ladder_snapshot_id,
            COUNT(*) AS observed_rungs,
            SUM(
                CASE
                    WHEN r.yes_direct_bid BETWEEN 0.001 AND 0.999
                     AND r.yes_direct_ask BETWEEN 0.001 AND 0.999
                    THEN 1 ELSE 0
                END
            ) AS two_sided_rungs,
            SUM(
                CASE
                    WHEN r.yes_direct_bid BETWEEN 0.001 AND 0.999
                     AND r.yes_direct_ask BETWEEN 0.001 AND 0.999
                    THEN (r.yes_direct_bid + r.yes_direct_ask) / 2.0
                    ELSE 0.0
                END
            ) AS quoted_mid_mass
        FROM tmax_v2_ladder_snapshots s
        JOIN tmax_v2_ladder_rung_quotes r USING (ladder_snapshot_id)
        GROUP BY s.ladder_snapshot_id
    )
    SELECT
        s.ladder_snapshot_id,
        s.city,
        s.target_date,
        s.source_snapshot_ts_utc AS decision_ts_utc,
        s.market_utc_offset_seconds,
        s.market_timezone,
        s.market_unit,
        s.rung_count,
        s.completeness_status,
        s.lineage_status,
        r.absolute_bracket_identity AS bracket,
        r.yes_direct_bid AS yes_bid,
        r.yes_direct_ask AS yes_ask,
        r.yes_direct_bid_size AS yes_bid_size,
        r.yes_direct_ask_size AS yes_ask_size,
        r.yes_direct_depth_ask_5c AS yes_depth_ask_5c,
        r.yes_book_status,
        r.yes_book_fetched_at_utc,
        q.two_sided_rungs,
        1.0 * q.two_sided_rungs / s.rung_count AS quote_fraction,
        q.quoted_mid_mass,
        o.win
    FROM tmax_v2_ladder_snapshots s
    JOIN tmax_v2_ladder_rung_quotes r USING (ladder_snapshot_id)
    JOIN snapshot_quality q USING (ladder_snapshot_id)
    JOIN outcomes o
      ON o.city = s.city
     AND o.target_date = s.target_date
     AND o.bracket = r.absolute_bracket_identity
    WHERE s.completeness_status = 'complete'
      AND s.lineage_status = 'pit_verified_capture'
      AND r.yes_direct_bid BETWEEN 0.001 AND 0.999
      AND r.yes_direct_ask BETWEEN 0.001 AND 0.999
      AND r.yes_direct_ask >= r.yes_direct_bid
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        frame = pd.read_sql_query(query, conn)
        identities = pd.read_sql_query(
            """
            SELECT DISTINCT
                s.city,
                s.target_date,
                r.absolute_bracket_identity AS bracket
            FROM tmax_v2_ladder_snapshots s
            JOIN tmax_v2_ladder_rung_quotes r USING (ladder_snapshot_id)
            WHERE s.completeness_status = 'complete'
              AND s.lineage_status = 'pit_verified_capture'
            """,
            conn,
        )
    finally:
        conn.close()
    identities["bracket_center"] = identities["bracket"].map(bracket_center)
    identities["bracket_rank"] = identities.groupby(["city", "target_date"])[
        "bracket_center"
    ].rank(method="dense")
    return frame.merge(
        identities,
        on=["city", "target_date", "bracket"],
        how="left",
        validate="many_to_one",
    )


def prepare_rows(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = raw.copy()
    frame["decision_ts"] = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    offsets = pd.to_numeric(frame["market_utc_offset_seconds"], errors="coerce").fillna(0)
    frame["decision_local"] = frame["decision_ts"] + pd.to_timedelta(offsets, unit="s")
    target_midnight = pd.to_datetime(frame["target_date"])
    frame["local_hours_from_target_midnight"] = (
        frame["decision_local"].dt.tz_localize(None) - target_midnight
    ).dt.total_seconds() / 3600.0
    frame["lifecycle"] = lifecycle_bin(frame["local_hours_from_target_midnight"])
    frame["yes_mid"] = (frame["yes_bid"] + frame["yes_ask"]) / 2.0
    frame["spread"] = frame["yes_ask"] - frame["yes_bid"]
    frame["ask_bucket"] = pd.cut(
        frame["yes_ask"],
        ASK_EDGES,
        labels=ASK_LABELS,
        right=False,
    )
    frame["full_ladder_80"] = frame["quote_fraction"] >= 0.80
    frame["normalized_mid"] = np.where(
        frame["full_ladder_80"] & frame["quoted_mid_mass"].gt(0),
        frame["yes_mid"] / frame["quoted_mid_mass"],
        np.nan,
    )
    frame["quote_age_minutes"] = (
        frame["decision_ts"]
        - pd.to_datetime(frame["yes_book_fetched_at_utc"], utc=True, errors="coerce")
    ).dt.total_seconds() / 60.0
    snapshot_groups = frame.groupby("ladder_snapshot_id", sort=False)
    mode_indices = snapshot_groups["yes_mid"].idxmax()
    mode_ranks = frame.loc[mode_indices, ["ladder_snapshot_id", "bracket_rank"]].rename(
        columns={"bracket_rank": "mode_rank"}
    )
    frame = frame.merge(mode_ranks, on="ladder_snapshot_id", how="left")
    frame["steps_from_mode"] = frame["bracket_rank"] - frame["mode_rank"]
    ordered = frame.sort_values(["ladder_snapshot_id", "bracket_rank"]).copy()
    ordered_groups = ordered.groupby("ladder_snapshot_id", sort=False)
    ordered["previous_rung_mid"] = ordered_groups["yes_mid"].shift()
    ordered["next_rung_mid"] = ordered_groups["yes_mid"].shift(-1)
    ordered["neighbor_ratio"] = ordered["yes_mid"] / np.sqrt(
        ordered["previous_rung_mid"] * ordered["next_rung_mid"]
    )
    frame = ordered

    # Deterministic denominator: first executable direct quote in each
    # two-hour local bin. This prevents high-frequency snapshots from
    # overweighting a city-day without selecting a hindsight-best price.
    frame["lifecycle_2h"] = np.floor(frame["local_hours_from_target_midnight"] / 2.0) * 2.0
    frame = frame.sort_values(
        ["city", "target_date", "bracket", "lifecycle_2h", "decision_ts"]
    )
    fixed = frame.drop_duplicates(
        ["city", "target_date", "bracket", "lifecycle_2h"],
        keep="first",
    ).copy()
    fixed = fixed.sort_values(["city", "target_date", "bracket", "decision_ts"])
    group = fixed.groupby(["city", "target_date", "bracket"], sort=False)
    fixed["previous_mid"] = group["yes_mid"].shift()
    fixed["mid_change"] = fixed["yes_mid"] - fixed["previous_mid"]
    fixed["price_path"] = pd.cut(
        fixed["mid_change"],
        [-math.inf, -0.02, 0.02, math.inf],
        labels=["falling_2c_plus", "stable", "rising_2c_plus"],
        right=False,
    )
    return frame, fixed


def bracket_center(value: Any) -> float:
    numbers = [
        float(item)
        for item in re.findall(r"(?<!\d)-?\d+(?:\.\d+)?", str(value))
    ]
    if not numbers:
        return float("nan")
    if len(numbers) >= 2 and "-" in str(value):
        return (numbers[0] + numbers[1]) / 2.0
    return numbers[0]


def mode_distance_bucket(values: pd.Series) -> pd.Series:
    return pd.cut(
        values,
        [-math.inf, -2.5, -1.5, -0.5, 0.5, 1.5, 2.5, math.inf],
        labels=[
            "cold_3plus",
            "cold_2",
            "cold_1",
            "mode",
            "hot_1",
            "hot_2",
            "hot_3plus",
        ],
        right=False,
    )


def kink_bucket(values: pd.Series) -> pd.Series:
    return pd.cut(
        values,
        [0.0, 0.67, 0.90, 1.10, 1.50, math.inf],
        labels=[
            "deep_discount",
            "discount",
            "aligned",
            "premium",
            "strong_premium",
        ],
        right=False,
    )


def slice_table(
    rows: pd.DataFrame,
    columns: list[str],
    *,
    draws: int,
    min_rows: int,
    seed: int,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    grouped = rows.groupby(columns, observed=True, dropna=False)
    for index, group in grouped:
        if len(group) < min_rows:
            continue
        values = index if isinstance(index, tuple) else (index,)
        label = "|".join(str(value) for value in values)
        record = summarize(group, label, draws, seed + len(records) * 2)
        for column, value in zip(columns, values):
            record[column] = value
        records.append(record)
    return pd.DataFrame(records)


def coverage_table(raw: pd.DataFrame, fixed: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for target_date, group in raw.groupby("target_date"):
        fixed_group = fixed[fixed["target_date"].eq(target_date)]
        records.append(
            {
                "target_date": target_date,
                "direct_quote_rows_raw": int(len(group)),
                "fixed_2h_rows": int(len(fixed_group)),
                "cities": int(group["city"].nunique()),
                "city_days": int(group[["city", "target_date"]].drop_duplicates().shape[0]),
                "snapshots": int(group["ladder_snapshot_id"].nunique()),
                "full_ladder_80_snapshots": int(
                    group.loc[group["full_ladder_80"], "ladder_snapshot_id"].nunique()
                ),
                "avg_quote_fraction": float(
                    group.drop_duplicates("ladder_snapshot_id")["quote_fraction"].mean()
                ),
                "median_quote_age_minutes": float(group["quote_age_minutes"].median()),
            }
        )
    return pd.DataFrame(records)


def fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):+.1%}"


def report_table(frame: pd.DataFrame, group_columns: list[str], limit: int | None = None) -> list[str]:
    if frame.empty:
        return ["_No slice met the minimum row count._"]
    view = frame if limit is None else frame.head(limit)
    headers = group_columns + [
        "rows",
        "dates",
        "cities",
        "win",
        "ask",
        "residual",
        "residual CI",
        "taker ROI",
        "ROI CI",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in view.iterrows():
        group_values = [str(row[column]) for column in group_columns]
        lines.append(
            "| "
            + " | ".join(
                group_values
                + [
                    str(int(row["rows"])),
                    str(int(row["dates"])),
                    str(int(row["cities"])),
                    fmt_pct(row["win_rate"]),
                    fmt_pct(row["avg_ask"]),
                    fmt_pct(row["calibration_residual"]),
                    f"[{fmt_pct(row['calibration_ci_low'])}, {fmt_pct(row['calibration_ci_high'])}]",
                    fmt_pct(row["fee_adjusted_roi"]),
                    f"[{fmt_pct(row['roi_ci_low'])}, {fmt_pct(row['roi_ci_high'])}]",
                ]
            )
            + " |"
        )
    return lines


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    raw = load_rows(args.db)
    expanded, fixed = prepare_rows(raw)
    coverage = coverage_table(expanded, fixed)

    lifecycle_price = slice_table(
        fixed,
        ["lifecycle", "ask_bucket"],
        draws=args.draws,
        min_rows=40,
        seed=2026072401,
    )
    lifecycle = slice_table(
        fixed,
        ["lifecycle"],
        draws=args.draws,
        min_rows=80,
        seed=2026072402,
    )
    price_path = slice_table(
        fixed.dropna(subset=["price_path"]),
        ["lifecycle", "price_path"],
        draws=args.draws,
        min_rows=40,
        seed=2026072403,
    )
    ladder_quality = slice_table(
        fixed.assign(
            ladder_coverage=np.where(fixed["full_ladder_80"], "full_80pct", "partial")
        ),
        ["ladder_coverage", "ask_bucket"],
        draws=args.draws,
        min_rows=40,
        seed=2026072404,
    )
    geometry = fixed[
        fixed["full_ladder_80"]
        & fixed["normalized_mid"].notna()
        & fixed["steps_from_mode"].notna()
    ].copy()
    geometry["mode_distance"] = mode_distance_bucket(geometry["steps_from_mode"])
    geometry["raw_yes_mid"] = geometry["yes_mid"]
    geometry["yes_mid"] = geometry["normalized_mid"]
    mode_distance = slice_table(
        geometry,
        ["lifecycle", "mode_distance"],
        draws=args.draws,
        min_rows=40,
        seed=2026072405,
    )
    geometry["kink"] = kink_bucket(geometry["neighbor_ratio"])
    ladder_kink = slice_table(
        geometry.dropna(subset=["kink"]),
        ["mode_distance", "kink"],
        draws=args.draws,
        min_rows=40,
        seed=2026072406,
    )
    windows = []
    for label, group in (
        ("all", fixed),
        ("early_2026-07-11_to_18", fixed[fixed["target_date"] < RECENT_START]),
        ("recent_2026-07-19_to_23", fixed[fixed["target_date"] >= RECENT_START]),
        ("lottery_ask_le_20c", fixed[fixed["yes_ask"] <= 0.20]),
        (
            "recent_lottery_ask_le_20c",
            fixed[(fixed["target_date"] >= RECENT_START) & (fixed["yes_ask"] <= 0.20)],
        ),
    ):
        windows.append(summarize(group, label, args.draws, 2026072450 + len(windows) * 2))
    window_frame = pd.DataFrame(windows)

    coverage.to_csv(args.output_dir / "coverage_by_target_date.csv", index=False)
    lifecycle.to_csv(args.output_dir / "calibration_by_lifecycle.csv", index=False)
    lifecycle_price.to_csv(args.output_dir / "calibration_by_lifecycle_price.csv", index=False)
    price_path.to_csv(args.output_dir / "calibration_by_price_path.csv", index=False)
    ladder_quality.to_csv(args.output_dir / "calibration_by_ladder_quality.csv", index=False)
    mode_distance.to_csv(args.output_dir / "calibration_by_mode_distance.csv", index=False)
    ladder_kink.to_csv(args.output_dir / "calibration_by_ladder_kink.csv", index=False)
    window_frame.to_csv(args.output_dir / "window_summary.csv", index=False)

    lottery = lifecycle_price[
        lifecycle_price["ask_bucket"].astype(str).isin(["0-3c", "3-5c", "5-10c", "10-20c"])
    ].copy()
    positive_lottery = lottery[
        (lottery["calibration_ci_low"] > 0)
        & (lottery["roi_ci_low"] > 0)
        & (lottery["dates"] >= 5)
    ].sort_values("fee_adjusted_roi", ascending=False)

    summary = {
        "contract": {
            "grain": "first direct executable YES quote per city-target_date-bracket-local_2h_bin",
            "labels": "canonical settled exact bracket",
            "entry": "direct YES ask at PIT snapshot",
            "fee": "shares * 0.05 * price * (1-price)",
            "exit": "hold to settlement",
            "full_ladder_geometry_min_quote_fraction": 0.80,
            "recent_start": RECENT_START,
            "no_live_change": True,
        },
        "inventory": {
            "raw_direct_quote_rows": int(len(expanded)),
            "fixed_2h_rows": int(len(fixed)),
            "dates": int(fixed["target_date"].nunique()),
            "cities": int(fixed["city"].nunique()),
            "min_target_date": str(fixed["target_date"].min()),
            "max_target_date": str(fixed["target_date"].max()),
            "full_ladder_80_rows": int(fixed["full_ladder_80"].sum()),
        },
        "windows": window_frame.to_dict(orient="records"),
        "positive_lottery_cells": positive_lottery.to_dict(orient="records"),
        "verdict": "inconclusive",
        "verdict_reason": (
            "p1_positive_lottery_cell_requires_p2_oof_confirmation"
            if not positive_lottery.empty
            else "no_stable_p1_lifecycle_price_lottery_cell"
        ),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    all_row = window_frame[window_frame["slice"].eq("all")].iloc[0]
    lottery_row = window_frame[window_frame["slice"].eq("lottery_ask_le_20c")].iloc[0]
    recent_lottery = window_frame[
        window_frame["slice"].eq("recent_lottery_ask_le_20c")
    ].iloc[0]
    lines = [
        "# Market-Implied Tail Residual P0/P1 v1",
        "",
        "> 2026-07-24; research-only; zero notional; no live change.",
        "",
        "## 结论",
        "",
        (
            f"完整 ladder canonical 已增量补到 `{summary['inventory']['max_target_date']}`。"
            f"共同已结算 PIT 分母为 {summary['inventory']['dates']} 个 target dates、"
            f"{summary['inventory']['cities']} 城、{summary['inventory']['fixed_2h_rows']:,} 条固定两小时机会行。"
        ),
        "",
        (
            f"在该分母上，所有 exact-bracket YES 的 fee-adjusted taker ROI 为 "
            f"{fmt_pct(all_row['fee_adjusted_roi'])}；ask<=20c 彩票为 "
            f"{fmt_pct(lottery_row['fee_adjusted_roi'])}，recent 7/19..7/23 为 "
            f"{fmt_pct(recent_lottery['fee_adjusted_roi'])}。"
        ),
        "",
        (
            "P1 没有发现同时满足 `>=5 target dates`、calibration residual CI>0、"
            f"fee-adjusted ROI CI>0 的生命周期×价格彩票格；候选格数量={len(positive_lottery)}。"
            "因此当前结论是 `inconclusive`，不能从静态市场形态直接造 selector。"
        ),
        "",
        "## P0 覆盖",
        "",
        "| target_date | raw quotes | fixed 2h rows | cities | snapshots | full ladder >=80% | avg quote fraction |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in coverage.iterrows():
        lines.append(
            f"| {row['target_date']} | {int(row['direct_quote_rows_raw'])} | "
            f"{int(row['fixed_2h_rows'])} | {int(row['cities'])} | {int(row['snapshots'])} | "
            f"{int(row['full_ladder_80_snapshots'])} | {row['avg_quote_fraction']:.1%} |"
        )
    lines += [
        "",
        "解释：7/11..7/13 只有被 orderbook budget 选中的少量 rung 有 direct quote；"
        "7/15 起 full-ladder coverage 才明显改善。缺报价是 evidence coverage gap，不是策略过滤。",
        "",
        "## 生命周期校准",
        "",
    ]
    lines.extend(report_table(lifecycle, ["lifecycle"]))
    lines += [
        "",
        "## 彩票价格带",
        "",
    ]
    lines.extend(report_table(lottery, ["lifecycle", "ask_bucket"]))
    lines += [
        "",
        "## Full-Ladder Geometry",
        "",
        "只在同一 snapshot 至少 80% rung 有 two-sided direct quote 时计算；"
        "概率基线是该 snapshot 内归一化 YES midpoint。`hot_1/hot_2/hot_3plus`"
        "表示比盘口概率众数高一格、两格、三格以上，不是 forecast distance。",
        "",
    ]
    lines.extend(report_table(mode_distance, ["lifecycle", "mode_distance"]))
    lines += [
        "",
        "## 价格路径",
        "",
        "这里的 rising/falling 只使用同一 bracket 上一个固定两小时 checkpoint，"
        "不使用未来 max bid 或事后最佳价格。",
        "",
    ]
    lines.extend(report_table(price_path, ["lifecycle", "price_path"]))
    lines += [
        "",
        "## 研究裁决",
        "",
        "- broad cheap YES 不是 alpha；是否存在 residual 必须由下一阶段 market-only model 在 OOF 概率质量上证明。",
        "- 当前只有 12 个 settled target dates，且 full-ladder 高质量覆盖主要在 7/15 后；不能据单格点估创建 hard gate。",
        "- P2 应在同一固定分母比较 raw market mid 与 full-ladder market-only model；先不加入 forecast/weather。",
        "- P2 通过后再做 P3 weather uplift，才能区分 market/base-rate 与 forecast/source alpha。",
        "- verdict=`inconclusive`; 保持 zero-notional research，不改 HeadA shadow/live。",
        "",
        "Artifacts:",
        "",
        "- `docs/analysis/2026-07/generated/market_implied_tail_residual_v1/summary.json`",
        "- `docs/analysis/2026-07/generated/market_implied_tail_residual_v1/coverage_by_target_date.csv`",
        "- `docs/analysis/2026-07/generated/market_implied_tail_residual_v1/calibration_by_lifecycle_price.csv`",
        "- `docs/analysis/2026-07/generated/market_implied_tail_residual_v1/calibration_by_mode_distance.csv`",
        "- `docs/analysis/2026-07/generated/market_implied_tail_residual_v1/calibration_by_ladder_kink.csv`",
        "- `docs/analysis/2026-07/generated/market_implied_tail_residual_v1/calibration_by_price_path.csv`",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
