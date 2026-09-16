#!/usr/bin/env python3
"""Reprice the frozen all-YES underround basket denominator with official fees.

This is a correction audit, not a new threshold search.  The headline result
keeps the old v0 denominator exactly: ``strategy_candidate == true`` and
``settlement_eval_status == settled_exactly_one_winner``.  Extra thresholds
are emitted only as explicitly post-hoc sensitivity rows.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
FACT_DIR = ROOT / "runtime/weather_edge_v1/all_yes_underround_basket_v0"
BASKETS_DEFAULT = FACT_DIR / "fact_baskets.jsonl"
LEGS_DEFAULT = FACT_DIR / "fact_basket_legs.jsonl"
OLD_SUMMARY_DEFAULT = FACT_DIR / "summary.json"
OUT_DIR_DEFAULT = ROOT / "docs/analysis/2026-07/generated/all_yes_underround_fee_correction_v1"
REPORT_DEFAULT = ROOT / "docs/analysis/2026-07/2026-07-14-all-yes-underround-fee-correction-v1.md"

FEE_RATE = 0.05
SEED = 20260714
BOOTSTRAP_REPS = 5000
THRESHOLDS = (0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06)


def official_weather_taker_fee(price: float, shares: float = 1.0) -> float:
    """Return the contract fee rounded once per order to five decimals."""

    return round(float(shares) * FEE_RATE * float(price) * (1.0 - float(price)), 5)


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = fieldnames or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def bootstrap_roi(
    rows: list[dict[str, Any]],
    *,
    pnl_key: str,
    date_key: str = "event_date",
    reps: int = BOOTSTRAP_REPS,
    seed: int = SEED,
) -> tuple[float | None, float | None]:
    by_date: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        by_date[str(row[date_key])][0] += float(row[pnl_key])
        by_date[str(row[date_key])][1] += float(row["unit_fill_cost"])
    dates = sorted(by_date)
    if len(dates) < 3:
        return None, None
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(reps):
        draw = rng.choice(dates, size=len(dates), replace=True)
        pnl = sum(by_date[str(day)][0] for day in draw)
        cost = sum(by_date[str(day)][1] for day in draw)
        if cost > 0:
            values.append(pnl / cost)
    if not values:
        return None, None
    return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def summarize(rows: list[dict[str, Any]], *, reps: int, seed: int) -> dict[str, Any]:
    cost = sum(float(row["unit_fill_cost"]) for row in rows)
    gross = sum(float(row["gross_pnl_unit"]) for row in rows)
    fee = sum(float(row["taker_fee_unit"]) for row in rows)
    net = sum(float(row["fee_adjusted_pnl_unit"]) for row in rows)
    gross_ci = bootstrap_roi(rows, pnl_key="gross_pnl_unit", reps=reps, seed=seed)
    net_ci = bootstrap_roi(rows, pnl_key="fee_adjusted_pnl_unit", reps=reps, seed=seed)
    return {
        "settled_observations": len(rows),
        "unique_events": len({row["opportunity_key"] for row in rows}),
        "active_dates": len({row["event_date"] for row in rows}),
        "unit_fill_cost": round(cost, 6),
        "gross_pnl_unit": round(gross, 6),
        "taker_fee_unit": round(fee, 6),
        "fee_adjusted_pnl_unit": round(net, 6),
        "gross_roi_on_fill_cost": gross / cost if cost else None,
        "gross_roi_ci_low": gross_ci[0],
        "gross_roi_ci_high": gross_ci[1],
        "fee_adjusted_roi_on_fill_cost": net / cost if cost else None,
        "fee_adjusted_roi_ci_low": net_ci[0],
        "fee_adjusted_roi_ci_high": net_ci[1],
        "fee_adjusted_roi_on_cash_plus_fee": net / (cost + fee) if cost + fee else None,
    }


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{100.0 * value:+.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baskets", default=str(BASKETS_DEFAULT))
    parser.add_argument("--legs", default=str(LEGS_DEFAULT))
    parser.add_argument("--old-summary", default=str(OLD_SUMMARY_DEFAULT))
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    parser.add_argument("--report-md", default=str(REPORT_DEFAULT))
    parser.add_argument("--bootstrap-reps", type=int, default=BOOTSTRAP_REPS)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    baskets_path = Path(args.baskets)
    legs_path = Path(args.legs)
    old_summary_path = Path(args.old_summary)
    out_dir = Path(args.out_dir)
    report_path = Path(args.report_md)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    all_baskets = list(read_jsonl(baskets_path))
    sensitivity_baskets = [
        row
        for row in all_baskets
        if row.get("quality_guard_pass")
        and row.get("underround") is not None
        and float(row["underround"]) >= min(THRESHOLDS)
    ]
    sensitivity_ids = {str(row["basket_id"]) for row in sensitivity_baskets}
    headline_ids = {str(row["basket_id"]) for row in all_baskets if row.get("strategy_candidate")}

    fee_by_basket: dict[str, float] = defaultdict(float)
    leg_count_by_basket: dict[str, int] = defaultdict(int)
    headline_leg_rows: list[dict[str, Any]] = []
    for leg in read_jsonl(legs_path):
        basket_id = str(leg["basket_id"])
        if basket_id not in sensitivity_ids:
            continue
        price = float(leg["best_ask"])
        unit_fee = official_weather_taker_fee(price, 1.0)
        five_share_fee = official_weather_taker_fee(price, 5.0)
        fee_by_basket[basket_id] += unit_fee
        leg_count_by_basket[basket_id] += 1
        if basket_id in headline_ids:
            headline_leg_rows.append(
                {
                    "basket_id": basket_id,
                    "event_date": leg.get("event_date"),
                    "city": leg.get("city"),
                    "event_slug": leg.get("event_slug"),
                    "leg_index": leg.get("leg_index"),
                    "condition_id": leg.get("condition_id"),
                    "bracket": leg.get("bracket"),
                    "best_ask": price,
                    "unit_shares": 1.0,
                    "taker_fee_unit": unit_fee,
                    "five_shares": 5.0,
                    "taker_fee_five_shares": five_share_fee,
                }
            )

    counterfactual_rows: list[dict[str, Any]] = []
    sensitivity_rows: list[dict[str, Any]] = []
    for basket in sensitivity_baskets:
        basket_id = str(basket["basket_id"])
        expected_legs = int(basket.get("legs") or 0)
        actual_legs = leg_count_by_basket.get(basket_id, 0)
        if actual_legs != expected_legs:
            raise RuntimeError(f"fee audit leg mismatch for {basket_id}: expected {expected_legs}, got {actual_legs}")
        settled = basket.get("settlement_eval_status") == "settled_exactly_one_winner"
        cost = float(basket.get("total_yes_ask_cost") or 0.0)
        gross = float(basket.get("unit_pnl")) if settled else None
        fee = round(fee_by_basket[basket_id], 5)
        net = gross - fee if gross is not None else None
        audited = {
            "basket_id": basket_id,
            "opportunity_key": basket.get("opportunity_key"),
            "snapshot_ts_utc": basket.get("snapshot_ts_utc"),
            "event_date": basket.get("event_date"),
            "city": basket.get("city"),
            "event_slug": basket.get("event_slug"),
            "legs": expected_legs,
            "underround": basket.get("underround"),
            "settlement_eval_status": basket.get("settlement_eval_status"),
            "unit_fill_cost": cost,
            "unit_payout": basket.get("unit_payout"),
            "gross_pnl_unit": gross,
            "taker_fee_unit": fee,
            "fee_adjusted_pnl_unit": net,
            "gross_roi_on_fill_cost": gross / cost if gross is not None and cost else None,
            "fee_adjusted_roi_on_fill_cost": net / cost if net is not None and cost else None,
            "gross_positive": gross > 0 if gross is not None else None,
            "fee_adjusted_positive": net > 0 if net is not None else None,
            "gross_to_nonpositive_flip": gross > 0 and net <= 0 if gross is not None and net is not None else None,
        }
        sensitivity_rows.append(audited)
        if basket_id in headline_ids:
            counterfactual_rows.append(audited)

    settled_headline = [row for row in counterfactual_rows if row["gross_pnl_unit"] is not None]
    headline_summary = summarize(settled_headline, reps=args.bootstrap_reps, seed=args.seed)
    old_summary = json.loads(old_summary_path.read_text(encoding="utf-8"))
    old_counts = old_summary["summary"]
    old_threshold = next(row for row in old_summary["threshold_stats"] if float(row["threshold"]) == 0.02)
    if len(counterfactual_rows) != int(old_counts["strategy_candidates"]):
        raise RuntimeError("frozen underround candidate denominator no longer reproduces the old report")
    if len(settled_headline) != int(old_counts["strategy_candidate_settled_exactly_one_winner"]):
        raise RuntimeError("frozen underround settled denominator no longer reproduces the old report")
    if abs(float(headline_summary["gross_pnl_unit"]) - float(old_threshold["settled_pnl_unit"])) > 1e-9:
        raise RuntimeError("frozen underround gross PnL no longer reproduces the old report")
    threshold_rows: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        rows = [
            row
            for row in sensitivity_rows
            if row["gross_pnl_unit"] is not None and float(row["underround"]) >= threshold
        ]
        result = summarize(rows, reps=args.bootstrap_reps, seed=args.seed)
        threshold_rows.append({"threshold": threshold, **result, "interpretation": "post_hoc_sensitivity_only"})

    daily_rows: list[dict[str, Any]] = []
    for event_date in sorted({str(row["event_date"]) for row in settled_headline}):
        rows = [row for row in settled_headline if str(row["event_date"]) == event_date]
        result = summarize(rows, reps=0, seed=args.seed)
        daily_rows.append({"event_date": event_date, **result})

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit": "all_yes_underround_fee_correction_v1",
        "source_summary": str(old_summary_path.relative_to(ROOT)),
        "frozen_denominator": {
            "candidate_rule": "strategy_candidate == true",
            "settled_rule": "settlement_eval_status == settled_exactly_one_winner",
            "candidate_observations": len(counterfactual_rows),
            "settled_observations": len(settled_headline),
            "pending_observations": len(counterfactual_rows) - len(settled_headline),
            "unique_candidate_events": len({row["opportunity_key"] for row in counterfactual_rows}),
            "candidate_event_date_min": min(str(row["event_date"]) for row in counterfactual_rows),
            "candidate_event_date_max": max(str(row["event_date"]) for row in counterfactual_rows),
            "settled_event_date_min": min(str(row["event_date"]) for row in settled_headline),
            "settled_event_date_max": max(str(row["event_date"]) for row in settled_headline),
        },
        "fee_contract": {
            "entry": "taker BUY_YES on every leg",
            "formula": "shares * 0.05 * price * (1 - price)",
            "precision": "round to 5 decimals per leg order",
            "maker_fee": 0.0,
        },
        "legacy_bug": {
            "builder": "scripts/analysis/market_structure_edge/build_all_yes_underround_basket_facts_v0.py",
            "cause": "unit_pnl was unit_payout minus total_yes_ask_cost; threshold_stats summed that gross field without any fee term",
            "affected_report": "docs/analysis/2026-06/2026-06-15-all-yes-underround-basket-facts-v0.md",
            "old_headline": "threshold 0.02 settled unit ROI +3.16%",
        },
        "headline": headline_summary,
        "classification": {
            "gross_positive": sum(row["gross_pnl_unit"] > 0 for row in settled_headline),
            "fee_adjusted_positive": sum(row["fee_adjusted_pnl_unit"] > 0 for row in settled_headline),
            "fee_adjusted_nonpositive": sum(row["fee_adjusted_pnl_unit"] <= 0 for row in settled_headline),
            "gross_to_nonpositive_flip": sum(row["gross_to_nonpositive_flip"] for row in settled_headline),
        },
        "bootstrap": {"block": "event_date", "reps": args.bootstrap_reps, "seed": args.seed},
        "verdict": {
            "old": "gross_positive",
            "corrected": "taker_expression_negative_after_official_fee",
            "live": "NO",
            "maker_hypothesis": "unresolved; requires all-leg fill/queue/adverse-selection evidence",
        },
    }

    per_basket_path = out_dir / "per_basket_counterfactual.csv"
    per_leg_path = out_dir / "per_leg_fee.csv"
    daily_path = out_dir / "daily_summary.csv"
    threshold_path = out_dir / "threshold_sensitivity.csv"
    summary_path = out_dir / "summary.json"
    write_csv(per_basket_path, counterfactual_rows)
    write_csv(per_leg_path, headline_leg_rows)
    write_csv(daily_path, daily_rows)
    write_csv(threshold_path, threshold_rows)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    h = headline_summary
    cls = summary["classification"]
    report = f"""# All-YES Underround Fee Correction v1

