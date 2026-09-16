from __future__ import annotations

import json
import gzip
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.forecast_quality.wcir_unified_amsterdam_pilot import (
    DEFAULT_OUTPUT,
    SUPPORT,
    OrdinalThresholdModel,
    ensure_safe_output,
    executable_replay,
    make_folds,
    verify_evidence_manifest,
)


def test_ordinal_threshold_model_returns_complete_distribution() -> None:
    rows = []
    for target_date, offset in (("2026-01-01", 0.0), ("2026-01-02", 0.2), ("2026-01-03", -0.2)):
        for value in range(30):
            rows.append({
                "target_date": target_date,
                "feature": value / 10 + offset,
                "next_official_delta_native_tick": int(np.clip(round(offset + value / 15 - 1), -2, 2)),
            })
    frame = pd.DataFrame(rows)
    model = OrdinalThresholdModel(["feature"], SUPPORT).fit(frame, np.ones(len(frame)))
    probability = model.predict_pmf(frame)
    assert probability.shape == (len(frame), len(SUPPORT))
    assert np.all(probability >= 0)
    assert np.allclose(probability.sum(axis=1), 1.0)


def test_generated_pilot_is_zero_notional_and_fail_closed() -> None:
    audit = json.loads((DEFAULT_OUTPUT / "ZERO_NOTIONAL_AUDIT.json").read_text())
    comparison = json.loads((DEFAULT_OUTPUT / "MODEL_COMPARISON.json").read_text())
    shadow = json.loads((DEFAULT_OUTPUT / "FROZEN_SHADOW_CONFIG.json").read_text())
    assert audit["orders"] == audit["fills"] == audit["notional"] == 0
    assert comparison["model_verdict"] == "FAIL"
    assert comparison["captured_pit_validation"]["M1_minus_B2"]["ci95"][0] > 0
    assert shadow["enabled_for_deployment"] is False
    assert shadow["negative_control_only"] is True
    assert shadow["notional_limit"] == 0


def test_prediction_parquets_preserve_probability_mass() -> None:
    for name in ("OOF_PREDICTIONS.parquet", "HISTORICAL_OUTER_PREDICTIONS.parquet"):
        frame = pd.read_parquet(DEFAULT_OUTPUT / name)
        probability_columns = [f"p_delta_{value:+d}" for value in SUPPORT]
        assert frame["target_date"].notna().all()
        assert np.allclose(frame[probability_columns].sum(axis=1), 1.0, atol=1e-8)


def test_evidence_manifest_rejects_extra_missing_and_hash_drift(tmp_path: Path) -> None:
    source = DEFAULT_OUTPUT
    destination = tmp_path / "evidence"
    destination.mkdir()
    manifest = json.loads((source / "EVIDENCE_MANIFEST.json").read_text())
    shutil.copy2(source / "EVIDENCE_MANIFEST.json", destination / "EVIDENCE_MANIFEST.json")
    for row in manifest["entries"]:
        target = destination / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / row["path"], target)
    verify_evidence_manifest(destination)

    extra = destination / "extra.txt"
    extra.write_text("extra")
    with pytest.raises(RuntimeError, match="entry-set drift"):
        verify_evidence_manifest(destination)
    extra.unlink()

    victim = destination / manifest["entries"][0]["path"]
    original = victim.read_bytes()
    victim.write_bytes(original + b"drift")
    with pytest.raises(RuntimeError, match="hash drift"):
        verify_evidence_manifest(destination)
    victim.write_bytes(original)

    victim.unlink()
    with pytest.raises(RuntimeError, match="entry-set drift"):
        verify_evidence_manifest(destination)


def test_small_sample_folds_and_runtime_output_fail_closed() -> None:
    with pytest.raises(ValueError, match="more than 365"):
        make_folds([f"2026-01-{day:02d}" for day in range(1, 21)])
    with pytest.raises(ValueError, match="must stay under"):
        ensure_safe_output(Path("runtime/wcir_pilot"))


def test_replay_keeps_pnl_null_for_missing_entry_or_exit(tmp_path: Path) -> None:
    captured = pd.DataFrame([
        {
            "event_id": "missing-entry", "target_date": "2026-08-26",
            "official_print_group_id": "g1", "official_running_max": 20,
            "last_official_native_value": 19, "source_detect_ts_utc": "2026-08-26T10:00:00Z",
            "ts_utc": "2026-08-26T10:00:01Z", "official_first_seen_at_utc": "2026-08-26T10:05:00Z",
            "token_id": "t1", "weather_label_eligible": True,
        },
        {
            "event_id": "missing-exit", "target_date": "2026-08-26",
            "official_print_group_id": "g2", "official_running_max": 20,
            "last_official_native_value": 19, "source_detect_ts_utc": "2026-08-26T11:00:00Z",
            "ts_utc": "2026-08-26T11:00:01Z", "official_first_seen_at_utc": "2026-08-26T11:05:00Z",
            "token_id": "t2", "weather_label_eligible": True,
        },
    ])
    predictions = pd.DataFrame([
        {"decision_vintage_id": "missing-entry", "p_new_running_max": 0.9},
        {"decision_vintage_id": "missing-exit", "p_new_running_max": 0.9},
    ])
    aligned = [
        {"event_id": "missing-entry", "checkpoint": "source_t0", "book_valid": False, "book_truth_id": None},
        {"event_id": "missing-exit", "checkpoint": "source_t0", "book_valid": True, "book_truth_id": "entry", "book_snapshot_id": "entry-snapshot"},
    ]
    truths = [{
        "truth_id": "entry",
        "sweeps": [
            {"side": "buy", "shares": 1.0, "fully_executable": True, "effective_value_usd": 0.50},
            {"side": "buy", "shares": 5.0, "fully_executable": True, "effective_value_usd": 2.50},
        ],
    }]
    aligned_path = tmp_path / "aligned.jsonl.gz"
    truth_path = tmp_path / "truth.jsonl.gz"
    with gzip.open(aligned_path, "wt") as handle:
        for row in aligned:
            handle.write(json.dumps(row) + "\n")
    with gzip.open(truth_path, "wt") as handle:
        for row in truths:
            handle.write(json.dumps(row) + "\n")
    rows, _, _ = executable_replay(captured, predictions, aligned_path, truth_path)
    assert rows.loc[rows["event_id"].eq("missing-entry"), "entry_eligible"].eq(False).all()
    assert rows.loc[rows["event_id"].eq("missing-entry"), "counterfactual_net_markout_usd"].isna().all()
    missing_exit = rows.loc[rows["event_id"].eq("missing-exit")]
    assert missing_exit["selector_pass"].all()
    assert missing_exit["markout_eligible"].eq(False).all()
    assert missing_exit["counterfactual_net_markout_usd"].isna().all()
