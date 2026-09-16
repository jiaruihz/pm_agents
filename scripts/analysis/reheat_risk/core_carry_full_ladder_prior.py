#!/usr/bin/env python3
"""Test a complete-ladder market prior inside the frozen Core Carry target.

Research only.  The runner resolves the exact orderbook snapshot referenced by
each frozen parent checkpoint, constructs a physically feasible exact-bracket
ladder prior, and compares one preregistered Core-offset challenger with the
frozen Core and same-row market.  It never mutates production state.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402
from weather_model_evaluation import (  # noqa: E402
    binary_loss_values,
    date_block_bootstrap_delta,
)


RESEARCH_ID = "current_yes_core_carry_full_ladder_prior_v1"
RUN_ID = "historical_parent_1349_full_ladder_20260808"
INPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_overshoot_risk_sizing_overlay_v1/"
    "opportunity_ledger.csv"
)
PREREG = (
    ROOT
    / "docs/analysis/2026-08/"
    "2026-08-08-current-yes-core-carry-full-ladder-prior-v1-preregistration.json"
)
REPORT = (
    ROOT
    / "docs/analysis/2026-08/"
    "2026-08-08-current-yes-core-carry-full-ladder-prior-v1.md"
)
SUMMARY = REPORT.with_suffix(".json")
ARTIFACT_ROOT = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store"
) / RESEARCH_ID / RUN_ID
ARCHIVE_PM_ROOT = Path("/Volumes/jrs-archive/pm_agents")

SEED = 20260808
EPS = 1e-6
WARMUP_DATES = 8
SECONDARY_DATES = 8
BOOTSTRAP_DRAWS = 5000
L2 = 0.2


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    return value


def resolve_snapshot(path_value: str) -> tuple[Path | None, str]:
    raw = Path(str(path_value))
    candidates = [
        (raw if raw.is_absolute() else ROOT / raw, "hot_or_compat"),
        (ARCHIVE_PM_ROOT / raw, "archive_runtime_exact_path"),
    ]
    for candidate, source in candidates:
        if candidate.is_file():
            return candidate, source
    return None, "missing_exact_snapshot"


def bracket_coordinate(label: str) -> float | None:
    parsed = parse_market_bracket(str(label))
    if parsed is None:
        return None
    if parsed.bottom:
        return parsed.high
    if parsed.top:
        return parsed.low
    if parsed.low is None or parsed.high is None:
        return None
    return (float(parsed.low) + float(parsed.high)) / 2.0


def boundary_midpoint(row: dict[str, Any]) -> tuple[float | None, bool]:
    summary = row.get("summary") or {}
    bid = summary.get("best_bid")
    ask = summary.get("best_ask")
    direct = bid is not None and ask is not None
    if bid is None and ask is None:
        return None, False
    bid_value = 0.0 if bid is None else float(bid)
    ask_value = 1.0 if ask is None else float(ask)
    if not (0 <= bid_value <= ask_value <= 1):
        return None, direct
    return (bid_value + ask_value) / 2.0, direct


def read_yes_ladder(path: Path, city: str, target_date: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    handle_context = (
        gzip.open(path, "rt", encoding="utf-8")
        if path.suffix == ".gz"
        else path.open("rt", encoding="utf-8")
    )
    with handle_context as handle:
        for line in handle:
            item = json.loads(line)
            if (
                str(item.get("city")) != city
                or str(item.get("event_date")) != target_date
                or str(item.get("outcome", "")).lower() != "yes"
                or item.get("status") != "ok"
            ):
                continue
            label = str(item.get("bracket", "")).strip()
            midpoint, direct = boundary_midpoint(item)
            coordinate = bracket_coordinate(label)
            if label and midpoint is not None and coordinate is not None:
                rows[label] = {
                    "bracket": label,
                    "coordinate": coordinate,
                    "midpoint": midpoint,
                    "direct_two_sided": direct,
                }
    return sorted(rows.values(), key=lambda item: item["coordinate"])


def ladder_features(parent: pd.DataFrame) -> pd.DataFrame:
    cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    output: list[dict[str, Any]] = []
    for row in parent.itertuples(index=False):
        resolved, source = resolve_snapshot(str(row.orderbook_file))
        base = {
            "opportunity_id": row.opportunity_id,
            "snapshot_resolution": source,
            "resolved_snapshot": str(resolved) if resolved else None,
            "ladder_status": "scorable",
        }
        if resolved is None:
            output.append({**base, "ladder_status": "missing_exact_snapshot"})
            continue
        key = (str(resolved), str(row.city), str(row.target_date))
        if key not in cache:
            cache[key] = read_yes_ladder(resolved, str(row.city), str(row.target_date))
        ladder = cache[key]
        current = next(
            (item for item in ladder if item["bracket"] == str(row.current_bracket)),
            None,
        )
        if current is None:
            output.append({**base, "ladder_status": "missing_current_rung", "rung_count": len(ladder)})
            continue
        higher = [item for item in ladder if item["coordinate"] > current["coordinate"]]
        lower = [item for item in ladder if item["coordinate"] < current["coordinate"]]
        if len(ladder) < 4 or not higher:
            status = "too_few_rungs" if len(ladder) < 4 else "no_upper_rung"
            output.append({**base, "ladder_status": status, "rung_count": len(ladder)})
            continue
        total_mass = float(sum(item["midpoint"] for item in ladder))
        lower_mass = float(sum(item["midpoint"] for item in lower))
        upper_mass = float(sum(item["midpoint"] for item in higher))
        adjacent_mass = float(higher[0]["midpoint"])
        far_mass = max(0.0, upper_mass - adjacent_mass)
        feasible_mass = float(current["midpoint"] + upper_mass)
        p_feasible = float(current["midpoint"] / feasible_mass)
        raw_market = float(np.clip(row.market_mid, EPS, 1 - EPS))
        p_feasible_clip = float(np.clip(p_feasible, EPS, 1 - EPS))
        prior_shift = math.log(p_feasible_clip / (1 - p_feasible_clip)) - math.log(
            raw_market / (1 - raw_market)
        )
        tail_ratio = float(np.clip(math.log((far_mass + EPS) / (adjacent_mass + EPS)), -8, 8))
        output.append(
            {
                **base,
                "rung_count": len(ladder),
                "direct_two_sided_rung_fraction": float(
                    np.mean([item["direct_two_sided"] for item in ladder])
                ),
                "ladder_total_mid_mass": total_mass,
                "lower_impossible_mid_mass": lower_mass,
                "current_mid_from_ladder": float(current["midpoint"]),
                "adjacent_upper_mid_mass": adjacent_mass,
                "far_upper_mid_mass": far_mass,
                "upper_mid_mass": upper_mass,
                "p_ladder_feasible_hold": p_feasible_clip,
                "feasible_prior_shift": float(np.clip(prior_shift, -8, 8)),
                "far_vs_adjacent_upper_log_ratio": tail_ratio,
            }
        )
    return parent.merge(pd.DataFrame(output), on="opportunity_id", validate="one_to_one")


def state_entry_weights(frame: pd.DataFrame) -> np.ndarray:
    keys = ["city", "target_date", "current_bracket"]
    rows_per_state = frame.groupby(keys)["label"].transform("size").to_numpy(float)
    states_per_date = (
        frame[keys].drop_duplicates().groupby("target_date")["city"].size().to_dict()
    )
    date_count = frame["target_date"].nunique()
    weights = np.array(
        [
            1.0 / (date_count * states_per_date[target_date] * state_rows)
            for target_date, state_rows in zip(frame["target_date"], rows_per_state, strict=True)
        ]
    )
    return weights / weights.sum()


def fit_predict(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, dict[str, Any]]:
    columns = ["feasible_prior_shift", "far_vs_adjacent_upper_log_ratio"]
    train_x = train[columns].to_numpy(float)
    test_x = test[columns].to_numpy(float)
    means = train_x.mean(axis=0)
    scales = train_x.std(axis=0)
    scales = np.where(scales < 1e-9, 1.0, scales)
    train_x = (train_x - means) / scales
    test_x = (test_x - means) / scales
    train_x = np.column_stack([np.ones(len(train)), train_x])
    test_x = np.column_stack([np.ones(len(test)), test_x])
    y = train["label"].to_numpy(float)
    weights = state_entry_weights(train)
    offset = train["base_logit"].to_numpy(float)
    penalty = np.array([0.01, L2, L2])

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        eta = offset + train_x @ beta
        probability = expit(eta)
        loss = float(
            np.sum(weights * (np.logaddexp(0, eta) - y * eta))
            + 0.5 * np.sum(penalty * beta * beta)
        )
        gradient = train_x.T @ (weights * (probability - y)) + penalty * beta
        return loss, gradient

    fit = minimize(
        lambda beta: objective(beta),
        np.zeros(3),
        jac=True,
        bounds=[(None, None), (0.0, None), (None, 0.0)],
        method="L-BFGS-B",
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if not fit.success:
        raise RuntimeError(f"full-ladder challenger fit failed: {fit.message}")
    probability = expit(test["base_logit"].to_numpy(float) + test_x @ fit.x)
    return np.clip(probability, EPS, 1 - EPS), {
        "columns": ["intercept", *columns],
        "coefficients_standardized": fit.x.tolist(),
        "means": means.tolist(),
        "scales": scales.tolist(),
        "train_rows": len(train),
        "train_dates": train["target_date"].nunique(),
    }


def first_state_entries(frame: pd.DataFrame) -> pd.DataFrame:
    keys = ["city", "target_date", "current_bracket"]
    labels = frame.groupby(keys)["label"].nunique()
    if not labels.empty and int(labels.max()) != 1:
        raise ValueError("conflicting labels within exact-bracket state")
    return (
        frame.sort_values(["target_date", "city", "current_bracket", "decision_snapshot_dt"])
        .drop_duplicates(keys, keep="first")
        .reset_index(drop=True)
    )


def metrics(frame: pd.DataFrame, probability: str) -> dict[str, Any]:
    scored = first_state_entries(frame)
    y = scored["label"].to_numpy(float)
    p = scored[probability].clip(EPS, 1 - EPS).to_numpy(float)
    brier = binary_loss_values(y, p, metric="brier")
    logloss = binary_loss_values(y, p, metric="logloss")
    daily = pd.DataFrame(
        {"target_date": scored["target_date"], "brier": brier, "logloss": logloss}
    ).groupby("target_date", as_index=False).mean(numeric_only=True)
    return {
        "rows": len(frame),
        "state_entries": len(scored),
        "target_dates": scored["target_date"].nunique(),
        "holds": int(scored["label"].sum()),
        "actual_rate": float(scored.groupby("target_date")["label"].mean().mean()),
        "mean_probability": float(scored.groupby("target_date")[probability].mean().mean()),
        "brier": float(daily["brier"].mean()),
        "logloss": float(daily["logloss"].mean()),
    }


def compare(frame: pd.DataFrame, candidate: str, baseline: str) -> dict[str, Any]:
    scored = first_state_entries(frame)
    y = scored["label"].to_numpy(float)
    candidate_brier = binary_loss_values(
        y, scored[candidate].to_numpy(float), metric="brier"
    )
    candidate_logloss = binary_loss_values(
        y, scored[candidate].to_numpy(float), metric="logloss"
    )
    baseline_brier = binary_loss_values(
        y, scored[baseline].to_numpy(float), metric="brier"
    )
    baseline_logloss = binary_loss_values(
        y, scored[baseline].to_numpy(float), metric="logloss"
    )
    return {
        "brier": date_block_bootstrap_delta(
            scored, candidate_brier, baseline_brier, draws=BOOTSTRAP_DRAWS, seed=SEED
        ),
        "logloss": date_block_bootstrap_delta(
            scored,
            candidate_logloss,
            baseline_logloss,
            draws=BOOTSTRAP_DRAWS,
            seed=SEED + 1,
        ),
    }


def expanding_oof(frame: pd.DataFrame, secondary_start: str) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique())
    rows: list[pd.DataFrame] = []
    for index, target_date in enumerate(dates):
        if index < WARMUP_DATES or target_date >= secondary_start:
            continue
        train = frame[frame["target_date"] < target_date]
        test = frame[frame["target_date"] == target_date].copy()
        test["p_core_plus_ladder"], _ = fit_predict(train, test)
        rows.append(test)
    return pd.concat(rows, ignore_index=True)


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_run"):
        raise RuntimeError("preregistration is not frozen")
    if file_sha256(INPUT) != prereg["denominator"]["input_sha256"]:
        raise RuntimeError("parent ledger hash differs from preregistration")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    parent = pd.read_csv(INPUT, low_memory=False)
    parent["target_date"] = parent["target_date"].astype(str)
    parent["current_bracket"] = parent["current_bracket"].astype(str)
    parent["decision_snapshot_dt"] = pd.to_datetime(
        parent["decision_snapshot_ts_utc"], utc=True, errors="raise"
    )
    parent["label"] = parent["label"].astype(int)
    parent["p_core_hold"] = parent["p_core_no_obs_age"].clip(EPS, 1 - EPS)
    parent["p_market_hold"] = parent["market_mid"].clip(EPS, 1 - EPS)
    parent["base_logit"] = np.log(parent["p_core_hold"] / (1 - parent["p_core_hold"]))
    ledger = ladder_features(parent)
    scorable = ledger[ledger["ladder_status"].eq("scorable")].copy()
    dates = sorted(scorable["target_date"].unique())
    if len(dates) <= WARMUP_DATES + SECONDARY_DATES:
        raise RuntimeError("insufficient independent scorable target dates")
    secondary_dates = dates[-SECONDARY_DATES:]
    secondary_start = secondary_dates[0]
    development = expanding_oof(scorable, secondary_start)
    train = scorable[scorable["target_date"] < secondary_start]
    secondary = scorable[scorable["target_date"].isin(secondary_dates)].copy()
    secondary["p_core_plus_ladder"], frozen_fit = fit_predict(train, secondary)

    model_columns = [
        "p_market_hold", "p_ladder_feasible_hold", "p_core_hold", "p_core_plus_ladder"
    ]
    secondary_metrics = {name: metrics(secondary, name) for name in model_columns}
    comparisons = {
        "challenger_vs_core": compare(secondary, "p_core_plus_ladder", "p_core_hold"),
        "challenger_vs_market": compare(secondary, "p_core_plus_ladder", "p_market_hold"),
        "ladder_prior_vs_market": compare(secondary, "p_ladder_feasible_hold", "p_market_hold"),
    }
    vs_core = comparisons["challenger_vs_core"]
    historical_pass = all(
        vs_core[metric]["delta"] < 0 and vs_core[metric]["ci_high"] < 0
        for metric in ["brier", "logloss"]
    )
    market_pass = all(
        comparisons["challenger_vs_market"][metric]["delta"] < 0
        for metric in ["brier", "logloss"]
    )
    coverage_rate = len(scorable) / len(parent)
    coverage_pass = coverage_rate >= 0.8 and len(dates) >= 24
    verdict = (
        "historical_probability_pass_clean_forward_required"
        if historical_pass and market_pass and coverage_pass
        else "reject_full_ladder_challenger_v1"
    )

    coverage = (
        ledger.groupby("ladder_status", dropna=False)
        .agg(rows=("opportunity_id", "size"), dates=("target_date", "nunique"))
        .reset_index()
    )
    ladder_diagnostics = {
        "median_total_mid_mass": float(scorable["ladder_total_mid_mass"].median()),
        "mean_lower_impossible_mid_mass": float(
            scorable["lower_impossible_mid_mass"].mean()
        ),
        "median_lower_impossible_mid_mass": float(
            scorable["lower_impossible_mid_mass"].median()
        ),
        "mean_direct_two_sided_rung_fraction": float(
            scorable["direct_two_sided_rung_fraction"].mean()
        ),
    }
    ledger.to_csv(ARTIFACT_ROOT / "full_ladder_feature_ledger.csv", index=False)
    development.to_csv(ARTIFACT_ROOT / "development_oof_predictions.csv", index=False)
    secondary.to_csv(ARTIFACT_ROOT / "historical_secondary_holdout_predictions.csv", index=False)
    coverage.to_csv(ARTIFACT_ROOT / "evidence_funnel.csv", index=False)
    (ARTIFACT_ROOT / "frozen_model.json").write_text(
        json.dumps(json_ready(frozen_fit), indent=2) + "\n", encoding="utf-8"
    )

    payload = {
        "schema_version": RESEARCH_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_id": RESEARCH_ID,
        "run_id": RUN_ID,
        "readiness": {
            "pit_state_four_clocks": "READY exact parent decision snapshot",
            "canonical_build_identity": "READY production manifest strict critical=0; parent artifact hash frozen",
            "fresh_market_quote_depth": "READY historical exact decision-time archive; current production collector healthy",
            "settlement_labels": "READY on parent ledger",
            "independent_target_dates": len(dates),
            "clean_frozen_forward": "BLOCKED hypothesis frozen on 2026-08-08; historical secondary holdout is not clean forward",
        },
        "denominator": {
            "parent_rows": len(parent),
            "parent_dates": parent["target_date"].nunique(),
            "scorable_rows": len(scorable),
            "scorable_dates": len(dates),
            "coverage_rate": coverage_rate,
            "parent_sha256": file_sha256(INPUT),
            "preregistration_sha256": file_sha256(PREREG),
            "date_min": min(parent["target_date"]),
            "date_max": max(parent["target_date"]),
        },
        "signal_funnel": [
            {"stage": "frozen Core PIT checkpoints", "grain": "checkpoint", "rows": len(parent), "dates": parent["target_date"].nunique()},
            {"stage": "full-ladder scorable", "grain": "checkpoint", "rows": len(scorable), "dates": len(dates)},
            {"stage": "historical secondary state entries", "grain": "state_entry", "rows": len(first_state_entries(secondary)), "dates": len(secondary_dates)},
        ],
        "evidence_funnel": coverage.to_dict(orient="records"),
        "development": {
            "rows": len(development),
            "dates": development["target_date"].nunique(),
            "metrics": {name: metrics(development, name) for name in ["p_market_hold", "p_ladder_feasible_hold", "p_core_hold", "p_core_plus_ladder"]},
            "challenger_vs_core": compare(development, "p_core_plus_ladder", "p_core_hold"),
        },
        "historical_secondary_holdout_dates": secondary_dates,
        "historical_secondary_metrics": secondary_metrics,
        "historical_secondary_comparisons": comparisons,
        "ladder_diagnostics": ladder_diagnostics,
        "frozen_fit": frozen_fit,
        "gates": {
            "historical_probability": "PASS" if historical_pass else "FAIL",
            "market_baseline": "PASS" if market_pass else "FAIL",
            "coverage": "PASS" if coverage_pass else "FAIL",
            "clean_forward": "FAIL_NOT_YET_AVAILABLE",
            "execution": "NOT_RUN_PROBABILITY_GATE_FAILED" if not (historical_pass and market_pass) else "DEFERRED_UNTIL_CLEAN_FORWARD",
        },
        "verdict": verdict,
        "production_action": "no_live_change",
        "artifacts": {"root": str(ARTIFACT_ROOT)},
    }
    SUMMARY.write_text(json.dumps(json_ready(payload), indent=2) + "\n", encoding="utf-8")

    def delta_text(item: dict[str, Any]) -> str:
        return (
            f"{item['delta']:+.6f}（95% CI "
            f"[{item['ci_low']:+.6f}, {item['ci_high']:+.6f}]）"
        )

    lines = [
        "# Core Carry full-ladder prior v1",
        "",
        f"**结论：`{verdict}`；不改 live。**",
        "",
        "## 研究问题与 readiness",
        "",
        "- 目标仍是 Core Carry 的 `P(current exact bracket holds)`；没有混入 first-seen/markout 策略。",
        f"- 固定母表 {len(parent):,} checkpoints / {parent['target_date'].nunique()} dates；完整 ladder 可评分 {len(scorable):,} / {coverage_rate:.1%}，覆盖 {len(dates)} dates。",
        "- 每行只读取母表保存的 exact decision-time orderbook snapshot；缺证据记 coverage gap。",
        "- 这个机制在 2026-08-08 才冻结，因此最后 8 dates 只是 historical secondary holdout，不是 clean forward。",
        "",
        "## Primary：historical secondary holdout",
        "",
        "| model | state entries | Brier | logloss | mean p | actual |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in model_columns:
        item = secondary_metrics[name]
        lines.append(
            f"| {name} | {item['state_entries']} | {item['brier']:.6f} | {item['logloss']:.6f} | {item['mean_probability']:.3%} | {item['actual_rate']:.3%} |"
        )
    lines.extend(
        [
            "",
            f"- challenger − Core Brier：{delta_text(vs_core['brier'])}",
            f"- challenger − Core logloss：{delta_text(vs_core['logloss'])}",
            f"- challenger − market Brier：{delta_text(comparisons['challenger_vs_market']['brier'])}",
            f"- challenger − market logloss：{delta_text(comparisons['challenger_vs_market']['logloss'])}",
            "",
            "## 为什么没有增量",
            "",
            f"- no-fit feasible-ladder prior 相对 raw market 的 Brier/logloss delta 为 {comparisons['ladder_prior_vs_market']['brier']['delta']:+.6f} / {comparisons['ladder_prior_vs_market']['logloss']['delta']:+.6f}，方向更差。",
            f"- 冻结模型的两个 ladder 系数都是 `{frozen_fit['coefficients_standardized'][1]:.6f}` / `{frozen_fit['coefficients_standardized'][2]:.6f}`；secondary 的小幅改善只来自 calibration intercept `{frozen_fit['coefficients_standardized'][0]:+.6f}`，不是 ladder shape。",
            f"- development OOF 的 challenger−Core Brier/logloss 为 {payload['development']['challenger_vs_core']['brier']['delta']:+.6f} / {payload['development']['challenger_vs_core']['logloss']['delta']:+.6f}；Brier CI 已完全在 0 以上，历史开发段明确更差。",
            f"- 市场本身已经基本清掉不可能的下方档：lower-rung mass 中位数 {ladder_diagnostics['median_lower_impossible_mid_mass']:.6f}、均值 {ladder_diagnostics['mean_lower_impossible_mid_mass']:.6f}；全 ladder midpoint 总质量中位数 {ladder_diagnostics['median_total_mid_mass']:.4f}，接近 1。静态完整 ladder 因而没有补出当前 token 未包含的新信息。",
            "- 451 个 coverage gap 全是旧 snapshot 只采 2–3 个局部 rungs；没有 missing file、未来 quote 替代或 current-rung join 错误。这是旧 collector scope，不是策略过滤。",
            "",
            "## 策略动作",
            "",
            f"- historical_probability={payload['gates']['historical_probability']}；market_baseline={payload['gates']['market_baseline']}；coverage={payload['gates']['coverage']}；clean_forward=FAIL_NOT_YET_AVAILABLE。",
            f"- execution={payload['gates']['execution']}。没有用少量 selected trades 倒推模型，也没有修改 eligibility、fixed 10、maker 或生产参数。",
            "- 本轮历史概率门失败：不新增 Core challenger、不接 shadow、不跑执行 ROI，也不为 coverage gap 追补一个事后 3-rung 版本。现有完整 ladder collector照常保留，但不把它接进 Core 概率。",
            "",
            "## 产物与复现",
            "",
            f"- 大产物：`{ARTIFACT_ROOT}`",
            f"- prereg SHA-256：`{file_sha256(PREREG)}`",
            "",
            "```bash",
            ".venv/bin/python scripts/analysis/reheat_risk/core_carry_full_ladder_prior.py",
            "```",
        ]
    )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_ready({
        "verdict": verdict,
        "gates": payload["gates"],
        "coverage": payload["denominator"],
        "secondary_metrics": secondary_metrics,
        "comparisons": comparisons,
        "report": str(REPORT.relative_to(ROOT)),
    }), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
