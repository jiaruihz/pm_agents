import json

from scripts.analysis.forecast_quality.research_high_frequency_strategy_eligibility_v2 import (
    load_wu_daily_max,
)
from scripts.analysis.forecast_quality.research_runway_metar_wu_alignment_v1 import (
    read_json_or_jsonl,
)


def test_runway_reader_uses_dated_shards_only(tmp_path) -> None:
    dated = tmp_path / "2026-07-28"
    dated.mkdir()
    (dated / "runway_observations.jsonl").write_text(
        json.dumps({"station": "RKPK"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "runway_observations.jsonl").write_text(
        json.dumps({"station": "ROOT_DUPLICATE"}) + "\n", encoding="utf-8"
    )

    assert read_json_or_jsonl(tmp_path) == [{"station": "RKPK"}]


def test_wu_reader_uses_dated_shards_only(tmp_path) -> None:
    dated = tmp_path / "2026-07-28"
    dated.mkdir()
    row = {"city": "Amsterdam", "target_date": "2026-07-28", "temp_c": 20}
    (dated / "wu_history_latency.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    (tmp_path / "wu_history_latency.jsonl").write_text(
        json.dumps({**row, "temp_c": 99}) + "\n", encoding="utf-8"
    )

    assert load_wu_daily_max(
        tmp_path,
        {"Amsterdam": {"unit": "C"}},
    ) == {("Amsterdam", "2026-07-28"): 20}
