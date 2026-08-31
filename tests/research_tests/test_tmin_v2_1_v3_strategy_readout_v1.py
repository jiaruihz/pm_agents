from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.tmin.tmin_v2_1_v3_strategy_readout_v1 import (
    HAZARD_FEATURES,
    ROOT,
    Fit,
    _build_weather_panel,
    _fit_adaptor,
    _load_weather_paths,
    _predict_survival,
    _selector_replay,
    build,
)


BASE = ROOT / "reviews/tmin_model_layer_v2_1_v3_research_v1"
FORENSICS = ROOT / "reviews/tmin_no_further_model_forensics_v1"
SEOUL = ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw/RKSI_apr14_aug21.csv"
TOKYO = ROOT / "runtime/research/tmin_cross_metar_basis_replay_v1/raw/RJTT_apr14_aug21.csv"


def test_weather_path_uses_utc_plus_nine_local_target_date() -> None:
    paths = _load_weather_paths({"Seoul": SEOUL, "Tokyo": TOKYO})
    row = paths[
        paths["city"].eq("Seoul")
        & paths["observation_event_time_utc"].eq(pd.Timestamp("2026-04-14T15:00:00Z"))
    ].iloc[0]
    assert row["target_date"] == "2026-04-15"


def test_weather_panel_gate_counts_are_independent_city_days() -> None:
    paths = _load_weather_paths({"Seoul": SEOUL, "Tokyo": TOKYO})
    panel, hazard = _build_weather_panel(paths)
    assert len(panel) == 1536
    assert panel[["city", "target_date"]].drop_duplicates().shape[0] == 256
    assert (
        panel.loc[panel["label"].eq(0), ["city", "target_date"]]
        .drop_duplicates()
        .shape[0]
        == 51
    )
    assert hazard["hazard_label"].isin([0, 1]).all()


def test_nonnegative_adaptor_and_frozen_v1_trade_headline() -> None:
    small = pd.DataFrame(
        {
            "target_date": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
            "label": [1, 0, 1, 0],
            "p_market": [0.8, 0.8, 0.8, 0.8],
            "routing_indicator": [1, 1, 1, 1],
            "z": [-1.0, 1.0, -1.0, 1.0],
        }
    )
    coefficients, _ = _fit_adaptor(small, ("z",))
    assert np.all(coefficients >= 0)

    trade = pd.read_csv(FORENSICS / "ROW_LEVEL_TRADE_FUNNEL.csv")
    trade["p_v1"] = trade["p_model"]
    summary, _ = _selector_replay(trade, "p_v1", active_windows_only=False)
    assert summary["settled_trades"] == 19
    assert summary["wins"] == 19
    assert summary["five_share_cost"] == 89.85885325
    assert abs(summary["fee_adjusted_pnl"] - 5.14114675) < 1e-12


def test_hazard_survival_is_hourly_product() -> None:
    hourly_hazard = 0.1
    fit = Fit(
        beta=np.array([np.log(hourly_hazard / (1.0 - hourly_hazard)), 0, 0, 0, 0, 0, 0]),
        median=np.zeros(len(HAZARD_FEATURES)),
        scale=np.ones(len(HAZARD_FEATURES)),
        features=HAZARD_FEATURES,
        iterations=0,
    )
    row = pd.DataFrame(
        {
            "rebound_c": [0.0],
            "minutes_since_running_min": [0.0],
            "temperature_change_60m_c": [0.0],
            "native_rung_safety_margin_c": [0.0],
            "hours_remaining": [3],
        }
    )
    assert _predict_survival(fit, row)[0] == pytest.approx(0.9**3)


def test_full_research_build_is_reproducible(tmp_path: Path) -> None:
    output = tmp_path / "readout"
    result = build(
        argparse.Namespace(
            row_audit=BASE / "ROW_LEVEL_PROBABILITY_AUDIT.parquet",
            trade_funnel=FORENSICS / "ROW_LEVEL_TRADE_FUNNEL.csv",
            iem_seoul=SEOUL,
            iem_tokyo=TOKYO,
            reconciliation=BASE / "SETTLEMENT_SOURCE_RECONCILIATION.parquet",
            forecast_inventory=BASE / "frozen_inputs/forecast_archive_inventory.json",
            output_dir=output,
            allow_existing=False,
        )
    )
    assert result["selector_performance"]["V2_1_PHYSICAL_TRANSFER_ROUTED"]["settled_trades"] == 0
    assert result["selector_performance"]["V3_ONE_HOUR_HAZARD_ROUTED"]["settled_trades"] == 0
    audit = pd.read_parquet(output / "ROW_LEVEL_MODEL_AND_SIGNAL_AUDIT.parquet")
    outside = audit["routing_indicator"].eq(0)
    assert np.array_equal(audit.loc[outside, "p_v2_1"], audit.loc[outside, "p_market"])
    assert np.array_equal(audit.loc[outside, "p_v3"], audit.loc[outside, "p_market"])

    foundation = json.loads((output / "FOUNDATION_SPLIT_RESULTS.json").read_text())
    for truth_variant in (
        "all_path_training",
        "exclude_four_exchange_dispute_days",
    ):
        folds = foundation["truth_sensitivity"][truth_variant]
        for fold_type in ("folds", "v2_1_adaptor_folds", "v3_adaptor_folds"):
            for fold in folds[fold_type]:
                if fold["training_max_target_date"] is not None:
                    assert fold["training_max_target_date"] < fold["test_date"]
    forecast = foundation["forecast_uncertainty"]
    assert forecast["gamma"] == 0.0
    assert forecast["inventory"]["available_at_native_rows"] == 0

    assert not np.array_equal(audit["p_v2_1"], audit["p_v2_1_strict"])
    assert not np.array_equal(audit["p_v3"], audit["p_v3_strict"])
    assert result["selector_performance"]["V2_1_STRICT_DISPUTE_EXCLUDED"]["settled_trades"] == 0
    assert result["selector_performance"]["V3_STRICT_DISPUTE_EXCLUDED"]["settled_trades"] == 0

    corrected = json.loads((output / "CORRECTED_DATE_REPLAY_RESULTS.json").read_text())
    assert corrected["cities"]["Seoul"]["labeled_crosses"] == 186
    assert corrected["cities"]["Seoul"]["no_side_wins"] == 184
    assert corrected["cities"]["Tokyo"]["labeled_crosses"] == 165
    assert corrected["cities"]["Tokyo"]["no_side_wins"] == 165

    manifest = json.loads((output / "EVIDENCE_MANIFEST.json").read_text())
    for item in manifest["inputs"]:
        path = ROOT / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    for item in manifest["outputs"]:
        path = output / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]

    with pytest.raises(FileExistsError):
        build(
            argparse.Namespace(
                row_audit=BASE / "ROW_LEVEL_PROBABILITY_AUDIT.parquet",
                trade_funnel=FORENSICS / "ROW_LEVEL_TRADE_FUNNEL.csv",
                iem_seoul=SEOUL,
                iem_tokyo=TOKYO,
                reconciliation=BASE / "SETTLEMENT_SOURCE_RECONCILIATION.parquet",
                forecast_inventory=BASE / "frozen_inputs/forecast_archive_inventory.json",
                output_dir=output,
                allow_existing=False,
            )
        )
