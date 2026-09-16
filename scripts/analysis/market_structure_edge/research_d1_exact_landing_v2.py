#!/usr/bin/env python3
"""Wide-denominator d1 exact-landing residual research.

This is the successor to the d1>=0.80 first-signal calibration.  It trains on
the wider d1 expression domain (direct d1 YES mid >= 0.50), with one total
training weight per city-day, and evaluates the frozen high-mid first signal
cohort separately.  The target is the exact d1 bracket, not "reaches d1".
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_d1_overshoot_hazard_v1 as hz,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_d1_exact_residual_strategy_v1 as prior,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_market_residual_exact_router_v1 as router,
)


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/d1_exact_landing_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-24-research-d1-exact-landing-v2.md"
MID_FLOOR = 0.50
MIN_TRAIN_DATES = 12
MODEL_C = 0.1
EDGE_BUFFER = 0.02
BOOTSTRAP_DRAWS = 4000
SEED = 20260724

MARKET = ["d1_market_logit"]
CARRY_CORE = [
    "decision_hour_local",
    "forecast_peak_delta_hours_local",
    "dewpoint_depression_f",
    "wind_speed_kt",
]
LANDING_GEOMETRY = [
    "d1_required_gap_steps",
    "forecast_gap_to_running_steps",
    "forecast_ceiling_to_d2_steps",
]
MODEL_SPECS = {
    "d1_market_calibrated": MARKET,
    "d1_market_plus_carry_core": MARKET + CARRY_CORE,
    "d1_market_plus_landing_geometry": MARKET + LANDING_GEOMETRY,
    "d1_market_plus_core_landing": MARKET + CARRY_CORE + LANDING_GEOMETRY,
}


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def markdown(frame: pd.DataFrame) -> str:
    return router._markdown_table(frame)


def build_pipeline(features: list[str]) -> Pipeline:
    return Pipeline(
        [
            (
                "pre",
                ColumnTransformer(
                    [
                        (
                            "numeric",
                            Pipeline(
                                [
                                    (
                                        "imputer",
                                        SimpleImputer(
                                            strategy="median",
                                            add_indicator=True,
                                        ),
                                    ),
                                    ("scale", StandardScaler()),
                                ]
                            ),
                            features,
                        )
                    ]
                ),
            ),
            (
                "model",
                LogisticRegression(
                    C=MODEL_C,
                    solver="lbfgs",
                    max_iter=1000,
                    random_state=SEED,
                ),
            ),
        ]
    )


def probability(model: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    probabilities = model.predict_proba(frame)
    classes = list(model.named_steps["model"].classes_)
    return probabilities[:, classes.index(1)]


def engineer(states: pd.DataFrame) -> pd.DataFrame:
    frame = router.engineer(states)
    frame["d1_market_probability"] = pd.to_numeric(
        frame["d1_yes_mid_trigger"], errors="coerce"
    ).clip(1e-6, 1 - 1e-6)
    frame["d1_market_logit"] = hz._logit(frame["d1_market_probability"])
    d1 = pd.to_numeric(frame["d1_settlement_threshold_native"], errors="coerce")
    d2 = pd.to_numeric(frame["d2_settlement_threshold_native"], errors="coerce")
    step = (d2 - d1).abs().replace(0.0, math.nan)
    frame["native_landing_step"] = step
    frame["d1_required_gap_steps"] = pd.to_numeric(
        frame["d1_required_gap_native"], errors="coerce"
    ) / step
    frame["forecast_gap_to_running_steps"] = pd.to_numeric(
        frame["forecast_gap_to_running_native"], errors="coerce"
    ) / step
    frame["forecast_ceiling_to_d2_steps"] = pd.to_numeric(
        frame["forecast_ceiling_margin_to_d2"], errors="coerce"
    ) / step
    frame["exact_win"] = frame["label"].eq(hz.CLASSES[1]).astype(int)
    frame["entry_cost"] = pd.to_numeric(frame["d1_yes_ask_exec"], errors="coerce")
    frame["entry_cost"] += hz._weather_fee(frame["entry_cost"])
    return frame


def wide_universe(frame: pd.DataFrame) -> pd.DataFrame:
    universe = frame[
        frame["model_eligible"]
        & frame["label"].isin(hz.CLASSES)
        & frame["d1_market_probability"].ge(MID_FLOOR)
        & frame["entry_cost"].notna()
    ].copy()
    counts = universe.groupby(["city", "target_date"])["city"].transform("size")
    universe["city_day_weight"] = 1.0 / counts.to_numpy(float)
    return universe


def expanding_oof(universe: pd.DataFrame) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    dates = sorted(universe["target_date"].unique())
    for index, date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = universe[universe["target_date"].lt(date)]
        test = universe[universe["target_date"].eq(date)]
        if train["exact_win"].nunique() < 2:
            continue
        columns = hz.KEY + [
            "label",
            "exact_win",
            "d1_market_probability",
            "entry_cost",
            "city_day_weight",
        ]
        raw = test[columns].copy()
        raw["model"] = "d1_market_raw"
        raw["p_exact"] = raw["d1_market_probability"]
        outputs.append(raw)
        for name, features in MODEL_SPECS.items():
            model = build_pipeline(features)
            model.fit(
                train[features],
                train["exact_win"],
                model__sample_weight=train["city_day_weight"],
            )
            block = test[columns].copy()
            block["model"] = name
            block["p_exact"] = probability(model, test[features])
            outputs.append(block)
    scored = pd.concat(outputs, ignore_index=True)
    scored["logloss"] = -(
        scored["exact_win"] * np.log(scored["p_exact"].clip(1e-9, 1))
        + (1 - scored["exact_win"])
        * np.log((1 - scored["p_exact"]).clip(1e-9, 1))
    )
    scored["brier"] = np.square(scored["p_exact"] - scored["exact_win"])
    scored["model_edge"] = scored["p_exact"] - scored["entry_cost"]
    return scored


def weighted_date_pair_summary(scored: pd.DataFrame) -> pd.DataFrame:
    baseline = scored[scored["model"].eq("d1_market_raw")][
        hz.KEY + ["logloss", "brier", "city_day_weight"]
    ].rename(columns={"logloss": "market_logloss", "brier": "market_brier"})
    rows = []
    for name in MODEL_SPECS:
        joined = scored[scored["model"].eq(name)].merge(
            baseline.drop(columns="city_day_weight"),
            on=hz.KEY,
            validate="one_to_one",
        )
        daily = []
        for date, group in joined.groupby("target_date"):
            weight = group["city_day_weight"].to_numpy(float)
            daily.append(
                {
                    "target_date": date,
                    "market_logloss": np.average(group["market_logloss"], weights=weight),
                    "model_logloss": np.average(group["logloss"], weights=weight),
                    "market_brier": np.average(group["market_brier"], weights=weight),
                    "model_brier": np.average(group["brier"], weights=weight),
                }
            )
        daily_frame = pd.DataFrame(daily)
        daily_frame["ll_delta"] = daily_frame["model_logloss"] - daily_frame["market_logloss"]
        daily_frame["br_delta"] = daily_frame["model_brier"] - daily_frame["market_brier"]
        rng = np.random.default_rng(SEED + len(rows))
        values = daily_frame[["ll_delta", "br_delta"]].to_numpy(float)
        boot = np.empty((BOOTSTRAP_DRAWS, 2))
        for draw in range(BOOTSTRAP_DRAWS):
            boot[draw] = values[rng.integers(0, len(values), len(values))].mean(axis=0)
        rows.append(
            {
                "model": name,
                "rows": len(joined),
                "city_days": int(joined.groupby(["city", "target_date"]).ngroups),
                "dates": len(daily_frame),
                "market_logloss": daily_frame["market_logloss"].mean(),
                "model_logloss": daily_frame["model_logloss"].mean(),
                "logloss_delta": daily_frame["ll_delta"].mean(),
                "logloss_delta_ci_low": np.quantile(boot[:, 0], 0.025),
                "logloss_delta_ci_high": np.quantile(boot[:, 0], 0.975),
                "market_brier": daily_frame["market_brier"].mean(),
                "model_brier": daily_frame["model_brier"].mean(),
                "brier_delta": daily_frame["br_delta"].mean(),
                "brier_delta_ci_low": np.quantile(boot[:, 1], 0.025),
                "brier_delta_ci_high": np.quantile(boot[:, 1], 0.975),
            }
        )
    return pd.DataFrame(rows)


def attach_frozen(scored: pd.DataFrame, frozen: pd.DataFrame) -> pd.DataFrame:
    canonical = frozen[hz.KEY + ["label", "cost"]].copy()
    canonical = canonical.rename(columns={"cost": "frozen_cost"})
    result = scored.merge(canonical, on=hz.KEY + ["label"], how="inner", validate="many_to_one")
    result["entry_cost"] = result["frozen_cost"]
    result["model_edge"] = result["p_exact"] - result["entry_cost"]
    return result


def policy_summary(scored: pd.DataFrame, *, label: str) -> pd.DataFrame:
    rows = []
    for model, group in scored.groupby("model"):
        for period, part in [
            ("all_oof", group),
            ("pre_forward", group[group["target_date"].lt(hz.FORWARD_START)]),
            ("historical_forward", group[group["target_date"].ge(hz.FORWARD_START)]),
        ]:
            trade = part[part["model_edge"].gt(EDGE_BUFFER)].copy()
            pnl = trade["exact_win"] - trade["entry_cost"]
            cost = float(trade["entry_cost"].sum())
            rows.append(
                {
                    "evaluation": label,
                    "model": model,
                    "period": period,
                    "rows": len(part),
                    "trades": len(trade),
                    "wins": int(trade["exact_win"].sum()),
                    "overshoots": int(trade["label"].eq(hz.CLASSES[2]).sum()),
                    "stalls": int(trade["label"].eq(hz.CLASSES[0]).sum()),
                    "cost": cost,
                    "pnl": float(pnl.sum()),
                    "roi": float(pnl.sum() / cost) if cost else math.nan,
                }
            )
    return pd.DataFrame(rows)


def live_features(states: pd.DataFrame) -> pd.DataFrame:
    live = prior.prepare_live(states)
    if live.empty:
        return live
    d1 = pd.to_numeric(live["d1_settlement_threshold_native"], errors="coerce")
    d2 = pd.to_numeric(live["d2_settlement_threshold_native"], errors="coerce")
    step = (d2 - d1).abs().replace(0.0, math.nan)
    live["d1_market_probability"] = pd.to_numeric(
        live["d1_yes_mid_trigger"], errors="coerce"
    ).clip(1e-6, 1 - 1e-6)
    live["d1_market_logit"] = hz._logit(live["d1_market_probability"])
    live["d1_required_gap_steps"] = pd.to_numeric(live["d1_required_gap_native"], errors="coerce") / step
    live["forecast_gap_to_running_steps"] = pd.to_numeric(live["forecast_gap_to_running_native"], errors="coerce") / step
    live["forecast_ceiling_to_d2_steps"] = pd.to_numeric(live["forecast_ceiling_margin_to_d2"], errors="coerce") / step
    return live


def score_live(universe: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    if live.empty:
        return live
    columns = hz.KEY + ["label", "exact_win", "d1_market_probability", "entry_cost"]
    raw = live[columns].copy()
    raw["model"] = "d1_market_raw"
    raw["p_exact"] = raw["d1_market_probability"]
    blocks = [raw]
    for name, features in MODEL_SPECS.items():
        model = build_pipeline(features)
        model.fit(
            universe[features],
            universe["exact_win"],
            model__sample_weight=universe["city_day_weight"],
        )
        block = live[columns].copy()
        block["model"] = name
        block["p_exact"] = probability(model, live[features])
        blocks.append(block)
    result = pd.concat(blocks, ignore_index=True)
    result["logloss"] = -(
        result["exact_win"] * np.log(result["p_exact"].clip(1e-9, 1))
        + (1 - result["exact_win"]) * np.log((1 - result["p_exact"]).clip(1e-9, 1))
    )
    result["brier"] = np.square(result["p_exact"] - result["exact_win"])
    result["model_edge"] = result["p_exact"] - result["entry_cost"]
    result["trade_edge_002"] = result["model_edge"].gt(EDGE_BUFFER)
    result["pnl_edge_002"] = np.where(
        result["trade_edge_002"], result["exact_win"] - result["entry_cost"], 0.0
    )
    return result


def write_report(
    audit: dict[str, Any],
    wide_scores: pd.DataFrame,
    frozen_scores: pd.DataFrame,
    frozen_policy: pd.DataFrame,
    live: pd.DataFrame,
) -> None:
    best_wide = wide_scores.sort_values("logloss_delta").iloc[0]
    best_frozen = frozen_scores.sort_values("logloss_delta").iloc[0]
    live_summary = (
        live.groupby("model")
        .agg(
            rows=("label", "size"),
            logloss=("logloss", "mean"),
            brier=("brier", "mean"),
            trades=("trade_edge_002", "sum"),
            wins=("exact_win", lambda value: int(value[live.loc[value.index, "trade_edge_002"]].sum())),
            pnl=("pnl_edge_002", "sum"),
        )
        .reset_index()
        if not live.empty
        else pd.DataFrame()
    )
    OUT_MD.write_text(
        f"""# d1 exact landing v2：宽分母两段 landing 物理检验

