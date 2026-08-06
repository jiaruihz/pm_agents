#!/usr/bin/env python3
"""Descriptive PIT, live-entry, weather-regime, and microstructure audit."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / "docs/analysis/2026-07/generated/current_yes_core_carry_no_obs_age_freeze_pre_live_v5/frozen_policy_entries.csv"
OOF = ROOT / "docs/analysis/2026-07/generated/current_yes_core_carry_no_obs_age_freeze_pre_live_v5/oof_states_five_share_cost.csv"
RUNTIME = Path("/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2")
OUT_DIR = ROOT / "docs/analysis/2026-08/generated/core_carry_descriptive_audit_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MD = ROOT / "docs/analysis/2026-08/2026-08-06-current-yes-core-carry-descriptive-microstructure-v1.md"
CURRENT_START = "2026-08-03"
SEED = 20260806
REPS = 5000


def jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def number(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if np.isfinite(out) else float("nan")


def paired_loss_delta(
    frame: pd.DataFrame,
    candidate: str,
    baseline: str,
) -> dict[str, Any]:
    work = frame[["target_date", "label", candidate, baseline]].dropna().copy()
    y = work["label"].to_numpy(float)
    pc = work[candidate].clip(1e-6, 1 - 1e-6).to_numpy(float)
    pb = work[baseline].clip(1e-6, 1 - 1e-6).to_numpy(float)
    work["brier_delta"] = (pc - y) ** 2 - (pb - y) ** 2
    work["logloss_delta"] = -(
        y * np.log(pc) + (1 - y) * np.log(1 - pc)
    ) + (y * np.log(pb) + (1 - y) * np.log(1 - pb))
    blocks = work.groupby("target_date")[["brier_delta", "logloss_delta"]].mean()
    values = blocks.to_numpy(float)
    rng = np.random.default_rng(SEED)
    draws = np.empty((REPS, 2), dtype=float)
    for idx in range(REPS):
        draws[idx] = values[rng.integers(0, len(values), len(values))].mean(axis=0)
    return {
        "rows": int(len(work)),
        "dates": int(work.target_date.nunique()),
        "brier_delta": float(work.brier_delta.mean()),
        "brier_delta_date_block_ci95": np.quantile(draws[:, 0], [0.025, 0.975]).tolist(),
        "logloss_delta": float(work.logloss_delta.mean()),
        "logloss_delta_date_block_ci95": np.quantile(draws[:, 1], [0.025, 0.975]).tolist(),
    }


def daily_stats(frame: pd.DataFrame) -> dict[str, Any]:
    counts = frame.groupby("target_date").size()
    return {
        "signals": int(len(frame)),
        "dates": int(len(counts)),
        "mean": float(counts.mean()),
        "median": float(counts.median()),
        "p90": float(counts.quantile(0.9)),
        "max": int(counts.max()),
        "by_date": {str(k): int(v) for k, v in counts.items()},
    }


def price_stats(frame: pd.DataFrame, *, live: bool = False) -> dict[str, Any]:
    spread = frame["spread"].astype(float)
    result = {
        "avg_bid": float(frame["bid"].mean()),
        "avg_ask": float(frame["ask"].mean()),
        "avg_mid": float(frame["mid"].mean()),
        "avg_spread": float(spread.mean()),
        "median_spread": float(spread.median()),
        "p90_spread": float(spread.quantile(0.9)),
        "spread_1c_rate": float(spread.le(0.01001).mean()),
        "avg_model_probability": float(frame["model_probability"].mean()),
        "avg_model_minus_mid": float((frame.model_probability - frame.mid).mean()),
        "avg_model_minus_ask": float((frame.model_probability - frame.ask).mean()),
    }
    if live:
        matched = frame[frame.actual_fill_price.notna()]
        result.update(
            {
                "matched_takers": int(len(matched)),
                "avg_actual_fill_price": float(matched.actual_fill_price.mean()),
                "avg_model_minus_actual_fill": float(
                    (matched.model_probability - matched.actual_fill_price).mean()
                ),
            }
        )
    else:
        result.update(
            {
                "avg_effective_cost": float(frame.effective_cost.mean()),
                "avg_model_minus_effective_cost": float(
                    (frame.model_probability - frame.effective_cost).mean()
                ),
            }
        )
    return result


def frozen_summary() -> tuple[dict[str, Any], pd.DataFrame]:
    entries = pd.read_csv(FROZEN)
    frame = entries.assign(
        bid=entries.current_yes_bid,
        ask=entries.current_yes_ask,
        mid=entries.market_mid,
        spread=entries.current_yes_ask - entries.current_yes_bid,
        effective_cost=entries.five_share_cost_per_share,
    )
    cities = frame.groupby("city").agg(signals=("label", "size"), wins=("label", "sum"))
    cities["accuracy"] = cities.wins / cities.signals
    oof = pd.read_csv(OOF)
    carry = oof[oof.market_mid.ge(0.80)].copy()
    result = {
        "scope": {
            "rows": int(len(frame)),
            "date_min": str(frame.target_date.min()),
            "date_max": str(frame.target_date.max()),
            "dates": int(frame.target_date.nunique()),
            "cities": int(frame.city.nunique()),
        },
        "daily": daily_stats(frame),
        "prices": price_stats(frame),
        "wins": int(frame.label.sum()),
        "losses": int(frame.label.eq(0).sum()),
        "accuracy": float(frame.label.mean()),
        "cities": cities.sort_values(["signals", "city"], ascending=[False, True])
        .reset_index()
        .to_dict("records"),
        "probability_same_denominator": {
            "core_vs_market_mid": paired_loss_delta(
                carry, "p_core_no_obs_age", "market_mid"
            ),
            "core_vs_no_clock": paired_loss_delta(
                carry, "p_core_no_obs_age", "p_core_no_clock"
            ),
            "core_vs_no_wind": paired_loss_delta(
                carry, "p_core_no_obs_age", "p_core_no_wind"
            ),
            "rows": int(len(carry)),
            "dates": int(carry.target_date.nunique()),
        },
    }
    return result, frame


def actual_fill_price(row: dict[str, Any]) -> float:
    place = ((row.get("exchange_response") or {}).get("place") or {})
    making = number(place.get("makingAmount"))
    taking = number(place.get("takingAmount"))
    if place.get("status") == "matched" and taking > 0:
        return making / taking
    return float("nan")


def score_by_checkpoint() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in jsonl(RUNTIME / "pre_live_scores.jsonl"):
        key = str(row.get("checkpoint_key") or "")
        if key:
            out[key] = row
    return out


def mode_counts(rows: list[dict[str, Any]], name: str) -> dict[str, int]:
    return dict(Counter(str(row.get(name) or "missing") for row in rows).most_common())


def live_summary() -> tuple[dict[str, Any], pd.DataFrame]:
    orders = jsonl(RUNTIME / "live_orders.jsonl")
    attempts = [row for row in jsonl(RUNTIME / "entry_attempts.jsonl") if row.get("status") == "planned"]
    takers = [
        row
        for row in orders
        if row.get("child_order_role") == "taker"
        and not str(row.get("execution_action") or "").startswith("core_carry_maker_")
    ]
    current = [row for row in takers if str(row.get("target_date") or "") >= CURRENT_START]
    records = []
    for row in current:
        bid = number(row.get("best_bid") or row.get("quote_best_bid"))
        ask = number(row.get("best_ask") or row.get("quote_best_ask"))
        records.append(
            {
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "status": row.get("status"),
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2,
                "spread": ask - bid,
                "model_probability": number(row.get("model_token_probability")),
                "actual_fill_price": actual_fill_price(row),
            }
        )
    frame = pd.DataFrame(records)
    scores = score_by_checkpoint()
    current_attempts = [row for row in attempts if str(row.get("target_date") or "") >= CURRENT_START]
    weather = [scores.get(str(row.get("checkpoint_key") or ""), {}) for row in current_attempts]
    weather_numeric = {}
    for key in (
        "forecast_peak_delta_hours_local",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "relative_humidity_pct",
        "minutes_since_running_max",
    ):
        values = pd.Series([number(row.get(key)) for row in weather]).dropna()
        weather_numeric[key] = {
            "mean": float(values.mean()),
            "median": float(values.median()),
            "min": float(values.min()),
            "max": float(values.max()),
        }
    maker_roots = [
        row
        for row in orders
        if row.get("child_order_role") == "maker"
        and str(row.get("target_date") or "") >= CURRENT_START
    ]
    filled_keys = {
        (row.get("city"), row.get("target_date"), row.get("token_id"))
        for row in orders
        if row.get("child_order_role") == "core_carry_maker_terminal"
        and row.get("status") == "filled"
    }
    result = {
        "scope": {
            "start_target_date": CURRENT_START,
            "end_target_date": str(frame.target_date.max()),
            "signals": int(len(frame)),
            "dates": int(frame.target_date.nunique()),
        },
        "daily": daily_stats(frame),
        "prices": price_stats(frame, live=True),
        "cities": frame.groupby("city").size().sort_values(ascending=False).astype(int).to_dict(),
        "weather_modes": {
            "temperature_context_regime": mode_counts(weather, "temperature_context_regime"),
            "intraday_state": mode_counts(weather, "intraday_state"),
            "heating_done_bucket_v1": mode_counts(weather, "heating_done_bucket_v1"),
            "wind_regime": mode_counts(weather, "wind_regime"),
            "moisture_cloud_regime": mode_counts(weather, "moisture_cloud_regime"),
            "precip_state": mode_counts(weather, "precip_state"),
            "numeric": weather_numeric,
        },
        "maker": {
            "intents": len(maker_roots),
            "fills": sum(
                (row.get("city"), row.get("target_date"), row.get("token_id"))
                in filled_keys
                for row in maker_roots
            ),
        },
        "all_live_signal_daily": daily_stats(pd.DataFrame(attempts)),
    }
    return result, frame


def fmt_counts(items: dict[str, int]) -> str:
    return "、".join(f"{key} {value}" for key, value in items.items())


def main() -> int:
    frozen, frozen_frame = frozen_summary()
    live, live_frame = live_summary()
    payload = {
        "denominator_scope": {
            "historical": "136 frozen first-positive city-day entries on 30 target dates",
            "probability": "same 0.80+ market-mid OOF PIT states",
            "live": "Mac raw target dates 2026-08-03 onward; no canonical PnL publication",
        },
        "frozen": frozen,
        "live_current_10_plus_5": live,
        "sizing_action": "keep fixed 10 taker; continuous net-EV sizing remains rejected for live",
        "readiness": {
            "pit_state_and_clocks": "PASS frozen / PASS raw live",
            "canonical_build_identity": "BLOCKED production manifest critical",
            "market_quote": "PASS entry bid/ask and matched taker principal",
            "settlement": "PASS frozen / BLOCKED latest canonical publication",
            "forward": "low sample",
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frozen_frame.to_csv(OUT_DIR / "frozen_entries.csv", index=False)
    live_frame.to_csv(OUT_DIR / "live_entries_current_10_plus_5.csv", index=False)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fp = frozen["prices"]
    lp = live["prices"]
    core_market = frozen["probability_same_denominator"]["core_vs_market_mid"]
    no_clock = frozen["probability_same_denominator"]["core_vs_no_clock"]
    no_wind = frozen["probability_same_denominator"]["core_vs_no_wind"]
    city_top = "、".join(
        f"{row['city']} {row['signals']}" for row in frozen["cities"][:10]
    )
    weather = live["weather_modes"]
    OUT_MD.write_text(
        f"""# Core Carry 触发、价格、天气模式与微观盘口审计 v1

