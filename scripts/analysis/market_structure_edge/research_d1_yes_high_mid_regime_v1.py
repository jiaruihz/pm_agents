#!/usr/bin/env python3
"""Regime audit for the d1 YES high-mid strategy.

The historical denominator is the first city-day row whose d1 YES book mid is
at least 0.80 in the PIT intraday regime atlas.  Historical opportunity replay
and actual live fills remain separate throughout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_calendar import CITY_TIMEZONE  # noqa: E402


DEFAULT_INPUT = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1"
    / "intraday_weather_regime_state_rows.csv"
)
DEFAULT_OUTPUT = ROOT / "docs/analysis/2026-07/generated/d1_yes_high_mid_regime_v1"
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_RAW = ROOT / "runtime/weather_edge_v1/d1_yes_high_mid_live_v1/shadow_events.jsonl"
RUN_ID = "pm_agent_local_strategy_runtime_d1_yes_high_mid_live_v1_live"
FORWARD_START = "2026-06-21"
LIVE_INCIDENT_KEYS = {
    ("Singapore", "2026-07-19"),
    ("Beijing", "2026-07-19"),
    ("Busan", "2026-07-19"),
    ("Chongqing", "2026-07-19"),
}
REGIME_DIMENSIONS = [
    "day_regime",
    "intraday_state",
    "running_max_state",
    "solar_window",
    "peak_clock_state",
    "moisture_cloud_regime",
    "wind_regime",
    "city_family",
    "entry_ask_band",
    "market_mid_band",
]
CONTINUOUS_FEATURES = [
    "d1_yes_mid",
    "d1_yes_ask",
    "decision_hour_local",
    "forecast_gap_to_running_native",
    "forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "relative_humidity_pct",
    "wind_speed_kt",
]


def weather_fee(price: pd.Series | float) -> pd.Series | float:
    return 0.05 * price * (1.0 - price)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _safe_logloss(labels: pd.Series, probabilities: pd.Series) -> float:
    p = probabilities.clip(1e-6, 1 - 1e-6)
    y = labels.astype(float)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def _block_bootstrap_roi(
    rows: pd.DataFrame, *, draws: int = 4000, seed: int = 20260722
) -> tuple[float, float, float]:
    daily = rows.groupby("target_date")[["pnl", "cost"]].sum()
    if len(daily) < 3:
        return (float("nan"), float("nan"), float("nan"))
    dates = daily.index.to_numpy()
    rng = np.random.default_rng(seed)
    values = np.empty(draws, dtype=float)
    for idx in range(draws):
        sample = daily.loc[rng.choice(dates, size=len(dates), replace=True)].sum()
        values[idx] = float(sample["pnl"] / sample["cost"])
    lo, hi = np.quantile(values, [0.025, 0.975])
    lower_tail = (np.count_nonzero(values <= 0) + 1) / (draws + 1)
    upper_tail = (np.count_nonzero(values >= 0) + 1) / (draws + 1)
    return (float(lo), float(hi), min(1.0, 2.0 * min(lower_tail, upper_tail)))


def _bh_adjust(pairs: list[tuple[int, float]]) -> dict[int, float]:
    if not pairs:
        return {}
    ordered = sorted(pairs, key=lambda item: item[1])
    count = len(ordered)
    adjusted: dict[int, float] = {}
    running = 1.0
    for rank_from_end, (idx, value) in enumerate(reversed(ordered), start=1):
        rank = count - rank_from_end + 1
        running = min(running, value * count / rank)
        adjusted[idx] = min(1.0, running)
    return adjusted


def _period_summary(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return {"rows": 0, "dates": 0, "roi": None}
    return {
        "rows": int(len(rows)),
        "dates": int(rows["target_date"].nunique()),
        "wins": int(rows["win"].sum()),
        "win_rate": round(float(rows["win"].mean()), 6),
        "pnl": round(float(rows["pnl"].sum()), 6),
        "cost": round(float(rows["cost"].sum()), 6),
        "roi": round(float(rows["pnl"].sum() / rows["cost"].sum()), 6),
    }


def build_primary_rows(frame: pd.DataFrame) -> pd.DataFrame:
    rows = frame.copy()
    rows["d1_yes_mid"] = 1.0 - (rows["d1_no_ask"] + rows["d1_no_bid"]) / 2.0
    rows["d1_yes_ask"] = 1.0 - rows["d1_no_bid"]
    rows = rows[
        rows["d1_yes_mid"].ge(0.80)
        & rows["d1_no_bid"].notna()
        & rows["d1_hit"].notna()
    ].copy()
    rows = (
        rows.sort_values(["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .reset_index(drop=True)
    )
    rows["win"] = rows["d1_hit"].astype(float)
    rows["fee"] = weather_fee(rows["d1_yes_ask"])
    rows["cost"] = rows["d1_yes_ask"] + rows["fee"]
    rows["pnl"] = rows["win"] - rows["cost"]
    rows["market_residual"] = rows["win"] - rows["d1_yes_mid"]
    rows["loss_mode"] = np.select(
        [rows["win"].eq(1), rows["skip_over_d1"].fillna(False).astype(bool), rows["current_bracket_held"].fillna(False).astype(bool)],
        ["win_exact_d1", "overshoot_d2_or_higher", "stall_at_current"],
        default="other_loss",
    )
    rows["period"] = np.where(rows["target_date"].lt(FORWARD_START), "train", "historical_forward")
    rows["peak_clock_state"] = pd.cut(
        pd.to_numeric(rows["forecast_peak_delta_hours_local"], errors="coerce"),
        [-np.inf, -0.25, 0.25, np.inf],
        labels=["peak_ahead", "near_peak", "peak_passed"],
    ).astype("string").fillna("peak_clock_unknown")
    rows["entry_ask_band"] = pd.cut(
        rows["d1_yes_ask"],
        [0.0, 0.85, 0.90, 0.95, 1.001],
        right=False,
        labels=["ask_0.80_0.85", "ask_0.85_0.90", "ask_0.90_0.95", "ask_0.95_plus"],
    ).astype("string").fillna("ask_other")
    rows["market_mid_band"] = pd.cut(
        rows["d1_yes_mid"],
        [0.80, 0.85, 0.90, 0.95, 1.001],
        right=False,
        labels=["mid_0.80_0.85", "mid_0.85_0.90", "mid_0.90_0.95", "mid_0.95_plus"],
    ).astype("string").fillna("mid_other")
    return rows


def summarize_regimes(rows: pd.DataFrame) -> pd.DataFrame:
    output: list[dict] = []
    for dimension in REGIME_DIMENSIONS:
        for value, group in rows.groupby(dimension, dropna=False, observed=True):
            lo, hi, pvalue = _block_bootstrap_roi(group, seed=20260722 + len(output))
            train = group[group["period"].eq("train")]
            forward = group[group["period"].eq("historical_forward")]
            output.append(
                {
                    "dimension": dimension,
                    "regime": str(value),
                    "rows": int(len(group)),
                    "dates": int(group["target_date"].nunique()),
                    "cities": int(group["city"].nunique()),
                    "wins": int(group["win"].sum()),
                    "losses": int(len(group) - group["win"].sum()),
                    "win_rate": float(group["win"].mean()),
                    "avg_market_mid": float(group["d1_yes_mid"].mean()),
                    "avg_entry_ask": float(group["d1_yes_ask"].mean()),
                    "market_calibration_delta": float(group["win"].mean() - group["d1_yes_mid"].mean()),
                    "market_brier": float(((group["win"] - group["d1_yes_mid"]) ** 2).mean()),
                    "market_logloss": _safe_logloss(group["win"], group["d1_yes_mid"]),
                    "cost": float(group["cost"].sum()),
                    "pnl": float(group["pnl"].sum()),
                    "roi": float(group["pnl"].sum() / group["cost"].sum()),
                    "roi_ci_low": lo,
                    "roi_ci_high": hi,
                    "bootstrap_pvalue": pvalue,
                    "overshoot_losses": int(group["loss_mode"].eq("overshoot_d2_or_higher").sum()),
                    "stall_losses": int(group["loss_mode"].eq("stall_at_current").sum()),
                    "other_losses": int(group["loss_mode"].eq("other_loss").sum()),
                    "train_rows": int(len(train)),
                    "train_dates": int(train["target_date"].nunique()),
                    "train_roi": None if train.empty else float(train["pnl"].sum() / train["cost"].sum()),
                    "forward_rows": int(len(forward)),
                    "forward_dates": int(forward["target_date"].nunique()),
                    "forward_roi": None if forward.empty else float(forward["pnl"].sum() / forward["cost"].sum()),
                }
            )
    result = pd.DataFrame(output)
    eligible = result["rows"].ge(10) & result["dates"].ge(5) & result["bootstrap_pvalue"].notna()
    pairs = [(int(idx), float(result.at[idx, "bootstrap_pvalue"])) for idx in result.index[eligible]]
    qvalues = _bh_adjust(pairs)
    result["fdr_qvalue"] = [qvalues.get(int(idx), float("nan")) for idx in result.index]
    result["low_sample"] = (result["rows"] < 30) | (result["dates"] < 10)
    return result.sort_values(["dimension", "rows", "regime"], ascending=[True, False, True])


def continuous_diagnostics(rows: pd.DataFrame) -> pd.DataFrame:
    output: list[dict] = []
    failure = rows["win"].eq(0)
    for feature in CONTINUOUS_FEATURES:
        values = pd.to_numeric(rows.get(feature), errors="coerce")
        covered = values.notna()
        positive = values[covered & failure]
        negative = values[covered & ~failure]
        auc = float("nan")
        if len(positive) and len(negative):
            comparisons = positive.to_numpy()[:, None] - negative.to_numpy()[None, :]
            auc = float((comparisons > 0).mean() + 0.5 * (comparisons == 0).mean())
        output.append(
            {
                "feature": feature,
                "covered_rows": int(covered.sum()),
                "failure_rows": int(len(positive)),
                "failure_median": None if positive.empty else float(positive.median()),
                "win_median": None if negative.empty else float(negative.median()),
                "auc_higher_predicts_failure": auc,
                "auc_separation": None if math.isnan(auc) else max(auc, 1.0 - auc),
            }
        )
    return pd.DataFrame(output).sort_values("auc_separation", ascending=False, na_position="last")


def _solar_window(hour: int) -> str:
    if hour <= 11:
        return "late_morning"
    if hour <= 14:
        return "solar_peak_window"
    if hour <= 17:
        return "afternoon_decay_window"
    return "evening_tail"


def live_forward(
    raw_path: Path, db_path: Path
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    raw = _jsonl(raw_path)
    first = [row for row in raw if row.get("event_type") is None and row.get("is_first_per_city_date")]
    settlements = {
        (str(row.get("city")), str(row.get("target_date"))): row
        for row in raw
        if row.get("event_type") == "settlement"
    }
    rows: list[dict] = []
    for event in first:
        key = (str(event["city"]), str(event["target_date"]))
        settlement = settlements.get(key)
        bounded = "+" not in str(event.get("d1_bracket") or "")
        incident = key in LIVE_INCIDENT_KEYS
        ts = pd.Timestamp(event["cycle_ts_utc"])
        timezone_name = CITY_TIMEZONE.get(key[0], "UTC")
        local = ts.tz_convert(ZoneInfo(timezone_name))
        win = None if settlement is None else float(bool(settlement.get("win")))
        entry_cost = float(event.get("entry_cost_with_fee"))
        rows.append(
            {
                "city": key[0],
                "target_date": key[1],
                "decision_ts_utc": event["cycle_ts_utc"],
                "decision_local": local.isoformat(),
                "decision_hour_local": int(local.hour),
                "solar_window": _solar_window(int(local.hour)),
                "d1_bracket": event.get("d1_bracket"),
                "d1_yes_mid": event.get("d1_yes_mid"),
                "d1_yes_ask": event.get("d1_yes_direct_ask"),
                "entry_cost_with_fee_per_share": entry_cost,
                "minutes_since_running_max": event.get("minutes_since_running_max"),
                "temp_trend_1h_f": event.get("d_tmpf_1h"),
                "temp_trend_3h_f": event.get("d_tmpf_3h"),
                "relative_humidity_pct": event.get("relative_humidity_pct"),
                "wind_speed_kt": event.get("wind_speed_kt"),
                "execution_mode": event.get("execution_mode"),
                "live_blocker": event.get("live_blocker"),
                "bounded_expression": bounded,
                "observation_continuity_incident": incident,
                "settled": settlement is not None,
                "settled_bracket": None if settlement is None else settlement.get("settled_bracket"),
                "win": win,
                "unit_pnl_replay": None if win is None else win - entry_cost,
                "clean_primary": bounded and not incident,
            }
        )
    frame = pd.DataFrame(rows)
    clean = frame[frame["clean_primary"] & frame["settled"]].copy()
    clean["pnl"] = clean["unit_pnl_replay"]
    clean["cost"] = clean["entry_cost_with_fee_per_share"]
    clean["market_mid_band"] = pd.cut(
        clean["d1_yes_mid"],
        [0.80, 0.85, 0.90, 0.95, 1.001],
        right=False,
        labels=["mid_0.80_0.85", "mid_0.85_0.90", "mid_0.90_0.95", "mid_0.95_plus"],
    ).astype("string").fillna("mid_other")
    trend = pd.to_numeric(clean["temp_trend_1h_f"], errors="coerce")
    clean["path_state"] = np.select(
        [trend.isna(), trend.ge(1.0)],
        ["trend_unknown", "active_warming_1h"],
        default="flat_1h",
    )
    humidity = pd.to_numeric(clean["relative_humidity_pct"], errors="coerce")
    clean["humidity_band"] = pd.cut(
        humidity,
        [-np.inf, 40, 60, 75, np.inf],
        labels=["rh_lt40", "rh_40_60", "rh_60_75", "rh_75_plus"],
    ).astype("string").fillna("rh_unknown")
    wind = pd.to_numeric(clean["wind_speed_kt"], errors="coerce")
    clean["wind_band"] = pd.cut(
        wind,
        [-np.inf, 10, 18, np.inf],
        right=False,
        labels=["light_wind", "moderate_wind", "windy_mixing_noise"],
    ).astype("string").fillna("wind_unknown")
    live_regime_rows: list[dict] = []
    for dimension in ["solar_window", "market_mid_band", "path_state", "humidity_band", "wind_band"]:
        for value, group in clean.groupby(dimension, observed=True):
            lo, hi, _ = _block_bootstrap_roi(group, seed=20260770 + len(live_regime_rows))
            live_regime_rows.append(
                {
                    "dimension": dimension,
                    "regime": str(value),
                    "rows": int(len(group)),
                    "dates": int(group["target_date"].nunique()),
                    "wins": int(group["win"].sum()),
                    "losses": int(len(group) - group["win"].sum()),
                    "win_rate": float(group["win"].mean()),
                    "cost": float(group["cost"].sum()),
                    "pnl": float(group["pnl"].sum()),
                    "roi": float(group["pnl"].sum() / group["cost"].sum()),
                    "roi_ci_low": lo,
                    "roi_ci_high": hi,
                    "low_sample": True,
                }
            )
    live_regimes = pd.DataFrame(live_regime_rows)
    live_lo, live_hi, live_p = _block_bootstrap_roi(clean, seed=20260771)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    trades = pd.read_sql_query(
        """
        SELECT fill_id, city, target_date, fill_price, fill_qty, fees_usd,
               cost_usd, pnl_usd_at_fill, settlement_status,
               execution_policy, child_order_role, fee_evidence_class
        FROM fact_trades
        WHERE run_id = ?
        ORDER BY fill_ts_utc
        """,
        conn,
        params=[RUN_ID],
    )
    conn.close()
    trade_incident = trades.apply(lambda row: (str(row["city"]), str(row["target_date"])) in LIVE_INCIDENT_KEYS, axis=1)
    clean_trades = trades[~trade_incident].copy()
    incident_trades = trades[trade_incident].copy()
    clean_trades_for_ci = clean_trades.rename(columns={"pnl_usd_at_fill": "pnl", "cost_usd": "cost"})
    actual_lo, actual_hi, actual_p = _block_bootstrap_roi(clean_trades_for_ci, seed=20260772)
    trades["observation_continuity_incident"] = trade_incident
    summary = {
        "first_signals_all": int(len(frame)),
        "bounded_signals": int(frame["bounded_expression"].sum()),
        "incident_signals": int(frame["observation_continuity_incident"].sum()),
        "clean_bounded_settled_signals": int(len(clean)),
        "clean_bounded_dates": int(clean["target_date"].nunique()),
        "clean_bounded_wins": int(clean["win"].sum()),
        "clean_bounded_win_rate": float(clean["win"].mean()),
        "clean_signal_replay_cost": float(clean["entry_cost_with_fee_per_share"].sum()),
        "clean_signal_replay_pnl": float(clean["unit_pnl_replay"].sum()),
        "clean_signal_replay_roi": float(clean["unit_pnl_replay"].sum() / clean["entry_cost_with_fee_per_share"].sum()),
        "clean_signal_replay_roi_ci": [live_lo, live_hi],
        "clean_signal_replay_bootstrap_pvalue": live_p,
        "actual_clean_fills": int(len(clean_trades)),
        "actual_clean_city_days": int(clean_trades[["city", "target_date"]].drop_duplicates().shape[0]),
        "actual_clean_cost": float(clean_trades["cost_usd"].sum()),
        "actual_clean_fees": float(clean_trades["fees_usd"].sum()),
        "actual_clean_pnl": float(clean_trades["pnl_usd_at_fill"].sum()),
        "actual_clean_roi": float(clean_trades["pnl_usd_at_fill"].sum() / clean_trades["cost_usd"].sum()),
        "actual_clean_roi_ci": [actual_lo, actual_hi],
        "actual_clean_bootstrap_pvalue": actual_p,
        "actual_incident_fills": int(len(incident_trades)),
        "actual_incident_cost": float(incident_trades["cost_usd"].sum()),
        "actual_incident_pnl": float(incident_trades["pnl_usd_at_fill"].sum()),
        "actual_all_fills": int(len(trades)),
        "actual_all_pnl": float(trades["pnl_usd_at_fill"].sum()),
        "actual_all_roi": float(trades["pnl_usd_at_fill"].sum() / trades["cost_usd"].sum()),
        "actual_unsettled_fills": int(trades["settlement_status"].ne("settled").sum()),
        "fee_evidence_classes": trades["fee_evidence_class"].value_counts(dropna=False).to_dict(),
    }
    return frame, live_regimes, trades, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--raw-events", type=Path, default=DEFAULT_RAW)
    args = parser.parse_args()

    source = pd.read_csv(args.input)
    primary = build_primary_rows(source)
    regimes = summarize_regimes(primary)
    continuous = continuous_diagnostics(primary)
    live_rows, live_regimes, live_trades, live_summary = live_forward(args.raw_events, args.db_path)
    overall_lo, overall_hi, overall_p = _block_bootstrap_roi(primary)

    summary = {
        "contract": {
            "historical_grain": "first qualifying PIT hour per city x target_date",
            "trigger": "d1 YES book mid >= 0.80",
            "label": "final exact winning bracket equals bounded d1",
            "historical_executable_price": "YES ask = 1 - d1 NO bid",
            "fee": "0.05 * price * (1-price) per share",
            "bootstrap": "target_date block, 4000 draws, 95%",
            "train_forward_split": f"train < {FORWARD_START}; historical forward >= {FORWARD_START}",
            "live_incident_exclusion": sorted([list(key) for key in LIVE_INCIDENT_KEYS]),
        },
        "data": {
            "input": str(args.input.relative_to(ROOT)),
            "input_sha256": _sha256(args.input),
            "raw_state_rows": int(len(source)),
            "raw_dates": int(source["target_date"].nunique()),
            "raw_cities": int(source["city"].nunique()),
            "live_raw": str(args.raw_events.relative_to(ROOT)),
            "live_raw_sha256": _sha256(args.raw_events),
            "db": str(args.db_path.relative_to(ROOT)),
        },
        "historical_primary": {
            **_period_summary(primary),
            "cities": int(primary["city"].nunique()),
            "avg_market_mid": float(primary["d1_yes_mid"].mean()),
            "avg_entry_ask": float(primary["d1_yes_ask"].mean()),
            "market_calibration_delta": float(primary["win"].mean() - primary["d1_yes_mid"].mean()),
            "market_brier": float(((primary["win"] - primary["d1_yes_mid"]) ** 2).mean()),
            "market_logloss": _safe_logloss(primary["win"], primary["d1_yes_mid"]),
            "roi_ci": [overall_lo, overall_hi],
            "bootstrap_pvalue": overall_p,
            "loss_modes": primary["loss_mode"].value_counts().to_dict(),
            "train": _period_summary(primary[primary["period"].eq("train")]),
            "historical_forward": _period_summary(primary[primary["period"].eq("historical_forward")]),
        },
        "multiple_testing": {
            "dimensions": REGIME_DIMENSIONS,
            "eligible_slices": int((regimes["fdr_qvalue"].notna()).sum()),
            "method": "Benjamini-Hochberg over slices with rows>=10 and dates>=5",
            "fdr_q_lt_0_05": int(regimes["fdr_qvalue"].lt(0.05).sum()),
        },
        "live_forward": live_summary,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    primary.to_csv(args.output_dir / "historical_first_signals.csv", index=False)
    regimes.to_csv(args.output_dir / "regime_summary.csv", index=False)
    continuous.to_csv(args.output_dir / "continuous_feature_diagnostics.csv", index=False)
    live_rows.to_csv(args.output_dir / "live_forward_first_signals.csv", index=False)
    live_regimes.to_csv(args.output_dir / "live_forward_regime_summary.csv", index=False)
    live_trades.to_csv(args.output_dir / "actual_live_fills.csv", index=False)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
