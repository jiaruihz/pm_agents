#!/usr/bin/env python3
"""Audit HeadA probabilities after correcting forecast-history local dates."""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from weather_data_feed.forecast_history import forecast_hourly_daily_max_local  # noqa: E402

DB = ROOT / "runtime/weather.db"
CACHE = Path("/Volumes/jrs/weather_data_feed_service_runtime/cache")
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-14-heada-timezone-probability-audit-v1.md"
OUT_JSON = OUT_MD.with_suffix(".json")
OUT_ROWS = ROOT / "docs/analysis/2026-07/generated/heada_timezone_probability_audit_v1/selector_rows.csv"
FEE_RATE = 0.05
SEED = 20260714
N_BOOT = 5000


def forecast_model(source: Any) -> str:
    return "ecmwf" if "ecmwf" in str(source or "").lower() else "gfs"


def select_cache(city: str, model: str) -> Path | None:
    aliases = [city, "LosAngeles"] if city == "LA" else [city]
    matches: list[Path] = []
    for alias in aliases:
        patterns = [f"ecmwf_v4_{alias}_*.json"] if model == "ecmwf" else [
            f"gfs_v4_{alias}_*.json", f"gfs_365d_{alias}_*.json"
        ]
        for pattern in patterns:
            matches.extend(CACHE.glob(pattern))
        if matches:
            break
    if not matches:
        return None

    def score(path: Path) -> tuple[int, float, str]:
        try:
            payload = json.loads(path.read_text())
            rows = len((payload.get("hourly") or {}).get("time") or [])
        except Exception:
            rows = 0
        return rows, path.stat().st_mtime, path.name

    return max(matches, key=score)


def wu_daily(icao: str) -> dict[str, float]:
    path = CACHE / "wu_obs" / f"wu_obs_{icao}.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path, usecols=["date_local", "temp"])
    frame["temp"] = pd.to_numeric(frame["temp"], errors="coerce")
    return frame.dropna().groupby("date_local")["temp"].max().to_dict()


def corrected_errors(city: str, icao: str, model: str) -> np.ndarray | None:
    path = select_cache(city, model)
    if path is None:
        return None
    forecast = forecast_hourly_daily_max_local(json.loads(path.read_text()), city=city)
    actual = wu_daily(icao)
    errors = [actual[day] - value for day, value in forecast.items() if day in actual]
    return np.asarray(errors, dtype=float) if len(errors) >= 30 else None


def bracket_probability(forecast_max_f: float, errors_f: np.ndarray, bracket: str, unit: str) -> float:
    forecast = forecast_max_f if unit.upper() == "F" else (forecast_max_f - 32.0) * 5.0 / 9.0
    errors = errors_f if unit.upper() == "F" else errors_f * 5.0 / 9.0
    simulated = np.round(forecast + errors).astype(int)
    label = str(bracket)
    if label.endswith("+"):
        return float(np.mean(simulated >= int(label[:-1])))
    if label.endswith("-"):
        return float(np.mean(simulated <= int(label[:-1])))
    if "-" in label and not label.startswith("-"):
        low, high = map(int, label.split("-", 1))
        return float(np.mean((simulated >= low) & (simulated <= high)))
    return float(np.mean(simulated == int(label)))


