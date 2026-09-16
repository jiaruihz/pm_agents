#!/usr/bin/env python3
"""Exact-d1 market residual strategy on the actual first-signal denominator.

The candidate denominator is the first city-day state where direct d1 YES mid
reaches 0.80.  The model predicts exact-d1 versus stall/overshoot, anchored by
the direct d1 market probability.  Historical evaluation is date-expanding
OOF; clean live rows are scored after fitting the historical sample.
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
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_d1_overshoot_hazard_v1 as hz,
)
from scripts.analysis.market_structure_edge import (  # noqa: E402
    research_market_residual_exact_router_v1 as router,
)


OUTPUT = ROOT / "docs/analysis/2026-07/generated/d1_exact_residual_strategy_v1"
REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-23-research-d1-exact-residual-strategy-v1.md"
)
MIN_TRAIN_DATES = 12
MODEL_C = 0.03
EDGE_BUFFERS = [0.0, 0.01, 0.02, 0.03, 0.05]
BOOTSTRAP_DRAWS = 4000
SEED = 20260723

MARKET = ["d1_market_logit"]
COMPACT = router.COMPACT_FEATURES
SPECS = {
    "d1_market_calibrated": (MARKET, []),
    "d1_market_plus_compact": (MARKET + COMPACT, []),
    "d1_market_plus_compact_city": (
        MARKET + COMPACT,
        ["city", "forecast_assigned_model", "unit"],
    ),
}
GATE_COLUMNS = [
    "forecast_bust_native",
    "forecast_ceiling_margin_to_d2",
    "positive_trend_1h_f",
]


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _markdown(frame: pd.DataFrame) -> str:
    return router._markdown_table(frame)


def pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    blocks: list[tuple[str, Any, list[str]]] = [
        (
            "numeric",
            Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                    ("scale", StandardScaler()),
                ]
            ),
            numeric,
        )
    ]
    if categorical:
        blocks.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        [
            ("features", ColumnTransformer(blocks)),
            (
                "model",
                LogisticRegression(
                    C=MODEL_C,
                    # lbfgs leaves the intercept unpenalized.  Penalizing the
                    # intercept (liblinear's default behavior) incorrectly
                    # drags this 94% exact cohort back toward 50%.
                    solver="lbfgs",
                    max_iter=1000,
                    random_state=SEED,
                ),
            ),
        ]
    )


def positive_probability(model: Pipeline, rows: pd.DataFrame) -> np.ndarray:
    probabilities = model.predict_proba(rows)
    classes = list(model.named_steps["model"].classes_)
    return probabilities[:, classes.index(1)]


def frozen_signal_cohort(
    states: pd.DataFrame, frozen: pd.DataFrame
) -> pd.DataFrame:
    """Attach corrected features to the canonical frozen first signals."""
    canonical = frozen[
        hz.KEY
        + [
            "label",
            "d1_yes_mid_trigger",
            "d1_yes_ask_exec",
            "cost",
        ]
    ].rename(
        columns={
            "label": "frozen_label",
            "d1_yes_mid_trigger": "frozen_d1_mid",
            "d1_yes_ask_exec": "frozen_d1_ask",
            "cost": "frozen_cost",
        }
    )
    cohort = states.merge(
        canonical,
        on=hz.KEY,
        how="inner",
        validate="one_to_one",
    )
    cohort = cohort[cohort["model_eligible"]].copy()
    if cohort["label"].ne(cohort["frozen_label"]).any():
        raise ValueError("corrected state labels disagree with frozen cohort")
    cohort["label"] = cohort["frozen_label"]
    cohort["exact_win"] = cohort["label"].eq(hz.CLASSES[1]).astype(int)
    cohort["d1_market_probability"] = pd.to_numeric(
        cohort["frozen_d1_mid"], errors="coerce"
    ).clip(1e-5, 1 - 1e-5)
    cohort["d1_market_logit"] = hz._logit(cohort["d1_market_probability"])
    cohort["entry_cost"] = pd.to_numeric(cohort["frozen_cost"], errors="coerce")
    return cohort


def expanding_oof(cohort: pd.DataFrame) -> pd.DataFrame:
    dates = sorted(cohort["target_date"].unique())
    outputs = []
    for index, date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = cohort[cohort["target_date"].lt(date)]
        test = cohort[cohort["target_date"].eq(date)]
        if train["exact_win"].nunique() < 2:
            continue
        raw = test[
            hz.KEY
            + [
                "label",
                "exact_win",
                "d1_market_probability",
                "entry_cost",
            ]
            + GATE_COLUMNS
        ].copy()
        raw["model"] = "d1_market_raw"
        raw["p_exact"] = raw["d1_market_probability"]
        outputs.append(raw)
        for name, (numeric, categorical) in SPECS.items():
            estimator = pipeline(numeric, categorical)
            estimator.fit(train, train["exact_win"])
            block = test[
                hz.KEY
                + [
                    "label",
                    "exact_win",
                    "d1_market_probability",
                    "entry_cost",
                ]
                + GATE_COLUMNS
            ].copy()
            block["model"] = name
            block["p_exact"] = positive_probability(estimator, test)
            outputs.append(block)
    result = pd.concat(outputs, ignore_index=True)
    result["logloss"] = -(
        result["exact_win"] * np.log(result["p_exact"].clip(1e-9, 1))
        + (1 - result["exact_win"])
        * np.log((1 - result["p_exact"]).clip(1e-9, 1))
    )
    result["brier"] = np.square(result["p_exact"] - result["exact_win"])
    return result


def paired_score(scored: pd.DataFrame) -> pd.DataFrame:
    baseline = scored[scored["model"].eq("d1_market_raw")][
        hz.KEY + ["logloss", "brier"]
    ].rename(columns={"logloss": "base_logloss", "brier": "base_brier"})
    rows = []
    for model in SPECS:
        joined = scored[scored["model"].eq(model)].merge(
            baseline, on=hz.KEY, validate="one_to_one"
        )
        daily = joined.groupby("target_date").agg(
            model_logloss=("logloss", "mean"),
            market_logloss=("base_logloss", "mean"),
            model_brier=("brier", "mean"),
            market_brier=("base_brier", "mean"),
        )
        daily["ll_delta"] = daily["model_logloss"] - daily["market_logloss"]
        daily["br_delta"] = daily["model_brier"] - daily["market_brier"]
        rng = np.random.default_rng(SEED + len(rows))
        values = daily[["ll_delta", "br_delta"]].to_numpy()
        boot = np.empty((BOOTSTRAP_DRAWS, 2))
        for draw in range(BOOTSTRAP_DRAWS):
            sample = values[rng.integers(0, len(values), len(values))]
            boot[draw] = sample.mean(axis=0)
        rows.append(
            {
                "model": model,
                "rows": len(joined),
                "dates": len(daily),
                "market_logloss": daily["market_logloss"].mean(),
                "model_logloss": daily["model_logloss"].mean(),
                "logloss_delta": daily["ll_delta"].mean(),
                "logloss_delta_ci_low": np.quantile(boot[:, 0], 0.025),
                "logloss_delta_ci_high": np.quantile(boot[:, 0], 0.975),
                "market_brier": daily["market_brier"].mean(),
                "model_brier": daily["model_brier"].mean(),
                "brier_delta": daily["br_delta"].mean(),
                "brier_delta_ci_low": np.quantile(boot[:, 1], 0.025),
                "brier_delta_ci_high": np.quantile(boot[:, 1], 0.975),
            }
        )
    return pd.DataFrame(rows)


def add_mechanism_veto(
    rows: pd.DataFrame,
    bust_threshold: float = 1.0,
    market_floor: float = 0.80,
) -> pd.DataFrame:
    frame = rows.copy()
    frame["overshoot_veto"] = (
        pd.to_numeric(
            frame["forecast_ceiling_margin_to_d2"], errors="coerce"
        ).ge(0)
        | (
            pd.to_numeric(
                frame["forecast_bust_native"], errors="coerce"
            ).ge(bust_threshold)
            & pd.to_numeric(
                frame["positive_trend_1h_f"], errors="coerce"
            ).gt(0)
        )
    )
    # One tick beyond the trigger is market confirmation, not a learned
    # weather threshold.  Exact 0.80 rows remain part of the evidence funnel
    # and are reported as vetoed, not silently removed from the denominator.
    frame["trigger_boundary_veto"] = pd.to_numeric(
        frame["d1_market_probability"], errors="coerce"
    ).le(market_floor)
    frame["mechanism_safe"] = ~(
        frame["overshoot_veto"] | frame["trigger_boundary_veto"]
    )
    return frame


def _policy_roi_ci(rows: pd.DataFrame, seed: int) -> tuple[float, float, float]:
    traded = rows[rows["trade"]]
    cost = float(traded["entry_cost"].sum())
    if cost <= 0:
        return math.nan, math.nan, math.nan
    point = float(traded["pnl"].sum() / cost)
    daily = traded.groupby("target_date").agg(
        pnl=("pnl", "sum"), cost=("entry_cost", "sum")
    )
    if len(daily) < 3:
        return point, math.nan, math.nan
    rng = np.random.default_rng(seed)
    values = daily[["pnl", "cost"]].to_numpy()
    boot = np.empty(BOOTSTRAP_DRAWS)
    for draw in range(BOOTSTRAP_DRAWS):
        sample = values[rng.integers(0, len(values), len(values))]
        boot[draw] = sample[:, 0].sum() / sample[:, 1].sum()
    return point, *np.quantile(boot, [0.025, 0.975])


def policy(scored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    details = []
    summaries = []
    for buffer in EDGE_BUFFERS:
        block = scored.copy()
        block["edge_buffer"] = buffer
        block["model_edge"] = block["p_exact"] - block["entry_cost"]
        block = add_mechanism_veto(block)
        for policy_name, eligible in [
            ("edge_only", pd.Series(True, index=block.index)),
            ("edge_plus_mechanism_veto_discovery", block["mechanism_safe"]),
        ]:
            variant = block.copy()
            variant["policy"] = policy_name
            variant["trade"] = variant["model_edge"].gt(buffer) & eligible
            variant["pnl"] = np.where(
                variant["trade"],
                variant["exact_win"] - variant["entry_cost"],
                0.0,
            )
            details.append(variant)
            period_frame = pd.concat(
                [
                    variant.assign(period="all_oof"),
                    variant[
                        variant["target_date"].lt(hz.FORWARD_START)
                    ].assign(period="pre_forward"),
                    variant[
                        variant["target_date"].ge(hz.FORWARD_START)
                    ].assign(period="historical_forward"),
                ]
            )
            for (model, period), part in period_frame.groupby(
                ["model", "period"]
            ):
                traded = part[part["trade"]]
                cost = float(traded["entry_cost"].sum())
                roi, roi_low, roi_high = _policy_roi_ci(
                    part, SEED + len(summaries)
                )
                summaries.append(
                    {
                        "model": model,
                        "policy": policy_name,
                        "edge_buffer": buffer,
                        "period": period,
                        "rows": len(part),
                        "vetoed_rows": int(
                            (
                                part["model_edge"].gt(buffer)
                                & ~part["mechanism_safe"]
                            ).sum()
                        ),
                        "trades": len(traded),
                        "wins": int(traded["exact_win"].sum()),
                        "overshoots": int(
                            traded["label"].eq(hz.CLASSES[2]).sum()
                        ),
                        "stalls": int(
                            traded["label"].eq(hz.CLASSES[0]).sum()
                        ),
                        "cost": cost,
                        "pnl": float(traded["pnl"].sum()),
                        "roi": roi,
                        "roi_ci_low": roi_low,
                        "roi_ci_high": roi_high,
                    }
                )
    return pd.concat(details, ignore_index=True), pd.DataFrame(summaries)


def veto_sensitivity(scored: pd.DataFrame) -> pd.DataFrame:
    base = scored[scored["model"].eq("d1_market_calibrated")].copy()
    rows = []
    for bust_threshold in [0.5, 1.0, 1.5]:
        for market_floor in [0.80, 0.805, 0.81]:
            block = add_mechanism_veto(
                base,
                bust_threshold=bust_threshold,
                market_floor=market_floor,
            )
            block["trade"] = (
                block["p_exact"].sub(block["entry_cost"]).gt(0.02)
                & block["mechanism_safe"]
            )
            block["pnl"] = np.where(
                block["trade"],
                block["exact_win"] - block["entry_cost"],
                0.0,
            )
            for period, part in [
                ("pre_forward", block[block["target_date"].lt(hz.FORWARD_START)]),
                (
                    "historical_forward",
                    block[block["target_date"].ge(hz.FORWARD_START)],
                ),
            ]:
                traded = part[part["trade"]]
                cost = float(traded["entry_cost"].sum())
                rows.append(
                    {
                        "bust_threshold_native": bust_threshold,
                        "market_floor": market_floor,
                        "period": period,
                        "trades": len(traded),
                        "wins": int(traded["exact_win"].sum()),
                        "losses": int(len(traded) - traded["exact_win"].sum()),
                        "pnl": float(traded["pnl"].sum()),
                        "roi": float(traded["pnl"].sum() / cost)
                        if cost > 0
                        else math.nan,
                    }
                )
    return pd.DataFrame(rows)


def prepare_live(states: pd.DataFrame) -> pd.DataFrame:
    city_family = (
        states[["city", "city_family"]]
        .dropna()
        .drop_duplicates("city")
        .set_index("city")["city_family"]
        .to_dict()
    )
    live = hz.build_live_rows(hz.LIVE_RAW, city_family, hz.DB_PATH)
    if live.empty:
        return live
    live["forecast_assigned_model"] = live["city"].map(hz.CITY_MODEL).fillna("gfs")
    for column in [
        "current_no_bid",
        "current_no_ask",
        "current_yes_ask",
        "d1_no_bid",
        "d1_no_ask",
    ]:
        live[column] = math.nan
    live = router.engineer(live)
    live["exact_win"] = live["label"].eq(hz.CLASSES[1]).astype(int)
    live["d1_market_probability"] = pd.to_numeric(
        live["d1_yes_mid_trigger"], errors="coerce"
    )
    live["d1_market_logit"] = hz._logit(live["d1_market_probability"])
    live["entry_cost"] = pd.to_numeric(live["cost"], errors="coerce")
    return live


def score_live(cohort: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    if live.empty:
        return live
    outputs = []
    raw = live[
        hz.KEY
        + ["label", "exact_win", "entry_cost", "d1_market_probability"]
        + GATE_COLUMNS
    ].copy()
    raw["model"] = "d1_market_raw"
    raw["p_exact"] = raw["d1_market_probability"]
    outputs.append(raw)
    for name, (numeric, categorical) in SPECS.items():
        estimator = pipeline(numeric, categorical)
        estimator.fit(cohort, cohort["exact_win"])
        block = live[
            hz.KEY
            + ["label", "exact_win", "entry_cost", "d1_market_probability"]
            + GATE_COLUMNS
        ].copy()
        block["model"] = name
        block["p_exact"] = positive_probability(estimator, live)
        outputs.append(block)
    result = pd.concat(outputs, ignore_index=True)
    result["logloss"] = -(
        result["exact_win"] * np.log(result["p_exact"].clip(1e-9, 1))
        + (1 - result["exact_win"])
        * np.log((1 - result["p_exact"]).clip(1e-9, 1))
    )
    result["brier"] = np.square(result["p_exact"] - result["exact_win"])
    result["model_edge"] = result["p_exact"] - result["entry_cost"]
    result = add_mechanism_veto(result)
    result["trade_at_002"] = (
        result["model_edge"].gt(0.02) & result["mechanism_safe"]
    )
    result["pnl_at_002"] = np.where(
        result["trade_at_002"],
        result["exact_win"] - result["entry_cost"],
        0.0,
    )
    return result


def write_report(
    audit: dict[str, Any],
    scores: pd.DataFrame,
    policies: pd.DataFrame,
    sensitivity: pd.DataFrame,
    live: pd.DataFrame,
    path: Path,
) -> None:
    best = scores.sort_values("logloss_delta").iloc[0]
    policy_view = policies[
        policies["edge_buffer"].eq(0.02)
        & policies["period"].isin(["pre_forward", "historical_forward"])
        & policies["model"].isin(
            ["d1_market_raw", "d1_market_calibrated"]
        )
    ]
    live_view = (
        live.groupby("model")
        .agg(
            rows=("label", "size"),
            trades=("trade_at_002", "sum"),
            wins=("exact_win", lambda x: int(x[live.loc[x.index, "trade_at_002"]].sum())),
            logloss=("logloss", "mean"),
            pnl=("pnl_at_002", "sum"),
        )
        .reset_index()
    )
    path.write_text(
        f"""# d1 exact residual strategy v1（2026-07-23）

