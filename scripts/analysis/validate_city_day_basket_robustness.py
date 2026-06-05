"""
validate_city_day_basket_robustness.py

Robustness checks for the PR2b city-day basket candidate.

This is not a new optimizer. It fixes one basket config and evaluates it
against raw_single / market_only / blended_single across several slices:
full sample, pre-holdout, holdout, recent, and live_filled opportunity subset.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    _attribution,
    _decide_basket,
    _decide_blended_single,
    _decide_market_only,
    _decide_raw_single,
    _summarize,
)
from weather_dashboard.basket import BasketConfig  # noqa: E402
from weather_dashboard.blend import load_default_config  # noqa: E402


def _load_rows(db_path: Path) -> pd.DataFrame:
    sql = """
        SELECT city, event_date, bracket, side,
               model_p_yes, market_yes_price,
               decision_entry_price, final_yes,
               live_filled, paper_ordered, eligible
        FROM fact_signal_candidates
        WHERE settlement_status = 'settled'
          AND decision_window_missing = 0
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND final_yes IS NOT NULL
    """
    with sqlite3.connect(str(db_path)) as conn:
        return pd.read_sql_query(sql, conn)


def _slice(df: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "full":
        return df
    if name == "train_pre_2026_05_26":
        return df[df["event_date"] < "2026-05-26"].reset_index(drop=True)
    if name == "holdout_from_2026_05_26":
        return df[df["event_date"] >= "2026-05-26"].reset_index(drop=True)
    if name == "recent_from_2026_06_01":
        return df[df["event_date"] >= "2026-06-01"].reset_index(drop=True)
    if name == "live_filled_only":
        return df[df["live_filled"] == 1].reset_index(drop=True)
    raise ValueError(f"unknown slice: {name}")


def _side_roi(summary: dict, side: str) -> float:
    st = summary.get("by_side", {}).get(side)
    if not st:
        return 0.0
    cost = st.get("total_cost_usd") or 0.0
    return (st.get("total_pnl_usd") or 0.0) / cost if cost else 0.0


def _evaluate(df: pd.DataFrame, basket_cfg: BasketConfig) -> dict:
    blend_cfg = load_default_config()
    raw_legs = _decide_raw_single(df, edge_threshold=0.03, notional=5.0)
    market_legs = _decide_market_only(df, edge_threshold=0.03, notional=5.0)
    blended_legs = _decide_blended_single(
        df, blend_cfg=blend_cfg, edge_threshold=0.03, notional=5.0
    )
    basket_legs = _decide_basket(df, blend_cfg=blend_cfg, basket_cfg=basket_cfg)

    raw_s = asdict(_summarize("raw_single", raw_legs))
    market_s = asdict(_summarize("market_only", market_legs))
    blended_s = asdict(_summarize("blended_single", blended_legs))
    basket_s = asdict(_summarize("basket", basket_legs))
    attr = _attribution(basket_legs, blended_legs)
    gates = {
        "missed_profit_lte_avoided_loss": (
            attr["missed_profit_usd"] <= attr["avoided_loss_usd"]
        ),
        "roi_gte_blended_80pct": basket_s["roi"] >= blended_s["roi"] * 0.8,
        "roi_excl_top5_nonnegative": basket_s["roi_excl_top5"] >= 0.0,
        "buy_no_roi_nonnegative": _side_roi(basket_s, "BUY_NO") >= 0.0,
    }
    return {
        "n_rows": int(len(df)),
        "date_range": [
            str(df["event_date"].min()) if len(df) else None,
            str(df["event_date"].max()) if len(df) else None,
        ],
        "rules": {
            "raw_single": raw_s,
            "market_only": market_s,
            "blended_single": blended_s,
            "basket": basket_s,
        },
        "attr_basket_vs_blended": attr,
        "gates": gates,
        "gate_count": sum(gates.values()),
    }


def _write_md(report: dict, out_path: Path) -> None:
    lines = [
        "# City-Day Basket PR2b Robustness Check",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> db: `{report['db_path']}`",
        f"> config: `{report['basket_config']}`",
        "",
        "## Slice Summary",
        "",
        "| slice | rows | range | raw ROI | blended ROI | basket ROI | basket top5 ROI | missed | avoided | gates |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in report["slices"].items():
        raw = s["rules"]["raw_single"]
        blended = s["rules"]["blended_single"]
        basket = s["rules"]["basket"]
        attr = s["attr_basket_vs_blended"]
        lines.append(
            f"| {name} | {s['n_rows']} | {s['date_range'][0]} -> {s['date_range'][1]} | "
            f"{raw['roi']*100:+.2f}% | {blended['roi']*100:+.2f}% | "
            f"{basket['roi']*100:+.2f}% | {basket['roi_excl_top5']*100:+.2f}% | "
            f"${attr['missed_profit_usd']:.0f} | ${attr['avoided_loss_usd']:.0f} | "
            f"{s['gate_count']}/4 |"
        )
    lines.extend([
        "",
        "## Quant Read",
        "",
        "- Full-sample PR2b pass is not enough for production because the selected parameters were chosen on the same sample.",
        "- Holdout and recent slices are the real overfit check. Positive raw ROI is not required, but tail-removed basket ROI should stay nonnegative before live promotion.",
        "- `live_filled_only` is an opportunity-subset counterfactual, not actual wallet PnL and not authoritative live fills accounting.",
        "",
        "Production remains unchanged.",
    ])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    db_path = DB_DEFAULT
    basket_cfg = BasketConfig(
        single_leg_notional_small=3.0,
        single_leg_notional_normal=8.0,
        city_day_notional_cap=15.0,
        max_no_legs_per_city_day=4,
        edge_small_threshold=0.03,
        edge_normal_threshold=0.06,
        prefer_no_over_yes=False,
    )
    df = _load_rows(db_path)
    slices = [
        "full",
        "train_pre_2026_05_26",
        "holdout_from_2026_05_26",
        "recent_from_2026_06_01",
        "live_filled_only",
    ]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(db_path),
        "basket_config": {
            "single_leg_notional_small": basket_cfg.single_leg_notional_small,
            "single_leg_notional_normal": basket_cfg.single_leg_notional_normal,
            "city_day_notional_cap": basket_cfg.city_day_notional_cap,
            "max_no_legs_per_city_day": basket_cfg.max_no_legs_per_city_day,
            "edge_small_threshold": basket_cfg.edge_small_threshold,
            "edge_normal_threshold": basket_cfg.edge_normal_threshold,
            "prefer_no_over_yes": basket_cfg.prefer_no_over_yes,
        },
        "slices": {name: _evaluate(_slice(df, name), basket_cfg) for name in slices},
    }

    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-city-day-basket-pr2b-robustness.json"
    md_path = out_dir / f"{today.isoformat()}-city-day-basket-pr2b-robustness.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_md(report, md_path)

    for name, s in report["slices"].items():
        raw = s["rules"]["raw_single"]
        blended = s["rules"]["blended_single"]
        basket = s["rules"]["basket"]
        attr = s["attr_basket_vs_blended"]
        print(
            f"{name}: rows={s['n_rows']} raw={raw['roi']*100:+.2f}% "
            f"blended={blended['roi']*100:+.2f}% basket={basket['roi']*100:+.2f}% "
            f"top5={basket['roi_excl_top5']*100:+.2f}% "
            f"missed={attr['missed_profit_usd']:.2f} avoided={attr['avoided_loss_usd']:.2f} "
            f"gates={s['gate_count']}/4"
        )
    print(f"JSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