Status: `descriptive / probability baseline mixed / latest canonical blocked / no-live-change`

## 动作

保持 fixed 10 taker；不启用 continuous net-EV sizing。maker 的 queue-preserving + max 2 reprices 是执行层实验，不改变信号、模型概率或 taker sizing。

## 触发频率

- 冻结 PIT 评测：{frozen['daily']['signals']} signals / {frozen['daily']['dates']} target dates，平均 {frozen['daily']['mean']:.2f}/日，中位 {frozen['daily']['median']:.1f}，P90 {frozen['daily']['p90']:.1f}，最高 {frozen['daily']['max']}。
- 全部 raw live：{live['all_live_signal_daily']['signals']} signals / {live['all_live_signal_daily']['dates']} target dates，平均 {live['all_live_signal_daily']['mean']:.2f}/日。
- 当前 10+5 窗口（{CURRENT_START} 起）：{live['daily']['signals']} signals / {live['daily']['dates']} target dates，平均 {live['daily']['mean']:.2f}/日；逐日 {fmt_counts(live['daily']['by_date'])}。

## 入场价格与点差

| scope | avg mid | avg ask | avg actual/effective cost | avg spread | median spread | 1c spread rate | avg model p | p-mid | p-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen 136 | {fp['avg_mid']:.4f} | {fp['avg_ask']:.4f} | {fp['avg_effective_cost']:.4f} | {fp['avg_spread']:.4f} | {fp['median_spread']:.4f} | {fp['spread_1c_rate']:.1%} | {fp['avg_model_probability']:.4f} | {fp['avg_model_minus_mid']:+.4f} | {fp['avg_model_minus_effective_cost']:+.4f} |
| live current | {lp['avg_mid']:.4f} | {lp['avg_ask']:.4f} | {lp['avg_actual_fill_price']:.4f} | {lp['avg_spread']:.4f} | {lp['median_spread']:.4f} | {lp['spread_1c_rate']:.1%} | {lp['avg_model_probability']:.4f} | {lp['avg_model_minus_mid']:+.4f} | {lp['avg_model_minus_actual_fill']:+.4f} |