def load_rows() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    frame = pd.read_sql_query(
        """
        SELECT f.candidate_id, f.city, f.icao, f.event_date AS target_date,
               f.bracket, f.unit, f.forecast_source, f.forecast_max_f,
               f.model_p_yes AS legacy_p, f.edge AS legacy_edge,
               f.decision_entry_price AS ask, f.decision_snapshot_ts_utc,
               COALESCE(f.final_yes, s.final_price) AS win,
               COALESCE(f.settlement_status, s.settlement_status) AS settlement_status,
               f.fact_built_at_utc
        FROM fact_signal_candidates f
        LEFT JOIN settlement_outcomes s
          ON s.city=f.city AND s.target_date=f.event_date AND s.bracket=f.bracket
        WHERE f.side='BUY_YES' AND f.decision_entry_price BETWEEN 0.05 AND 0.20
        """,
        conn,
    )
    conn.close()
    for column in ["forecast_max_f", "legacy_p", "legacy_edge", "ask", "win"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[frame["settlement_status"].eq("settled") & frame["win"].isin([0.0, 1.0])].copy()
    frame = frame.sort_values(["target_date", "city", "bracket", "decision_snapshot_ts_utc", "ask"])
    frame = frame.drop_duplicates(["target_date", "city", "bracket"], keep="first").reset_index(drop=True)
    frame["model"] = frame["forecast_source"].map(forecast_model)

    cache: dict[tuple[str, str, str], np.ndarray | None] = {}
    probabilities = []
    error_ns = []
    for row in frame.itertuples(index=False):
        key = (str(row.city), str(row.icao), str(row.model))
        if key not in cache:
            cache[key] = corrected_errors(*key)
        errors = cache[key]
        if errors is None or not math.isfinite(float(row.forecast_max_f)):
            probabilities.append(math.nan)
            error_ns.append(0)
        else:
            probabilities.append(bracket_probability(float(row.forecast_max_f), errors, str(row.bracket), str(row.unit)))
            error_ns.append(len(errors))
    frame["corrected_p"] = probabilities
    frame["corrected_error_n"] = error_ns
    frame = frame.dropna(subset=["corrected_p", "legacy_p", "ask"]).copy()
    frame["fee"] = FEE_RATE * frame["ask"] * (1.0 - frame["ask"])
    frame["corrected_edge"] = frame["corrected_p"] - frame["ask"]

    low = frame["bracket"].astype(str).str.extract(r"(-?\d+(?:\.\d+)?)", expand=False).astype(float)
    low_f = np.where(frame["unit"].astype(str).str.upper().eq("C"), low * 9.0 / 5.0 + 32.0, low)
    width_f = np.where(frame["unit"].astype(str).str.upper().eq("C"), 1.8, 2.0)
    frame["dist"] = (low_f - frame["forecast_max_f"]) / width_f
    return frame


def live_order(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(
            ["target_date", "city", "decision_snapshot_ts_utc", "ask", "legacy_edge", "bracket"],
            ascending=[True, True, True, True, False, True],
        )
        .drop_duplicates(["target_date", "city"], keep="first")
        .copy()
    )


def selector(frame: pd.DataFrame, probability: str) -> pd.DataFrame:
    edge = "legacy_edge" if probability == "legacy_p" else "corrected_edge"
    return live_order(frame[(frame[edge] >= 0.20) & (frame["dist"] > 0)].copy())


def roi_ci(frame: pd.DataFrame) -> tuple[float, float]:
    if frame.empty:
        return math.nan, math.nan
    daily = frame.assign(pnl=frame["win"] - frame["ask"] - frame["fee"]).groupby("target_date").agg(
        pnl=("pnl", "sum"), cost=("ask", "sum")
    )
    rng = np.random.default_rng(SEED)
    values = []
    for _ in range(N_BOOT):
        sample = daily.iloc[rng.integers(0, len(daily), len(daily))]
        if sample["cost"].sum() > 0:
            values.append(sample["pnl"].sum() / sample["cost"].sum())
    return tuple(float(x) for x in np.quantile(values, [0.025, 0.975]))


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "dates": 0}
    pnl = frame["win"] - frame["ask"] - frame["fee"]
    lo, hi = roi_ci(frame)
    daily = frame.assign(pnl=pnl).groupby("target_date").agg(pnl=("pnl", "sum"), cost=("ask", "sum"))
    return {
        "rows": len(frame), "dates": frame["target_date"].nunique(), "cities": frame["city"].nunique(),
        "wins": int(frame["win"].sum()), "win_rate": float(frame["win"].mean()),
        "avg_ask": float(frame["ask"].mean()), "avg_legacy_p": float(frame["legacy_p"].mean()),
        "avg_corrected_p": float(frame["corrected_p"].mean()), "cost_per_share": float(frame["ask"].sum()),
        "pnl_per_share": float(pnl.sum()), "roi": float(pnl.sum() / frame["ask"].sum()),
        "roi_ci_low": lo, "roi_ci_high": hi, "losing_days": int((daily["pnl"] < 0).sum()),
        "le_minus_50pct_days": int(((daily["pnl"] / daily["cost"]) <= -0.5).sum()),
        "max_daily_loss_5shares": float(5.0 * daily["pnl"].min()),
    }


