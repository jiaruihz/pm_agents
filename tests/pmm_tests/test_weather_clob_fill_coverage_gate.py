from scripts.analysis.execution_quality.weather_clob_fill_coverage_gate import summarize_rows
from src.strategies.weather_edge_v1.ids import make_fill_id


def test_fill_gate_detects_synthetic_and_physical_duplicates() -> None:
    execution_id = "a" * 64
    base = {
        "execution_id": execution_id,
        "order_id": "0xorder",
        "filled_shares": 5.0,
        "filled_price": 0.5,
        "filled_at_utc": "2026-07-10T01:00:00Z",
    }
    rows = [
        {**base, "fill_id": make_fill_id(execution_id=execution_id)},
        {**base, "fill_id": "real-fill"},
    ]
    caps = {
        (execution_id, "0xorder"): {
            "max_shares": 10.0,
            "max_cost": 5.0,
            "city": "Paris",
            "target_date": "2026-07-10",
            "bracket": "30",
        }
    }

    summary = summarize_rows(rows, order_caps=caps)

    assert summary["synthetic_fill_rows"] == 1
    assert summary["duplicate_physical_keys"] == 1
