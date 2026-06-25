#!/usr/bin/env python3
"""Peak-YES execution timing replay.

This uses the v4 scored rows and asks whether the problem is probability or
timing: do earlier entries have better economics than waiting for more confirmed
no-reheat states?
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SCORED = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_mechanism_v4/peak_yes_mechanism_v4_scored_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_peak_yes_execution_timing_v1"
OUT_HOUR = OUT_DIR / "peak_yes_timing_v1_hour_band_summary.csv"
OUT_RULES = OUT_DIR / "peak_yes_timing_v1_first_signal_rules.csv"
OUT_WAIT = OUT_DIR / "peak_yes_timing_v1_wait_cost_summary.csv"
OUT_EVENTS = OUT_DIR / "peak_yes_timing_v1_event_rows.csv"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-execution-timing-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-current-yes-peak-yes-execution-timing-v1.md"
SEED = 20260625


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        out = float(value)
        return None if not math.isfinite(out) else out
    return value


def pct(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        out = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(out):
        return "NA"
    return f"{out * 100:+.1f}%"


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        out = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(out):
        return "NA"
    return f"{out:.{digits}f}"


def date_bootstrap_roi(frame: pd.DataFrame, reps: int = 4000) -> list[float | None]:
    by_date = frame.groupby("target_date")[["current_yes_ask", "trade_pnl"]].sum()
    if len(by_date) < 2:
        return [None, None]
    vals = by_date.to_numpy(float)
    rng = np.random.default_rng(SEED)
    out = []
    for _ in range(reps):
        sample = vals[rng.integers(0, len(vals), size=len(vals))]
        cost = sample[:, 0].sum()
        if cost > 0:
            out.append(float(sample[:, 1].sum() / cost))
    if not out:
        return [None, None]
    arr = np.asarray(out)
    return [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]


def summary(frame: pd.DataFrame, name: str) -> dict[str, Any]:
    if frame.empty:
        return {
            "rule": name,
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "roi_ci95": [None, None],
            "win_rate": None,
            "break_rate": None,
            "avg_ask": None,
            "avg_hour": None,
        }
    cost = float(frame["current_yes_ask"].sum())
    pnl = float(frame["trade_pnl"].sum())
    return {
        "rule": name,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "roi_ci95": date_bootstrap_roi(frame),
        "win_rate": float(frame["label_survive"].mean()),
        "break_rate": float(frame["label_future_break"].mean()),
        "avg_ask": float(frame["current_yes_ask"].mean()),
        "avg_hour": float(frame["decision_hour_local"].mean()),
    }


def add_common(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["target_date"] = out["target_date"].astype(str)
    out["decision_hour_local"] = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    out["label_survive"] = pd.to_numeric(out["label_survive"], errors="coerce").fillna(0).astype(int)
    out["label_future_break"] = 1 - out["label_survive"]
    out["trade_pnl"] = out["label_survive"] - out["current_yes_ask"]
    out["edge_market_components"] = (1.0 - out["p_break_market_components_l2"]) - out["current_yes_ask"]
    out["edge_components"] = (1.0 - out["p_break_components_l2"]) - out["current_yes_ask"]
    out["hour_band"] = pd.cut(
        out["decision_hour_local"],
        bins=[9, 12, 15, 18, 22],
        labels=["10-12", "13-15", "16-18", "19-21"],
        right=True,
    ).astype(str)
    out["event_key"] = (
        out["city"].astype(str)
        + "|"
        + out["target_date"].astype(str)
        + "|"
        + out["current_bracket"].astype(str)
    )
    return out


def first_per_event(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(["event_key", "decision_hour_local", "decision_snapshot_ts_utc"])
        .drop_duplicates("event_key", keep="first")
        .copy()
    )


def last_per_event(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(["event_key", "decision_hour_local", "decision_snapshot_ts_utc"])
        .drop_duplicates("event_key", keep="last")
        .copy()
    )


def hour_band_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in ["train", "holdout", "forward"]:
        frame = df[df["period"].eq(period)]
        rows.append({**summary(frame, "all_hours"), "period": period, "hour_band": "all"})
        for band, sub in frame.groupby("hour_band", dropna=False):
            rows.append({**summary(sub, f"hour_{band}"), "period": period, "hour_band": str(band)})
    return pd.DataFrame(rows)


def first_signal_rules(df: pd.DataFrame) -> pd.DataFrame:
    train = df[df["period"].eq("train")]
    q20_components = float(train["p_break_components_l2"].quantile(0.20))
    q20_market_components = float(train["p_break_market_components_l2"].quantile(0.20))
    q30_market_components = float(train["p_break_market_components_l2"].quantile(0.30))

    specs = [
        ("first_edge_market_components_ge_0", df["edge_market_components"].ge(0.00)),
        ("first_edge_market_components_ge_2", df["edge_market_components"].ge(0.02)),
        ("first_edge_components_ge_2", df["edge_components"].ge(0.02)),
        ("first_low_components_q20", df["p_break_components_l2"].le(q20_components)),
        ("first_low_market_components_q20", df["p_break_market_components_l2"].le(q20_market_components)),
        ("first_low_market_components_q30", df["p_break_market_components_l2"].le(q30_market_components)),
    ]
    rows = []
    event_rows = []
    for name, mask in specs:
        selected = first_per_event(df[mask].copy())
        for period in ["train", "holdout", "forward"]:
            sub = selected[selected["period"].eq(period)].copy()
            rows.append({**summary(sub, name), "period": period})
        event_rows.append(selected.assign(rule=name))
    events = pd.concat(event_rows, ignore_index=True) if event_rows else pd.DataFrame()
    events.to_csv(OUT_EVENTS, index=False)
    return pd.DataFrame(rows)


def wait_cost_summary(df: pd.DataFrame) -> pd.DataFrame:
    specs = [
        ("edge_market_components_ge_0", df["edge_market_components"].ge(0.00)),
        ("edge_components_ge_2", df["edge_components"].ge(0.02)),
        ("low_components_pbreak_le_25", df["p_break_components_l2"].le(0.25)),
        ("low_market_components_pbreak_le_25", df["p_break_market_components_l2"].le(0.25)),
    ]
    rows = []
    for name, mask in specs:
        sub = df[mask].copy()
        first = first_per_event(sub)
        last = last_per_event(sub)
        pair = first[
            [
                "event_key",
                "period",
                "target_date",
                "city",
                "current_yes_ask",
                "trade_pnl",
                "label_survive",
                "decision_hour_local",
            ]
        ].merge(
            last[
                [
                    "event_key",
                    "current_yes_ask",
                    "trade_pnl",
                    "decision_hour_local",
                ]
            ],
            on="event_key",
            suffixes=("_first", "_last"),
        )
        pair = pair[pair["decision_hour_local_last"].gt(pair["decision_hour_local_first"])].copy()
        pair["ask_change_wait"] = pair["current_yes_ask_last"] - pair["current_yes_ask_first"]
        pair["pnl_change_wait"] = pair["trade_pnl_last"] - pair["trade_pnl_first"]
        for period in ["train", "holdout", "forward"]:
            p = pair[pair["period"].eq(period)]
            if p.empty:
                rows.append(
                    {
                        "rule": name,
                        "period": period,
                        "events": 0,
                        "dates": 0,
                        "avg_hour_first": None,
                        "avg_hour_last": None,
                        "avg_ask_first": None,
                        "avg_ask_last": None,
                        "avg_ask_change_wait": None,
                        "roi_first": None,
                        "roi_last": None,
                        "roi_delta_wait": None,
                    }
                )
                continue
            cost_first = p["current_yes_ask_first"].sum()
            cost_last = p["current_yes_ask_last"].sum()
            roi_first = p["trade_pnl_first"].sum() / cost_first if cost_first else None
            roi_last = p["trade_pnl_last"].sum() / cost_last if cost_last else None
            rows.append(
                {
                    "rule": name,
                    "period": period,
                    "events": int(len(p)),
                    "dates": int(p["target_date"].nunique()),
                    "avg_hour_first": float(p["decision_hour_local_first"].mean()),
                    "avg_hour_last": float(p["decision_hour_local_last"].mean()),
                    "avg_ask_first": float(p["current_yes_ask_first"].mean()),
                    "avg_ask_last": float(p["current_yes_ask_last"].mean()),
                    "avg_ask_change_wait": float(p["ask_change_wait"].mean()),
                    "roi_first": roi_first,
                    "roi_last": roi_last,
                    "roi_delta_wait": (roi_last - roi_first) if roi_first is not None and roi_last is not None else None,
                }
            )
    return pd.DataFrame(rows)


def render_summary_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| period | rule | rows | dates | win | avg ask | avg hour | ROI | CI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['period']} | {row['rule']} | {row['rows']} | {row['dates']} | "
            f"{pct(row.get('win_rate'))} | {num(row.get('avg_ask'))} | {num(row.get('avg_hour'), 1)} | "
            f"{pct(row.get('roi'))} | [{pct(row['roi_ci95'][0])}, {pct(row['roi_ci95'][1])}] |"
        )
    return lines


def build_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Current-YES Peak-YES Execution Timing v1",
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
        f"- rows: {payload['coverage']['rows']} / dates {payload['coverage']['dates']} / cities {payload['coverage']['cities']}",
        "",
        "This is opportunity replay at quote ask, not live fill PnL. Each first-signal rule buys the first eligible row per city/date/current bracket.",
        "",
        "## First Signal Rules",
        "",
        *render_summary_table(payload["first_signal_rules"]),
        "",
        "## Hour Bands",
        "",
        *render_summary_table(payload["hour_band_summary"]),
        "",
        "## Wait Cost",
        "",
        "| period | rule | events | first hour | last hour | ask first | ask last | wait ask change | ROI first | ROI last |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["wait_cost_summary"]:
        lines.append(
            f"| {row['period']} | {row['rule']} | {row['events']} | {num(row.get('avg_hour_first'),1)} | "
            f"{num(row.get('avg_hour_last'),1)} | {num(row.get('avg_ask_first'))} | {num(row.get('avg_ask_last'))} | "
            f"{num(row.get('avg_ask_change_wait'))} | {pct(row.get('roi_first'))} | {pct(row.get('roi_last'))} |"
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"significance={payload['verdict']['significance']} / baseline={payload['verdict']['baseline']} / forward={payload['verdict']['forward']} / conclusion={payload['verdict']['conclusion']}",
            "",
            payload["verdict"]["text"],
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
    df = add_common(pd.read_csv(SCORED, low_memory=False))
    hour = hour_band_summary(df)
    rules = first_signal_rules(df)
    wait = wait_cost_summary(df)
    hour.to_csv(OUT_HOUR, index=False)
    rules.to_csv(OUT_RULES, index=False)
    wait.to_csv(OUT_WAIT, index=False)

    hold_edge0 = rules[
        rules["period"].eq("holdout") & rules["rule"].eq("first_edge_market_components_ge_0")
    ].iloc[0]
    hold_low30 = rules[
        rules["period"].eq("holdout") & rules["rule"].eq("first_low_market_components_q30")
    ].iloc[0]
    fwd_edge0 = rules[
        rules["period"].eq("forward") & rules["rule"].eq("first_edge_market_components_ge_0")
    ].iloc[0]
    wait_edge0 = wait[
        wait["period"].eq("holdout") & wait["rule"].eq("edge_market_components_ge_0")
    ].iloc[0]
    headline = (
        "Execution timing helps but does not solve peak YES: first market+components edge>=0 "
        f"holdout has {int(hold_edge0['rows'])} rows ROI {pct(hold_edge0['roi'])}, CI "
        f"[{pct(hold_edge0['roi_ci95'][0])}, {pct(hold_edge0['roi_ci95'][1])}], "
        f"forward ROI {pct(fwd_edge0['roi'])}. "
        f"The broader first low-risk q30 rule has holdout ROI {pct(hold_low30['roi'])}. "
        f"Waiting inside the same edge>=0 event raises ask by {num(wait_edge0['avg_ask_change_wait'])} on average "
        f"and changes ROI from {pct(wait_edge0['roi_first'])} to {pct(wait_edge0['roi_last'])}."
    )
    verdict = {
        "significance": "FAIL",
        "baseline": "FAIL",
        "forward": "FAIL",
        "conclusion": "inconclusive",
        "text": (
            "Earlier first-signal entry is economically better than repeated/waited confirmation in some rows, but the edge is still not statistically live-ready. "
            "This supports forward maker-first/timing telemetry rather than immediate taker live: log first signal, quote drift, maker fill chance, and whether later confirmation merely buys a more expensive version of the same payoff."
        ),
    }
    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {"scored_rows": rel(SCORED)},
        "coverage": {
            "rows": int(len(df)),
            "dates": int(df["target_date"].nunique()),
            "cities": int(df["city"].nunique()),
            "min_target_date": str(df["target_date"].min()),
            "max_target_date": str(df["target_date"].max()),
        },
        "headline": headline,
        "first_signal_rules": rules[
            rules["rule"].isin(
                [
                    "first_edge_market_components_ge_0",
                    "first_edge_market_components_ge_2",
                    "first_edge_components_ge_2",
                    "first_low_market_components_q30",
                ]
            )
        ].sort_values(["period", "rule"]).to_dict("records"),
        "hour_band_summary": hour[hour["period"].isin(["holdout", "forward"])].to_dict("records"),
        "wait_cost_summary": wait.to_dict("records"),
        "verdict": verdict,
        "outputs": {
            "hour_band_summary": rel(OUT_HOUR),
            "first_signal_rules": rel(OUT_RULES),
            "wait_cost_summary": rel(OUT_WAIT),
            "event_rows": rel(OUT_EVENTS),
            "json": rel(OUT_JSON),
            "markdown": rel(OUT_MD),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_markdown(json_ready(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
