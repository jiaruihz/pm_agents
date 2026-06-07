"""Probability post-calibration for weather strategy.

Compares raw model_p_yes against multiple calibration methods using:
  - market price baseline (does model beat market as probability estimator?)
  - isotonic regression
  - Platt scaling (logistic in logit-space)
  - convex ensemble: w*model + (1-w)*market (grid over w)

Two holdout regimes:
  - time_split: first 80% by date trains, last 20% holds out
  - leave_one_city_out: train on N-1 cities, evaluate on held-out city

Data source:
  runtime/weather.db
    signals.model_p_yes         model probability for YES
    signals.market_price        market YES price at signal time
    settlements.final_price     1.0 if YES bracket hit, else 0.0

Aggregation:
  One observation per (target_date, city, bracket) — picks the signal row with
  the smallest hours_to_settle (i.e. decision-time snapshot). This avoids
  treating many correlated within-day snapshots as independent.

Metric:
  Brier score, calibration curve (10 bins), reliability diagram quantiles.

Output:
  - prints summary table to stdout
  - writes JSON to docs/analysis/<YYYY-MM>/<YYYY-MM-DD>-probability-calibration.json
  - writes markdown to docs/analysis/<YYYY-MM>/<YYYY-MM-DD>-probability-calibration.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB_DEFAULT = ROOT / "runtime" / "weather.db"


def _load_observations(db_path: Path) -> pd.DataFrame:
    """Return one row per (target_date, city, bracket) — decision-time snapshot."""
    conn = sqlite3.connect(str(db_path))
    sql = """
        SELECT s.target_date, s.city, s.bracket, s.condition_id, s.signal_side,
               s.model_p_yes, s.market_price, s.hours_to_settle,
               s.forecast_source, s.snapshot_ts_utc,
               x.final_price
          FROM signals s
          JOIN settlements x
            ON x.target_date  = s.target_date
           AND x.condition_id = s.condition_id
           AND x.bracket      = s.bracket
         WHERE x.settlement_status = 'settled'
           AND x.final_price IS NOT NULL
           AND s.model_p_yes BETWEEN 0.0 AND 1.0
           AND s.market_price BETWEEN 0.0 AND 1.0
    """
    df = pd.read_sql_query(sql, conn)
    conn.close()
    df["final_price"] = df["final_price"].astype(float)
    df["y_yes"] = (df["final_price"] >= 0.5).astype(int)
    df = (
        df.sort_values(["target_date", "city", "bracket", "hours_to_settle"])
        .groupby(["target_date", "city", "bracket"], as_index=False)
        .first()
    )
    return df.reset_index(drop=True)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def reliability_bins(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, n_bins + 1)
    out = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi if i < n_bins - 1 else p <= hi)
        n = int(mask.sum())
        out.append({
            "bin": f"[{lo:.2f},{hi:.2f}{')' if i < n_bins - 1 else ']'}",
            "n": n,
            "mean_pred": float(p[mask].mean()) if n > 0 else None,
            "mean_actual": float(y[mask].mean()) if n > 0 else None,
        })
    return out


def fit_isotonic(p_train: np.ndarray, y_train: np.ndarray):
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(p_train, y_train)
    return iso


def fit_platt(p_train: np.ndarray, y_train: np.ndarray):
    from sklearn.linear_model import LogisticRegression
    eps = 1e-6
    z = np.clip(p_train, eps, 1 - eps)
    logit = np.log(z / (1 - z)).reshape(-1, 1)
    lr = LogisticRegression()
    lr.fit(logit, y_train)
    return lr


def predict_platt(lr, p: np.ndarray) -> np.ndarray:
    eps = 1e-6
    z = np.clip(p, eps, 1 - eps)
    logit = np.log(z / (1 - z)).reshape(-1, 1)
    return lr.predict_proba(logit)[:, 1]


def best_ensemble_weight(p_model: np.ndarray, p_market: np.ndarray, y: np.ndarray, grid: Iterable[float]) -> tuple[float, float]:
    best_w, best_b = None, math.inf
    for w in grid:
        p_ens = w * p_model + (1 - w) * p_market
        b = brier(p_ens, y)
        if b < best_b:
            best_w, best_b = float(w), b
    return best_w, best_b


def evaluate_split(train_df: pd.DataFrame, test_df: pd.DataFrame, fixed_ensemble_w: float | None = 0.3) -> dict:
    p_model_tr, p_market_tr, y_tr = (
        train_df["model_p_yes"].to_numpy(),
        train_df["market_price"].to_numpy(),
        train_df["y_yes"].to_numpy(),
    )
    p_model_te, p_market_te, y_te = (
        test_df["model_p_yes"].to_numpy(),
        test_df["market_price"].to_numpy(),
        test_df["y_yes"].to_numpy(),
    )

    iso = fit_isotonic(p_model_tr, y_tr)
    p_iso_te = iso.predict(p_model_te)

    pl = fit_platt(p_model_tr, y_tr)
    p_platt_te = predict_platt(pl, p_model_te)

    grid = np.linspace(0.0, 1.0, 21)
    best_w, _ = best_ensemble_weight(p_model_tr, p_market_tr, y_tr, grid)
    p_ens_best_te = best_w * p_model_te + (1 - best_w) * p_market_te

    fixed_w = float(fixed_ensemble_w) if fixed_ensemble_w is not None else None
    p_ens_fixed_te = None
    if fixed_w is not None:
        p_ens_fixed_te = fixed_w * p_model_te + (1 - fixed_w) * p_market_te

    iso_ens = fit_isotonic(0.5 * p_model_tr + 0.5 * p_market_tr, y_tr)
    p_iso_ens_te = iso_ens.predict(0.5 * p_model_te + 0.5 * p_market_te)

    methods = {
        "raw_model": p_model_te,
        "market_baseline": p_market_te,
        "isotonic_model": p_iso_te,
        "platt_model": p_platt_te,
        "ensemble_best_w": p_ens_best_te,
        "isotonic_on_50_50": p_iso_ens_te,
    }
    if p_ens_fixed_te is not None:
        methods["ensemble_fixed_w"] = p_ens_fixed_te

    result = {
        "n_train": int(len(train_df)),
        "n_test": int(len(test_df)),
        "best_ensemble_weight_on_train": float(best_w),
        "fixed_ensemble_weight": fixed_w,
        "brier": {k: brier(v, y_te) for k, v in methods.items()},
    }
    raw_brier = result["brier"]["raw_model"]
    mkt_brier = result["brier"]["market_baseline"]
    result["delta_vs_market"] = {k: float(v - mkt_brier) for k, v in result["brier"].items()}
    result["delta_vs_raw_model"] = {k: float(v - raw_brier) for k, v in result["brier"].items()}
    return result


def time_split_evaluation(df: pd.DataFrame, train_frac: float = 0.8) -> dict:
    df_sorted = df.sort_values("target_date").reset_index(drop=True)
    cutoff_idx = int(len(df_sorted) * train_frac)
    cutoff_date = df_sorted.iloc[cutoff_idx]["target_date"]
    train = df_sorted.iloc[:cutoff_idx]
    test = df_sorted.iloc[cutoff_idx:]
    res = evaluate_split(train, test)
    res["regime"] = "time_split"
    res["train_frac"] = train_frac
    res["train_date_range"] = [train["target_date"].min(), train["target_date"].max()]
    res["test_date_range"] = [test["target_date"].min(), test["target_date"].max()]
    res["cutoff_date"] = cutoff_date
    res["reliability_bins"] = {
        "raw_model": reliability_bins(test["model_p_yes"].to_numpy(), test["y_yes"].to_numpy()),
        "market_baseline": reliability_bins(test["market_price"].to_numpy(), test["y_yes"].to_numpy()),
    }
    return res


def leave_one_city_out_evaluation(df: pd.DataFrame, min_test_n: int = 30) -> dict:
    per_city = []
    cities = sorted(df["city"].unique())
    for city in cities:
        test = df[df["city"] == city]
        train = df[df["city"] != city]
        if len(test) < min_test_n or len(train) < min_test_n:
            continue
        res = evaluate_split(train, test)
        res["held_out_city"] = city
        res["n_test"] = int(len(test))
        per_city.append(res)
    avg_brier: dict[str, float] = {}
    methods = list(per_city[0]["brier"].keys()) if per_city else []
    for m in methods:
        avg_brier[m] = float(np.mean([r["brier"][m] for r in per_city]))
    raw = avg_brier.get("raw_model", float("nan"))
    mkt = avg_brier.get("market_baseline", float("nan"))
    return {
        "regime": "leave_one_city_out",
        "cities_evaluated": len(per_city),
        "min_test_n": min_test_n,
        "avg_brier_across_cities": avg_brier,
        "avg_delta_vs_market": {k: float(v - mkt) for k, v in avg_brier.items()},
        "avg_delta_vs_raw_model": {k: float(v - raw) for k, v in avg_brier.items()},
        "per_city": per_city,
    }


def _format_table(rows: list[tuple]) -> str:
    if not rows:
        return ""
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    out = []
    for r in rows:
        out.append("  ".join(str(r[i]).ljust(widths[i]) for i in range(len(r))))
    return "\n".join(out)


def write_markdown(out_path: Path, ts: pd.DataFrame, ts_res: dict, loo_res: dict, df: pd.DataFrame) -> None:
    today = dt.date.today().isoformat()
    lines = [
        f"# Weather Probability Calibration — {today}",
        "",
        "> 自动生成，由 `scripts/analysis/calibrate_weather_probability.py` 产出。",
        "> 数据源：`runtime/weather.db` 的 `signals` join `settlements`（settled only），按",
        "> `(target_date, city, bracket)` 去重，取 `hours_to_settle` 最小的 snapshot（最接近决策时）。",
        "",
        "## 0. 样本",
        "",
        f"- 总样本：{len(df)} 行",
        f"- 城市数：{df['city'].nunique()}",
        f"- 日期范围：{df['target_date'].min()} → {df['target_date'].max()}",
        f"- 实际 YES 命中率：{df['y_yes'].mean():.4f}",
        f"- raw model 平均 p_yes：{df['model_p_yes'].mean():.4f}",
        f"- market price 平均 p_yes：{df['market_price'].mean():.4f}",
        "",
        "## 1. Time-split 评估（按日期 80/20）",
        "",
        f"- train: {ts_res['train_date_range'][0]} → {ts_res['train_date_range'][1]}（n={ts_res['n_train']}）",
        f"- test:  {ts_res['test_date_range'][0]} → {ts_res['test_date_range'][1]}（n={ts_res['n_test']}）",
        f"- best ensemble weight (on train): w_model = {ts_res['best_ensemble_weight_on_train']:.2f}",
        "",
        "| 方法 | Brier | Δ vs market | Δ vs raw_model |",
        "|---|---:|---:|---:|",
    ]
    for k, b in ts_res["brier"].items():
        lines.append(
            f"| `{k}` | {b:.5f} | {ts_res['delta_vs_market'][k]:+.5f} | {ts_res['delta_vs_raw_model'][k]:+.5f} |"
        )
    lines += [
        "",
        "### 1.1 Reliability bins（holdout）",
        "",
        "Raw model：",
        "",
        "| bin | n | mean_pred | mean_actual |",
        "|---|---:|---:|---:|",
    ]
    for r in ts_res["reliability_bins"]["raw_model"]:
        mp = "—" if r["mean_pred"] is None else f"{r['mean_pred']:.4f}"
        ma = "—" if r["mean_actual"] is None else f"{r['mean_actual']:.4f}"
        lines.append(f"| {r['bin']} | {r['n']} | {mp} | {ma} |")
    lines += [
        "",
        "Market baseline：",
        "",
        "| bin | n | mean_pred | mean_actual |",
        "|---|---:|---:|---:|",
    ]
    for r in ts_res["reliability_bins"]["market_baseline"]:
        mp = "—" if r["mean_pred"] is None else f"{r['mean_pred']:.4f}"
        ma = "—" if r["mean_actual"] is None else f"{r['mean_actual']:.4f}"
        lines.append(f"| {r['bin']} | {r['n']} | {mp} | {ma} |")
    lines += [
        "",
        "## 2. Leave-one-city-out 评估",
        "",
        f"- 评估城市数：{loo_res['cities_evaluated']}",
        f"- 单城最少 holdout 样本：{loo_res['min_test_n']}",
        "",
        "| 方法 | Avg Brier across cities | Δ vs market | Δ vs raw_model |",
        "|---|---:|---:|---:|",
    ]
    for k, b in loo_res["avg_brier_across_cities"].items():
        lines.append(
            f"| `{k}` | {b:.5f} | {loo_res['avg_delta_vs_market'][k]:+.5f} | {loo_res['avg_delta_vs_raw_model'][k]:+.5f} |"
        )

    lines += [
        "",
        "### 2.1 per-city（top10 by raw vs market delta，正=raw 更差）",
        "",
    ]
    pc = loo_res["per_city"]
    if pc:
        pc_sorted = sorted(pc, key=lambda r: r["delta_vs_market"]["raw_model"], reverse=True)
        lines += ["| 城市 | n_test | raw Brier | market Brier | raw-market |", "|---|---:|---:|---:|---:|"]
        for r in pc_sorted[:10]:
            lines.append(
                f"| {r['held_out_city']} | {r['n_test']} | {r['brier']['raw_model']:.4f} | "
                f"{r['brier']['market_baseline']:.4f} | {r['delta_vs_market']['raw_model']:+.4f} |"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="Weather probability post-calibration")
    p.add_argument("--db-path", default=str(DB_DEFAULT))
    p.add_argument("--train-frac", type=float, default=0.8)
    p.add_argument("--loo-min-test-n", type=int, default=30)
    p.add_argument("--out-prefix", default=None, help="Override docs/analysis/YYYY-MM/YYYY-MM-DD-probability-calibration")
    args = p.parse_args()

    db_path = Path(args.db_path)
    df = _load_observations(db_path)
    print(f"loaded {len(df)} (target_date,city,bracket) observations")
    print(
        f"  yes rate={df['y_yes'].mean():.4f}, "
        f"mean model_p_yes={df['model_p_yes'].mean():.4f}, "
        f"mean market_price={df['market_price'].mean():.4f}"
    )

    print("\n=== time_split ===")
    ts_res = time_split_evaluation(df, train_frac=args.train_frac)
    for k, b in ts_res["brier"].items():
        print(f"  {k:<32} brier={b:.5f}  vs_market={ts_res['delta_vs_market'][k]:+.5f}  vs_raw={ts_res['delta_vs_raw_model'][k]:+.5f}")

    print("\n=== leave_one_city_out ===")
    loo_res = leave_one_city_out_evaluation(df, min_test_n=args.loo_min_test_n)
    for k, b in loo_res["avg_brier_across_cities"].items():
        print(f"  {k:<32} avg_brier={b:.5f}  vs_market={loo_res['avg_delta_vs_market'][k]:+.5f}")

    today = dt.date.today()
    prefix = args.out_prefix or f"docs/analysis/{today.strftime('%Y-%m')}/{today.isoformat()}-probability-calibration"
    json_path = ROOT / f"{prefix}.json"
    md_path = ROOT / f"{prefix}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps({"sample": {
            "n": int(len(df)),
            "cities": int(df["city"].nunique()),
            "date_min": df["target_date"].min(),
            "date_max": df["target_date"].max(),
            "yes_rate": float(df["y_yes"].mean()),
            "mean_model_p_yes": float(df["model_p_yes"].mean()),
            "mean_market_price": float(df["market_price"].mean()),
        }, "time_split": ts_res, "leave_one_city_out": loo_res}, indent=2, default=float),
        encoding="utf-8",
    )
    write_markdown(md_path, df, ts_res, loo_res, df)
    print(f"\nJSON  -> {json_path}")
    print(f"MD    -> {md_path}")


if __name__ == "__main__":
    main()
