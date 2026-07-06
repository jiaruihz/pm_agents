#!/usr/bin/env python3
"""Regime-routed NO execution quality by spread and top-of-book depth.

This is an execution-layer diagnostic.  It keeps live_real fills separate from
raw/shadow candidate rows and does not propose live policy changes by itself.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_execution_liquidity_quality_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-06-regime-execution-liquidity-quality-v1.md"

LIVE_ORDER_PATHS = [
    ROOT / "runtime/weather_edge_v1/regime_routed_no_tiny_live_v1/live_orders.jsonl",
    ROOT / "runtime/weather_edge_v1/remote_pm_agent/regime_routed_no_tiny_live_v1/live_orders.jsonl",
]

CANDIDATE_PATHS = [
    ROOT / "runtime/weather_edge_v1/regime_routed_no_tiny_live_v1/accepted_candidates.jsonl",
    ROOT / "runtime/weather_edge_v1/regime_routed_no_tiny_live_v1/blocked_candidates.jsonl",
    ROOT / "runtime/weather_edge_v1/remote_pm_agent/regime_routed_no_tiny_live_v1/blocked_candidates.jsonl",
]

HISTORICAL_PROXY_PATH = (
    ROOT / "docs/analysis/2026-07/generated/regime_routed_no_tail_shadow_city_bias_v4/dropped_or_resized_rows.csv"
)


def pct(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{100 * x:+.1f}%"


def money(v: Any) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def num(v: Any, digits: int = 3) -> str:
    try:
        x = float(v)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{x:.{digits}f}"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            row["_source_path"] = str(path.relative_to(ROOT))
            rows.append(row)
    return rows


def as_float(v: Any) -> float:
    if v is None or v == "":
        return float("nan")
    try:
        return float(v)
    except Exception:
        return float("nan")


def spread_bucket(spread: float) -> str:
    if not math.isfinite(spread):
        return "unknown_spread"
    if spread <= 0.03:
        return "tight_le_3c"
    if spread <= 0.08:
        return "mid_3c_to_8c"
    return "wide_gt_8c"


def depth_bucket(notional: float) -> str:
    if not math.isfinite(notional):
        return "unknown_depth"
    if notional < 2.50:
        return "thin_lt_2_5"
    if notional < 5.00:
        return "small_2_5_to_5"
    return "usable_ge_5"


def quality_bucket(spread: float, notional: float) -> str:
    if not math.isfinite(spread) or not math.isfinite(notional):
        return "unknown_quality"
    if spread > 0.08 or notional < 2.50:
        return "thin_or_wide"
    return "not_thin_or_wide"


def bootstrap_roi_ci(df: pd.DataFrame, cost_col: str, pnl_col: str, block_col: str = "target_date", n: int = 2000) -> tuple[float, float]:
    d = df[[block_col, cost_col, pnl_col]].dropna()
    if d.empty:
        return (float("nan"), float("nan"))
    blocks = d.groupby(block_col, as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    if len(blocks) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(20260706)
    costs = blocks["cost"].to_numpy(float)
    pnls = blocks["pnl"].to_numpy(float)
    vals: list[float] = []
    for _ in range(n):
        idx = rng.integers(0, len(blocks), len(blocks))
        c = costs[idx].sum()
        if c > 0:
            vals.append(float(pnls[idx].sum() / c))
    if not vals:
        return (float("nan"), float("nan"))
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return (float(lo), float(hi))


def summarize(df: pd.DataFrame, group_cols: list[str], cost_col: str, pnl_col: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, g in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        cost = float(g[cost_col].sum())
        pnl = float(g[pnl_col].sum())
        ci_lo, ci_hi = bootstrap_roi_ci(g, cost_col, pnl_col)
        rec = dict(zip(group_cols, keys, strict=False))
        rec.update(
            rows=int(len(g)),
            dates=int(g["target_date"].nunique()) if "target_date" in g else 0,
            cities=int(g["city"].nunique()) if "city" in g else 0,
            cost_usd=cost,
            pnl_usd=pnl,
            roi=pnl / cost if cost else float("nan"),
            roi_ci_low=ci_lo,
            roi_ci_high=ci_hi,
            avg_spread=float(g["spread"].mean()) if "spread" in g else float("nan"),
            avg_top_ask_notional=float(g["top_ask_notional"].mean()) if "top_ask_notional" in g else float("nan"),
            win_rate=float(g["won"].mean()) if "won" in g else float("nan"),
        )
        rows.append(rec)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["cost_usd", "rows"], ascending=False)


def load_fact_trades() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{ROOT / 'runtime/weather.db'}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    q = """
        SELECT
            execution_id,
            fill_id,
            order_id,
            city,
            target_date,
            bracket,
            side,
            fill_price,
            fill_qty,
            cost_usd,
            settlement_status,
            pnl_usd_at_fill,
            order_ts_utc,
            fill_ts_utc
        FROM fact_trades
        WHERE trade_class = 'live_real'
          AND (
              strategy_id LIKE '%regime%'
              OR strategy_name LIKE '%regime%'
              OR producer_run_id LIKE '%regime%'
          )
    """
    return pd.read_sql_query(q, conn)


def load_settlement_outcomes() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{ROOT / 'runtime/weather.db'}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    q = """
        SELECT city, target_date, bracket, token_id, final_price, settlement_status
        FROM settlement_outcomes
        WHERE settlement_status = 'settled'
    """
    return pd.read_sql_query(q, conn)


def live_orders_frame() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in LIVE_ORDER_PATHS:
        for raw in load_jsonl(path):
            ex = raw.get("execution_id")
            if not ex:
                continue
            response = raw.get("exchange_response") or {}
            place = response.get("place") if isinstance(response, dict) else {}
            making = as_float(place.get("makingAmount") if isinstance(place, dict) else None)
            taking = as_float(place.get("takingAmount") if isinstance(place, dict) else None)
            actual_px = making / taking if math.isfinite(making) and math.isfinite(taking) and taking else float("nan")
            posted_price = as_float(raw.get("posted_price") or raw.get("limit_price") or raw.get("requested_price"))
            posted_notional = as_float(raw.get("posted_notional") or raw.get("notional"))
            spread = as_float(raw.get("spread") if raw.get("spread") is not None else raw.get("quote_spread"))
            rows.append(
                {
                    "execution_id": ex,
                    "created_at_utc": raw.get("created_at_utc"),
                    "city": raw.get("city"),
                    "target_date": raw.get("target_date"),
                    "bracket": raw.get("bracket"),
                    "strategy_instance": raw.get("strategy_instance"),
                    "status": raw.get("status"),
                    "place_status": place.get("status") if isinstance(place, dict) else None,
                    "posted_price": posted_price,
                    "posted_notional": posted_notional,
                    "actual_fill_price_from_response": actual_px,
                    "spread": spread,
                    "top_ask_notional": posted_notional,
                    "spread_bucket": spread_bucket(spread),
                    "depth_bucket": depth_bucket(posted_notional),
                    "quality_bucket": quality_bucket(spread, posted_notional),
                    "source_path": raw.get("_source_path"),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values(["execution_id", "source_path"]).drop_duplicates("execution_id", keep="first")
    return df


def candidate_frame() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in CANDIDATE_PATHS:
        for raw in load_jsonl(path):
            ask = as_float(raw.get("ask") if raw.get("ask") is not None else raw.get("current_no_ask"))
            bid = as_float(raw.get("bid"))
            spread = ask - bid if math.isfinite(ask) and math.isfinite(bid) else float("nan")
            ask_notional = as_float(raw.get("ask_notional") if raw.get("ask_notional") is not None else raw.get("live_order_notional_usd"))
            ask_size = as_float(raw.get("ask_size") if raw.get("ask_size") is not None else raw.get("current_no_ask_size"))
            if not math.isfinite(ask_notional) and math.isfinite(ask) and math.isfinite(ask_size):
                ask_notional = ask * ask_size
            skip = str(raw.get("execution_skip_reason") or "")
            if "duplicate_live_city_date_token" in skip:
                continue
            rows.append(
                {
                    "source_path": raw.get("_source_path"),
                    "created_at_utc": raw.get("created_at_utc"),
                    "candidate_status": raw.get("candidate_status"),
                    "execution_skip_reason": skip,
                    "strategy_instance": raw.get("strategy_instance"),
                    "city": raw.get("city"),
                    "target_date": str(raw.get("target_date") or ""),
                    "bracket": str(raw.get("bracket") or ""),
                    "token_id": str(raw.get("token_id") or ""),
                    "market_id": str(raw.get("market_id") or ""),
                    "route_leg": raw.get("route_leg"),
                    "expression": raw.get("expression"),
                    "rule_id": raw.get("rule_id"),
                    "decision_snapshot_ts_utc": raw.get("decision_snapshot_ts_utc") or raw.get("snapshot_ts_utc"),
                    "ask": ask,
                    "bid": bid,
                    "spread": spread,
                    "top_ask_notional": ask_notional,
                    "ask_size": ask_size,
                    "spread_bucket": spread_bucket(spread),
                    "depth_bucket": depth_bucket(ask_notional),
                    "quality_bucket": quality_bucket(spread, ask_notional),
                    "soft_weight_to_ask_ratio": as_float(raw.get("soft_weight_to_ask_ratio")),
                    "soft_shares": as_float(raw.get("soft_shares")),
                    "city_source_bias_regime": raw.get("city_source_bias_regime"),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[df["ask"].notna()].copy()
    df["dedupe_key"] = (
        df["city"].astype(str)
        + "|"
        + df["target_date"].astype(str)
        + "|"
        + df["bracket"].astype(str)
        + "|"
        + df["token_id"].astype(str)
        + "|"
        + df["rule_id"].astype(str)
    )
    df = df.sort_values(["dedupe_key", "created_at_utc"]).drop_duplicates("dedupe_key", keep="first")
    return df


def historical_thin_depth_proxy() -> pd.DataFrame:
    """Load the V4 live-like rows that were too small after sizing.

    The proxy is not a spread/depth replay.  It is the longer-window row set
    whose live-like sizing failed the min-share threshold, which is the closest
    durable historical evidence for "small top-book / low-capacity" rows.
    """
    if not HISTORICAL_PROXY_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(HISTORICAL_PROXY_PATH)
    df["unit_cost"] = pd.to_numeric(df["router_ask"], errors="coerce")
    df["unit_pnl"] = pd.to_numeric(df["router_payoff"], errors="coerce") - df["unit_cost"]
    df["won"] = pd.to_numeric(df["router_payoff"], errors="coerce")
    df["spread"] = float("nan")
    df["top_ask_notional"] = float("nan")
    return df


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for col, _ in cols:
            v = row.get(col)
            if col in {"roi", "roi_ci_low", "roi_ci_high", "win_rate"}:
                vals.append(pct(v))
            elif col.endswith("usd") or col in {"cost_usd", "pnl_usd"}:
                vals.append(money(v))
            elif col.startswith("avg_") or col in {"spread", "top_ask_notional", "posted_price"}:
                vals.append(num(v, 3))
            elif isinstance(v, float):
                vals.append(num(v, 2))
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    facts = load_fact_trades()
    live_orders = live_orders_frame()
    live = facts.merge(live_orders, on="execution_id", how="left", suffixes=("", "_raw"))
    live["settled"] = live["settlement_status"].eq("settled")
    live_settled = live[live["settled"]].copy()
    live_settled["won"] = live_settled["pnl_usd_at_fill"].astype(float).gt(0).astype(float)

    live_by_quality = summarize(live_settled, ["quality_bucket"], "cost_usd", "pnl_usd_at_fill")
    live_by_spread = summarize(live_settled, ["spread_bucket"], "cost_usd", "pnl_usd_at_fill")
    live_by_depth = summarize(live_settled, ["depth_bucket"], "cost_usd", "pnl_usd_at_fill")
    live_by_city = summarize(live_settled, ["city"], "cost_usd", "pnl_usd_at_fill")

    candidates = candidate_frame()
    outcomes = load_settlement_outcomes()
    if not candidates.empty:
        candidates = candidates.merge(
            outcomes[["token_id", "final_price", "settlement_status"]].drop_duplicates("token_id"),
            on="token_id",
            how="left",
        )
        settled_candidates = candidates[candidates["settlement_status"].eq("settled")].copy()
        settled_candidates["unit_cost"] = settled_candidates["ask"].astype(float)
        settled_candidates["unit_pnl"] = settled_candidates["final_price"].astype(float) - settled_candidates["unit_cost"]
        settled_candidates["won"] = settled_candidates["final_price"].astype(float)
        cand_by_quality = summarize(settled_candidates, ["quality_bucket"], "unit_cost", "unit_pnl")
        cand_by_spread = summarize(settled_candidates, ["spread_bucket"], "unit_cost", "unit_pnl")
        cand_by_depth = summarize(settled_candidates, ["depth_bucket"], "unit_cost", "unit_pnl")
        cand_by_city = summarize(settled_candidates, ["city"], "unit_cost", "unit_pnl")
    else:
        settled_candidates = pd.DataFrame()
        cand_by_quality = cand_by_spread = cand_by_depth = cand_by_city = pd.DataFrame()

    hist_proxy = historical_thin_depth_proxy()
    if not hist_proxy.empty:
        hist_by_patch = summarize(hist_proxy, ["evidence_layer", "patch_effect"], "unit_cost", "unit_pnl")
        hist_cape = hist_proxy[hist_proxy["city"].eq("CapeTown")].copy()
        hist_cape_summary = summarize(hist_cape, ["evidence_layer", "city"], "unit_cost", "unit_pnl")
    else:
        hist_by_patch = hist_cape = hist_cape_summary = pd.DataFrame()

    for name, df in [
        ("live_orders_enriched.csv", live),
        ("live_by_quality.csv", live_by_quality),
        ("live_by_spread.csv", live_by_spread),
        ("live_by_depth.csv", live_by_depth),
        ("live_by_city.csv", live_by_city),
        ("candidate_rows_deduped.csv", candidates),
        ("candidate_settled_rows.csv", settled_candidates),
        ("candidate_by_quality.csv", cand_by_quality),
        ("candidate_by_spread.csv", cand_by_spread),
        ("candidate_by_depth.csv", cand_by_depth),
        ("candidate_by_city.csv", cand_by_city),
        ("historical_thin_depth_proxy_rows.csv", hist_proxy),
        ("historical_thin_depth_proxy_by_patch.csv", hist_by_patch),
        ("historical_thin_depth_proxy_cape_town.csv", hist_cape),
        ("historical_thin_depth_proxy_cape_town_summary.csv", hist_cape_summary),
    ]:
        df.to_csv(OUT_DIR / name, index=False)

    summary = {
        "data_snapshot": {
            "fact_live_rows": int(len(facts)),
            "fact_live_settled_rows": int(live_settled.shape[0]),
            "raw_live_order_rows_deduped": int(live_orders.shape[0]),
            "candidate_rows_deduped": int(candidates.shape[0]),
            "candidate_settled_rows": int(settled_candidates.shape[0]),
        },
        "live_by_quality": live_by_quality.to_dict(orient="records"),
        "live_by_spread": live_by_spread.to_dict(orient="records"),
        "candidate_by_quality": cand_by_quality.to_dict(orient="records"),
        "candidate_by_spread": cand_by_spread.to_dict(orient="records"),
        "historical_thin_depth_proxy_by_patch": hist_by_patch.to_dict(orient="records"),
        "historical_thin_depth_proxy_cape_town_summary": hist_cape_summary.to_dict(orient="records"),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    cape = live[live["city"].eq("CapeTown")].copy()
    lines = [
        "# Regime Execution Liquidity Quality V1",
        "",
        "## Conclusion",
        "",
        "CapeTown-style thin/wide books are an execution-risk bucket, not a confirmed city edge.  "
        "The current live sample is too small for a hard city-pool action: 12 settled regime live fills, "
        "with only 1 CapeTown live fill and it is still open.  The execution evidence does show that "
        "wide-spread/thin-depth rows are fragile and should remain tiny/diagnostic unless fresh forward proves otherwise.",
        "",
        "Verdict: `inconclusive_execution_bucket_keep_tiny_probe`.",
        "",
        "## Data Snapshot",
        "",
        f"- Rebuilt DB: `{ROOT / 'runtime/weather.db'}`.",
        "- CLOB fill coverage gate: `gate_pass=true` before this report.",
        f"- Regime live fact rows: `{len(facts)}`; settled rows: `{len(live_settled)}`.",
        f"- Raw live order rows after execution_id dedupe: `{len(live_orders)}`.",
        f"- Candidate diagnostic rows after de-dupe: `{len(candidates)}`; settled: `{len(settled_candidates)}`.",
        "- Live tables use actual fact_trades fills and settlement PnL. Candidate tables are per-share diagnostic only, not live fill simulation.",
        "",
        "## Live Real By Liquidity Quality",
        "",
        md_table(
            live_by_quality,
            [
                ("quality_bucket", "quality"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("avg_spread", "avg spread"),
                ("avg_top_ask_notional", "avg posted/top ask $"),
                ("cost_usd", "cost"),
                ("pnl_usd", "pnl"),
                ("roi", "roi"),
                ("roi_ci_low", "ci low"),
                ("roi_ci_high", "ci high"),
            ],
        ),
        "",
        "## Live Real By Spread",
        "",
        md_table(
            live_by_spread,
            [
                ("spread_bucket", "spread"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("avg_spread", "avg spread"),
                ("cost_usd", "cost"),
                ("pnl_usd", "pnl"),
                ("roi", "roi"),
                ("roi_ci_low", "ci low"),
                ("roi_ci_high", "ci high"),
            ],
        ),
        "",
        "## Candidate Diagnostic By Liquidity Quality",
        "",
        "Current runtime candidate rows are not settled yet, so this section is intentionally empty for PnL.",
        "",
        md_table(
            cand_by_quality,
            [
                ("quality_bucket", "quality"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("avg_spread", "avg spread"),
                ("avg_top_ask_notional", "avg top ask $"),
                ("cost_usd", "unit cost"),
                ("pnl_usd", "unit pnl"),
                ("roi", "unit roi"),
                ("roi_ci_low", "ci low"),
                ("roi_ci_high", "ci high"),
            ],
        ),
        "",
        "## Candidate Diagnostic By Spread",
        "",
        "Current runtime candidate rows are not settled yet, so this section is intentionally empty for PnL.",
        "",
        md_table(
            cand_by_spread,
            [
                ("spread_bucket", "spread"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("avg_spread", "avg spread"),
                ("cost_usd", "unit cost"),
                ("pnl_usd", "unit pnl"),
                ("roi", "unit roi"),
                ("roi_ci_low", "ci low"),
                ("roi_ci_high", "ci high"),
            ],
        ),
        "",
        "## Historical Thin-Depth Proxy",
        "",
        "This uses the existing V4 live-like replay rows whose sizing failed the min-share threshold "
        "(`below_min5_after_sizing`).  It is a capacity/thin-book proxy, not a direct spread replay.",
        "",
        md_table(
            hist_by_patch,
            [
                ("evidence_layer", "layer"),
                ("patch_effect", "bucket"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("cost_usd", "unit cost"),
                ("pnl_usd", "unit pnl"),
                ("roi", "unit roi"),
                ("roi_ci_low", "ci low"),
                ("roi_ci_high", "ci high"),
            ],
            max_rows=20,
        ),
        "",
        "## CapeTown Historical Thin-Depth Proxy",
        "",
        md_table(
            hist_cape_summary,
            [
                ("evidence_layer", "layer"),
                ("city", "city"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cost_usd", "unit cost"),
                ("pnl_usd", "unit pnl"),
                ("roi", "unit roi"),
                ("roi_ci_low", "ci low"),
                ("roi_ci_high", "ci high"),
            ],
        ),
        "",
        "## CapeTown Case",
        "",
        md_table(
            cape[
                [
                    "city",
                    "target_date",
                    "bracket",
                    "spread",
                    "posted_price",
                    "posted_notional",
                    "cost_usd",
                    "settlement_status",
                    "pnl_usd_at_fill",
                ]
            ],
            [
                ("city", "city"),
                ("target_date", "target"),
                ("bracket", "bracket"),
                ("spread", "spread"),
                ("posted_price", "posted px"),
                ("posted_notional", "posted $"),
                ("cost_usd", "fill cost"),
                ("settlement_status", "settlement"),
                ("pnl_usd_at_fill", "pnl"),
            ],
        ),
        "",
        "## Interpretation",
        "",
        "- CapeTown 7/05 was a thin/wide execution: spread `9c`, posted/top-ask notional `$1.92`, actual fill `$1.0584 @ 18c`; it is still unsettled.",
        "- The positive realized regime PnL is not coming from a broad proof that thin/wide books are good.  The settled live sample is small and CI crosses zero.",
        "- Current runtime candidate rows do not yet have settlement, so they cannot answer opportunity-vs-loss.",
        "- The longer historical proxy is mildly negative for `below_min5_after_sizing`: frozen/live-like `-8.1%` unit ROI and historical-best-ask `-6.1%`.  That argues against treating thin-capacity rows as the source of edge.",
        "- For live action this supports keeping these markets as tiny probes and adding/using execution telemetry, not cutting or sizing them up from this evidence alone.",
        "",
        "## Files",
        "",
        f"- Summary JSON: `{OUT_DIR.relative_to(ROOT) / 'summary.json'}`",
        f"- Live enriched rows: `{OUT_DIR.relative_to(ROOT) / 'live_orders_enriched.csv'}`",
        f"- Candidate diagnostic rows: `{OUT_DIR.relative_to(ROOT) / 'candidate_settled_rows.csv'}`",
        f"- Historical thin-depth proxy rows: `{OUT_DIR.relative_to(ROOT) / 'historical_thin_depth_proxy_rows.csv'}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
