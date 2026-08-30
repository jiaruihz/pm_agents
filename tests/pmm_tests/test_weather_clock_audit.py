from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ops.audit_weather_clock_contracts import _audit_rows, audit_static


def test_clock_audit_separates_exchange_second_precision_from_real_reversal():
    result = _audit_rows(
        [
            {
                "fill_id": "precision-only",
                "order_ts_utc": "2026-07-09T13:46:08.750690Z",
                "fill_ts_utc": "2026-07-09T13:46:08Z",
            },
            {
                "fill_id": "real-reversal",
                "order_ts_utc": "2026-07-09T13:46:10Z",
                "fill_ts_utc": "2026-07-09T13:46:08Z",
            },
        ],
        table="fact_trades",
        identity_field="fill_id",
        clock_fields=("order_ts_utc", "fill_ts_utc"),
        ordered_pairs=(("order_ts_utc", "fill_ts_utc"),),
        precision_tolerance_seconds={("order_ts_utc", "fill_ts_utc"): 1.0},
    )

    assert result["precision_only_reversal_rows"] == 1
    assert result["causal_reversal_rows"] == 1
    assert result["precision_only_reversals"][0]["identity"] == "precision-only"
    assert result["reversals"][0]["identity"] == "real-reversal"


def test_clock_affecting_weather_boundaries_use_shared_contract():
    root = Path(__file__).resolve().parents[2]
    result = audit_static(root)
    assert result["modules"] >= 48
    assert result["violations"] == []
    assert result["status"] == "pass"


@pytest.mark.parametrize(
    "relative",
    [
        "scripts/etl/build_weather_fact_trades.py",
        "scripts/etl/backfill_signal_candidate_decision_windows.py",
        "scripts/analysis/reheat_risk/build_forecast_peak_clock_backfill_dataset_v1.py",
        "scripts/analysis/reheat_risk/research_theta_current_yes_forecast_peak_clock_backfill_v3.py",
    ],
)
def test_clock_cli_imports_work_outside_checkout(tmp_path, relative):
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, str(root / relative), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
