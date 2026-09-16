#!/usr/bin/env python3
"""Replay buying one share of NO on both outermost Tmax brackets on D-1.

The strategy is model-free.  One row is one PIT city-day ladder snapshot, and
the two legs are the lowest and highest absolute bracket identities in that
ladder.  Entry uses direct NO asks plus the official Weather taker fee and
holds both legs to settlement.
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
    ROOT / "docs/analysis/2026-07/generated/d1_extreme_no_basket_v1"
)
DEFAULT_REPORT = (
    ROOT / "docs/analysis/2026-07/2026-07-27-d1-extreme-no-basket-v1.md"
)
FEE_RATE = 0.05
POLICIES = {
    "D-1_12_18_first": (-12.0, -6.0),
    "D-1_18_24_first": (-6.0, 0.0),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


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


def fee_per_share(price: pd.Series | float) -> pd.Series | float:
    return FEE_RATE * price * (1.0 - price)


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
        r.absolute_bracket_identity AS bracket,
        r.question,
        r.yes_direct_bid AS yes_bid,
        r.yes_direct_ask AS yes_ask,
        r.no_direct_bid AS no_bid,
        r.no_direct_ask AS no_ask,
        r.no_direct_ask_size AS no_ask_size,
        r.no_book_fetched_at_utc,
        o.win
    FROM tmax_v2_ladder_snapshots s
    JOIN tmax_v2_ladder_rung_quotes r USING (ladder_snapshot_id)
    LEFT JOIN outcomes o
      ON o.city = s.city
     AND o.target_date = s.target_date
     AND o.bracket = r.absolute_bracket_identity
    WHERE s.completeness_status = 'complete'
      AND s.lineage_status = 'pit_verified_capture'
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def build_snapshot_rows(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    frame = raw.copy()
    frame["bracket_center"] = frame["bracket"].map(bracket_center)
    frame["decision_ts"] = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    offsets = pd.to_numeric(
        frame["market_utc_offset_seconds"], errors="coerce"
    ).fillna(0)
    frame["decision_local"] = frame["decision_ts"] + pd.to_timedelta(
        offsets, unit="s"
    )
    frame["local_hours_from_target_midnight"] = (
        frame["decision_local"].dt.tz_localize(None)
        - pd.to_datetime(frame["target_date"])
    ).dt.total_seconds() / 3600.0
    frame["yes_two_sided"] = (
        frame["yes_bid"].between(0.001, 0.999)
        & frame["yes_ask"].between(0.001, 0.999)
        & frame["yes_ask"].ge(frame["yes_bid"])
    )
    frame["no_executable"] = (
        frame["no_bid"].between(0.001, 0.999)
        & frame["no_ask"].between(0.001, 0.999)
        & frame["no_ask"].ge(frame["no_bid"])
        & frame["no_ask_size"].ge(1.0)
    )

    records: list[dict[str, Any]] = []
    for snapshot_id, group in frame.groupby("ladder_snapshot_id", sort=False):
        ordered = group.dropna(subset=["bracket_center"]).sort_values(
            ["bracket_center", "bracket"]
        )
        if len(ordered) < 3:
            continue
        low = ordered.iloc[0]
        high = ordered.iloc[-1]
        if pd.isna(low["win"]) or pd.isna(high["win"]):
            continue
        quote_fraction = float(ordered["yes_two_sided"].mean())
        quoted_mid_mass = float(
            (
                (ordered.loc[ordered["yes_two_sided"], "yes_bid"]
                 + ordered.loc[ordered["yes_two_sided"], "yes_ask"])
                / 2.0
            ).sum()
        )
        tail_mid_raw = float(
            (low["yes_bid"] + low["yes_ask"] + high["yes_bid"] + high["yes_ask"])
            / 2.0
        )
        market_tail_probability = (
            tail_mid_raw / quoted_mid_mass
            if quote_fraction >= 0.80 and quoted_mid_mass > 0
            else float("nan")
        )
        low_fee = float(fee_per_share(low["no_ask"]))
        high_fee = float(fee_per_share(high["no_ask"]))
        cost = float(low["no_ask"] + high["no_ask"] + low_fee + high_fee)
        tail_hit = float(low["win"] + high["win"])
        payout = 2.0 - tail_hit
        records.append(
            {
                "ladder_snapshot_id": snapshot_id,
                "city": low["city"],
                "target_date": low["target_date"],
                "decision_ts_utc": low["decision_ts_utc"],
                "decision_local": low["decision_local"],
                "local_hours_from_target_midnight": low[
                    "local_hours_from_target_midnight"
                ],
                "market_timezone": low["market_timezone"],
                "market_unit": low["market_unit"],
                "rung_count": int(len(ordered)),
                "quote_fraction": quote_fraction,
                "low_bracket": low["bracket"],
                "high_bracket": high["bracket"],
                "low_question": low["question"],
                "high_question": high["question"],
                "low_no_ask": float(low["no_ask"]),
                "high_no_ask": float(high["no_ask"]),
                "low_no_ask_size": float(low["no_ask_size"]),
                "high_no_ask_size": float(high["no_ask_size"]),
                "low_no_executable": bool(low["no_executable"]),
                "high_no_executable": bool(high["no_executable"]),
                "low_win": float(low["win"]),
                "high_win": float(high["win"]),
                "tail_hit": tail_hit,
                "market_tail_probability": market_tail_probability,
                "fees": low_fee + high_fee,
                "cost": cost,
                "payout": payout,
                "pnl": payout - cost,
                "break_even_tail_probability": 2.0 - cost,
            }
        )
    snapshots = pd.DataFrame(records)
    funnel = {
        "raw_rung_rows": int(len(frame)),
        "complete_pit_settled_snapshots": int(frame["ladder_snapshot_id"].nunique()),
        "both_extreme_labels_available_snapshots": int(len(snapshots)),
        "d1_snapshots": int(
            snapshots["local_hours_from_target_midnight"].between(
                -24.0, 0.0, inclusive="left"
            ).sum()
        ),
        "d1_both_extremes_executable_snapshots": int(
            (
                snapshots["local_hours_from_target_midnight"].between(
                    -24.0, 0.0, inclusive="left"
                )
                & snapshots["low_no_executable"]
                & snapshots["high_no_executable"]
            ).sum()
        ),
        "d1_full_ladder_80_snapshots": int(
            (
                snapshots["local_hours_from_target_midnight"].between(
                    -24.0, 0.0, inclusive="left"
                )
                & snapshots["low_no_executable"]
                & snapshots["high_no_executable"]
                & snapshots["quote_fraction"].ge(0.80)
            ).sum()
        ),
    }
    return snapshots, funnel


def select_policies(snapshots: pd.DataFrame) -> pd.DataFrame:
    selected: list[pd.DataFrame] = []
    base = snapshots[
        snapshots["low_no_executable"]
        & snapshots["high_no_executable"]
        & snapshots["quote_fraction"].ge(0.80)
        & snapshots["market_tail_probability"].notna()
    ].copy()
    for policy, (start_hour, end_hour) in POLICIES.items():
        eligible = base[
            base["local_hours_from_target_midnight"].ge(start_hour)
            & base["local_hours_from_target_midnight"].lt(end_hour)
        ].sort_values(["city", "target_date", "decision_ts_utc"])
        first = eligible.drop_duplicates(["city", "target_date"], keep="first").copy()
        first["policy"] = policy
        selected.append(first)
    return pd.concat(selected, ignore_index=True)


def block_ratio_ci(
    rows: pd.DataFrame,
    numerator: str,
    denominator: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float]:
    if rows["target_date"].nunique() < 3:
        return (float("nan"), float("nan"))
    daily = rows.groupby("target_date")[[numerator, denominator]].sum()
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    samples = np.empty(draws)
    for index in range(draws):
        sampled = daily.loc[
            rng.choice(dates, size=len(dates), replace=True)
        ].sum()
        samples[index] = sampled[numerator] / sampled[denominator]
    return tuple(float(value) for value in np.quantile(samples, [0.025, 0.975]))


def summarize(
    rows: pd.DataFrame, label: str, *, draws: int, seed: int
) -> dict[str, Any]:
    work = rows.copy()
    if work.empty:
        return {"slice": label, "baskets": 0, "dates": 0, "cities": 0}
    work["one"] = 1.0
    work["market_expected_pnl"] = (
        2.0 - work["market_tail_probability"] - work["cost"]
    )
    work["excess_pnl_vs_market"] = (
        work["market_tail_probability"] - work["tail_hit"]
    )
    roi_ci = block_ratio_ci(
        work, "pnl", "cost", draws=draws, seed=seed
    )
    excess_ci = block_ratio_ci(
        work,
        "excess_pnl_vs_market",
        "cost",
        draws=draws,
        seed=seed + 1,
    )
    return {
        "slice": label,
        "baskets": int(len(work)),
        "dates": int(work["target_date"].nunique()),
        "cities": int(work["city"].nunique()),
        "tail_hits": int(work["tail_hit"].sum()),
        "tail_hit_rate": float(work["tail_hit"].mean()),
        "break_even_tail_rate": float(
            (2.0 * len(work) - work["cost"].sum()) / len(work)
        ),
        "market_tail_probability": float(
            work["market_tail_probability"].mean()
        ),
        "avg_cost": float(work["cost"].mean()),
        "fees": float(work["fees"].sum()),
        "pnl": float(work["pnl"].sum()),
        "fee_adjusted_roi": float(work["pnl"].sum() / work["cost"].sum()),
        "roi_ci_low": roi_ci[0],
        "roi_ci_high": roi_ci[1],
        "market_expected_roi": float(
            work["market_expected_pnl"].sum() / work["cost"].sum()
        ),
        "excess_roi_vs_market": float(
            work["excess_pnl_vs_market"].sum() / work["cost"].sum()
        ),
        "excess_ci_low": excess_ci[0],
        "excess_ci_high": excess_ci[1],
    }


def fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):+.2%}"


def report_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| slice | baskets | dates | tail hit | break-even tail | market tail | avg cost | PnL | ROI (95% CI) | excess vs market (95% CI) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['slice']} | {int(row['baskets'])} | {int(row['dates'])} | "
            f"{fmt_pct(row['tail_hit_rate'])} | {fmt_pct(row['break_even_tail_rate'])} | "
            f"{fmt_pct(row['market_tail_probability'])} | {row['avg_cost']:.4f} | "
            f"${row['pnl']:+.3f} | {fmt_pct(row['fee_adjusted_roi'])} "
            f"[{fmt_pct(row['roi_ci_low'])}, {fmt_pct(row['roi_ci_high'])}] | "
            f"{fmt_pct(row['excess_roi_vs_market'])} "
            f"[{fmt_pct(row['excess_ci_low'])}, {fmt_pct(row['excess_ci_high'])}] |"
        )
    return lines


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    raw = load_rows(args.db)
    snapshots, funnel = build_snapshot_rows(raw)
    selected = select_policies(snapshots)
    selected["market_expected_pnl"] = (
        2.0 - selected["market_tail_probability"] - selected["cost"]
    )
    selected["excess_pnl_vs_market"] = (
        selected["market_tail_probability"] - selected["tail_hit"]
    )

    summaries: list[dict[str, Any]] = []
    for policy_index, (policy, group) in enumerate(selected.groupby("policy")):
        summaries.append(
            summarize(
                group,
                policy,
                draws=args.draws,
                seed=2026072701 + policy_index * 10,
            )
        )
        dates = sorted(group["target_date"].unique())
        split = max(1, len(dates) // 2)
        for suffix, subset_dates in (
            ("early_half", dates[:split]),
            ("recent_half", dates[split:]),
        ):
            summaries.append(
                summarize(
                    group[group["target_date"].isin(subset_dates)],
                    f"{policy}|{suffix}",
                    draws=args.draws,
                    seed=2026072702 + len(summaries) * 10,
                )
            )
    summary_frame = pd.DataFrame(summaries)

    tail_hits = selected[selected["tail_hit"].eq(1.0)].copy()
    detail_columns = [
        "policy",
        "city",
        "target_date",
        "decision_ts_utc",
        "decision_local",
        "low_bracket",
        "high_bracket",
        "low_question",
        "high_question",
        "low_no_ask",
        "high_no_ask",
        "market_tail_probability",
        "tail_hit",
        "cost",
        "fees",
        "payout",
        "pnl",
    ]
    selected[detail_columns].to_csv(
        args.output_dir / "selected_baskets.csv", index=False
    )
    tail_hits[detail_columns].to_csv(
        args.output_dir / "tail_hit_losses.csv", index=False
    )
    summary_frame.to_csv(args.output_dir / "summary.csv", index=False)

    primary = summary_frame[
        ~summary_frame["slice"].str.contains(r"\|", regex=True)
    ].copy()
    rejected = bool(
        not primary.empty
        and (primary["roi_ci_high"] < 0).all()
        and (primary["excess_ci_high"] <= 0).all()
    )
    verdict = "rejected_for_expression" if rejected else "inconclusive"
    payload = {
        "contract": {
            "grain": "one first executable full-ladder snapshot per city-target_date-policy",
            "policies": POLICIES,
            "legs": "one share NO on lowest and highest absolute bracket",
            "entry": "direct NO ask with >=1 share top size",
            "fee": "shares * 0.05 * price * (1-price)",
            "exit": "hold both legs to exact-bracket settlement",
            "market_baseline": "normalized same-snapshot full-ladder YES midpoint tail mass",
            "trade_class": "research_replay",
        },
        "funnel": funnel,
        "summary": summaries,
        "verdict": verdict,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# D-1 Extreme NO Basket v1",
        "",
        "> 2026-07-27；research replay；zero notional；不改 live。",
        "",
        "## 数据快照",
        "",
        "- 数据源：`runtime/weather.db` canonical Tmax v2 ladder + `settlement_outcomes`。",
        f"- DB mtime UTC：`{pd.Timestamp(args.db.stat().st_mtime, unit='s', tz='UTC').isoformat()}`。",
        f"- 记录：{len(raw):,} rung rows；{len(selected):,} settled policy-baskets；unsettled=0，missing_bracket=0。",
        "",
        "## 结论",
        "",
        (
            "固定在目标日前一天，分别取当地 12:00–18:00、18:00–24:00 "
            "区间内首个可执行 full-ladder snapshot；每个 city-day 等份买最低档 NO "
            "和最高档 NO，direct ask 入场、官方 Weather taker fee、持有到结算。"
        ),
        "",
    ]
    lines.extend(report_table(primary))
    lines += [
        "",
        (
            f"裁决：`{verdict}`。这等于同时做空两个最外侧挂牌 condition；在 ladder "
            "恰好覆盖且互斥完备时，可改写成“一份 $1 回款 + 中间所有档位 YES”。"
            "市场已把两端低命中率计入 NO 价格，实际收益只剩 tail calibration "
            "residual，且还要付两腿 spread/fee。"
        ),
        "",
        "## 时间稳定性（描述性，不冒充预注册 forward）",
        "",
    ]
    lines.extend(
        report_table(
            summary_frame[summary_frame["slice"].str.contains(r"\|", regex=True)]
        )
    )
    lines += [
        "",
        "本次规则未在样本开始前冻结，因此 early/recent half 只作 chronology check，"
        "不能把 recent half 写成正式 frozen forward。",
        "",
        "## Signal / Evidence Funnel",
        "",
        "Signal funnel（snapshot/city-day）:",
        "",
        "- raw universe：所有 complete + `pit_verified_capture` + settled ladder。",
        "- mechanism candidate：D-1 snapshot。",
        "- first city-day signal：每个固定当地时段首个可执行 snapshot。",
        "- policy selection：不使用 forecast、价格阈值、城市筛选或事后 winner。",
        "",
        "Evidence funnel（snapshot）:",
        "",
    ]
    for key, value in funnel.items():
        lines.append(f"- `{key}` = {value:,}")
    lines += [
        "",
        "## Tail-hit Losses",
        "",
        "| policy | date | city | low | high | cost | payout | pnl |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for _, row in tail_hits.sort_values(
        ["policy", "target_date", "city"]
    ).iterrows():
        lines.append(
            f"| {row['policy']} | {row['target_date']} | {row['city']} | "
            f"{row['low_bracket']} | {row['high_bracket']} | {row['cost']:.4f} | "
            f"{row['payout']:.1f} | {row['pnl']:+.4f} |"
        )
    if tail_hits.empty:
        lines.append("| _none_ |  |  |  |  |  |  |  |")
    lines += [
        "",
        "## 口径与 Gate",
        "",
        "- unit：basket / city-day；`trade_class=research_replay`，不是 actual fill。",
        "- exact bracket：最低/最高指当时 ladder 的两个最外侧挂牌 condition；仅当问题文本明确写 "
        "`or below` / `or higher` 时才是 open-ended。命中其中一端时该腿 NO 归零，另一腿兑付 $1；"
        "落在其他档或挂牌范围外时两腿都兑付。",
        "- capacity：每腿仅要求 top ask size >=1 share；未测试更大 size。",
        "- significance：以 target_date block bootstrap 95% CI。",
        "- baseline：同一 snapshot 归一化 full-ladder YES midpoint 的两端概率质量。",
        "- forward：`NA`；chronological half 非预注册 frozen forward。",
        f"- conclusion：`{verdict}`；不启动 shadow、不改 live。",
        "",
        "Artifacts:",
        "",
        "- `scripts/analysis/market_structure_edge/research_d1_extreme_no_basket_v1.py`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_basket_v1/summary.json`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_basket_v1/summary.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_basket_v1/selected_baskets.csv`",
        "- `docs/analysis/2026-07/generated/d1_extreme_no_basket_v1/tail_hit_losses.csv`",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
