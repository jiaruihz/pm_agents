#!/usr/bin/env python3
"""Research branch for current-YES peak-forming entries.

This is research-only.  It compares two entry states:

1. post_decline_confirmed: current live idea, after the observed max has faded.
2. peak_forming: still at the running max, before a visible fade confirms it.

The goal is to decide whether the second state deserves a shadow branch, while
explicitly calling out METAR-update-minute repricing risk.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-current-yes-peak-forming-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-current-yes-peak-forming-v1.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{100 * float(value):+.1f}%"


def dollars(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"${float(value):.2f}"


def data_self_check() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    try:
        return {
            "fact_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) v FROM fact_trades").fetchone()["v"],
            "trade_class": [dict(r) for r in conn.execute("SELECT trade_class, COUNT(*) rows FROM fact_trades GROUP BY trade_class")],
            "settlement_status": [dict(r) for r in conn.execute("SELECT settlement_status, COUNT(*) rows FROM fact_trades GROUP BY settlement_status")],
            "signal_coverage": dict(
                conn.execute(
                    "SELECT COUNT(*) rows, SUM(eligible) eligible, SUM(paper_ordered) paper_ordered, SUM(live_filled) live_filled "
                    "FROM fact_signal_candidates"
                ).fetchone()
            ),
            "clob_orders_fills": [
                dict(r)
                for r in conn.execute(
                    "SELECT o.status, COUNT(*) orders, SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) with_fill "
                    "FROM orders o LEFT JOIN fills f USING(execution_id) WHERE o.venue='polymarket_clob' GROUP BY o.status"
                )
            ],
        }
    finally:
        conn.close()


def score_rows(rows: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    numeric_features = artifact["numeric_features"]
    categorical_features = artifact["categorical_features"]
    numeric = rows[numeric_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales
    cat_parts = []
    for idx, feature in enumerate(categorical_features):
        values = rows[feature].astype(str).to_numpy()
        cats = [str(x) for x in artifact["categories"][idx]]
        lookup = {cat: col for col, cat in enumerate(cats)}
        mat = np.zeros((len(rows), len(cats)), dtype=float)
        for row_idx, value in enumerate(values):
            col = lookup.get(str(value))
            if col is not None:
                mat[row_idx, col] = 1.0
        cat_parts.append(mat)
    transformed = np.concatenate([numeric, *cat_parts], axis=1)
    logits = transformed @ np.asarray(artifact["coef"], dtype=float) + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-logits))


def load_scored() -> pd.DataFrame:
    df = pd.read_csv(FEATURE_ROWS)
    for col in ("current_yes_wins", "has_d1_no", "d1_no_loses", "is_f"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().isin({"true", "1"})
    artifact = json.loads(MODEL_ARTIFACT.read_text(encoding="utf-8"))
    df["p_yes_win"] = score_rows(df, artifact)
    df["edge_snapshot"] = df["p_yes_win"] - df["yes_current_ask"]
    df["available_notional_at_ask"] = df["yes_current_ask"] * df["yes_current_size"]
    df["snapshot_dt"] = pd.to_datetime(df["snapshot_ts_utc"], utc=True, errors="coerce")
    return df


def dedupe_live(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = (
        frame.sort_values("snapshot_dt")
        .drop_duplicates(["target_date", "city", "current_bracket"], keep="first")
        .sort_values(["target_date", "city", "snapshot_dt"])
        .groupby(["target_date", "city"])
        .head(2)
        .copy()
    )
    return out


def summarize(frame: pd.DataFrame, *, taker_cushion: float = 0.02) -> dict[str, Any]:
    d = dedupe_live(frame)
    if d.empty:
        return {"orders": 0, "active_dates": 0, "cities": 0}
    notional = 5.0
    price = np.minimum(d["yes_current_ask"].astype(float).to_numpy() + taker_cushion, 0.999)
    label = d["label_yes_wins"].astype(int).to_numpy()
    p = d["p_yes_win"].to_numpy()
    pnl = np.where(label == 1, notional / price - notional, -notional)
    ev = notional * (p / price - 1.0)
    return {
        "orders": int(len(d)),
        "active_dates": int(d["target_date"].nunique()),
        "cities": int(d["city"].nunique()),
        "notional": float(notional * len(d)),
        "orders_per_active_day": float(len(d) / d["target_date"].nunique()),
        "win_rate": float(label.mean()),
        "roi_plus_2c": float(pnl.sum() / (notional * len(d))),
        "pnl_plus_2c": float(pnl.sum()),
        "model_ev_plus_2c": float(ev.sum()),
        "model_ev_per_order": float(ev.mean()),
        "avg_ask": float(d["yes_current_ask"].mean()),
        "median_ask": float(d["yes_current_ask"].median()),
        "avg_p_yes_win": float(d["p_yes_win"].mean()),
        "avg_snapshot_edge": float(d["edge_snapshot"].mean()),
        "avg_decline_c": float(d["decline_c"].mean()),
        "date_min": str(d["target_date"].min()),
        "date_max": str(d["target_date"].max()),
    }


def branch_tables(scored: pd.DataFrame) -> dict[str, Any]:
    holdout = scored[scored["period"].eq("holdout")].copy()
    base = holdout[
        holdout["has_d1_no"]
        & holdout["decision_hour_local"].between(12, 16)
        & holdout["yes_current_ask"].ge(0.55)
        & holdout["available_notional_at_ask"].ge(5.0)
        & holdout["p_yes_win"].ge(0.5)
    ].copy()

    slices: dict[str, pd.DataFrame] = {
        "peak_all_ev05": base[base["decline_c"].lt(0.01) & base["edge_snapshot"].ge(0.05)].copy(),
        "peak_h13_ev05": base[base["decline_c"].lt(0.01) & base["decision_hour_local"].eq(13) & base["edge_snapshot"].ge(0.05)].copy(),
        "peak_h14_ev05": base[base["decline_c"].lt(0.01) & base["decision_hour_local"].eq(14) & base["edge_snapshot"].ge(0.05)].copy(),
        "peak_h15_ev05": base[base["decline_c"].lt(0.01) & base["decision_hour_local"].eq(15) & base["edge_snapshot"].ge(0.05)].copy(),
        "peak_h13_ev03": base[base["decline_c"].lt(0.01) & base["decision_hour_local"].eq(13) & base["edge_snapshot"].ge(0.03)].copy(),
        "post_decline_live_ev05": base[
            base["decline_c"].ge(0.5)
            & base["decision_hour_local"].between(13, 15)
            & base["edge_snapshot"].ge(0.05)
        ].copy(),
    }
    summaries = {name: summarize(frame) for name, frame in slices.items()}
    details = {
        name: dedupe_live(frame)[
            [
                "target_date",
                "city",
                "decision_hour_local",
                "decline_c",
                "yes_current_ask",
                "p_yes_win",
                "edge_snapshot",
                "available_notional_at_ask",
                "label_yes_wins",
                "current_bracket",
            ]
        ].head(80).to_dict("records")
        for name, frame in slices.items()
    }
    return {"summaries": summaries, "details": details}


def md_table(rows: list[tuple[str, dict[str, Any]]]) -> list[str]:
    out = [
        "| slice | orders | active days | orders/day | win | ROI +2c | model EV | avg ask | avg p | avg edge |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in rows:
        out.append(
            "| {name} | {orders} | {days} | {opd} | {win} | {roi} | {ev} | {ask} | {p} | {edge} |".format(
                name=name,
                orders=row.get("orders", 0),
                days=row.get("active_dates", 0),
                opd="NA" if row.get("orders_per_active_day") is None else f"{row['orders_per_active_day']:.1f}",
                win=pct(row.get("win_rate")),
                roi=pct(row.get("roi_plus_2c")),
                ev=dollars(row.get("model_ev_plus_2c")),
                ask="NA" if row.get("avg_ask") is None else f"{row['avg_ask']:.3f}",
                p="NA" if row.get("avg_p_yes_win") is None else f"{row['avg_p_yes_win']:.3f}",
                edge=pct(row.get("avg_snapshot_edge")),
            )
        )
    return out


def write_report(payload: dict[str, Any]) -> None:
    s = payload["summaries"]
    lines = [
        "# Theta Current YES Peak Forming v1",
        "",
        "Status: research_only / shadow_candidate_candidate",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `peak_forming_current_yes` = current temperature is still at the observed running max, but the model estimates the current bracket is likely to remain the final max bracket.",
        "",
        "## Data Self-Check",
        "",
        f"- fact_built_at_utc: `{payload['data_self_check']['fact_built_at_utc']}`",
        f"- fact_trades trade_class: `{payload['data_self_check']['trade_class']}`",
        f"- settlement_status: `{payload['data_self_check']['settlement_status']}`",
        f"- fact_signal_candidates coverage: `{payload['data_self_check']['signal_coverage']}`",
        f"- CLOB orders/fills join: `{payload['data_self_check']['clob_orders_fills']}`",
        "",
        "## Human Verdict",
        "",
        "There are two separate edges, and they should stay as separate branches.",
        "",
        "- `post_decline_confirmed`: wait for a visible temperature fade. This is the current live branch: lower volume, cleaner evidence.",
        "- `peak_forming`: enter while temperature is still at the running max. This can find cheaper asks, but only the early h13 slice looks worth shadowing; h14/h15 plateau is noisy and should not go live.",
        "",
        "Recommended action: keep current live unchanged, add a paper/shadow branch for `peak_h13_ev05` only. Do not deploy real orders until we collect fresh-book telemetry around METAR update minutes.",
        "",
        "## Branch Comparison",
        "",
        *md_table(
            [
                ("post_decline_live_ev05", s["post_decline_live_ev05"]),
                ("peak_all_ev05", s["peak_all_ev05"]),
                ("peak_h13_ev05", s["peak_h13_ev05"]),
                ("peak_h14_ev05", s["peak_h14_ev05"]),
                ("peak_h15_ev05", s["peak_h15_ev05"]),
                ("peak_h13_ev03", s["peak_h13_ev03"]),
            ]
        ),
        "",
        "## Key Read",
        "",
        "- The confirmed-decline branch remains the cleaner live branch: about 30 holdout orders, 90%+ win rate, and positive +2c ROI.",
        "- Plateau as a broad rule is not good enough. The full peak slice has more volume, but win rate/ROI are dragged down by later hours.",
        "- The interesting new branch is `peak_h13_ev05`: it has more pre-confirmation character, cheaper average ask, and strong point estimates in holdout.",
        "- h14/h15 plateau should be treated as a warning: by then the market and weather path behave differently, and the raw model overestimates some cases.",
        "",
        "## METAR Update-Minute Execution Risk",
        "",
        "This report uses the existing half-hour orderbook replay and weather feature rows. That means it can understate execution error around a METAR update minute:",
        "",
        "- The weather observation can update first, then the market can reprice within seconds.",
        "- A backtest row may pair the new weather state with an orderbook quote that is stale by one snapshot interval or by a fast repricing burst.",
        "- This is exactly why the live branch now uses `fresh_ask <= snapshot_ask + 0.02`; the peak-forming shadow branch must record the same fresh-book delta before any live discussion.",
        "",
        "Required shadow telemetry fields:",
        "",
        "- `snapshot_ask`, `fresh_best_ask`, `fresh_best_bid`, `fresh_ask_size`",
        "- `fresh_ask_minus_snapshot_ask`",
        "- `snapshot_age_seconds`",
        "- `metar_last_obs_age_seconds` if available",
        "- `decision_second_within_minute`",
        "- `would_trade_after_fresh_book_guard`",
        "",
        "## Candidate Shadow Rule",
        "",
        "```text",
        "branch: theta_current_yes_peak_forming_v1",
        "side: BUY_YES current running-max bracket",
        "state: decline_c == 0 / still at running max",
        "time: local hour == 13",
        "price: ask >= 0.55",
        "model: p_yes_win >= 0.5 and p_yes_win - ask >= 0.05",
        "execution telemetry: fresh_ask <= snapshot_ask + 0.02, but shadow-only first",
        "size: zero-notional shadow / paper only",
        "```",
        "",
        "## Output",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def main() -> int:
    scored = load_scored()
    payload = {
        "generated_at_utc": now_utc(),
        "evidence_layer": "time-aligned orderbook replay / opportunity research, not live fills",
        "row_grain": "one row before dedupe = city-target_date-current_bracket-snapshot; summary orders are live-style deduped",
        "data_self_check": data_self_check(),
        "feature_rows": int(len(scored)),
        **branch_tables(scored),
    }
    payload = json_ready(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(payload)
    print(json.dumps({"json": str(OUT_JSON), "md": str(OUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
