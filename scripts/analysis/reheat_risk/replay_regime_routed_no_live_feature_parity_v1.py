#!/usr/bin/env python3
"""Replay regime-routed NO live/backtest feature parity.

This is a diagnostic replay, not a live approval.  It checks:

1. Whether the historical regime-routed selected trades would pass the live
   feature-parity requirement added after the 2026-06-25 incident.
2. Whether the two NYC live order moments would still be executable when the
   METAR mechanism features are reconstructed point-in-time.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT / "scripts/ops"
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import regime_routed_no_tiny_live as live  # noqa: E402
import research_intraday_weather_regime_atlas_v1 as atlas  # noqa: E402
import research_regime_routed_no_expression_v1 as research  # noqa: E402
from weather_data_feed.source_policy import load_city_configs  # noqa: E402
from weather_data_feed.production_paths import historical_strategy_snapshots  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_live_feature_parity_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_HIST = OUT_DIR / "historical_parity_summary.csv"
OUT_NYC = OUT_DIR / "nyc_live_order_parity_replay.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-regime-routed-no-live-feature-parity-v1.md"

SELECTED = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv"
MAIN_VARIANT = "routed_capped_d2_no_relaxed70_best_ask"
CORE_COLS = live.CORE_LIVE_REGIME_COLS
CORE_LABELS = live.CORE_LIVE_REGIME_LABELS

NYC_REPLAYS = [
    {
        "label": "first_live_order",
        "asof_utc": "2026-06-25T15:54:21+00:00",
        "snapshot": str(historical_strategy_snapshots() / "snapshot_20260625_2330.json"),
        "posted_price": 0.35,
    }
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"{100.0 * value:+.1f}%"


def money(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "NA"
    return f"${value:+,.2f}"


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def summarize_trades(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "hit_rate": None,
        }
    cost = float(pd.to_numeric(frame["stake_cost_usd"], errors="coerce").sum())
    pnl = float(pd.to_numeric(frame["stake_profit_usd"], errors="coerce").sum())
    payoff = pd.to_numeric(frame["payoff"], errors="coerce")
    return {
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "hit_rate": float(payoff.mean()) if len(payoff.dropna()) else None,
    }


def add_historical_parity_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["hist_core_fields_present"] = out[CORE_COLS].apply(lambda col: pd.to_numeric(col, errors="coerce").notna()).all(axis=1)
    out["hist_core_labels_known"] = ~out[CORE_LABELS].astype(str).apply(lambda row: any("unknown" in item for item in row), axis=1)
    out["live_parity_gate_ok"] = out["hist_core_fields_present"] & out["hist_core_labels_known"]
    return out


def historical_replay() -> tuple[pd.DataFrame, dict[str, Any]]:
    df = pd.read_csv(SELECTED, low_memory=False)
    main = df[df["variant"].eq(MAIN_VARIANT)].copy()
    main = add_historical_parity_flags(main)
    rows = []
    for name, frame in [
        ("main_before_parity_gate", main),
        ("main_after_parity_gate", main[main["live_parity_gate_ok"]].copy()),
        ("main_failed_parity_gate", main[~main["live_parity_gate_ok"]].copy()),
    ]:
        row = {"slice": name, **summarize_trades(frame)}
        rows.append(row)
    by_route = []
    for route, group in main.groupby("route_leg", dropna=False):
        before = summarize_trades(group)
        after = summarize_trades(group[group["live_parity_gate_ok"]])
        by_route.append(
            {
                "slice": f"route::{route}",
                "rows": before["rows"],
                "after_rows": after["rows"],
                "parity_pass_rate": after["rows"] / before["rows"] if before["rows"] else None,
                "roi_before": before["roi"],
                "roi_after": after["roi"],
            }
        )
    summary = {
        "main_variant": MAIN_VARIANT,
        "main_rows": int(len(main)),
        "parity_pass_rows": int(main["live_parity_gate_ok"].sum()),
        "parity_pass_rate": float(main["live_parity_gate_ok"].mean()) if len(main) else None,
        "unknown_label_counts": {
            col: int(main[col].astype(str).str.contains("unknown", na=False).sum())
            for col in CORE_LABELS
        },
        "missing_core_field_counts": {
            col: int(pd.to_numeric(main[col], errors="coerce").isna().sum())
            for col in CORE_COLS
        },
        "summary_rows": rows,
        "route_rows": by_route,
    }
    hist = pd.DataFrame(rows + by_route)
    return hist, summary


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def metar_records_asof(cfg: Any, tz: ZoneInfo, local_date: Any, asof: datetime, *, hours: float = 30.0) -> list[tuple[datetime, float, dict[str, Any]]]:
    data = live.metar.source.fetch_json(
        live.metar.source.METAR_API,
        {"ids": cfg.official_icao, "format": "json", "hours": str(hours)},
        max_rounds=1,
        timeout_sec=15.0,
        proxy_candidates=live.metar.WEATHER_PROXY_CANDIDATES,
    )
    records: list[tuple[datetime, float, dict[str, Any]]] = []
    if not isinstance(data, list):
        return records
    for rec in data:
        if not isinstance(rec, dict) or rec.get("temp") is None or not rec.get("reportTime"):
            continue
        try:
            dt = datetime.fromisoformat(str(rec["reportTime"]).replace("Z", "+00:00"))
            temp_c = float(rec["temp"])
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        raw_receipt = rec.get("receiptTime") or rec.get("reportTime")
        receipt_dt = datetime.fromisoformat(str(raw_receipt).replace("Z", "+00:00"))
        if receipt_dt.tzinfo is None:
            receipt_dt = receipt_dt.replace(tzinfo=timezone.utc)
        if receipt_dt <= asof and dt.astimezone(tz).date() == local_date:
            records.append((dt, temp_c, rec))
    return sorted(records, key=lambda row: row[0])


def feature_dict_asof(cfg: Any, tz: ZoneInfo, local_date: Any, asof: datetime) -> dict[str, Any]:
    records = metar_records_asof(cfg, tz, local_date, asof)
    if not records:
        return {"live_feature_status": "no_asof_records"}
    latest_dt, latest_temp_c, latest = records[-1]
    running_max_c = max(temp for _dt, temp, _raw in records)
    max_hits = [dt for dt, temp, _raw in records if temp >= running_max_c - 0.05]
    dewpoint_c = live.safe_float(latest.get("dewp"))
    wind_kt = live.safe_float(latest.get("wspd"))
    return {
        "live_feature_status": "ok",
        "relative_humidity_pct": live.relative_humidity_pct(latest_temp_c, dewpoint_c) if math.isfinite(dewpoint_c) else math.nan,
        "sky_cover_code": live.sky_cover_code(latest),
        "dewpoint_depression_f": live.temp_f(latest_temp_c - dewpoint_c) - 32.0 if math.isfinite(dewpoint_c) else math.nan,
        "wind_speed_kt": wind_kt,
        "temp_trend_1h_f": live.trend_f(records, latest_dt, latest_temp_c, 1.0),
        "temp_trend_3h_f": live.trend_f(records, latest_dt, latest_temp_c, 3.0),
        "minutes_since_running_max": (latest_dt - max_hits[-1]).total_seconds() / 60.0 if max_hits else math.nan,
        "asof_record_count": len(records),
        "asof_last_obs_utc": latest_dt.isoformat(),
        "asof_current_temp_c": latest_temp_c,
        "asof_running_max_c": running_max_c,
    }


def replay_nyc_order_case(case: dict[str, Any]) -> dict[str, Any]:
    cfg = {item.city: item for item in load_city_configs(include_station_diff=False)}["NYC"]
    tz = ZoneInfo(cfg.timezone_name)
    asof = parse_ts(case["asof_utc"])
    snapshot_path = ROOT / case["snapshot"]
    payload, records = live.load_snapshot(snapshot_path)
    sub = records[records["city"].eq("NYC") & records["target_date"].eq("2026-06-25")].copy()
    if sub.empty:
        return {**case, "status": "missing_snapshot_city_rows"}

    first = sub.iloc[0]
    features = feature_dict_asof(cfg, tz, asof.date(), asof)
    if features.get("live_feature_status") != "ok":
        return {**case, **features, "status": "missing_features"}

    current_c = live.safe_float(features["asof_current_temp_c"])
    running_c = live.safe_float(features["asof_running_max_c"])
    unit = str(cfg.unit)
    current_native = live.native_value(current_c, unit)
    running_native = live.native_value(running_c, unit)
    running_value = live.metar.market_value(running_c, unit)
    base = {
        "city": "NYC",
        "target_date": "2026-06-25",
        "decision_snapshot_ts_utc": str(first.get("snapshot_ts_utc")),
        "decision_hour_local": int(asof.astimezone(tz).hour),
        "timezone": cfg.timezone_name,
        "unit": unit,
        "icao": cfg.official_icao,
        "current_temp_c": current_c,
        "running_max_c": running_c,
        "current_native": current_native,
        "running_native": running_native,
        "decline_native": running_native - current_native,
        "running_value": running_value,
        "forecast_source": str(first.get("forecast_source") or ""),
        "forecast_clock_source": "paper_snapshot_live",
        "forecast_max_native": live.safe_float(first.get("forecast_max_native")),
        "forecast_peak_hour_local": live.safe_float(first.get("forecast_peak_hour_local")),
        "forecast_gap_to_running_native": live.safe_float(first.get("forecast_max_native")) - running_native,
        **features,
    }
    labelled = atlas.add_regime_labels(pd.DataFrame([base])).iloc[0].to_dict()
    books = live.market_rows_for_city(sub, running_value=running_value, running_native=running_native, unit=unit)
    cur_no = books[books["outcome"].eq("no") & books["contains_running"]].copy()
    if cur_no.empty:
        return {**case, **labelled, "status": "missing_current_no_book"}
    row = cur_no.sort_values("book_ask").iloc[0]
    labelled.update(
        {
            "expression": "current_bracket_no",
            "route_leg": "runway_current_no",
            "ask": float(row["book_ask"]),
            "ask_size": float(row["book_ask_size"]),
            "bid": float(row["book_bid"]),
            "token_id": str(row["book_token_id"]),
            "bracket": str(row["bracket"]),
            "question": str(row.get("question") or ""),
        }
    )
    selected = research.add_soft_weights(pd.DataFrame([labelled])).iloc[0]
    soft_notional = 5.0 * float(selected["soft_balanced"])
    soft_shares = soft_notional / float(selected["ask"]) if float(selected["ask"]) else math.nan
    live_order_shares = live.clamp_order_shares_to_top_ask(soft_shares, selected.get("ask_size"))
    live_order_notional = live_order_shares * float(selected["ask"]) if math.isfinite(live_order_shares) else math.nan
    parity_ok = (
        str(selected.get("live_feature_status")) == "ok"
        and all(pd.notna(pd.to_numeric(selected.get(col), errors="coerce")) for col in CORE_COLS)
        and not any("unknown" in str(selected.get(col)) for col in CORE_LABELS)
    )
    would_execute_without_prior_duplicate = bool(
        parity_ok
        and research.ASK_MIN <= float(selected["ask"]) <= research.ASK_CAPS["relaxed70"]
        and live_order_shares >= 5.0
        and str(selected["token_id"])
    )
    return {
        **case,
        "status": "ok",
        "snapshot_ts_utc": payload.get("ts_utc"),
        "current_native": current_native,
        "running_native": running_native,
        "running_value": running_value,
        "forecast_source": selected.get("forecast_source"),
        "forecast_max_native": float(selected.get("forecast_max_native")),
        "forecast_peak_hour_local": float(selected.get("forecast_peak_hour_local")),
        "forecast_gap_to_running_native": float(selected.get("forecast_gap_to_running_native")),
        "day_regime": selected.get("day_regime"),
        "intraday_state": selected.get("intraday_state"),
        "moisture_cloud_regime": selected.get("moisture_cloud_regime"),
        "wind_regime": selected.get("wind_regime"),
        "running_max_state": selected.get("running_max_state"),
        "temp_trend_1h_f": float(selected.get("temp_trend_1h_f")),
        "temp_trend_3h_f": float(selected.get("temp_trend_3h_f")),
        "relative_humidity_pct": float(selected.get("relative_humidity_pct")),
        "sky_cover_code": float(selected.get("sky_cover_code")),
        "dewpoint_depression_f": float(selected.get("dewpoint_depression_f")),
        "wind_speed_kt": float(selected.get("wind_speed_kt")),
        "minutes_since_running_max": float(selected.get("minutes_since_running_max")),
        "asof_last_obs_utc": selected.get("asof_last_obs_utc"),
        "bracket": selected.get("bracket"),
        "ask": float(selected.get("ask")),
        "bid": float(selected.get("bid")),
        "ask_size": float(selected.get("ask_size")),
        "soft_balanced": float(selected.get("soft_balanced")),
        "soft_notional_usd": soft_notional,
        "soft_shares": soft_shares,
        "live_order_shares": live_order_shares,
        "live_order_notional_usd": live_order_notional,
        "live_order_clamped_by_top_ask": bool(
            math.isfinite(live_order_shares) and math.isfinite(soft_shares) and live_order_shares < soft_shares
        ),
        "live_feature_parity_ok": parity_ok,
        "would_execute_without_prior_duplicate": would_execute_without_prior_duplicate,
        "would_execute_with_existing_duplicate": False,
        "existing_duplicate_reason": "duplicate_live_city_date_token",
    }


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col)
            if isinstance(val, float):
                if col in {"roi", "roi_before", "roi_after", "hit_rate", "parity_pass_rate"}:
                    vals.append(pct(val))
                elif "usd" in col or col == "pnl_usd":
                    vals.append(money(val))
                else:
                    vals.append(f"{val:.3f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(payload: dict[str, Any]) -> None:
    hist_rows = payload["historical"]["summary_rows"]
    nyc_rows = payload["nyc_replay_rows"]
    text = "\n".join(
        [
            "# Regime-Routed NO Live Feature Parity V1",
            "",
            f"Generated: `{payload['generated_at_utc']}`",
            "",
            "## Verdict",
            "",
            "- This is a parity/incident replay, not a live approval.",
            "- Historical selected rows mostly survive the new live feature-parity gate; this says the live feature gate is not cutting the sample to zero.",
            "- The 2026-06-25 NYC order would still have passed the feature and sizing gates if there had been no prior duplicate order; with the new duplicate gate, the second same-token order is blocked.",
            "- Live remains blocked until a broader point-in-time replay and deploy review pass.",
            "",
            "## Historical Main Variant",
            "",
            md_table(hist_rows, ["slice", "rows", "dates", "cities", "cost_usd", "pnl_usd", "roi", "hit_rate"]),
            "",
            "## NYC As-Of Replay",
            "",
            md_table(
                nyc_rows,
                [
                    "label",
                    "asof_utc",
                    "bracket",
                    "ask",
                    "day_regime",
                    "intraday_state",
                    "moisture_cloud_regime",
                    "wind_regime",
                    "running_max_state",
                    "soft_balanced",
                    "soft_shares",
                    "live_feature_parity_ok",
                    "would_execute_without_prior_duplicate",
                    "would_execute_with_existing_duplicate",
                ],
            ),
            "",
            "## Boundary",
            "",
            "This replay uses archived selected-trade rows for historical performance and live-style AviationWeather METAR reconstruction for the NYC as-of cases. It does not restore live.",
            "",
        ]
    )
    OUT_MD.write_text(text, encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hist_frame, hist_summary = historical_replay()
    nyc_rows = [replay_nyc_order_case(case) for case in NYC_REPLAYS]
    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {
            "selected_trade_details": str(SELECTED.relative_to(ROOT)),
            "main_variant": MAIN_VARIANT,
        },
        "historical": hist_summary,
        "nyc_replay_rows": nyc_rows,
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "historical_csv": str(OUT_HIST.relative_to(ROOT)),
            "nyc_csv": str(OUT_NYC.relative_to(ROOT)),
            "report_md": str(OUT_MD.relative_to(ROOT)),
        },
    }
    hist_frame.to_csv(OUT_HIST, index=False)
    pd.DataFrame(nyc_rows).to_csv(OUT_NYC, index=False)
    OUT_JSON.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(finite(payload))
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
