"""
recalibrate_blend.py

Weekly probability-blend recalibration for the Weather Edge Engine.

Reads settled signal candidates from runtime/weather.db, refits the convex
blend  p_blend = alpha * model_p_yes_raw + (1 - alpha) * market_yes_price
against bracket-level settlement labels, and reports drift vs the currently
deployed global alpha (0.30 by default).

This is intentionally lighter than scripts/analysis/model_vs_market/calibrate_weather_probability.py:
  - one observation per (target_date, city, bracket), latest decision snapshot
  - alpha grid search + sklearn IsotonicRegression + LogisticRegression (Platt)
  - simple holdout: last 7 days held out (time split)
  - emits a JSON drift report; never writes config

Drift flags follow the plan §5 / §9:
  - |best_alpha - deployed_alpha| > 0.10               → "alpha_drift"
  - Brier(best_alpha) < Brier(deployed_alpha) - 0.005  → "brier_improvement_significant"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime" / "weather.db"
DEFAULT_OUT = ROOT / "docs" / "analysis"


@dataclass
class DriftReport:
    generated_at_utc: str
    n_obs_train: int
    n_obs_holdout: int
    train_window: tuple[str, str]
    holdout_window: tuple[str, str]
    deployed_alpha: float
    brier_raw: float
    brier_market: float
    brier_deployed: float
    best_alpha_grid: float
    brier_best_grid: float
    brier_isotonic: float
    brier_platt: float
    drift_flags: list[str]
    alpha_grid: list[tuple[float, float]]


def _load_observations(db_path: Path) -> pd.DataFrame:
    """One row per (target_date, city, bracket) — latest decision snapshot.

    fact_signal_candidates already stores best-decision snapshot per
    (city, target_date, bracket, side). We dedup further to (city, target_date,
    bracket) since YES/NO rows share model_p_yes and market_yes_price.
    """
    sql = """
        SELECT city, event_date, bracket,
               model_p_yes, market_yes_price, final_yes,
               decision_snapshot_ts_utc
        FROM fact_signal_candidates
        WHERE settlement_status = 'settled'
          AND decision_window_missing = 0
          AND model_p_yes IS NOT NULL
          AND market_yes_price IS NOT NULL
          AND final_yes IS NOT NULL
    """
    with sqlite3.connect(str(db_path)) as c:
        df = pd.read_sql_query(sql, c)
    df = df.drop_duplicates(subset=["city", "event_date", "bracket"], keep="last")
    df["model_p_yes"] = df["model_p_yes"].astype(float).clip(1e-6, 1 - 1e-6)
    df["market_yes_price"] = df["market_yes_price"].astype(float).clip(1e-6, 1 - 1e-6)
    df["final_yes"] = df["final_yes"].astype(float)
    return df.reset_index(drop=True)


def _brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def _alpha_grid(raw: np.ndarray, mkt: np.ndarray, y: np.ndarray,
                step: float = 0.05) -> list[tuple[float, float]]:
    out = []
    for a in np.arange(0.0, 1.0 + 1e-9, step):
        a = round(a, 4)
        p = a * raw + (1 - a) * mkt
        out.append((a, _brier(p, y)))
    return out


def _fit_isotonic(raw_train, y_train, raw_holdout) -> np.ndarray:
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(raw_train, y_train)
    return iso.predict(raw_holdout)


def _fit_platt(raw_train, y_train, raw_holdout) -> np.ndarray:
    # Operate in logit-space for Platt.
    eps = 1e-6
    z = np.log(np.clip(raw_train, eps, 1 - eps) /
               np.clip(1 - raw_train, eps, 1 - eps)).reshape(-1, 1)
    lr = LogisticRegression()
    lr.fit(z, y_train)
    z_h = np.log(np.clip(raw_holdout, eps, 1 - eps) /
                 np.clip(1 - raw_holdout, eps, 1 - eps)).reshape(-1, 1)
    return lr.predict_proba(z_h)[:, 1]


def _time_split(df: pd.DataFrame, holdout_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("event_date").reset_index(drop=True)
    dates = sorted(df["event_date"].unique())
    if len(dates) <= holdout_days:
        return df.iloc[:0], df
    cut = dates[-holdout_days]
    train = df[df["event_date"] < cut].reset_index(drop=True)
    holdout = df[df["event_date"] >= cut].reset_index(drop=True)
    return train, holdout


def recalibrate(db_path: Path, deployed_alpha: float, holdout_days: int) -> DriftReport:
    df = _load_observations(db_path)
    if df.empty:
        raise SystemExit("No settled observations found.")

    train, holdout = _time_split(df, holdout_days=holdout_days)
    if train.empty or holdout.empty:
        raise SystemExit(
            f"Time split produced empty train ({len(train)}) or holdout ({len(holdout)}). "
            "Reduce --holdout-days or wait for more settlements."
        )

    raw_tr, mkt_tr, y_tr = (train["model_p_yes"].values,
                            train["market_yes_price"].values,
                            train["final_yes"].values)
    raw_ho, mkt_ho, y_ho = (holdout["model_p_yes"].values,
                            holdout["market_yes_price"].values,
                            holdout["final_yes"].values)

    grid = _alpha_grid(raw_ho, mkt_ho, y_ho)
    best_alpha, brier_best = min(grid, key=lambda kv: kv[1])

    brier_raw = _brier(raw_ho, y_ho)
    brier_market = _brier(mkt_ho, y_ho)
    brier_deployed = _brier(deployed_alpha * raw_ho + (1 - deployed_alpha) * mkt_ho, y_ho)

    # sklearn methods are fit on train, scored on holdout.
    p_iso = _fit_isotonic(raw_tr, y_tr, raw_ho)
    brier_iso = _brier(p_iso, y_ho)

    p_platt = _fit_platt(raw_tr, y_tr, raw_ho)
    brier_platt = _brier(p_platt, y_ho)

    flags: list[str] = []
    if abs(best_alpha - deployed_alpha) > 0.10:
        flags.append("alpha_drift")
    if brier_best < brier_deployed - 0.005:
        flags.append("brier_improvement_significant")

    return DriftReport(
        generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        n_obs_train=int(len(train)),
        n_obs_holdout=int(len(holdout)),
        train_window=(str(train["event_date"].min()), str(train["event_date"].max())),
        holdout_window=(str(holdout["event_date"].min()), str(holdout["event_date"].max())),
        deployed_alpha=deployed_alpha,
        brier_raw=brier_raw,
        brier_market=brier_market,
        brier_deployed=brier_deployed,
        best_alpha_grid=float(best_alpha),
        brier_best_grid=float(brier_best),
        brier_isotonic=brier_iso,
        brier_platt=brier_platt,
        drift_flags=flags,
        alpha_grid=[(float(a), float(b)) for a, b in grid],
    )


def _print_summary(r: DriftReport) -> None:
    print("=" * 60)
    print(f"recalibrate_blend.py — {r.generated_at_utc}")
    print(f"train: {r.n_obs_train} obs  window {r.train_window}")
    print(f"holdout: {r.n_obs_holdout} obs  window {r.holdout_window}")
    print()
    print(f"deployed alpha = {r.deployed_alpha:.2f}")
    print(f"  Brier(raw)        = {r.brier_raw:.5f}")
    print(f"  Brier(market)     = {r.brier_market:.5f}")
    print(f"  Brier(deployed)   = {r.brier_deployed:.5f}")
    print(f"  Brier(best alpha={r.best_alpha_grid:.2f}) = {r.brier_best_grid:.5f}")
    print(f"  Brier(isotonic)   = {r.brier_isotonic:.5f}")
    print(f"  Brier(Platt)      = {r.brier_platt:.5f}")
    print()
    print("alpha grid:")
    for a, b in r.alpha_grid:
        marker = "  <- best" if abs(a - r.best_alpha_grid) < 1e-9 else ""
        deployed_marker = "  <- deployed" if abs(a - r.deployed_alpha) < 1e-9 else ""
        print(f"  alpha={a:.2f}  Brier={b:.5f}{marker}{deployed_marker}")
    print()
    if r.drift_flags:
        print(f"DRIFT FLAGS: {', '.join(r.drift_flags)}")
    else:
        print("No drift flags.")
    print("=" * 60)


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly probability-blend recalibration.")
    ap.add_argument("--db", type=Path, default=DB_DEFAULT, help="Path to weather.db")
    ap.add_argument("--deployed-alpha", type=float, default=0.30,
                    help="Currently deployed global alpha (default: 0.30).")
    ap.add_argument("--holdout-days", type=int, default=7,
                    help="Number of most-recent days to hold out (default: 7).")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                    help="Directory for JSON report (placed under YYYY-MM/).")
    args = ap.parse_args()

    report = recalibrate(args.db, args.deployed_alpha, args.holdout_days)
    _print_summary(report)

    today = dt.date.today()
    out_dir = args.out_dir / today.strftime("%Y-%m")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today.isoformat()}-recalibrate-blend.json"
    out_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()
