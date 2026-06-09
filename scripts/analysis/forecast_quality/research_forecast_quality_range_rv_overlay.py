#!/usr/bin/env python3
"""Test whether forecast-quality filters improve Range RV proxy returns.

This is local research only. It uses fact_signal_candidates as the source of
decision-time model/market probabilities and settled labels. It does not use
live PnL and does not modify N100/live configuration.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime/weather.db"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-09-forecast-quality-range-rv-overlay.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-09-forecast-quality-range-rv-overlay.md"
TARGET_METRIC = "forecast_quality_filter_range_rv_proxy_value"
SEED = 20260609


def parse_bracket_value(label: Any) -> float | None:
    if label is None:
        return None
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(label))
    if not nums:
        return None
    vals = [float(x) for x in nums]
    return sum(vals) / len(vals)


def entropy(probs: np.ndarray) -> float:
    p = probs[np.isfinite(probs) & (probs > 0)]
    if len(p) == 0:
        return float("nan")
    h = -float(np.sum(p * np.log(p)))
    return h / math.log(len(probs)) if len(probs) > 1 else 0.0


def q(s: pd.Series, quantile: float, fallback: float) -> float:
    values = s.replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return fallback
    return float(values.quantile(quantile))


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x * 100:+.1f}%"


def money(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x:+.2f}"


def run_sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def run_sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def bootstrap_roi_by_date(df: pd.DataFrame, n_boot: int = 2000) -> dict[str, Any]:
    if df.empty or df["cost"].sum() <= 0:
        return {"roi": None, "ci95": [None, None], "event_dates": 0}
    daily = df.groupby("event_date", dropna=False).agg({"pnl": "sum", "cost": "sum"})
    daily = daily[daily["cost"] > 0]
    if daily.empty:
        return {"roi": None, "ci95": [None, None], "event_dates": 0}
    rng = np.random.default_rng(SEED)
    vals = daily.to_numpy(dtype=float)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, len(vals), len(vals))
        sample = vals[idx]
        boots[i] = float(sample[:, 0].sum() / sample[:, 1].sum())
    roi = float(daily["pnl"].sum() / daily["cost"].sum())
    return {
        "roi": roi,
        "ci95": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
        "event_dates": int(len(daily)),
    }


def split_by_date(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    dates = sorted(str(x) for x in df["event_date"].dropna().unique())
    cut = max(1, min(len(dates) - 1, int(math.floor(len(dates) * 0.7)))) if len(dates) > 1 else 1
    train_dates = set(dates[:cut])
    holdout_dates = set(dates[cut:])
    return (
        df[df["event_date"].astype(str).isin(train_dates)].copy(),
        df[df["event_date"].astype(str).isin(holdout_dates)].copy(),
        {
            "method": "event_date chronological 70/30 split",
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
            "train_dates": len(train_dates),
            "holdout_dates": len(holdout_dates),
        },
    )


def load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT
          candidate_id, condition_id, market_id, side, event_date, city, city_pool,
          bracket, forecast_source, model_version, decision_hours_to_settle,
          decision_snapshot_ts_utc, model_p_yes, market_yes_price, final_yes,
          settlement_status, decision_window_missing, fact_built_at_utc
        FROM fact_signal_candidates
        WHERE settlement_status='settled'
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND final_yes IS NOT NULL
        """,
        conn,
    )
    df["model_p_yes"] = pd.to_numeric(df["model_p_yes"], errors="coerce")
    df["market_yes_price"] = pd.to_numeric(df["market_yes_price"], errors="coerce")
    df["final_yes"] = pd.to_numeric(df["final_yes"], errors="coerce")
    df["bracket_value"] = df["bracket"].map(parse_bracket_value)
    return df.dropna(subset=["model_p_yes", "market_yes_price", "final_yes", "bracket_value"])