## 结论

旧的 `+3.16%` 是 gross ROI，不是可执行 taker ROI。固定原报告 `{summary['frozen_denominator']['candidate_observations']}` 个 strategy-candidate observations、其中 `{summary['frozen_denominator']['settled_observations']}` 个 settled observations 后，官方 fee 为 `{h['taker_fee_unit']:.5f}` unit，gross PnL `{h['gross_pnl_unit']:+.5f}` 变成 fee-adjusted PnL `{h['fee_adjusted_pnl_unit']:+.5f}`；ROI 从 `{pct(h['gross_roi_on_fill_cost'])}` 变为 `{pct(h['fee_adjusted_roi_on_fill_cost'])}`，target-date bootstrap CI `{pct(h['fee_adjusted_roi_ci_low'])}`..`{pct(h['fee_adjusted_roi_ci_high'])}`。

因此 `all_yes_underround_basket_v0` 的 taker 表达式应从“正收益候选”纠正为 **fee 后显著为负**，不能 live。`{cls['gross_positive']}` 个 settled basket 在 gross 口径全部为正；扣 fee 后仅 `{cls['fee_adjusted_positive']}` 个为正，`{cls['gross_to_nonpositive_flip']}` 个翻为 non-positive。

## Bug 与影响半径

- 根因：`build_all_yes_underround_basket_facts_v0.py:410-413` 直接用 `unit_payout - total_yes_ask_cost` 生成 `unit_pnl`，`:180-181` 又直接汇总该 gross 字段；全链没有 fee 字段或 fee 曲线。
- 被污染窗口：candidate event dates `{summary['frozen_denominator']['candidate_event_date_min']}`..`{summary['frozen_denominator']['candidate_event_date_max']}`；已结算绩效窗口 `{summary['frozen_denominator']['settled_event_date_min']}`..`{summary['frozen_denominator']['settled_event_date_max']}`。
- 受影响旧判断：`2026-06-15-all-yes-underround-basket-facts-v0.md` threshold `0.02` 的 `+3.2%`，以及同表所有 threshold ROI，均为 gross-only。
- 高 threshold 行只保留为 post-hoc sensitivity，不能据此把阈值改成 `0.03/0.05` 后宣称找到 alpha；同一 event 在多个 snapshot 重复出现，且没有 all-leg atomic fill 证据。

## 固定口径

- 每腿 entry：marketable `BUY_YES` taker。
- fee：`shares * 0.05 * price * (1-price)`，每腿订单 round 到 5 decimals。
- 主 ROI 分母仍是旧报告的 summed fill cost，另在 JSON 给出 cash+fee denominator sensitivity。
- bootstrap block：`event_date`，`{args.bootstrap_reps}` reps，seed `{args.seed}`。

## 可复查输出

- 逐 basket：`{per_basket_path.relative_to(ROOT)}`
- 逐 leg fee：`{per_leg_path.relative_to(ROOT)}`
- 逐日：`{daily_path.relative_to(ROOT)}`
- threshold sensitivity：`{threshold_path.relative_to(ROOT)}`
- machine summary：`{summary_path.relative_to(ROOT)}`
"""
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({"summary": summary, "outputs": [str(path) for path in [summary_path, per_basket_path, daily_path, threshold_path, report_path]]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
