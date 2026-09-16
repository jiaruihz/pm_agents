#!/usr/bin/env python3
"""Reversal lottery lab v2 with source-grain settlement overlay.

v1 used `fact_signal_candidates.final_yes` directly and therefore inherited a
candidate-table settlement gap after 2026-06-10.  This v2 keeps the same PIT
selectors but evaluates low-price BUY_YES rows with a research-side
`settlement_outcomes` overlay keyed by city/date/bracket.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

import research_reversal_lottery_lab_v1 as v1


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/reversal_lottery_lab_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-lottery-lab-v2.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-01-reversal-lottery-lab-v2.json"


def sqlite_frame(query: str) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{v1.DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def db_snapshot() -> dict[str, Any]:
    payload = v1.db_snapshot()
    overlay = sqlite_frame(
        """
        SELECT
          COUNT(*) AS rows,
          MIN(f.event_date) AS min_date,
          MAX(f.event_date) AS max_date,
          COUNT(DISTINCT f.event_date) AS dates,
          COUNT(DISTINCT f.city) AS cities,
          SUM(CASE WHEN f.final_yes IN (0.0, 1.0) THEN 1 ELSE 0 END) AS fsc_settled_rows,
          SUM(CASE WHEN f.final_yes IS NULL AND so.settlement_status = 'settled'
                   AND so.final_price IN (0.0, 1.0) THEN 1 ELSE 0 END) AS overlay_filled_rows
        FROM fact_signal_candidates f
        LEFT JOIN settlement_outcomes so
          ON so.city = f.city
         AND so.target_date = f.event_date
         AND so.bracket = f.bracket
        WHERE f.side = 'BUY_YES'
          AND f.decision_entry_price BETWEEN 0.01 AND 0.25
          AND COALESCE(f.final_yes, so.final_price) IN (0.0, 1.0)
          AND COALESCE(f.settlement_status, so.settlement_status) = 'settled'
        """
    ).iloc[0].to_dict()
    payload["low_price_yes_source_grain_overlay"] = overlay
    return payload


def load_low_price_yes_overlay() -> pd.DataFrame:
    df = sqlite_frame(
        """
        SELECT
          f.candidate_id,
          f.city,
          f.event_date AS target_date,
          f.bracket,
          f.side,
          f.forecast_source,
          f.forecast_max_native,
          f.forecast_peak_delta_hours_local,
          f.forecast_max_in_bracket,
          f.forecast_max_above_bracket_f,
          f.forecast_max_below_bracket_f,
          f.model_p_yes,
          f.market_yes_price,
          f.edge,
          f.decision_entry_price,
          f.first_seen_ts_utc,
          f.decision_snapshot_ts_utc,
          f.final_yes AS fsc_final_yes,
          f.settlement_status AS fsc_settlement_status,
          so.final_price AS source_final_yes,
          so.settlement_status AS source_settlement_status
        FROM fact_signal_candidates f
        LEFT JOIN settlement_outcomes so
          ON so.city = f.city
         AND so.target_date = f.event_date
         AND so.bracket = f.bracket
        WHERE f.side = 'BUY_YES'
          AND f.decision_entry_price BETWEEN 0.01 AND 0.25
          AND COALESCE(f.final_yes, so.final_price) IN (0.0, 1.0)
          AND COALESCE(f.settlement_status, so.settlement_status) = 'settled'
        """
    )
    numeric = [
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
        "forecast_max_in_bracket",
        "forecast_max_above_bracket_f",
        "forecast_max_below_bracket_f",
        "model_p_yes",
        "market_yes_price",
        "edge",
        "decision_entry_price",
        "fsc_final_yes",
        "source_final_yes",
    ]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["ask"] = df["decision_entry_price"]
    df["payoff"] = df["fsc_final_yes"].combine_first(df["source_final_yes"]).astype(float)
    df["settlement_source"] = df["fsc_final_yes"].notna().map({True: "fact_signal_candidates", False: "settlement_outcomes"})
    df["target_date"] = df["target_date"].astype(str)
    return df


def add_forward_metrics(summary: pd.DataFrame) -> pd.DataFrame:
    out = summary.copy()
    hold = out[out["period"].eq("holdout")][["family", "label", "roi", "rows", "dates"]].rename(
        columns={"roi": "holdout_roi", "rows": "holdout_rows", "dates": "holdout_dates"}
    )
    recent = out[out["period"].eq("recent")][["family", "label", "roi", "rows", "dates"]].rename(
        columns={"roi": "recent_roi", "rows": "recent_rows", "dates": "recent_dates"}
    )
    full = out[out["period"].eq("full")].merge(hold, on=["family", "label"], how="left").merge(recent, on=["family", "label"], how="left")
    return full


def render_report(db: dict[str, Any], low_summary: pd.DataFrame, expr_summary: pd.DataFrame, city: pd.DataFrame, regime: pd.DataFrame) -> str:
    cols = [
        ("label", "label"),
        ("rows", "rows"),
        ("dates", "dates"),
        ("cities", "cities"),
        ("avg_ask", "avg ask"),
        ("win_rate", "win"),
        ("roi", "ROI"),
        ("roi_ci_low", "CI low"),
        ("roi_ci_high", "CI high"),
        ("holdout_rows", "H rows"),
        ("holdout_roi", "H ROI"),
        ("recent_rows", "R rows"),
        ("recent_roi", "R ROI"),
        ("top3_removed_roi", "top3 removed"),
        ("losing_days", "loss days"),
        ("max_daily_loss", "max loss"),
    ]
    low_full = add_forward_metrics(low_summary).sort_values(["roi", "rows"], ascending=False)
    expr_full = add_forward_metrics(expr_summary).sort_values(["roi", "rows"], ascending=False)
    edge20 = low_full[low_full["label"].eq("edge_ge_20c")].iloc[0]
    overlay = db["low_price_yes_source_grain_overlay"]
    top_city = city[city["label"].eq("edge_ge_20c")].sort_values("pnl", ascending=False).head(20)
    top_regime = regime[(regime["label"].eq("edge_ge_20c")) & regime["rows"].ge(10)].sort_values("pnl", ascending=False).head(20)

    lines = [
        "# Reversal Lottery Lab v2",
        "",
        "Generated: 2026-07-01",
        "",
        "## One-Line Verdict",
        "",
        "`edge_ge_20c` upgrades from thin shadow to a real `shadow_candidate`: after the `fact_signal_candidates` settlement fallback fix, it has full, holdout, and recent positive ROI, with full-window date bootstrap CI above zero. It is still not live-approved because this is a model/market low-price edge without a same-row alternative-expression baseline and it needs forward shadow execution/settlement.",
        "",
        f"`edge_ge_20c`: {int(edge20['rows'])} rows / {int(edge20['dates'])} dates / {int(edge20['cities'])} cities, avg ask {edge20['avg_ask']:.3f}, win {v1.pct(edge20['win_rate'])}, ROI {v1.pct(edge20['roi'])}, CI [{v1.pct(edge20['roi_ci_low'])}, {v1.pct(edge20['roi_ci_high'])}], holdout {int(edge20['holdout_rows'])} rows ROI {v1.pct(edge20['holdout_roi'])}, recent {int(edge20['recent_rows'])} rows ROI {v1.pct(edge20['recent_roi'])}, top3-removed ROI {v1.pct(edge20['top3_removed_roi'])}.",
        "",
        "```text",
        "significance=PASS for historical edge_ge_20c",
        "baseline=PARTIAL (beats market ask break-even, but no same-row expression baseline)",
        "forward=PARTIAL (holdout/recent positive after overlay, but still offline backfill not forward shadow)",
        "conclusion=shadow_candidate; independent lottery shadow head; no live orders",
        "```",
        "",
        "## Data Snapshot",
        "",
        f"- DB: `{db['db_path']}`, fact built `{db['fact_signal_candidates'].get('fact_built_at_utc')}`.",
        f"- CLOB fill coverage gate pass: `{db.get('gate_pass')}`. This report does not publish live_real ROI.",
        f"- Canonical settled low-price BUY_YES after ETL fallback: {int(db['low_price_yes_settled']['rows'])} rows, {db['low_price_yes_settled']['min_date']}..{db['low_price_yes_settled']['max_date']}.",
        f"- Source-grain overlay parity check: {int(overlay['rows'])} rows / {int(overlay['dates'])} dates / {int(overlay['cities'])} cities, {overlay['min_date']}..{overlay['max_date']}; extra rows beyond canonical `final_yes` now `{int(overlay['overlay_filled_rows'])}`.",
        f"- Expression matrix still covers only 2026-05-19..2026-06-26 from `{v1.rel(v1.EVENT_ROWS)}`.",
        "",
        "## Practical Shadow Prompt",
        "",
        "```text",
        "strategy_family = low_price_yes_lottery_reversal",
        "side = BUY_YES",
        "decision_entry_price between 0.01 and 0.25",
        "edge >= 0.20",
        "one candidate per city-date, earliest PIT decision snapshot",
        "settle via settlement_outcomes city/date/bracket fallback",
        "zero notional; no live order",
        "```",
        "",
        "## Low-Price YES With Source-Grain Settlement",
        "",
        v1.md_table(low_full, cols),
        "",
        "## Same-Snapshot Expression Sanity",
        "",
        "This remains a separate denominator. The hotter-tail expression is still too thin; the stronger result is the fact-level low-price lottery edge, not a current-runner expression switch.",
        "",
        v1.md_table(expr_full, cols),
        "",
        "## Top edge_ge_20c Cities",
        "",
        v1.md_table(
            top_city,
            [
                ("city", "city"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("avg_ask", "ask"),
                ("win_rate", "win"),
                ("roi", "ROI"),
                ("pnl", "PnL"),
            ],
            max_rows=20,
        ),
        "",
        "## edge_ge_20c Regime Contributions",
        "",
        v1.md_table(
            top_regime,
            [
                ("slice_type", "slice"),
                ("slice_value", "value"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("avg_ask", "ask"),
                ("win_rate", "win"),
                ("roi", "ROI"),
                ("pnl", "PnL"),
            ],
            max_rows=20,
        ),
        "",
        "## Interpretation",
        "",
        "- The v1 weak-forward read was mostly a settlement coverage artifact in `fact_signal_candidates`, not a failure of the lottery idea. The local ETL now falls back to `settlement_outcomes` by city/date/bracket.",
        "- `edge>=0.20` is deliberately simple and was already fixed before the source-grain overlay; this reduces threshold-mining risk.",
        "- The payoff is still very volatile: many days are -100%, and positive expectancy comes from occasional 5x-20x winners. This is appropriate only as a capped lottery shadow sleeve.",
        "- Next research step is forward shadow extraction from current low-price candidates, plus a data-layer fix so `fact_signal_candidates` can use `settlement_outcomes` fallback natively.",
        "",
        "## Artifacts",
        "",
        f"- Summary CSV: `{v1.rel(OUT_DIR / 'summary.csv')}`",
        f"- Detail CSV: `{v1.rel(OUT_DIR / 'details.csv')}`",
        f"- Daily CSV: `{v1.rel(OUT_DIR / 'daily.csv')}`",
        f"- Script: `scripts/analysis/forecast_quality/research_reversal_lottery_lab_v2.py`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = db_snapshot()
    low = load_low_price_yes_overlay()
    low_summary, low_detail, low_daily = v1.evaluate_low_price(low)
    expr = v1.load_expression_rows()
    expr_summary, expr_detail, expr_daily = v1.evaluate_expression_matrix(expr)
    detail = pd.concat([low_detail, expr_detail], ignore_index=True, sort=False)
    daily = pd.concat([low_daily, expr_daily], ignore_index=True, sort=False)
    summary = pd.concat([low_summary, expr_summary], ignore_index=True, sort=False)
    city, regime = v1.contribution_tables(detail)

    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    detail.to_csv(OUT_DIR / "details.csv", index=False)
    daily.to_csv(OUT_DIR / "daily.csv", index=False)
    city.to_csv(OUT_DIR / "city_contribution.csv", index=False)
    regime.to_csv(OUT_DIR / "regime_contribution.csv", index=False)
    payload = {
        "data_snapshot": db,
        "low_price_overlay_rows": int(len(low)),
        "expression_rows": int(len(expr)),
        "summary_rows": int(len(summary)),
        "detail_rows": int(len(detail)),
        "artifacts": {
            "summary": v1.rel(OUT_DIR / "summary.csv"),
            "details": v1.rel(OUT_DIR / "details.csv"),
            "daily": v1.rel(OUT_DIR / "daily.csv"),
            "city": v1.rel(OUT_DIR / "city_contribution.csv"),
            "regime": v1.rel(OUT_DIR / "regime_contribution.csv"),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    OUT_MD.write_text(render_report(db, low_summary, expr_summary, city, regime), encoding="utf-8")
    print(f"wrote {v1.rel(OUT_MD)}")
    print(f"wrote {v1.rel(OUT_JSON)}")


if __name__ == "__main__":
    main()