def build_decision_sets(cands: pd.DataFrame) -> pd.DataFrame:
    group_cols = [
        "city",
        "event_date",
        "forecast_source",
        "model_version",
        "decision_snapshot_ts_utc",
    ]
    rows: list[dict[str, Any]] = []
    for key, g in cands.groupby(group_cols, dropna=False):
        legs = (
            g.sort_values(["bracket_value", "bracket", "side"])
            .groupby("bracket", as_index=False, dropna=False)
            .agg(
                {
                    "bracket_value": "first",
                    "model_p_yes": "median",
                    "market_yes_price": "median",
                    "final_yes": "max",
                    "decision_hours_to_settle": "median",
                }
            )
            .sort_values(["bracket_value", "bracket"])
            .reset_index(drop=True)
        )
        if len(legs) < 3:
            continue
        model_raw = legs["model_p_yes"].clip(lower=0).to_numpy(dtype=float)
        market_raw = legs["market_yes_price"].clip(lower=0).to_numpy(dtype=float)
        if model_raw.sum() <= 0 or market_raw.sum() <= 0:
            continue
        model = model_raw / model_raw.sum()
        market = market_raw / market_raw.sum()
        final_hits = legs.index[legs["final_yes"] >= 0.5].tolist()
        if len(final_hits) != 1:
            continue
        final_i = int(final_hits[0])
        mode_i = int(np.argmax(model))
        idx = np.arange(len(legs))
        adj2_mask = np.abs(idx - mode_i) <= 1
        adj3_mask = np.abs(idx - mode_i) <= 2
        row = dict(zip(group_cols, key))
        row["decision_set_id"] = "|".join(str(x) for x in key)
        row["n_brackets"] = int(len(legs))
        row["mode_i"] = mode_i
        row["final_i"] = final_i
        row["decision_hours_to_settle"] = float(legs["decision_hours_to_settle"].median())
        row["model_entropy"] = entropy(model)
        row["model_mode_probability"] = float(model[mode_i])
        row["model_adjacent2_mass"] = float(model[adj2_mask].sum())
        row["model_adjacent3_mass"] = float(model[adj3_mask].sum())
        row["model_tail_mass_outside_adjacent3"] = float(model[~adj3_mask].sum())
        row["market_tail_mass_outside_adjacent3"] = float(market[~adj3_mask].sum())
        row["model_market_l1_gap"] = float(np.abs(model - market).sum())
        row["distribution_variance"] = float(
            np.sum(model * (legs["bracket_value"].to_numpy(dtype=float) - np.sum(model * legs["bracket_value"].to_numpy(dtype=float))) ** 2)
        )
        row["final_in_adjacent3"] = int(abs(final_i - mode_i) <= 2)
        row["legs_json"] = json.dumps(
            [
                {
                    "bracket": str(r["bracket"]),
                    "market": float(r["market_yes_price"]),
                    "model": float(r["model_p_yes"]),
                    "final_yes": float(r["final_yes"]),
                }
                for _, r in legs.iterrows()
            ],
            ensure_ascii=False,
        )
        rows.append(row)
    return pd.DataFrame(rows)