## 结论

候选策略已收敛为：在每个 city-day 首次 `d1 YES mid >= 0.80` 时，
先用只含 market 的 expanding calibration 估计 exact probability，再计算
fee-adjusted edge。候选研究规则还加入两类 mechanism veto：
forecast ceiling 已到 d2，或 forecast 已被打穿至少 1 native unit 且仍升温；
恰好卡在 0.80 trigger 边界也不成交。否则 edge 超过 0.02 才买 d1 YES。

最佳 OOF proper-score 模型 `{best['model']}` 相对 d1 market 的 date-equal
binary logloss delta 为 `{best['logloss_delta']:+.4f}`，95% date bootstrap CI
`[{best['logloss_delta_ci_low']:+.4f}, {best['logloss_delta_ci_high']:+.4f}]`。
delta < 0 才是优于 market。

## 分母与防泄漏

- signal funnel：冻结 218 first signals → 216 行可回连 corrected state →
  剔除 11 行 7/02-05 known forecast pollution → 205 行模型分母。
- evidence funnel：direct d1 bid/ask 与 settlement 可用；缺完整 ladder 不会被写成策略筛除。
- 历史：date-expanding OOF，最少 12 个训练日期，C={MODEL_C}。
- forward：{hz.FORWARD_START} 起单列；clean live 只在历史冻结拟合后评分。
- fee-adjusted cost：direct ask + `0.05*p*(1-p)`。

