from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/analysis/proposal_reward/research_weather_proposal_window_v1.py"
SPEC = importlib.util.spec_from_file_location("weather_proposal_window_v1", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_temperature_ancillary_parsing() -> None:
    text = (
        "q: title: Will the highest temperature in Madrid be 35°C on August 11?, "
        "description: This market will resolve to the temperature range recorded on 11 Aug '26.\n\n"
        "This market can not resolve until the first data point for the following date has been published "
        "on the resolution source. market_id: 3435824 res_data: p1: 0, p2: 1, p3: 0.5."
    )
    question = MODULE.extract_question(text)
    description = MODULE.extract_description(text)
    assert question == "Will the highest temperature in Madrid be 35°C on August 11?"
    assert MODULE.parse_ancillary_temperature_identity(question, description) == (
        "highest",
        "Madrid",
        date(2026, 8, 11),
    )
    assert MODULE.rule_class(description) == "first_next_date_point"
    assert MODULE.MARKET_ID_RE.search(text).group(1) == "3435824"


def test_current_contract_amounts_use_six_decimal_usdc() -> None:
    assert MODULE.as_usdc("600000") == 0.6
    assert MODULE.as_usdc("250000000") == 250.0


def test_hong_kong_rule_is_separate_from_first_next_point() -> None:
    description = "This market can not resolve until data for this date has been published."
    assert MODULE.rule_class(description) == "date_data_published"
