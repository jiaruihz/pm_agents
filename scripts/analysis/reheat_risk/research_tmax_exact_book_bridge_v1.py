#!/usr/bin/env python3
"""Exact-book bridge replay for the Tmax distribution expression layer.

This is the smallest honest bridge between the existing four-bucket Tmax model
and the intended exact-bracket target book:

  - keep the existing current/d1/d2/tail probabilities fixed
  - add executable d1/d2 YES sibling expressions from the complementary NO bid
  - select one first eligible city-day event with taker fee included

It is research/shadow only.  It does not produce live orders and does not claim
to be a full ladder model; probabilities beyond d2 remain collapsed into tail.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
P5_OPPS = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1/opportunities.csv"
ATLAS = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/"
    / "intraday_weather_regime_state_rows.csv"
)
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-tmax-exact-book-bridge-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-tmax-exact-book-bridge-v1.json"
DB_PATH = ROOT / "runtime/weather.db"

METHOD = "loo_no_city_source_blend"
ASK_FLOORS = [0.20, 0.40]
ASK_CEILING = 0.99
EDGE_THRESHOLD = 0.02
TAKER_FEE_RATE = 0.05
FIXED_SHARES = 5.0
BOOT_N = 2000
BOOT_SEED = 20260705

EXPRESSION_SETS = {
    "legacy_4expr": ["current_yes", "current_no", "d1_no", "d2_no"],
    "legacy_no_current_yes_3expr": ["current_no", "d1_no", "d2_no"],
    "bridge_6expr": ["current_yes", "current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"],
    "bridge_no_current_yes_5expr": ["current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"],
}
SELECTION_RULES = {
    "gross_edge02": "gross_edge",
    "fee_edge02": "fee_adjusted_edge",
}
FOCUS_ROWS = [
    ("legacy_4expr", "gross_edge02", 0.20),
    ("legacy_4expr", "fee_edge02", 0.20),
    ("legacy_no_current_yes_3expr", "fee_edge02", 0.20),
    ("legacy_4expr", "fee_edge02", 0.40),
    ("legacy_no_current_yes_3expr", "fee_edge02", 0.40),
    ("bridge_6expr", "fee_edge02", 0.40),
    ("bridge_no_current_yes_5expr", "fee_edge02", 0.40),
]


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return _json_ready(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _fmt_pct(value: object, *, signed: bool = True) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    sign = "+" if signed else ""
    return f"{v:{sign}.1%}"


def _fmt_num(value: object, digits: int = 2) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    return f"{v:.{digits}f}"


def _fee(price: float) -> float:
    if not math.isfinite(price):
        return math.nan
    return TAKER_FEE_RATE * price * (1.0 - price)


def _side(expr: str) -> str:
    return "YES" if str(expr).endswith("_yes") else "NO"


def _bucket(expr: str) -> str:
    if str(expr).startswith("current"):
        return "current"
    if str(expr).startswith("d1"):
        return "d1"
    if str(expr).startswith("d2"):
        return "d2"
    return "unknown"


def _date_block_ci(rows: pd.DataFrame, *, cost_col: str = "cost_net", pnl_col: str = "pnl_net") -> dict[str, float]:
    if rows.empty:
        return {"roi": math.nan, "ci_low": math.nan, "ci_high": math.nan}
    cost = float(rows[cost_col].sum())
    pnl = float(rows[pnl_col].sum())
    roi = pnl / cost if cost else math.nan
    by_day = rows.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    if len(by_day) < 3:
        return {"roi": roi, "ci_low": math.nan, "ci_high": math.nan}
    rng = np.random.default_rng(BOOT_SEED)
    arr = by_day[["cost", "pnl"]].to_numpy(dtype=float)
    boot = []
    for _ in range(BOOT_N):
        sample = arr[rng.integers(0, len(arr), size=len(arr))]
        c = sample[:, 0].sum()
        p = sample[:, 1].sum()
        boot.append(p / c if c else math.nan)
    return {
        "roi": roi,
        "ci_low": float(np.nanpercentile(boot, 2.5)),
        "ci_high": float(np.nanpercentile(boot, 97.5)),
    }


def _inventory() -> dict[str, Any]:
    out: dict[str, Any] = {
        "p5_opportunities": {},
        "atlas": {},
    }
    if P5_OPPS.exists():
        p5_dates = pd.read_csv(P5_OPPS, usecols=["target_date", "scope"])
        out["p5_opportunities"] = {
            "rows": int(len(p5_dates)),
            "min_date": str(p5_dates["target_date"].min()),
            "max_date": str(p5_dates["target_date"].max()),
            "scopes": {str(k): int(v) for k, v in p5_dates["scope"].value_counts(dropna=False).to_dict().items()},
        }
    if ATLAS.exists():
        atlas_dates = pd.read_csv(ATLAS, usecols=["target_date"])
        out["atlas"] = {
            "rows": int(len(atlas_dates)),
            "min_date": str(atlas_dates["target_date"].min()),
            "max_date": str(atlas_dates["target_date"].max()),
        }
    if DB_PATH.exists():
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        try:
            for table, date_col in [
                ("fact_signal_candidates", "event_date"),
                ("fact_trades", "target_date"),
                ("settlement_outcomes", "target_date"),
            ]:
                out[table] = dict(
                    conn.execute(
                        f"SELECT COUNT(*) AS rows, MIN({date_col}) AS min_date, MAX({date_col}) AS max_date "
                        f"FROM {table}"
                    ).fetchone()
                )
        finally:
            conn.close()
    return out


def _load_opps() -> pd.DataFrame:
    if not P5_OPPS.exists():
        raise FileNotFoundError(f"missing P5 opportunities: {P5_OPPS}")
    df = pd.read_csv(P5_OPPS)
    required = {
        "scope",
        "method",
        "expression",
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "ask",
        "p_win",
        "model_edge",
        "model_roi",
        "win",
        "unit_pnl",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"P5 opportunities missing columns: {missing}")
    df = df[df["method"].eq(METHOD)].copy()
    for col in [
        "decision_hour_local",
        "ask",
        "ask_size",
        "p_win",
        "model_edge",
        "model_roi",
        "win",
        "unit_pnl",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "wind_speed_kt",
        "relative_humidity_pct",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_quote_bridge() -> pd.DataFrame:
    if not ATLAS.exists():
        raise FileNotFoundError(f"missing atlas rows: {ATLAS}")
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "current_bracket",
        "d1_no_bracket",
        "d1_no_bid",
        "d2_no_bracket",
        "d2_no_bid",
        "current_no_bid",
    ]
    quote = pd.read_csv(ATLAS, usecols=lambda c: c in set(cols))
    quote = quote.drop_duplicates(["city", "target_date", "decision_hour_local"]).copy()
    for col in ["decision_hour_local", "d1_no_bid", "d2_no_bid", "current_no_bid"]:
        if col in quote.columns:
            quote[col] = pd.to_numeric(quote[col], errors="coerce")
    quote["d1_yes_ask_proxy"] = 1.0 - quote["d1_no_bid"]
    quote["d2_yes_ask_proxy"] = 1.0 - quote["d2_no_bid"]
    return quote


def _add_common_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["side"] = out["expression"].map(_side)
    out["bucket"] = out["expression"].map(_bucket)
    out["taker_fee"] = out["ask"].astype(float).map(_fee)
    out["gross_edge"] = out["p_win"].astype(float) - out["ask"].astype(float)
    out["fee_adjusted_edge"] = out["gross_edge"] - out["taker_fee"]
    out["gross_roi_model"] = out["gross_edge"] / out["ask"].replace(0, np.nan)
    out["fee_adjusted_roi_model"] = out["fee_adjusted_edge"] / (out["ask"] + out["taker_fee"]).replace(0, np.nan)
    out["cost_gross"] = out["ask"].astype(float)
    out["pnl_gross"] = out["unit_pnl"].astype(float)
    out["cost_net"] = out["ask"].astype(float) + out["taker_fee"].astype(float)
    out["pnl_net"] = out["unit_pnl"].astype(float) - out["taker_fee"].astype(float)
    out["cost_fixed5_net"] = out["cost_net"] * FIXED_SHARES
    out["pnl_fixed5_net"] = out["pnl_net"] * FIXED_SHARES
    return out


def build_expression_candidates() -> pd.DataFrame:
    opps = _load_opps()
    quote = _load_quote_bridge()
    base = opps.merge(
        quote,
        on=["city", "target_date", "decision_hour_local"],
        how="left",
        validate="many_to_one",
    )
    base["quote_source"] = "p5_native_expression"
    base["ask_source"] = "native_ask"

    siblings = []
    for no_expr, yes_expr, ask_col, bucket_col in [
        ("d1_no", "d1_yes", "d1_yes_ask_proxy", "d1_no_bracket"),
        ("d2_no", "d2_yes", "d2_yes_ask_proxy", "d2_no_bracket"),
    ]:
        sub = base[base["expression"].eq(no_expr)].copy()
        if sub.empty:
            continue
        sub["expression"] = yes_expr
        sub["ask"] = pd.to_numeric(sub[ask_col], errors="coerce")
        sub["ask_size"] = np.nan
        sub["p_win"] = 1.0 - pd.to_numeric(sub["p_win"], errors="coerce")
        sub["win"] = sub["actual_bucket"].eq(_bucket(yes_expr)).astype(float)
        sub["unit_pnl"] = sub["win"] - sub["ask"]
        sub["model_edge"] = sub["p_win"] - sub["ask"]
        sub["model_roi"] = sub["model_edge"] / sub["ask"].replace(0, np.nan)
        sub["quote_source"] = "atlas_complement_no_bid"
        sub["ask_source"] = f"1_minus_{no_expr}_bid"
        sub["sibling_bracket"] = sub[bucket_col]
        siblings.append(sub)

    out = pd.concat([base, *siblings], ignore_index=True) if siblings else base
    out = out[out["ask"].notna() & out["p_win"].notna() & out["unit_pnl"].notna()].copy()
    out = out[out["ask"].ge(0.0) & out["ask"].le(1.0)].copy()
    out = _add_common_columns(out)
    return out.sort_values(["scope", "city", "target_date", "decision_hour_local", "expression"]).reset_index(drop=True)


def select_policy(
    candidates: pd.DataFrame,
    *,
    expression_set: str,
    scope: str,
    selection_rule: str,
    ask_floor: float,
) -> pd.DataFrame:
    expressions = EXPRESSION_SETS[expression_set]
    edge_col = SELECTION_RULES[selection_rule]
    eligible = candidates[
        candidates["scope"].eq(scope)
        & candidates["expression"].isin(expressions)
        & candidates["ask"].ge(ask_floor)
        & candidates["ask"].le(ASK_CEILING)
        & candidates[edge_col].ge(EDGE_THRESHOLD)
    ].copy()
    if eligible.empty:
        return eligible
    state_best = (
        eligible.sort_values(
            ["scope", "city", "target_date", "decision_hour_local", edge_col, "fee_adjusted_roi_model"],
            ascending=[True, True, True, True, False, False],
        )
        .groupby(["scope", "city", "target_date", "decision_hour_local"], as_index=False)
        .head(1)
        .copy()
    )
    selected = (
        state_best.sort_values(
            ["scope", "target_date", "city", "decision_hour_local", edge_col, "fee_adjusted_roi_model"],
            ascending=[True, True, True, True, False, False],
        )
        .groupby(["scope", "city", "target_date"], as_index=False)
        .head(1)
        .copy()
    )
    selected["expression_set"] = expression_set
    selected["selection_rule"] = selection_rule
    selected["selection_edge"] = selected[edge_col]
    selected["ask_floor"] = ask_floor
    selected["policy_shares"] = FIXED_SHARES
    return selected.sort_values(["scope", "target_date", "decision_hour_local", "city"]).reset_index(drop=True)


def build_all_selected(candidates: pd.DataFrame) -> dict[tuple[str, str, str, float], pd.DataFrame]:
    selected: dict[tuple[str, str, str, float], pd.DataFrame] = {}
    scopes = sorted(candidates["scope"].dropna().unique())
    for expression_set in EXPRESSION_SETS:
        for selection_rule in SELECTION_RULES:
            for scope in scopes:
                for ask_floor in ASK_FLOORS:
                    selected[(expression_set, selection_rule, scope, ask_floor)] = select_policy(
                        candidates,
                        expression_set=expression_set,
                        selection_rule=selection_rule,
                        scope=scope,
                        ask_floor=ask_floor,
                    )
    return selected


def perf(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": math.nan,
            "cost_gross": 0.0,
            "pnl_gross": 0.0,
            "roi_gross": math.nan,
            "cost_net": 0.0,
            "pnl_net": 0.0,
            "roi_net": math.nan,
            "roi_net_ci_low": math.nan,
            "roi_net_ci_high": math.nan,
            "avg_ask": math.nan,
            "avg_p_win": math.nan,
            "avg_gross_edge": math.nan,
            "avg_fee_edge": math.nan,
            "positive_days": 0,
            "negative_days": 0,
            "worst_day_roi_net": math.nan,
            "best_day_roi_net": math.nan,
        }
    ci = _date_block_ci(rows)
    daily = rows.groupby("target_date", as_index=False).agg(cost_net=("cost_net", "sum"), pnl_net=("pnl_net", "sum"))
    daily["roi_net"] = daily["pnl_net"] / daily["cost_net"]
    return {
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "cities": int(rows["city"].nunique()),
        "wins": int(rows["win"].sum()),
        "win_rate": float(rows["win"].mean()),
        "cost_gross": float(rows["cost_gross"].sum()),
        "pnl_gross": float(rows["pnl_gross"].sum()),
        "roi_gross": float(rows["pnl_gross"].sum() / rows["cost_gross"].sum()) if rows["cost_gross"].sum() else math.nan,
        "cost_net": float(rows["cost_net"].sum()),
        "pnl_net": float(rows["pnl_net"].sum()),
        "roi_net": ci["roi"],
        "roi_net_ci_low": ci["ci_low"],
        "roi_net_ci_high": ci["ci_high"],
        "avg_ask": float(rows["ask"].mean()),
        "avg_p_win": float(rows["p_win"].mean()),
        "avg_gross_edge": float(rows["gross_edge"].mean()),
        "avg_fee_edge": float(rows["fee_adjusted_edge"].mean()),
        "positive_days": int((daily["pnl_net"] > 0).sum()),
        "negative_days": int((daily["pnl_net"] < 0).sum()),
        "worst_day_roi_net": float(daily["roi_net"].min()) if not daily.empty else math.nan,
        "best_day_roi_net": float(daily["roi_net"].max()) if not daily.empty else math.nan,
    }


def policy_summary(selected: dict[tuple[str, str, str, float], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for (expression_set, selection_rule, scope, ask_floor), data in selected.items():
        rows.append(
            {
                "expression_set": expression_set,
                "selection_rule": selection_rule,
                "scope": scope,
                "ask_floor": ask_floor,
                **perf(data),
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "ask_floor", "selection_rule", "expression_set"]).reset_index(drop=True)


def concat_selected(selected: dict[tuple[str, str, str, float], pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for key, data in selected.items():
        if not data.empty:
            frames.append(data)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def daily_summary(all_selected: pd.DataFrame) -> pd.DataFrame:
    if all_selected.empty:
        return pd.DataFrame()
    rows = []
    keys = ["expression_set", "selection_rule", "scope", "ask_floor", "target_date"]
    for key, grp in all_selected.groupby(keys, dropna=False):
        item = dict(zip(keys, key, strict=True))
        item.update(
            {
                "rows": int(len(grp)),
                "cities": int(grp["city"].nunique()),
                "wins": int(grp["win"].sum()),
                "win_rate": float(grp["win"].mean()),
                "cost_net": float(grp["cost_net"].sum()),
                "pnl_net": float(grp["pnl_net"].sum()),
                "roi_net": float(grp["pnl_net"].sum() / grp["cost_net"].sum()) if grp["cost_net"].sum() else math.nan,
                "avg_ask": float(grp["ask"].mean()),
                "avg_fee_edge": float(grp["fee_adjusted_edge"].mean()),
            }
        )
        rows.append(item)
    return pd.DataFrame(rows).sort_values(keys).reset_index(drop=True)


def expression_summary(all_selected: pd.DataFrame) -> pd.DataFrame:
    if all_selected.empty:
        return pd.DataFrame()
    rows = []
    keys = ["expression_set", "selection_rule", "scope", "ask_floor", "expression"]
    for key, grp in all_selected.groupby(keys, dropna=False):
        item = dict(zip(keys, key, strict=True))
        item.update(perf(grp))
        rows.append(item)
    return pd.DataFrame(rows).sort_values(["scope", "ask_floor", "selection_rule", "expression_set", "pnl_net"]).reset_index(drop=True)


def diff_vs_baseline(selected: dict[tuple[str, str, str, float], pd.DataFrame]) -> pd.DataFrame:
    rows = []
    scopes = sorted({key[2] for key in selected})
    for scope in scopes:
        for ask_floor in ASK_FLOORS:
            base = selected.get(("legacy_4expr", "fee_edge02", scope, ask_floor), pd.DataFrame()).copy()
            bridge = selected.get(("bridge_no_current_yes_5expr", "fee_edge02", scope, ask_floor), pd.DataFrame()).copy()
            if base.empty and bridge.empty:
                continue
            cols = ["scope", "city", "target_date"]
            b1 = base.set_index(cols)
            b2 = bridge.set_index(cols)
            idx = b1.index.union(b2.index)
            for key in idx:
                left = b1.loc[key] if key in b1.index else None
                right = b2.loc[key] if key in b2.index else None
                row = {
                    "scope": scope,
                    "ask_floor": ask_floor,
                    "city": key[1],
                    "target_date": key[2],
                    "baseline_present": left is not None,
                    "bridge_present": right is not None,
                    "baseline_expression": "" if left is None else left["expression"],
                    "bridge_expression": "" if right is None else right["expression"],
                    "baseline_hour": math.nan if left is None else float(left["decision_hour_local"]),
                    "bridge_hour": math.nan if right is None else float(right["decision_hour_local"]),
                    "baseline_pnl_net": 0.0 if left is None else float(left["pnl_net"]),
                    "bridge_pnl_net": 0.0 if right is None else float(right["pnl_net"]),
                    "pnl_net_delta": (0.0 if right is None else float(right["pnl_net"]))
                    - (0.0 if left is None else float(left["pnl_net"])),
                }
                row["changed"] = row["baseline_expression"] != row["bridge_expression"] or row["baseline_present"] != row["bridge_present"]
                rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["scope", "ask_floor", "pnl_net_delta", "target_date", "city"]).reset_index(drop=True)


def later_signal_audit(candidates: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for expression_set in ["legacy_4expr", "bridge_no_current_yes_5expr"]:
        for scope in sorted(candidates["scope"].dropna().unique()):
            eligible = candidates[
                candidates["scope"].eq(scope)
                & candidates["expression"].isin(EXPRESSION_SETS[expression_set])
                & candidates["ask"].ge(0.40)
                & candidates["ask"].le(ASK_CEILING)
                & candidates["fee_adjusted_edge"].ge(EDGE_THRESHOLD)
            ].copy()
            if eligible.empty:
                continue
            state_best = (
                eligible.sort_values(
                    ["city", "target_date", "decision_hour_local", "fee_adjusted_edge", "fee_adjusted_roi_model"],
                    ascending=[True, True, True, False, False],
                )
                .groupby(["scope", "city", "target_date", "decision_hour_local"], as_index=False)
                .head(1)
            )
            for (city, target_date), grp in state_best.groupby(["city", "target_date"], dropna=False):
                grp = grp.sort_values(["decision_hour_local", "fee_adjusted_edge"], ascending=[True, False]).copy()
                first = grp.iloc[0]
                later = grp[grp["decision_hour_local"].gt(first["decision_hour_local"])]
                opposite = later[later["side"].ne(first["side"])]
                different_expr = later[later["expression"].ne(first["expression"])]
                rows.append(
                    {
                        "expression_set": expression_set,
                        "scope": scope,
                        "city": city,
                        "target_date": target_date,
                        "first_hour": float(first["decision_hour_local"]),
                        "first_expression": first["expression"],
                        "first_side": first["side"],
                        "first_pnl_net": float(first["pnl_net"]),
                        "later_eligible_rows": int(len(later)),
                        "has_later_different_expression": bool(len(different_expr)),
                        "has_later_opposite_side": bool(len(opposite)),
                        "first_later_expression": "" if different_expr.empty else different_expr.iloc[0]["expression"],
                        "first_opposite_expression": "" if opposite.empty else opposite.iloc[0]["expression"],
                    }
                )
    return pd.DataFrame(rows).sort_values(["scope", "expression_set", "target_date", "city"]).reset_index(drop=True)


def _table(df: pd.DataFrame, cols: list[str], *, max_rows: int = 40) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in df.head(max_rows).to_dict("records"):
        vals = []
        for col in cols:
            val = row.get(col)
            if isinstance(val, float):
                if not math.isfinite(val):
                    vals.append("n/a")
                elif col.startswith("roi") or col in {"win_rate", "avg_gross_edge", "avg_fee_edge"}:
                    vals.append(_fmt_pct(val))
                elif col in {"cost_net", "pnl_net", "avg_ask", "avg_p_win"}:
                    vals.append(_fmt_num(val, 3 if col.startswith("avg") else 2))
                else:
                    vals.append(_fmt_num(val, 2))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _write_report(
    *,
    candidates: pd.DataFrame,
    summary: pd.DataFrame,
    daily: pd.DataFrame,
    expr_summary: pd.DataFrame,
    diff: pd.DataFrame,
    audit: pd.DataFrame,
    report: dict[str, Any],
) -> None:
    focus = summary[
        summary.apply(
            lambda r: (r["expression_set"], r["selection_rule"], float(r["ask_floor"])) in FOCUS_ROWS,
            axis=1,
        )
        & summary["scope"].isin(["dev_cv", "verified_forward"])
    ].copy()
    focus = focus.sort_values(["scope", "ask_floor", "selection_rule", "expression_set"])

    primary = summary[
        summary["expression_set"].eq("bridge_no_current_yes_5expr")
        & summary["selection_rule"].eq("fee_edge02")
        & summary["ask_floor"].eq(0.40)
        & summary["scope"].eq("verified_forward")
    ]
    legacy = summary[
        summary["expression_set"].eq("legacy_4expr")
        & summary["selection_rule"].eq("fee_edge02")
        & summary["ask_floor"].eq(0.40)
        & summary["scope"].eq("verified_forward")
    ]
    primary_roi = "n/a" if primary.empty else _fmt_pct(primary.iloc[0]["roi_net"])
    legacy_roi = "n/a" if legacy.empty else _fmt_pct(legacy.iloc[0]["roi_net"])

    daily_focus = daily[
        daily["scope"].eq("verified_forward")
        & daily["ask_floor"].eq(0.40)
        & daily["selection_rule"].eq("fee_edge02")
        & daily["expression_set"].isin(["legacy_4expr", "bridge_no_current_yes_5expr"])
    ].copy()

    expr_focus = expr_summary[
        expr_summary["scope"].eq("verified_forward")
        & expr_summary["ask_floor"].eq(0.40)
        & expr_summary["selection_rule"].eq("fee_edge02")
        & expr_summary["expression_set"].isin(["legacy_4expr", "bridge_6expr", "bridge_no_current_yes_5expr"])
    ].copy()

    changed = diff[diff["changed"] & diff["scope"].eq("verified_forward") & diff["ask_floor"].eq(0.40)].copy()
    changed_bad = changed.sort_values("pnl_net_delta").head(12)
    changed_good = changed.sort_values("pnl_net_delta", ascending=False).head(12)

    audit_focus = audit[
        audit["scope"].eq("verified_forward") & audit["expression_set"].isin(["legacy_4expr", "bridge_no_current_yes_5expr"])
    ].copy()
    audit_summary = (
        audit_focus.groupby(["expression_set", "scope"], as_index=False)
        .agg(
            city_days=("city", "count"),
            later_eligible_days=("later_eligible_rows", lambda s: int((s > 0).sum())),
            later_different_days=("has_later_different_expression", "sum"),
            later_opposite_days=("has_later_opposite_side", "sum"),
        )
        if not audit_focus.empty
        else pd.DataFrame()
    )

    lines = [
        "# Tmax Exact-Book Bridge v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> source P5: `{P5_OPPS.relative_to(ROOT)}`",
        f"> source atlas: `{ATLAS.relative_to(ROOT)}`",
        "> Scope: research/shadow only. No live runner or order policy changed.",
        "",
        "## 结论",
        "",
        "- 这版完成的是 `四桶概率 -> exact sibling 表达` 的最小实验，不是全 ladder hazard 模型。",
        "- d1/d2 YES 可以从 `1 - NO bid` 构造出可成交 ask proxy；`ask>=0.40 + fee_edge02` verified 上 bridge_no_current_yes_5expr 为 "
        f"`{primary_roi}`，legacy_4expr 为 `{legacy_roi}`。",
        "- 这个改善方向是合理的，但 dev/verified 都没有足够窄的 CI，且 d1_yes 单腿仍弱；新的 live 表达不应替换现有策略。合理动作是把 `bridge_no_current_yes_5expr` 作为 shadow target-book bridge 记录，继续补全真正 full-ladder/hazard 概率。",
        "- `current_yes` 仍然不该回 live：它在 Lucknow 暴露的是重锚和 basis 风险；本实验把 `no_current_yes` 单独列出来，而不是靠单日事故把 d1_no 删掉。",
        "",
        "## 口径",
        "",
        f"- 模型：固定 `{METHOD}` 的 current/d1/d2/tail 概率，不重训、不新增天气 gate。",
        "- 表达集：legacy_4expr=`current_yes/current_no/d1_no/d2_no`；bridge_6expr=legacy + `d1_yes/d2_yes`；bridge_no_current_yes_5expr=去掉 `current_yes` 后的 bridge。",
        "- d1/d2 YES ask：`1 - d1_no_bid` / `1 - d2_no_bid`。这是 executable ask proxy；若要 live，需要实时 fresh CLOB 重新拉 sibling book。",
        f"- 执行成本：taker fee `0.05 * ask * (1-ask)`；`fee_edge02` 用 `p_win - ask - fee >= {EDGE_THRESHOLD}` 选表达。",
        "- 回测单位：每 city-day 第一条 eligible state；固定 5 shares 只影响美元数，不影响 ROI。",
        "",
        "## Policy Summary",
        "",
        *_table(
            focus,
            [
                "scope",
                "ask_floor",
                "selection_rule",
                "expression_set",
                "rows",
                "dates",
                "cities",
                "win_rate",
                "cost_net",
                "pnl_net",
                "roi_net",
                "roi_net_ci_low",
                "roi_net_ci_high",
                "avg_ask",
                "avg_fee_edge",
            ],
            max_rows=40,
        ),
        "",
        "## Verified Daily, ask>=0.40 fee_edge02",
        "",
        *_table(
            daily_focus,
            [
                "expression_set",
                "target_date",
                "rows",
                "cities",
                "wins",
                "win_rate",
                "cost_net",
                "pnl_net",
                "roi_net",
                "avg_ask",
                "avg_fee_edge",
            ],
            max_rows=80,
        ),
        "",
        "## Expression Contribution",
        "",
        *_table(
            expr_focus,
            [
                "expression_set",
                "expression",
                "rows",
                "dates",
                "win_rate",
                "cost_net",
                "pnl_net",
                "roi_net",
                "avg_ask",
                "avg_fee_edge",
            ],
            max_rows=80,
        ),
        "",
        "## Bridge vs Legacy Changed Rows",
        "",
        "同一 city-day 上，bridge_no_current_yes_5expr 相对 legacy_4expr 的变化。负数表示 bridge 更差，正数表示 bridge 更好。",
        "",
        "### Worst Deltas",
        "",
        *_table(
            changed_bad,
            [
                "ask_floor",
                "city",
                "target_date",
                "baseline_expression",
                "bridge_expression",
                "baseline_pnl_net",
                "bridge_pnl_net",
                "pnl_net_delta",
            ],
            max_rows=12,
        ),
        "",
        "### Best Deltas",
        "",
        *_table(
            changed_good,
            [
                "ask_floor",
                "city",
                "target_date",
                "baseline_expression",
                "bridge_expression",
                "baseline_pnl_net",
                "bridge_pnl_net",
                "pnl_net_delta",
            ],
            max_rows=12,
        ),
        "",
        "## Later Signal Audit",
        "",
        "这个表只回答 first-lock 后当天还有多少后续候选，不等于允许翻仓；真实 target-book reconciliation 还缺 close bid、market id 和持仓血缘。",
        "",
        *_table(
            audit_summary,
            ["expression_set", "scope", "city_days", "later_eligible_days", "later_different_days", "later_opposite_days"],
            max_rows=20,
        ),
        "",
        "## Data Boundary",
        "",
        f"- `p5_opportunities`: `{report['inventory'].get('p5_opportunities')}`",
        f"- `atlas`: `{report['inventory'].get('atlas')}`",
        f"- `settlement_outcomes`: `{report['inventory'].get('settlement_outcomes')}`",
        "- P5/P6 可评分分母当前到 2026-07-03；atlas 虽到 2026-07-04，但 tmax 四桶 label 对 7/04 仍有缺口，不能把 7/04 硬算进 verified。",
        "",
        "## Verdict",
        "",
        "conclusion=`shadow_bridge_complete_not_live`; target-book bridge v1 完成离线实验，但没有足够证据替换 legacy 四表达。下一步是 full ladder/hazard 概率层和实时 sibling book 落盘。",
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'expression_candidates.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'selected_trades.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'policy_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'daily.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'expression_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'bridge_vs_legacy_diff.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'later_signal_audit.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = build_expression_candidates()
    selected = build_all_selected(candidates)
    all_selected = concat_selected(selected)
    summary = policy_summary(selected)
    daily = daily_summary(all_selected)
    expr_summary = expression_summary(all_selected)
    diff = diff_vs_baseline(selected)
    audit = later_signal_audit(candidates)
    inventory = _inventory()

    candidates.to_csv(OUT_DIR / "expression_candidates.csv", index=False)
    all_selected.to_csv(OUT_DIR / "selected_trades.csv", index=False)
    summary.to_csv(OUT_DIR / "policy_summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily.csv", index=False)
    expr_summary.to_csv(OUT_DIR / "expression_summary.csv", index=False)
    diff.to_csv(OUT_DIR / "bridge_vs_legacy_diff.csv", index=False)
    audit.to_csv(OUT_DIR / "later_signal_audit.csv", index=False)

    focus_summary = summary[
        summary.apply(
            lambda r: (r["expression_set"], r["selection_rule"], float(r["ask_floor"])) in FOCUS_ROWS,
            axis=1,
        )
    ]
    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "method": METHOD,
        "edge_threshold": EDGE_THRESHOLD,
        "ask_floors": ASK_FLOORS,
        "ask_ceiling": ASK_CEILING,
        "fixed_shares": FIXED_SHARES,
        "taker_fee_rate": TAKER_FEE_RATE,
        "inventory": inventory,
        "candidate_rows": int(len(candidates)),
        "selected_rows": int(len(all_selected)),
        "focus_summary": focus_summary.to_dict("records"),
        "verdict": "shadow_bridge_complete_not_live",
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True), encoding="utf-8")
    _write_report(
        candidates=candidates,
        summary=summary,
        daily=daily,
        expr_summary=expr_summary,
        diff=diff,
        audit=audit,
        report=report,
    )
    print(
        json.dumps(
            {
                "report_path": str(REPORT_PATH.relative_to(ROOT)),
                "candidate_rows": int(len(candidates)),
                "selected_rows": int(len(all_selected)),
                "verdict": report["verdict"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
