#!/usr/bin/env python3
"""Full-odds-support price-conditioned Core Carry challenger.

This run uses the original 4,061-row Core training universe rather than the
later 0.80+ opportunity ledger.  Candidate selection is expanding OOF on the
development window; the last seven target dates are only a temporal holdout.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import SplineTransformer, StandardScaler

from scripts.analysis.reheat_risk.core_carry_price_conditioned_challenger import (
    compare,
    file_sha256,
    json_ready,
    score,
    training_weights,
)
from scripts.analysis.versioned_artifact_output import (
    prepare_new_run_output,
    resolve_run_output,
)


ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ID = "current_yes_core_carry_price_conditioned_challenger_v1"
RUN_ID = "full_support_temporal_holdout_7d_20260809"
V3_MODULE_PATH = Path(
    "/Users/deepsleep/projects/pm_agents_prod/scripts/analysis/reheat_risk/"
    "research_current_yes_core_carry_peak_clock_fix_v3.py"
)
PREREG = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-09-current-yes-core-carry-price-conditioned-"
    "full-support-preregistration.json"
)
REPORT = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-09-current-yes-core-carry-price-conditioned-"
    "full-support-v2.md"
)
RESULT = REPORT.with_suffix(".json")

SEED = 20260809
MIN_TRAIN_DATES = 14
HOLDOUT_DATES = 7
EXPECTED_ROWS = 4061
EXPECTED_DATES = 46
CORE_FEATURES = (
    "market_logit",
    "decision_hour_local",
    "dewpoint_depression_f",
    "wind_speed_kt",
)
WEATHER_FEATURES = (
    "decision_hour_local",
    "dewpoint_depression_f",
    "wind_speed_kt",
)
INTERACTION_FEATURES = tuple(
    f"market_x_{column}" for column in WEATHER_FEATURES
)
CANDIDATES = (
    "core_reweighted_linear",
    "market_spline_core",
    "market_spline_weather_interactions",
)


def load_v3_module() -> Any:
    if not V3_MODULE_PATH.exists():
        raise FileNotFoundError(V3_MODULE_PATH)
    spec = importlib.util.spec_from_file_location(
        "core_carry_peak_clock_fix_v3_full_support", V3_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v3 research module: {V3_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_full_universe(module: Any) -> pd.DataFrame:
    frame = module.load_universe().copy()
    if len(frame) != EXPECTED_ROWS or frame["target_date"].nunique() != EXPECTED_DATES:
        raise RuntimeError(
            "full-support denominator drift: "
            f"rows={len(frame)} dates={frame['target_date'].nunique()}"
        )
    frame["target_date"] = frame["target_date"].astype(str)
    frame["label"] = frame["label"].astype(int)
    frame["p_market_hold"] = frame["market_mid"].clip(1e-6, 1 - 1e-6)
    for source, target in zip(
        WEATHER_FEATURES, INTERACTION_FEATURES, strict=True
    ):
        frame[target] = frame["market_logit"] * pd.to_numeric(
            frame[source], errors="coerce"
        )
    return frame.sort_values(
        ["target_date", "city", "decision_snapshot_dt"], kind="stable"
    ).reset_index(drop=True)


def deployed_core_weights(frame: pd.DataFrame) -> np.ndarray:
    count = frame.groupby(["city", "target_date"])["label"].transform("size")
    return 1.0 / count.clip(lower=1).to_numpy(float)


def fit_deployed_style_core(module: Any, train: pd.DataFrame) -> Pipeline:
    model = module.make_model(list(module.V3))
    model.fit(
        train[list(module.V3)],
        train["label"],
        model__sample_weight=deployed_core_weights(train),
    )
    return model


def make_candidate(candidate: str) -> Pipeline:
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]
    )
    if candidate == "core_reweighted_linear":
        pre = ColumnTransformer(
            [("core", numeric, list(CORE_FEATURES))], remainder="drop"
        )
    else:
        market_spline = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                (
                    "spline",
                    SplineTransformer(
                        n_knots=4,
                        degree=2,
                        include_bias=False,
                        extrapolation="linear",
                    ),
                ),
                ("scale", StandardScaler()),
            ]
        )
        transformers: list[tuple[str, Any, list[str]]] = [
            ("market_spline", market_spline, ["market_logit"]),
            ("weather", numeric, list(WEATHER_FEATURES)),
        ]
        if candidate == "market_spline_weather_interactions":
            transformers.append(
                ("interactions", numeric, list(INTERACTION_FEATURES))
            )
        elif candidate != "market_spline_core":
            raise ValueError(f"unknown candidate: {candidate}")
        pre = ColumnTransformer(transformers, remainder="drop")
    return Pipeline(
        [
            ("pre", pre),
            (
                "model",
                LogisticRegression(
                    C=0.1,
                    max_iter=3000,
                    solver="lbfgs",
                    random_state=SEED,
                ),
            ),
        ]
    )


def fit_candidate(train: pd.DataFrame, candidate: str) -> Pipeline:
    model = make_candidate(candidate)
    # Preserve target-date/city-day equality while keeping sklearn's effective
    # regularization scale comparable to ordinary per-row weights.
    weights = training_weights(train) * len(train)
    columns = [
        *CORE_FEATURES,
        *INTERACTION_FEATURES,
    ]
    model.fit(
        train[columns],
        train["label"],
        model__sample_weight=weights,
    )
    return model


def predict_candidate(
    model: Pipeline, frame: pd.DataFrame
) -> np.ndarray:
    columns = [*CORE_FEATURES, *INTERACTION_FEATURES]
    return model.predict_proba(frame[columns])[:, 1]


def expanding_development(
    module: Any, frame: pd.DataFrame, holdout_start: str
) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique().tolist())
    output: list[pd.DataFrame] = []
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES or target_date >= holdout_start:
            continue
        train = frame[frame["target_date"].lt(target_date)].copy()
        test = frame[frame["target_date"].eq(target_date)].copy()
        core = fit_deployed_style_core(module, train)
        test["p_core_hold"] = core.predict_proba(
            test[list(module.V3)]
        )[:, 1]
        for candidate in CANDIDATES:
            fitted = fit_candidate(train, candidate)
            test[f"p_{candidate}"] = predict_candidate(fitted, test)
        output.append(test)
    if not output:
        raise RuntimeError("no full-support development rows")
    return pd.concat(output, ignore_index=True)


def select_candidate(
    development: pd.DataFrame,
) -> tuple[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        metrics = score(development, f"p_{candidate}")
        rows.append({"candidate": candidate, **metrics})
    for metric in ("brier", "logloss"):
        for rank, row in enumerate(
            sorted(rows, key=lambda item: item[metric]), start=1
        ):
            row[f"{metric}_rank"] = rank
    for row in rows:
        row["mean_rank"] = (
            row["brier_rank"] + row["logloss_rank"]
        ) / 2.0
    selected = min(
        rows, key=lambda row: (row["mean_rank"], row["brier"])
    )["candidate"]
    return str(selected), rows


def band_metrics(frame: pd.DataFrame) -> list[dict[str, Any]]:
    bins = [-np.inf, 0.20, 0.50, 0.80, 0.90, 0.9895, np.inf]
    labels = [
        "below_0.20",
        "0.20_to_0.50",
        "0.50_to_0.80",
        "0.80_to_0.90",
        "0.90_to_0.9895",
        "above_0.9895",
    ]
    work = frame.copy()
    work["mid_band"] = pd.cut(
        work["p_market_hold"], bins=bins, labels=labels, right=False
    )
    rows: list[dict[str, Any]] = []
    for band, group in work.groupby("mid_band", observed=True):
        rows.append(
            {
                "mid_band": str(band),
                "rows": int(len(group)),
                "dates": int(group["target_date"].nunique()),
                "challenger": score(group, "p_challenger"),
                "core": score(group, "p_core_hold"),
                "market": score(group, "p_market_hold"),
                "challenger_vs_core": compare(
                    group, "p_challenger", "p_core_hold"
                ),
            }
        )
    return rows


def compact_model(model: Pipeline, candidate: str, dates: list[str]) -> dict[str, Any]:
    logistic = model.named_steps["model"]
    names = model.named_steps["pre"].get_feature_names_out().tolist()
    return {
        "candidate": candidate,
        "training_dates": dates,
        "feature_names": names,
        "coef": logistic.coef_[0].tolist(),
        "intercept": float(logistic.intercept_[0]),
        "C": 0.1,
        "training_weight": "equal target date, city-day, checkpoint",
    }


def render_report(payload: dict[str, Any]) -> str:
    metrics = payload["holdout_metrics"]
    vs_core = payload["holdout_comparisons"]["core"]
    vs_market = payload["holdout_comparisons"]["market"]

    def delta(values: dict[str, Any], metric: str) -> str:
        item = values[metric]
        return (
            f"{item['delta']:+.6f} "
            f"CI [{item['ci_low']:+.6f},{item['ci_high']:+.6f}]"
        )

    lines = [
        "# Current-YES Core Carry：全赔率支持连续校准 challenger v2",
        "",
        f"Status: `{payload['status']}`",
        "",
        "## 结论与动作",
        "",
        payload["headline"],
        "",
        "- 当前 live Core 继续运行，本研究没有改 production。",
        "- 本次修正了上一轮分母：上一轮 1,349-row ledger 已经是 0.80+ 切片；本轮回到原始 4,061-row / 全赔率训练 support。",
        "- 最后 7 个 target dates 不参与拟合或选型；由于其结果此前已在其他研究出现，只是 temporal holdout，不冒充 clean forward。",
        "",
        "## 固定口径",
        "",
        f"- universe：{payload['denominator']['rows']:,} checkpoints / {payload['denominator']['city_days']} city-days / {payload['denominator']['dates']} dates / {payload['denominator']['cities']} cities。",
        f"- market mid support：`{payload['denominator']['market_mid_min']:.4f}`–`{payload['denominator']['market_mid_max']:.4f}`。",
        f"- expanding development OOF：{payload['development']['dates']} dates / {payload['development']['rows']} rows；holdout：{payload['holdout']['dates']} dates / {payload['holdout']['rows']} rows（{payload['holdout']['date_min']}–{payload['holdout']['date_max']}）。",
        f"- 候选 K={len(CANDIDATES)}；开发窗选中 `{payload['selected_candidate']}`。",
        "",
        "## Holdout 概率结果",
        "",
        "| 模型 | Brier | Logloss |",
        "|---|---:|---:|",
        f"| challenger | {metrics['challenger']['brier']:.6f} | {metrics['challenger']['logloss']:.6f} |",
        f"| deployed-style Core | {metrics['core']['brier']:.6f} | {metrics['core']['logloss']:.6f} |",
        f"| same-row market | {metrics['market']['brier']:.6f} | {metrics['market']['logloss']:.6f} |",
        "",
        f"- challenger − Core：Brier `{delta(vs_core, 'brier')}`；logloss `{delta(vs_core, 'logloss')}`。",
        f"- challenger − market：Brier `{delta(vs_market, 'brier')}`；logloss `{delta(vs_market, 'logloss')}`。",
        "",
        "## 按赔率带诊断",
        "",
        "| mid band | rows | challenger Brier | Core Brier | market Brier |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in payload["holdout_by_mid_band"]:
        lines.append(
            f"| {row['mid_band']} | {row['rows']} | "
            f"{row['challenger']['brier']:.6f} | {row['core']['brier']:.6f} | "
            f"{row['market']['brier']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Readiness、双漏斗与三道门",
            "",
            "- PIT/features/labels：READY，来自原始 Core frozen training universe。",
            "- market baseline：READY，同 rows 的 PIT midpoint。",
            "- current canonical：本轮不读取；当前 production critical 不污染冻结历史分母。",
            "- clean frozen forward：BLOCKED，需从本次冻结后的新 target dates 开始。",
            "- signal funnel：4,061 full-support checkpoints → expanding development OOF → one selected candidate → 7-date holdout。",
            "- evidence funnel：PIT feature、market、label同分母完整；本轮是概率研究，不宣称 execution/fill/PnL。",
            f"- significance={payload['gates']['significance']}；baseline={payload['gates']['baseline']}；forward={payload['gates']['forward']}；conclusion={payload['gates']['conclusion']}。",
            "",
            "## 复现",
            "",
            "```bash",
            ".venv/bin/python scripts/analysis/reheat_risk/research_current_yes_core_carry_taker_10share_v1.py --price-conditioned-challenger",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_run"):
        raise RuntimeError("full-support preregistration is not frozen")
    module = load_v3_module()
    frame = load_full_universe(module)
    dates = sorted(frame["target_date"].unique().tolist())
    holdout_dates = dates[-HOLDOUT_DATES:]
    holdout_start = holdout_dates[0]

    development = expanding_development(module, frame, holdout_start)
    selected, selection = select_candidate(development)
    train = frame[frame["target_date"].lt(holdout_start)].copy()
    holdout = frame[frame["target_date"].isin(holdout_dates)].copy()
    core = fit_deployed_style_core(module, train)
    challenger = fit_candidate(train, selected)
    holdout["p_core_hold"] = core.predict_proba(
        holdout[list(module.V3)]
    )[:, 1]
    holdout["p_challenger"] = predict_candidate(challenger, holdout)

    comparisons = {
        "core": compare(holdout, "p_challenger", "p_core_hold"),
        "market": compare(holdout, "p_challenger", "p_market_hold"),
    }
    metrics = {
        "challenger": score(holdout, "p_challenger"),
        "core": score(holdout, "p_core_hold"),
        "market": score(holdout, "p_market_hold"),
    }
    same_sign = all(
        comparisons["core"][metric]["delta"] < 0
        for metric in ("brier", "logloss")
    )
    significance = all(
        comparisons["core"][metric]["ci_high"] < 0
        for metric in ("brier", "logloss")
    )
    baseline = all(
        comparisons["market"][metric]["delta"] < 0
        for metric in ("brier", "logloss")
    )
    if significance and baseline:
        status = "historical_holdout_pass_clean_forward_required"
        conclusion = "shadow_candidate"
        headline = (
            "全赔率连续 challenger 在 temporal holdout 上显著优于 deployed-style Core，"
            "但尚无 clean forward；冻结为 zero-notional candidate，不改 live。"
        )
    elif same_sign:
        status = "point_improvement_inconclusive"
        conclusion = "inconclusive"
        headline = (
            "全赔率连续 challenger 对 Core 的 Brier/logloss 都是点估改善，但 7-date holdout "
            "CI 仍跨 0；保留研究 artifact，不改 live。"
        )
    else:
        status = "historical_holdout_failed"
        conclusion = "inconclusive"
        headline = (
            "全赔率连续 challenger 没有在 temporal holdout 同时改善 Brier 与 logloss；"
            "本版不进入 shadow，当前 Core 继续运行。"
        )

    output_dir = prepare_new_run_output(
        resolve_run_output(RESEARCH_ID, run_id=RUN_ID, explicit_output=None)
    )
    pd.DataFrame(selection).to_csv(
        output_dir / "candidate_selection.csv", index=False
    )
    prediction_columns = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "label",
        "market_mid",
        "p_market_hold",
        "p_core_hold",
        "p_challenger",
    ]
    holdout[prediction_columns].to_csv(
        output_dir / "temporal_holdout_predictions.csv", index=False
    )
    joblib.dump(challenger, output_dir / "frozen_challenger.joblib")

    payload = {
        "schema_version": RESEARCH_ID,
        "research_id": RESEARCH_ID,
        "run_id": RUN_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "headline": headline,
        "denominator": {
            "rows": int(len(frame)),
            "city_days": int(frame.groupby(["city", "target_date"]).ngroups),
            "cities": int(frame["city"].nunique()),
            "dates": int(len(dates)),
            "date_min": dates[0],
            "date_max": dates[-1],
            "market_mid_min": float(frame["market_mid"].min()),
            "market_mid_max": float(frame["market_mid"].max()),
            "source_module": str(V3_MODULE_PATH),
            "source_module_sha256": file_sha256(V3_MODULE_PATH),
        },
        "development": {
            "rows": int(len(development)),
            "dates": int(development["target_date"].nunique()),
            "date_min": str(development["target_date"].min()),
            "date_max": str(development["target_date"].max()),
        },
        "holdout": {
            "rows": int(len(holdout)),
            "dates": int(len(holdout_dates)),
            "date_min": holdout_dates[0],
            "date_max": holdout_dates[-1],
            "evidence_class": "retrospective_temporal_holdout_not_clean_forward",
        },
        "candidate_selection": selection,
        "selected_candidate": selected,
        "frozen_model": compact_model(
            challenger, selected, sorted(train["target_date"].unique().tolist())
        ),
        "holdout_metrics": metrics,
        "holdout_comparisons": comparisons,
        "holdout_by_mid_band": band_metrics(holdout),
        "gates": {
            "significance": "PASS" if significance else "FAIL",
            "baseline": "PASS" if baseline else "FAIL",
            "forward": "NA_clean_forward_not_started",
            "conclusion": conclusion,
        },
        "supersedes_for_decision_use": (
            "historical_temporal_holdout_7d_20260809 high-mid-only run"
        ),
        "production_action": "none",
        "artifacts": {
            "output_dir": str(output_dir),
            "preregistration": str(PREREG.relative_to(ROOT)),
            "preregistration_sha256": file_sha256(PREREG),
            "frozen_model": str(output_dir / "frozen_challenger.joblib"),
            "frozen_model_sha256": file_sha256(
                output_dir / "frozen_challenger.joblib"
            ),
        },
    }
    payload = json_ready(payload)
    RESULT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    REPORT.write_text(render_report(payload), encoding="utf-8")
    (output_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "selected_candidate": selected,
                "status": status,
                "denominator": payload["denominator"],
                "holdout_metrics": metrics,
                "holdout_comparisons": comparisons,
                "holdout_by_mid_band": payload["holdout_by_mid_band"],
                "report": str(REPORT.relative_to(ROOT)),
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