当前窗 {live['maker']['intents']} 个 maker intents、{live['maker']['fills']} 个已确认 maker fills。点差小不等于 maker 易成交：高概率 carry 的 bid queue 较厚，且 maker cap 经常先于 ask 限制追价。

## 城市分布

- 冻结评测覆盖 {frozen['scope']['cities']} 城；Top 10：{city_top}。最大单城 Istanbul 占 {frozen['cities'][0]['signals'] / frozen['scope']['rows']:.1%}，整体城市集中度不高，但 Istanbul/Karachi/LA 等城市准确率差异仍因样本不足不能转成 city gate。
- 当前 10+5：{fmt_counts(live['cities'])}。只有 {live['daily']['dates']} 个日期，城市切片全部 low-sample，不能做城市 keep/cut。

## 天气模式与模型表达

当前 10+5 的 raw PIT regime：

- temperature context：{fmt_counts(weather['temperature_context_regime'])}
- intraday state：{fmt_counts(weather['intraday_state'])}
- heating done：{fmt_counts(weather['heating_done_bucket_v1'])}
- wind：{fmt_counts(weather['wind_regime'])}
- moisture/cloud：{fmt_counts(weather['moisture_cloud_regime'])}
- precipitation：{fmt_counts(weather['precip_state'])}

冻结模型直接使用的只有 `market_logit + local hour + forecast peak delta + dewpoint depression + wind speed`。rain/cloud、gust、dewpoint trend、warm advection 和完整 path regime 虽已进入 telemetry，但不直接进入 frozen probability，因此模型对这些语义仍是部分表达，不是完整物理模型。

