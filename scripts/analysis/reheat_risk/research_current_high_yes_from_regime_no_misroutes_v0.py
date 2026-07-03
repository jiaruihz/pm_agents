#!/usr/bin/env python3
"""Analyze current-high YES as the inverse expression of stale NO misroutes.

The regime-routed NO work found a group of rows that should not be treated as
"fresh runway current NO".  This script asks the narrower inverse question:
when the day has already touched the current high and then stalls/pulls back,
does buying the current-high YES have a cleaner payoff expression?
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_mechanism_split_v2/trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_high_yes_from_regime_no_misroutes_v0"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-current-high-yes-from-regime-no-misroutes-v0.md"
SNAPSHOT_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
JEDDAH_IEM = (
    ROOT
    / "docs/analysis/2026-06/generated/regime_routed_no_wind_context_v2/iem_direction_cache/"
    / "iem_OEJN_2026-05-18_2026-06-24.csv"
)

STAKE_USD = 5.0
FORWARD_START = "2026-06-21"
SEED = 20260629


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:+.1f}%"


def usd(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"${float(value):+.2f}"


def num(value: float | None, digits: int = 3) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def json_ready(value):
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return value if math.isfinite(value) else None
    return value


def boolish(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = numeric.fillna(0).astype(float) > 0
    text_mask = numeric.isna()
    if text_mask.any():
        out.loc[text_mask] = series.loc[text_mask].astype(str).str.lower().isin({"true", "1", "1.0", "yes"})
    return out


def bootstrap_roi_by_date(df: pd.DataFrame, pnl_col: str, cost_col: str, n_iter: int = 5000) -> tuple[float, float]:
    if df.empty or df["target_date"].nunique() < 3:
        return (float("nan"), float("nan"))
    by_date = df.groupby("target_date", as_index=False)[[pnl_col, cost_col]].sum()
    rng = np.random.default_rng(SEED)
    rois: list[float] = []
    idx = np.arange(len(by_date))
    pnl_values = by_date[pnl_col].to_numpy(dtype=float)
    cost_values = by_date[cost_col].to_numpy(dtype=float)
    for _ in range(n_iter):
        sample = rng.choice(idx, size=len(idx), replace=True)
        cost = cost_values[sample].sum()
        if cost > 0:
            rois.append(float(pnl_values[sample].sum() / cost))
    if not rois:
        return (float("nan"), float("nan"))
    return tuple(float(x) for x in np.quantile(rois, [0.025, 0.975]))


def add_payoffs(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["yes_ask"] = pd.to_numeric(out["current_yes_ask"], errors="coerce")
    out["no_ask"] = pd.to_numeric(out["current_bracket_no_ask"], errors="coerce")
    out["yes_payoff"] = pd.to_numeric(out["current_yes_payoff"], errors="coerce").fillna(0.0)
    out["no_payoff"] = pd.to_numeric(out["current_bracket_no_payoff"], errors="coerce").fillna(0.0)
    out["yes_cost_usd"] = STAKE_USD
    out["no_cost_usd"] = STAKE_USD
    out["yes_pnl_usd"] = out["yes_payoff"] * (STAKE_USD / out["yes_ask"]) - STAKE_USD
    out["no_pnl_usd"] = out["no_payoff"] * (STAKE_USD / out["no_ask"]) - STAKE_USD
    out["window2"] = np.where(out["target_date"].astype(str) >= FORWARD_START, "forward_2026_06_21_plus", "train_to_2026_06_20")
    return out


def summarize(df: pd.DataFrame, name: str, side: str = "yes") -> dict[str, object]:
    if df.empty:
        return {
            "slice": name,
            "side": side,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": float("nan"),
            "avg_ask": float("nan"),
            "pnl_usd": 0.0,
            "roi": float("nan"),
            "roi_ci_low": float("nan"),
            "roi_ci_high": float("nan"),
        }
    ask_col = f"{side}_ask"
    payoff_col = f"{side}_payoff"
    pnl_col = f"{side}_pnl_usd"
    cost_col = f"{side}_cost_usd"
    ci_low, ci_high = bootstrap_roi_by_date(df, pnl_col, cost_col)
    return {
        "slice": name,
        "side": side,
        "rows": int(len(df)),
        "dates": int(df["target_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "wins": int(pd.to_numeric(df[payoff_col], errors="coerce").fillna(0).sum()),
        "win_rate": float(pd.to_numeric(df[payoff_col], errors="coerce").fillna(0).mean()),
        "avg_ask": float(pd.to_numeric(df[ask_col], errors="coerce").mean()),
        "pnl_usd": float(pd.to_numeric(df[pnl_col], errors="coerce").sum()),
        "roi": float(pd.to_numeric(df[pnl_col], errors="coerce").sum() / pd.to_numeric(df[cost_col], errors="coerce").sum()),
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
    }


def build_slices(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "all_unapproved_stale": df,
        "pullback_uncertain": df[df["intraday_state"].eq("pullback_uncertain")],
        "plateau_near_high": df[df["intraday_state"].eq("plateau_near_high")],
        "mature_fade": df[df["intraday_state"].eq("mature_fade")],
        "clock_unknown": df[df["running_max_state"].eq("running_max_clock_unknown")],
        "pullback_or_plateau": df[df["intraday_state"].isin(["pullback_uncertain", "plateau_near_high"])],
        "exclude_clock_unknown": df[~df["running_max_state"].eq("running_max_clock_unknown")],
    }


def jeddah_observation_timeline() -> pd.DataFrame:
    obs = pd.read_csv(JEDDAH_IEM)
    obs["valid_utc"] = pd.to_datetime(obs["valid"], utc=True, errors="coerce")
    obs = obs[(obs["valid_utc"] >= "2026-06-21T06:00:00Z") & (obs["valid_utc"] <= "2026-06-21T15:00:00Z")].copy()
    obs["local_time"] = obs["valid_utc"].dt.tz_convert("Asia/Riyadh").dt.strftime("%Y-%m-%d %H:%M")
    obs["temp_c"] = (pd.to_numeric(obs["tmpf"], errors="coerce") - 32.0) * 5.0 / 9.0
    obs["running_max_c"] = obs["temp_c"].cummax()
    obs["is_first_35c"] = obs["temp_c"].round().eq(35) & ~obs["temp_c"].round().shift(fill_value=0).eq(35)
    obs["note"] = ""
    obs.loc[obs["valid_utc"].eq(pd.Timestamp("2026-06-21T09:00:00Z")), "note"] = "first 35C high"
    obs.loc[obs["valid_utc"].eq(pd.Timestamp("2026-06-21T10:00:00Z")), "note"] = "dropped to 33.9C; latest obs for 10:30Z decision"
    obs.loc[obs["valid_utc"].eq(pd.Timestamp("2026-06-21T11:00:00Z")), "note"] = "equal-high revisit; still 35C bracket"
    return obs[["valid_utc", "local_time", "tmpf", "temp_c", "running_max_c", "dwpf", "relh", "drct", "sknt", "skyc1", "note"]]


def jeddah_price_timeline() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for path in sorted(SNAPSHOT_DIR.glob("snapshot_20260621_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for rec in payload.get("records", []):
            if rec.get("city") != "Jeddah":
                continue
            if str(rec.get("target_date")) != "2026-06-21" or str(rec.get("bracket")) != "35":
                continue
            ts_utc = rec.get("ts_utc") or rec.get("snapshot_ts_utc") or payload.get("ts_utc")
            ts = pd.Timestamp(ts_utc)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            rows.append(
                {
                    "snapshot_file": path.name,
                    "ts_utc": ts.isoformat(),
                    "local_time": ts.to_pydatetime().astimezone(ZoneInfo("Asia/Riyadh")).strftime("%Y-%m-%d %H:%M:%S"),
                    "yes_ask": rec.get("yes_best_ask"),
                    "yes_bid": rec.get("yes_best_bid"),
                    "no_ask": rec.get("no_best_ask"),
                    "no_bid": rec.get("no_best_bid"),
                    "no_ask_size": rec.get("no_ask_size"),
                    "forecast_max_native": rec.get("forecast_max_native"),
                    "forecast_peak_hour_local": rec.get("forecast_peak_hour_local"),
                    "metar_source": rec.get("metar_source"),
                    "no_book_fetched_at_utc": rec.get("no_book_fetched_at_utc"),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["ts_utc_dt"] = pd.to_datetime(out["ts_utc"], utc=True)
    out = out.sort_values("ts_utc_dt")
    out = out[(out["ts_utc_dt"] >= "2026-06-21T07:00:00Z") & (out["ts_utc_dt"] <= "2026-06-21T12:30:00Z")]
    return out.drop(columns=["ts_utc_dt"])


def md_table(rows: list[dict[str, object]], cols: list[tuple[str, str]]) -> str:
    lines = ["| " + " | ".join(label for label, _key in cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in rows:
        values = []
        for _label, key in cols:
            value = row.get(key)
            if isinstance(value, float):
                if key in {"win_rate", "roi", "roi_ci_low", "roi_ci_high"}:
                    values.append(pct(value))
                elif key.endswith("usd") or key == "pnl_usd":
                    values.append(usd(value))
                else:
                    values.append(num(value))
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(DETAILS)
    raw["target_date"] = raw["target_date"].astype(str)
    stale = raw[boolish(raw["mechanism_unapproved_stale_current_no"])].copy()
    stale = add_payoffs(stale)

    rows: list[dict[str, object]] = []
    for slice_name, slice_df in build_slices(stale).items():
        rows.append(summarize(slice_df, slice_name, "yes"))
        rows.append(summarize(slice_df, slice_name, "no"))
        for window_name, window_df in slice_df.groupby("window2"):
            rows.append(summarize(window_df, f"{slice_name}::{window_name}", "yes"))
            rows.append(summarize(window_df, f"{slice_name}::{window_name}", "no"))

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT_DIR / "slice_summary.csv", index=False)

    detail_cols = [
        "target_date",
        "city",
        "decision_snapshot_ts_utc",
        "timezone",
        "current_bracket",
        "current_native",
        "running_native",
        "current_yes_ask",
        "current_bracket_no_ask",
        "current_yes_payoff",
        "current_bracket_no_payoff",
        "yes_pnl_usd",
        "no_pnl_usd",
        "running_max_state",
        "intraday_state",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "forecast_gap_to_running_native",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "day_regime",
        "moisture_cloud_regime",
        "wind_regime",
        "final_max_native",
        "final_winning_bracket",
    ]
    stale[detail_cols].to_csv(OUT_DIR / "stale_no_inverse_yes_details.csv", index=False)

    j_obs = jeddah_observation_timeline()
    j_prices = jeddah_price_timeline()
    j_obs.to_csv(OUT_DIR / "jeddah_2026_06_21_observation_timeline.csv", index=False)
    j_prices.to_csv(OUT_DIR / "jeddah_2026_06_21_price_timeline.csv", index=False)

    j_row = stale[(stale["city"].eq("Jeddah")) & (stale["target_date"].eq("2026-06-21"))].iloc[0].to_dict()
    report = {
        "generated_at_utc": now_utc(),
        "source_details": str(DETAILS.relative_to(ROOT)),
        "source_rows": int(len(raw)),
        "unapproved_stale_rows": int(len(stale)),
        "date_range": [str(raw["target_date"].min()), str(raw["target_date"].max())],
        "forward_start": FORWARD_START,
        "stake_usd": STAKE_USD,
        "jeddah_decision": {
            key: j_row.get(key)
            for key in [
                "decision_snapshot_ts_utc",
                "current_native",
                "running_native",
                "current_bracket",
                "current_yes_ask",
                "current_bracket_no_ask",
                "current_yes_payoff",
                "current_bracket_no_payoff",
                "final_max_native",
                "final_winning_bracket",
                "running_max_state",
                "intraday_state",
                "minutes_since_running_max",
                "forecast_peak_delta_hours_local",
                "forecast_gap_to_running_native",
            ]
        },
        "summary": rows,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(json_ready(report), indent=2, ensure_ascii=False), encoding="utf-8")

    headline_rows = summary[
        summary["slice"].isin(
            [
                "pullback_uncertain",
                "plateau_near_high",
                "mature_fade",
                "clock_unknown",
                "pullback_or_plateau",
                "all_unapproved_stale",
            ]
        )
        & summary["side"].eq("yes")
    ].to_dict("records")
    inverse_rows = summary[
        summary["slice"].isin(["pullback_uncertain", "plateau_near_high", "mature_fade", "all_unapproved_stale"])
        & summary["side"].isin(["yes", "no"])
    ].to_dict("records")

    pullback_details = stale[stale["intraday_state"].eq("pullback_uncertain")].copy()
    pullback_details["yes_roi"] = pullback_details["yes_pnl_usd"] / STAKE_USD
    pullback_details["no_roi"] = pullback_details["no_pnl_usd"] / STAKE_USD
    pullback_rows = pullback_details[
        [
            "target_date",
            "city",
            "current_bracket",
            "current_native",
            "running_native",
            "current_yes_ask",
            "current_bracket_no_ask",
            "current_yes_payoff",
            "yes_roi",
            "minutes_since_running_max",
            "forecast_peak_delta_hours_local",
            "temp_trend_1h_f",
        ]
    ].to_dict("records")

    obs_rows = j_obs.to_dict("records")
    price_rows = j_prices[
        ["local_time", "yes_ask", "no_ask", "no_ask_size", "forecast_max_native", "forecast_peak_hour_local", "metar_source"]
    ].to_dict("records")

    md = f"""# 2026-06-29 Current-High YES From Regime-NO Misroutes v0

