#!/usr/bin/env python3
"""Detailed daily breakdown for the leading no-reheat market-lag slice."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import research_current_yes_no_reheat_segment_breakdown_v1 as seg
from scripts.analysis.reheat_risk import research_current_yes_no_reheat_state_slices_v1 as base


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_no_reheat_slice_detail_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-24-current-yes-no-reheat-slice-detail-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-yes-no-reheat-slice-detail-v1.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-rows", default=str(base.FEATURE_ROWS))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        out = float(value)
        return None if not math.isfinite(out) else out
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f * 100:+.{digits}f}%"


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.{digits}f}"


def slice_rows(feature_path: Path) -> pd.DataFrame:
    rows = base.load_current_yes_rows(feature_path)
    tradable = rows[
        rows["decision_hour_local"].between(10, 21)
        & rows["current_yes_ask"].between(0.35, 0.97, inclusive="left")
        & rows["current_yes_ask_size"].fillna(0).ge(5.0)
    ].copy()
    tradable = seg.add_segment_features(tradable)
    holdout = tradable[tradable["period"].eq("holdout")].copy()
    masks = base.state_masks(holdout)
    mask = (
        masks["stalled_high_ge2obs"]
        & holdout["forecast_gap_bin"].eq("forecast_near_or_below")
        & holdout["current_yes_ask"].ge(0.50)
        & holdout["current_yes_ask"].lt(0.70)
    )
    out = holdout[mask].copy()
    out["slice_name"] = "forecast_near_or_below__stalled_high_ge2obs__ask_50_70"
    out["row_pnl"] = out["label_survive"] - out["current_yes_ask"]
    return out


def summarize(frame: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    grouped = (
        frame.groupby(group_cols, dropna=False)
        .agg(
            rows=("city", "size"),
            cities=("city", "nunique"),
            wins=("label_survive", "sum"),
            breaks=("label_future_break", "sum"),
            avg_ask=("current_yes_ask", "mean"),
            cost=("current_yes_ask", "sum"),
            pnl=("row_pnl", "sum"),
            avg_hour=("decision_hour_local", "mean"),
            avg_forecast_gap=("max_forecast_gap_to_running_native", "mean"),
            avg_ask_delta_from_min_prior=("ask_delta_from_min_prior", "mean"),
        )
        .reset_index()
    )
    grouped["win_rate"] = grouped["wins"] / grouped["rows"]
    grouped["future_break_rate"] = grouped["breaks"] / grouped["rows"]
    grouped["roi"] = grouped["pnl"] / grouped["cost"]
    return grouped


def build_markdown(payload: dict[str, Any], out_md: Path) -> None:
    lines = [
        "# Current-YES No-Reheat Slice Detail v1",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Slice: `forecast_near_or_below × stalled_high_ge2obs × ask_50_70`",
        "",
        "## 结论",
        "",
        payload["headline"],
        "",
        "## Summary",
        "",
        f"- rows: {payload['summary']['rows']}",
        f"- active dates: {payload['summary']['dates']}",
        f"- cities: {payload['summary']['cities']}",
        f"- win rate: {pct(payload['summary']['win_rate'])}",
        f"- future-break rate: {pct(payload['summary']['future_break_rate'])}",
        f"- avg ask: {num(payload['summary']['avg_ask'])}",
        f"- ROI: {pct(payload['summary']['roi'])}",
        f"- cost: {num(payload['summary']['cost'])}",
        f"- pnl per $1-share accounting: {num(payload['summary']['pnl'])}",
        "",
        "## Daily Breakdown",
        "",
        "| date | rows | cities | wins | breaks | win | avg ask | cost | pnl | ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["daily"]:
        lines.append(
            "| {target_date} | {rows} | {cities} | {wins} | {breaks} | {win} | {ask} | {cost} | {pnl} | {roi} |".format(
                target_date=row["target_date"],
                rows=row["rows"],
                cities=row["cities"],
                wins=row["wins"],
                breaks=row["breaks"],
                win=pct(row["win_rate"]),
                ask=num(row["avg_ask"]),
                cost=num(row["cost"]),
                pnl=num(row["pnl"]),
                roi=pct(row["roi"]),
            )
        )
    lines.extend(
        [
            "",
            "## City Breakdown",
            "",
            "| city | rows | dates | wins | breaks | win | avg ask | pnl | ROI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["city"]:
        lines.append(
            "| {city} | {rows} | {dates} | {wins} | {breaks} | {win} | {ask} | {pnl} | {roi} |".format(
                city=row["city"],
                rows=row["rows"],
                dates=row["dates"],
                wins=row["wins"],
                breaks=row["breaks"],
                win=pct(row["win_rate"]),
                ask=num(row["avg_ask"]),
                pnl=num(row["pnl"]),
                roi=pct(row["roi"]),
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Unit is opportunity row, not live fill.",
            "- The slice has positive point estimate, but sample is only 30 rows; this remains shadow/research until forward rows validate it.",
            "- Losses cluster on 2026-06-01, 2026-06-14, 2026-06-15, and especially 2026-06-17.",
            "",
            "## Outputs",
            "",
            f"- daily CSV: `{payload['outputs']['daily_csv']}`",
            f"- city CSV: `{payload['outputs']['city_csv']}`",
            f"- row CSV: `{payload['outputs']['row_csv']}`",
            f"- json: `{payload['outputs']['json']}`",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    feature_path = Path(args.feature_rows)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = slice_rows(feature_path)
    daily = summarize(rows, ["target_date"]).sort_values("target_date")
    city = summarize(rows, ["city"]).rename(columns={"cities": "city_count"})
    city["dates"] = rows.groupby("city")["target_date"].nunique().reindex(city["city"]).to_numpy()
    city = city.sort_values(["pnl", "rows"], ascending=False)
    row_cols = [
        "target_date",
        "city",
        "decision_snapshot_ts_utc",
        "decision_hour_local",
        "current_bracket",
        "current_yes_ask",
        "label_survive",
        "label_future_break",
        "row_pnl",
        "minutes_since_running_max",
        "plateau_obs_count_at_high",
        "max_forecast_gap_to_running_native",
        "ask_delta_from_min_prior",
    ]
    daily_csv = out_dir / "daily.csv"
    city_csv = out_dir / "city.csv"
    row_csv = out_dir / "rows.csv"
    daily.to_csv(daily_csv, index=False)
    city.to_csv(city_csv, index=False)
    rows[row_cols].sort_values(["target_date", "city"]).to_csv(row_csv, index=False)

    cost = float(rows["current_yes_ask"].sum())
    pnl = float(rows["row_pnl"].sum())
    summary = {
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "wins": int(rows["label_survive"].sum()),
        "breaks": int(rows["label_future_break"].sum()),
        "win_rate": float(rows["label_survive"].mean()),
        "future_break_rate": float(rows["label_future_break"].mean()),
        "avg_ask": float(rows["current_yes_ask"].mean()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost,
    }
    payload = {
        "generated_at_utc": now_utc(),
        "input_feature_rows": str(feature_path.relative_to(ROOT)),
        "slice": "forecast_near_or_below × stalled_high_ge2obs × ask_50_70",
        "headline": (
            "这个切片的点估计确实强：30 rows / 17 dates / win 80.0% / avg ask 0.618 / ROI +29.5%。"
            " 但每天只有 1-3 单，PnL 由 2026-06-03..06-10 的连续小胜和 2026-06-17 的双亏共同决定，仍需要 forward shadow 验证。"
        ),
        "summary": summary,
        "daily": daily.to_dict("records"),
        "city": city[
            ["city", "rows", "dates", "wins", "breaks", "win_rate", "avg_ask", "cost", "pnl", "roi"]
        ].to_dict("records"),
        "outputs": {
            "daily_csv": str(daily_csv.relative_to(ROOT)),
            "city_csv": str(city_csv.relative_to(ROOT)),
            "row_csv": str(row_csv.relative_to(ROOT)),
            "json": str(out_json.relative_to(ROOT)),
            "markdown": str(out_md.relative_to(ROOT)),
        },
    }
    out_json.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    build_markdown(json_ready(payload), out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
