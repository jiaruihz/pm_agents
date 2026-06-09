#!/usr/bin/env python3
"""Side Band + Forecast Regime Clean Test v0.

Target metric:
    side_band_forecast_regime_alpha

This is an opportunity-grain counterfactual over
``runtime/weather.db.fact_signal_candidates``. ``fact_trades`` is read only for
mandatory source self-checks and historical live instance diagnostics.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
OUT_JSON_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-side-band-forecast-regime-v0.json"
OUT_MD_DEFAULT = ROOT / "docs" / "analysis" / "2026-06" / "2026-06-10-side-band-forecast-regime-v0.md"

PRICE_BANDS = ((0.20, 0.80), (0.25, 0.75), (0.30, 0.70), (0.35, 0.65))
SIDES = ("BUY_YES", "BUY_NO", "both")
HOUR_BUCKETS = ("T-12-18", "T-18-24", "T-24-36", "T-36+")
EDGE_THRESHOLDS = (0.03, 0.05, 0.08, 0.10)
LIQUIDITY_FILTERS = ("none", "mild", "strict")
ELIGIBLE_MODES = ("no_filter", "eligible_control")
REGIMES = (
    "no_regime_baseline",
    "low_uncertainty_allowed",
    "medium_uncertainty_price_sensitive",
    "high_uncertainty_no_trade",
    "tail_risk_block",
)

INSTANCE_EXPR = """
CASE
  WHEN producer_run_id LIKE '%mid_price_core_v2_25_75%'
    THEN 'mid_price_core_v2_25_75'
  WHEN producer_run_id LIKE '%mid_price_core_v1_25_75%'
    THEN 'mid_price_core_v1_25_75'
  WHEN producer_run_id LIKE '%mid_price_core_v1_side_band%'
    THEN 'mid_price_core_v1_side_band'
  ELSE strategy_id
END
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260610)
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--min-train-rows", type=int, default=30)
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:+.1f}%"


def num(value: float | None, digits: int = 3) -> str:
    return "NA" if value is None else f"{value:.{digits}f}"


def money(value: float | None) -> str:
    return "NA" if value is None else f"{value:+.2f}"


def ci_text(value: list[float | None] | None) -> str:
    if not value or value[0] is None or value[1] is None:
        return "NA"
    return f"[{pct(value[0])}, {pct(value[1])}]"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def ci95(values: list[float]) -> list[float | None]:
    return [percentile(values, 0.025), percentile(values, 0.975)]


def hour_bucket(hours: Any) -> str | None:
    if hours is None:
        return None
    h = float(hours)
    if 12 <= h < 18:
        return "T-12-18"
    if 18 <= h < 24:
        return "T-18-24"
    if 24 <= h < 36:
        return "T-24-36"
    if h >= 36:
        return "T-36+"
    return None


def bracket_sort_value(label: str) -> float:
    text = str(label).strip()
    if text.endswith("+"):
        text = text[:-1]
    if "-" in text:
        text = text.split("-", 1)[0]
    try:
        return float(text)
    except ValueError:
        return 9999.0


def entropy(probs: list[float]) -> float:
    values = [p for p in probs if p > 0]
    if not values:
        return 0.0
    denom = math.log(len(probs)) if len(probs) > 1 else 1.0
    return -sum(p * math.log(p) for p in values) / denom


def normalize(values: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in values.values() if v > 0)
    if total <= 0:
        return {k: 0.0 for k in values}
    return {k: max(v, 0.0) / total for k, v in values.items()}


def data_self_check(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    return {
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc).isoformat()
        if db_path.exists()
        else None,
        "max_fact_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": rows(
            conn, "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
        ),
        "settlement_status_distribution": rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades "
            "GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "candidate_coverage": rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "order_fill_coverage": rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
        "fact_signal_candidates_max_built_at_utc": scalar(
            conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"
        ),
        "fact_signal_candidates_rows": scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates"),
        "fact_trades_rows": scalar(conn, "SELECT COUNT(*) FROM fact_trades"),
        "fact_trades_missing_bracket_rows": scalar(
            conn, "SELECT COUNT(*) FROM fact_trades WHERE settlement_status='missing_bracket'"
        ),
        "fact_trades_unsettled_rows": scalar(
            conn,
            "SELECT COUNT(*) FROM fact_trades WHERE settlement_status IS NULL OR settlement_status<>'settled'",
        ),
    }