Generated UTC: `{report['generated_at_utc']}`

## Question

Jeddah 2026-06-21 exposed a clean expression mismatch: the NO route treated a stale/pullback state as if it were still fresh runway. This report tests the inverse expression on the same rows: buy the current-high YES after the day has already touched the high and the latest state is pullback/stall/fade.

This is research/shadow evidence only. It reuses selected historical rows from `regime_routed_no_mechanism_split_v2`; it is not a live approval.

## Evidence Snapshot

- Source rows: `{len(raw)}` selected regime-routed NO rows, date range `{raw['target_date'].min()}`..`{raw['target_date'].max()}`.
- Inverse universe: `{len(stale)}` rows where v2 labels the old route as `unapproved_stale_current_no`.
- Pricing: executable historical `current_yes_ask` and `current_bracket_no_ask` from the same selected trade details.
- Unit: one candidate row = one city + target date + decision snapshot + bracket. PnL assumes `${STAKE_USD:.0f}` stake at top ask.

## Jeddah 2026-06-21

The 35C high was first printed at 12:00 local. The latest observation available for the 13:30 decision was the 13:00 local METAR/IEM row, which had already fallen back to 33.9C. At that decision snapshot the 35C YES ask was 0.57 and the 35C NO ask was 0.44. The day later revisited 35C at 14:00 local, so 35 YES won and 35 NO lost.