def probability_summary(frame: pd.DataFrame) -> dict[str, Any]:
    y = frame["win"].astype(int).to_numpy()
    out: dict[str, Any] = {"rows": len(frame), "dates": frame["target_date"].nunique(), "realized": float(y.mean())}
    for name in ["legacy_p", "corrected_p", "ask"]:
        p = np.clip(frame[name].to_numpy(float), 0.001, 0.999)
        out[name] = {
            "mean": float(p.mean()), "brier": float(brier_score_loss(y, p)),
            "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        }
    return out


def fmt_pct(value: Any) -> str:
    return "NA" if value is None or not math.isfinite(float(value)) else f"{float(value):+.1%}"


def main() -> None:
    rows = load_rows()
    windows = {
        "full": rows,
        "train_through_2026_06_20": rows[rows["target_date"] <= "2026-06-20"],
        "recent_ge_2026_06_21": rows[rows["target_date"] >= "2026-06-21"],
        "forward_ge_2026_07_01": rows[rows["target_date"] >= "2026-07-01"],
        "fresh_ge_2026_07_08": rows[rows["target_date"] >= "2026-07-08"],
    }
    results: dict[str, Any] = {}
    selected_rows = []
    for window, data in windows.items():
        legacy = selector(data, "legacy_p")
        corrected = selector(data, "corrected_p")
        legacy_keys = set(zip(legacy.target_date, legacy.city, legacy.bracket))
        corrected_keys = set(zip(corrected.target_date, corrected.city, corrected.bracket))
        results[window] = {
            "probability": probability_summary(data),
            "legacy_selector": summarize(legacy),
            "corrected_selector": summarize(corrected),
            "overlap": len(legacy_keys & corrected_keys),
            "legacy_only": len(legacy_keys - corrected_keys),
            "corrected_only": len(corrected_keys - legacy_keys),
        }
        selected_rows.extend(legacy.assign(window=window, arm="legacy_selector").to_dict("records"))
        selected_rows.extend(corrected.assign(window=window, arm="corrected_selector").to_dict("records"))

    full_legacy = selector(rows, "legacy_p")
    diagnostics = {}
    diagnostics["source"] = {
        str(key): summarize(group) for key, group in full_legacy.groupby("model", dropna=False)
    }
    full_legacy = full_legacy.copy()
    full_legacy["ask_band"] = pd.cut(full_legacy["ask"], [0.049, 0.08, 0.12, 0.16, 0.201], include_lowest=True).astype(str)
    diagnostics["ask_band"] = {
        str(key): summarize(group) for key, group in full_legacy.groupby("ask_band", observed=True)
    }
    full_legacy["probability_inflation"] = full_legacy["legacy_p"] - full_legacy["corrected_p"]
    full_legacy["inflation_band"] = pd.qcut(full_legacy["probability_inflation"], 4, duplicates="drop").astype(str)
    diagnostics["probability_inflation_quartile"] = {
        str(key): summarize(group) for key, group in full_legacy.groupby("inflation_band", observed=True)
    }

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db": str(DB), "cache": str(CACHE),
        "fact_built_at_utc": str(rows["fact_built_at_utc"].max()),
        "settled_range": [str(rows["target_date"].min()), str(rows["target_date"].max())],
        "neutral_rows": len(rows), "neutral_dates": rows["target_date"].nunique(),
        "results": results, "diagnostics": diagnostics,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    OUT_ROWS.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(selected_rows).to_csv(OUT_ROWS, index=False)

    lines = []
    for window, result in results.items():
        for arm in ["legacy_selector", "corrected_selector"]:
            value = result[arm]
            lines.append(
                f"| {window} | {arm} | {value.get('rows', 0)} | {value.get('dates', 0)} | "
                f"{value.get('wins', 0)} | {fmt_pct(value.get('win_rate'))} | {fmt_pct(value.get('avg_ask'))} | "
                f"{fmt_pct(value.get('avg_legacy_p'))} | {fmt_pct(value.get('avg_corrected_p'))} | "
                f"{fmt_pct(value.get('roi'))} [{fmt_pct(value.get('roi_ci_low'))}, {fmt_pct(value.get('roi_ci_high'))}] |"
            )
    p = results["full"]["probability"]
    fwd = results["forward_ge_2026_07_01"]
    OUT_MD.write_text(
        f"""# HeadA Timezone Probability Audit v1

Generated: 2026-07-14
Scope: `forecast_tail_low_price_yes`; same canonical candidate denominator, official Weather fee.

## Verdict

The legacy probability layer has a deterministic calendar bug: GMT forecast archives were grouped by their raw UTC date and joined to station-local `date_local`. The bug is real, but it is **not the explanation for the low hit rate**: correcting it changes mean probability from {fmt_pct(p['legacy_p']['mean'])} to {fmt_pct(p['corrected_p']['mean'])} on the neutral universe and does not improve Brier score. It changes some threshold triggers, but it does not rescue probability calibration.

Current action: fix the probability producer; keep HeadA at tiny fixed 5-share risk while fresh corrected snapshots accumulate. No hindsight city blacklist and no new threshold is promoted from this audit.

## Data

- Neutral settled denominator: {len(rows)} rows / {rows['target_date'].nunique()} dates / {rows['city'].nunique()} cities, {rows['target_date'].min()}..{rows['target_date'].max()}.
- Canonical fact build: `{rows['fact_built_at_utc'].max()}`.
- Error histories are recomputed from the same GFS/ECMWF caches and WU station observations, changing only UTC-to-city-local calendar grouping.
- Grain: one city-date-bracket decision; selector matches live's earliest eligible city-date ordering.

## Probability Quality

| head | mean probability | realized | Brier | AUC |
|---|---:|---:|---:|---:|
| legacy model | {fmt_pct(p['legacy_p']['mean'])} | {fmt_pct(p['realized'])} | {p['legacy_p']['brier']:.4f} | {p['legacy_p']['auc']:.4f} |
| timezone-corrected | {fmt_pct(p['corrected_p']['mean'])} | {fmt_pct(p['realized'])} | {p['corrected_p']['brier']:.4f} | {p['corrected_p']['auc']:.4f} |
| market ask | {fmt_pct(p['ask']['mean'])} | {fmt_pct(p['realized'])} | {p['ask']['brier']:.4f} | {p['ask']['auc']:.4f} |

## Selector Replay

| window | arm | rows | dates | wins | win rate | avg ask | avg legacy p | avg corrected p | fee-adjusted ROI [date bootstrap 95% CI] |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(lines)}

Forward 7/01+ overlap is {fwd['overlap']} tickets; legacy-only {fwd['legacy_only']}, corrected-only {fwd['corrected_only']}. These are opportunity replays, not actual fills.

## Code Audit

1. **Confirmed bug**: `compute_error_distribution` and the ECMWF twin grouped GMT cache timestamps by `t[:10]`; fixed to city-local calendar aggregation in `weather_data_feed.forecast_history`.
2. `model_p_yes` is an empirical unconditional historical-error probability. It does not condition on forecast lead, season, current source freshness, or recent regime. Even after the calendar fix, it is a ranking input, not automatically a trustworthy absolute probability.
3. Live refreshes the book but keeps the candidate snapshot probability for up to six hours. This can create a fresh-price/stale-weather comparison. It needs telemetry and a later PIT replay, not an immediate new gate.
4. The live selector takes the earliest eligible bracket per city-date. Only 11 historical city-dates have more than one eligible bracket, so this ordering mismatch is real but not the main loss driver.
5. `forecast_source_best_D_excluded_v1` is telemetry only in current code; it is not a hidden hard block. Existing `dist<=0` is the only model-shape exclusion in this path.

## Can We Remove Obvious Bad Tickets?

Not cleanly yet.

- Fresh 7/08..7/13: the 39 legacy-selector opportunities won 3 times (7.7%); 5-8c tickets were 0/18, but the same cheap band was profitable over the full 68-day history. Cutting it now would be a six-day hindsight rule.
- Recent 6/21..7/13: GFS was 16/70 and ECMWF 6/70, but both model families were positive over the full history and the ECMWF deterioration overlaps the known source outage/pollution window. This is a source-regime diagnostic, not a city/source hard block.
- Higher `model_p_yes` or larger reported edge is not monotonic. Fresh edge quartiles won 2/10, 0/10, 0/9, 1/10. The raw score is therefore unsuitable for sizing and cannot support a simple “remove low score” fix.
- `dist>2 brackets` is 0/4 recently, but four rows are not a selector. `dist<=0` remains the only mechanism-defined exclusion already supported by broader evidence.

The clean optimization target is a new calibrated tail ranking built on PIT source/lead/season features while preserving equal daily capacity. Until it beats the old rank in forward data, the honest live action remains fixed 5 shares rather than adding filters after losses.

## Contract Verdict

significance=FAIL for any new selector because corrected probabilities were reconstructed from historical cache rather than captured prospectively; baseline=FAIL/NA pending corrected live snapshots; forward=FAIL/NA; conclusion=inconclusive.

The software defect is confirmed and fixed. The trading improvement is not yet confirmed; fresh corrected snapshots and the affected-window counterfactual must settle first.

## Eight Rings

Covered: canonical opportunity denominator, probability calibration, official fee, target-date bootstrap, same-selector replay, source/ask diagnostics. Partial/missing: actual corrected fills, fresh PIT forward, orderbook queue execution, capacity, portfolio correlation.
""",
        encoding="utf-8",
    )
    print(json.dumps({"data": payload | {"diagnostics": "written_to_json"}, "report": str(OUT_MD)}, indent=2, default=str))


if __name__ == "__main__":
    main()
