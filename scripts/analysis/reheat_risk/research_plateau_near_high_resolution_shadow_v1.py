#!/usr/bin/env python3
"""Plateau-near-high resolution shadow ledger.

This is not a trading rule. It records high-plateau states so we can later
learn whether a plateau resolves by reheating through the current bracket or by
holding/fading.
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
STATE_ROWS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/plateau_near_high_resolution_shadow_v1"
OUT_LEDGER = OUT_DIR / "plateau_shadow_rows.csv"
OUT_SUMMARY = OUT_DIR / "summary.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-plateau-near-high-resolution-shadow-v1.md"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    return value


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+.2f}"


def md_table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.loc[:, [c for c in cols if c in df.columns]].copy()
    if limit is not None:
        view = view.head(limit)
    for col in view.columns:
        if col in {"win_rate", "no_roi", "yes_roi", "future_break_rate"}:
            view[col] = view[col].map(pct)
        elif col.endswith("_usd") or col in {"avg_no_ask", "avg_yes_ask"}:
            if col.startswith("avg_"):
                view[col] = view[col].map(lambda x: "NA" if pd.isna(x) else f"{float(x):.3f}")
            else:
                view[col] = view[col].map(money)
    return "\n".join(
        [
            "| " + " | ".join(view.columns) + " |",
            "| " + " | ".join(["---"] * len(view.columns)) + " |",
            *["| " + " | ".join(str(v) for v in row) + " |" for row in view.astype(str).to_numpy()],
        ]
    )


def classify_shadow_hint(row: pd.Series) -> str:
    peak_delta = pd.to_numeric(row.get("forecast_peak_delta_hours_local"), errors="coerce")
    gap = pd.to_numeric(row.get("forecast_gap_to_running_native"), errors="coerce")
    trend3 = pd.to_numeric(row.get("temp_trend_3h_f"), errors="coerce")
    moisture = str(row.get("moisture_cloud_regime"))
    if pd.notna(peak_delta) and peak_delta >= 0 and pd.notna(gap) and gap >= 1.0 and pd.notna(trend3) and trend3 > 0:
        if moisture != "humid_overcast_suppression":
            return "rebreak_no_watch"
    if pd.notna(peak_delta) and peak_delta < 0 and (pd.isna(gap) or gap <= 1.0):
        return "hold_or_fade_watch"
    return "unresolved_watch"


def summarize(frame: pd.DataFrame, by: list[str], label: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(by, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        no_payoff = pd.to_numeric(group["current_bracket_no_payoff"], errors="coerce")
        no_ask = pd.to_numeric(group["current_bracket_no_ask"], errors="coerce")
        yes_payoff = pd.to_numeric(group["current_yes_payoff"], errors="coerce")
        yes_ask = pd.to_numeric(group["current_yes_ask"], errors="coerce")
        no_pnl = ((no_payoff * (1.0 / no_ask) - 1.0) * 5.0).where(no_ask.gt(0))
        yes_pnl = ((yes_payoff * (1.0 / yes_ask) - 1.0) * 5.0).where(yes_ask.gt(0))
        no_valid = no_ask.notna()
        yes_valid = yes_ask.notna()
        row = {
            "slice": label,
            "rows": int(len(group)),
            "dates": int(group["target_date"].nunique()),
            "cities": int(group["city"].nunique()),
            "future_break_rate": float(pd.to_numeric(group["future_break_any"], errors="coerce").mean()),
            "no_rows": int(no_valid.sum()),
            "no_wins": int(no_payoff.where(no_valid).fillna(0).sum()),
            "no_win_rate": float(no_payoff.where(no_valid).mean()),
            "avg_no_ask": float(no_ask.mean()),
            "no_pnl_usd": float(no_pnl.sum(skipna=True)),
            "no_roi": float(no_pnl.sum(skipna=True) / (5.0 * no_valid.sum())) if no_valid.sum() else None,
            "yes_rows": int(yes_valid.sum()),
            "yes_wins": int(yes_payoff.where(yes_valid).fillna(0).sum()),
            "yes_win_rate": float(yes_payoff.where(yes_valid).mean()),
            "avg_yes_ask": float(yes_ask.mean()),
            "yes_pnl_usd": float(yes_pnl.sum(skipna=True)),
            "yes_roi": float(yes_pnl.sum(skipna=True) / (5.0 * yes_valid.sum())) if yes_valid.sum() else None,
        }
        for col, val in zip(by, keys):
            row[col] = val
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["rows", "no_pnl_usd"], ascending=[False, False]).reset_index(drop=True)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = pd.read_csv(STATE_ROWS, low_memory=False)
    plateau = state[
        state["running_max_state"].eq("near_high_plateau") | state["intraday_state"].eq("plateau_near_high")
    ].copy()
    plateau["shadow_hint"] = plateau.apply(classify_shadow_hint, axis=1)
    cols = [
        "target_date",
        "city",
        "decision_hour_local",
        "decision_snapshot_ts_utc",
        "unit",
        "current_native",
        "running_native",
        "minutes_since_running_max",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "forecast_peak_delta_hours_local",
        "forecast_gap_to_running_native",
        "remaining_heat_native",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "city_family",
        "shadow_hint",
        "current_bracket",
        "current_bracket_no_ask",
        "current_bracket_no_payoff",
        "current_yes_ask",
        "current_yes_payoff",
        "future_break_any",
        "future_break_step",
    ]
    plateau.loc[:, [c for c in cols if c in plateau.columns]].to_csv(OUT_LEDGER, index=False)

    by_hint = summarize(plateau, ["shadow_hint"], "by_shadow_hint")
    by_state = summarize(plateau, ["running_max_state", "intraday_state"], "by_state")
    by_moisture = summarize(plateau, ["moisture_cloud_regime"], "by_moisture")
    recent = plateau[plateau["target_date"].astype(str).ge("2026-06-21")].copy()

    payload = {
        "generated_at_utc": now_utc(),
        "source": str(STATE_ROWS.relative_to(ROOT)),
        "date_min": str(plateau["target_date"].min()) if not plateau.empty else None,
        "date_max": str(plateau["target_date"].max()) if not plateau.empty else None,
        "rows": int(len(plateau)),
        "dates": int(plateau["target_date"].nunique()) if not plateau.empty else 0,
        "cities": int(plateau["city"].nunique()) if not plateau.empty else 0,
        "recent_2026_06_21_plus_rows": int(len(recent)),
        "settlement_note": "Rows after 2026-06-26 are not settled in current DB if absent from this ledger.",
        "by_shadow_hint": by_hint.to_dict("records"),
        "by_state": by_state.to_dict("records"),
        "by_moisture": by_moisture.to_dict("records"),
        "outputs": {
            "ledger": str(OUT_LEDGER.relative_to(ROOT)),
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "shadow_ledger_only",
            "live_ready": False,
            "reason": "Plateau states are a separate resolution problem; sample is thin and not a v3 trading rule.",
        },
    }
    OUT_SUMMARY.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Plateau Near-High Resolution Shadow V1",
        "",
        "## 结论",
        "",
        f"修复 running-max clock 后，plateau 不再是之前那 14 笔混杂 stale 样本；当前 atlas 里 `near_high_plateau/plateau_near_high` 共 {len(plateau)} 个 state rows，覆盖 {payload['date_min']}..{payload['date_max']}。这是 shadow ledger，不是交易规则。",
        "",
        "人话：plateau 是“温度贴着高点横住”，核心问题不是直接买 NO 或 YES，而是观察它之后会不会 rebreak。现在只记录，不进 v3 live。",
        "",
        "## By Shadow Hint",
        "",
        md_table(by_hint, ["shadow_hint", "rows", "dates", "cities", "future_break_rate", "no_rows", "no_win_rate", "avg_no_ask", "no_pnl_usd", "no_roi", "yes_rows", "yes_win_rate", "avg_yes_ask", "yes_pnl_usd", "yes_roi"]),
        "",
        "## By State",
        "",
        md_table(by_state, ["running_max_state", "intraday_state", "rows", "dates", "cities", "future_break_rate", "no_rows", "no_win_rate", "avg_no_ask", "no_pnl_usd", "no_roi"]),
        "",
        "## By Moisture",
        "",
        md_table(by_moisture, ["moisture_cloud_regime", "rows", "dates", "cities", "future_break_rate", "no_rows", "no_win_rate", "avg_no_ask", "no_pnl_usd", "no_roi"]),
        "",
        "Verdict: `shadow_ledger_only`。下一步用 forward 新样本校准 `rebreak_no_watch` / `hold_or_fade_watch`，不要把 plateau 粗暴并入 runway NO。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