## 审计

```json
{json.dumps(_json_ready(audit), ensure_ascii=False, indent=2)}
```

## Proper score

{_markdown(scores)}

## 策略结果（edge buffer=0.02）

{_markdown(policy_view)}

## Mechanism veto sensitivity

{_markdown(sensitivity)}

## Clean live（edge buffer=0.02）

{_markdown(live_view)}

## 策略状态

`edge_only` 已被 clean live 否定。`edge_plus_mechanism_veto_discovery`
在当前 clean live 上修复了三个 overshoot 和一个 trigger-boundary stall，
但这个 veto 是看过这些 live miss 后形成的，clean live 只能算 discovery，
不能再算独立 forward。它现在有资格进入 zero-notional shadow，尚无资格 live。
本研究不修改 live 配置，也不把 discovery slice 当成 confirmed alpha。
""",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_states, source_audit = hz.build_historical_states(hz.ATLAS, hz.FACTORY)
    states = router.engineer(raw_states)
    frozen = hz.first_signal_rows(hz.FROZEN_FIRST)
    cohort = frozen_signal_cohort(states, frozen)
    scored = expanding_oof(cohort)
    score_summary = paired_score(scored)
    policy_detail, policy_summary = policy(scored)
    sensitivity = veto_sensitivity(scored)
    live_rows = prepare_live(states)
    live_scored = score_live(cohort, live_rows)
    comparison = frozen.merge(
        cohort[hz.KEY + ["label"]],
        on=hz.KEY,
        how="left",
        suffixes=("_frozen", "_rebuilt"),
    )
    all_state_match = frozen.merge(
        states[hz.KEY + ["model_eligible"]],
        on=hz.KEY,
        how="left",
        validate="one_to_one",
    )
    audit = {
        **source_audit,
        "model_first_signal_rows": len(cohort),
        "model_first_signal_dates": int(cohort["target_date"].nunique()),
        "model_labels": cohort["label"].value_counts().to_dict(),
        "frozen_rows": len(frozen),
        "frozen_corrected_state_matches": int(
            all_state_match["model_eligible"].notna().sum()
        ),
        "frozen_model_rows_after_pollution_exclusion": int(
            all_state_match["model_eligible"].eq(True).sum()
        ),
        "frozen_missing_corrected_state_rows": int(
            all_state_match["model_eligible"].isna().sum()
        ),
        "frozen_label_mismatches": int(
            (
                comparison["label_rebuilt"].notna()
                & comparison["label_frozen"].ne(comparison["label_rebuilt"])
            ).sum()
        ),
        "oof_rows_by_model": scored.groupby("model").size().to_dict(),
        "oof_dates": int(scored["target_date"].nunique()),
        "historical_forward_oof_rows": int(
            scored.loc[
                scored["model"].eq("d1_market_raw")
                & scored["target_date"].ge(hz.FORWARD_START)
            ].shape[0]
        ),
        "clean_live_rows": len(live_rows),
    }
    cohort[
        hz.KEY
        + [
            "label",
            "d1_market_probability",
            "entry_cost",
            "exact_win",
        ]
        + COMPACT
    ].to_csv(args.output_dir / "first_signal_features.csv", index=False)
    scored.to_csv(args.output_dir / "historical_oof_predictions.csv", index=False)
    score_summary.to_csv(args.output_dir / "proper_score_summary.csv", index=False)
    policy_detail.to_csv(args.output_dir / "policy_detail.csv", index=False)
    policy_summary.to_csv(args.output_dir / "policy_summary.csv", index=False)
    sensitivity.to_csv(args.output_dir / "veto_sensitivity.csv", index=False)
    live_scored.to_csv(args.output_dir / "clean_live_predictions.csv", index=False)
    (args.output_dir / "audit.json").write_text(
        json.dumps(_json_ready(audit), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(
        audit,
        score_summary,
        policy_summary,
        sensitivity,
        live_scored,
        args.report,
    )
    print(json.dumps(_json_ready(audit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
