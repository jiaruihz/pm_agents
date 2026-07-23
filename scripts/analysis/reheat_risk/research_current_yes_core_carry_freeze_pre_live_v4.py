#!/usr/bin/env python3
"""Freeze the current-YES core carry model and replay its live entry contract.

The historical selector is replayed with a five-share ask-ladder cost, rather
than a top-of-book proxy.  The output model artifact is execution-neutral and
is consumed by the zero-notional pre-live runner.
"""

from __future__ import annotations

import gzip
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_mechanism_timing_audit_v1 as audit,
)
from scripts.analysis.reheat_risk import (  # noqa: E402
    research_current_yes_carry_residual_entry_v2 as v2,
)
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    canonical_hash,
    official_weather_fee_per_share,
    score_probability,
    walk_ask_ladder,
)
from weather_data_feed.city_calendar import CITY_TIMEZONE  # noqa: E402


CORE = list(v2.FEATURE_SETS["core"])
FEATURE_SET_NAME = "core"
PROBABILITY_COLUMN = "p_core"
QUANTITY = 5.0
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/current_yes_core_carry_freeze_pre_live_v4"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-23-current-yes-core-carry-freeze-pre-live-v4.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-23-current-yes-core-carry-freeze-pre-live-v4.md"
ARTIFACT_PATH = ROOT / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v1.json"
ARTIFACT_FROZEN_AT_UTC = "2026-07-23T10:30:04+00:00"
ARTIFACT_VERSION = "current_yes_core_carry_model_v1"
ARTIFACT_LIFECYCLE = "pre_live_frozen_zero_notional_only"
ENTRY_POLICY_EXTRAS: dict[str, Any] = {}
REPORT_TITLE = "Current-YES Core Carry v1 冻结与 pre-live 准备"
REPORT_STATUS = "superseded-for-now / zero-notional historical artifact"
POLICY_LABEL = "v1"
OBSERVATION_AGE_NOTE = (
    "v1 包含 obs_age_min；后续 train/serve 审计确认该维主要表达采集节奏，"
    "已由 no-age v2 supersede-for-now。"
)


def json_ready(value: Any) -> Any:
    return v2.json_ready(value)


def timestamp_ns(value: Any) -> int | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(parsed) else int(parsed.value)


def raw_asks_for_row(row: pd.Series) -> list[dict[str, Any]]:
    relative = str(row.get("orderbook_file") or "")
    path = Path(relative)
    if not path.is_absolute():
        path = ROOT / path
    wanted_ts = timestamp_ns(row.get("decision_snapshot_ts_utc"))
    if not path.exists() or wanted_ts is None:
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if (
                record.get("status") == "ok"
                and str(record.get("outcome") or "").lower() == "yes"
                and str(record.get("city") or "") == str(row["city"])
                and str(record.get("event_date") or "") == str(row["target_date"])
                and str(record.get("bracket") or "") == str(row["current_bracket"])
                and timestamp_ns(record.get("snapshot_ts_utc")) == wanted_ts
            ):
                return list((record.get("raw") or {}).get("asks") or [])
    return []