Status: `research / no live change`

## 结论

**Verdict: 当前可得物理因子下 `d1 exact landing` 没有独立 market residual。**
宽分母中最接近 market 的模型是 `{best_wide['model']}`，但 date-equal logloss
delta 仍为 `{best_wide['logloss_delta']:+.4f}`，95% CI
`[{best_wide['logloss_delta_ci_low']:+.4f}, {best_wide['logloss_delta_ci_high']:+.4f}]`；
正值表示劣于 market。冻结 high-mid first-signal 中最接近的模型
`{best_frozen['model']}` 也为 `{best_frozen['logloss_delta']:+.4f}`。

因此不新增 d1 landing strategy、不把 forecast ceiling 或 d1 distance 写成 hard veto，
也不替换 Current-YES Carry。此前 first-signal-only calibration 的正结果应理解为窄
high-mid cohort 的 base-rate 重标定；宽分母和 clean-live 都不支持把它解释为物理 alpha。

## 目标

在 direct d1 YES mid >= {MID_FLOOR:.2f} 的 PIT d1 expression 分母上，预测
`P(final exactly d1)`；这同时要求未来能达到 d1、且到达后不会继续到 d2+。
训练每个 city-day 总权重为一，冻结 d1>=0.80 first signal 与 clean live 只作评估。

