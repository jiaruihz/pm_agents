#!/usr/bin/env python3
"""Offline verifier for the frozen Amsterdam rev2 evidence directory.

This verifier recomputes published score summaries and market-replay aggregates
from packaged Parquet/JSON only.  It does not read JRS or any runtime root and
does not retrain models.
"""

from __future__ import annotations

import argparse
import json
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SUPPORT = np.arange(-10, 11, dtype=int)
PROBABILITY_COLUMNS = [f"p_delta_{value:+d}" for value in SUPPORT]
MODEL_IDS = {
    "B2": "B2_latest_fast_rounded",
    "M1": "ams_next_print_m1_ordinal_logit",
    "M2": "ams_next_print_m2_monotonic_additive",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metric_rows(frame: pd.DataFrame, pmf: np.ndarray) -> pd.DataFrame:
    labels = frame["next_official_delta_native_tick"].to_numpy(int)
    indices = labels - int(SUPPORT[0])
    cdf = np.cumsum(pmf, axis=1)
    observed = (SUPPORT[None, :] >= labels[:, None]).astype(float)
    return pd.DataFrame({
        "target_date": frame["target_date"].to_numpy(),
        "official_print_group_id": frame["official_print_group_id"].to_numpy(),
        "rps": np.sum((cdf[:, :-1] - observed[:, :-1]) ** 2, axis=1) / (len(SUPPORT) - 1),
        "logloss": -np.log(np.clip(pmf[np.arange(len(frame)), indices], 1e-7, 1.0)),
        "brier_up": (pmf[:, SUPPORT > 0].sum(axis=1) - (labels > 0)) ** 2,
        "brier_down": (pmf[:, SUPPORT < 0].sum(axis=1) - (labels < 0)) ** 2,
        "brier_unchanged": (pmf[:, SUPPORT == 0].sum(axis=1) - (labels == 0)) ** 2,
    })


def _score(frame: pd.DataFrame, pmf: np.ndarray) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = _metric_rows(frame, pmf)
    groups = rows.groupby(["target_date", "official_print_group_id"], as_index=False).mean(numeric_only=True)
    daily = groups.groupby("target_date").mean(numeric_only=True)
    summary = {column: float(daily[column].mean()) for column in daily.columns}
    summary.update({
        "raw_rows": int(len(frame)), "official_print_groups": int(frame["official_print_group_id"].nunique()),
        "target_dates": int(frame["target_date"].nunique()),
        "weighting": "target_date_equal_then_official_print_group_equal_then_row_equal",
    })
    return rows, summary


def _bootstrap(candidate: pd.DataFrame, baseline: pd.DataFrame, reps: int = 2000) -> dict[str, Any]:
    left = candidate.groupby(["target_date", "official_print_group_id"])["rps"].mean().groupby("target_date").mean()
    right = baseline.groupby(["target_date", "official_print_group_id"])["rps"].mean().groupby("target_date").mean()
    dates = sorted(set(left.index) & set(right.index))
    delta = np.array([left[date] - right[date] for date in dates], dtype=float)
    rng = np.random.default_rng(20260829)
    samples = np.array([rng.choice(delta, len(delta), replace=True).mean() for _ in range(reps)])
    return {"candidate_minus_baseline": float(delta.mean()), "ci95": [float(np.quantile(samples, .025)), float(np.quantile(samples, .975))], "target_dates": len(dates), "bootstrap_unit": "target_date", "reps": reps}


def _evaluate_prediction_bundle(frame: pd.DataFrame, predictions: pd.DataFrame) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    summaries, rows = {}, {}
    for short, model_id in MODEL_IDS.items():
        selected = predictions.loc[predictions["model_id"].eq(model_id)].set_index("decision_vintage_id").loc[frame["decision_vintage_id"]].reset_index()
        rows[short], summaries[short] = _score(frame, selected[PROBABILITY_COLUMNS].to_numpy(float))
    summaries["M1_minus_B2"] = _bootstrap(rows["M1"], rows["B2"])
    summaries["M2_minus_B2"] = _bootstrap(rows["M2"], rows["B2"])
    return summaries, rows


def verify_manifest(root: Path) -> None:
    manifest = json.loads((root / "EVIDENCE_MANIFEST.json").read_text(encoding="utf-8"))
    expected = {row["path"]: row for row in manifest["entries"]}
    actual = {
        str(path.relative_to(root)): path for path in root.rglob("*")
        if path.is_file() and path.name != "EVIDENCE_MANIFEST.json" and not path.name.endswith((".zip", ".zip.sha256"))
    }
    if set(expected) != set(actual):
        raise RuntimeError(f"entry-set drift missing={sorted(set(expected)-set(actual))} extra={sorted(set(actual)-set(expected))}")
    for relative, path in actual.items():
        row = expected[relative]
        if path.stat().st_size != row["size_bytes"] or sha256(path) != row["sha256"]:
            raise RuntimeError(f"hash drift: {relative}")


def _frame_from_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    first = predictions.loc[predictions["model_id"].eq(MODEL_IDS["B2"])].copy()
    if first["decision_vintage_id"].duplicated().any():
        raise RuntimeError("duplicate B2 decision rows")
    return first[[
        "decision_vintage_id", "official_print_group_id", "target_date", "observed_at",
        "next_official_delta_native_tick", "last_official_native_value", "official_running_max",
    ]]


def _assert_close(actual: Any, expected: Any, path: str = "root") -> None:
    if isinstance(expected, dict):
        for key, value in expected.items():
            if key not in actual:
                raise RuntimeError(f"missing result key {path}.{key}")
            _assert_close(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise RuntimeError(f"length drift {path}")
        for index, value in enumerate(expected):
            _assert_close(actual[index], value, f"{path}[{index}]")
    elif isinstance(expected, float):
        if not np.isclose(float(actual), expected, rtol=0, atol=1e-12):
            raise RuntimeError(f"numeric drift {path}: {actual} != {expected}")
    elif actual != expected:
        raise RuntimeError(f"value drift {path}: {actual!r} != {expected!r}")


def reproduce(root: Path) -> dict[str, Any]:
    verify_manifest(root)
    published = json.loads((root / "MODEL_COMPARISON_REV2.json").read_text())
    oof = pd.read_parquet(root / "HISTORICAL_EXPANDING_OOF_PREDICTIONS.parquet")
    outer = pd.read_parquet(root / "HISTORICAL_OUTER_PREDICTIONS.parquet")
    captured = pd.read_parquet(root / "CAPTURED_PIT_PREDICTIONS.parquet")
    oof_frame = _frame_from_predictions(oof)
    full, _ = _evaluate_prediction_bundle(oof_frame, oof)
    matched_ids = set(oof.loc[oof["opportunity_matched"].astype(bool), "decision_vintage_id"])
    matched_frame = oof_frame.loc[oof_frame["decision_vintage_id"].isin(matched_ids)]
    matched_predictions = oof.loc[oof["decision_vintage_id"].isin(matched_ids)]
    matched, _ = _evaluate_prediction_bundle(matched_frame, matched_predictions)
    outer_summary, _ = _evaluate_prediction_bundle(_frame_from_predictions(outer), outer)
    captured_summary, _ = _evaluate_prediction_bundle(_frame_from_predictions(captured), captured)
    recomputed = {
        "historical_expanding_oof_all_checkpoints": full,
        "historical_expanding_oof_opportunity_matched": matched,
        "historical_untouched_outer_20_dates": outer_summary,
        "captured_pit_87_rows": captured_summary,
    }
    _assert_close(recomputed, published["denominators"], "denominators")

    panel = pd.read_parquet(root / "FROZEN_CAPTURED_MODEL_PANEL.parquet")
    path = pd.read_parquet(root / "FROZEN_CAPTURED_KNMI_PATH.parquet")
    parity = json.loads((root / "FEATURE_PARITY_AUDIT.json").read_text())
    if len(panel) != 87 or panel["event_id"].nunique() != 87 or len(path) != parity["frozen_path_rows"]:
        raise RuntimeError("captured feature denominator drift")
    if not np.isclose(panel["latest_fast_native_value"], panel["source_temp_c"]).all():
        raise RuntimeError("captured latest source value drift")

    reconciliation = pd.read_parquet(root / "MARKET_ARCHIVE_RECONCILIATION_ROWS.parquet")
    replay = pd.read_parquet(root / "DIRECT_BOOK_REPRICING_REPLAY_ROWS.parquet")
    market = json.loads((root / "MARKET_ARCHIVE_RECONCILIATION.json").read_text())
    market_recomputed = {
        "events": len(reconciliation),
        "reason_histogram": dict(Counter(reconciliation["primary_reason"])),
        "rest_exact_identity_t0": int(reconciliation["rest_exact_identity"].sum()),
        "direct_entry_1share": int(reconciliation["entry_1share"].sum()),
        "direct_entry_5share": int(reconciliation["entry_5share"].sum()),
        "strict_ws_exact_token_coverage_events": int(reconciliation["strict_ws_valid_rows"].gt(0).sum()),
    }
    _assert_close(market_recomputed, {key: market[key] for key in market_recomputed}, "market")
    expected_pairs = len(reconciliation) * 2 * 5
    if len(replay) != expected_pairs or replay[["event_id", "shares", "horizon_seconds_from_source_detect"]].duplicated().any():
        raise RuntimeError("market replay row-set drift")
    if replay[["orders", "fills", "notional"]].to_numpy().sum() != 0:
        raise RuntimeError("zero-notional drift")
    return {
        "status": "PASS", "manifest_entries": len(json.loads((root / "EVIDENCE_MANIFEST.json").read_text())["entries"]),
        "oof_rows_per_model": int(len(oof) / 3), "captured_rows_per_model": 87,
        "captured_path_rows": len(path), "market_events": len(reconciliation), "replay_rows": len(replay),
        "orders": 0, "fills": 0, "notional": 0,
        "scope": "offline frozen-evidence reproduction; model retraining intentionally out of scope",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    result = reproduce(args.root.resolve())
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
