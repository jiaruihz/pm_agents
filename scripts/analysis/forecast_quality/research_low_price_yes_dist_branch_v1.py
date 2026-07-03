"""HeadA low-price YES bracket-distance branch audit v1.

This script answers one narrow question: whether the forecast-tail low-price
YES selector should remove tickets whose bracket is not above the decision-time
forecast max.

It uses the frozen HeadA selected denominator and joins canonical
fact_signal_candidates only for decision-time forecast/book fields.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "docs/analysis/2026-07/generated/low_price_yes_integrated_tail_v2/enriched_rows.csv"
DB_PATH = ROOT / "runtime/weather.db"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_dist_branch_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-04-low-price-yes-dist-branch-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-04-low-price-yes-dist-branch-v1.json"

WEATHER_TAKER_FEE_RATE = 0.05
SHARES = 8.0
N_BOOT = 5000
RNG_SEED = 20260704


def fmt_pct(value: Any, *, signed: bool = True) -> str:
    try:
        x = float(value) * 100.0
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(x):
        return ""
    return f"{x:+.1f}%" if signed else f"{x:.1f}%"


def fmt_money(value: Any) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def first_number(text: Any) -> float:
    m = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", str(text or ""))
    return float(m.group(1)) if m else math.nan


def weather_taker_fee(shares: float, price: float) -> float:
    if shares <= 0.0 or price <= 0.0 or price >= 1.0:
        return 0.0
    return shares * WEATHER_TAKER_FEE_RATE * price * (1.0 - price)


def date_block_ci(daily: pd.DataFrame, rng: np.random.Generator) -> tuple[float, float]:
    if daily.empty:
        return math.nan, math.nan
    pnl = daily["pnl"].to_numpy(dtype=float)
    cost = daily["cost"].to_numpy(dtype=float)
    n = len(daily)
    vals: list[float] = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, n, size=n)
        c = float(cost[idx].sum())
        vals.append(float(pnl[idx].sum() / c) if c > 0 else math.nan)
    arr = np.array([v for v in vals if math.isfinite(v)], dtype=float)
    if arr.size == 0:
        return math.nan, math.nan
    return float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def paired_delta_ci(
    a_daily: pd.DataFrame,
    b_daily: pd.DataFrame,
    all_dates: list[str],
    rng: np.random.Generator,
) -> tuple[float, float]:
    a = a_daily.set_index("target_date").reindex(all_dates, fill_value=0.0)
    b = b_daily.set_index("target_date").reindex(all_dates, fill_value=0.0)
    a_pnl = a["pnl"].to_numpy(dtype=float)
    a_cost = a["cost"].to_numpy(dtype=float)
    b_pnl = b["pnl"].to_numpy(dtype=float)
    b_cost = b["cost"].to_numpy(dtype=float)
    n = len(all_dates)
    vals: list[float] = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, n, size=n)
        ac = float(a_cost[idx].sum())
        bc = float(b_cost[idx].sum())
        if ac <= 0 or bc <= 0:
            continue
        vals.append(float(a_pnl[idx].sum() / ac - b_pnl[idx].sum() / bc))
    arr = np.array([v for v in vals if math.isfinite(v)], dtype=float)
    if arr.size == 0:
        return math.nan, math.nan
    return float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def load_fact_fields() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        return pd.read_sql_query(
            """
            SELECT
              candidate_id,
              unit,
              forecast_max_f AS fact_forecast_max_f,
              forecast_source AS fact_forecast_source,
              yes_spread,
              yes_depth_ask_5c,
              fact_built_at_utc
            FROM fact_signal_candidates
            """,
            conn,
        )
    finally:
        conn.close()


def add_dist_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["unit"] = out["unit"].fillna("").astype(str)
    out["bracket_low_native_recalc"] = out["bracket"].map(first_number)
    is_f = out["unit"].str.upper().str.startswith("F")
    out["bracket_width_f"] = np.where(is_f, 2.0, 1.8)
    out["bracket_low_f_recalc"] = np.where(
        is_f,
        out["bracket_low_native_recalc"],
        out["bracket_low_native_recalc"] * 9.0 / 5.0 + 32.0,
    )
    out["forecast_max_f_used"] = pd.to_numeric(out["fact_forecast_max_f"], errors="coerce")
    out["dist_br"] = (
        out["bracket_low_f_recalc"] - out["forecast_max_f_used"]
    ) / out["bracket_width_f"]
    eps = 1e-9
    out["dist_sign"] = np.select(
        [
            out["dist_br"].isna(),
            out["dist_br"] < -eps,
            out["dist_br"].abs() <= eps,
            out["dist_br"] > eps,
        ],
        ["missing", "lt0", "eq0", "gt0"],
        default="missing",
    )
    dist = out["dist_br"]
    out["dist_band"] = np.select(
        [
            dist.isna(),
            dist < -1.0,
            (dist >= -1.0) & (dist < -0.5),
            (dist >= -0.5) & (dist < -eps),
            dist.abs() <= eps,
            (dist > eps) & (dist <= 0.5),
            (dist > 0.5) & (dist <= 1.0),
            dist > 1.0,
        ],
        ["missing", "<-1", "-1..-0.5", "-0.5..<0", "=0", "0..0.5", "0.5..1", ">1"],
        default="missing",
    )
    return out


def add_pnl(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["entry"] = pd.to_numeric(out["ask"], errors="coerce")
    out["payoff"] = pd.to_numeric(out["payoff"], errors="coerce")
    out["fee"] = out["entry"].map(lambda p: weather_taker_fee(SHARES, float(p)) if math.isfinite(float(p)) else math.nan)
    out["cost"] = SHARES * out["entry"] + out["fee"]
    out["pnl"] = SHARES * out["payoff"] - out["cost"]
    out["roi_row"] = out["pnl"] / out["cost"]
    out["win"] = out["payoff"] >= 0.5
    return out


def daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["target_date", "rows", "wins", "cost", "pnl", "roi"])
    d = frame.groupby("target_date", as_index=False).agg(
        rows=("candidate_id", "count"),
        wins=("win", "sum"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
    )
    d["roi"] = d["pnl"] / d["cost"]
    return d


def summarize_slice(name: str, frame: pd.DataFrame, rng: np.random.Generator) -> dict[str, Any]:
    d = daily(frame)
    cost = float(frame["cost"].sum()) if not frame.empty else 0.0
    pnl = float(frame["pnl"].sum()) if not frame.empty else 0.0
    ci_low, ci_high = date_block_ci(d, rng)
    return {
        "slice": name,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()) if not frame.empty else 0,
        "cities": int(frame["city"].nunique()) if not frame.empty else 0,
        "wins": int(frame["win"].sum()) if not frame.empty else 0,
        "win_rate": float(frame["win"].mean()) if not frame.empty else math.nan,
        "avg_ask": float(frame["entry"].mean()) if not frame.empty else math.nan,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost > 0 else math.nan,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "losing_days": int((d["pnl"] < 0).sum()) if not d.empty else 0,
        "le_minus50pct_days": int((d["roi"] <= -0.5).sum()) if not d.empty else 0,
        "max_daily_loss_usd": float(d["pnl"].min()) if not d.empty else math.nan,
        "max_daily_loss_roi": float(d["roi"].min()) if not d.empty else math.nan,
    }


def make_summaries(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    windows = {
        "train_le_2026_06_20": df["target_date"].astype(str) <= "2026-06-20",
        "recent_ge_2026_06_21": df["target_date"].astype(str) >= "2026-06-21",
        "full": pd.Series(True, index=df.index),
    }
    policies = {
        "all_current_selector": lambda x: x,
        "remove_dist_lt0_keep_eq0": lambda x: x[x["dist_sign"].isin(["eq0", "gt0"])],
        "remove_dist_le0_hot_only": lambda x: x[x["dist_sign"] == "gt0"],
        "dist_lt0_only": lambda x: x[x["dist_sign"] == "lt0"],
        "dist_eq0_only": lambda x: x[x["dist_sign"] == "eq0"],
        "dist_gt0_only": lambda x: x[x["dist_sign"] == "gt0"],
        "dist_missing_only": lambda x: x[x["dist_sign"] == "missing"],
    }
    rng = np.random.default_rng(RNG_SEED)
    recs: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    for window_name, mask in windows.items():
        base = df[mask].copy()
        all_dates = sorted(base["target_date"].astype(str).unique().tolist())
        all_daily = daily(base)
        all_summary = summarize_slice("all_current_selector", base, rng)
        for policy_name, selector in policies.items():
            sub = selector(base).copy()
            s = summarize_slice(policy_name, sub, rng)
            s["window"] = window_name
            recs.append(s)
            if policy_name in {"remove_dist_lt0_keep_eq0", "remove_dist_le0_hot_only"}:
                sub_daily = daily(sub)
                lo, hi = paired_delta_ci(sub_daily, all_daily, all_dates, rng)
                s_roi = s["roi"]
                deltas.append(
                    {
                        "window": window_name,
                        "policy": policy_name,
                        "roi_delta_vs_all": (
                            float(s_roi - all_summary["roi"])
                            if math.isfinite(float(s_roi)) and math.isfinite(float(all_summary["roi"]))
                            else math.nan
                        ),
                        "roi_delta_ci_low": lo,
                        "roi_delta_ci_high": hi,
                        "rows_removed": int(len(base) - len(sub)),
                        "winner_rows_removed": int((base.drop(sub.index, errors="ignore")["win"]).sum()),
                    }
                )
    return pd.DataFrame(recs), pd.DataFrame(deltas)


def top_contrib(df: pd.DataFrame, mask: pd.Series, group_cols: list[str]) -> pd.DataFrame:
    sub = df[mask].copy()
    if sub.empty:
        return pd.DataFrame()
    out = sub.groupby(group_cols, as_index=False).agg(
        rows=("candidate_id", "count"),
        wins=("win", "sum"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
        avg_dist=("dist_br", "mean"),
        avg_ask=("entry", "mean"),
    )
    out["win_rate"] = out["wins"] / out["rows"]
    out["roi"] = out["pnl"] / out["cost"]
    return out.sort_values(["pnl", "rows"], ascending=[True, False])


def render_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in frame.iterrows():
        vals: list[str] = []
        for col in columns:
            v = row.get(col)
            if col in {"roi", "win_rate", "avg_ask", "roi_ci_low", "roi_ci_high", "roi_delta_vs_all", "roi_delta_ci_low", "roi_delta_ci_high", "max_daily_loss_roi"}:
                vals.append(fmt_pct(v, signed=col != "avg_ask"))
            elif col in {"pnl", "cost", "max_daily_loss_usd"}:
                vals.append(fmt_money(v))
            elif isinstance(v, float):
                vals.append(f"{v:.3f}" if math.isfinite(v) else "")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def make_markdown(df: pd.DataFrame, summary: pd.DataFrame, deltas: pd.DataFrame) -> str:
    train = summary[summary["window"] == "train_le_2026_06_20"].copy()
    full = summary[summary["window"] == "full"].copy()
    recent = summary[summary["window"] == "recent_ge_2026_06_21"].copy()
    dist_counts = df.groupby("dist_sign", as_index=False).agg(
        rows=("candidate_id", "count"),
        wins=("win", "sum"),
        avg_dist=("dist_br", "mean"),
        avg_ask=("entry", "mean"),
    )
    dist_counts["win_rate"] = dist_counts["wins"] / dist_counts["rows"]

    def rows_for(frame: pd.DataFrame) -> pd.DataFrame:
        wanted = [
            "all_current_selector",
            "remove_dist_lt0_keep_eq0",
            "remove_dist_le0_hot_only",
            "dist_lt0_only",
            "dist_eq0_only",
            "dist_gt0_only",
        ]
        return frame[frame["slice"].isin(wanted)].set_index("slice").loc[wanted].reset_index()

    dcols = ["window", "policy", "rows_removed", "winner_rows_removed", "roi_delta_vs_all", "roi_delta_ci_low", "roi_delta_ci_high"]
    train_delta = deltas[deltas["window"] == "train_le_2026_06_20"][dcols]
    cold_city = top_contrib(df, df["dist_sign"].eq("lt0"), ["city"]).head(8)
    cold_source = top_contrib(df, df["dist_sign"].eq("lt0"), ["forecast_source"]).head(8)
    eqlines = df[df["dist_sign"].eq("eq0")].sort_values(["target_date", "city"])[
        ["target_date", "city", "bracket", "forecast_source", "entry", "payoff", "dist_br", "pnl"]
    ]

    return f"""# HeadA Low-Price YES `dist<0` Branch Audit v1

