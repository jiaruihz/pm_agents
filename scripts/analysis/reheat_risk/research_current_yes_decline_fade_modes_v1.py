#!/usr/bin/env python3
"""Research current-YES decline/fade intraday modes.

Evidence layer: opportunity/orderbook replay plus physical observed max.  This
script does not publish live_real PnL and does not alter live policy.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/current_yes_decline_fade_20260620_feature_factory/reheat_feature_rows.csv"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-21-current-yes-decline-fade-modes-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-21-current-yes-decline-fade-modes-v1.md"
SEED = 20260621


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB))
    parser.add_argument("--feature-rows", default=str(FEATURE_ROWS))
    parser.add_argument("--gate-json", default=str(GATE))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None, signed: bool = False) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    sign = "+" if signed else ""
    return f"{float(value) * 100:{sign}.1f}%"


def fnum(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


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
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def self_check(db_path: Path, gate_path: Path) -> dict[str, Any]:
    conn = connect_ro(db_path)
    try:
        out = {
            "fact_trades_freshness": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades",
            )[0],
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "fact_trades_by_settlement": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY COALESCE(settlement_status, '') ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled, MIN(event_date) AS min_event_date, MAX(event_date) AS max_event_date, "
                "MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status AS order_status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }
    finally:
        conn.close()

    if gate_path.exists():
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        out["clob_fill_coverage_gate"] = {
            "gate_pass": gate.get("gate_pass"),
            "fail_reasons": gate.get("fail_reasons", []),
        }
    else:
        out["clob_fill_coverage_gate"] = {"gate_pass": None, "missing": True}
    return out


def load_state_rows(feature_path: Path) -> pd.DataFrame:
    usecols = [
        "city",
        "target_date",
        "decision_hour_local",
        "current_bracket",
        "unit",
        "current_temp_c",
        "running_max_c",
        "decline_from_max_c",
        "final_max_c",
        "current_yes_ask",
        "current_yes_ask_size",
        "current_bracket_held",
        "minutes_since_running_max",
        "relative_humidity_pct",
        "sky_cover_code",
        "wind_speed_kt",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "forecast_peak_delta_hours_local",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "forecast_peak_hour_spread",
    ]
    df = pd.read_csv(feature_path, usecols=lambda col: col in set(usecols))
    keys = ["city", "target_date", "decision_hour_local", "current_bracket"]
    df = df.drop_duplicates(keys).copy()
    for col in [
        "decision_hour_local",
        "current_temp_c",
        "running_max_c",
        "decline_from_max_c",
        "final_max_c",
        "current_yes_ask",
        "current_yes_ask_size",
        "current_bracket_held",
        "minutes_since_running_max",
        "relative_humidity_pct",
        "sky_cover_code",
        "wind_speed_kt",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "forecast_peak_delta_hours_local",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "forecast_peak_hour_spread",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["target_date"] = df["target_date"].astype(str)
    df["unit"] = df["unit"].astype(str)
    df = df[df["current_yes_ask"].between(0.001, 0.999, inclusive="both")].copy()
    df["future_gain_c"] = df["final_max_c"] - df["running_max_c"]
    df["future_break_ge_0_5c"] = df["future_gain_c"].ge(0.5)
    df["physical_fade_confirmed"] = df["future_gain_c"].lt(0.5)
    df["settled_current_yes"] = df["current_bracket_held"].isin([0.0, 1.0])
    df["yes_roi_proxy"] = np.where(
        df["settled_current_yes"],
        df["current_bracket_held"] / df["current_yes_ask"] - 1.0,
        np.nan,
    )
    df["decline_ge_0_5"] = df["decline_from_max_c"].ge(0.5)
    df["decline_ge_1_0"] = df["decline_from_max_c"].ge(1.0)
    df["decline_ge_1_5"] = df["decline_from_max_c"].ge(1.5)
    df["minutes_since_ge_90"] = df["minutes_since_running_max"].ge(90)
    df["humid"] = df["relative_humidity_pct"].ge(75)
    df["cloudy"] = df["sky_cover_code"].ge(1)
    df["cooling_3h"] = df["temp_trend_3h_f"].lt(0)
    df["before_or_at_14"] = df["decision_hour_local"].le(14)
    df["after_or_at_16"] = df["decision_hour_local"].ge(16)
    return df


def assign_mode(row: pd.Series) -> str:
    if not bool(row.get("decline_ge_0_5")):
        return "not_declined"
    hour = float(row.get("decision_hour_local", np.nan))
    decline = float(row.get("decline_from_max_c", np.nan))
    mins = float(row.get("minutes_since_running_max", np.nan))
    relh = float(row.get("relative_humidity_pct", np.nan))
    sky = float(row.get("sky_cover_code", np.nan))
    trend3 = float(row.get("temp_trend_3h_f", np.nan))
    if hour <= 14 and decline >= 1.0 and mins >= 60 and relh >= 75 and sky >= 1:
        return "early_humid_convective_dip"
    if hour <= 14 and (mins < 90 or decline < 1.0):
        return "early_recent_or_small_dip"
    if hour >= 16 and mins >= 90 and decline >= 1.0:
        return "late_mature_cooldown"
    if hour >= 16 and mins >= 90 and decline >= 0.5:
        return "late_soft_cooldown"
    if trend3 < 0 and mins >= 90:
        return "cooling_trend_fade"
    return "generic_decline"


def summarize_subset(df: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    sub = df[mask].copy()
    settled = sub[sub["settled_current_yes"]].copy()
    cost = float(settled["current_yes_ask"].sum()) if len(settled) else 0.0
    payout = float(settled["current_bracket_held"].sum()) if len(settled) else 0.0
    roi = (payout / cost - 1.0) if cost > 0 else None
    return {
        "state_rows": int(len(sub)),
        "active_dates": int(sub["target_date"].nunique()) if len(sub) else 0,
        "cities": int(sub["city"].nunique()) if len(sub) else 0,
        "avg_hour": float(sub["decision_hour_local"].mean()) if len(sub) else None,
        "avg_decline_c": float(sub["decline_from_max_c"].mean()) if len(sub) else None,
        "avg_minutes_since_max": float(sub["minutes_since_running_max"].mean()) if len(sub) else None,
        "future_break_ge_0_5c_rate": float(sub["future_break_ge_0_5c"].mean()) if len(sub) else None,
        "physical_confirmed_rate": float(sub["physical_fade_confirmed"].mean()) if len(sub) else None,
        "settled_rows": int(len(settled)),
        "yes_win_rate_settled": float(settled["current_bracket_held"].mean()) if len(settled) else None,
        "avg_yes_ask_settled": float(settled["current_yes_ask"].mean()) if len(settled) else None,
        "yes_roi_proxy_settled": roi,
    }


def bootstrap_roi_delta(df: pd.DataFrame, rule_mask: pd.Series, base_mask: pd.Series, n: int = 2000) -> dict[str, Any]:
    settled = df[df["settled_current_yes"]].copy()
    rule = settled[rule_mask.loc[settled.index]]
    base = settled[base_mask.loc[settled.index]]
    if rule.empty or base.empty:
        return {"n_boot": 0, "delta_roi": None, "ci95": [None, None]}
    dates = np.array(sorted(set(settled["target_date"])))
    rng = np.random.default_rng(SEED)

    def roi(frame: pd.DataFrame) -> float | None:
        cost = float(frame["current_yes_ask"].sum())
        if cost <= 0:
            return None
        return float(frame["current_bracket_held"].sum() / cost - 1.0)

    point = (roi(rule) or 0.0) - (roi(base) or 0.0)
    draws: list[float] = []
    for _ in range(n):
        sample_dates = rng.choice(dates, size=len(dates), replace=True)
        sample = pd.concat([settled[settled["target_date"].eq(d)] for d in sample_dates], ignore_index=True)
        r = sample[rule_mask.reindex(sample.index, fill_value=False)] if False else None
        # Recompute masks after concat because index identity is lost.
        rmask = (
            sample["decline_from_max_c"].ge(0.5)
            & sample["decision_hour_local"].between(15, 21)
            & sample["minutes_since_running_max"].ge(90)
        )
        bmask = sample["decline_from_max_c"].lt(0.5) & sample["decision_hour_local"].between(15, 21)
        rr = roi(sample[rmask])
        bb = roi(sample[bmask])
        if rr is not None and bb is not None:
            draws.append(rr - bb)
    if not draws:
        return {"n_boot": 0, "delta_roi": point, "ci95": [None, None]}
    lo, hi = np.quantile(draws, [0.025, 0.975])
    return {"n_boot": len(draws), "delta_roi": point, "ci95": [float(lo), float(hi)]}


def top_false_fades(df: pd.DataFrame) -> list[dict[str, Any]]:
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "current_temp_c",
        "running_max_c",
        "decline_from_max_c",
        "final_max_c",
        "future_gain_c",
        "current_yes_ask",
        "minutes_since_running_max",
        "relative_humidity_pct",
        "sky_cover_code",
        "wind_speed_kt",
        "mode",
    ]
    sub = df[df["decline_ge_0_5"] & df["future_break_ge_0_5c"]].copy()
    sub = sub.sort_values(["future_gain_c", "decline_from_max_c"], ascending=[False, False]).head(12)
    return sub[cols].to_dict("records")


def build_report(payload: dict[str, Any], out_md: Path) -> None:
    rules = payload["rules"]
    modes = payload["modes"]
    singapore = payload["singapore_2026_06_20"]
    mature = rules["mature_fade_h15_21_decline_ge_0_5_min_since_ge_90"]
    early = rules["early_humid_convective_dip_h10_14_decline_ge_1_0"]
    late = rules["late_mature_cooldown_h16_21_decline_ge_1_0"]
    boot = payload["bootstrap_mature_vs_late_no_decline"]
    sc = payload["self_check"]

    lines = [
        "# 2026-06-21 Current-YES Decline Fade Modes v1",
        "",
        f"Generated UTC: `{payload['generated_at_utc']}`",
        "",
        "## Verdict",
        "",
        "先不把新加坡这种 `decline_c >= 0.5C` 直接加成 live 硬规则。更合理的改法是把它做成 current-YES fade head 的一个候选状态特征/soft gate，并且先 shadow：`h15-21 + decline>=0.5C + minutes_since_running_max>=90` 比裸 `decline>=0.5C` 稳；`h10-14 + humid/cloudy deep dip` 反而是典型 false-fade 风险。",
        "",
        f"本轮 mature fade slice: state_rows={mature['state_rows']}, active_dates={mature['active_dates']}, physical_confirmed_rate={pct(mature['physical_confirmed_rate'])}, settled YES proxy ROI={pct(mature['yes_roi_proxy_settled'], signed=True)}。",
        f"相对同小时 late no-decline baseline 的日期 bootstrap ROI delta={pct(boot['delta_roi'], signed=True)}, 95% CI [{pct(boot['ci95'][0], signed=True)}, {pct(boot['ci95'][1], signed=True)}]；relative delta 过显著性门，但绝对 proxy ROI 仍略负且没有 forward shadow/live 证据，所以结论等级 `inconclusive / shadow_candidate only`。",
        "",
        "## Data Snapshot",
        "",
        f"- Feature rows: `{payload['feature_rows_path']}`",
        f"- State grain: one row per city/date/hour/current bracket; not fill/PnL grain.",
        f"- Feature target_date range: `{payload['feature_range']['min_date']}`..`{payload['feature_range']['max_date']}`; state rows={payload['feature_range']['state_rows']}; cities={payload['feature_range']['cities']}.",
        f"- CLOB fill gate: `{sc['clob_fill_coverage_gate'].get('gate_pass')}`; live_real PnL is not used in this report.",
        f"- `run_stack.sh` rebuilt fact tables but exited non-cleanly on frontend port 5174; DB/fact/gate artifacts were still produced.",
        "",
        "## Mandatory 5-Line Self-Check",
        "",
        f"1. fact_trades freshness: rows={sc['fact_trades_freshness']['rows']}, max_built={sc['fact_trades_freshness']['max_fact_built_at_utc']}.",
        f"2. trade_class distribution: `{sc['fact_trades_by_class']}`.",
        f"3. settlement distribution: `{sc['fact_trades_by_settlement']}`.",
        f"4. fact_signal_candidates coverage: `{sc['fact_signal_candidate_coverage']}`.",
        f"5. CLOB order/fill join: `{sc['clob_order_fill_join']}`.",
        "",
        "## Singapore 2026-06-20 Read",
        "",
        "The completed observation path says the 30.0C -> 27.2C drop was not actually a confirmed daily high. It later printed 31.1C, so this is a false-fade example, not a clean fade-confirmed example.",
        "",
        "| hour | current C | running max C | decline C | final max C | ask | minutes since max | RH | sky | wind kt |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in singapore:
        lines.append(
            "| {decision_hour_local:.0f} | {current_temp_c:.1f} | {running_max_c:.1f} | {decline_from_max_c:.1f} | "
            "{final_max_c:.1f} | {current_yes_ask:.3f} | {minutes_since_running_max:.0f} | "
            "{relative_humidity_pct:.0f} | {sky_cover_code:.0f} | {wind_speed_kt:.0f} |".format(**row)
        )

    lines.extend(
        [
            "",
            "## Mode Summary",
            "",
            "| Mode | Rows | Dates | Cities | Future break >=0.5C | Physical confirmed | Settled YES ROI proxy |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, row in modes.items():
        lines.append(
            f"| `{name}` | {row['state_rows']} | {row['active_dates']} | {row['cities']} | "
            f"{pct(row['future_break_ge_0_5c_rate'])} | {pct(row['physical_confirmed_rate'])} | "
            f"{pct(row['yes_roi_proxy_settled'], signed=True)} |"
        )

    lines.extend(
        [
            "",
            "## Rule Slices",
            "",
            "| Rule | Rows | Dates | Cities | Avg decline C | Future break >=0.5C | Settled YES win | Settled YES ROI proxy |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, row in rules.items():
        lines.append(
            f"| `{name}` | {row['state_rows']} | {row['active_dates']} | {row['cities']} | "
            f"{fnum(row['avg_decline_c'])} | {pct(row['future_break_ge_0_5c_rate'])} | "
            f"{pct(row['yes_win_rate_settled'])} | {pct(row['yes_roi_proxy_settled'], signed=True)} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "1. `late_mature_cooldown` is the only form that looks structurally aligned with fade-confirmed current YES: the high is old enough, the day is late enough, and future new-high risk is lower.",
            "2. `early_humid_convective_dip` is the Singapore-like trap: rain/cloud/sea-breeze or convective cooling can knock the observation down hard before the true high is done. It should be a risk flag, not a buy trigger.",
            "3. `early_recent_or_small_dip` is mostly observation noise or a brief plateau; treating `decline>=0.5C` alone as confirmation overfires.",
            "4. The trading layer still needs forward shadow evidence. Opportunity proxy is useful for triage, but this report does not satisfy significance/baseline/forward gates for a live change.",
            "",
            "## Contract Verdict",
            "",
            "significance=PASS for relative mature-vs-no-decline delta, baseline=FAIL on absolute proxy ROI, forward=NA, conclusion=inconclusive/shadow_candidate. Action: do not add a live hard rule; add a research/shadow feature candidate for mature fade, and add an early humid dip veto/risk telemetry candidate.",
            "",
            "## Outputs",
            "",
            f"- JSON: `{payload['outputs']['json']}`",
            f"- Markdown: `{payload['outputs']['markdown']}`",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    feature_path = Path(args.feature_rows)
    gate_path = Path(args.gate_json)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    df = load_state_rows(feature_path)
    df["mode"] = df.apply(assign_mode, axis=1)

    rule_masks = {
        "all_decline_ge_0_5": df["decline_ge_0_5"],
        "early_h10_14_decline_ge_0_5": df["decline_ge_0_5"] & df["decision_hour_local"].between(10, 14),
        "early_humid_convective_dip_h10_14_decline_ge_1_0": (
            df["decision_hour_local"].between(10, 14)
            & df["decline_ge_1_0"]
            & df["minutes_since_running_max"].ge(60)
            & df["relative_humidity_pct"].ge(75)
            & df["sky_cover_code"].ge(1)
        ),
        "mature_fade_h15_21_decline_ge_0_5_min_since_ge_90": (
            df["decision_hour_local"].between(15, 21)
            & df["decline_ge_0_5"]
            & df["minutes_since_running_max"].ge(90)
        ),
        "late_mature_cooldown_h16_21_decline_ge_1_0": (
            df["decision_hour_local"].between(16, 21)
            & df["decline_ge_1_0"]
            & df["minutes_since_running_max"].ge(90)
        ),
        "recent_peak_decline_ge_0_5_min_since_lt_60": (
            df["decline_ge_0_5"] & df["minutes_since_running_max"].lt(60)
        ),
        "no_decline_h15_21_baseline": df["decline_from_max_c"].lt(0.5) & df["decision_hour_local"].between(15, 21),
    }
    rules = {name: summarize_subset(df, mask) for name, mask in rule_masks.items()}
    modes = {name: summarize_subset(df, df["mode"].eq(name)) for name in sorted(df["mode"].dropna().unique())}

    mature_mask = rule_masks["mature_fade_h15_21_decline_ge_0_5_min_since_ge_90"]
    base_mask = rule_masks["no_decline_h15_21_baseline"]
    singapore = (
        df[df["city"].eq("Singapore") & df["target_date"].eq("2026-06-20")]
        .sort_values("decision_hour_local")
        .replace({np.nan: None})
        .to_dict("records")
    )

    payload = {
        "generated_at_utc": now_utc(),
        "script": "scripts/analysis/reheat_risk/research_current_yes_decline_fade_modes_v1.py",
        "feature_rows_path": str(feature_path.relative_to(ROOT) if feature_path.is_absolute() else feature_path),
        "feature_range": {
            "min_date": str(df["target_date"].min()),
            "max_date": str(df["target_date"].max()),
            "state_rows": int(len(df)),
            "settled_state_rows": int(df["settled_current_yes"].sum()),
            "cities": int(df["city"].nunique()),
        },
        "self_check": self_check(db_path, gate_path),
        "rules": rules,
        "modes": modes,
        "bootstrap_mature_vs_late_no_decline": bootstrap_roi_delta(df, mature_mask, base_mask),
        "singapore_2026_06_20": singapore,
        "top_false_fades": top_false_fades(df),
        "outputs": {
            "json": str(out_json.relative_to(ROOT) if out_json.is_absolute() else out_json),
            "markdown": str(out_md.relative_to(ROOT) if out_md.is_absolute() else out_md),
        },
        "verdict": {
            "significance": "PASS_relative_delta_only",
            "baseline": "FAIL",
            "forward": "NA",
            "conclusion": "inconclusive/shadow_candidate",
            "action": "do_not_add_live_hard_rule; add mature-fade shadow feature candidate and early-humid-dip risk telemetry",
        },
    }
    out_json.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    build_report(json_ready(payload), out_md)
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "state_rows": len(df)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
