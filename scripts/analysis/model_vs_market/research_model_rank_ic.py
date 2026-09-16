"""Model rank-IC research for weather strategy signals.

Ring3 asks whether the model can rank opportunities even if its probabilities
are not well calibrated. The primary universe is `fact_signal_candidates`
because ranking should be checked on opportunities, not only filled trades.

This script is diagnostic only. It does not change live configuration.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(x):
        return None
    return x


def _safe_div(num: float, den: float) -> float | None:
    if den == 0 or math.isnan(den):
        return None
    return float(num / den)


def _spearman(x: pd.Series, y: pd.Series) -> float | None:
    frame = pd.DataFrame({"x": x, "y": y}).replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < 3 or frame["x"].nunique() < 2 or frame["y"].nunique() < 2:
        return None
    return float(frame["x"].rank(method="average").corr(frame["y"].rank(method="average")))


def _load_candidates(db_path: Path, start: str | None, end: str | None) -> tuple[pd.DataFrame, dict]:
    conn = sqlite3.connect(str(db_path))
    data_quality = {
        "max_fact_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates").fetchone()[0],
        "fact_signal_candidates_rows": conn.execute("SELECT COUNT(*) FROM fact_signal_candidates").fetchone()[0],
        "eligible_rows": conn.execute("SELECT COUNT(*) FROM fact_signal_candidates WHERE eligible=1").fetchone()[0],
        "decision_window_missing_rows": conn.execute(
            "SELECT COUNT(*) FROM fact_signal_candidates WHERE decision_window_missing=1"
        ).fetchone()[0],
    }
    where = [
        "eligible = 1",
        "final_yes IS NOT NULL",
        "decision_window_missing = 0",
        "decision_entry_price IS NOT NULL",
        "decision_entry_price > 0",
        "model_p_yes IS NOT NULL",
        "market_yes_price IS NOT NULL",
    ]
    params: list[str] = []
    if start:
        where.append("event_date >= ?")
        params.append(start)
    if end:
        where.append("event_date <= ?")
        params.append(end)
    sql = f"""
        SELECT
            candidate_id, condition_id, market_id, side, event_date, bracket,
            city, city_pool, forecast_source, model_version,
            model_p_yes, market_yes_price, edge, abs_edge,
            decision_entry_price, yes_spread, no_spread,
            paper_ordered, live_filled, final_yes, win_by_count,
            counterfactual_pnl, counterfactual_pnl_best,
            fact_built_at_utc
        FROM fact_signal_candidates
        WHERE {' AND '.join(where)}
    """
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    if df.empty:
        return df, data_quality

    side = df["side"].astype(str).str.upper()
    df["model_side_prob"] = np.where(side.isin(["BUY_NO", "NO"]), 1.0 - df["model_p_yes"], df["model_p_yes"])
    df["market_side_prob"] = np.where(side.isin(["BUY_NO", "NO"]), 1.0 - df["market_yes_price"], df["market_yes_price"])
    df["realized_side_win"] = np.where(side.isin(["BUY_NO", "NO"]), 1.0 - df["final_yes"], df["final_yes"])
    df["model_edge_at_decision"] = df["model_side_prob"] - df["decision_entry_price"]
    df["model_vs_market_delta"] = df["model_side_prob"] - df["market_side_prob"]
    df["realized_roi_at_decision"] = (df["realized_side_win"] - df["decision_entry_price"]) / df["decision_entry_price"]
    df["price_bin"] = pd.cut(
        df["decision_entry_price"],
        bins=[i / 20 for i in range(21)],
        include_lowest=True,
        right=False,
    ).astype(str)
    return df.reset_index(drop=True), data_quality


def _bootstrap_ic(
    df: pd.DataFrame,
    score_col: str,
    outcome_col: str,
    cluster_col: str,
    n_boot: int,
    seed: int,
) -> dict:
    observed = _spearman(df[score_col], df[outcome_col])
    clusters = sorted(df[cluster_col].dropna().astype(str).unique().tolist())
    if observed is None or not clusters:
        return {"mean": observed, "ci_low": None, "ci_high": None, "n_clusters": len(clusters)}
    rng = np.random.default_rng(seed)
    by_cluster = {c: df[df[cluster_col].astype(str) == c] for c in clusters}
    values: list[float] = []
    for _ in range(n_boot):
        draw = rng.choice(clusters, size=len(clusters), replace=True)
        sample = pd.concat([by_cluster[c] for c in draw], ignore_index=True)
        val = _spearman(sample[score_col], sample[outcome_col])
        if val is not None and not math.isnan(val):
            values.append(val)
    if not values:
        return {"mean": observed, "ci_low": None, "ci_high": None, "n_clusters": len(clusters)}
    lo, hi = np.percentile(np.array(values), [2.5, 97.5])
    return {"mean": observed, "ci_low": float(lo), "ci_high": float(hi), "n_clusters": len(clusters)}


def _top_vs_rest_delta(df: pd.DataFrame, score_col: str, threshold: float, n_boot: int, seed: int) -> dict:
    if df.empty:
        return {"top_n": 0, "rest_n": 0, "delta_roi": None, "ci_low": None, "ci_high": None}

    def roi(frame: pd.DataFrame) -> float | None:
        if frame.empty:
            return None
        # one unit notional per opportunity at decision side price
        pnl = ((frame["realized_side_win"] - frame["decision_entry_price"]) / frame["decision_entry_price"]).sum()
        return float(pnl / len(frame))

    top = df[df[score_col] >= threshold]
    rest = df[df[score_col] < threshold]
    top_roi = roi(top)
    rest_roi = roi(rest)
    delta = None if top_roi is None or rest_roi is None else top_roi - rest_roi

    dates = sorted(df["event_date"].dropna().astype(str).unique().tolist())
    if not dates or delta is None:
        return {
            "threshold": float(threshold),
            "top_n": int(len(top)),
            "rest_n": int(len(rest)),
            "top_roi": top_roi,
            "rest_roi": rest_roi,
            "delta_roi": delta,
            "ci_low": None,
            "ci_high": None,
        }
    by_date = {d: df[df["event_date"].astype(str) == d] for d in dates}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(n_boot):
        draw = rng.choice(dates, size=len(dates), replace=True)
        sample = pd.concat([by_date[d] for d in draw], ignore_index=True)
        sample_top = sample[sample[score_col] >= threshold]
        sample_rest = sample[sample[score_col] < threshold]
        t = roi(sample_top)
        r = roi(sample_rest)
        if t is not None and r is not None:
            values.append(t - r)
    if not values:
        ci_low = ci_high = None
    else:
        ci_low, ci_high = np.percentile(np.array(values), [2.5, 97.5])
        ci_low, ci_high = float(ci_low), float(ci_high)
    return {
        "threshold": float(threshold),
        "top_n": int(len(top)),
        "rest_n": int(len(rest)),
        "top_roi": top_roi,
        "rest_roi": rest_roi,
        "delta_roi": delta,
        "ci_low": ci_low,
        "ci_high": ci_high,
    }


def _quantile_table(df: pd.DataFrame, score_col: str, n_bins: int) -> list[dict]:
    if df.empty or df[score_col].nunique() < n_bins:
        return []
    work = df.copy()
    work["rank_bucket"] = pd.qcut(work[score_col], q=n_bins, labels=False, duplicates="drop")
    rows = []
    for bucket, g in work.groupby("rank_bucket", dropna=False):
        rows.append({
            "bucket": int(bucket),
            "n": int(len(g)),
            "score_min": float(g[score_col].min()),
            "score_max": float(g[score_col].max()),
            "win_rate": float(g["realized_side_win"].mean()),
            "mean_roi": float(g["realized_roi_at_decision"].mean()),
            "counterfactual_pnl_sum": float(g["counterfactual_pnl"].sum()) if g["counterfactual_pnl"].notna().any() else None,
        })
    return rows


def _group_ic(df: pd.DataFrame, group_col: str, score_col: str, min_n: int) -> list[dict]:
    rows = []
    for value, g in df.groupby(group_col, dropna=False):
        if len(g) < min_n:
            continue
        rows.append({
            group_col: str(value),
            "n": int(len(g)),
            "dates": int(g["event_date"].nunique()),
            "ic_win": _spearman(g[score_col], g["realized_side_win"]),
            "ic_roi": _spearman(g[score_col], g["realized_roi_at_decision"]),
            "mean_roi": float(g["realized_roi_at_decision"].mean()),
        })
    return sorted(rows, key=lambda r: (r["ic_roi"] is None, r["ic_roi"] if r["ic_roi"] is not None else -999), reverse=True)


def _forward_test(df: pd.DataFrame, score_col: str, train_frac: float, top_quantile: float, n_boot: int, seed: int) -> dict:
    dates = sorted(df["event_date"].dropna().astype(str).unique().tolist())
    if len(dates) < 3:
        return {"status": "insufficient_dates", "n_dates": len(dates)}
    cutoff_idx = max(1, min(len(dates) - 1, int(len(dates) * train_frac)))
    train_dates = set(dates[:cutoff_idx])
    test_dates = set(dates[cutoff_idx:])
    train = df[df["event_date"].astype(str).isin(train_dates)]
    test = df[df["event_date"].astype(str).isin(test_dates)]
    threshold = float(train[score_col].quantile(top_quantile))
    train_ic = _bootstrap_ic(train, score_col, "realized_roi_at_decision", "event_date", n_boot, seed)
    test_ic = _bootstrap_ic(test, score_col, "realized_roi_at_decision", "event_date", n_boot, seed + 1)
    top_delta = _top_vs_rest_delta(test, score_col, threshold, n_boot, seed + 2)
    forward_pass = bool(
        train_ic.get("mean") is not None
        and test_ic.get("mean") is not None
        and train_ic["mean"] > 0
        and test_ic["mean"] > 0
    )
    significance_pass = bool(test_ic.get("ci_low") is not None and test_ic["ci_low"] > 0)
    baseline_pass = bool(top_delta.get("ci_low") is not None and top_delta["ci_low"] > 0)
    verdict = "confirmed" if significance_pass and baseline_pass and forward_pass else (
        "shadow_candidate" if significance_pass and baseline_pass else "inconclusive"
    )
    return {
        "status": "ok",
        "train_date_range": [dates[0], dates[cutoff_idx - 1]],
        "test_date_range": [dates[cutoff_idx], dates[-1]],
        "score_col": score_col,
        "top_quantile": top_quantile,
        "threshold": threshold,
        "train_ic_roi": train_ic,
        "test_ic_roi": test_ic,
        "test_top_vs_rest": top_delta,
        "gates": {
            "significance": "PASS" if significance_pass else "FAIL",
            "baseline": "PASS" if baseline_pass else "FAIL",
            "forward": "PASS" if forward_pass else "FAIL",
            "verdict": verdict,
        },
    }


def _fmt_pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NA"
    return f"{value:+.1%}"


def _fmt_float(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NA"
    return f"{value:+.4f}"


def _write_md(path: Path, result: dict) -> None:
    fwd = result["forward_test"]
    gates = fwd.get("gates", {})
    lines = [
        "# Model Rank IC Research",
        "",
        f"> generated_at_utc: `{result['generated_at_utc']}`",
        f"> DB: `{result['db_path']}`",
        "> Scope: Ring3 signal ranking diagnostic using `fact_signal_candidates`; no live behavior changed.",
        "",
        "## What IC / Rank Means",
        "",
        "IC is the correlation between a signal score and later realized outcome. Rank means the model does not need to be a calibrated probability; it only needs to sort better opportunities above worse opportunities.",
        "",
        "Primary score: `model_edge_at_decision = model_side_prob - decision_entry_price`.",
        "",
        "## Data Quality",
        "",
        "| field | value |",
        "|---|---:|",
    ]
    for key, value in result["data_quality"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines += [
        f"| `usable_rows` | `{result['sample']['n']}` |",
        f"| `usable_dates` | `{result['sample']['dates']}` |",
        f"| `date_range` | `{result['sample']['date_range'][0]} → {result['sample']['date_range'][1]}` |",
        "",
        "## Gates",
        "",
        "| gate | status |",
        "|---|---|",
        f"| `significance` | `{gates.get('significance', 'NA')}` |",
        f"| `baseline` | `{gates.get('baseline', 'NA')}` |",
        f"| `forward` | `{gates.get('forward', 'NA')}` |",
        f"| `verdict` | `{gates.get('verdict', 'NA')}` |",
        "",
        "## Overall IC",
        "",
        "| score | outcome | IC | 95% CI |",
        "|---|---|---:|---:|",
    ]
    for row in result["overall_ic"]:
        lines.append(
            f"| `{row['score']}` | `{row['outcome']}` | {_fmt_float(row['ic']['mean'])} | "
            f"[{_fmt_float(row['ic']['ci_low'])}, {_fmt_float(row['ic']['ci_high'])}] |"
        )
    lines += [
        "",
        "## Forward Top-Rank Test",
        "",
        f"- Train: `{fwd.get('train_date_range')}`",
        f"- Test: `{fwd.get('test_date_range')}`",
        f"- Top threshold from train q={fwd.get('top_quantile')}: `{fwd.get('threshold')}`",
        f"- Test IC ROI: `{_fmt_float((fwd.get('test_ic_roi') or {}).get('mean'))}`, "
        f"CI `[{_fmt_float((fwd.get('test_ic_roi') or {}).get('ci_low'))}, {_fmt_float((fwd.get('test_ic_roi') or {}).get('ci_high'))}]`",
        f"- Test top-vs-rest ROI delta: `{_fmt_pct((fwd.get('test_top_vs_rest') or {}).get('delta_roi'))}`, "
        f"CI `[{_fmt_pct((fwd.get('test_top_vs_rest') or {}).get('ci_low'))}, {_fmt_pct((fwd.get('test_top_vs_rest') or {}).get('ci_high'))}]`",
        "",
        "## Rank Buckets",
        "",
        "| bucket | n | score range | win_rate | mean ROI | cf PnL |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["rank_buckets"]:
        lines.append(
            f"| {row['bucket']} | {row['n']} | {row['score_min']:.4f}..{row['score_max']:.4f} | "
            f"{_fmt_pct(row['win_rate'])} | {_fmt_pct(row['mean_roi'])} | {_fmt_float(row['counterfactual_pnl_sum'])} |"
        )
    lines += [
        "",
        "## Group IC Highlights",
        "",
        "### By side",
        "",
        "| side | n | dates | IC ROI | mean ROI |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in result["by_side"]:
        lines.append(
            f"| `{row['side']}` | {row['n']} | {row['dates']} | {_fmt_float(row['ic_roi'])} | {_fmt_pct(row['mean_roi'])} |"
        )
    lines += [
        "",
        "### By forecast source",
        "",
        "| forecast_source | n | dates | IC ROI | mean ROI |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in result["by_forecast_source"]:
        lines.append(
            f"| `{row['forecast_source']}` | {row['n']} | {row['dates']} | {_fmt_float(row['ic_roi'])} | {_fmt_pct(row['mean_roi'])} |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Ring3 can only justify rank/sizing research, not live hard gates by itself.",
        "- A failed or inconclusive IC test means model ranking has not been proven useful on this universe.",
        "- Any live action still requires the full three-gate performance workflow in `WEATHER_ANALYSIS_CONTRACT.md`.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DB_DEFAULT)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--score-col", default="model_edge_at_decision")
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--top-quantile", type=float, default=0.8)
    parser.add_argument("--min-group-n", type=int, default=30)
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--out-json", type=Path, default=ROOT / "runtime/weather_edge_v1/market_data/research/2026-06-09-model-rank-ic.json")
    parser.add_argument("--out-md", type=Path, default=ROOT / "docs/analysis/2026-06/2026-06-09-model-rank-ic.md")
    args = parser.parse_args()

    df, quality = _load_candidates(args.db_path, args.start, args.end)
    if df.empty:
        raise SystemExit("No usable fact_signal_candidates rows found.")
    if args.score_col not in df.columns:
        raise SystemExit(f"Unknown score column: {args.score_col}")

    overall_ic = []
    for score in ["model_edge_at_decision", "model_side_prob", "model_vs_market_delta", "abs_edge"]:
        if score not in df.columns:
            continue
        for outcome in ["realized_side_win", "realized_roi_at_decision", "counterfactual_pnl"]:
            overall_ic.append({
                "score": score,
                "outcome": outcome,
                "ic": _bootstrap_ic(df, score, outcome, "event_date", args.bootstrap, args.seed),
            })

    result = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "db_path": str(args.db_path),
        "params": {
            "start": args.start,
            "end": args.end,
            "score_col": args.score_col,
            "train_frac": args.train_frac,
            "top_quantile": args.top_quantile,
            "min_group_n": args.min_group_n,
            "bootstrap": args.bootstrap,
            "seed": args.seed,
        },
        "data_quality": quality,
        "sample": {
            "n": int(len(df)),
            "dates": int(df["event_date"].nunique()),
            "date_range": [str(df["event_date"].min()), str(df["event_date"].max())],
            "cities": int(df["city"].nunique()),
        },
        "overall_ic": overall_ic,
        "forward_test": _forward_test(df, args.score_col, args.train_frac, args.top_quantile, args.bootstrap, args.seed),
        "rank_buckets": _quantile_table(df, args.score_col, 5),
        "by_side": _group_ic(df, "side", args.score_col, args.min_group_n),
        "by_forecast_source": _group_ic(df, "forecast_source", args.score_col, args.min_group_n),
        "by_model_version": _group_ic(df, "model_version", args.score_col, args.min_group_n),
        "by_price_bin": _group_ic(df, "price_bin", args.score_col, args.min_group_n),
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_md(args.out_md, result)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
