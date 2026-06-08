"""
backtest_weather_edge_engine_blended_single.py

Offline backtest for strategy_id=weather_edge_engine_blended_single_v0.

The strategy is a shadow candidate: it keeps the current single-leg market
shape but uses market-anchored blended probability for the executable edge.
This script compares it against:
  - raw_single: legacy raw model probability rule on the same opportunity set
  - market_only: no raw model contribution
  - current live actual fills from fact_trades, grouped by inferred instance

Important: blended_single_v0 opportunity PnL is a counterfactual replay over
fact_signal_candidates, not wallet PnL and not an actual live fill record.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from scripts.analysis.eval_city_day_basket import (  # noqa: E402
    DB_DEFAULT,
    OUT_DEFAULT,
    _attribution,
    _decide_blended_single,
    _decide_market_only,
    _decide_raw_single,
    _summarize,
)
from weather_dashboard.blend import load_default_config  # noqa: E402

STRATEGY_ID = "weather_edge_engine_blended_single_v0"
STRATEGY_SPEC = (
    _ROOT
    / "weather_dashboard"
    / "strategy_specs"
    / "weather_edge_engine_blended_single_v0.json"
)

INSTANCE_CASE = """
CASE
  WHEN run_id LIKE '%_mid_price_core_v1_25_75' THEN 'mid_price_core_v1_25_75'
  WHEN run_id LIKE '%_mid_price_core_v2_25_75' THEN 'mid_price_core_v2_25_75'
  WHEN run_id LIKE '%_mid_price_core_v1_side_band' THEN 'mid_price_core_v1_side_band'
  WHEN execution_policy='mid_price_core_v2' AND entry_price_window='0.25-0.75' THEN 'mid_price_core_v2_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window='0.25-0.75' THEN 'mid_price_core_v1_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window IN ('0.20-0.45','0.35-0.65') THEN 'mid_price_core_v1_side_band'
  ELSE COALESCE(strategy_id, 'unknown')
