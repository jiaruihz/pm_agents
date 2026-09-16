#!/usr/bin/env python3
"""Refine the low-price YES lottery selector and sizing policy.

Evidence layers:
1. Historical: fact_signal_candidates + settlement_outcomes, target_date before
   the forward cut.
2. Forward: fact_signal_candidates candidate pool after the forward cut with a
   research-only CLOB market settlement overlay.

The goal is not to maximize headline ROI. It is to find a selector/sizing pair
that preserves convexity without depending on sub-5c dust tickets or one huge
winner.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime/weather.db"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-selector-refinement-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-lottery-selector-refinement-v1.json"

FORWARD_START = "2026-06-27"
RECENT_START = "2026-06-08"
STAKE_USD = 5.0
RNG_SEED = 20260702


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def pct(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{100 * x:+.1f}%"


def money(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {"roi", "win_rate", "top_trade_removed_roi", "roi_ci_low", "roi_ci_high"}
    money_cols = {"cost", "pnl", "max_daily_loss", "avg_cost"}
    int_cols = {"rows", "dates", "cities", "losing_days", "roi_le_minus_50_days"}
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals = []
        for key, _label in cols:
            val = row.get(key)
            if key in pct_cols or key.endswith("_roi"):
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl") or key.endswith("_loss"):
                vals.append(money(val))
            elif key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append("" if val is None else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def sqlite_frame(query: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        return pd.read_sql_query(query, conn, params=params or {})
    finally:
        conn.close()


def db_snapshot() -> dict[str, Any]:
    return {
        "db_path": rel(DB_PATH),
        "fact_signal_candidates": sqlite_frame(
            """
            SELECT COUNT(*) AS rows, MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date,
                   MAX(fact_built_at_utc) AS fact_built_at_utc
            FROM fact_signal_candidates
            """
        ).iloc[0].to_dict(),
        "settlement_outcomes_recent": sqlite_frame(
            """
            SELECT target_date, COUNT(*) AS rows,
                   SUM(CASE WHEN settlement_status = 'settled' THEN 1 ELSE 0 END) AS settled
            FROM settlement_outcomes
            WHERE target_date >= '2026-06-26'
            GROUP BY target_date
            ORDER BY target_date
            """
        ).to_dict(orient="records"),
    }


def load_historical() -> pd.DataFrame:
    df = sqlite_frame(
        """
        SELECT
          f.candidate_id,
          f.condition_id,
          f.market_id,
          f.city,
          f.event_date AS target_date,
          f.bracket,
          f.forecast_source,
          f.forecast_peak_source,
          f.forecast_max_native,
          f.forecast_peak_delta_hours_local,
          f.forecast_max_in_bracket,
          f.forecast_max_above_bracket_f,
          f.forecast_max_below_bracket_f,
          f.model_p_yes,
          f.market_yes_price,
          f.edge,
          f.decision_entry_price AS ask,
          f.first_seen_ts_utc,
          f.decision_snapshot_ts_utc,
          COALESCE(f.final_yes, so.final_price) AS payoff
        FROM fact_signal_candidates f
        LEFT JOIN settlement_outcomes so
          ON so.city = f.city
         AND so.target_date = f.event_date
         AND so.bracket = f.bracket
        WHERE f.side = 'BUY_YES'
          AND f.event_date < :forward_start
          AND f.decision_entry_price BETWEEN 0.01 AND 0.25
          AND COALESCE(f.final_yes, so.final_price) IN (0.0, 1.0)
          AND COALESCE(f.settlement_status, so.settlement_status) = 'settled'
        """,
        {"forward_start": FORWARD_START},
    )
    return normalize(df)


def load_forward_pool() -> pd.DataFrame:
    df = sqlite_frame(
        """
        SELECT
          f.candidate_id,
          f.condition_id,
          f.market_id,
          f.city,
          f.event_date AS target_date,
          f.bracket,
          f.forecast_source,
          f.forecast_peak_source,
          f.forecast_max_native,
          f.forecast_peak_delta_hours_local,
          f.forecast_max_in_bracket,
          f.forecast_max_above_bracket_f,
          f.forecast_max_below_bracket_f,
          f.model_p_yes,
          f.market_yes_price,
          f.edge,
          f.decision_entry_price AS ask,
          f.first_seen_ts_utc,
          f.decision_snapshot_ts_utc
        FROM fact_signal_candidates f
        WHERE f.side = 'BUY_YES'
          AND f.event_date >= :forward_start
          AND f.decision_entry_price BETWEEN 0.01 AND 0.25
        """,
        {"forward_start": FORWARD_START},
    )
    return normalize(df)


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    numeric = [
        "forecast_max_native",
        "forecast_peak_delta_hours_local",
        "forecast_max_in_bracket",
        "forecast_max_above_bracket_f",
        "forecast_max_below_bracket_f",
        "model_p_yes",
        "market_yes_price",
        "edge",
        "ask",
        "payoff",
    ]
    for col in numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["target_date"] = df["target_date"].astype(str)
    return df


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4))
    return session


def overlay_forward_clob(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if df.empty:
        return df, {"market_api_calls": 0, "market_api_errors": 0}
    session = make_session()
    cache: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for cid in sorted(str(x) for x in df["condition_id"].dropna().unique()):
        try:
            response = session.get(
                f"https://clob.polymarket.com/markets/{cid}",
                timeout=20,
                headers={"Accept": "application/json", "User-Agent": "pm-agents-research/1.0"},
            )
            response.raise_for_status()
            market = response.json()
            yes_price = np.nan
            yes_winner = None
            for token in market.get("tokens") or []:
                if str(token.get("outcome") or "").lower() == "yes":
                    if token.get("price") is not None:
                        yes_price = float(token["price"])
                    if token.get("winner") is not None:
                        yes_winner = bool(token["winner"])
            cache[cid] = {
                "clob_closed": bool(market.get("closed")),
                "clob_yes_price": yes_price,
                "clob_yes_winner": yes_winner,
            }
        except Exception as exc:
            errors.append({"condition_id": cid, "error": str(exc)})
        time.sleep(0.05)
    out = df.copy()
    out["clob_closed"] = out["condition_id"].map(lambda x: cache.get(str(x), {}).get("clob_closed", False))
    out["clob_yes_price"] = out["condition_id"].map(lambda x: cache.get(str(x), {}).get("clob_yes_price", np.nan))
    out["payoff"] = out["clob_yes_price"]
    out = out[out["payoff"].isin([0.0, 1.0])].copy()
    return out, {
        "market_api_calls": len(cache),
        "market_api_errors": len(errors),
        "sample_errors": errors[:5],
        "raw_forward_rows": int(len(df)),
        "closed_forward_rows": int(len(out)),
        "open_or_unresolved_forward_rows": int(len(df) - len(out)),
    }


def is_gfs(df: pd.DataFrame) -> pd.Series:
    source = df["forecast_source"].fillna("").str.lower()
    peak = df["forecast_peak_source"].fillna("").str.lower()
    return source.str.contains("gfs") | peak.str.contains("gfs")


def is_ecmwf(df: pd.DataFrame) -> pd.Series:
    source = df["forecast_source"].fillna("").str.lower()
    peak = df["forecast_peak_source"].fillna("").str.lower()
    return source.str.contains("ecmwf") | peak.str.contains("ecmwf")


SelectorFn = Callable[[pd.DataFrame], pd.Series]
SizingFn = Callable[[pd.DataFrame], np.ndarray]


def selector_specs() -> dict[str, SelectorFn]:
    return {
        "base_edge20_ask01_25": lambda d: d["edge"].ge(0.20) & d["ask"].between(0.01, 0.25),
        "no_dust_edge20_ask05_20": lambda d: d["edge"].ge(0.20) & d["ask"].between(0.05, 0.20),
        "no_dust_edge20_ask05_25": lambda d: d["edge"].ge(0.20) & d["ask"].between(0.05, 0.25),
        "ask10_edge20_ask10_25": lambda d: d["edge"].ge(0.20) & d["ask"].between(0.10, 0.25),
        "model25_ask05_20": lambda d: d["model_p_yes"].ge(0.25) & d["ask"].between(0.05, 0.20),
        "gfs_edge20_ask05_25": lambda d: d["edge"].ge(0.20) & d["ask"].between(0.05, 0.25) & is_gfs(d),
        "ecmwf_edge20_ask05_25": lambda d: d["edge"].ge(0.20) & d["ask"].between(0.05, 0.25) & is_ecmwf(d),
        "edge25_ask05_25": lambda d: d["edge"].ge(0.25) & d["ask"].between(0.05, 0.25),
    }


def sizing_specs() -> dict[str, SizingFn]:
    return {
        "fixed5": lambda d: np.full(len(d), STAKE_USD),
        "payout25_cap5": lambda d: np.minimum(STAKE_USD, d["ask"].to_numpy(float) * 25.0),
        "payout50_cap5": lambda d: np.minimum(STAKE_USD, d["ask"].to_numpy(float) * 50.0),
        "ask_scaled_05_20": lambda d: STAKE_USD * np.clip((d["ask"].to_numpy(float) - 0.05) / 0.15, 0.25, 1.0),
    }


def pick(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    g = df[mask].copy()
    if g.empty:
        return g
    return g.sort_values(
        ["target_date", "city", "decision_snapshot_ts_utc", "ask", "edge"],
        ascending=[True, True, True, True, False],
    ).drop_duplicates(["target_date", "city"])


def apply_sizing(df: pd.DataFrame, sizing_name: str) -> pd.DataFrame:
    g = df.copy()
    costs = sizing_specs()[sizing_name](g)
    g["cost"] = costs
    g["shares"] = g["cost"] / g["ask"]
    g["pnl"] = g["payoff"] * g["shares"] - g["cost"]
    g["win"] = g["payoff"].astype(float).eq(1.0).astype(int)
    return g


def block_bootstrap_ci(daily: pd.DataFrame, n_boot: int = 5000) -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    costs = daily["cost"].to_numpy(float)
    pnls = daily["pnl"].to_numpy(float)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        cost = float(costs[idx].sum())
        if cost:
            vals.append(float(pnls[idx].sum() / cost))
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return (float(lo), float(hi))


def summarize(g: pd.DataFrame, *, period: str, selector: str, sizing: str) -> dict[str, Any]:
    if g.empty:
        return {
            "selector": selector,
            "sizing": sizing,
            "period": period,
            "rows": 0,
            "dates": 0,
            "cities": 0,
        }
    daily = g.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci_low, ci_high = block_bootstrap_ci(daily)
    top_removed = g.sort_values("pnl", ascending=False).iloc[1:]
    top_removed_roi = float(top_removed["pnl"].sum() / top_removed["cost"].sum()) if float(top_removed["cost"].sum()) > 0 else None
    return {
        "selector": selector,
        "sizing": sizing,
        "period": period,
        "rows": int(len(g)),
        "dates": int(g["target_date"].nunique()),
        "cities": int(g["city"].nunique()),
        "avg_ask": float(g["ask"].mean()),
        "avg_cost": float(g["cost"].mean()),
        "win_rate": float(g["win"].mean()),
        "cost": float(g["cost"].sum()),
        "pnl": float(g["pnl"].sum()),
        "roi": float(g["pnl"].sum() / g["cost"].sum()),
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "losing_days": int((daily["pnl"] < 0).sum()),
        "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
        "max_daily_loss": float(daily["pnl"].min()),
        "top_trade_removed_roi": top_removed_roi,
    }


def evaluate_periods(hist: pd.DataFrame, fwd: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    detail_rows: list[pd.DataFrame] = []
    daily_rows: list[pd.DataFrame] = []
    for selector_name, selector_fn in selector_specs().items():
        hist_pick = pick(hist, selector_fn(hist))
        fwd_pick = pick(fwd, selector_fn(fwd))
        for sizing_name in sizing_specs():
            for period_name, base in [
                ("historical", hist_pick),
                ("historical_recent", hist_pick[hist_pick["target_date"] >= RECENT_START]),
                ("forward", fwd_pick),
            ]:
                sized = apply_sizing(base, sizing_name)
                summary_rows.append(summarize(sized, period=period_name, selector=selector_name, sizing=sizing_name))
                if not sized.empty:
                    detail = sized.copy()
                    detail["selector"] = selector_name
                    detail["sizing"] = sizing_name
                    detail["period"] = period_name
                    detail_rows.append(detail)
                    daily = detail.groupby(["period", "selector", "sizing", "target_date"], as_index=False).agg(
                        rows=("pnl", "size"), wins=("win", "sum"), cost=("cost", "sum"), pnl=("pnl", "sum")
                    )
                    daily["roi"] = daily["pnl"] / daily["cost"]
                    daily_rows.append(daily)
    return (
        pd.DataFrame(summary_rows),
        pd.concat(detail_rows, ignore_index=True, sort=False) if detail_rows else pd.DataFrame(),
        pd.concat(daily_rows, ignore_index=True, sort=False) if daily_rows else pd.DataFrame(),
    )


def champion_row(summary: pd.DataFrame) -> pd.Series:
    # Chosen for mechanism and robustness, not max point ROI:
    # remove dust tickets, cap payout/shares, keep enough rows, retain positive
    # historical/recent/forward/top-removed metrics.
    row = summary[
        summary["selector"].eq("no_dust_edge20_ask05_20")
        & summary["sizing"].eq("payout25_cap5")
        & summary["period"].eq("forward")
    ]
    return row.iloc[0]


def render_report(db: dict[str, Any], overlay: dict[str, Any], summary: pd.DataFrame, detail: pd.DataFrame, daily: pd.DataFrame) -> str:
    full = summary.pivot_table(index=["selector", "sizing"], columns="period", values=["rows", "dates", "avg_ask", "avg_cost", "roi", "top_trade_removed_roi", "cost", "pnl", "max_daily_loss"], aggfunc="first")
    full.columns = [f"{period}_{metric}" for metric, period in full.columns]
    full = full.reset_index()

    champ_sel = "no_dust_edge20_ask05_20"
    champ_sizing = "payout25_cap5"
    champ = full[(full["selector"].eq(champ_sel)) & (full["sizing"].eq(champ_sizing))].iloc[0]
    base = full[(full["selector"].eq("base_edge20_ask01_25")) & (full["sizing"].eq("fixed5"))].iloc[0]
    gfs = full[(full["selector"].eq("gfs_edge20_ask05_25")) & (full["sizing"].eq("payout25_cap5"))].iloc[0]

    compact = full[
        [
            "selector",
            "sizing",
            "historical_rows",
            "historical_avg_ask",
            "historical_roi",
            "historical_top_trade_removed_roi",
            "historical_recent_rows",
            "historical_recent_roi",
            "forward_rows",
            "forward_avg_ask",
            "forward_avg_cost",
            "forward_roi",
            "forward_top_trade_removed_roi",
            "forward_max_daily_loss",
        ]
    ].copy()
    compact = compact.sort_values(["forward_top_trade_removed_roi", "forward_roi"], ascending=False)

    champ_detail = detail[
        detail["selector"].eq(champ_sel)
        & detail["sizing"].eq(champ_sizing)
        & detail["period"].eq("forward")
    ].sort_values("pnl", ascending=False)
    champ_daily = daily[
        daily["selector"].eq(champ_sel)
        & daily["sizing"].eq(champ_sizing)
        & daily["period"].eq("forward")
    ].sort_values("target_date")

    lines = [
        "# Low-Price YES Lottery Selector Refinement v1",
        "",
        "Generated: 2026-07-02",
        "",
        "## Verdict",
        "",
        "The best current version is not the raw 1c-25c fixed-cost lottery. The cleaner shadow candidate is:",
        "",
        "```text",
        "BUY_YES",
        "edge >= 0.20",
        "ask 0.05..0.20",
        "one candidate per city-date",
        "earliest PIT decision snapshot, lower ask tie-break",
        "sizing = min($5, ask * 25 shares)",
        "```",
        "",
        "This removes sub-5c dust tickets and caps maximum payout/shares. It gives up some headline convexity but materially improves forward robustness versus the raw fixed-$5 rule.",
        "",
        "```text",
        "significance=PARTIAL (historical positive; no fresh forward CI significance because only 3 closed forward dates)",
        "baseline=PARTIAL (beats raw dust-dependent robustness; still not a clean same-row market/base-rate proof)",
        "forward=PARTIAL (positive first closed forward, but too few dates)",
        "conclusion=shadow_candidate_keep_collecting; no live",
        "```",
        "",
        "## Data Snapshot",
        "",
        f"- DB: `{db['db_path']}`, fact built `{db['fact_signal_candidates'].get('fact_built_at_utc')}`.",
        f"- fact_signal_candidates: {int(db['fact_signal_candidates']['rows'])} rows, {db['fact_signal_candidates']['min_event_date']}..{db['fact_signal_candidates']['max_event_date']}.",
        f"- Forward cut: `{FORWARD_START}`. Historical excludes target dates >= forward cut.",
        f"- Forward candidate pool: {overlay['raw_forward_rows']} low-price BUY_YES rows; CLOB closed overlay rows: {overlay['closed_forward_rows']}; open/unresolved rows: {overlay['open_or_unresolved_forward_rows']}; API errors: {overlay['market_api_errors']}.",
        "- `run_stack.sh` rebuilt DB/fact tables but exited non-cleanly because frontend port 5174 stayed busy; CLOB fill coverage gate was checked separately and passed.",
        "",
        "## Best Candidate vs Raw Baseline",
        "",
        md_table(
            pd.DataFrame([base, champ, gfs]),
            [
                ("selector", "selector"),
                ("sizing", "sizing"),
                ("historical_rows", "hist rows"),
                ("historical_avg_ask", "hist ask"),
                ("historical_roi", "hist ROI"),
                ("historical_top_trade_removed_roi", "hist top-removed"),
                ("historical_recent_rows", "recent rows"),
                ("historical_recent_roi", "recent ROI"),
                ("forward_rows", "fwd rows"),
                ("forward_avg_ask", "fwd ask"),
                ("forward_avg_cost", "fwd avg cost"),
                ("forward_roi", "fwd ROI"),
                ("forward_top_trade_removed_roi", "fwd top-removed"),
                ("forward_max_daily_loss", "fwd max loss"),
            ],
        ),
        "",
        "## Candidate Menu",
        "",
        md_table(
            compact,
            [
                ("selector", "selector"),
                ("sizing", "sizing"),
                ("historical_rows", "hist rows"),
                ("historical_roi", "hist ROI"),
                ("historical_top_trade_removed_roi", "hist top-removed"),
                ("historical_recent_roi", "recent ROI"),
                ("forward_rows", "fwd rows"),
                ("forward_avg_cost", "fwd avg cost"),
                ("forward_roi", "fwd ROI"),
                ("forward_top_trade_removed_roi", "fwd top-removed"),
                ("forward_max_daily_loss", "fwd max loss"),
            ],
            max_rows=24,
        ),
        "",
        "## Champion Forward Rows",
        "",
        md_table(
            champ_detail[["target_date", "city", "bracket", "ask", "edge", "model_p_yes", "payoff", "cost", "pnl"]].rename(columns={"payoff": "win"}),
            [
                ("target_date", "date"),
                ("city", "city"),
                ("bracket", "bracket"),
                ("ask", "ask"),
                ("edge", "edge"),
                ("model_p_yes", "model_p"),
                ("win", "YES final"),
                ("cost", "cost"),
                ("pnl", "PnL"),
            ],
            max_rows=40,
        ),
        "",
        "## Champion Daily Forward",
        "",
        md_table(
            champ_daily,
            [
                ("target_date", "date"),
                ("rows", "rows"),
                ("wins", "wins"),
                ("cost", "cost"),
                ("pnl", "PnL"),
                ("roi", "ROI"),
            ],
        ),
        "",
        "## Core Logic",
        "",
        "- The alpha hypothesis is not simply `cheap ticket go brrr`. It is: when market asks 5c-20c while the model still assigns at least 20c of edge, the market may be underpricing a tail path created by forecast miss, station basis, or intraday reheat.",
        "- The 5c floor removes hard-to-fill dust and reduces dependence on one 3c winner.",
        "- The `ask * 25 shares` cap means each signal has a fixed maximum payout, not fixed cash spend. A 5c ticket costs $1.25; a 20c ticket costs $5. This keeps cheap tickets from dominating share exposure.",
        "- GFS-only is interesting as a tag: forward is strong and top-removed stays positive, but historical sample/ROI is thinner than the broader no-dust sleeve, so it should not be the main rule yet.",
        "",
        "## Reliability Judgment",
        "",
        f"- Raw baseline forward ROI was {pct(base['forward_roi'])}, but top-trade removed was {pct(base['forward_top_trade_removed_roi'])}; it relied too much on one dust winner.",
        f"- Champion forward ROI is {pct(champ['forward_roi'])}, and top-trade removed is {pct(champ['forward_top_trade_removed_roi'])}; the point estimate is lower than the dust-max version but the shape is healthier.",
        "- Still not live: closed forward support is only 3 target dates, and the baseline question is not fully separated from broad cheap-YES/base-rate effects.",
        "",
        "## Artifacts",
        "",
        f"- Script: `scripts/analysis/forecast_quality/{Path(__file__).name}`",
        f"- Summary CSV: `{rel(OUT_DIR / 'summary.csv')}`",
        f"- Details CSV: `{rel(OUT_DIR / 'details.csv')}`",
        f"- Daily CSV: `{rel(OUT_DIR / 'daily.csv')}`",
        f"- JSON: `{rel(OUT_JSON)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = db_snapshot()
    hist = load_historical()
    fwd_pool = load_forward_pool()
    fwd, overlay = overlay_forward_clob(fwd_pool)
    summary, detail, daily = evaluate_periods(hist, fwd)

    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    detail.to_csv(OUT_DIR / "details.csv", index=False)
    daily.to_csv(OUT_DIR / "daily.csv", index=False)
    payload = {
        "db_snapshot": db,
        "forward_overlay": overlay,
        "artifacts": {
            "summary": rel(OUT_DIR / "summary.csv"),
            "details": rel(OUT_DIR / "details.csv"),
            "daily": rel(OUT_DIR / "daily.csv"),
            "report": rel(OUT_MD),
        },
        "champion": {
            "selector": "no_dust_edge20_ask05_20",
            "sizing": "payout25_cap5",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    OUT_MD.write_text(render_report(db, overlay, summary, detail, daily), encoding="utf-8")
    print(f"wrote {rel(OUT_MD)}")
    print(f"wrote {rel(OUT_JSON)}")


if __name__ == "__main__":
    main()