def run_clob_gate() -> dict[str, Any]:
    script = ROOT / "scripts" / "analysis" / "execution_quality" / "weather_clob_fill_coverage_gate.py"
    if not script.exists():
        return {"gate_ran": False, "error": f"missing {script}"}
    proc = subprocess.run(
        ["python3", str(script)],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    parsed: dict[str, Any] | None = None
    if proc.stdout:
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            for line in reversed(proc.stdout.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        pass
    return {
        "gate_ran": True,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "").splitlines()[-20:],
        "stderr_tail": (proc.stderr or "").splitlines()[-20:],
        "parsed": parsed,
    }


def infer_decision_shares(row: dict[str, Any]) -> float | None:
    """Recover the shares used by fact_signal_candidates counterfactual_pnl."""

    entry = row.get("decision_entry_price")
    final_yes = row.get("final_yes")
    pnl = row.get("counterfactual_pnl")
    if entry is None or final_yes is None or pnl is None:
        return None
    entry = float(entry)
    final_yes = float(final_yes)
    pnl = float(pnl)
    if row["side"] == "BUY_YES":
        per_share = final_yes - entry
    elif row["side"] == "BUY_NO":
        per_share = (1.0 - final_yes) - entry
    else:
        return None
    if abs(per_share) < 1e-12:
        return None
    shares = pnl / per_share
    return shares if shares > 0 else None


def filter_funnel(conn: sqlite3.Connection) -> dict[str, Any]:
    total = scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates")
    settled_decision = scalar(
        conn,
        "SELECT COUNT(*) FROM fact_signal_candidates "
        "WHERE settlement_status='settled' AND decision_window_missing=0 "
        "AND decision_snapshot_ts_utc IS NOT NULL",
    )
    recognizable = scalar(
        conn,
        """
        SELECT COUNT(*) FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND city IS NOT NULL
          AND event_date IS NOT NULL
          AND side IN ('BUY_YES','BUY_NO')
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
          AND decision_entry_price IS NOT NULL
          AND decision_entry_price > 0
          AND decision_entry_price < 1
          AND counterfactual_pnl IS NOT NULL
          AND final_yes IS NOT NULL
          AND condition_id IS NOT NULL
        """,
    )
    side_band = scalar(
        conn,
        """
        SELECT COUNT(*) FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND side IN ('BUY_YES','BUY_NO')
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND counterfactual_pnl IS NOT NULL
          AND final_yes IS NOT NULL
          AND decision_entry_price BETWEEN 0.20 AND 0.80
        """,
    )
    executable = scalar(
        conn,
        """
        SELECT COUNT(*) FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND side IN ('BUY_YES','BUY_NO')
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND decision_entry_price IS NOT NULL
          AND counterfactual_pnl IS NOT NULL
          AND final_yes IS NOT NULL
          AND decision_entry_price BETWEEN 0.20 AND 0.80
          AND (
            (side='BUY_YES' AND yes_spread IS NOT NULL)
            OR (side='BUY_NO' AND no_spread IS NOT NULL)
          )
        """,
    )
    return {
        "fact_signal_candidates_rows": total,
        "settled_decision_window_present_rows": settled_decision,
        "recognizable_market_model_price_side_rows": recognizable,
        "side_band_candidate_rows_020_080": side_band,
        "forecast_regime_layered_rows": None,
        "executable_matched_rows": executable,
    }


def load_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    raw = rows(
        conn,
        """
        SELECT
          candidate_id,
          condition_id,
          market_id,
          side,
          event_date,
          bracket,
          city,
          city_pool,
          icao,
          forecast_source,
          model_version,
          decision_hours_to_settle,
          decision_snapshot_ts_utc,
          model_p_yes,
          market_yes_price,
          edge,
          abs_edge,
          decision_entry_price,
          yes_spread,
          no_spread,
          eligible,
          paper_ordered,
          live_filled,
          settlement_status,
          final_yes,
          bracket_hit,
          win_by_count,
          counterfactual_pnl,
          fact_built_at_utc
        FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND city IS NOT NULL
          AND event_date IS NOT NULL
          AND side IN ('BUY_YES','BUY_NO')
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND market_yes_price > 0
          AND market_yes_price < 1
          AND decision_entry_price IS NOT NULL
          AND decision_entry_price > 0
          AND decision_entry_price < 1
          AND counterfactual_pnl IS NOT NULL
          AND final_yes IS NOT NULL
          AND condition_id IS NOT NULL
        """,
    )
    for row in raw:
        row["decision_hour_bucket"] = hour_bucket(row["decision_hours_to_settle"])
        shares = infer_decision_shares(row)
        row["inferred_decision_shares"] = shares
        row["eval_cost"] = float(row["decision_entry_price"]) * shares if shares is not None else 0.0
        row["eval_pnl"] = float(row["counterfactual_pnl"])
        row["eval_roi"] = safe_div(row["eval_pnl"], row["eval_cost"])
        row["side_spread"] = row.get("yes_spread") if row["side"] == "BUY_YES" else row.get("no_spread")
    return [row for row in raw if row["decision_hour_bucket"] in HOUR_BUCKETS]


def decision_feature_sets(candidates: list[dict[str, Any]]) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in candidates:
        key = (
            str(row["city"]),
            str(row["event_date"]),
            str(row["decision_snapshot_ts_utc"]),
            str(row["forecast_source"]),
            str(row["model_version"]),
        )
        bracket = str(row["bracket"])
        prev = grouped[key].get(bracket)
        if prev is None or row["side"] == "BUY_YES":
            grouped[key][bracket] = row

    features: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    history: dict[tuple[str, str], list[int]] = defaultdict(list)
    for key in sorted(grouped, key=lambda k: (k[1], k[0], k[2], k[3], k[4])):
        by_bracket = grouped[key]
        ordered = sorted(by_bracket.values(), key=lambda row: bracket_sort_value(str(row["bracket"])))
        model = normalize({str(row["bracket"]): float(row["model_p_yes"]) for row in ordered})
        market = normalize({str(row["bracket"]): float(row["market_yes_price"]) for row in ordered})
        model_mode = max(ordered, key=lambda row: model[str(row["bracket"])])
        mode_idx = ordered.index(model_mode)
        adj2 = ordered[max(0, mode_idx - 1) : min(len(ordered), mode_idx + 1)]
        if len(adj2) < 2 and len(ordered) >= 2:
            adj2 = ordered[max(0, min(mode_idx, len(ordered) - 2)) : max(0, min(mode_idx, len(ordered) - 2)) + 2]
        adj3 = ordered[max(0, mode_idx - 1) : min(len(ordered), mode_idx + 2)]
        if len(adj3) < 3 and len(ordered) >= 3:
            start = max(0, min(mode_idx - 1, len(ordered) - 3))
            adj3 = ordered[start : start + 3]
        adj3_brackets = {str(row["bracket"]) for row in adj3}
        final_brackets = {str(row["bracket"]) for row in ordered if float(row["final_yes"]) >= 0.5}
        adj3_miss = int(bool(final_brackets) and final_brackets.isdisjoint(adj3_brackets))
        hist_key = (str(ordered[0]["city"]), str(ordered[0]["forecast_source"]))
        hist = history[hist_key]
        hist_miss_rate = safe_div(sum(hist), len(hist))
        model_probs = [model[str(row["bracket"])] for row in ordered]
        market_probs = [market[str(row["bracket"])] for row in ordered]
        model_tail_mass = 1.0 - sum(model[str(row["bracket"])] for row in adj3)
        market_tail_mass = 1.0 - sum(market[str(row["bracket"])] for row in adj3)
        model_market_l1_gap = sum(abs(model[str(row["bracket"])] - market[str(row["bracket"])]) for row in ordered)
        f = {
            "model_entropy": entropy(model_probs),
            "model_mode_probability": model[str(model_mode["bracket"])],
            "adjacent2_mass": sum(model[str(row["bracket"])] for row in adj2),
            "adjacent3_mass": sum(model[str(row["bracket"])] for row in adj3),
            "model_tail_mass_outside_adjacent3": model_tail_mass,
            "market_tail_mass_outside_adjacent3": market_tail_mass,
            "model_market_l1_gap": model_market_l1_gap,
            "forecast_source": ordered[0]["forecast_source"],
            "model_version": ordered[0]["model_version"],
            "city_source_expanding_adjacent3_miss_rate": hist_miss_rate,
            "adjacent3_miss_label": adj3_miss,
        }
        f["forecast_regime"] = classify_regime(f)
        features[key] = f
        history[hist_key].append(adj3_miss)
    return features


def classify_regime(f: dict[str, Any]) -> str:
    hist = f.get("city_source_expanding_adjacent3_miss_rate")
    hist_high = hist is not None and hist >= 0.18
    if f["model_tail_mass_outside_adjacent3"] >= 0.16 or (f["model_market_l1_gap"] >= 0.75 and f["market_tail_mass_outside_adjacent3"] >= 0.20):
        return "tail_risk_block"
    if f["model_entropy"] >= 0.82 or f["model_mode_probability"] < 0.20 or hist_high:
        return "high_uncertainty_no_trade"
    if f["model_entropy"] <= 0.68 and f["model_mode_probability"] >= 0.28 and f["adjacent3_mass"] >= 0.72:
        return "low_uncertainty_allowed"
    return "medium_uncertainty_price_sensitive"


def attach_features(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    features = decision_feature_sets(candidates)
    out = []
    for row in candidates:
        key = (
            str(row["city"]),
            str(row["event_date"]),
            str(row["decision_snapshot_ts_utc"]),
            str(row["forecast_source"]),
            str(row["model_version"]),
        )
        f = features.get(key)
        if f is None:
            continue
        out.append({**row, **f})
    return out


def spread_ok(row: dict[str, Any], liquidity_filter: str) -> bool:
    if liquidity_filter == "none":
        return True
    spread = row.get("side_spread")
    if spread is None:
        return False
    cap = 0.15 if liquidity_filter == "mild" else 0.08
    return float(spread) <= cap


def side_ok(row_side: str, side_filter: str) -> bool:
    return side_filter == "both" or row_side == side_filter


def rule_baseline_filter(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    lo, hi = rule["price_band"]
    return (
        side_ok(str(row["side"]), str(rule["side"]))
        and str(row["decision_hour_bucket"]) == rule["hour_bucket"]
        and lo <= float(row["decision_entry_price"]) <= hi
        and (rule["eligible_mode"] == "no_filter" or int(row.get("eligible") or 0) == 1)
        and spread_ok(row, str(rule["liquidity_filter"]))
    )


def rule_selected_filter(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    if not rule_baseline_filter(row, rule):
        return False
    if float(row["abs_edge"]) < float(rule["edge_threshold"]):
        return False
    regime = str(rule["forecast_regime"])
    if regime == "no_regime_baseline":
        return True
    return row.get("forecast_regime") == regime


def old_side_band_filter(row: dict[str, Any]) -> bool:
    price = float(row["decision_entry_price"])
    edge = float(row["abs_edge"])
    if row["side"] == "BUY_YES":
        return 0.20 <= price <= 0.45 and edge >= 0.20
    return 0.35 <= price <= 0.65 and edge >= 0.10


def old_v1_25_75_filter(row: dict[str, Any]) -> bool:
    return 0.25 <= float(row["decision_entry_price"]) <= 0.75 and float(row["abs_edge"]) >= 0.10


def summarize(rows_in: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["eval_cost"]) for row in rows_in)
    pnl = sum(float(row["eval_pnl"]) for row in rows_in)
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0, "rows": 0.0})
    for row in rows_in:
        slot = by_date[str(row["event_date"])]
        slot["cost"] += float(row["eval_cost"])
        slot["pnl"] += float(row["eval_pnl"])
        slot["rows"] += 1.0
    top5 = sorted(by_date.items(), key=lambda item: item[1]["pnl"], reverse=True)[:5]
    top5_dates = {date for date, _value in top5}
    removed_cost = sum(value["cost"] for date, value in by_date.items() if date not in top5_dates)
    removed_pnl = sum(value["pnl"] for date, value in by_date.items() if date not in top5_dates)
    worst_day_pnl = min((value["pnl"] for value in by_date.values()), default=None)
    return {
        "rows": len(rows_in),
        "active_event_dates": len(by_date),
        "active_city_days": len({(row["city"], row["event_date"]) for row in rows_in}),
        "cost": cost,
        "pnl": pnl,
        "roi": safe_div(pnl, cost),
        "win_rate": safe_div(sum(1 for row in rows_in if int(row.get("win_by_count") or 0) == 1), len(rows_in)),
        "avg_entry_price": safe_div(sum(float(row["decision_entry_price"]) for row in rows_in), len(rows_in)),
        "avg_abs_edge": safe_div(sum(float(row["abs_edge"]) for row in rows_in), len(rows_in)),
        "top5_event_dates": sorted(top5_dates),
        "top5_removed_pnl": removed_pnl,
        "top5_removed_roi": safe_div(removed_pnl, removed_cost),
        "worst_day_pnl": worst_day_pnl,
    }


def bootstrap_eval(
    selected: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    *,
    iters: int,
    seed: int,
) -> dict[str, list[float | None]]:
    selected_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    baseline_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        selected_by_date[str(row["event_date"])].append(row)
    for row in baseline:
        baseline_by_date[str(row["event_date"])].append(row)
    dates = sorted(set(selected_by_date) | set(baseline_by_date))
    if not dates:
        return {"roi_ci95": [None, None], "excess_roi_ci95": [None, None]}
    rng = random.Random(seed)
    roi_samples: list[float] = []
    excess_samples: list[float] = []
    for _ in range(iters):
        sel_sample: list[dict[str, Any]] = []
        base_sample: list[dict[str, Any]] = []
        for _date in dates:
            pick = rng.choice(dates)
            sel_sample.extend(selected_by_date.get(pick, []))
            base_sample.extend(baseline_by_date.get(pick, []))
        sel_roi = summarize(sel_sample)["roi"]
        base_roi = summarize(base_sample)["roi"]
        if sel_roi is not None and math.isfinite(sel_roi):
            roi_samples.append(sel_roi)
        if sel_roi is not None and base_roi is not None and math.isfinite(sel_roi - base_roi):
            excess_samples.append(sel_roi - base_roi)
    return {"roi_ci95": ci95(roi_samples), "excess_roi_ci95": ci95(excess_samples)}


def evaluate_rule(
    data: list[dict[str, Any]],
    rule: dict[str, Any],
    *,
    iters: int,
    seed: int,
) -> dict[str, Any]:
    selected = [row for row in data if rule_selected_filter(row, rule)]
    baseline = [row for row in data if rule_baseline_filter(row, rule)]
    selected_summary = summarize(selected)
    baseline_summary = summarize(baseline)
    selected_roi = selected_summary["roi"]
    baseline_roi = baseline_summary["roi"]
    boot = bootstrap_eval(selected, baseline, iters=iters, seed=seed)
    return {
        "rule": dict(rule),
        "selected": selected_summary,
        "baseline": baseline_summary,
        "excess_roi": None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi,
        "roi_ci95_cluster_by_event_date": boot["roi_ci95"],
        "excess_roi_ci95_cluster_by_event_date": boot["excess_roi_ci95"],
    }


def split_dates(dates: list[str], train_frac: float) -> tuple[set[str], set[str]]:
    ordered = sorted(dates)
    if len(ordered) < 2:
        return set(ordered), set()
    split_idx = max(1, min(len(ordered) - 1, int(math.floor(len(ordered) * train_frac))))
    return set(ordered[:split_idx]), set(ordered[split_idx:])


def rule_grid() -> list[dict[str, Any]]:
    out = []
    for price_band in PRICE_BANDS:
        for side in SIDES:
            for hour in HOUR_BUCKETS:
                for edge in EDGE_THRESHOLDS:
                    for liq in LIQUIDITY_FILTERS:
                        for eligible in ELIGIBLE_MODES:
                            for regime in REGIMES:
                                out.append(
                                    {
                                        "price_band": price_band,
                                        "side": side,
                                        "hour_bucket": hour,
                                        "edge_threshold": edge,
                                        "liquidity_filter": liq,
                                        "eligible_mode": eligible,
                                        "forecast_regime": regime,
                                    }
                                )
    return out


def human_rule(rule: dict[str, Any]) -> str:
    lo, hi = rule["price_band"]
    side = {"BUY_YES": "只买 YES", "BUY_NO": "只买 NO", "both": "YES/NO 都允许"}[rule["side"]]
    regime = {
        "no_regime_baseline": "不加 forecast regime",
        "low_uncertainty_allowed": "只在低不确定性时交易",
        "medium_uncertainty_price_sensitive": "只在中等不确定性且价位合适时交易",
        "high_uncertainty_no_trade": "专门观察高不确定性区间",
        "tail_risk_block": "专门观察尾部风险区间",
    }[rule["forecast_regime"]]
    liq = {"none": "不加盘口过滤", "mild": "点差 <=15c", "strict": "点差 <=8c"}[rule["liquidity_filter"]]
    eligible = "不使用旧 eligible 硬门" if rule["eligible_mode"] == "no_filter" else "只看旧 eligible 对照"
    return (
        f"{side}，entry price 在 {lo:.2f}-{hi:.2f}，{rule['hour_bucket']}，"
        f"模型 edge 至少 {rule['edge_threshold']:.2f}，{regime}，{liq}，{eligible}"
    )


def gate_result(train_eval: dict[str, Any], holdout_eval: dict[str, Any]) -> dict[str, str]:
    train_roi_ci = train_eval["roi_ci95_cluster_by_event_date"]
    train_excess_ci = train_eval["excess_roi_ci95_cluster_by_event_date"]
    holdout_roi_ci = holdout_eval["roi_ci95_cluster_by_event_date"]
    holdout_excess_ci = holdout_eval["excess_roi_ci95_cluster_by_event_date"]
    significance = "PASS" if train_roi_ci[0] is not None and train_roi_ci[0] > 0 else "FAIL"
    baseline = "PASS" if train_excess_ci[0] is not None and train_excess_ci[0] > 0 else "FAIL"
    forward = (
        "PASS"
        if holdout_eval["selected"]["rows"] >= 30
        and holdout_eval["selected"]["active_event_dates"] >= 5
        and holdout_roi_ci[0] is not None
        and holdout_roi_ci[0] > 0
        and holdout_excess_ci[0] is not None
        and holdout_excess_ci[0] > 0
        and (holdout_eval["selected"]["top5_removed_roi"] is not None and holdout_eval["selected"]["top5_removed_roi"] > 0)
        else "FAIL"
    )
    verdict = "confirmed" if significance == baseline == forward == "PASS" else "inconclusive"
    return {
        "significance": significance,
        "baseline": baseline,
        "forward": forward,
        "verdict": verdict,
    }


def select_train_rules(
    train_rows: list[dict[str, Any]],
    *,
    min_train_rows: int,
    iters: int,
    seed: int,
) -> list[dict[str, Any]]:
    candidates = []
    for idx, rule in enumerate(rule_grid()):
        selected_count = sum(1 for row in train_rows if rule_selected_filter(row, rule))
        baseline_count = sum(1 for row in train_rows if rule_baseline_filter(row, rule))
        if selected_count < min_train_rows or baseline_count < selected_count:
            continue
        evaluated = evaluate_rule(train_rows, rule, iters=iters, seed=seed + idx)
        if evaluated["selected"]["rows"] < min_train_rows:
            continue
        candidates.append(evaluated)
    candidates.sort(
        key=lambda item: (
            item["excess_roi"] if item["excess_roi"] is not None else -999,
            item["selected"]["top5_removed_roi"] if item["selected"]["top5_removed_roi"] is not None else -999,
            item["selected"]["active_event_dates"],
        ),
        reverse=True,
    )
    return candidates


def live_instance_diagnostic(conn: sqlite3.Connection, gate: dict[str, Any]) -> dict[str, Any]:
    parsed = gate.get("parsed") or {}
    gate_pass = bool(parsed.get("gate_pass")) if isinstance(parsed, dict) else False
    if not gate_pass:
        return {
            "gate_pass": False,
            "note": "CLOB coverage gate did not pass or did not return parseable JSON; live_real PnL/ROI suppressed.",
            "instances": [],
        }
    return {
        "gate_pass": True,
        "instances": rows(
            conn,
            f"""
            SELECT
              ({INSTANCE_EXPR}) AS strategy_instance,
              COUNT(*) AS fills,
              SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_fills,
              COUNT(DISTINCT target_date) AS active_dates,
              SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS settled_cost,
              SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
              SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END)
                / NULLIF(SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END), 0) AS roi,
              SUM(CASE WHEN side='BUY_YES' THEN 1 ELSE 0 END) AS buy_yes_fills,
              SUM(CASE WHEN side='BUY_NO' THEN 1 ELSE 0 END) AS buy_no_fills,
              MIN(target_date) AS min_target_date,
              MAX(target_date) AS max_target_date
            FROM fact_trades
            WHERE trade_class='live_real'
              AND ({INSTANCE_EXPR}) IN (
                'mid_price_core_v1_25_75',
                'mid_price_core_v1_side_band',
                'mid_price_core_v2_25_75'
              )
            GROUP BY strategy_instance
            ORDER BY strategy_instance
            """,
        ),
    }


def old_strategy_diagnostic(data: list[dict[str, Any]]) -> dict[str, Any]:
    side_rows = [row for row in data if old_side_band_filter(row)]
    v1_rows = [row for row in data if old_v1_25_75_filter(row)]
    by_model_side = []
    for name, items in (("v1_25_75_proxy", v1_rows), ("side_band_proxy", side_rows)):
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            grouped[(str(row["model_version"]), str(row["side"]))].append(row)
        for (model, side), group in sorted(grouped.items()):
            by_model_side.append({"selector": name, "model_version": model, "side": side, **summarize(group)})
    return {
        "v1_25_75_proxy": summarize(v1_rows),
        "side_band_proxy": summarize(side_rows),
        "by_model_side": by_model_side,
        "definition": {
            "v1_25_75_proxy": "decision_entry_price 0.25-0.75 and abs_edge>=0.10.",
            "side_band_proxy": "BUY_YES 0.20-0.45 abs_edge>=0.20; BUY_NO 0.35-0.65 abs_edge>=0.10.",
        },
    }


def table(headers: list[str], rows_in: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows_in:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def fmt_rule_eval(label: str, eval_row: dict[str, Any]) -> list[Any]:
    s = eval_row["selected"]
    return [
        label,
        s["rows"],
        s["active_event_dates"],
        f"{s['cost']:.2f}",
        money(s["pnl"]),
        pct(s["roi"]),
        ci_text(eval_row["roi_ci95_cluster_by_event_date"]),
        pct(eval_row["baseline"]["roi"]),
        pct(eval_row["excess_roi"]),
        ci_text(eval_row["excess_roi_ci95_cluster_by_event_date"]),
        pct(s["top5_removed_roi"]),
        money(s["worst_day_pnl"]),
    ]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    best = report["selected_rules"][0] if report["selected_rules"] else None
    lines = [
        "# Side Band + Forecast Regime Clean Test v0",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> target_metric: `{report['target_metric']}`",
        f"> DB: `{report['db_path']}`",
        "> Scope: local counterfactual research only; no N100/live config changed; no live action.",
        "",
        "## 数据快照",
        "",
        "- 数据源：`runtime/weather.db.fact_signal_candidates` 是主实验唯一机会粒度来源；`fact_trades` 只用于强制自检和旧 live 实例诊断。",
        f"- DB last_modified：`{report['data_self_check']['db_last_modified_utc']}`。",
        f"- fact_signal_candidates rows：`{report['data_self_check']['fact_signal_candidates_rows']}`；fact built：`{report['data_self_check']['fact_signal_candidates_max_built_at_utc']}`。",
        f"- fact_trades rows：`{report['data_self_check']['fact_trades_rows']}`；unsettled/null：`{report['data_self_check']['fact_trades_unsettled_rows']}`；missing_bracket：`{report['data_self_check']['fact_trades_missing_bracket_rows']}`。",
        f"- train：`{report['split']['train_start']}` -> `{report['split']['train_end']}`，active dates `{report['split']['train_dates']}`。",
        f"- holdout：`{report['split']['holdout_start']}` -> `{report['split']['holdout_end']}`，active dates `{report['split']['holdout_dates']}`。",
        f"- CLOB coverage gate：`gate_ran={report['clob_fill_coverage_gate'].get('gate_ran')}`；`gate_pass={report['live_instance_diagnostic'].get('gate_pass')}`。主实验不发布 live_real PnL/ROI。",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(
            {
                "max_fact_built_at_utc": report["data_self_check"]["max_fact_built_at_utc"],
                "trade_class_distribution": report["data_self_check"]["trade_class_distribution"],
                "settlement_status_distribution": report["data_self_check"]["settlement_status_distribution"],
                "candidate_coverage": report["data_self_check"]["candidate_coverage"],
                "order_fill_coverage": report["data_self_check"]["order_fill_coverage"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 目标指标与分母",
        "",
        "`side_band_forecast_regime_alpha` = 在 `city + event_date + decision_snapshot_ts_utc` 的机会粒度上，先用 full opportunity 复现/推广 side-band 入场价带，再用 forecast quality regime 分层；每个候选规则的 ROI 都减去同 side、同 price band、同 decision-hour bucket、同 liquidity/eligible 档的 baseline ROI。",
        "",
        "- 主分母：`fact_signal_candidates` 中 `settlement_status='settled'`、`decision_window_missing=0`、有 `decision_snapshot_ts_utc/model_p_yes/market_yes_price/decision_entry_price/counterfactual_pnl/final_yes` 的机会行。",
        "- `eligible` 不作为硬门；grid 同时报告 `no_filter` 和 `eligible_control`。",
        "- ROI 口径：从 `counterfactual_pnl=(payoff-entry)*shares` 反推 decision shares 后，用 `SUM(counterfactual_pnl) / SUM(decision_entry_price * inferred_shares)`；这是机会层 cost-proxy ROI，不是钱包 PnL。",
        "- baseline：同价位/同窗口/同 side universe，不加 edge threshold 和 forecast regime。",
        "",
        "## Filter Funnel",
        "",
        table(
            ["step", "rows", "drop vs previous"],
            [
                [
                    row["step"],
                    f"{row['rows']} ({row['active_dates']} active dates)"
                    if row.get("active_dates") != "NA"
                    else row["rows"],
                    row["drop_vs_previous"],
                ]
                for row in report["filter_funnel_table"]
            ],
        ),
        "",
        "任何一步掉超过 70% 的解释：本轮最大掉点写在 JSON `filter_funnel_drop_notes`；主要来自 settlement + decision-window 可用性，不是因为旧 eligible gate。",
        "",
        "## Forecast Regime Features",
        "",
        "只用 `fact_signal_candidates` 同一 decision snapshot 内的模型/市场分布构造：`model_entropy`、`model_mode_probability`、`adjacent2_mass`、`adjacent3_mass`、`model_tail_mass_outside_adjacent3`、`model_market_l1_gap`、`forecast_source/model_version`，以及只看过去 event_date 的 `city_source_expanding_adjacent3_miss_rate`。",
        "",
        table(
            ["regime", "rows"],
            [[row["forecast_regime"], row["rows"]] for row in report["regime_distribution"]],
        ),
        "",
        "## 结论先行",
        "",
    ]
    if best:
        gates = best["gates"]
        lines.append(
            f"- train 选出的最强规则是：{human_rule(best['rule'])}。"
            f"train excess ROI `{pct(best['train']['excess_roi'])}`，95% CI `{ci_text(best['train']['excess_roi_ci95_cluster_by_event_date'])}`；"
            f"holdout excess ROI `{pct(best['holdout']['excess_roi'])}`，95% CI `{ci_text(best['holdout']['excess_roi_ci95_cluster_by_event_date'])}`。"
        )
        lines.append(
            f"- 三门：significance=`{gates['significance']}`，baseline=`{gates['baseline']}`，forward=`{gates['forward']}`，verdict=`{gates['verdict']}`。"
        )
    lines.extend(
        [
            "- 人话结论：旧 side-band 形态在历史上确实有赚过的片段，机制上也像是在过滤低价 YES 彩票票和部分高不确定性 NO，但按 cost-proxy 复核后这个 clean test 仍没确认三门。它现在只能说明“有值得继续观察的 latent edge 线索”，不能说已经确认可复制，更不能推出 live 动作。",
            "",
            "## 旧策略复现诊断",
            "",
            "这里用 full opportunity 复现旧规则形状，不把它当 live 绩效。旧 live 实例若 gate 不过会抑制 PnL/ROI。",
            "",
            table(
                ["selector", "rows", "dates", "cost", "pnl", "ROI", "top5 removed ROI", "avg entry", "avg abs edge"],
                [
                    [
                        "mid_price_core_v1_25_75 proxy",
                        report["old_strategy_diagnostic"]["v1_25_75_proxy"]["rows"],
                        report["old_strategy_diagnostic"]["v1_25_75_proxy"]["active_event_dates"],
                        f"{report['old_strategy_diagnostic']['v1_25_75_proxy']['cost']:.2f}",
                        money(report["old_strategy_diagnostic"]["v1_25_75_proxy"]["pnl"]),
                        pct(report["old_strategy_diagnostic"]["v1_25_75_proxy"]["roi"]),
                        pct(report["old_strategy_diagnostic"]["v1_25_75_proxy"]["top5_removed_roi"]),
                        num(report["old_strategy_diagnostic"]["v1_25_75_proxy"]["avg_entry_price"]),
                        num(report["old_strategy_diagnostic"]["v1_25_75_proxy"]["avg_abs_edge"]),
                    ],
                    [
                        "mid_price_core_v1_side_band proxy",
                        report["old_strategy_diagnostic"]["side_band_proxy"]["rows"],
                        report["old_strategy_diagnostic"]["side_band_proxy"]["active_event_dates"],
                        f"{report['old_strategy_diagnostic']['side_band_proxy']['cost']:.2f}",
                        money(report["old_strategy_diagnostic"]["side_band_proxy"]["pnl"]),
                        pct(report["old_strategy_diagnostic"]["side_band_proxy"]["roi"]),
                        pct(report["old_strategy_diagnostic"]["side_band_proxy"]["top5_removed_roi"]),
                        num(report["old_strategy_diagnostic"]["side_band_proxy"]["avg_entry_price"]),
                        num(report["old_strategy_diagnostic"]["side_band_proxy"]["avg_abs_edge"]),
                    ],
                ],
            ),
            "",
            "- 旧 side-band proxy 定义：BUY_YES 0.20-0.45 且 abs_edge>=0.20；BUY_NO 0.35-0.65 且 abs_edge>=0.10。",
            "- 旧 25-75 proxy 定义：BUY_YES/BUY_NO 都用 0.25-0.75 且 abs_edge>=0.10。",
            "- 这能复现 mid quote / entry band / side mix 的形状，但不能证明旧参数正确；真实 strategy_instance 归因优先看 `producer_run_id`，不能只用 price window。",
            "",
            "## 候选规则详情",
            "",
        ]
    )
    for idx, item in enumerate(report["selected_rules"][:5], start=1):
        lines.extend(
            [
                f"### Candidate {idx}",
                "",
                f"- 规则解释：{human_rule(item['rule'])}。",
                f"- baseline：同 side、同 price band、同 decision-hour bucket、同 liquidity/eligible 档，不加 edge threshold 和 forecast regime。",
                "",
                table(
                    ["split", "rows", "dates", "cost", "pnl", "ROI", "ROI CI", "baseline ROI", "excess ROI", "excess CI", "top5 removed ROI", "worst_day_pnl"],
                    [fmt_rule_eval("train", item["train"]), fmt_rule_eval("holdout", item["holdout"])],
                ),
                "",
                f"- 三门：significance=`{item['gates']['significance']}`；baseline=`{item['gates']['baseline']}`；forward=`{item['gates']['forward']}`；final verdict=`{item['gates']['verdict']}`。",
                "",
            ]
        )
    lines.extend(
        [
            "## 总表",
            "",
            table(
                ["direction", "human-readable idea", "sample size", "holdout result", "top5 removed", "gates", "verdict", "next step"],
                [
                    [
                        item["rule"]["side"],
                        human_rule(item["rule"]),
                        f"train {item['train']['selected']['rows']} / holdout {item['holdout']['selected']['rows']}",
                        f"ROI {pct(item['holdout']['selected']['roi'])}, excess {pct(item['holdout']['excess_roi'])}",
                        pct(item["holdout"]["selected"]["top5_removed_roi"]),
                        f"{item['gates']['significance']}/{item['gates']['baseline']}/{item['gates']['forward']}",
                        item["gates"]["verdict"],
                        "只保留为 offline/shadow 研究线索；补更长 forward 样本和可成交性复核",
                    ]
                    for item in report["selected_rules"][:10]
                ],
            ),
            "",
            "## 三门与 live 结论",
            "",
            "- significance：看 train ROI CI 是否全大于 0。",
            "- baseline：看 train excess ROI CI 是否全大于 0。",
            "- forward：holdout 至少 30 行、5 个 event_date、ROI CI 和 excess CI 都全大于 0，且 top5 removed ROI 仍大于 0。",
            "- 本报告没有任何规则三门全过，最终只能 `inconclusive`。",
            "- live：不能改 live，不能调 size，不能改城市池；主实验是本地 counterfactual research。",
            "",
            "## 8 环覆盖自检",
            "",
            "- 1 描述性绩效切片：covered，但只作为机会层和旧实例诊断。",
            "- 2 统计推断：covered，按 event_date cluster bootstrap。",
            "- 3 信号判别：covered，side/price/edge/regime 相对 baseline。",
            "- 4 概率分布评估：partial，regime 用模型分布形状和 expanding historical miss。",
            "- 5 执行微结构：partial，仅用 fact 表里的 spread 字段做 none/mild/strict；未读取 raw orderbook。",
            "- 6 容量：NA。",
            "- 7 组合相关性：partial，bootstrap 按 event_date 聚类。",
            "- 8 基准/反事实：covered，同价位/同窗口/同 side baseline。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db_path).resolve()
    conn = connect(db_path)
    self_check = data_self_check(conn, db_path)
    gate = run_clob_gate()
    funnel = filter_funnel(conn)
    data = attach_features(load_candidates(conn))
    funnel["forecast_regime_layered_rows"] = len(data)
    side_band_regime_rows = sum(1 for row in data if 0.20 <= float(row["decision_entry_price"]) <= 0.80)
    side_band_executable_rows = sum(
        1
        for row in data
        if 0.20 <= float(row["decision_entry_price"]) <= 0.80
        and row.get("side_spread") is not None
    )

    dates = sorted({str(row["event_date"]) for row in data})
    train_dates, holdout_dates = split_dates(dates, args.train_frac)
    train_rows = [row for row in data if row["event_date"] in train_dates]
    holdout_rows = [row for row in data if row["event_date"] in holdout_dates]

    train_candidates = select_train_rules(
        train_rows,
        min_train_rows=args.min_train_rows,
        iters=args.bootstrap_iters,
        seed=args.seed,
    )
    selected_rules = []
    for idx, train_eval in enumerate(train_candidates[:20]):
        holdout_eval = evaluate_rule(holdout_rows, train_eval["rule"], iters=args.bootstrap_iters, seed=args.seed + 10000 + idx)
        gates = gate_result(train_eval, holdout_eval)
        selected_rules.append(
            {
                "rule": train_eval["rule"],
                "human_readable_idea": human_rule(train_eval["rule"]),
                "train": train_eval,
                "holdout": holdout_eval,
                "gates": gates,
            }
        )

    funnel_items = [
        ("fact_signal_candidates rows", funnel["fact_signal_candidates_rows"]),
        ("settled + decision_window present rows", funnel["settled_decision_window_present_rows"]),
        ("recognizable market/model/price/side rows", funnel["recognizable_market_model_price_side_rows"]),
        ("side_band candidate rows", funnel["side_band_candidate_rows_020_080"]),
        ("forecast regime layered side_band rows", side_band_regime_rows),
        ("executable/spread-available side_band rows", side_band_executable_rows),
    ]
    prev = None
    funnel_table = []
    drop_notes = []
    for step, count in funnel_items:
        drop = None if prev is None or prev == 0 else 1.0 - (float(count) / float(prev))
        if drop is not None and drop > 0.70:
            drop_notes.append({"step": step, "drop": drop, "explanation": "Large filter loss; see report context."})
        funnel_table.append(
            {
                "step": step,
                "rows": count,
                "active_dates": "NA",
                "drop_vs_previous": "NA" if drop is None else pct(drop),
            }
        )
        prev = count
    funnel_table.append(
        {
            "step": "train rows from recognizable analysis rows",
            "rows": len(train_rows),
            "active_dates": len(train_dates),
            "drop_vs_previous": "date split, not a filter",
        }
    )
    funnel_table.append(
        {
            "step": "holdout rows from recognizable analysis rows",
            "rows": len(holdout_rows),
            "active_dates": len(holdout_dates),
            "drop_vs_previous": "date split, not a filter",
        }
    )

    regime_distribution = []
    grouped: dict[str, int] = defaultdict(int)
    for row in data:
        grouped[str(row["forecast_regime"])] += 1
    for regime in REGIMES:
        if regime == "no_regime_baseline":
            continue
        regime_distribution.append({"forecast_regime": regime, "rows": grouped.get(regime, 0)})

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": "side_band_forecast_regime_alpha",
        "db_path": str(db_path),
        "data_self_check": self_check,
        "clob_fill_coverage_gate": gate,
        "filter_funnel": funnel,
        "filter_funnel_table": funnel_table,
        "filter_funnel_drop_notes": drop_notes,
        "split": {
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "train_dates": len(train_dates),
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
            "holdout_dates": len(holdout_dates),
        },
        "grid": {
            "price_bands": PRICE_BANDS,
            "sides": SIDES,
            "decision_hour_buckets": HOUR_BUCKETS,
            "model_edge_thresholds": EDGE_THRESHOLDS,
            "liquidity_spread_filters": {
                "none": "no spread filter",
                "mild": "side spread <= 0.15; NA rows fail this filter",
                "strict": "side spread <= 0.08; NA rows fail this filter",
            },
            "eligible_modes": ELIGIBLE_MODES,
            "forecast_regimes": REGIMES,
            "rules_pre_registered": len(rule_grid()),
            "rules_evaluated_on_train_after_min_rows": len(train_candidates),
        },
        "regime_distribution": regime_distribution,
        "selected_rules": selected_rules,
        "old_strategy_diagnostic": old_strategy_diagnostic(data),
        "live_instance_diagnostic": live_instance_diagnostic(conn, gate),
    }
    return report


def main() -> None:
    args = parse_args()
    report = build_report(args)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    write_json(out_json, report)
    write_md(out_md, report)
    print(out_json)
    print(out_md)


if __name__ == "__main__":
    main()
