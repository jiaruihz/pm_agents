#!/usr/bin/env python3
"""Wind-context research for regime-routed NO.

This extends the coarse wind-speed sensitivity with PIT wind direction and
static city geography labels.  It is a research artifact, not a live rule.
"""

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
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_regime_routed_no_expression_v1 as routed  # noqa: E402
from weather_data_feed.observation_sources.iem import build_iem_asos_params  # noqa: E402
from weather_data_feed.weather_context import city_wind_context  # noqa: E402
from weather_data_feed.source_policy import load_city_configs  # noqa: E402


SELECTED = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_wind_context_v2"
IEM_CACHE = OUT_DIR / "iem_direction_cache"
LOCAL_IEM_CACHE_DIRS = [
    ROOT / "runtime/rule_source_research/obs_cache",
    ROOT / "runtime/rule_source_research/wl_iem_cache",
]
OUT_JSON = OUT_DIR / "summary.json"
OUT_DETAILS = OUT_DIR / "selected_with_wind_context.csv"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_CONTEXT = OUT_DIR / "context_summary.csv"
OUT_CITY = OUT_DIR / "city_context_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-regime-routed-no-wind-context-v2.md"

MAIN_VARIANT = "routed_capped_d2_no_relaxed70_best_ask"
BASE_NOTIONAL_USD = 5.0
MIN_ORDER_SHARES = 5.0


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def parse_utc(value: Any) -> datetime | None:
    if value in {None, "", np.nan}:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_iem_station(icao: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
    IEM_CACHE.mkdir(parents=True, exist_ok=True)
    path = IEM_CACHE / f"iem_{icao}_{start_utc.date()}_{end_utc.date()}.csv"
    if path.exists() and path.stat().st_size > 0:
        return pd.read_csv(path)
    local = load_local_iem_cache(icao, start_utc, end_utc)
    if not local.empty and {"valid", "drct", "sknt"}.issubset(local.columns):
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


def load_local_iem_cache(icao: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
    frames = []
    for folder in LOCAL_IEM_CACHE_DIRS:
        patterns = [
            str(folder / f"iem_ext_{icao}_*.csv"),
            str(folder / f"iem_{icao}_*.csv"),
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
    mask = valid.between(start_utc - timedelta(days=1), end_utc + timedelta(days=1))
    return out[mask].copy()


def normalize_iem(df: pd.DataFrame, icao: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["icao"] = icao
    out["valid_utc"] = pd.to_datetime(out["valid"], utc=True, errors="coerce")
    for col in ["tmpf", "dwpf", "relh", "drct", "sknt"]:
        if col in out:
            out[col] = pd.to_numeric(out[col].replace({"M": np.nan, "": np.nan}), errors="coerce")
    return out.dropna(subset=["valid_utc"]).sort_values("valid_utc")


def add_static_context(row: pd.Series) -> dict[str, Any]:
    return city_wind_context(str(row.get("city") or ""), row.get("wind_dir_deg"))


def load_selected() -> pd.DataFrame:
    raw = pd.read_csv(SELECTED, low_memory=False)
    return raw[raw["variant"].eq(MAIN_VARIANT)].copy()


def add_iem_direction(selected: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    configs = {cfg.city: cfg for cfg in load_city_configs(include_station_diff=True)}
    start = parse_utc(selected["decision_snapshot_ts_utc"].min()) - timedelta(days=1)
    end = parse_utc(selected["decision_snapshot_ts_utc"].max()) + timedelta(days=1)
    assert start is not None and end is not None
    station_frames = {}
    fetch_status: dict[str, str] = {}
    for city in sorted(selected["city"].dropna().astype(str).unique()):
        cfg = configs.get(city)
        if cfg is None:
            fetch_status[city] = "missing_city_config"
            continue
        try:
            station_frames[city] = normalize_iem(fetch_iem_station(cfg.official_icao, start, end), cfg.official_icao)
            fetch_status[city] = f"ok:{len(station_frames[city])}"
        except Exception as exc:  # noqa: BLE001
            station_frames[city] = pd.DataFrame()
            fetch_status[city] = f"fetch_error:{type(exc).__name__}:{exc}"

    rows = []
    for _, row in selected.iterrows():
        city = str(row["city"])
        asof = parse_utc(row.get("decision_snapshot_ts_utc"))
        df = station_frames.get(city, pd.DataFrame())
        out = row.to_dict()
        if asof is None or df.empty:
            out.update({"wind_context_join_status": "missing_iem_or_asof", "wind_dir_deg": math.nan, "wind_iem_sknt": math.nan})
            rows.append(out)
            continue
        prior = df[df["valid_utc"].le(asof)]
        if prior.empty:
            out.update({"wind_context_join_status": "no_prior_iem_obs", "wind_dir_deg": math.nan, "wind_iem_sknt": math.nan})
            rows.append(out)
            continue
        obs = prior.iloc[-1]
        age_min = (asof - obs["valid_utc"].to_pydatetime()).total_seconds() / 60.0
        out.update(
            {
                "wind_context_join_status": "matched_iem_asof",
                "wind_iem_obs_utc": obs["valid_utc"].isoformat(),
                "wind_iem_age_min": age_min,
                "wind_dir_deg": obs.get("drct"),
                "wind_iem_sknt": obs.get("sknt"),
            }
        )
        rows.append(out)
    enriched = pd.DataFrame(rows)
    static = enriched.apply(add_static_context, axis=1, result_type="expand")
    enriched = pd.concat([enriched, static], axis=1)
    return enriched, fetch_status


def add_weights(frame: pd.DataFrame) -> pd.DataFrame:
    out = routed.add_soft_weights(frame).copy()
    wind = pd.to_numeric(out["wind_iem_sknt"].fillna(out["wind_speed_kt"]), errors="coerce")
    out["wind_context_multiplier"] = np.select(
        [
            wind.ge(18) & out["coastal_flow_state"].eq("onshore_marine_flow"),
            wind.ge(18) & out["geo_context"].astype(str).str.startswith("coastal"),
            wind.ge(18),
            wind.ge(10) & out["coastal_flow_state"].eq("onshore_marine_flow"),
            wind.ge(10),
        ],
        [0.70, 0.80, 0.88, 0.90, 0.96],
        default=1.00,
    )
    out["soft_wind_context"] = (
        pd.to_numeric(out["soft_balanced"], errors="coerce")
        * pd.to_numeric(out["wind_context_multiplier"], errors="coerce")
    ).clip(0, 1)
    out["soft_wind_only"] = (
        pd.to_numeric(out["soft_balanced"], errors="coerce")
        * np.select([wind.ge(18), wind.ge(10)], [0.80, 0.95], default=1.00)
    ).clip(0, 1)
    return out


def summarize(frame: pd.DataFrame, weight_col: str) -> dict[str, Any]:
    clean = frame[frame["payoff"].notna()].copy()
    weight = pd.to_numeric(clean[weight_col], errors="coerce").fillna(0).clip(lower=0)
    ask = pd.to_numeric(clean["ask"], errors="coerce")
    executed = (BASE_NOTIONAL_USD * weight / ask).ge(MIN_ORDER_SHARES)
    cost = float((pd.to_numeric(clean["stake_cost_usd"], errors="coerce") * weight).sum())
    pnl = float((pd.to_numeric(clean["stake_profit_usd"], errors="coerce") * weight).sum())
    exec_cost = float((pd.to_numeric(clean.loc[executed, "stake_cost_usd"], errors="coerce") * weight[executed]).sum())
    exec_pnl = float((pd.to_numeric(clean.loc[executed, "stake_profit_usd"], errors="coerce") * weight[executed]).sum())
    return {
        "weight_policy": weight_col,
        "rows": int(len(clean)),
        "dates": int(clean["target_date"].nunique()),
        "cities": int(clean["city"].nunique()),
        "exec_rows": int(executed.sum()),
        "exec_dates": int(clean.loc[executed, "target_date"].nunique()),
        "cost_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": pnl / cost if cost else None,
        "exec_cost_usd": round(exec_cost, 6),
        "exec_pnl_usd": round(exec_pnl, 6),
        "exec_roi": exec_pnl / exec_cost if exec_cost else None,
        "avg_weight": float(weight.mean()) if len(weight) else None,
    }


def summarize_group(frame: pd.DataFrame, group_col: str) -> pd.DataFrame:
    rows = []
    for key, group in frame.groupby(group_col, dropna=False):
        row = summarize(group, "soft_wind_context")
        row[group_col] = str(key)
        row["hit_rate"] = float(pd.to_numeric(group["payoff"], errors="coerce").mean()) if len(group) else None
        rows.append(row)
    return pd.DataFrame(rows)


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col)
            if isinstance(val, float):
                if col in {"roi", "exec_roi", "avg_weight", "hit_rate"}:
                    vals.append(pct(val))
                elif "usd" in col:
                    vals.append(f"{val:+.2f}")
                else:
                    vals.append(f"{val:.2f}")
            elif val is None:
                vals.append("NA")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    selected = load_selected()
    with_dir, fetch_status = add_iem_direction(selected)
    scored = add_weights(with_dir)

    policies = pd.DataFrame(
        [
            summarize(scored, "full_size"),
            summarize(scored, "soft_balanced"),
            summarize(scored, "soft_wind_only"),
            summarize(scored, "soft_wind_context"),
        ]
    )
    context_frames = []
    for col in ["geo_context", "coastal_flow_state", "wind_sector", "city_family"]:
        context_frames.append(summarize_group(scored, col).assign(group_col=col).rename(columns={col: "group_value"}))
    context = pd.concat(context_frames, ignore_index=True)
    city = summarize_group(scored, "city").sort_values("pnl_usd")

    scored.to_csv(OUT_DETAILS, index=False)
    policies.to_csv(OUT_POLICY, index=False)
    context.to_csv(OUT_CONTEXT, index=False)
    city.to_csv(OUT_CITY, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "regime_routed_no_wind_context_v2",
        "variant": MAIN_VARIANT,
        "rows": int(len(scored)),
        "date_min": str(scored["target_date"].min()) if len(scored) else None,
        "date_max": str(scored["target_date"].max()) if len(scored) else None,
        "wind_direction_coverage_rows": int(scored["wind_context_join_status"].eq("matched_iem_asof").sum()),
        "fetch_status": fetch_status,
        "policy_summary": policies.to_dict("records"),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "details": str(OUT_DETAILS.relative_to(ROOT)),
            "policy_summary": str(OUT_POLICY.relative_to(ROOT)),
            "context_summary": str(OUT_CONTEXT.relative_to(ROOT)),
            "city_summary": str(OUT_CITY.relative_to(ROOT)),
            "report_md": str(OUT_MD.relative_to(ROOT)),
        },
        "boundary": "research_only_not_live_rule; static onshore sectors are hand-labeled first-principles approximations",
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")

    policy_rows = policies.to_dict("records")
    context_rows = context.sort_values(["group_col", "rows"], ascending=[True, False]).groupby("group_col").head(8).to_dict("records")
    text = "\n".join(
        [
            "# Regime-Routed NO Wind Context V2",
            "",
            f"Generated: `{payload['generated_at_utc']}`",
            "",
            "## Verdict",
            "",
            "This is a research-only wind-context test.  It adds point-in-time IEM wind direction, a hand-labeled city geography class, and a coastal onshore/offshore approximation to the existing regime-routed NO denominator.",
            "",
            "Main read: a full wind model is scientifically cleaner than raw wind speed, but the current evidence still supports only telemetry/shadow sizing.  The direction/geography labels are first-principles approximations, not a confirmed edge.",
            "",
            f"Coverage: `{payload['wind_direction_coverage_rows']}` / `{payload['rows']}` rows matched an as-of IEM wind direction.",
            "",
            "## Policy Summary",
            "",
            md_table(
                policy_rows,
                [
                    "weight_policy",
                    "rows",
                    "dates",
                    "cities",
                    "exec_rows",
                    "exec_dates",
                    "cost_usd",
                    "pnl_usd",
                    "roi",
                    "exec_cost_usd",
                    "exec_pnl_usd",
                    "exec_roi",
                    "avg_weight",
                ],
            ),
            "",
            "## Context Slices",
            "",
            md_table(
                context_rows,
                ["group_col", "group_value", "rows", "dates", "cities", "exec_rows", "cost_usd", "pnl_usd", "roi", "exec_roi", "hit_rate", "avg_weight"],
            ),
            "",
            "## Boundary",
            "",
            "- Static onshore sectors are hand-labeled mechanism approximations.  They now live in `weather_data_feed.weather_context` so research and runner telemetry share the same definitions.",
            "- `soft_wind_context` is intentionally conservative and exploratory; it is not optimized and not live-approved.",
            "- Runner integration should remain telemetry/shadow-only until frozen replay confirms that live-available wind direction coverage is stable.",
            "",
        ]
    )
    OUT_MD.write_text(text, encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
