#!/usr/bin/env python3
"""Audit frozen core-carry losses and stress additive maker selection.

The frozen strategy result is a taker-only OOF replay.  This audit keeps that
denominator fixed, describes the six losing city-days, and treats maker fills
as an execution overlay whose outcome-dependent fill probability is unknown.
It deliberately does not infer fills from future quote touches.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_residual_entry_v2 as residual,
)


ENTRY_PATH = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_no_obs_age_freeze_pre_live_v5/frozen_policy_entries.csv"
)
OUT_DIR = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_loss_maker_selection_v1"
)
OUT_JSON = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-27-current-yes-core-carry-loss-maker-selection-v1.json"
)
OUT_MD = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-27-current-yes-core-carry-loss-maker-selection-v1.md"
)
LIVE_INSTANCE = "current_yes_core_carry_tiny_live_v2"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def bracket_upper(value: Any) -> float:
    text = str(value)
    try:
        return float(text.split("-")[-1].strip())
    except ValueError:
        return float("nan")


def load_entries() -> pd.DataFrame:
    entries = pd.read_csv(ENTRY_PATH)
    universe = residual.prepare_universe()
    keys = ["city", "target_date", "decision_snapshot_ts_utc"]
    features = [
        "unit",
        "final_native",
        "running_native",
        "forecast_gap_to_running_native",
        "forecast_peak_delta_hours_local",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "relative_humidity_pct",
        "sky_cover_code",
        "decline_native",
        "minutes_since_running_max",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "gap_debiased_steps",
        "path_faded",
        "running_max_state",
    ]
    context = universe[keys + features].drop_duplicates(keys)
    frame = entries.merge(context, on=keys, how="left", validate="one_to_one")
    frame["loss"] = 1 - frame["label"]
    frame["spread"] = frame["current_yes_ask"] - frame["current_yes_bid"]
    frame["bracket_upper"] = frame["current_bracket"].map(bracket_upper)
    frame["loss_direction"] = np.where(
        frame["label"].eq(1),
        "won",
        np.where(
            frame["final_native"].gt(frame["bracket_upper"]),
            "upward_overshoot",
            "below_exact_bracket",
        ),
    )
    return frame


def slice_row(frame: pd.DataFrame, name: str, mask: pd.Series) -> dict[str, Any]:
    selected = frame[mask.fillna(False)]
    other = frame[~mask.fillna(False)]
    return {
        "slice": name,
        "selected_n": len(selected),
        "selected_losses": int(selected["loss"].sum()),
        "selected_loss_rate": float(selected["loss"].mean()) if len(selected) else None,
        "other_n": len(other),
        "other_losses": int(other["loss"].sum()),
        "other_loss_rate": float(other["loss"].mean()) if len(other) else None,
        "post_hoc": True,
    }


def maker_stress(frame: pd.DataFrame, observed_fill_rate: float) -> dict[str, Any]:
    # The live maker can chase only as high as the trigger midpoint/model cap.
    # Use trigger midpoint as the conservative (highest-cost) maker fill price.
    taker_cost = 5.0 * frame["five_share_cost_per_share"]
    taker_pnl = 5.0 * (frame["label"] - frame["five_share_cost_per_share"])
    maker_cost = 5.0 * frame["market_mid"]
    maker_pnl = 5.0 * (frame["label"] - frame["market_mid"])
    wins = int(frame["label"].sum())
    losses = int(frame["loss"].sum())

    scenarios = [
        ("all_maker_fills", 1.0, 1.0),
        ("outcome_neutral_at_observed_fill_rate", observed_fill_rate, observed_fill_rate),
        (
            "max_loss_selected_at_same_overall_fill_rate",
            max(0.0, (observed_fill_rate * len(frame) - losses) / wins),
            1.0,
        ),
        ("losses_only_fill", 0.0, 1.0),
    ]
    output = []
    for name, q_win, q_loss in scenarios:
        weights = frame["label"] * q_win + frame["loss"] * q_loss
        overlay_cost = float((maker_cost * weights).sum())
        overlay_pnl = float((maker_pnl * weights).sum())
        combined_cost = float(taker_cost.sum() + overlay_cost)
        combined_pnl = float(taker_pnl.sum() + overlay_pnl)
        output.append(
            {
                "scenario": name,
                "maker_fill_probability_win": q_win,
                "maker_fill_probability_loss": q_loss,
                "expected_maker_fills": float(weights.sum()),
                "maker_cost_usd": overlay_cost,
                "maker_pnl_usd": overlay_pnl,
                "combined_cost_usd": combined_cost,
                "combined_pnl_usd": combined_pnl,
                "combined_roi": combined_pnl / combined_cost,
            }
        )
    return {
        "price_assumption": "maker fills at trigger midpoint cap; no taker fee",
        "fill_assumption": (
            "stress expectation only; historical quote touch is not treated as a fill"
        ),
        "taker_only": {
            "cost_usd": float(taker_cost.sum()),
            "pnl_usd": float(taker_pnl.sum()),
            "roi": float(taker_pnl.sum() / taker_cost.sum()),
        },
        "scenarios": output,
    }


def live_fill_snapshot() -> dict[str, Any]:
    db = ROOT / "runtime/weather.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT maker_only, COUNT(*) AS fills,
               COUNT(DISTINCT city || '|' || target_date) AS city_days,
               SUM(fill_qty) AS shares,
               SUM(cost_usd) AS cost_usd,
               SUM(CASE WHEN settled=1 THEN 1 ELSE 0 END) AS settled_fills
        FROM fact_trades
        WHERE instance_id=?
        GROUP BY maker_only
        """,
        (LIVE_INSTANCE,),
    ).fetchall()
    summary = {("maker" if row["maker_only"] else "taker"): dict(row) for row in rows}
    taker_city_days = int(summary.get("taker", {}).get("city_days") or 0)
    maker_city_days = int(summary.get("maker", {}).get("city_days") or 0)
    return {
        "instance_id": LIVE_INSTANCE,
        "by_leg": summary,
        "maker_fill_rate_per_taker_city_day": (
            maker_city_days / taker_city_days if taker_city_days else None
        ),
        "settlement_warning": (
            "No live maker fill is settled in canonical fact_trades; "
            "maker win/loss selection is not estimable yet."
        ),
    }