Generated: 2026-07-04

Scope: only HeadA `forecast_tail_low_price_yes`. This is not a TP, METAR, or tmax-distribution study.

## Verdict

`dist<0` is a real drag and is semantically outside the HeadA thesis. The implemented selector change is **remove `dist<0` only**, and keep logging `dist=0` separately. Removing `dist<=0` is cleaner as a hot-tail definition, but it is one notch more aggressive because exact-boundary rows are tiny and noisy.

Contract verdict for live action remains `shadow_candidate`: this was deployed as a tiny-live thesis-consistency removal, not as a size-up or confirmed alpha. The local `run_stack` refresh failed at a separate `fact_signal_candidates.candidate_id` uniqueness issue, so I am not calling this `confirmed` in this report.

Implementation note: `scripts/ops/low_price_yes_lottery_tiny_live.py` now blocks rows with `forecast_to_bracket_low_native < 0` as `dist_lt0_cold_or_inside_forecast_tail_v1`. Blocked rows still append to `shadow_decisions.jsonl` and `blocked_candidates.jsonl` with bracket-distance fields for forward review.

Plain English: `dist<0` means the ticket's lower bound is below the forecast max. Buying YES there is not "weather gets hotter than forecast"; it is "forecast was too high or the market underpriced a cooler/inside bracket." That may occasionally win, but it is a different bet and it has hurt this sleeve.