def add_five_share_cost(frame: pd.DataFrame) -> pd.DataFrame:
    """Use top ask when its size covers five; stream raw books only otherwise."""

    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        ask = float(row["current_yes_ask"])
        top_size = float(row["current_yes_ask_size"])
        if top_size >= QUANTITY:
            ladder = walk_ask_ladder([{"price": ask, "size": top_size}], QUANTITY)
            source = "feature_top_ask_covers_quantity"
        else:
            ladder = walk_ask_ladder(raw_asks_for_row(row), QUANTITY)
            source = "archived_full_ask_ladder"
        rows.append(
            {
                "five_share_executable": bool(ladder["executable"]),
                "five_share_cost_per_share": ladder["effective_cost_per_share"],
                "five_share_principal_vwap": ladder["principal_vwap"],
                "five_share_best_ask": ladder["best_ask"],
                "five_share_cost_source": source,
                "five_share_slippage_per_share": (
                    None
                    if ladder["effective_cost_per_share"] is None
                    else float(ladder["effective_cost_per_share"])
                    - (ask + official_weather_fee_per_share(ask))
                ),
            }
        )
    return pd.concat([frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def first_entries(frame: pd.DataFrame, *, floor: float, edge_buffer: float) -> pd.DataFrame:
    eligible = frame[
        frame["five_share_executable"]
        & ~frame["current_bracket"].astype(str).str.contains(r"\+", regex=True)
        & frame["market_mid"].ge(floor)
        & frame[PROBABILITY_COLUMN].sub(frame["five_share_cost_per_share"]).gt(edge_buffer)
    ].copy()
    eligible["model_probability"] = eligible[PROBABILITY_COLUMN]
    eligible["model_edge_after_fee_and_depth"] = (
        eligible[PROBABILITY_COLUMN] - eligible["five_share_cost_per_share"]
    )
    return audit.first_city_day(eligible)


def entry_metrics(entries: pd.DataFrame, name: str) -> dict[str, Any]:
    if entries.empty:
        return {
            "selector": name,
            "city_days": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "avg_effective_cost": None,
            "roi": None,
            "signals_per_covered_date": None,
        }
    cost = entries["five_share_cost_per_share"].astype(float)
    pnl = entries["label"].astype(float) - cost
    daily = (
        entries.assign(_cost=cost, _pnl=pnl)
        .groupby("target_date")[["_pnl", "_cost"]]
        .sum()
    )
    values = daily[["_pnl", "_cost"]].to_numpy(float)
    rng = np.random.default_rng(audit.SEED)
    draws = []
    for _ in range(audit.BOOTSTRAP_REPS):
        sample = values[rng.integers(0, len(values), len(values))]
        sample_cost = float(sample[:, 1].sum())
        if sample_cost > 0:
            draws.append(float(sample[:, 0].sum() / sample_cost))
    return {
        "selector": name,
        "city_days": int(len(entries)),
        "dates": int(entries["target_date"].nunique()),
        "cities": int(entries["city"].nunique()),
        "wins": int(entries["label"].sum()),
        "losses": int(entries["label"].eq(0).sum()),
        "win_rate": float(entries["label"].mean()),
        "avg_effective_cost": float(cost.mean()),
        "avg_edge": float(entries["model_edge_after_fee_and_depth"].mean()),
        "roi": float(pnl.sum() / cost.sum()),
        "target_date_block_ci95": [
            float(np.quantile(draws, 0.025)),
            float(np.quantile(draws, 0.975)),
        ],
        "signals_per_covered_date": float(len(entries) / entries["target_date"].nunique()),
        "signals_p90_active_date": float(entries.groupby("target_date").size().quantile(0.90)),
        "signals_max_active_date": int(entries.groupby("target_date").size().max()),
        "worst_day_pnl_per_share": float(
            entries.assign(_pnl=pnl).groupby("target_date")["_pnl"].sum().min()
        ),
    }


def fit_artifact(universe: pd.DataFrame) -> tuple[dict[str, Any], Any]:
    model = audit.make_model(CORE)
    counts = universe.groupby(["city", "target_date"])["label"].transform("size")
    weights = 1.0 / counts.clip(lower=1).to_numpy(float)
    model.fit(universe[CORE], universe["label"], model__sample_weight=weights)

    numeric = model.named_steps["pre"].named_transformers_["numeric"]
    imputer = numeric.named_steps["imputer"]
    scaler = numeric.named_steps["scale"]
    logistic = model.named_steps["model"]
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "lifecycle": ARTIFACT_LIFECYCLE,
        "frozen_at_utc": ARTIFACT_FROZEN_AT_UTC,
        "target": "current exact bracket remains final winning bracket",
        "numeric_features": CORE,
        "numeric_medians": [float(value) for value in imputer.statistics_],
        "numeric_means": [float(value) for value in scaler.mean_],
        "numeric_scales": [float(value) for value in scaler.scale_],
        "coef": [float(value) for value in logistic.coef_[0]],
        "intercept": float(logistic.intercept_[0]),
        "estimator": {
            "type": "median_imputer+standard_scaler+logistic_regression",
            "C": 0.1,
            "random_state": audit.SEED,
            "sample_weight": "each city-target_date has total training weight one",
        },
        "training_snapshot": {
            "rows": int(len(universe)),
            "city_days": int(universe.groupby(["city", "target_date"]).ngroups),
            "cities": int(universe["city"].nunique()),
            "dates": int(universe["target_date"].nunique()),
            "date_min": str(universe["target_date"].min()),
            "date_max": str(universe["target_date"].max()),
            "source": "current_yes_carry_residual_entry_v2.prepare_universe",
        },
        "entry_policy": {
            "expression": "current_bracket_BUY_YES",
            "bounded_exact_bracket_only": True,
            "local_hour_start": 13,
            "local_hour_end": 17,
            "checkpoint": "first successfully scored fresh book at or after local minute 30 in each local hour",
            "one_scored_checkpoint_per_city_day_local_hour": True,
            "first_positive_ev_locks_city_day": True,
            "wait_for_second_confirmation": False,
            "market_mid_floor": 0.8,
            "taker_shares": 5,
            "min_edge_after_fee_and_depth": 0.0,
            "price_input": "fresh direct two-sided CLOB book",
            "cost_input": "full five-share ask-ladder principal plus official per-level fee",
            "d1_no_required": False,
            "maker_enabled": False,
            **ENTRY_POLICY_EXTRAS,
        },
        "research_lineage": [
            "docs/analysis/2026-07/2026-07-22-current-yes-carry-residual-entry-v2.json",
            "docs/analysis/2026-07/2026-07-22-current-yes-carry-clean-exhaustion-backfill-v3.json",
            "docs/analysis/2026-07/2026-07-23-current-yes-core-carry-freeze-pre-live-v4.json",
        ],
    }
    artifact["artifact_hash"] = canonical_hash(artifact)
    return artifact, model


def golden_rows(universe: pd.DataFrame, artifact: dict[str, Any], model: Any) -> list[dict[str, Any]]:
    positions = np.linspace(0, len(universe) - 1, 12, dtype=int)
    output = []
    for position in positions:
        row = universe.iloc[int(position)]
        features = {
            name: (None if pd.isna(row[name]) else float(row[name]))
            for name in CORE
        }
        expected = float(model.predict_proba(pd.DataFrame([features], columns=CORE))[0, 1])
        actual = score_probability(features, artifact)
        if abs(expected - actual) > 1e-12:
            raise AssertionError(f"artifact parity failed at row={position}: {expected} != {actual}")
        output.append(
            {
                "source_row": int(position),
                "city": str(row["city"]),
                "target_date": str(row["target_date"]),
                "features": features,
                "expected_probability": expected,
            }
        )
    return output


def local_minute(row: pd.Series) -> int | None:
    zone = CITY_TIMEZONE.get(str(row["city"]))
    stamp = pd.to_datetime(row["decision_snapshot_ts_utc"], utc=True, errors="coerce")
    if not zone or pd.isna(stamp):
        return None
    return int(stamp.tz_convert(ZoneInfo(zone)).minute)


def write_report(payload: dict[str, Any]) -> None:
    core = payload["frozen_policy_result"]
    lines = [
        f"# {REPORT_TITLE}",
        "",
        f"Status: `{REPORT_STATUS}`",
        "",
        "## 冻结结论",
        "",
        f"- 固定策略：当地 13–17 点、market midpoint≥0.80、每小时第一个 minute≥30 且成功读到 fresh "
        f"two-sided book 的 checkpoint；5-share 全 ask ladder 加官方 fee 后首次正 EV 入场，同 city-day 锁定。",
        f"- OOF 五档成本重放：{core['city_days']} 笔 / {core['dates']} 个目标日 / {core['cities']} 城，"
        f"{core['wins']} 胜 {core['losses']} 负，胜率 {core['win_rate']:.2%}，"
        f"均价（含 fee）{core['avg_effective_cost']:.4f}，ROI {core['roi']:+.2%}，"
        f"date-block 95% CI [{core['target_date_block_ci95'][0]:+.2%},"
        f"{core['target_date_block_ci95'][1]:+.2%}]。",
        "- 不加入 faded/support/clean-exhaustion hard gate；这些物理语义会继续记录，但历史同分母没有证明其增量。",
        f"- 不等待第二次确认，不设 0.95 ask 门槛，不做 maker；这些都不是 {POLICY_LABEL} 的历史获利定义。",
        f"- {OBSERVATION_AGE_NOTE}",
        "",
        "## 回测/live 时钟一致性",
        "",
        f"历史 OOF states 中 minute=:30 占 {payload['checkpoint_audit']['minute_30_share']:.2%}，"
        f"minute 28–35 占 {payload['checkpoint_audit']['minute_28_35_share']:.2%}。"
        "runner 只负责发现新 snapshot，不允许按盘口轮询频率重复撞策略阈值。",
        "",
        "## 上线状态",
        "",
        "模型参数、特征顺序、imputation、5-share 成本和 checkpoint contract 已冻结。当前仅用于 "
        "zero-notional pre-live：记录全部正/负评分与 would-order，不包含签名、私钥或真实下单代码。"
        "would-order 会同时读取 H1/H2 submitted city-day 并标记 family conflict，避免未来 canary 重复暴露。"
        "真实下单不由本冻结报告自动授权。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)

    universe = v2.prepare_universe()
    oof = v2.expanding_predictions(universe)
    costed = add_five_share_cost(oof)

    sensitivity = []
    for floor in (0.75, 0.80, 0.85, 0.90):
        for edge_buffer in (0.0, 0.005, 0.01, 0.02):
            entries = first_entries(costed, floor=floor, edge_buffer=edge_buffer)
            sensitivity.append(
                {
                    "market_mid_floor": floor,
                    "edge_buffer": edge_buffer,
                    **entry_metrics(entries, f"floor={floor:.2f},buffer={edge_buffer:.3f}"),
                }
            )

    frozen_entries = first_entries(costed, floor=0.80, edge_buffer=0.0)
    artifact, fitted_model = fit_artifact(universe)
    golden = golden_rows(universe, artifact, fitted_model)
    ARTIFACT_PATH.write_text(
        json.dumps(json_ready(artifact), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    costed.to_csv(OUT_DIR / "oof_states_five_share_cost.csv", index=False)
    frozen_entries.to_csv(OUT_DIR / "frozen_policy_entries.csv", index=False)
    pd.DataFrame(sensitivity).to_csv(OUT_DIR / "threshold_sensitivity.csv", index=False)
    (OUT_DIR / "model_golden_rows.json").write_text(
        json.dumps(json_ready(golden), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    minutes = costed.apply(local_minute, axis=1)
    payload = {
        "generated_at_utc": generated_at,
        "status": "_".join(
            part
            for part in REPORT_STATUS.lower()
            .replace("/", " ")
            .replace("-", " ")
            .split()
            if part
        ),
        "frozen_policy_result": entry_metrics(frozen_entries, f"frozen_{POLICY_LABEL}"),
        "cost_audit": {
            "oof_rows": int(len(costed)),
            "full_five_share_executable_rate": float(costed["five_share_executable"].mean()),
            "rows_requiring_full_ladder_beyond_top": int(
                costed["five_share_cost_source"].eq("archived_full_ask_ladder").sum()
            ),
            "selected_avg_slippage_cents_per_share": float(
                100 * frozen_entries["five_share_slippage_per_share"].mean()
            ),
        },
        "checkpoint_audit": {
            "minute_30_share": float(minutes.eq(30).mean()),
            "minute_28_35_share": float(minutes.between(28, 35).mean()),
            "contract": artifact["entry_policy"]["checkpoint"],
        },
        "selection_review": {
            "chosen_model": FEATURE_SET_NAME,
            "not_added": [
                "legacy faded/path features: historical strict-high clock semantics were wrong and path-added did not improve core",
                "clean exhaustion hard gates: semantically correct but no same-denominator proper-score improvement",
                "CC ceiling/route merge: no incremental historical benefit",
                "maker: no execution alpha credited in v1",
            ],
            "sensitivity_is_diagnostic_not_threshold_optimization": True,
        },
        "artifact": {
            "path": str(ARTIFACT_PATH.relative_to(ROOT)),
            "hash": artifact["artifact_hash"],
            "golden_rows": len(golden),
        },
        "outputs": {
            "script": str(Path(__file__).relative_to(ROOT)),
            "json": str(OUT_JSON.relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
            "oof_costed": str((OUT_DIR / "oof_states_five_share_cost.csv").relative_to(ROOT)),
            "entries": str((OUT_DIR / "frozen_policy_entries.csv").relative_to(ROOT)),
            "sensitivity": str((OUT_DIR / "threshold_sensitivity.csv").relative_to(ROOT)),
            "golden": str((OUT_DIR / "model_golden_rows.json").relative_to(ROOT)),
        },
        "real_live_action": "none",
    }
    OUT_JSON.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(payload)
    print(json.dumps(json_ready(payload), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