## 模型相对市场

- selected 136：平均 model p {fp['avg_model_probability']:.2%}，market mid {fp['avg_mid']:.2%}，差 {fp['avg_model_minus_mid']:+.2%}；扣 5-share ladder+fee 后平均净 edge {fp['avg_model_minus_effective_cost']:+.2%}。这是 selector 后均值，不能单独证明 alpha。
- 同分母 {core_market['rows']} 个 0.80+ OOF PIT states：core−market Brier Δ {core_market['brier_delta']:+.6f}，date-block 95% CI [{core_market['brier_delta_date_block_ci95'][0]:+.6f}, {core_market['brier_delta_date_block_ci95'][1]:+.6f}]；logloss Δ {core_market['logloss_delta']:+.6f}，CI [{core_market['logloss_delta_date_block_ci95'][0]:+.6f}, {core_market['logloss_delta_date_block_ci95'][1]:+.6f}]。负值才代表模型优于市场。
- full core−no-clock：Brier Δ {no_clock['brier_delta']:+.6f}，CI [{no_clock['brier_delta_date_block_ci95'][0]:+.6f}, {no_clock['brier_delta_date_block_ci95'][1]:+.6f}]；full core−no-wind：{no_wind['brier_delta']:+.6f}，CI [{no_wind['brier_delta_date_block_ci95'][0]:+.6f}, {no_wind['brier_delta_date_block_ci95'][1]:+.6f}]。这回答 peak-clock/wind 是否提供稳定增量。

## 证据边界

- signal funnel：OOF 0.80+ PIT state → positive full-ladder net EV → first city-day signal。
- evidence funnel：frozen rows 有 PIT quote/settlement；latest live 使用 Mac raw bid/ask/matched taker evidence。
- 当前 production manifest 仍 critical，latest canonical settlement/PnL 不在本报告更新；旧的 8/03 cutoff 后 PnL 结论不外推到 8/06 新信号。
- `significance` 见同分母 CI；`baseline` 为同 row market mid；`forward=low-sample`；`conclusion=inconclusive / no-live-change`。
""",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
