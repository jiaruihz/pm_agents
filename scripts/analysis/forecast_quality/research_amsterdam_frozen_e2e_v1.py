#!/usr/bin/env python3
"""Materialise a same-denominator Amsterdam frozen-evaluation ledger.

This is a research-only reader of sealed WCIR artifacts.  It deliberately does
not import an execution client or write under a runtime directory.  Historical
market rows are optional: a missing quote or settlement stays as a NULL row in
the ledger, rather than becoming an abstain or being silently filtered away.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = ROOT / "reviews/wcir_unified_data_amsterdam_pilot_v1_1"
DEFAULT_OUTPUT = ROOT / "reviews/generated/amsterdam_frozen_e2e_v1"
DEFAULT_LEGACY_CHECKPOINTS = {
    "V9_FINAL_SETTLEMENT": Path("/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_remaining_heat_v9_ecmwf_day1/development_2026_08_run61b8/checkpoint_predictions.csv.gz"),
    "MARKET_OFFSET_FINAL_SETTLEMENT": Path("/Volumes/jrs/weather_data_feed_service_runtime/research/model_runs/amsterdam_knmi_market_offset_probability/collector_replay_20260730_20260811_v8_final_review/checkpoint_predictions.csv.gz"),
}
MODELS = ("B2", "M1", "M2")
SUPPORT = np.arange(-10, 11, dtype=int)
IDENTITY = ("market_id", "condition_id", "token_id")


def _jsonable(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(type(value).__name__)


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], source: str) -> None:
    missing = [name for name in columns if name not in frame]
    if missing:
        raise ValueError(f"{source} missing required columns: {missing}")


def _probability_columns(frame: pd.DataFrame) -> list[str]:
    columns = [f"p_delta_{tick:+d}" for tick in SUPPORT]
    _require_columns(frame, columns, "prediction parquet")
    return columns


def _weather_metrics(frame: pd.DataFrame, model: str) -> dict[str, Any]:
    cols = [f"{model.lower()}_{name}" for name in (f"p_delta_{tick:+d}" for tick in SUPPORT)]
    available = frame.loc[frame[cols].notna().all(axis=1) & frame["next_official_delta_native_tick"].notna()].copy()
    if available.empty:
        return {"status": "NOT_ESTIMABLE", "rows": 0}
    probabilities = available[cols].to_numpy(float)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    labels = available["next_official_delta_native_tick"].astype(int).to_numpy()
    positions = labels - SUPPORT[0]
    in_support = (positions >= 0) & (positions < len(SUPPORT))
    probabilities, positions, available = probabilities[in_support], positions[in_support], available.loc[in_support]
    if "model_score_weight" in available:
        weight_values = available["model_score_weight"]
    elif "weight" in available:
        weight_values = available["weight"]
    else:
        weight_values = pd.Series(1.0, index=available.index)
    weights = pd.to_numeric(weight_values, errors="coerce").fillna(0.0).to_numpy(float)
    if not np.isfinite(weights).any() or weights.sum() <= 0:
        weights = np.ones(len(available), dtype=float)
    weights /= weights.sum()
    one_hot = np.zeros_like(probabilities)
    one_hot[np.arange(len(positions)), positions] = 1.0
    rps = np.sum((np.cumsum(probabilities, axis=1) - np.cumsum(one_hot, axis=1)) ** 2, axis=1) / (len(SUPPORT) - 1)
    chosen = SUPPORT[probabilities.argmax(axis=1)]
    return {
        "status": "OK",
        "rows": int(len(available)),
        "target_dates": int(available.target_date.nunique()),
        "first_target_date": str(available.target_date.min()),
        "last_target_date": str(available.target_date.max()),
        "official_print_groups": int(available["official_print_group_id"].nunique()) if "official_print_group_id" in available else None,
        "exact_accuracy": float(np.average(chosen == labels[in_support], weights=weights)),
        "rps": float(np.average(rps, weights=weights)),
        "logloss": float(np.average(-np.log(np.clip(probabilities[np.arange(len(positions)), positions], 1e-12, 1.0)), weights=weights)),
    }


def _wide_predictions(input_dir: Path) -> pd.DataFrame:
    chunks: list[pd.DataFrame] = []
    for cohort_suffix in ("FULL_CHECKPOINT", "OPPORTUNITY_MATCHED", "CAPTURED_PIT"):
        frames: list[pd.DataFrame] = []
        for model in MODELS:
            frame = pd.read_parquet(input_dir / f"{model}_PREDICTIONS_{cohort_suffix}.parquet")
            _require_columns(frame, ("decision_vintage_id", "cohort_id", "true_delta_tick", "target_date"), str(model))
            pcols = _probability_columns(frame)
            renamed = frame[["decision_vintage_id", "cohort_id", "true_delta_tick", "target_date", "official_print_group_id", "observed_at", "weight", *pcols]].copy()
            renamed = renamed.rename(columns={name: f"{model.lower()}_{name}" for name in pcols})
            renamed = renamed.rename(columns={"weight": f"{model.lower()}_weight"})
            frames.append(renamed)
        first, *rest = frames
        key = ["decision_vintage_id", "cohort_id"]
        for other in rest:
            # Same cohort/model rows are a hard invariant; an inner join would hide a violation.
            if not first[key].equals(other[key]):
                raise ValueError(f"{cohort_suffix}: model prediction denominators differ")
            keep = [name for name in other if name not in {*key, "true_delta_tick", "target_date", "official_print_group_id", "observed_at"}]
            first = first.merge(other[key + keep], on=key, how="left", validate="one_to_one")
        chunks.append(first)
    return pd.concat(chunks, ignore_index=True)


def _exact_market_join(base: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """Left join only exact identities; preserve every base row and its reason."""
    _require_columns(base, ("decision_vintage_id", *IDENTITY), "base ledger")
    _require_columns(market, ("decision_vintage_id", *IDENTITY), "market ledger")
    market = market.copy()
    valid = market[list(IDENTITY)].notna().all(axis=1) & market[list(IDENTITY)].ne("").all(axis=1)
    market = market.loc[valid].drop_duplicates(["decision_vintage_id", *IDENTITY], keep="last")
    market["_exact_market_row_present"] = True
    right = [name for name in market if name not in {"decision_vintage_id", *IDENTITY}]
    result = base.merge(market[["decision_vintage_id", *IDENTITY, *right]], on=["decision_vintage_id", *IDENTITY], how="left", validate="many_to_one", suffixes=("", "_market"))
    base_has_identity = result[list(IDENTITY)].notna().all(axis=1) & result[list(IDENTITY)].ne("").all(axis=1)
    matched = result["_exact_market_row_present"].eq(True)
    result["market_join_reason"] = np.where(~base_has_identity, "market_identity_missing", np.where(matched, "EXACT_IDENTITY_MATCHED", "market_row_missing"))
    result = result.drop(columns=["_exact_market_row_present"])
    return result


def _normalise_legacy(path: Path) -> pd.DataFrame:
    """Read an explicitly supplied old-model export, never discover files by scan.

    A legacy export must retain exact market identity.  Common aliases are
    accepted so historical V9/offset artifacts can be materialised externally.
    """
    frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    aliases = {"settlement_label": ("settled_yes", "label", "outcome_yes"), "market_probability": ("market_p", "raw_market_probability"), "legacy_probability": ("model_probability", "posterior_probability", "v9_probability")}
    for canonical, choices in aliases.items():
        if canonical not in frame:
            for candidate in choices:
                if candidate in frame:
                    frame[canonical] = frame[candidate]
                    break
    _require_columns(frame, ("target_date", *IDENTITY), str(path))
    return frame


def _fit_layer_b(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Frozen target-date split.  It only runs when an independent eval is viable."""
    needed = ["target_date", "settlement_label", "market_probability", "b2_p_up"]
    if any(name not in frame for name in needed):
        return frame, {"status": "NOT_ESTIMABLE", "reason": "required_layer_b_columns_missing"}
    work = frame.dropna(subset=needed).copy()
    work["target_date"] = work.target_date.astype(str)
    dates = sorted(work.target_date.unique())
    if len(dates) < 2:
        return frame, {"status": "NOT_ESTIMABLE", "reason": "fewer_than_two_target_dates", "rows": int(len(work))}
    split = max(1, int(len(dates) * 0.8))
    train_dates, eval_dates = set(dates[:split]), set(dates[split:])
    train, evaluation = work[work.target_date.isin(train_dates)], work[work.target_date.isin(eval_dates)]
    x_train = train[["market_probability", "b2_p_up"]].to_numpy(float)
    x_eval = evaluation[["market_probability", "b2_p_up"]].to_numpy(float)
    required_rank = 3  # intercept + market + independent frozen weather feature
    design_rank = int(np.linalg.matrix_rank(np.column_stack([np.ones(len(x_train)), x_train])))
    if (
        len(train) < required_rank
        or evaluation.empty
        or train.settlement_label.nunique() < 2
        or design_rank < required_rank
    ):
        return frame, {
            "status": "NOT_ESTIMABLE",
            "reason": "insufficient_or_rank_deficient_nonoverlapping_design",
            "train_rows": int(len(train)),
            "eval_rows": int(len(evaluation)),
            "design_rank": design_rank,
            "required_design_rank": required_rank,
        }
    # Ridge is intentionally fixed: no tuning on eval dates.
    mr1 = Ridge(alpha=1.0).fit(x_train, train.settlement_label.to_numpy(float))
    mr0 = Ridge(alpha=1.0).fit(x_train[:, [0]], train.settlement_label.to_numpy(float))
    result = frame.copy()
    result["mr0_prediction"] = np.nan
    result["mr1_prediction"] = np.nan
    eval_index = evaluation.index
    result.loc[eval_index, "mr0_prediction"] = np.clip(mr0.predict(x_eval[:, [0]]), 0, 1)
    result.loc[eval_index, "mr1_prediction"] = np.clip(mr1.predict(x_eval), 0, 1)
    raw_market_eval = _binary_score(
        evaluation["market_probability"], evaluation["settlement_label"]
    )
    mr0_eval = _binary_score(result.loc[eval_index, "mr0_prediction"], evaluation["settlement_label"])
    mr1_eval = _binary_score(result.loc[eval_index, "mr1_prediction"], evaluation["settlement_label"])
    return result, {
        "status": "OK",
        "train_target_dates": sorted(train_dates),
        "eval_target_dates": sorted(eval_dates),
        "train_rows": int(len(train)),
        "eval_rows": int(len(evaluation)),
        "design_rank": design_rank,
        "required_design_rank": required_rank,
        "alpha": 1.0,
        "raw_market_eval": raw_market_eval,
        "mr0_market_only_eval": mr0_eval,
        "mr1_market_plus_b2_eval": mr1_eval,
        "mr1_minus_mr0_brier": (
            float(mr1_eval["brier"] - mr0_eval["brier"])
            if mr0_eval["status"] == mr1_eval["status"] == "OK"
            else None
        ),
    }