## Data Snapshot

- Input denominator: `{INPUT.relative_to(ROOT)}`.
- Rows: {len(df)}; dates {df['target_date'].min()}..{df['target_date'].max()}; cities {df['city'].nunique()}.
- Canonical join: `runtime/weather.db:fact_signal_candidates` for `unit`, `forecast_max_f`, `yes_spread`, `yes_depth_ask_5c`.
- DB fact build available in table: {df['fact_built_at_utc'].dropna().max() if 'fact_built_at_utc' in df.columns else ''}.
- Fee model: official Weather taker fee `shares * 0.05 * price * (1-price)`, fixed {SHARES:.0f} shares, hold to settlement.
- Refresh caveat: `scripts/ops/sync_weather_remote.sh` succeeded, but `run_stack.sh` failed on a separate `fact_signal_candidates.candidate_id` uniqueness error after printing a 45,053-row candidate summary. This report uses the last available canonical table built at the timestamp above.

## `dist` Definition

```text
dist = (bracket_low_f - decision_forecast_max_f) / bracket_width_f
F markets: width = 2.0F
C markets: width = 1.8F
```

- `dist < 0`: bracket starts below forecast max. This is not a hot-tail ticket.
- `dist = 0`: bracket starts exactly at forecast max. Boundary case; not clearly hot-tail, but not the same as below-forecast.
- `dist > 0`: bracket starts above forecast max. This is the actual hotter-than-forecast lottery thesis.