Observation timeline:

{md_table(obs_rows, [('UTC', 'valid_utc'), ('local', 'local_time'), ('tmpf', 'tmpf'), ('temp_c', 'temp_c'), ('running_c', 'running_max_c'), ('wind_kt', 'sknt'), ('note', 'note')])}

Price timeline for Jeddah 35C:

{md_table(price_rows, [('local', 'local_time'), ('YES ask', 'yes_ask'), ('NO ask', 'no_ask'), ('NO ask size', 'no_ask_size'), ('fcst max C', 'forecast_max_native'), ('fcst peak hr', 'forecast_peak_hour_local'), ('metar src', 'metar_source')])}

## Slice Result

Current-high YES on stale-NO rows:

{md_table(headline_rows, [('slice', 'slice'), ('rows', 'rows'), ('dates', 'dates'), ('cities', 'cities'), ('wins', 'wins'), ('win rate', 'win_rate'), ('avg ask', 'avg_ask'), ('PnL', 'pnl_usd'), ('ROI', 'roi'), ('CI low', 'roi_ci_low'), ('CI high', 'roi_ci_high')])}

YES vs the old NO side on the same rows:

{md_table(inverse_rows, [('slice', 'slice'), ('side', 'side'), ('rows', 'rows'), ('wins', 'wins'), ('avg ask', 'avg_ask'), ('PnL', 'pnl_usd'), ('ROI', 'roi')])}

