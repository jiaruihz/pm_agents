#!/usr/bin/env python3
"""Materialize peak-YES first-signal timing shadow telemetry.

This does not place orders.  It turns the v4 historical scored rows into the
JSONL shape we want the live runner to emit forward: first signal, quote drift
after first signal, maker probe economics, and replay-only payoff labels.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

if __package__:
    from .peak_yes_timing_shared import json_ready, num, pct
else:
    from peak_yes_timing_shared import json_ready, num, pct

ROOT = Path(__file__).resolve().parents[3]
SCORED = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_scored_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_timing_shadow_telemetry_v1"
OUT_JSONL = OUT_DIR / "peak_yes_timing_shadow_telemetry_v1.jsonl"
OUT_SUMMARY = OUT_DIR / "peak_yes_timing_shadow_telemetry_v1_summary.csv"
OUT_SCHEMA = OUT_DIR / "peak_yes_timing_shadow_telemetry_v1_schema.json"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-timing-shadow-telemetry-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-timing-shadow-telemetry-v1.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def add_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["target_date"] = out["target_date"].astype(str)
    out["decision_hour_local"] = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    out["current_yes_ask"] = pd.to_numeric(out["current_yes_ask"], errors="coerce")
    out["p_survive_market_components_l2"] = 1.0 - pd.to_numeric(out["p_break_market_components_l2"], errors="coerce")
    out["edge_market_components_l2"] = out["p_survive_market_components_l2"] - out["current_yes_ask"]
    out["p_survive_components_l2"] = 1.0 - pd.to_numeric(out["p_break_components_l2"], errors="coerce")
    out["edge_components_l2"] = out["p_survive_components_l2"] - out["current_yes_ask"]
    out["event_key"] = (
        out["city"].astype(str)
        + "|"
        + out["target_date"].astype(str)
        + "|"
        + out["current_bracket"].astype(str)
    )
    out["label_survive"] = pd.to_numeric(out["label_survive"], errors="coerce").fillna(0).astype(int)
    out["ask_unit_pnl"] = out["label_survive"] - out["current_yes_ask"]
    out["maker_probe_price_1c_inside"] = (out["current_yes_ask"] - 0.01).clip(lower=0.01)
    out["maker_probe_edge_1c_inside"] = out["p_survive_market_components_l2"] - out["maker_probe_price_1c_inside"]
    out["maker_probe_unit_pnl_replay"] = out["label_survive"] - out["maker_probe_price_1c_inside"]
    return out.sort_values(["event_key", "decision_hour_local", "decision_snapshot_ts_utc"]).reset_index(drop=True)


def materialize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for event_key, group in df.groupby("event_key", sort=False):
        group = group.sort_values(["decision_hour_local", "decision_snapshot_ts_utc"]).copy()
        signal = group[group["edge_market_components_l2"].ge(0.0)].head(1)
        if signal.empty:
            continue
        first = signal.iloc[0]
        first_hour = float(first["decision_hour_local"])
        first_ask = float(first["current_yes_ask"])
        first_edge = float(first["edge_market_components_l2"])
        for _, row in group[group["decision_hour_local"].ge(first_hour)].iterrows():
            is_first = bool(row.name == first.name)
            ask_change = float(row["current_yes_ask"] - first_ask)
            rows.append(
                {
                    "record_type": "theta_current_yes_peak_timing_shadow",
                    "telemetry_version": 1,
                    "source": "historical_replay_v4_scored_rows",
                    "shadow_rule": "first_edge_market_components_ge_0",
                    "event_key": event_key,
                    "is_first_signal": is_first,
                    "has_prior_first_signal": not is_first,
                    "first_signal_hour_local": first_hour,
                    "first_signal_ask": first_ask,
                    "first_signal_edge_market_components_l2": first_edge,
                    "hours_since_first_signal": float(row["decision_hour_local"] - first_hour),
                    "ask_change_since_first_signal": ask_change,
                    "city": str(row["city"]),
                    "target_date": str(row["target_date"]),
                    "decision_snapshot_ts_utc": str(row.get("decision_snapshot_ts_utc", "")),
                    "decision_hour_local": float(row["decision_hour_local"]),
                    "current_bracket": str(row.get("current_bracket", "")),
                    "current_yes_ask": float(row["current_yes_ask"]),
                    "maker_probe_price_1c_inside": float(row["maker_probe_price_1c_inside"]),
                    "p_survive_market_components_l2": float(row["p_survive_market_components_l2"]),
                    "edge_market_components_l2": float(row["edge_market_components_l2"]),
                    "maker_probe_edge_1c_inside": float(row["maker_probe_edge_1c_inside"]),
                    "p_survive_components_l2": float(row["p_survive_components_l2"]),
                    "edge_components_l2": float(row["edge_components_l2"]),
                    "day_regime": str(row.get("day_regime", "")),
                    "intraday_state": str(row.get("intraday_state", "")),
                    "running_max_state": str(row.get("running_max_state", "")),
                    "solar_altitude_deg": row.get("solar_altitude_deg"),
                    "decision_obs_age_min": row.get("decision_obs_age_min"),
                    "d_sky_3h": row.get("d_sky_3h"),
                    "d_sknt_3h": row.get("d_sknt_3h"),
                    "d_relh_3h": row.get("d_relh_3h"),
                    "forecast_slope_to_peak_native_per_h": row.get("forecast_slope_to_peak_native_per_h"),
                    "label_survive_replay": int(row["label_survive"]),
                    "ask_unit_pnl_replay": float(row["ask_unit_pnl"]),
                    "maker_probe_unit_pnl_replay": float(row["maker_probe_unit_pnl_replay"]),
                    "period": str(row["period"]),
                }
            )
    return pd.DataFrame(rows)


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period, frame in events.groupby("period", sort=False):
        for scope, sub in [
            ("all_tracking_rows", frame),
            ("first_signal_only", frame[frame["is_first_signal"]]),
            ("post_first_tracking", frame[~frame["is_first_signal"]]),
        ]:
            if sub.empty:
                rows.append({"period": period, "scope": scope, "rows": 0})
                continue
            cost = sub["current_yes_ask"].sum()
            maker_cost = sub["maker_probe_price_1c_inside"].sum()
            rows.append(
                {
                    "period": period,
                    "scope": scope,
                    "rows": int(len(sub)),
                    "events": int(sub["event_key"].nunique()),
                    "dates": int(sub["target_date"].nunique()),
                    "win_rate": float(sub["label_survive_replay"].mean()),
                    "avg_ask": float(sub["current_yes_ask"].mean()),
                    "avg_maker_probe_price": float(sub["maker_probe_price_1c_inside"].mean()),
                    "avg_edge": float(sub["edge_market_components_l2"].mean()),
                    "avg_ask_change_since_first": float(sub["ask_change_since_first_signal"].mean()),
                    "ask_roi_replay": float(sub["ask_unit_pnl_replay"].sum() / cost) if cost else None,
                    "maker_probe_roi_replay": float(sub["maker_probe_unit_pnl_replay"].sum() / maker_cost) if maker_cost else None,
                }
            )
    return pd.DataFrame(rows)


def schema() -> dict[str, Any]:
    return {
        "record_type": "theta_current_yes_peak_timing_shadow",
        "version": 1,
        "required_live_fields": [
            "strategy_instance",
            "telemetry_run_id",
            "city",
            "target_date",
            "decision_snapshot_ts_utc",
            "decision_hour_local",
            "current_bracket",
            "current_yes_ask",
            "fresh_best_bid",
            "fresh_best_ask",
            "p_survive_market_components_l2",
            "edge_market_components_l2",
            "is_first_signal",
            "first_signal_hour_local",
            "first_signal_ask",
            "ask_change_since_first_signal",
            "maker_probe_price",
            "maker_probe_edge",
        ],
        "replay_only_fields": ["label_survive_replay", "ask_unit_pnl_replay", "maker_probe_unit_pnl_replay"],
        "state_key": ["city", "target_date", "current_bracket"],
        "first_signal_rule": "edge_market_components_l2 >= 0.0",
    }


def render(payload: dict[str, Any]) -> None:
    lines = [
        "# Current-YES Peak-YES Timing Shadow Telemetry v1",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "## 一句话结论",
        "",
        payload["headline"],
        "",
        "## 数据范围",
        "",
        f"- scored rows: `{payload['inputs']['scored_rows']}`",
        f"- telemetry rows: {payload['coverage']['telemetry_rows']} / events {payload['coverage']['events']} / dates {payload['coverage']['dates']}",
        "",
        "This materializes the JSONL shape for forward shadow logging. Replay-only payoff fields must not exist in live forward rows.",
        "",
        "## Summary",
        "",
        "| period | scope | rows | events | win | ask | maker probe | ask drift | ask ROI | maker ROI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["summary"]:
        lines.append(
            f"| {row.get('period')} | {row.get('scope')} | {row.get('rows', 0)} | {row.get('events', 0)} | "
            f"{pct(row.get('win_rate'))} | {num(row.get('avg_ask'))} | {num(row.get('avg_maker_probe_price'))} | "
            f"{num(row.get('avg_ask_change_since_first'))} | {pct(row.get('ask_roi_replay'))} | {pct(row.get('maker_probe_roi_replay'))} |"
        )
    lines.extend(
        [
            "",
            "## Live 接入要点",
            "",
            "- 只新增 shadow telemetry，不下单、不改变 taker/live gates。",
            "- state key 是 `city + target_date + current_bracket`；runner 需要记住当天首次 signal。",
            "- 每个后续 cycle 记录 ask drift、fresh bid/ask、maker probe edge，判断 later confirmation 是否只是更贵。",
            "- replay label/PnL 字段只在研究产物里存在，live forward row 不能写这些后验字段。",
            "",
            "## Outputs",
            "",
        ]
    )
    for label, path in payload["outputs"].items():
        lines.append(f"- {label}: `{path}`")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    events = materialize(add_fields(pd.read_csv(SCORED, low_memory=False)))
    summary = summarize(events)
    with OUT_JSONL.open("w", encoding="utf-8") as fh:
        for row in events.to_dict("records"):
            fh.write(json.dumps(json_ready(row), ensure_ascii=False, sort_keys=True) + "\n")
    summary.to_csv(OUT_SUMMARY, index=False)
    OUT_SCHEMA.write_text(json.dumps(schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    hold_first = summary[(summary["period"].eq("holdout")) & (summary["scope"].eq("first_signal_only"))].iloc[0]
    hold_post = summary[(summary["period"].eq("holdout")) & (summary["scope"].eq("post_first_tracking"))].iloc[0]
    headline = (
        f"Shadow telemetry replay materialized {len(events)} rows across {events['event_key'].nunique()} first-signal events. "
        f"Holdout first-signal rows have ask ROI {pct(hold_first['ask_roi_replay'])}; post-first tracking rows have "
        f"average ask drift {num(hold_post['avg_ask_change_since_first'])} and ask ROI {pct(hold_post['ask_roi_replay'])}. "
        "This is the right forward logging shape for maker-first/timing research, not a live approval."
    )
    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {"scored_rows": rel(SCORED)},
        "coverage": {
            "telemetry_rows": int(len(events)),
            "events": int(events["event_key"].nunique()),
            "dates": int(events["target_date"].nunique()),
            "cities": int(events["city"].nunique()),
        },
        "headline": headline,
        "summary": summary.to_dict("records"),
        "schema": schema(),
        "outputs": {
            "telemetry_jsonl": rel(OUT_JSONL),
            "summary": rel(OUT_SUMMARY),
            "schema": rel(OUT_SCHEMA),
            "json": rel(OUT_JSON),
            "markdown": rel(OUT_MD),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    render(json_ready(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
