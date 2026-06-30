#!/usr/bin/env python3
"""Classify city strategy fit from historical station-vs-forecast bias.

Input is the reproducible output of
research_historical_forecast_station_bias_v1.py. This script turns the
city/source error distribution into route-level strategy fit labels.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path(
    "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/"
    "city_model_error_summary.csv"
)
DEFAULT_OUT_DIR = Path(
    "docs/analysis/2026-06/generated/city_strategy_fit_by_forecast_bias_v1"
)
DEFAULT_REPORT = Path(
    "docs/analysis/2026-06/2026-06-30-city-strategy-fit-by-forecast-bias-v1.md"
)


def _f(row: dict[str, str], key: str) -> float:
    return float(row[key])


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def classify_bias(row: dict[str, str]) -> tuple[str, str]:
    bias = _f(row, "bias")
    p90 = _f(row, "p90")
    p10 = _f(row, "p10")
    hot = _f(row, "pct_actual_ge_forecast_plus_1")
    cold = _f(row, "pct_forecast_ge_actual_plus_1")
    mae = _f(row, "mae")

    if bias >= 0.7 and hot >= 0.40 and cold <= 0.15:
        return (
            "hot_underforecast_clean",
            "actual tends to beat forecast; positive tail is directional and cold tail is small",
        )
    if bias >= 0.5 and p90 >= 2.0 and hot >= 0.35:
        return (
            "hot_underforecast_noisy",
            "actual often beats forecast, but opposite-tail or error width still matters",
        )
    if bias <= -0.5 and cold >= 0.35 and hot <= 0.20:
        return (
            "cold_overforecast_clean",
            "forecast tends to run too hot; positive tail is limited",
        )
    if cold >= 0.25 and p10 <= -1.5:
        return (
            "cold_overforecast_noisy",
            "overforecast tail is meaningful, but positive breaks are not negligible",
        )
    if mae <= 1.0 and hot < 0.25 and cold < 0.25 and abs(bias) < 0.35:
        return (
            "balanced_tight",
            "forecast is comparatively well centered and tight; price/regime should dominate",
        )
    if hot >= 0.25 and cold >= 0.20:
        return (
            "two_sided_noisy",
            "both hot and cold tails are live; route needs larger edge and smaller size",
        )
    return (
        "mild_or_mixed",
        "directional bias is present but not strong enough to own the decision",
    )


def classify_fit(row: dict[str, str], bias_regime: str) -> dict[str, str]:
    bias = _f(row, "bias")
    p90 = _f(row, "p90")
    hot = _f(row, "pct_actual_ge_forecast_plus_1")
    cold = _f(row, "pct_forecast_ge_actual_plus_1")

    if bias_regime == "hot_underforecast_clean":
        current_no = "strong"
        high_yes = "strong"
        capped_no = "weak"
        fade_yes = "weak"
    elif bias_regime == "hot_underforecast_noisy":
        current_no = "medium"
        high_yes = "medium"
        capped_no = "weak"
        fade_yes = "weak_to_medium"
    elif bias_regime == "cold_overforecast_clean":
        current_no = "weak"
        high_yes = "weak"
        capped_no = "strong"
        fade_yes = "strong"
    elif bias_regime == "cold_overforecast_noisy":
        current_no = "weak_to_medium"
        high_yes = "weak_to_medium"
        capped_no = "medium"
        fade_yes = "medium"
    elif bias_regime == "balanced_tight":
        current_no = "neutral"
        high_yes = "neutral"
        capped_no = "neutral"
        fade_yes = "neutral"
    elif bias_regime == "two_sided_noisy":
        current_no = "shadow_only"
        high_yes = "shadow_only"
        capped_no = "shadow_only"
        fade_yes = "shadow_only"
    else:
        current_no = "neutral_to_medium" if bias > 0 else "neutral"
        high_yes = "neutral_to_medium" if p90 >= 1.5 and hot >= 0.25 else "neutral"
        capped_no = "neutral_to_medium" if cold >= 0.20 else "neutral"
        fade_yes = "neutral_to_medium" if cold >= 0.20 else "neutral"

    return {
        "runway_current_bracket_no_fit": current_no,
        "higher_yes_or_hot_break_fit": high_yes,
        "forecast_capped_higher_no_fit": capped_no,
        "current_high_yes_or_peak_fade_fit": fade_yes,
    }


def _risk_note(row: dict[str, str], regime: str) -> str:
    unit = row["unit"]
    mae = _f(row, "mae")
    p90 = _f(row, "p90")
    p10 = _f(row, "p10")
    if regime == "two_sided_noisy":
        return f"wide two-sided station/source error: p10={_fmt(p10)}{unit}, p90={_fmt(p90)}{unit}"
    if mae >= 1.5:
        return f"large average station/source error: MAE={_fmt(mae)}{unit}"
    if regime.startswith("hot"):
        return f"avoid treating forecast ceiling as hard cap; p90 hot miss={_fmt(p90)}{unit}"
    if regime.startswith("cold"):
        return f"runway signals need stronger live confirmation; p10 cold miss={_fmt(p10)}{unit}"
    return "use as calibration feature; do not hard-select city without live regime and price"


def build(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(
            f"missing input: {input_path}. Run research_historical_forecast_station_bias_v1.py first."
        )

    rows = [r for r in csv.DictReader(input_path.open()) if r["is_best_model"] == "True"]
    output_rows: list[dict[str, Any]] = []
    for row in rows:
        regime, reason = classify_bias(row)
        fit = classify_fit(row, regime)
        output_rows.append(
            {
                "city": row["city"],
                "city_pool": row["city_pool"],
                "region": row["region"],
                "unit": row["unit"],
                "best_model": row["model"],
                "n": row["n"],
                "bias": row["bias"],
                "mae": row["mae"],
                "p10": row["p10"],
                "p50": row["p50"],
                "p90": row["p90"],
                "hot_tail_pct": round(_f(row, "pct_actual_ge_forecast_plus_1") * 100, 1),
                "cold_tail_pct": round(_f(row, "pct_forecast_ge_actual_plus_1") * 100, 1),
                "source_bias_regime": regime,
                "classification_reason": reason,
                **fit,
                "risk_note": _risk_note(row, regime),
            }
        )

    order = {
        "hot_underforecast_clean": 0,
        "hot_underforecast_noisy": 1,
        "cold_overforecast_clean": 2,
        "cold_overforecast_noisy": 3,
        "balanced_tight": 4,
        "two_sided_noisy": 5,
        "mild_or_mixed": 6,
    }
    output_rows.sort(key=lambda r: (order.get(r["source_bias_regime"], 99), r["city"]))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "city_strategy_fit_by_forecast_bias.csv"
    with out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)

    by_regime: dict[str, list[dict[str, Any]]] = {}
    for row in output_rows:
        by_regime.setdefault(row["source_bias_regime"], []).append(row)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(output_rows, by_regime, out_csv), encoding="utf-8")
    return {"rows": len(output_rows), "report": str(report_path), "csv": str(out_csv)}


def _md_table(rows: list[dict[str, Any]], keys: list[tuple[str, str]]) -> str:
    lines = ["| " + " | ".join(label for _, label in keys) + " |"]
    lines.append("| " + " | ".join("---" for _ in keys) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(k, "")) for k, _ in keys) + " |")
    return "\n".join(lines)


def render_report(
    rows: list[dict[str, Any]], by_regime: dict[str, list[dict[str, Any]]], out_csv: Path
) -> str:
    summary_rows = []
    for regime, items in sorted(by_regime.items()):
        summary_rows.append(
            {
                "regime": regime,
                "n": len(items),
                "cities": ", ".join(r["city"] for r in items),
            }
        )

    hot_rows = [
        r
        for r in rows
        if r["source_bias_regime"] in {"hot_underforecast_clean", "hot_underforecast_noisy"}
    ]
    cold_rows = [
        r
        for r in rows
        if r["source_bias_regime"] in {"cold_overforecast_clean", "cold_overforecast_noisy"}
    ]
    balanced_rows = [
        r
        for r in rows
        if r["source_bias_regime"] in {"balanced_tight", "mild_or_mixed", "two_sided_noisy"}
    ]

    cols = [
        ("city", "city"),
        ("unit", "unit"),
        ("best_model", "model"),
        ("bias", "bias"),
        ("p10", "p10"),
        ("p90", "p90"),
        ("hot_tail_pct", "hot%"),
        ("cold_tail_pct", "cold%"),
        ("runway_current_bracket_no_fit", "current NO"),
        ("forecast_capped_higher_no_fit", "capped NO"),
        ("current_high_yes_or_peak_fade_fit", "fade YES"),
    ]

    return "\n".join(
        [
            "# City Strategy Fit by Forecast Bias v1",
            "",
            "Generated: 2026-06-30",
            "",
            "## Verdict",
            "",
            "需要给城市分类，但分类对象不是“城市好坏”，而是“这个城市/数据源的历史 station-vs-forecast 偏移适合哪类交易表达”。同一个城市可能适合 current-bracket NO，却不适合 forecast-capped higher NO。",
            "",
            "Input uses the historical bias layer from `2026-06-30-historical-forecast-station-bias-v1`: `error = actual daily max - forecast daily max`.",
            "",
            "## Regime Summary",
            "",
            _md_table(summary_rows, [("regime", "regime"), ("n", "cities"), ("cities", "city list")]),
            "",
            "## Hot Underforecast Cities",
            "",
            "这些城市/模型长期实际偏热，forecast ceiling 不能当硬上限。它们更适合研究 `runway current-bracket NO` 或 higher/hot-break YES；不适合作为 `forecast_capped_higher_no` 的干净城市池。",
            "",
            _md_table(hot_rows, cols),
            "",
            "## Cold Overforecast Cities",
            "",
            "这些城市/模型长期 forecast 偏热，适合优先研究 capped higher NO / peak-fade YES；runway current-bracket NO 要求更强实时升温确认。",
            "",
            _md_table(cold_rows, cols),
            "",
            "## Balanced, Mixed, Or Noisy Cities",
            "",
            "这些城市不应该仅靠历史 source bias 决定方向；要把实时 regime、盘口价格和执行纪律放在前面。`two_sided_noisy` 城市尤其应该先 shadow 或降 size。",
            "",
            _md_table(balanced_rows, cols),
            "",
            "## How To Use",
            "",
            "- `hot_underforecast_clean`: 优先给 current-bracket NO / hot-break YES 更高 prior；capped higher NO 要明显更便宜、更大 margin。",
            "- `cold_overforecast_clean`: 优先给 capped higher NO / current-high YES 更高 prior；current NO 只在实时 runway 很干净时考虑。",
            "- `two_sided_noisy`: 不做城市级硬过滤，但 size 应小，必须依赖更强 live feature 和盘口 edge。",
            "- `balanced_tight`: forecast bias 不提供明显方向，策略胜负更依赖 intraday regime 和 market price。",
            "",
            "Boundary: this is a calibration/selection feature layer, not a live gate. Live eligibility still needs strategy-specific replay/shadow with real ask, capacity, settlement and execution rules.",
            "",
            "## Artifact",
            "",
            f"- CSV: `{out_csv}`",
            "- Generated CSV is ignored by git and reproducible from the script.",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()
    result = build(args)
    print(result)


if __name__ == "__main__":
    main()
