#!/usr/bin/env python3
"""Mechanism replay for pullback_uncertain current-high YES candidates."""

from __future__ import annotations

import csv
import glob
import io
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from weather_data_feed.observation_sources.iem import build_iem_asos_params


DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_mechanism_split_v2/trade_details.csv"
SNAPSHOT_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/pullback_uncertain_current_high_yes_mechanism_v0"
IEM_CACHE = OUT_DIR / "iem_case_cache"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-pullback-uncertain-current-high-yes-mechanism-v0.md"

LOCAL_IEM_CACHE_DIRS = [
    ROOT / "docs/analysis/2026-06/generated/regime_routed_no_wind_context_v2/iem_direction_cache",
    ROOT / "runtime/rule_source_research/obs_cache",
    ROOT / "runtime/rule_source_research/wl_iem_cache",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_num(value: Any, digits: int = 1) -> str:
    try:
        f = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.{digits}f}"


def fmt_price(value: Any) -> str:
    try:
        f = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.3f}".rstrip("0").rstrip(".")


def pct(value: Any) -> str:
    try:
        f = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f * 100:+.1f}%"


def boolish(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    out = numeric.fillna(0).astype(float) > 0
    text_mask = numeric.isna()
    if text_mask.any():
        out.loc[text_mask] = series.loc[text_mask].astype(str).str.lower().isin({"true", "1", "1.0", "yes"})
    return out


def half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def parse_bracket(bracket: Any) -> tuple[int, int]:
    text = str(bracket).strip()
    if text.endswith("+"):
        low = int(text[:-1])
        return low, 10_000
    if "-" in text:
        low, high = text.split("-", 1)
        return int(low), int(high)
    val = int(float(text))
    return val, val


def native_temp_from_tmpf(tmpf: Any, unit: str) -> float:
    f = float(tmpf)
    if unit == "F":
        return f
    return (f - 32.0) * 5.0 / 9.0


def bucket_for_temp(native_temp: float, unit: str) -> int:
    # Weather temperature markets here settle by integer bracket.  Use half-up
    # instead of Python banker's round so .5 sits on the intuitive boundary.
    return half_up(native_temp)


def load_selected() -> pd.DataFrame:
    raw = pd.read_csv(DETAILS, low_memory=False)
    mask = boolish(raw["mechanism_unapproved_stale_current_no"]) & raw["intraday_state"].eq("pullback_uncertain")
    selected = raw[mask].copy()
    selected["decision_dt_utc"] = pd.to_datetime(selected["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    return selected.sort_values(["target_date", "city"])


def load_local_iem_cache(icao: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
    frames = []
    for folder in LOCAL_IEM_CACHE_DIRS:
        patterns = [
            str(folder / f"iem_ext_{icao}_*.csv"),
            str(folder / f"iem_{icao}_*.csv"),
            str(folder / f"iem_v2_{icao}_*.csv"),
        ]
        for pattern in patterns:
            for path in glob.glob(pattern):
                try:
                    df = pd.read_csv(path)
                except Exception:
                    continue
                if "valid" not in df.columns:
                    continue
                frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).drop_duplicates()
    valid = pd.to_datetime(out["valid"], utc=True, errors="coerce")
    mask = valid.between(start_utc - timedelta(hours=2), end_utc + timedelta(hours=2))
    return out[mask].copy()


def fetch_iem_station(icao: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
    IEM_CACHE.mkdir(parents=True, exist_ok=True)
    path = IEM_CACHE / f"iem_{icao}_{start_utc.date()}_{end_utc.date()}.csv"
    if path.exists() and path.stat().st_size > 0:
        cached = pd.read_csv(path)
        if iem_coverage_ok(cached, start_utc, end_utc):
            return cached
    local = load_local_iem_cache(icao, start_utc, end_utc)
    if not local.empty and "tmpf" in local.columns and iem_coverage_ok(local, start_utc, end_utc):
        local.to_csv(path, index=False)
        return local
    params = build_iem_asos_params(
        icao,
        start_utc,
        end_utc,
        columns=("tmpf", "dwpf", "relh", "drct", "sknt", "skyc1"),
    )
    resp = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", params=params, timeout=30)
    resp.raise_for_status()
    lines = [line for line in resp.text.splitlines() if line.strip() and not line.startswith("#")]
    df = pd.DataFrame(list(csv.DictReader(io.StringIO("\n".join(lines))))) if lines else pd.DataFrame()
    df.to_csv(path, index=False)
    return df


def iem_coverage_ok(df: pd.DataFrame, start_utc: datetime, end_utc: datetime) -> bool:
    if df.empty or "valid" not in df.columns or "tmpf" not in df.columns:
        return False
    valid = pd.to_datetime(df["valid"], utc=True, errors="coerce").dropna()
    if valid.empty:
        return False
    return bool(valid.min() <= pd.Timestamp(start_utc) + pd.Timedelta(hours=2) and valid.max() >= pd.Timestamp(end_utc) - pd.Timedelta(hours=4))


def normalize_obs(df: pd.DataFrame, row: pd.Series, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["valid_utc"] = pd.to_datetime(out["valid"], utc=True, errors="coerce")
    out = out[out["valid_utc"].between(start_utc, end_utc)].copy()
    for col in ["tmpf", "dwpf", "relh", "drct", "sknt"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col].replace({"M": np.nan, "": np.nan}), errors="coerce")
    out = out.dropna(subset=["valid_utc", "tmpf"]).sort_values("valid_utc")
    unit = str(row["unit"])
    low, high = parse_bracket(row["current_bracket"])
    tz = ZoneInfo(str(row["timezone"]))
    out["target_date"] = str(row["target_date"])
    out["city"] = str(row["city"])
    out["icao"] = str(row["icao"])
    out["local_time"] = out["valid_utc"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d %H:%M")
    out["native_temp"] = out["tmpf"].map(lambda x: native_temp_from_tmpf(x, unit))
    out["native_bucket"] = out["native_temp"].map(lambda x: bucket_for_temp(x, unit))
    out["hit_current_bracket"] = out["native_bucket"].between(low, high)
    out["above_current_bracket"] = out["native_bucket"] > high
    out["running_native_max"] = out["native_temp"].cummax()
    return out


def case_obs_window(row: pd.Series) -> tuple[datetime, datetime]:
    tz = ZoneInfo(str(row["timezone"]))
    target_date = datetime.fromisoformat(str(row["target_date"])).date()
    local_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, tzinfo=tz)
    local_end = local_start + timedelta(hours=23, minutes=59)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def load_price_timeline(row: pd.Series) -> pd.DataFrame:
    target = datetime.fromisoformat(str(row["target_date"])).date()
    candidate_dates = {target - timedelta(days=1), target, target + timedelta(days=1)}
    rows: list[dict[str, Any]] = []
    for day in sorted(candidate_dates):
        for path in sorted(SNAPSHOT_DIR.glob(f"snapshot_{day:%Y%m%d}_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for rec in payload.get("records", []):
                if str(rec.get("city")) != str(row["city"]):
                    continue
                if str(rec.get("target_date")) != str(row["target_date"]):
                    continue
                if str(rec.get("bracket")) != str(row["current_bracket"]):
                    continue
                ts = parse_utc(rec.get("ts_utc") or rec.get("snapshot_ts_utc") or payload.get("ts_utc"))
                local = ts.astimezone(ZoneInfo(str(row["timezone"])))
                rows.append(
                    {
                        "target_date": str(row["target_date"]),
                        "city": str(row["city"]),
                        "bracket": str(row["current_bracket"]),
                        "snapshot_file": path.name,
                        "ts_utc": ts.isoformat(),
                        "local_time": local.strftime("%Y-%m-%d %H:%M:%S"),
                        "yes_ask": rec.get("yes_best_ask"),
                        "yes_bid": rec.get("yes_best_bid"),
                        "no_ask": rec.get("no_best_ask"),
                        "no_bid": rec.get("no_best_bid"),
                        "no_ask_size": rec.get("no_ask_size"),
                        "forecast_max_native": rec.get("forecast_max_native"),
                        "forecast_peak_hour_local": rec.get("forecast_peak_hour_local"),
                        "metar_latest_ts_utc": rec.get("metar_latest_ts_utc"),
                        "metar_latest_temp_f": rec.get("metar_latest_temp_f"),
                        "metar_current_max_f": rec.get("metar_current_max_f"),
                        "metar_source": rec.get("metar_source"),
                    }
                )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["ts_utc_dt"] = pd.to_datetime(out["ts_utc"], utc=True, errors="coerce")
    decision = pd.Timestamp(row["decision_snapshot_ts_utc"])
    if decision.tzinfo is None:
        decision = decision.tz_localize("UTC")
    else:
        decision = decision.tz_convert("UTC")
    out = out[out["ts_utc_dt"].between(decision - pd.Timedelta(hours=3), decision + pd.Timedelta(hours=3))]
    return out.sort_values("ts_utc_dt").drop(columns=["ts_utc_dt"])


def summarize_case(row: pd.Series, obs: pd.DataFrame, prices: pd.DataFrame) -> dict[str, Any]:
    decision = pd.Timestamp(row["decision_snapshot_ts_utc"])
    if decision.tzinfo is None:
        decision = decision.tz_localize("UTC")
    else:
        decision = decision.tz_convert("UTC")
    tz = ZoneInfo(str(row["timezone"]))
    before = obs[obs["valid_utc"].le(decision)] if not obs.empty else obs
    after = obs[obs["valid_utc"].gt(decision)] if not obs.empty else obs
    first_hit = obs[obs["hit_current_bracket"]].head(1)
    latest = before.tail(1)
    after_equal = after[after["hit_current_bracket"]]
    after_above = after[after["above_current_bracket"]]
    after_max_bucket = int(after["native_bucket"].max()) if not after.empty else None
    after_max_native = float(after["native_temp"].max()) if not after.empty else None
    low, high = parse_bracket(row["current_bracket"])
    no_decision = float(row["current_bracket_no_ask"])
    yes_decision = float(row["current_yes_ask"])
    no_requires = "final bucket outside current bracket"
    final_same_bracket = str(row["final_winning_bracket"]) == str(row["current_bracket"])
    if first_hit.empty:
        mechanism = "source_gap_observation_path"
    elif not after_above.empty:
        mechanism = "break_above_after_pullback"
    elif final_same_bracket and not after_equal.empty:
        mechanism = "same_bracket_revisit_risk"
    elif final_same_bracket:
        mechanism = "already_printed_high_holds"
    else:
        mechanism = "unexplained_or_source_gap"
    return {
        "target_date": str(row["target_date"]),
        "city": str(row["city"]),
        "unit": str(row["unit"]),
        "bracket": str(row["current_bracket"]),
        "decision_local": decision.to_pydatetime().astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "latest_obs_local": "" if latest.empty else latest.iloc[0]["local_time"],
        "latest_obs_native": None if latest.empty else float(latest.iloc[0]["native_temp"]),
        "latest_obs_bucket": None if latest.empty else int(latest.iloc[0]["native_bucket"]),
        "first_hit_local": "" if first_hit.empty else first_hit.iloc[0]["local_time"],
        "first_hit_native": None if first_hit.empty else float(first_hit.iloc[0]["native_temp"]),
        "first_hit_bucket": None if first_hit.empty else int(first_hit.iloc[0]["native_bucket"]),
        "after_equal_revisit_local": "" if after_equal.empty else after_equal.iloc[0]["local_time"],
        "after_above_local": "" if after_above.empty else after_above.iloc[0]["local_time"],
        "after_max_native": after_max_native,
        "after_max_bucket": after_max_bucket,
        "final_max_native": float(row["final_max_native"]),
        "final_winning_bracket": str(row["final_winning_bracket"]),
        "yes_ask": yes_decision,
        "no_ask": no_decision,
        "yes_roi": 1.0 / yes_decision - 1.0,
        "no_roi": -1.0,
        "forecast_peak_delta_hours_local": float(row["forecast_peak_delta_hours_local"]),
        "forecast_gap_to_running_native": float(row["forecast_gap_to_running_native"]),
        "temp_trend_1h_f": float(row["temp_trend_1h_f"]),
        "temp_trend_3h_f": float(row["temp_trend_3h_f"]),
        "mechanism": mechanism,
        "no_requires": no_requires,
        "price_rows": int(len(prices)),
        "obs_rows": int(len(obs)),
        "bracket_low": low,
        "bracket_high": high,
    }


def timeline_string(obs: pd.DataFrame, row: pd.Series) -> str:
    if obs.empty:
        return "missing_obs"
    decision = pd.Timestamp(row["decision_snapshot_ts_utc"])
    if decision.tzinfo is None:
        decision = decision.tz_localize("UTC")
    else:
        decision = decision.tz_convert("UTC")
    low, high = parse_bracket(row["current_bracket"])
    relevant = obs.copy()
    relevant["near"] = relevant["native_bucket"].between(max(-999, low - 2), min(9999, high + 2))
    relevant = relevant[(relevant["near"]) | (relevant["valid_utc"].between(decision - pd.Timedelta(hours=2), decision + pd.Timedelta(hours=3)))]
    parts = []
    for _, r in relevant.iterrows():
        marker = ""
        if r["valid_utc"] <= decision and (decision - r["valid_utc"]) <= pd.Timedelta(minutes=70):
            marker = " latest"
        if bool(r["hit_current_bracket"]):
            marker += " hit"
        if bool(r["above_current_bracket"]):
            marker += " above"
        local_hm = str(r["local_time"])[11:16]
        parts.append(f"{local_hm} {fmt_num(r['native_temp'], 1)}({int(r['native_bucket'])}){marker}".strip())
    return " -> ".join(parts)


def md_table(rows: list[dict[str, Any]], columns: list[tuple[str, str, str]]) -> str:
    lines = ["| " + " | ".join(label for label, _key, _kind in columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        vals = []
        for _label, key, kind in columns:
            value = row.get(key)
            if kind == "price":
                vals.append(fmt_price(value))
            elif kind == "num":
                vals.append(fmt_num(value))
            elif kind == "pct":
                vals.append(pct(value))
            else:
                vals.append("" if value is None else str(value))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def json_ready(value: Any) -> Any:
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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    selected = load_selected()
    summaries: list[dict[str, Any]] = []
    obs_frames: list[pd.DataFrame] = []
    price_frames: list[pd.DataFrame] = []
    timeline_rows: list[dict[str, Any]] = []

    for _, row in selected.iterrows():
        start_utc, end_utc = case_obs_window(row)
        raw_obs = fetch_iem_station(str(row["icao"]), start_utc, end_utc)
        obs = normalize_obs(raw_obs, row, start_utc, end_utc)
        prices = load_price_timeline(row)
        summary = summarize_case(row, obs, prices)
        summary["obs_timeline"] = timeline_string(obs, row)
        summaries.append(summary)
        if not obs.empty:
            obs_frames.append(obs.assign(decision_snapshot_ts_utc=row["decision_snapshot_ts_utc"]))
        if not prices.empty:
            price_frames.append(prices)
        timeline_rows.append(
            {
                "target_date": row["target_date"],
                "city": row["city"],
                "bracket": row["current_bracket"],
                "timeline": summary["obs_timeline"],
            }
        )

    summary_df = pd.DataFrame(summaries)
    summary_df.to_csv(OUT_DIR / "case_summary.csv", index=False)
    if obs_frames:
        pd.concat(obs_frames, ignore_index=True).to_csv(OUT_DIR / "observation_timelines.csv", index=False)
    if price_frames:
        pd.concat(price_frames, ignore_index=True).to_csv(OUT_DIR / "price_timelines.csv", index=False)
    pd.DataFrame(timeline_rows).to_csv(OUT_DIR / "compact_observation_timelines.csv", index=False)

    pattern_summary = (
        summary_df.groupby("mechanism", as_index=False)
        .agg(rows=("city", "count"), yes_roi_avg=("yes_roi", "mean"), avg_yes_ask=("yes_ask", "mean"), avg_no_ask=("no_ask", "mean"))
        .sort_values("rows", ascending=False)
    )
    pattern_summary.to_csv(OUT_DIR / "pattern_summary.csv", index=False)

    report = {
        "generated_at_utc": now_utc(),
        "source_details": str(DETAILS.relative_to(ROOT)),
        "rows": int(len(summary_df)),
        "pattern_summary": pattern_summary.to_dict("records"),
        "case_summary": summary_df.to_dict("records"),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(json_ready(report), ensure_ascii=False, indent=2), encoding="utf-8")

    overview_cols = [
        ("date", "target_date", "str"),
        ("city", "city", "str"),
        ("bracket", "bracket", "str"),
        ("first hit", "first_hit_local", "str"),
        ("latest obs", "latest_obs_local", "str"),
        ("latest temp", "latest_obs_native", "num"),
        ("after same", "after_equal_revisit_local", "str"),
        ("after above", "after_above_local", "str"),
        ("after max", "after_max_native", "num"),
        ("YES ask", "yes_ask", "price"),
        ("NO ask", "no_ask", "price"),
        ("YES ROI", "yes_roi", "pct"),
        ("mechanism", "mechanism", "str"),
    ]
    timeline_cols = [
        ("date", "target_date", "str"),
        ("city", "city", "str"),
        ("bracket", "bracket", "str"),
        ("obs path local: temp(bucket)", "timeline", "str"),
    ]
    pattern_cols = [
        ("mechanism", "mechanism", "str"),
        ("rows", "rows", "str"),
        ("avg YES ask", "avg_yes_ask", "price"),
        ("avg NO ask", "avg_no_ask", "price"),
        ("avg YES ROI", "yes_roi_avg", "pct"),
    ]
    md = f"""# 2026-06-29 Pullback-Uncertain Current-High YES Mechanism v0

Generated UTC: `{report['generated_at_utc']}`

## Question

The prior inverse test found `pullback_uncertain` current-high YES had 7 wins in 7 historical rows. This report checks whether that is a real mechanism or a coincidence by replaying each row's observation path and same-bracket orderbook path where available.

Key distinction: current-bracket NO after a pullback does **not** only need "no break above". It needs the final winning bracket to be outside the current bracket. A later equal-bracket revisit is enough to kill NO and make current-high YES win.

## Case Summary

{md_table(summary_df.to_dict('records'), overview_cols)}

## Pattern Summary

{md_table(pattern_summary.to_dict('records'), pattern_cols)}

## Compact Observation Paths

`temp(bucket)` uses the market's native unit. `latest` marks the observation available near the decision snapshot; `hit` means the current bracket was touched; `above` means the day later moved to a higher bracket.

{md_table(timeline_rows, timeline_cols)}

## Mechanism Read

- `already_printed_high_holds` means the current bracket had already been officially printed before decision; no later same-bracket revisit was required. YES wins if no higher bracket appears.
- `same_bracket_revisit_risk` is the Jeddah-style version: the day had already printed the high bracket, then pulled back, and later revisited the same bracket without breaking above. That is enough for current-high YES and fatal for NO.
- In the 6 rows with usable observation path, none required a higher-bracket break for YES to win. The real risk to YES was only a later higher bracket, not the absence of reheat. TelAviv 2026-06-11 has settlement/feature evidence but incomplete IEM path in this replay, so it stays a source-gap row.
- This means the 35 NO at 0.44 in Jeddah was not obviously rich. It was priced against a high probability that 35C remained/revisited; forecast peak near 13:00 did not remove equal-bracket revisit risk.
- The expression is different from broad peak fade: it should target "already printed high bracket + observed pullback + no evidence of higher-bracket break", not all stalled/fade states.

## Data Caveats

- Observation timelines are research IEM/METAR replays, not proof that every row was live-visible at that exact second. A live shadow head must use the shared observation cache and mark missing fields as unknown.
- Same-bracket price timelines are only available for a subset of these rows in the current paper-snapshot mirror. Decision-time YES/NO asks come from the selected trade-details evidence layer for all 7 rows.
- Sample size is 7 rows. This is a mechanism candidate, not a live-ready strategy.

Artifacts:

- Case summary: `{(OUT_DIR / 'case_summary.csv').relative_to(ROOT)}`
- Observation timelines: `{(OUT_DIR / 'observation_timelines.csv').relative_to(ROOT)}`
- Price timelines: `{(OUT_DIR / 'price_timelines.csv').relative_to(ROOT)}`
- Compact timelines: `{(OUT_DIR / 'compact_observation_timelines.csv').relative_to(ROOT)}`
"""
    OUT_MD.write_text(md, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