## Distribution

{render_table(dist_counts, ["dist_sign", "rows", "wins", "win_rate", "avg_ask", "avg_dist"])}

## Train Window: <= 2026-06-20

{render_table(rows_for(train), ["slice", "rows", "dates", "cities", "win_rate", "avg_ask", "roi", "roi_ci_low", "roi_ci_high", "losing_days", "le_minus50pct_days", "max_daily_loss_roi"])}

Train paired delta versus current selector:

{render_table(train_delta, dcols)}

## Recent Window: >= 2026-06-21

{render_table(rows_for(recent), ["slice", "rows", "dates", "cities", "win_rate", "avg_ask", "roi", "roi_ci_low", "roi_ci_high", "losing_days", "le_minus50pct_days", "max_daily_loss_roi"])}

## Full Window

{render_table(rows_for(full), ["slice", "rows", "dates", "cities", "win_rate", "avg_ask", "roi", "roi_ci_low", "roi_ci_high", "losing_days", "le_minus50pct_days", "max_daily_loss_roi"])}

## Removed `dist<0` Branch: Contribution

Worst city contributions inside `dist<0`:

{render_table(cold_city, ["city", "rows", "wins", "win_rate", "avg_ask", "avg_dist", "pnl", "roi"])}

By forecast source inside `dist<0`:

{render_table(cold_source, ["forecast_source", "rows", "wins", "win_rate", "avg_ask", "avg_dist", "pnl", "roi"])}

Exact-boundary `dist=0` rows:

{render_table(eqlines, ["target_date", "city", "bracket", "forecast_source", "entry", "payoff", "dist_br", "pnl"])}

## Decision

The live selector patch is:

```text
if forecast_max_f is available and dist < 0:
    block as cold_or_inside_forecast_tail_v1
else:
    keep existing selector behavior
```

Reason: this is a thesis-consistency removal, not a tuned ROI threshold. It removes below-forecast tickets while preserving exact-boundary rows until forward telemetry says whether `dist=0` should be blocked too.

Do not size up from this. The same audit still shows the larger uncertainty is book-state/stale-quote feasibility, not just `dist`.
"""


def main() -> None:
    base = pd.read_csv(INPUT)
    facts = load_fact_fields()
    df = base.merge(facts, on="candidate_id", how="left", validate="one_to_one")
    df = add_dist_fields(df)
    df = add_pnl(df)
    summary, deltas = make_summaries(df)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "rows_with_dist.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    deltas.to_csv(OUT_DIR / "paired_deltas.csv", index=False)
    payload = {
        "input": str(INPUT.relative_to(ROOT)),
        "db": str(DB_PATH.relative_to(ROOT)),
        "rows": int(len(df)),
        "date_min": str(df["target_date"].min()),
        "date_max": str(df["target_date"].max()),
        "fact_built_at_utc_max": str(df["fact_built_at_utc"].dropna().max()),
        "fee_model": {
            "shares": SHARES,
            "weather_taker_fee_rate": WEATHER_TAKER_FEE_RATE,
            "formula": "shares * fee_rate * price * (1 - price)",
        },
        "summary": json.loads(summary.to_json(orient="records")),
        "paired_deltas": json.loads(deltas.to_json(orient="records")),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(make_markdown(df, summary, deltas), encoding="utf-8")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"wrote {OUT_JSON.relative_to(ROOT)}")
    print(f"wrote {(OUT_DIR / 'summary.csv').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
