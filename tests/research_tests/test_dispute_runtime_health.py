from scripts.ops.polymarket_dispute_forward import runtime_health_status


def test_runtime_health_preserves_canonical_error() -> None:
    assert runtime_health_status(
        {"status": "error", "errors": ["missing_receipt"], "warnings": []},
        {"demands": 1, "successful_demands": 0},
    ) == "error"


def test_runtime_health_reports_warming_capture_coverage() -> None:
    assert runtime_health_status(
        {"status": "healthy", "warnings": []},
        {"demands": 1, "successful_demands": 0},
    ) == "warming"


def test_runtime_health_is_ok_only_when_checks_are_clean() -> None:
    assert runtime_health_status(
        {"status": "healthy", "warnings": []},
        {"demands": 1, "successful_demands": 1},
    ) == "ok"
