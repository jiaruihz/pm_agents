#!/usr/bin/env python3
"""City/model/data-depth slices for forecast-quality Range RV overlay."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
MARKET_STRUCTURE_DIR = ROOT / "scripts" / "analysis" / "market_structure_edge"
sys.path.append(str(SCRIPT_DIR))
sys.path.append(str(MARKET_STRUCTURE_DIR))

import research_forecast_quality_range_rv_overlay as overlay  # noqa: E402
import research_range_rv_scanner as scanner  # noqa: E402
import research_range_rv_variant_lab_v03 as variants  # noqa: E402


DB_PATH = ROOT / "runtime/weather.db"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-10-forecast-quality-range-rv-city-model.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-10-forecast-quality-range-rv-city-model.md"
TARGET_METRIC = "forecast_quality_range_rv_city_model_data_depth_value"


def pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x * 100:+.1f}%"


def money(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "NA"
    return f"{x:+.2f}"


def table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    base = overlay.summarize_slice(df)
    base["avg_hist_n"] = float(df["hist_n"].mean()) if not df.empty and "hist_n" in df else None
    return base


def split_summary(
    rows: pd.DataFrame,
    train_dates: set[str],
    holdout_dates: set[str],
    group_col: str,
    min_total_rows: int = 1,
) -> list[dict[str, Any]]:
    out = []
    for group, g in rows.groupby(group_col, dropna=False):
        if len(g) < min_total_rows:
            continue
        train = g[g["event_date"].astype(str).isin(train_dates)]
        holdout = g[g["event_date"].astype(str).isin(holdout_dates)]
        tr = summarize(train)
        ho = summarize(holdout)
        out.append(
            {
                group_col: str(group),
                "total_rows": int(len(g)),
                "train_rows": tr["rows"],
                "train_dates": tr["event_dates"],
                "train_roi": tr["roi"],
                "train_pnl": tr["pnl"],
                "holdout_rows": ho["rows"],
                "holdout_dates": ho["event_dates"],
                "holdout_roi": ho["roi"],
                "holdout_pnl": ho["pnl"],
                "avg_hist_n": ho["avg_hist_n"],
            }
        )
    return sorted(out, key=lambda x: (x["holdout_pnl"], x["holdout_rows"]), reverse=True)


def add_data_depth_bucket(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    out["data_depth_bucket"] = pd.cut(
        out["hist_n"].fillna(0),
        bins=[-1, 0, 2, 5, 10, 10_000],
        labels=["no_prior", "prior_1_2", "prior_3_5", "prior_6_10", "prior_11_plus"],
    ).astype(str)
    return out


def choose_train_city_model_probe(candidate: pd.DataFrame, train_dates: set[str], holdout_dates: set[str]) -> dict[str, Any]:
    train_rows = candidate[candidate["event_date"].astype(str).isin(train_dates)]
    selected_keys: set[tuple[str, str]] = set()
    diagnostics = []
    for (city, model), g in train_rows.groupby(["city", "model_version"], dropna=False):
        s = summarize(g)
        keep = s["rows"] >= 3 and s["roi"] is not None and s["roi"] > 0
        diagnostics.append(
            {
                "city": str(city),
                "model_version": str(model),
                "train_rows": s["rows"],
                "train_roi": s["roi"],
                "train_pnl": s["pnl"],
                "selected": bool(keep),
            }
        )
        if keep:
            selected_keys.add((str(city), str(model)))
    selected = candidate[
        candidate.apply(lambda r: (str(r["city"]), str(r["model_version"])) in selected_keys, axis=1)
    ]
    train = selected[selected["event_date"].astype(str).isin(train_dates)]
    holdout = selected[selected["event_date"].astype(str).isin(holdout_dates)]
    return {
        "selection_rule": "select city+model pairs on train only when train_rows>=3 and train_roi>0",
        "selected_pairs": sorted([{"city": c, "model_version": m} for c, m in selected_keys], key=lambda x: (x["city"], x["model_version"])),
        "diagnostics": sorted(diagnostics, key=lambda x: (x["selected"], x["train_pnl"]), reverse=True),
        "train": summarize(train),
        "holdout": summarize(holdout),
    }


def to_variant_rows(rows: pd.DataFrame, algorithm: str) -> list[dict[str, Any]]:
    out = []
    for _, row in rows.iterrows():
        legs = []
        for leg in json.loads(row["legs_json"]):
            legs.append(
                {
                    "condition_id": leg["condition_id"],
                    "market_id": leg["market_id"],
                    "bracket": leg["bracket"],
                    "side": "BUY_YES",
                    "market_yes_price": leg["market"],
                    "final_yes": leg["final_yes"],
                }
            )
        out.append(
            {
                "candidate_id": f"{algorithm}|{row['decision_set_id']}|{row['range_brackets']}",
                "algorithm": algorithm,
                "city": row["city"],
                "city_pool": row.get("city_pool"),
                "event_date": str(row["event_date"]),
                "target_date": str(row["event_date"]),
                "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                "decision_dt": scanner.parse_ts(str(row["decision_snapshot_ts_utc"])),
                "n_legs": len(legs),
                "brackets": [leg["bracket"] for leg in legs],
                "sides": ["BUY_YES"] * len(legs),
                "condition_ids": [leg["condition_id"] for leg in legs],
                "taker_cost": float(row["cost"]),
                "taker_pnl": float(row["pnl"]),
                "legs": legs,
            }
        )
    return out


def executable_summary(rows: pd.DataFrame, train_dates: set[str], holdout_dates: set[str], orderbook_glob: str) -> dict[str, Any]:
    strategy_rows = to_variant_rows(rows, "forecast_quality_probe_adjacent3")
    coverage = variants.attach_orderbook(strategy_rows, orderbook_glob)
    matched = []
    for row in strategy_rows:
        if row.get("orderbook_taker_cost") is None or row.get("orderbook_taker_pnl") is None:
            continue
        matched.append(
            {
                "event_date": row["event_date"],
                "city": row["city"],
                "cost": float(row["orderbook_taker_cost"]),
                "pnl": float(row["orderbook_taker_pnl"]),
                "hit": int(float(row["orderbook_taker_pnl"]) > 0),
            }
        )
    df = pd.DataFrame(matched)
    if df.empty:
        return {"coverage": coverage, "train": summarize(df), "holdout": summarize(df)}
    train = df[df["event_date"].astype(str).isin(train_dates)]
    holdout = df[df["event_date"].astype(str).isin(holdout_dates)]
    return {"coverage": coverage, "train": summarize(train), "holdout": summarize(holdout)}


def fmt_slice(rows: list[dict[str, Any]], key: str, limit: int | None = None) -> list[dict[str, Any]]:
    shown = rows if limit is None else rows[:limit]
    out = []
    for r in shown:
        out.append(
            {
                key: r[key],
                "total": r["total_rows"],
                "train": r["train_rows"],
                "train_roi": pct(r["train_roi"]),
                "train_pnl": money(r["train_pnl"]),
                "holdout": r["holdout_rows"],
                "holdout_roi": pct(r["holdout_roi"]),
                "holdout_pnl": money(r["holdout_pnl"]),
                "avg_hist_n": "NA" if r["avg_hist_n"] is None else f"{r['avg_hist_n']:.1f}",
            }
        )
    return out


def render_md(payload: dict[str, Any]) -> str:
    cand = payload["candidate"]
    no_filter = payload["baseline_no_filter"]
    train_probe = payload["train_selected_probe"]
    exe_base = payload["executable_no_filter"]
    exe_cand = payload["executable_candidate"]
    model_rows = fmt_slice(payload["by_model"], "model_version")
    data_rows = fmt_slice(payload["by_data_depth"], "data_depth_bucket")
    city_rows = fmt_slice(payload["by_city"], "city", limit=30)
    pair_rows = [
        {"city": x["city"], "model": x["model_version"]}
        for x in train_probe["selected_pairs"]
    ]
    lines = [
        "# Forecast Quality Range RV City/Model/Data-Depth Study",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        f"> DB: `{payload['db_path']}`",
        "> Scope: local research only; no N100/live config changed; no live action.",
        "",
        "## 一句话",
        "",
        "- 现在最值得继续试的不是“严格 forecast quality 硬过滤”，而是 `adjacent3 + cost<=0.85 + medium_quality` 这个软过滤版本。",
        "- 城市/模型确实有关系，但目前每个 city+model 的样本都很薄，不能只按 holdout winner 选城市。",
        "- 数据积累有一点关系：有历史样本的 bucket 更容易解释，但当前样本太少，不能证明“样本越多越赚钱”。",
        "",
        "## 数据快照",
        "",
        f"- fact_signal_candidates rows: `{payload['self_check']['fact_signal_candidates_rows']}`",
        f"- fact_trades rows: `{payload['self_check']['fact_trades_rows']}`",
        f"- decision_sets: `{payload['funnel']['decision_sets']}`",
        f"- range rows: `{payload['funnel']['range_rows']}`",
        f"- candidate rows: `{cand['rows']}`",
        f"- train: `{payload['split']['train_start']}` -> `{payload['split']['train_end']}` ({payload['split']['train_dates']} dates)",
        f"- holdout: `{payload['split']['holdout_start']}` -> `{payload['split']['holdout_end']}` ({payload['split']['holdout_dates']} dates)",
        "",
        "## 候选版本 vs 不筛 baseline",
        "",
        "| version | train rows | train ROI | train PnL | holdout rows | holdout ROI | holdout PnL |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| no_filter adjacent3 cost<=0.85 | {no_filter['train']['rows']} | {pct(no_filter['train']['roi'])} | {money(no_filter['train']['pnl'])} | {no_filter['holdout']['rows']} | {pct(no_filter['holdout']['roi'])} | {money(no_filter['holdout']['pnl'])} |",
        f"| medium_quality adjacent3 cost<=0.85 | {cand['train']['rows']} | {pct(cand['train']['roi'])} | {money(cand['train']['pnl'])} | {cand['holdout']['rows']} | {pct(cand['holdout']['roi'])} | {money(cand['holdout']['pnl'])} |",
        f"| train-selected city+model probe | {train_probe['train']['rows']} | {pct(train_probe['train']['roi'])} | {money(train_probe['train']['pnl'])} | {train_probe['holdout']['rows']} | {pct(train_probe['holdout']['roi'])} | {money(train_probe['holdout']['pnl'])} |",
        "",
        "## Time-aligned orderbook 复核",
        "",
        "这个表更接近真实可成交性：每条腿用 `orderbook_snapshot_ts <= decision_snapshot_ts` 的历史盘口 best ask。",
        "",
        "| version | matched / rows | train rows | train ROI | train PnL | holdout rows | holdout ROI | holdout PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| no_filter adjacent3 cost<=0.85 | {exe_base['coverage'].get('fully_matched_strategy_rows')} / {exe_base['coverage'].get('strategy_rows')} | {exe_base['train']['rows']} | {pct(exe_base['train']['roi'])} | {money(exe_base['train']['pnl'])} | {exe_base['holdout']['rows']} | {pct(exe_base['holdout']['roi'])} | {money(exe_base['holdout']['pnl'])} |",
        f"| medium_quality adjacent3 cost<=0.85 | {exe_cand['coverage'].get('fully_matched_strategy_rows')} / {exe_cand['coverage'].get('strategy_rows')} | {exe_cand['train']['rows']} | {pct(exe_cand['train']['roi'])} | {money(exe_cand['train']['pnl'])} | {exe_cand['holdout']['rows']} | {pct(exe_cand['holdout']['roi'])} | {money(exe_cand['holdout']['pnl'])} |",
        "",
        "## 按模型",
        "",
        table(model_rows, ["model_version", "total", "train", "train_roi", "train_pnl", "holdout", "holdout_roi", "holdout_pnl", "avg_hist_n"]),
        "",
        "## 按数据积累",
        "",
        table(data_rows, ["data_depth_bucket", "total", "train", "train_roi", "train_pnl", "holdout", "holdout_roi", "holdout_pnl", "avg_hist_n"]),
        "",
        "## 按城市 top30",
        "",
        table(city_rows, ["city", "total", "train", "train_roi", "train_pnl", "holdout", "holdout_roi", "holdout_pnl", "avg_hist_n"]),
        "",
        "## train-only 选出来的 city+model",
        "",
        table(pair_rows, ["city", "model"]),
        "",
        "## 当前可试探版本",
        "",
        "- `range_rv_forecast_quality_probe_v0`: buy YES on model-mode adjacent3 range。",
        "- 条件：`range_width=3`, `market_yes_price_sum<=0.85`, `medium_quality=1`。",
        "- 不按城市 hard allowlist 直接砍；城市/model 只作为 size/risk tag 记录，因为 city+model 样本还太少。",
        "- orderbook holdout 目前仍是正的，但样本很小；所以合理动作是 shadow/paper 或极小额受控 probe，不是直接扩大 live。",
        "- 若要 live，必须小额、maker/限价、单 city-day notional 很小，并且必须单独走 deploy 流程。本报告本身不改 live。",
        "",
        "## 结论等级",
        "",
        "`proxy=positive`, `city_model_evidence=thin`, `executable=small_positive`, `conclusion=shadow_or_tiny_probe_candidate_only`. No live action.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", default=str(DB_PATH))
    ap.add_argument("--out-json", default=str(OUT_JSON))
    ap.add_argument("--out-md", default=str(OUT_MD))
    ap.add_argument("--orderbook-glob", default=str(scanner.ORDERBOOK_GLOB_DEFAULT))
    args = ap.parse_args()

    db_path = Path(args.db_path)
    conn = sqlite3.connect(db_path)
    try:
        self_check = {
            "fact_signal_candidates_rows": int(overlay.run_sql_scalar(conn, "SELECT COUNT(*) FROM fact_signal_candidates")),
            "fact_trades_rows": int(overlay.run_sql_scalar(conn, "SELECT COUNT(*) FROM fact_trades")),
        }
        candidates = overlay.load_candidates(conn)
    finally:
        conn.close()

    decision_sets = overlay.add_historical_features(overlay.build_decision_sets(candidates))
    train_sets, holdout_sets, split = overlay.split_by_date(decision_sets)
    decision_sets, thresholds = overlay.derive_filters(train_sets, decision_sets)
    train_dates = set(train_sets["event_date"].astype(str))
    holdout_dates = set(holdout_sets["event_date"].astype(str))
    range_rows = add_data_depth_bucket(overlay.build_range_rows(decision_sets))

    no_filter = range_rows[
        (range_rows["range_width"] == 3)
        & (range_rows["cost"] <= 0.85)
    ].copy()
    candidate = range_rows[
        (range_rows["range_width"] == 3)
        & (range_rows["cost"] <= 0.85)
        & (range_rows["filter_medium_quality"] == 1)
    ].copy()

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
        "baseline_no_filter": {
            "train": summarize(no_filter[no_filter["event_date"].astype(str).isin(train_dates)]),
            "holdout": summarize(no_filter[no_filter["event_date"].astype(str).isin(holdout_dates)]),
        },
        "executable_no_filter": executable_summary(no_filter, train_dates, holdout_dates, args.orderbook_glob),
        "executable_candidate": executable_summary(candidate, train_dates, holdout_dates, args.orderbook_glob),
        "candidate": {
            "definition": "adjacent3 cost<=0.85 medium_quality",
            "rows": int(len(candidate)),
            "train": summarize(candidate[candidate["event_date"].astype(str).isin(train_dates)]),
            "holdout": summarize(candidate[candidate["event_date"].astype(str).isin(holdout_dates)]),
        },
        "by_model": split_summary(candidate, train_dates, holdout_dates, "model_version"),
        "by_forecast_source": split_summary(candidate, train_dates, holdout_dates, "forecast_source"),
        "by_data_depth": split_summary(candidate, train_dates, holdout_dates, "data_depth_bucket"),
        "by_city": split_summary(candidate, train_dates, holdout_dates, "city"),
        "train_selected_probe": choose_train_city_model_probe(candidate, train_dates, holdout_dates),
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "candidate_rows": payload["candidate"]["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
