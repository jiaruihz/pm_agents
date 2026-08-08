from __future__ import annotations

import json

from weather_dashboard.legacy_migration import live_cycle, research_csv, strategy_runtime_orders


class _Report:
    def __init__(self, value: int) -> None:
        self.value = value

    def as_dict(self) -> dict[str, int]:
        return {"value": self.value}


def test_migration_reports_overwrite_one_latest_file_per_family(tmp_path) -> None:
    writers = (
        (strategy_runtime_orders.write_report, "strategy_runtime_order_migration_latest.json"),
        (live_cycle.write_report, "live_cycle_migration_latest.json"),
        (research_csv.write_report, "legacy_research_migration_latest.json"),
    )

    for writer, expected_name in writers:
        first_path = writer([_Report(1)], tmp_path)
        second_path = writer([_Report(2)], tmp_path)

        assert first_path == second_path == tmp_path / expected_name
        assert json.loads(second_path.read_text(encoding="utf-8"))["reports"] == [{"value": 2}]

    assert sorted(path.name for path in tmp_path.glob("*.json")) == sorted(
        expected_name for _writer, expected_name in writers
    )
    assert not list(tmp_path.glob("*.tmp"))