def add_historical_features(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.sort_values("event_date").copy()
    out["hist_adj3_miss"] = np.nan
    out["hist_n"] = 0
    for _, idxs in out.groupby(["city", "forecast_source", "model_version"], dropna=False).groups.items():
        hist: list[int] = []
        for idx in idxs:
            out.loc[idx, "hist_n"] = len(hist)
            if hist:
                out.loc[idx, "hist_adj3_miss"] = float(np.mean(hist))
            hist.append(1 - int(out.loc[idx, "final_in_adjacent3"]))
    global_miss = float(1 - out["final_in_adjacent3"].mean()) if len(out) else 0.0
    out["hist_adj3_miss_filled"] = out["hist_adj3_miss"].fillna(global_miss)
    return out


def derive_filters(train: pd.DataFrame, all_sets: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    out = all_sets.copy()
    thresholds = {
        "tail_q75": q(train["model_tail_mass_outside_adjacent3"], 0.75, 0.25),
        "tail_q50": q(train["model_tail_mass_outside_adjacent3"], 0.50, 0.15),
        "adj3_q50": q(train["model_adjacent3_mass"], 0.50, 0.80),
        "adj3_q67": q(train["model_adjacent3_mass"], 0.67, 0.90),
        "entropy_q67": q(train["model_entropy"], 0.67, 0.75),
        "mode_q67": q(train["model_mode_probability"], 0.67, 0.35),
        "hist_q75": q(train["hist_adj3_miss_filled"], 0.75, 0.25),
    }
    out["filter_no_filter"] = 1
    out["filter_loose_drop_worst_tail"] = (
        out["model_tail_mass_outside_adjacent3"] <= thresholds["tail_q75"]
    ).astype(int)
    out["filter_medium_quality"] = (
        (out["model_tail_mass_outside_adjacent3"] <= thresholds["tail_q50"])
        & (out["model_adjacent3_mass"] >= thresholds["adj3_q50"])
    ).astype(int)
    out["filter_strict_low_uncertainty"] = (
        (out["model_entropy"] <= thresholds["entropy_q67"])
        & (out["model_mode_probability"] >= thresholds["mode_q67"])
        & (out["model_adjacent3_mass"] >= thresholds["adj3_q67"])
        & (out["hist_adj3_miss_filled"] <= thresholds["hist_q75"])
    ).astype(int)
    return out, thresholds


def centered_range_indices(mode_i: int, n: int, width: int) -> list[int] | None:
    if width == 2:
        if mode_i == 0:
            return [0, 1]
        if mode_i == n - 1:
            return [n - 2, n - 1]
        # choose the side whose market cost is handled later by the legs' prices;
        # for a deterministic proxy use lower temperature side first.
        return [mode_i - 1, mode_i]
    if width == 3:
        if mode_i == 0:
            return [0, 1, 2]
        if mode_i == n - 1:
            return [n - 3, n - 2, n - 1]
        return [mode_i - 1, mode_i, mode_i + 1]
    return None


def build_range_rows(decision_sets: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, ds in decision_sets.iterrows():
        legs = json.loads(ds["legs_json"])
        n = len(legs)
        for width in (2, 3):
            idxs = centered_range_indices(int(ds["mode_i"]), n, width)
            if not idxs:
                continue
            selected = [legs[i] for i in idxs]
            cost = sum(float(x["market"]) for x in selected)
            if cost <= 0 or cost >= 1:
                continue
            payout = 1.0 if int(ds["final_i"]) in idxs else 0.0
            row = ds.drop(labels=["legs_json"]).to_dict()
            row.update(
                {
                    "range_width": width,
                    "range_brackets": ",".join(str(x["bracket"]) for x in selected),
                    "cost": cost,
                    "payout": payout,
                    "pnl": payout - cost,
                    "hit": int(payout > 0),
                    "cheap_70": int(cost <= 0.70),
                    "cheap_75": int(cost <= 0.75),
                    "cheap_80": int(cost <= 0.80),
                    "cheap_85": int(cost <= 0.85),
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def summarize_slice(df: pd.DataFrame) -> dict[str, Any]:
    roi = bootstrap_roi_by_date(df)
    return {
        "rows": int(len(df)),
        "event_dates": int(df["event_date"].nunique()) if not df.empty else 0,
        "cities": int(df["city"].nunique()) if not df.empty else 0,
        "cost": float(df["cost"].sum()) if not df.empty else 0.0,
        "pnl": float(df["pnl"].sum()) if not df.empty else 0.0,
        "roi": roi["roi"],
        "roi_ci95": roi["ci95"],
        "hit_rate": float(df["hit"].mean()) if not df.empty else None,
    }


def evaluate(range_rows: pd.DataFrame, train_dates: set[str], holdout_dates: set[str]) -> list[dict[str, Any]]:
    filters = [
        "filter_no_filter",
        "filter_loose_drop_worst_tail",
        "filter_medium_quality",
        "filter_strict_low_uncertainty",
    ]
    cost_caps = [0.70, 0.75, 0.80, 0.85]
    out: list[dict[str, Any]] = []
    for width in (2, 3):
        base_width = range_rows[range_rows["range_width"] == width]
        for cap in cost_caps:
            cap_rows = base_width[base_width["cost"] <= cap]
            baseline_train = cap_rows[cap_rows["event_date"].astype(str).isin(train_dates)]
            baseline_holdout = cap_rows[cap_rows["event_date"].astype(str).isin(holdout_dates)]
            base_train_roi = summarize_slice(baseline_train)["roi"]
            base_holdout_roi = summarize_slice(baseline_holdout)["roi"]
            for f in filters:
                selected = cap_rows[cap_rows[f] == 1]
                train = selected[selected["event_date"].astype(str).isin(train_dates)]
                holdout = selected[selected["event_date"].astype(str).isin(holdout_dates)]
                train_sum = summarize_slice(train)
                holdout_sum = summarize_slice(holdout)
                out.append(
                    {
                        "range_width": width,
                        "cost_cap": cap,
                        "filter": f.replace("filter_", ""),
                        "train": train_sum,
                        "holdout": holdout_sum,
                        "train_roi_delta_vs_no_filter": None
                        if train_sum["roi"] is None or base_train_roi is None
                        else train_sum["roi"] - base_train_roi,
                        "holdout_roi_delta_vs_no_filter": None
                        if holdout_sum["roi"] is None or base_holdout_roi is None
                        else holdout_sum["roi"] - base_holdout_roi,
                    }
                )
    return out


def table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any]) -> str:
    best_rows = []
    representative: dict[tuple[int, float, str], dict[str, Any]] = {}
    for item in payload["results"]:
        h = item["holdout"]
        if h["rows"] == 0:
            continue
        representative[(int(item["range_width"]), float(item["cost_cap"]), str(item["filter"]))] = item
        best_rows.append(
            {
                "width": item["range_width"],
                "cost_cap": item["cost_cap"],
                "filter": item["filter"],
                "train_rows": item["train"]["rows"],
                "train_roi": pct(item["train"]["roi"]),
                "train_ci": f"[{pct(item['train']['roi_ci95'][0])}, {pct(item['train']['roi_ci95'][1])}]",
                "holdout_rows": h["rows"],
                "holdout_roi": pct(h["roi"]),
                "holdout_ci": f"[{pct(h['roi_ci95'][0])}, {pct(h['roi_ci95'][1])}]",
                "holdout_delta": pct(item["holdout_roi_delta_vs_no_filter"]),
                "holdout_pnl": money(h["pnl"]),
            }
        )
    best_rows = sorted(best_rows, key=lambda r: (r["width"], r["cost_cap"], r["filter"]))
    adj2 = representative.get((2, 0.75, "no_filter"))
    adj2_strict = representative.get((2, 0.75, "strict_low_uncertainty"))
    adj3 = representative.get((3, 0.85, "no_filter"))
    adj3_medium = representative.get((3, 0.85, "medium_quality"))
    funnel = payload["funnel"]
    lines = [
        "# Forecast Quality Filter Range RV Overlay",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{payload['db_path']}`",
        "> Scope: local research only; no N100/live config changed; no live action.",
        "",
        "## 数据快照",
        "",
        f"- fact_signal_candidates rows: `{payload['self_check']['fact_signal_candidates_rows']}`",
        f"- fact_trades rows: `{payload['self_check']['fact_trades_rows']}`",
        f"- decision_sets: `{funnel['decision_sets']}`",
        f"- range rows: `{funnel['range_rows']}`",
        f"- train: `{payload['split']['train_start']}` -> `{payload['split']['train_end']}` ({payload['split']['train_dates']} dates)",
        f"- holdout: `{payload['split']['holdout_start']}` -> `{payload['split']['holdout_end']}` ({payload['split']['holdout_dates']} dates)",
        "",
        "## 大白话结论",
        "",
        "- 如果只看这个 decision-price proxy，Range RV 本身是赚钱的：不加过滤的 adjacent2 在 holdout 大约 `"
        + pct(adj2["holdout"]["roi"] if adj2 else None)
        + "`，adjacent3 大约 `"
        + pct(adj3["holdout"]["roi"] if adj3 else None)
        + "`。",
        "- 但这不是 live/executable 结论，只是用 fact 表里的 `market_yes_price` 做的历史 counterfactual。",
        "- forecast quality 过滤没有稳定证明“比不筛更赚钱”。adjacent2 严格过滤从 `"
        + str(adj2["holdout"]["rows"] if adj2 else "NA")
        + "` 行砍到 `"
        + str(adj2_strict["holdout"]["rows"] if adj2_strict else "NA")
        + "` 行，ROI 没明显变好。",
        "- adjacent3 的中等过滤看起来有帮助：holdout ROI 从 `"
        + pct(adj3["holdout"]["roi"] if adj3 else None)
        + "` 到 `"
        + pct(adj3_medium["holdout"]["roi"] if adj3_medium else None)
        + "`，但样本只剩 `"
        + str(adj3_medium["holdout"]["rows"] if adj3_medium else "NA")
        + "` 行，不能当定论。",
        "- 所以目前最像真的东西是：Range RV 的核心机会可能在“买模型 mode 附近的相邻温度区间”，forecast quality 更适合做软分层，不适合做硬开关。",
        "",
        "## ROI 对比",
        "",
        "ROI 是用 decision-time `market_yes_price` 做的相邻区间 counterfactual：买入区间内所有 YES，若最终温度落入区间则 payout=1，否则 payout=0。不是 live PnL。",
        "",
        table(
            best_rows,
            [
                "width",
                "cost_cap",
                "filter",
                "train_rows",
                "train_roi",
                "train_ci",
                "holdout_rows",
                "holdout_roi",
                "holdout_ci",
                "holdout_delta",
                "holdout_pnl",
            ],
        ),
        "",
        "## Filters",
        "",
        "- `no_filter`: 不用 forecast quality 过滤。",
        "- `loose_drop_worst_tail`: 只去掉模型自己也认为尾部风险最高的 25%。",
        "- `medium_quality`: 要求模型尾部风险低于中位数，且 adjacent3 模型质量高于中位数。",
        "- `strict_low_uncertainty`: 低 entropy、高 mode probability、高 adjacent3 mass、历史 miss 不高，最接近上一版 strict regime。",
        "",
        "## 结论等级",
        "",
        "`proxy_significance=mixed_positive`, `executable=NA`, `baseline=not_full_gate`, `forward=proxy_only`, `conclusion=inconclusive` for live action. No live action.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", default=str(DB_PATH))
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--out-md", default=str(OUT_MD))
    args = ap.parse_args()

    db_path = Path(args.db_path)
    conn = sqlite3.connect(db_path)
    try:
        self_check = {
            "fact_signal_candidates_rows": int(run_sql_scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates")),
            "fact_trades_rows": int(run_sql_scalar(conn, "SELECT COUNT(*) FROM fact_trades")),
            "candidate_coverage": run_sql_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "trade_class_distribution": run_sql_rows(
                conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"
            ),
        }
        candidates = load_candidates(conn)
    finally:
        conn.close()

    decision_sets = add_historical_features(build_decision_sets(candidates))
    train_sets, holdout_sets, split = split_by_date(decision_sets)
    decision_sets, thresholds = derive_filters(train_sets, decision_sets)
    train_dates = set(train_sets["event_date"].astype(str))
    holdout_dates = set(holdout_sets["event_date"].astype(str))
    range_rows = build_range_rows(decision_sets)
    results = evaluate(range_rows, train_dates, holdout_dates)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "self_check": self_check,
        "split": split,
        "funnel": {
            "input_candidate_rows": int(len(candidates)),
            "decision_sets": int(len(decision_sets)),
            "range_rows": int(len(range_rows)),
        },
        "thresholds_train_derived": thresholds,
        "results": results,
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), **payload["funnel"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
