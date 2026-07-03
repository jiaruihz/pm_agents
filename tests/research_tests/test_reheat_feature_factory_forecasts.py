import importlib.util
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/analysis/reheat_risk/research_reheat_feature_factory_v1.py"
spec = importlib.util.spec_from_file_location("research_reheat_feature_factory_v1", MODULE_PATH)
factory = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = factory
spec.loader.exec_module(factory)


def _rows(decision_ts: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "city": "NYC",
                "target_date": "2026-06-21",
                "bracket": "90-91",
                "outcome": "yes",
                "decision_snapshot_ts_utc": decision_ts,
            }
        ]
    )


def _forecasts(*decision_ts_values: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "city": "NYC",
                "target_date": "2026-06-21",
                "bracket": "90-91",
                "outcome": "yes",
                "decision_snapshot_ts_utc": ts,
                "forecast_source": f"source_{idx}",
                "forecast_max_f": 90.0 + idx,
                "forecast_peak_hour_local": 12 + idx,
            }
            for idx, ts in enumerate(decision_ts_values)
        ]
    )


def test_add_forecasts_does_not_backfill_from_future_snapshot() -> None:
    out = factory.add_forecasts(
        _rows("2026-06-21T10:00:00Z"),
        _forecasts("2026-06-21T11:00:00Z"),
    )

    assert out.loc[0, "forecast_join_status"] == "matched_future"
    assert pd.isna(out.loc[0, "forecast_source"])
    assert pd.isna(out.loc[0, "forecast_max_f"])
    assert pd.isna(out.loc[0, "forecast_peak_hour_local"])


def test_add_forecasts_keeps_latest_prior_snapshot_only() -> None:
    out = factory.add_forecasts(
        _rows("2026-06-21T10:00:00Z"),
        _forecasts("2026-06-21T09:00:00Z", "2026-06-21T11:00:00Z"),
    )

    assert out.loc[0, "forecast_join_status"] == "matched_asof"
    assert out.loc[0, "forecast_source"] == "source_0"
    assert out.loc[0, "forecast_max_f"] == 90.0
    assert out.loc[0, "forecast_peak_hour_local"] == 12
