from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path

from weather_data_feed.observation_sources import FetchSettings, ObservationSourceRequest
from weather_data_feed.observation_sources import fetchers


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/ops/weather_proposal_reward_shadow_v1.py"
SPEC = importlib.util.spec_from_file_location("weather_proposal_reward_shadow_v1", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


DESCRIPTION = """This market will resolve based on the highest temperature recorded in the 'Daily Observations' table on Weather Underground.
This market will resolve to the temperature range recorded in degrees Celsius.
The resolution source is https://www.wunderground.com/history/daily/es/madrid/LEMD.
This market can not resolve until the first data point for the following date has been published on the resolution source.
"""


def test_cli_defaults_follow_shared_production_contract() -> None:
    args = MODULE.build_parser().parse_args([])

    assert args.output_dir == (
        "/Volumes/jrs/weather_data_feed_service_runtime/output/"
        "proposal_reward_shadow_v1"
    )
    assert args.market_proxy == "http://127.0.0.1:7896"


def test_rule_and_market_question_parsing() -> None:
    assert MODULE.parse_wu_contract(DESCRIPTION) == ("LEMD", "ES", "C")
    assert MODULE.parse_market_question(
        "Will the highest temperature in Madrid be 33°C or below on August 12?"
    ) == {"threshold": 33, "unit": "C", "relation": "or_below"}
    assert MODULE.outcome_for_market(
        "Will the highest temperature in Madrid be 35°C on August 12?",
        final_value=36,
        native_unit="C",
    )["proposed_price"] == 0.0


def test_discovery_coalesces_high_and_low_for_one_station() -> None:
    config = {
        "city_windows": {"Madrid": {"start_minute": 3, "end_minute": 12, "poll_seconds": 2}}
    }
    markets = [
        {
            "id": "1",
            "question": "Will the highest temperature in Madrid be 35°C on August 12?",
            "description": DESCRIPTION,
            "questionID": "q1",
            "negRiskRequestID": "r1",
            "umaBond": "250",
            "umaReward": "0.6",
            "customLiveness": 900,
        }
    ]
    events = [
        {
            "id": "e-high",
            "slug": "highest-temperature-in-madrid-on-august-12-2026",
            "title": "Highest temperature in Madrid on August 12?",
            "active": True,
            "closed": False,
            "markets": markets,
        },
        {
            "id": "e-low",
            "slug": "lowest-temperature-in-madrid-on-august-12-2026",
            "title": "Lowest temperature in Madrid on August 12?",
            "active": True,
            "closed": False,
            "markets": [
                {
                    **markets[0],
                    "id": "2",
                    "question": "Will the lowest temperature in Madrid be 22°C on August 12?",
                }
            ],
        },
    ]
    watches = MODULE.build_watches(
        events,
        config=config,
        discovered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    assert len(watches) == 1
    watch = next(iter(watches.values()))
    assert {event["kind"] for event in watch["events"]} == {"highest", "lowest"}
    assert watch["poll_seconds"] == 2


def test_plus_two_no_is_selected_for_single_500_dollar_shadow() -> None:
    candidates = [
        {
            "candidate_id": f"c{threshold}",
            "relation": "exact",
            "threshold": threshold,
            "proposed_price": 1.0 if threshold == 36 else 0.0,
        }
        for threshold in range(33, 40)
    ]
    assert MODULE.choose_single_request(candidates, final_value=36) == "c38"


def test_weather_com_history_preserves_requested_native_celsius(monkeypatch) -> None:
    captured: dict = {}
    observation_ts = int(datetime(2026, 8, 12, 12, tzinfo=timezone.utc).timestamp())

    class Response:
        def json(self):
            return {"observations": [{"valid_time_gmt": observation_ts, "temp": 36}]}

    def fake_get(url, *, params, settings, headers):
        captured.update({"url": url, "params": params, "headers": headers})
        return Response()

    monkeypatch.setattr(fetchers, "_http_get", fake_get)
    result = fetchers.fetch_weather_com_history_hourly(
        ObservationSourceRequest(
            city="Madrid",
            station_or_feed="LEMD",
            target_date="2026-08-12",
            timezone_name="Europe/Madrid",
            source_key="weather_com_history_hourly",
            metadata={"native_unit": "C", "weather_com_country": "ES"},
        ),
        FetchSettings(timeout_sec=1),
    )
    assert captured["params"]["units"] == "m"
    assert "LEMD:9:ES" in captured["url"]
    assert result.records[0].temp_c == 36
    assert result.records[0].metadata["native_temp"] == 36
    assert result.records[0].metadata["native_round"] == 36
    assert result.metadata["native_unit"] == "C"


def test_shared_fetcher_can_reuse_a_persistent_http_client() -> None:
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    response = fetchers._http_get(
        "https://example.test/data",
        params={"x": "1"},
        settings=FetchSettings(timeout_sec=2, http_client=Client()),
        headers={"Accept": "application/json"},
    )
    assert isinstance(response, Response)
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 2
    assert calls[0][1]["headers"]["Accept"] == "application/json"


def test_lineage_stops_at_zero_notional_proposal_intent() -> None:
    config = {
        "strategy_family": "polymarket.weather.proposal_reward",
        "strategy_instance": "polymarket_weather_proposal_reward_shadow_v1",
        "expected_contract": {
            "bond_usdc": 250,
            "final_fee_usdc": 250,
            "total_deposit_usdc": 500,
            "reward_usdc": 0.6,
            "custom_liveness_seconds": 900,
        },
    }
    watch = {
        "watch_id": "watch",
        "city": "Madrid",
        "station": "LEMD",
        "target_date": "2026-08-12",
        "native_unit": "C",
        "first_empty_poll_at_utc": "2026-08-12T22:04:00+00:00",
        "events": [
            {
                "event_id": "event",
                "event_slug": "slug",
                "kind": "highest",
                "description_sha256": "rules",
                "markets": [
                    {
                        "market_id": str(value),
                        "question": f"Will the highest temperature in Madrid be {value}°C on August 12?",
                        "question_id": f"q{value}",
                        "neg_risk_request_id": f"r{value}",
                        "uma_bond_usdc": 250.0,
                        "uma_reward_usdc": 0.6,
                        "custom_liveness_seconds": 900,
                    }
                    for value in range(34, 40)
                ],
            }
        ],
    }
    result = {
        "source_first_seen_at_utc": "2026-08-12T22:06:00+00:00",
        "source_observation_utc": "2026-08-12T22:00:00+00:00",
        "hypothetical_ready_at_utc": "2026-08-12T22:06:00.5+00:00",
        "source_payload_hash": "next",
        "target_payload_hash": "target",
        "target_record_count": 24,
        "final_values": {"highest": 36, "lowest": 20},
    }
    rows = MODULE.build_lineage_rows(watch, result, config=config, build_id="sha")
    intent = rows["proposal_intents.jsonl"][0]
    assert intent["market_id"] == "38"
    assert intent["submission_enabled"] is False
    assert intent["private_key_access"] is False
    assert intent["actual_deposit_usdc"] == 0.0
    assert "trade_intents.jsonl" not in rows


def test_runner_has_no_order_or_signing_client_import() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "py_clob_client" not in source
    assert "web3" not in source
    assert "private_key" not in source.lower().replace('"private_key_access"', "")
