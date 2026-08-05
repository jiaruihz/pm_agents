import json

import numpy as np
import pandas as pd
import pytest

from scripts.analysis.reheat_risk import late_window_shared
from scripts.analysis.reheat_risk import (
    research_late_window_residual_capture_feature_layer_v2 as feature_layer_v2,
)
from scripts.analysis.reheat_risk import research_late_window_residual_capture_v1 as legacy_v1
from scripts.ops import check_weather_docs


def test_late_window_variants_use_one_shared_helper_implementation(tmp_path):
    assert legacy_v1.best_level is late_window_shared.best_level
    assert feature_layer_v2.best_level is late_window_shared.best_level
    assert legacy_v1.block_ci is late_window_shared.block_ci
    assert feature_layer_v2.block_ci is late_window_shared.block_ci

    observation = tmp_path / "latest.json"
    observation.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-08-05T00:00:00Z",
                "records": [
                    {
                        "city": "Chengdu",
                        "status": "ok",
                        "target_date": "2026-08-05",
                        "running_max_c": 33.0,
                        "decline_c": 1.0,
                        "source_chain": ["METAR", "WU"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    row = late_window_shared.load_today_observation(observation).iloc[0]
    assert row["running_max_f"] == 91.4
    assert row["source_chain"] == "METAR,WU"

    basket = pd.DataFrame(
        [
            {
                "settled": True,
                "execution_mode": "taker",
                "city": "Chengdu",
                "target_date": "2026-08-05",
                "decision_hour_local": 17,
                "snapshot_ts_utc": "2026-08-05T09:00:00Z",
                "leg": "current_yes",
                "cost_per_share": 0.4,
                "pnl_per_share": 0.6,
                "final_winning_bracket": "33",
            }
        ]
    )
    result = late_window_shared.basket_rows(basket).iloc[0]
    assert result["roi"] == pytest.approx(1.5)
    assert late_window_shared.block_ci(np.array([1.0])) == (None, None)


def test_research_debt_checker_rejects_new_entrypoint_and_duplicate_growth(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    script_root = repo / "scripts/analysis/family"
    script_root.mkdir(parents=True)
    body = """def repeated_function(values):
    total = 0
    for value in values:
        if value:
            total += value
        else:
            total -= 1
    return total
"""
    first = script_root / "research_city_a_v1.py"
    second = script_root / "research_city_b_v1.py"
    first.write_text(body, encoding="utf-8")
    second.write_text(body, encoding="utf-8")
    monkeypatch.setattr(check_weather_docs, "ROOT", repo)
    monkeypatch.setattr(
        check_weather_docs,
        "hygiene_config",
        lambda: {
            "max_research_experiment_entrypoints": 1,
            "max_repeated_research_function_bodies": 0,
        },
    )

    errors: list[str] = []
    check_weather_docs.check_research_script_debt(
        errors,
        {
            "scripts/analysis/family/research_city_a_v1.py",
            "scripts/analysis/family/research_city_b_v1.py",
        },
    )

    assert any("entrypoints grew" in error for error in errors)
    assert any("function bodies grew" in error for error in errors)
