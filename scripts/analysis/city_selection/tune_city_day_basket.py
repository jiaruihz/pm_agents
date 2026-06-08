"""
tune_city_day_basket.py

PR2b parameter sweep for the Weather Edge Engine city-day basket.

This script reuses the PR2 offline replay primitives and varies only basket
parameters. It keeps raw_single / market_only / blended_single fixed at the
PR2 baseline so every basket run is compared against the same single-leg
reference.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.analysis.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    _attribution,
    _decide_basket,
    _decide_blended_single,
    _decide_market_only,
    _decide_raw_single,
    _load_settled,
    _summarize,
)
from weather_dashboard.basket import BasketConfig  # noqa: E402
from weather_dashboard.blend import load_default_config  # noqa: E402


def _parse_float_list(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_int_list(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _side_roi(summary: dict, side: str) -> float:
    st = summary.get("by_side", {}).get(side)
    if not st:
        return 0.0
    cost = st.get("total_cost_usd") or 0.0
    return (st.get("total_pnl_usd") or 0.0) / cost if cost else 0.0


def _gate_result(row: dict, blended_roi: float) -> dict[str, bool]:
    return {
        "missed_profit_lte_avoided_loss": (
            row["attr_vs_blended"]["missed_profit_usd"]
            <= row["attr_vs_blended"]["avoided_loss_usd"]
        ),
        "roi_gte_blended_80pct": row["basket"]["roi"] >= blended_roi * 0.8,
        "roi_excl_top5_nonnegative": row["basket"]["roi_excl_top5"] >= 0.0,
        "buy_no_roi_nonnegative": _side_roi(row["basket"], "BUY_NO") >= 0.0,
    }


def _write_markdown(report: dict, out_path: Path) -> None:
    baseline = report["baseline"]
    top_rows = report["top_rows"][:12]
    gate_pass = [r for r in report["rows"] if all(r["gates"].values())]

    lines: list[str] = []
    lines.append("# City-Day Basket PR2b Parameter Sweep")
    lines.append("")
    lines.append(f"> generated_at_utc: `{report['generated_at_utc']}`")
    lines.append(f"> data: `{report['db_path']}` / `fact_signal_candidates`")
    lines.append(
        f"> input rows: {report['n_input_rows']} "
        f"({report['date_range'][0]} -> {report['date_range'][1]})"
    )
    lines.append("")
    lines.append("## Baseline")
    lines.append("")
    lines.append("| rule | n_legs | cost | pnl | ROI | ROI excl top5 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for key in ("raw_single", "market_only", "blended_single"):
        s = baseline[key]
        lines.append(
            f"| {key} | {s['n_legs']} | ${s['total_cost_usd']:.0f} | "
            f"${s['total_pnl_usd']:+.0f} | {s['roi']*100:+.2f}% | "
            f"{s['roi_excl_top5']*100:+.2f}% |"
        )
    lines.append("")
    lines.append("## Gate Summary")
    lines.append("")
    lines.append(
        f"- full gate pass configs: {len(gate_pass)} / {len(report['rows'])}"
    )
    lines.append(
        "- gate = missed_profit <= avoided_loss, basket ROI >= 80% blended ROI, "
        "top-5 removed ROI >= 0, BUY_NO ROI >= 0"
    )
    lines.append("")
    lines.append("## Top Configs")
    lines.append("")
    lines.append(
        "| rank | pass | small/normal | cap | max legs | edge small/normal | "
        "prefer NO | n | pnl | ROI | top5 ROI | missed | avoided | net vs blended |"
    )
    lines.append("|---:|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for i, row in enumerate(top_rows, 1):
        cfg = row["basket_config"]
        b = row["basket"]
        attr = row["attr_vs_blended"]
        passed = "yes" if all(row["gates"].values()) else "no"
        lines.append(
            f"| {i} | {passed} | ${cfg['small']:.0f}/${cfg['normal']:.0f} | "
            f"${cfg['cap']:.0f} | {cfg['max_legs']} | "
            f"{cfg['edge_small']:.2f}/{cfg['edge_normal']:.2f} | "
            f"{cfg['prefer_no']} | {b['n_legs']} | ${b['total_pnl_usd']:+.0f} | "
            f"{b['roi']*100:+.2f}% | {b['roi_excl_top5']*100:+.2f}% | "
            f"${attr['missed_profit_usd']:.0f} | ${attr['avoided_loss_usd']:.0f} | "
            f"${attr['net_basket_vs_baseline_usd']:+.0f} |"
        )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    if gate_pass:
        best = report["top_rows"][0]
        lines.append(
            "At least one PR2b basket configuration passes the offline gate. "
            "This is still an offline replay over aggregated decision-window "
            "candidates, not a production approval."
        )
        lines.append(
            f"Best ranked config: `{best['basket_config']}` with basket ROI "
            f"{best['basket']['roi']*100:+.2f}%."
        )
    else:
        lines.append(
            "No PR2b basket configuration passed all gates. The strongest runs "
            "improved ROI and tail sensitivity but still missed more blended "
            "single-leg profit than they avoided."
        )
    lines.append("")
    lines.append("Production remains unchanged: no N100 config change, no canary.")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sweep city-day basket PR2b parameters.")
    ap.add_argument("--db", type=Path, default=DB_DEFAULT)
    ap.add_argument("--min-date", type=str, default=None)
    ap.add_argument("--edge-threshold", type=float, default=0.03)
    ap.add_argument("--leg-notional", type=float, default=5.0)
    ap.add_argument("--small-notionals", default="3,5")
    ap.add_argument("--normal-notionals", default="5,8")
    ap.add_argument("--caps", default="15,20,25")
    ap.add_argument("--max-legs", default="3,4")
    ap.add_argument("--edge-small-thresholds", default="0.02,0.03")
    ap.add_argument("--edge-normal-thresholds", default="0.05,0.06")
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    args = ap.parse_args()

    df = _load_settled(args.db, args.min_date)
    blend_cfg = load_default_config()

    raw_legs = _decide_raw_single(df, args.edge_threshold, args.leg_notional)
    market_legs = _decide_market_only(df, args.edge_threshold, args.leg_notional)
    blended_legs = _decide_blended_single(
        df, blend_cfg, args.edge_threshold, args.leg_notional
    )

    raw_s = asdict(_summarize("raw_single", raw_legs))
    market_s = asdict(_summarize("market_only", market_legs))
    blended_s = asdict(_summarize("blended_single", blended_legs))

    rows: list[dict] = []
    for small in _parse_float_list(args.small_notionals):
        for normal in _parse_float_list(args.normal_notionals):
            if normal < small:
                continue
            for cap in _parse_float_list(args.caps):
                for max_legs in _parse_int_list(args.max_legs):
                    for edge_small in _parse_float_list(args.edge_small_thresholds):
                        for edge_normal in _parse_float_list(args.edge_normal_thresholds):
                            if edge_normal < edge_small:
                                continue
                            for prefer_no in (True, False):
                                cfg = BasketConfig(
                                    single_leg_notional_small=small,
                                    single_leg_notional_normal=normal,
                                    city_day_notional_cap=cap,
                                    max_no_legs_per_city_day=max_legs,
                                    edge_small_threshold=edge_small,
                                    edge_normal_threshold=edge_normal,
                                    prefer_no_over_yes=prefer_no,
                                )
                                basket_legs = _decide_basket(df, blend_cfg, cfg)
                                basket_s = asdict(_summarize("basket", basket_legs))
                                row = {
                                    "basket_config": {
                                        "small": small,
                                        "normal": normal,
                                        "cap": cap,
                                        "max_legs": max_legs,
                                        "edge_small": edge_small,
                                        "edge_normal": edge_normal,
                                        "prefer_no": prefer_no,
                                    },
                                    "basket": basket_s,
                                    "attr_vs_raw": _attribution(basket_legs, raw_legs),
                                    "attr_vs_blended": _attribution(
                                        basket_legs, blended_legs
                                    ),
                                }
                                row["gates"] = _gate_result(row, blended_s["roi"])
                                row["gate_count"] = sum(row["gates"].values())
                                rows.append(row)

    rows_sorted = sorted(
        rows,
        key=lambda r: (
            all(r["gates"].values()),
            r["gate_count"],
            r["attr_vs_blended"]["net_basket_vs_baseline_usd"],
            r["basket"]["roi_excl_top5"],
            r["basket"]["roi"],
        ),
        reverse=True,
    )

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(args.db),
        "n_input_rows": int(len(df)),
        "date_range": [str(df["event_date"].min()), str(df["event_date"].max())],
        "baseline": {
            "raw_single": raw_s,
            "market_only": market_s,
            "blended_single": blended_s,
        },
        "rows": rows_sorted,
        "top_rows": rows_sorted[:20],
    }

    today = dt.date.today()
    out_dir = args.out_dir / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-city-day-basket-pr2b-sweep.json"
    md_path = out_dir / f"{today.isoformat()}-city-day-basket-pr2b-sweep.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)

    print(f"rows={len(rows_sorted)} full_gate_pass={sum(1 for r in rows_sorted if all(r['gates'].values()))}")
    for i, row in enumerate(rows_sorted[:8], 1):
        cfg = row["basket_config"]
        b = row["basket"]
        attr = row["attr_vs_blended"]
        passed = "PASS" if all(row["gates"].values()) else f"{row['gate_count']}/4"
        print(
            f"{i:02d} {passed} cfg={cfg} "
            f"n={b['n_legs']} pnl={b['total_pnl_usd']:+.2f} roi={b['roi']*100:+.2f}% "
            f"top5={b['roi_excl_top5']*100:+.2f}% "
            f"missed={attr['missed_profit_usd']:.2f} avoided={attr['avoided_loss_usd']:.2f} "
            f"net={attr['net_basket_vs_baseline_usd']:+.2f}"
        )
    print(f"JSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