END
"""


def _fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _data_self_check(conn: sqlite3.Connection) -> dict[str, list]:
    checks = {
        "fact_trades_freshness": "SELECT MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades",
        "fact_trades_by_class": "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
        "fact_trades_by_settlement": "SELECT settlement_status, COUNT(*) AS rows FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        "fact_signal_candidates_coverage": "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        "clob_orders_with_fills": "SELECT o.status, COUNT(*) AS orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
    }
    return {name: _fetchall(conn, sql) for name, sql in checks.items()}


def _load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
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
    return pd.read_sql_query(sql, conn)


def _side_roi(summary: dict, side: str) -> float:
    st = summary.get("by_side", {}).get(side)
    if not st:
        return 0.0
    cost = st.get("total_cost_usd") or 0.0
    return (st.get("total_pnl_usd") or 0.0) / cost if cost else 0.0


def _summaries_for_df(df: pd.DataFrame) -> dict:
    blend_cfg = load_default_config()
    raw_legs = _decide_raw_single(df, edge_threshold=0.03, notional=5.0)
    market_legs = _decide_market_only(df, edge_threshold=0.03, notional=5.0)
    blended_legs = _decide_blended_single(
        df, blend_cfg=blend_cfg, edge_threshold=0.03, notional=5.0
    )

    raw_s = asdict(_summarize("raw_single", raw_legs))
    market_s = asdict(_summarize("market_only", market_legs))
    blended_s = asdict(_summarize(STRATEGY_ID, blended_legs))
    attr = _attribution(blended_legs, raw_legs)
    return {
        "n_rows": int(len(df)),
        "date_range": [
            str(df["event_date"].min()) if len(df) else None,
            str(df["event_date"].max()) if len(df) else None,
        ],
        "rules": {
            "raw_single": raw_s,
            "market_only": market_s,
            STRATEGY_ID: blended_s,
        },
        "attribution_blended_vs_raw": {
            "raw_winning_profit_missed_by_blend": attr["missed_profit_usd"],
            "raw_losing_cost_avoided_by_blend": attr["avoided_loss_usd"],
            "shared_profit": attr["shared_profit_usd"],
            "shared_loss": attr["shared_loss_usd"],
            "net_blended_vs_raw": attr["net_basket_vs_baseline_usd"],
        },
        "buy_no_roi": _side_roi(blended_s, "BUY_NO"),
        "buy_yes_roi": _side_roi(blended_s, "BUY_YES"),
    }


def _current_live_actual(conn: sqlite3.Connection) -> dict:
    rows = _fetchall(
        conn,
        f"""
        SELECT
          {INSTANCE_CASE} AS strategy_instance,
          COUNT(*) AS fills,
          COUNT(DISTINCT target_date) AS active_target_days,
          MIN(target_date) AS min_target_date,
          MAX(target_date) AS max_target_date,
          SUM(cost_usd) AS cost_usd,
          SUM(pnl_usd_at_fill) AS pnl_usd,
          AVG(CAST(win_by_count AS REAL)) AS win_rate
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
        GROUP BY strategy_instance
        ORDER BY pnl_usd DESC
        """,
    )
    for row in rows:
        cost = row.get("cost_usd") or 0.0
        pnl = row.get("pnl_usd") or 0.0
        row["roi"] = pnl / cost if cost else 0.0

    by_side = _fetchall(
        conn,
        f"""
        SELECT
          {INSTANCE_CASE} AS strategy_instance,
          side,
          COUNT(*) AS fills,
          SUM(cost_usd) AS cost_usd,
          SUM(pnl_usd_at_fill) AS pnl_usd,
          AVG(CAST(win_by_count AS REAL)) AS win_rate
        FROM fact_trades
        WHERE trade_class='live_real'
          AND settlement_status='settled'
        GROUP BY strategy_instance, side
        ORDER BY strategy_instance, side
        """,
    )
    for row in by_side:
        cost = row.get("cost_usd") or 0.0
        pnl = row.get("pnl_usd") or 0.0
        row["roi"] = pnl / cost if cost else 0.0
    return {"by_instance": rows, "by_instance_side": by_side}


def _write_markdown(report: dict, out_path: Path) -> None:
    full = report["opportunity_backtest"]["full"]
    live_subset = report["opportunity_backtest"]["live_filled_only"]
    live_actual = report["current_live_actual"]["by_instance"]

    lines: list[str] = [
        "# Weather Edge Engine Blended Single v0 Backtest",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> strategy_id: `{STRATEGY_ID}`",
        f"> strategy_spec: `{report['strategy_spec']}`",
        f"> data: `{report['db_path']}`",
        "",
        "## Strategy Identity",
        "",
        "| field | value |",
        "|---|---|",
        f"| strategy_id | `{STRATEGY_ID}` |",
        "| strategy_family | `weather_edge_engine` |",
        "| probability_source | `blended` |",
        "| decision_mode | `single_leg_shadow` |",
        "| execution_mode | `shadow` |",
        "| code | `weather_dashboard/blend/blender.py` |",
        "| config | `weather_dashboard/blend/city_blend_config.json` |",
        "",
        "This strategy does not submit orders. It is a counterfactual shadow replay over the opportunity table.",
        "",
        "## Data Self-Check",
        "",
        "```json",
        json.dumps(report["data_self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Opportunity Backtest",
        "",
        "| slice | rule | legs | cost | pnl | ROI | win_rate | top5 ROI |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for slice_name, block in report["opportunity_backtest"].items():
        for rule_name, s in block["rules"].items():
            lines.append(
                f"| {slice_name} | {rule_name} | {s['n_legs']} | "
                f"${s['total_cost_usd']:.0f} | ${s['total_pnl_usd']:+.0f} | "
                f"{s['roi']*100:+.2f}% | {s['win_rate']*100:.1f}% | "
                f"{s['roi_excl_top5']*100:+.2f}% |"
            )
    lines.extend([
        "",
        "## Blended vs Raw Attribution",
        "",
        "| slice | raw winning profit missed | raw losing cost avoided | shared profit | shared loss | net blended vs raw |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for slice_name, block in report["opportunity_backtest"].items():
        a = block["attribution_blended_vs_raw"]
        lines.append(
            f"| {slice_name} | ${a['raw_winning_profit_missed_by_blend']:.0f} | "
            f"${a['raw_losing_cost_avoided_by_blend']:.0f} | "
            f"${a['shared_profit']:.0f} | ${a['shared_loss']:.0f} | "
            f"${a['net_blended_vs_raw']:+.0f} |"
        )
    lines.extend([
        "",
        "## Current Live Actual Fills",
        "",
        "These rows are real `trade_class='live_real'` settled fills. They are not the same denominator as the shadow opportunity replay.",
        "",
        "| strategy_instance | fills | target dates | cost | pnl | ROI | win_rate |",
        "|---|---:|---|---:|---:|---:|---:|",
    ])
    for row in live_actual:
        lines.append(
            f"| {row['strategy_instance']} | {row['fills']} | "
            f"{row['min_target_date']} -> {row['max_target_date']} | "
            f"${(row['cost_usd'] or 0):.0f} | ${(row['pnl_usd'] or 0):+.0f} | "
            f"{(row['roi'] or 0)*100:+.2f}% | {(row['win_rate'] or 0)*100:.1f}% |"
        )

    full_blend = full["rules"][STRATEGY_ID]
    live_blend = live_subset["rules"][STRATEGY_ID]
    lines.extend([
        "",
        "## Read",
        "",
        f"- Full opportunity sample: `{STRATEGY_ID}` improves raw_single ROI "
        f"from {full['rules']['raw_single']['roi']*100:+.2f}% to {full_blend['roi']*100:+.2f}%.",
        f"- `live_filled_only` opportunity subset: `{STRATEGY_ID}` improves raw_single ROI "
        f"from {live_subset['rules']['raw_single']['roi']*100:+.2f}% to {live_blend['roi']*100:+.2f}%.",
        "- This is not a production approval: the strategy has not been shadow-written on N100逐 snapshot lineage yet.",
        "- Next step is PR3a shadow double-write with the lineage fields listed in the strategy spec.",
    ])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    db_path = DB_DEFAULT
    with sqlite3.connect(str(db_path)) as conn:
        df = _load_candidates(conn)
        report = {
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "strategy_id": STRATEGY_ID,
            "strategy_spec": str(STRATEGY_SPEC.relative_to(_ROOT)),
            "db_path": str(db_path),
            "data_self_check": _data_self_check(conn),
            "opportunity_backtest": {
                "full": _summaries_for_df(df),
                "holdout_from_2026_05_26": _summaries_for_df(
                    df[df["event_date"] >= "2026-05-26"].reset_index(drop=True)
                ),
                "recent_from_2026_06_01": _summaries_for_df(
                    df[df["event_date"] >= "2026-06-01"].reset_index(drop=True)
                ),
                "live_filled_only": _summaries_for_df(
                    df[df["live_filled"] == 1].reset_index(drop=True)
                ),
            },
            "current_live_actual": _current_live_actual(conn),
        }

    today = dt.date.today()
    out_dir = OUT_DEFAULT / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{today.isoformat()}-blended-single-v0-backtest.json"
    md_path = out_dir / f"{today.isoformat()}-blended-single-v0-backtest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, md_path)

    print(f"strategy_id={STRATEGY_ID}")
    for name, block in report["opportunity_backtest"].items():
        raw = block["rules"]["raw_single"]
        blended = block["rules"][STRATEGY_ID]
        print(
            f"{name}: raw ROI={raw['roi']*100:+.2f}% "
            f"blended ROI={blended['roi']*100:+.2f}% "
            f"raw pnl={raw['total_pnl_usd']:+.2f} "
            f"blended pnl={blended['total_pnl_usd']:+.2f}"
        )
    print(f"JSON written to {json_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