def coverage_gate_snapshot() -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts/analysis/execution_quality/"
                "weather_clob_fill_coverage_gate.py"
            ),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "gate_pass": False,
            "fail_reasons": ["coverage_gate_output_not_json"],
            "stderr": completed.stderr.strip(),
        }
    payload["exit_code"] = completed.returncode
    return payload


def main() -> int:
    frame = load_entries()
    live = live_fill_snapshot()
    observed_fill_rate = float(live["maker_fill_rate_per_taker_city_day"] or 0.0)
    slices = [
        slice_row(frame, "market_mid_lt_0.85", frame["market_mid"].lt(0.85)),
        slice_row(
            frame,
            "minutes_since_running_max_lt_60",
            frame["minutes_since_running_max"].lt(60),
        ),
        slice_row(
            frame,
            "forecast_peak_passed_1.5_to_2.5h",
            frame["forecast_peak_delta_hours_local"].ge(1.5)
            & frame["forecast_peak_delta_hours_local"].lt(2.5),
        ),
        slice_row(frame, "path_faded", frame["path_faded"].eq(True)),
    ]
    loss_columns = [
        "city",
        "target_date",
        "decision_hour_local",
        "current_bracket",
        "final_native",
        "loss_direction",
        "market_mid",
        "five_share_cost_per_share",
        "model_probability",
        "model_edge_after_fee_and_depth",
        "forecast_peak_delta_hours_local",
        "minutes_since_running_max",
        "decline_native",
        "path_faded",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "forecast_gap_to_running_native",
    ]
    losses = frame[frame["loss"].eq(1)][loss_columns].copy()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    losses.to_csv(OUT_DIR / "loss_cases.csv", index=False)
    pd.DataFrame(slices).to_csv(OUT_DIR / "post_hoc_slices.csv", index=False)
    payload = {
        "data_snapshot": {
            "frozen_entries": str(ENTRY_PATH.relative_to(ROOT)),
            "city_days": len(frame),
            "target_dates": int(frame["target_date"].nunique()),
            "wins": int(frame["label"].sum()),
            "losses": int(frame["loss"].sum()),
            "grain": "first positive-EV city-day taker expression",
        },
        "loss_direction": frame[frame["loss"].eq(1)]["loss_direction"]
        .value_counts()
        .to_dict(),
        "loss_cases": losses.to_dict("records"),
        "post_hoc_slices": slices,
        "live_maker_evidence": live,
        "maker_stress": maker_stress(frame, observed_fill_rate),
        "verdict": {
            "maker_adverse_selection": "plausible_but_not_yet_estimable",
            "reason": (
                "all six taker losses were upward overshoots, exactly the path in which "
                "current-YES bids can be hit while the thesis deteriorates; live maker "
                "settlements are still zero"
            ),
            "live_action": "do_not_size_up; keep maker and taker performance separate",
            "hard_filter_action": "none; six post-hoc losses are insufficient",
            "conclusion": "inconclusive",
        },
        "coverage_gate": coverage_gate_snapshot(),
    }
    OUT_JSON.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    stress = {row["scenario"]: row for row in payload["maker_stress"]["scenarios"]}
    lines = [
        "# Current-YES core carry：loss 形态与 maker adverse-selection 审计",
        "",
        "Status: `inconclusive / no live sizing change`",
        "",
        "## 结论",
        "",
        "- 冻结 136 个 city-day 是 taker-only；maker 不在 +4.67% ROI 中。",
        "- 6 个 loss 全部是 current exact bracket 后续向上 overshoot，不是低于该档。",
        "- maker adverse selection 在机制上成立：thesis 变差时卖单更可能击中 current-YES bid；"
        "但当前 live maker 只有少量 fill 且 0 个 canonical settled，尚不能估计条件成交率。",
        "- 不增加 maker size；maker/taker 继续分账。6 个事后 loss 不足以增加 hard gate。",
        "",
        "## Loss 特征（仅解释，不作 gate）",
        "",
        "| slice | slice loss | complement loss |",
        "|---|---:|---:|",
    ]
    for row in slices:
        lines.append(
            f"| {row['slice']} | {row['selected_losses']}/{row['selected_n']} "
            f"({row['selected_loss_rate']:.1%}) | {row['other_losses']}/{row['other_n']} "
            f"({row['other_loss_rate']:.1%}) |"
        )
    lines.extend(
        [
            "",
            "共同形态是价格较低、running high 尚年轻、forecast peak 只过去约 1–2 小时，"
            "并且大多没有形成 mature fade。具体逐笔见 generated `loss_cases.csv`。",
            "",
            "## Maker 压力测试",
            "",
            "使用 trigger midpoint 作为 maker 允许追到的最高成本；这是压力情景，不把未来 touch 当 fill。",
            "",
            "| scenario | combined ROI | expected maker fills |",
            "|---|---:|---:|",
            f"| taker only | {payload['maker_stress']['taker_only']['roi']:.2%} | 0 |",
            f"| outcome-neutral at observed fill rate | "
            f"{stress['outcome_neutral_at_observed_fill_rate']['combined_roi']:.2%} | "
            f"{stress['outcome_neutral_at_observed_fill_rate']['expected_maker_fills']:.1f} |",
            f"| losses always fill, same overall fill rate | "
            f"{stress['max_loss_selected_at_same_overall_fill_rate']['combined_roi']:.2%} | "
            f"{stress['max_loss_selected_at_same_overall_fill_rate']['expected_maker_fills']:.1f} |",
            f"| only losses fill | {stress['losses_only_fill']['combined_roi']:.2%} | "
            f"{stress['losses_only_fill']['expected_maker_fills']:.1f} |",
            "",
            "## Evidence 边界",
            "",
            "- signal funnel：冻结 136 city-days / 30 target dates / 6 losses。",
            f"- live evidence funnel：taker city-days "
            f"{live.get('by_leg', {}).get('taker', {}).get('city_days', 0)}；maker fill city-days "
            f"{live.get('by_leg', {}).get('maker', {}).get('city_days', 0)}；settled maker fills 0。",
            f"- CLOB fill coverage gate：`gate_pass="
            f"{str(payload['coverage_gate'].get('gate_pass')).lower()}`；"
            "当前失败来自一笔本策略外 Busan 旧 fill 的 fee lineage unknown，"
            "因此本报告不发布 live PnL。",
            "",
            "三门：`significance=NA`、`baseline=NA`、`forward=FAIL`，"
            "`conclusion=inconclusive`。动作：不扩大 maker，继续独立采集真实 fill/markout/settlement。",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(json_ready(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