## 预注册模型组

- `d1_market_calibrated`：d1 market probability 的连续校准。
- `d1_market_plus_carry_core`：加 peak clock、dewpoint depression、wind、local hour。
- `d1_market_plus_landing_geometry`：加 d1 distance、forecast runway、forecast ceiling 到 d2 的 rung-normalized 距离。
- `d1_market_plus_core_landing`：两组一起；不使用已知语义有缺陷的 strict-high/path clock，也不引入 city gate。

## 数据完整性与双漏斗

```json
{json.dumps(json_ready(audit), ensure_ascii=False, indent=2)}
```

signal funnel 与 evidence funnel 的缺口均记录在审计；缺完整 ladder 不被解释成策略筛选。
费用为 direct executable d1 ask 加官方 Weather fee。当前尚无历史 full five-share d1 ladder
复放，因此交易层只是价格覆盖诊断，不能视作容量或 live 结论。

## 宽分母同分母 proper score

{markdown(wide_scores)}

## 冻结 d1>=0.80 first-signal proper score

{markdown(frozen_scores)}

## 冻结 first-signal fee replay（edge > {EDGE_BUFFER:.2f}；诊断，不调阈值）

{markdown(frozen_policy)}

## Clean live score（历史拟合；不参与选择）

{markdown(live_summary)}

