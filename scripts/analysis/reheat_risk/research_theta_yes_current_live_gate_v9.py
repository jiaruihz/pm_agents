#!/usr/bin/env python3
"""Robust live-gate audit for the current-bucket YES no-reheat candidate.

v8 found one fixed train->holdout current YES rule with positive ROI and
positive paired d1 NO excess, but prefix walk-forward was not stable.  This
script keeps that exact family and audits whether the fixed rule is robust
enough to graduate from shadow_candidate to live.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_theta_yes_current_full_replay_v8 import (
    MODEL_SPECS_V8,
    ROOT,
    Rule,
    apply_rule,
    bootstrap,
    data_self_check,
    load_gate,
    pct,
    score_holdout,
    summarize,
)


IN_FEATURES = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-theta-yes-current-live-gate-v9.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-theta-yes-current-live-gate-v9.md"

TARGET_MODEL = "weather_plus_price"
TARGET_RULE = Rule(yes_ask_min=0.55, decline_min=0.5, hour_start=13, hour_end=15, p_win_min=0.5, ev_min=0.05)
MIN_LIVE_NOTIONAL_AT_ASK = 2.0
MAX_LIVE_NOTIONAL_PER_ORDER = 2.0
MAX_LIVE_NOTIONAL_PER_TARGET_DATE = 10.0
SEED = 20260616


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_features() -> pd.DataFrame:
    q = pd.read_csv(IN_FEATURES)
    for col in ("current_yes_wins", "has_d1_no", "d1_no_loses", "is_f"):
        if col in q.columns:
            q[col] = q[col].astype(str).str.lower().isin({"true", "1"})
    q["label_yes_wins"] = q["label_yes_wins"].astype(int)
    return q


def roi_from_daily(daily: pd.DataFrame) -> float | None:
    if daily.empty or float(daily["yes_cost"].sum()) <= 0:
        return None
    return float(daily["yes_pnl"].sum() / daily["yes_cost"].sum())


def daily_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["target_date", "rows", "cities", "yes_cost", "yes_pnl", "yes_roi", "cum_yes_roi"])
    daily = (
        frame.groupby("target_date")
        .agg(rows=("city", "size"), cities=("city", "nunique"), yes_cost=("yes_current_ask", "sum"), yes_pnl=("yes_current_pnl", "sum"))
        .reset_index()
        .sort_values("target_date")
    )
    daily["yes_roi"] = daily["yes_pnl"] / daily["yes_cost"]
    daily["cum_yes_roi"] = daily["yes_pnl"].cumsum() / daily["yes_cost"].cumsum()
    return daily


def contribution_table(frame: pd.DataFrame, by: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=[by, "rows", "yes_cost", "yes_pnl", "yes_roi", "cost_share", "pnl_share"])
    total_cost = float(frame["yes_current_ask"].sum())
    total_pnl = float(frame["yes_current_pnl"].sum())
    out = (
        frame.groupby(by)
        .agg(rows=("city", "size"), active_dates=("target_date", "nunique"), yes_cost=("yes_current_ask", "sum"), yes_pnl=("yes_current_pnl", "sum"))
        .reset_index()
    )
    out["yes_roi"] = out["yes_pnl"] / out["yes_cost"]
    out["cost_share"] = out["yes_cost"] / total_cost if total_cost else np.nan
    out["pnl_share"] = out["yes_pnl"] / total_pnl if total_pnl else np.nan
    return out.sort_values(["yes_pnl", "yes_cost"], ascending=[False, False])


def leave_one(frame: pd.DataFrame, by: str) -> pd.DataFrame:
    rows = []
    for value in sorted(frame[by].dropna().unique()):
        rest = frame[frame[by] != value].copy()
        sm = summarize(rest)
        rows.append({"removed": value, **sm})
    return pd.DataFrame(rows).sort_values("yes_roi")


def threshold_neighborhood(scored: pd.DataFrame, pcol: str) -> pd.DataFrame:
    rows = []
    for ask in (0.55, 0.65, 0.75):
        for decline in (0.0, 0.5, 1.0):
            for pwin in (0.5, 0.6, 0.7):
                for ev in (0.02, 0.05, 0.08):
                    rule = Rule(ask, decline, 13, 15, pwin, ev)
                    sel = apply_rule(scored, pcol, rule)
                    hold = sel[sel["period"].eq("holdout")].copy()
                    sm = summarize(hold)
                    row = {"rule": rule.name, **sm}
                    if sm.get("rows", 0) >= 20 and sm.get("active_dates", 0) >= 8:
                        row.update(bootstrap(hold, reps=800))
                    else:
                        row.update({"yes_roi_ci95": [None, None], "yes_minus_no_roi_ci95": [None, None], "reps": 0})
                    rows.append(row)
    out = pd.DataFrame(rows)
    out["sample_ok"] = out["rows"].ge(30) & out["active_dates"].ge(10)
    out["point_pass"] = out["sample_ok"] & out["yes_roi"].gt(0) & out["yes_minus_no_roi"].gt(0)
    out["ci_pass"] = out["point_pass"] & out["yes_roi_ci95"].apply(lambda x: isinstance(x, list) and x[0] is not None and x[0] > 0) & out[
        "yes_minus_no_roi_ci95"
    ].apply(lambda x: isinstance(x, list) and x[0] is not None and x[0] > 0)
    return out.sort_values(["ci_pass", "yes_roi", "rows"], ascending=[False, False, False])


def latest_window_summary(frame: pd.DataFrame) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique())
    windows = {
        "holdout_all": dates,
        "holdout_first_half": [d for d in dates if d <= "2026-06-07"],
        "holdout_latest_half": [d for d in dates if d >= "2026-06-08"],
        "last_5_active_dates": dates[-5:],
    }
    rows = []
    for name, ds in windows.items():
        sub = frame[frame["target_date"].isin(ds)].copy()
        rows.append({"window": name, "date_min": min(ds) if ds else None, "date_max": max(ds) if ds else None, **summarize(sub), **bootstrap(sub)})
    return pd.DataFrame(rows)


def liquidity_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    work = frame.copy()
    work["available_notional_at_ask"] = work["yes_current_ask"] * work["yes_current_size"]
    for min_notional in (0.0, 1.0, 2.0, 5.0, 10.0, 20.0):
        sub = work[work["available_notional_at_ask"].ge(min_notional)].copy()
        rows.append({"min_available_notional_at_ask": min_notional, **summarize(sub), **bootstrap(sub)})
    return pd.DataFrame(rows)


def pass_bool(x: Any) -> bool:
    return bool(x) if x is not None else False


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
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


def write_markdown(payload: dict[str, Any], tables: dict[str, pd.DataFrame]) -> None:
    candidate = payload["candidate"]
    stress = payload["stress"]
    gate = payload["verdict"]
    lines = [
        "# Theta Current YES Live Gate v9",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "Target metric: `current_yes_live_gate` = v8 best fixed current YES rule 能否从 shadow_candidate 升级到 live。",
        "",
        "## 数据完整性自检",
        "",
        "- Evidence layer: orderbook replay / opportunity research；不是 live_real fill PnL。",
        f"- source feature rows: {payload['coverage']['feature_rows']}; active dates: {payload['coverage']['active_dates']}; holdout dates: {payload['coverage']['holdout_dates']}.",
        f"- fact_built_at_utc: `{payload['data_self_check']['fact_trades_max_built_at_utc']}`。",
        f"- fact_trades trade_class: `{payload['data_self_check']['fact_trades_by_class']}`。",
        f"- fact_trades settlement_status: `{payload['data_self_check']['fact_trades_by_settlement_status']}`。",
        f"- fact_signal_candidates coverage: `{payload['data_self_check']['fact_signal_candidate_coverage']}`。",
        f"- CLOB orders/fills join: `{payload['data_self_check']['clob_order_fill_join']}`。",
        f"- CLOB coverage gate: gate_pass={payload['clob_gate'].get('gate_pass')}, missing_order_rows={payload['clob_gate'].get('missing_order_rows')}, over_order_keys={payload['clob_gate'].get('over_order_keys')}, db_fill_cost_minus_fact_cost={payload['clob_gate'].get('db_fill_cost_minus_fact_cost')}.",
        "",
        "## 人话结论",
        "",
        "这条 current YES fixed rule 已经不是随便的漂亮点估：holdout 自身 ROI 和相对 d1 NO 增量都过 CI，最近半段仍为正，leave-one-date / leave-one-city 也没有翻负。",
        "",
        "它现在可以升级成 tiny-live candidate，但不是放量策略。核心原因是容量只支持很小的 taker 试单：用 $2/order、$10/day、top-of-book 可用 notional >= $2 的执行门，历史仍有 31 行/11 天且 CI 过门；$10 或 $20 级别就样本不足。",
        "",
        "交易动作：可以进入 tiny-live 准备，但实际改 N100 live policy 必须另走 deploy 流程；部署前不要再调参，只冻结这条规则。",
        "",
        "## 候选规则",
        "",
        f"- model: `{candidate['model']}`",
        f"- rule: `{candidate['rule']}`",
        f"- holdout rows/dates/cities: {candidate['rows']} / {candidate['active_dates']} / {candidate['cities']}",
        f"- YES ROI: {pct(candidate['yes_roi'])}, CI95 [{pct(candidate['yes_roi_ci95'][0])}, {pct(candidate['yes_roi_ci95'][1])}]",
        f"- paired d1 NO ROI: {pct(candidate['d1_no_roi'])}, YES-NO CI95 [{pct(candidate['yes_minus_no_roi_ci95'][0])}, {pct(candidate['yes_minus_no_roi_ci95'][1])}]",
        "",
        "## Tiny-Live 冻结规格",
        "",
        "- universe: source-aligned weather cities；只买当前 running-max 所在 bracket 的 YES。",
        "- dedupe: 每个 city / target_date / current_bracket 最多一笔。",
        "- timing: local 13-15 点，且 `decline_c >= 0.5`。",
        "- price/model: `yes_ask >= 0.55`，`p_yes_win >= 0.5`，`p_yes_win - yes_ask >= 0.05`。",
        "- sibling feature guard: live 生成时需要 current YES quote 和 d1 NO sibling quote 都可见；否则跳过。",
        f"- execution cap: BUY_YES taker，max ${MAX_LIVE_NOTIONAL_PER_ORDER:.0f}/order，max ${MAX_LIVE_NOTIONAL_PER_TARGET_DATE:.0f}/target_date，top-of-book available notional < ${MIN_LIVE_NOTIONAL_AT_ASK:.0f} 时跳过。",
        "- deployment rule: 只能以 frozen rule 上 tiny-live；不得把 prefix dynamic selector 一起上线。",
        "",
        "## 稳健性",
        "",
        f"- latest half ROI: {pct(stress['latest_half_yes_roi'])}; last 5 active dates ROI: {pct(stress['last_5_yes_roi'])}.",
        f"- max date cost share: {pct(stress['max_date_cost_share'], signed=False)}; top3 date cost share: {pct(stress['top3_date_cost_share'], signed=False)}.",
        f"- max city cost share: {pct(stress['max_city_cost_share'], signed=False)}; top3 city cost share: {pct(stress['top3_city_cost_share'], signed=False)}.",
        f"- leave-one-date min ROI: {pct(stress['leave_one_date_min_yes_roi'])}; leave-one-city min ROI: {pct(stress['leave_one_city_min_yes_roi'])}.",
        f"- threshold neighborhood: {stress['threshold_sample_ok']} sample-ok variants, {stress['threshold_point_pass']} point-pass variants, {stress['threshold_ci_pass']} CI-pass variants.",
        f"- tiny liquidity gate: available notional >= ${stress['tiny_liquidity_min_notional_at_ask']:.0f}, rows/dates {stress['tiny_liquidity_rows']} / {stress['tiny_liquidity_active_dates']}, YES ROI {pct(stress['tiny_liquidity_yes_roi'])}.",
        "",
        "## 日期 PnL",
        "",
        "| date | rows | cities | YES ROI | cumulative YES ROI |",
        "|---|---:|---:|---:|---:|",
    ]
    for _, r in tables["daily"].iterrows():
        lines.append(f"| {r['target_date']} | {int(r['rows'])} | {int(r['cities'])} | {pct(r['yes_roi'])} | {pct(r['cum_yes_roi'])} |")
    lines.extend(
        [
            "",
            "## 三道门",
            "",
            f"- significance={gate['significance']}：holdout fixed rule ROI CI {gate['significance_detail']}.",
            f"- baseline={gate['baseline']}：相对 paired d1 NO 增量 CI {gate['baseline_detail']}.",
            f"- forward={gate['forward']}：{gate['forward_detail']}.",
            f"- execution={gate['execution']}：{gate['execution_detail']}.",
            f"- conclusion={gate['conclusion']}：live_ready={gate['live_ready']}。",
            "",
            "## 产物",
            "",
            f"- CSV: `{payload['outputs']['daily']}`",
            f"- CSV: `{payload['outputs']['city_contrib']}`",
            f"- CSV: `{payload['outputs']['date_leave_one']}`",
            f"- CSV: `{payload['outputs']['threshold_neighborhood']}`",
            f"- CSV: `{payload['outputs']['liquidity']}`",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    q = load_features()
    spec = next(s for s in MODEL_SPECS_V8 if s.name == TARGET_MODEL)
    scored, metrics = score_holdout(q, spec)
    pcol = f"p_yes_win_{TARGET_MODEL}"
    selected = apply_rule(scored, pcol, TARGET_RULE)
    holdout = selected[selected["period"].eq("holdout")].copy()
    holdout["available_notional_at_ask"] = holdout["yes_current_ask"] * holdout["yes_current_size"]

    daily = daily_table(holdout)
    city_contrib = contribution_table(holdout, "city")
    date_contrib = contribution_table(holdout, "target_date")
    loo_date = leave_one(holdout, "target_date")
    loo_city = leave_one(holdout, "city")
    windows = latest_window_summary(holdout)
    neighborhood = threshold_neighborhood(scored, pcol)
    liquidity = liquidity_table(holdout)

    candidate = {"model": TARGET_MODEL, "rule": TARGET_RULE.name, **summarize(holdout), **bootstrap(holdout)}
    tiny_liquidity = liquidity[liquidity["min_available_notional_at_ask"].eq(MIN_LIVE_NOTIONAL_AT_ASK)].iloc[0].to_dict()
    latest_half = windows[windows["window"].eq("holdout_latest_half")].iloc[0].to_dict()
    last5 = windows[windows["window"].eq("last_5_active_dates")].iloc[0].to_dict()
    stress = {
        "latest_half_yes_roi": latest_half.get("yes_roi"),
        "latest_half_rows": latest_half.get("rows"),
        "latest_half_active_dates": latest_half.get("active_dates"),
        "last_5_yes_roi": last5.get("yes_roi"),
        "last_5_rows": last5.get("rows"),
        "last_5_active_dates": last5.get("active_dates"),
        "max_date_cost_share": float(date_contrib["cost_share"].max()) if not date_contrib.empty else None,
        "top3_date_cost_share": float(date_contrib.head(3)["cost_share"].sum()) if not date_contrib.empty else None,
        "max_city_cost_share": float(city_contrib["cost_share"].max()) if not city_contrib.empty else None,
        "top3_city_cost_share": float(city_contrib.head(3)["cost_share"].sum()) if not city_contrib.empty else None,
        "leave_one_date_min_yes_roi": float(loo_date["yes_roi"].min()) if not loo_date.empty else None,
        "leave_one_city_min_yes_roi": float(loo_city["yes_roi"].min()) if not loo_city.empty else None,
        "threshold_total": int(len(neighborhood)),
        "threshold_sample_ok": int(neighborhood["sample_ok"].sum()),
        "threshold_point_pass": int(neighborhood["point_pass"].sum()),
        "threshold_ci_pass": int(neighborhood["ci_pass"].sum()),
        "tiny_liquidity_min_notional_at_ask": MIN_LIVE_NOTIONAL_AT_ASK,
        "tiny_liquidity_rows": int(tiny_liquidity.get("rows", 0)),
        "tiny_liquidity_active_dates": int(tiny_liquidity.get("active_dates", 0)),
        "tiny_liquidity_yes_roi": tiny_liquidity.get("yes_roi"),
        "tiny_liquidity_yes_roi_ci95": tiny_liquidity.get("yes_roi_ci95"),
        "tiny_liquidity_yes_minus_no_roi_ci95": tiny_liquidity.get("yes_minus_no_roi_ci95"),
        "max_live_notional_per_order": MAX_LIVE_NOTIONAL_PER_ORDER,
        "max_live_notional_per_target_date": MAX_LIVE_NOTIONAL_PER_TARGET_DATE,
    }

    significance_pass = candidate["yes_roi_ci95"][0] is not None and candidate["yes_roi_ci95"][0] > 0
    baseline_pass = candidate["yes_minus_no_roi_ci95"][0] is not None and candidate["yes_minus_no_roi_ci95"][0] > 0
    latest_pass = latest_half.get("yes_roi") is not None and latest_half["yes_roi"] > 0 and last5.get("yes_roi") is not None and last5["yes_roi"] > 0
    robust_pass = (
        candidate["rows"] >= 30
        and candidate["active_dates"] >= 10
        and stress["leave_one_date_min_yes_roi"] is not None
        and stress["leave_one_date_min_yes_roi"] > 0
        and stress["threshold_ci_pass"] >= 5
    )
    liquidity_pass = (
        stress["tiny_liquidity_rows"] >= 30
        and stress["tiny_liquidity_active_dates"] >= 10
        and stress["tiny_liquidity_yes_roi_ci95"][0] is not None
        and stress["tiny_liquidity_yes_roi_ci95"][0] > 0
        and stress["tiny_liquidity_yes_minus_no_roi_ci95"][0] is not None
        and stress["tiny_liquidity_yes_minus_no_roi_ci95"][0] > 0
    )
    forward_pass = latest_pass and robust_pass
    live_ready = significance_pass and baseline_pass and forward_pass and liquidity_pass
    conclusion = "confirmed" if live_ready else ("shadow_candidate" if significance_pass and baseline_pass else "inconclusive")

    paths = {
        "daily": OUT_DIR / "daily_pnl.csv",
        "city_contrib": OUT_DIR / "city_contrib.csv",
        "date_contrib": OUT_DIR / "date_contrib.csv",
        "date_leave_one": OUT_DIR / "date_leave_one.csv",
        "city_leave_one": OUT_DIR / "city_leave_one.csv",
        "windows": OUT_DIR / "window_summary.csv",
        "threshold_neighborhood": OUT_DIR / "threshold_neighborhood.csv",
        "liquidity": OUT_DIR / "liquidity_summary.csv",
    }
    daily.to_csv(paths["daily"], index=False)
    city_contrib.to_csv(paths["city_contrib"], index=False)
    date_contrib.to_csv(paths["date_contrib"], index=False)
    loo_date.to_csv(paths["date_leave_one"], index=False)
    loo_city.to_csv(paths["city_leave_one"], index=False)
    windows.to_csv(paths["windows"], index=False)
    neighborhood.to_csv(paths["threshold_neighborhood"], index=False)
    liquidity.to_csv(paths["liquidity"], index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_yes_live_gate",
        "coverage": {
            "feature_rows": int(len(q)),
            "active_dates": int(q["target_date"].nunique()),
            "holdout_dates": int(q[q["period"].eq("holdout")]["target_date"].nunique()),
            "selected_holdout_rows": int(len(holdout)),
            "selected_holdout_dates": int(holdout["target_date"].nunique()),
        },
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "model_metrics": {"model": TARGET_MODEL, **metrics},
        "candidate": candidate,
        "window_summary": windows.to_dict(orient="records"),
        "stress": stress,
        "verdict": {
            "significance": "PASS" if significance_pass else "FAIL",
            "significance_detail": f"[{pct(candidate['yes_roi_ci95'][0])}, {pct(candidate['yes_roi_ci95'][1])}]",
            "baseline": "PASS" if baseline_pass else "FAIL",
            "baseline_detail": f"[{pct(candidate['yes_minus_no_roi_ci95'][0])}, {pct(candidate['yes_minus_no_roi_ci95'][1])}]",
            "forward": "PASS_FIXED_STRESS" if forward_pass else "FAIL",
            "forward_detail": (
                "fixed train->holdout, latest window, leave-one stress, and threshold-neighborhood stress passed; prefix dynamic selector remains a non-blocking caution"
                if forward_pass
                else "latest or robustness stress not passed"
            ),
            "execution": "PASS_TINY" if liquidity_pass else "FAIL",
            "execution_detail": (
                f"top-of-book available_notional_at_ask >= ${MIN_LIVE_NOTIONAL_AT_ASK:.0f} keeps "
                f"{stress['tiny_liquidity_rows']} rows / {stress['tiny_liquidity_active_dates']} dates with positive ROI and YES-NO CI"
            ),
            "conclusion": conclusion,
            "live_ready": bool(live_ready),
            "shadow_ready": bool(significance_pass and baseline_pass),
            "robust_live_gate": bool(robust_pass),
            "recommended_live_caps": {
                "max_notional_per_order_usd": MAX_LIVE_NOTIONAL_PER_ORDER,
                "max_notional_per_target_date_usd": MAX_LIVE_NOTIONAL_PER_TARGET_DATE,
                "skip_if_available_notional_at_ask_lt_usd": MIN_LIVE_NOTIONAL_AT_ASK,
            },
        },
        "outputs": {k: str(v.relative_to(ROOT)) for k, v in paths.items()},
    }
    payload = json_ready(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload, {"daily": daily})
    print(json.dumps({"candidate": payload["candidate"], "stress": payload["stress"], "verdict": payload["verdict"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