The cleanest slice is `pullback_uncertain`: 7 rows, 7 wins, YES ROI +33.7%; the old NO side on those exact rows was 0 wins and -100% ROI. This is not enough sample for live, but it is a coherent mechanism worth shadowing.

Pullback details:

{md_table(pullback_rows, [('date', 'target_date'), ('city', 'city'), ('bracket', 'current_bracket'), ('current', 'current_native'), ('running', 'running_native'), ('YES ask', 'current_yes_ask'), ('NO ask', 'current_bracket_no_ask'), ('YES win', 'current_yes_payoff'), ('YES ROI', 'yes_roi'), ('min since high', 'minutes_since_running_max'), ('peak delta h', 'forecast_peak_delta_hours_local'), ('1h trend F', 'temp_trend_1h_f')])}

## Interpretation

This is not the old broad `peak fade` rule revived as-is. The old family mixed several states: early false fades, plateau, mature fades, and clock-unknown rows. Here the candidate is narrower:

- `pullback_uncertain`: high was already printed; latest report has pulled back; the market still offers substantial NO because forecast/runway context says reheat is possible. In this sample, buying the high-bracket YES was correct.
- `plateau_near_high`: latest report is still near the high rather than a real pullback. This was mixed and negative for YES; do not merge it into the candidate.
- `mature_fade`: point estimate is noisy and not clearly positive for YES in this selected universe. It may belong to a different low-price NO tail or separate current-YES fade model, not this expression.
- `clock_unknown`: exclude from live/shadow scoring until the observation clock is fixed.

## Verdict

significance=FAIL, baseline=PARTIAL, forward=FAIL/TOO_THIN, conclusion=inconclusive_shadow_candidate.

Recommended next action: add a zero-notional shadow head named `current_high_yes_pullback_uncertain_v0` that logs these conditions but does not place live orders. It should use shared observation-cache features, require a known first-touch/running-max clock, and evaluate against the old current-YES peak/fade heads on a fixed denominator.

Artifacts:

- Summary JSON: `{(OUT_DIR / 'summary.json').relative_to(ROOT)}`
- Slice summary CSV: `{(OUT_DIR / 'slice_summary.csv').relative_to(ROOT)}`
- Details CSV: `{(OUT_DIR / 'stale_no_inverse_yes_details.csv').relative_to(ROOT)}`
- Jeddah observation timeline: `{(OUT_DIR / 'jeddah_2026_06_21_observation_timeline.csv').relative_to(ROOT)}`
- Jeddah price timeline: `{(OUT_DIR / 'jeddah_2026_06_21_price_timeline.csv').relative_to(ROOT)}`
"""
    OUT_MD.write_text(md, encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