## 判定规则

只有 landing geometry 在 wide 和 frozen 同分母 proper score 都有稳定、前瞻方向一致的
market residual，且新 shadow forward 独立复现，才把它升级为 d1 landing sleeve。否则保留为
共同 ladder 概率层的研究特征；不加 hard veto，不改现有 d1 live 或 Current-YES Carry。
""",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_states, source_audit = hz.build_historical_states(hz.ATLAS, hz.FACTORY)
    states = engineer(raw_states)
    universe = wide_universe(states)
    oof = expanding_oof(universe)
    wide_scores = weighted_date_pair_summary(oof)
    frozen = hz.first_signal_rows(hz.FROZEN_FIRST)
    frozen_oof = attach_frozen(oof, frozen)
    frozen_scores = weighted_date_pair_summary(
        frozen_oof.assign(city_day_weight=1.0)
    )
    frozen_policy = policy_summary(frozen_oof, label="frozen_first_signal")
    live = score_live(universe, live_features(states))
    audit = {
        **source_audit,
        "wide_mid_floor": MID_FLOOR,
        "wide_rows": len(universe),
        "wide_city_days": int(universe.groupby(["city", "target_date"]).ngroups),
        "wide_dates": int(universe["target_date"].nunique()),
        "wide_label_counts": universe["label"].value_counts().to_dict(),
        "wide_oof_rows_by_model": oof.groupby("model").size().to_dict(),
        "wide_oof_dates": int(oof["target_date"].nunique()),
        "frozen_rows": len(frozen),
        "frozen_oof_rows_by_model": frozen_oof.groupby("model").size().to_dict(),
        "frozen_oof_dates": int(frozen_oof["target_date"].nunique()),
        "clean_live_rows": int(live[hz.KEY].drop_duplicates().shape[0]) if not live.empty else 0,
        "data_snapshot_note": "Mac market-data mirror incrementally synced 2026-07-24; existing canonical DB covers event_date through 2026-07-23; model inputs are fixed historical atlas/factory plus clean-live journal",
    }
    universe[
        hz.KEY
        + ["label", "exact_win", "d1_market_probability", "entry_cost", "city_day_weight"]
        + CARRY_CORE
        + LANDING_GEOMETRY
    ].to_csv(args.output_dir / "wide_landing_features.csv", index=False)
    oof.to_csv(args.output_dir / "wide_oof_predictions.csv", index=False)
    wide_scores.to_csv(args.output_dir / "wide_proper_score_summary.csv", index=False)
    frozen_oof.to_csv(args.output_dir / "frozen_first_signal_oof.csv", index=False)
    frozen_scores.to_csv(args.output_dir / "frozen_proper_score_summary.csv", index=False)
    frozen_policy.to_csv(args.output_dir / "frozen_policy_summary.csv", index=False)
    live.to_csv(args.output_dir / "clean_live_predictions.csv", index=False)
    (args.output_dir / "audit.json").write_text(
        json.dumps(json_ready(audit), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(audit, wide_scores, frozen_scores, frozen_policy, live)
    print(json.dumps(json_ready(audit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