def _binary_score(probability: pd.Series, label: pd.Series) -> dict[str, Any]:
    valid = probability.notna() & label.notna()
    if not valid.any():
        return {"status": "NOT_ESTIMABLE", "rows": 0}
    p = probability.loc[valid].clip(1e-12, 1 - 1e-12).astype(float).to_numpy()
    y = label.loc[valid].astype(float).to_numpy()
    return {"status": "OK", "rows": int(len(y)), "accuracy": float(((p >= .5) == (y >= .5)).mean()), "brier": float(np.mean((p - y) ** 2)), "logloss": float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))}


def _legacy_checkpoint_rows() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load only the two documented, fixed legacy artifacts; no directory scan."""
    rows: list[pd.DataFrame] = []
    report: dict[str, Any] = {}
    for family, path in DEFAULT_LEGACY_CHECKPOINTS.items():
        if not path.is_file():
            report[family] = {"status": "MISSING_ARTIFACT", "path": str(path)}
            continue
        source = pd.read_csv(path, compression="gzip")
        _require_columns(source, ("target_date", "observed_at_utc", "label_leave", "market_p"), str(path))
        model_column = "p_market_posterior" if family.startswith("MARKET_OFFSET") else "p_model"
        _require_columns(source, (model_column,), str(path))
        frame = pd.DataFrame({
            "record_scope": family,
            "target_date": source.target_date.astype(str),
            "observed_at": pd.to_datetime(source.observed_at_utc, utc=True, errors="coerce"),
            "settlement_label": pd.to_numeric(source.label_leave, errors="coerce"),
            "market_probability": pd.to_numeric(source.market_p, errors="coerce"),
            "legacy_probability": pd.to_numeric(source[model_column], errors="coerce"),
            "legacy_source_path": str(path),
            # Old artifacts do not carry token identity: make the absence explicit.
            "market_join_reason": "legacy_checkpoint_without_exact_token_identity",
        })
        paired = frame.dropna(subset=["settlement_label", "market_probability", "legacy_probability"])
        report[family] = {
            "status": "OK", "rows": int(len(frame)), "target_dates": int(frame.target_date.nunique()),
            "first_target_date": str(frame.target_date.min()), "last_target_date": str(frame.target_date.max()),
            "same_rows_labels": int(len(paired)),
            "model": _binary_score(paired.legacy_probability, paired.settlement_label),
            "raw_market": _binary_score(paired.market_probability, paired.settlement_label),
            "path": str(path),
        }
        rows.append(frame)
    return (pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()), report


def build_common_ledger(input_dir: Path, legacy_paths: Iterable[Path] = ()) -> tuple[pd.DataFrame, dict[str, Any]]:
    cohort = pd.read_parquet(input_dir / "AMSTERDAM_COHORT_LEDGER.parquet")
    predictions = _wide_predictions(input_dir)
    base = cohort.merge(predictions, on=["decision_vintage_id", "cohort_id", "target_date", "official_print_group_id", "observed_at"], how="left", validate="one_to_one")
    base["next_official_delta_native_tick"] = base["true_delta_tick"].combine_first(base["next_official_delta_native_tick"])
    base = base.drop(columns=["true_delta_tick"])
    identity = pd.read_parquet(input_dir / "AMSTERDAM_EVENT_MARKET_IDENTITY_RECONCILIATION.parquet")
    for name in IDENTITY:
        if name not in base:
            base[name] = pd.Series(pd.NA, index=base.index, dtype="object")
        else:
            base[name] = base[name].astype("object")
    p2 = base.cohort_id.eq("P2_CAPTURED_PIT_OPPORTUNITY")
    base.loc[p2, list(IDENTITY)] = base.loc[p2, ["decision_vintage_id"]].merge(identity[["decision_vintage_id", *IDENTITY]], on="decision_vintage_id", how="left")[list(IDENTITY)].to_numpy()
    replay = pd.read_parquet(input_dir / "EXECUTABLE_REPLAY_ROWS_V2.parquet")
    replay = replay.loc[replay.markout_horizon_seconds_after_official_first_seen.eq(30)].copy()
    base = _exact_market_join(base, replay)
    legacy_loaded = 0
    for path in legacy_paths:
        legacy = _normalise_legacy(path)
        legacy_loaded += len(legacy)
        # Exact identity only; differing denominators remain separate nullable evidence columns.
        extra = [name for name in legacy if name not in {"target_date", *IDENTITY}]
        base = base.merge(legacy[["target_date", *IDENTITY, *extra]], on=["target_date", *IDENTITY], how="left", suffixes=("", "_legacy"))
    base["record_scope"] = "WCIR_NEXT_METAR"
    legacy_rows, legacy_report = _legacy_checkpoint_rows()
    if not legacy_rows.empty:
        base = pd.concat([base, legacy_rows], ignore_index=True, sort=False)
    base, layer_b = _fit_layer_b(base)
    metrics = {cohort_id: {model: _weather_metrics(group, model) for model in MODELS} for cohort_id, group in base.groupby("cohort_id", sort=True)}
    summary = {
        "schema_version": "amsterdam_frozen_e2e_v1",
        "denominator_scope": "sealed WCIR P0/P1/P2 next-routine-EHAM-METAR checkpoints; exact market evidence is left-joined only for P2 identities",
        "signal_funnel": {"raw_cohort_rows": int(len(cohort)), "model_scored_rows": int(base["b2_p_delta_+0"].notna().sum()), "target_dates": int(cohort.target_date.nunique())},
        "evidence_funnel": {
            "market_join_reasons": base.market_join_reason.value_counts(dropna=False).to_dict(),
            "exact_identity_rows": int(base.market_join_reason.eq("EXACT_IDENTITY_MATCHED").sum()),
            "entry_eligible_rows": int(base.get("tier_a_plus_b_entry_eligible", pd.Series(False, index=base.index)).eq(True).sum()),
            "markout_30s_rows": int(base.get("markout_eligible", pd.Series(False, index=base.index)).eq(True).sum()),
            "legacy_rows_loaded": int(legacy_loaded),
            "legacy_checkpoint_rows_loaded": int(len(legacy_rows)),
        },
        "cohorts": metrics,
        "legacy_final_settlement_comparators": legacy_report,
        "layer_b": layer_b,
        "orders_fills_notional": [0, 0, 0],
    }
    return base, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--legacy-model-parquet", type=Path, action="append", default=[])
    args = parser.parse_args()
    ledger, summary = build_common_ledger(args.input_dir, args.legacy_model_parquet)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ledger.to_parquet(args.output_dir / "AMSTERDAM_FROZEN_COMMON_LEDGER_V1.parquet", index=False)
    (args.output_dir / "AMSTERDAM_FROZEN_COMMON_SUMMARY_V1.json").write_text(json.dumps(summary, indent=2, default=_jsonable) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "rows": len(ledger), "layer_b": summary["layer_b"], "cohorts": summary["cohorts"]}, default=_jsonable))


if __name__ == "__main__":
    main()
