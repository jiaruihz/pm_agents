from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/analysis/dispute_repricing/research_dispute_repricing_v1.py"
SPEC = importlib.util.spec_from_file_location("dispute_repricing_v1", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

from src.strategies.rule_lawyer.dispute import (  # noqa: E402
    classify_dispute_case,
    executable_bid_vwap,
    executable_vwap,
    first_clear_non_p4_rows,
    opportunity_cluster_id,
)


def test_executable_bid_vwap_uses_highest_bids_first() -> None:
    levels = [
        {"side": "bid", "price": 0.80, "size": 10},
        {"side": "bid", "price": 0.75, "size": 20},
        {"side": "ask", "price": 0.90, "size": 100},
    ]
    assert executable_bid_vwap(levels, 25) == pytest.approx(0.77)


def test_ancillary_market_and_title_parsing() -> None:
    text = (
        "q: title: Will the metric be above 10?, description: Rules here. "
        "market_id: 12345 res_data: p1: 0, p2: 1, p3: 0.5."
    )
    assert MODULE.MARKET_ID_RE.search(text).group(1) == "12345"
    assert MODULE.extract_title(text) == "Will the metric be above 10?"


@pytest.mark.parametrize(
    ("settled", "proposed", "expected"),
    [
        (MODULE.TOO_EARLY, 0, "too_early"),
        (MODULE.HALF, 0, "unknown_50_50"),
        (0, 0, "upheld"),
        (MODULE.ONE, 0, "binary_flip"),
        (None, 0, "unsettled"),
    ],
)
def test_settlement_class(settled: int | None, proposed: int, expected: str) -> None:
    assert MODULE.settlement_class(settled, proposed) == expected


def test_reverse_outcome_index_matches_uma_gamma_mapping() -> None:
    # p1=0 is Gamma's second outcome, so its reverse is index 0.
    assert MODULE.reverse_outcome_index(0) == 0
    # p2=1 is Gamma's first outcome, so its reverse is index 1.
    assert MODULE.reverse_outcome_index(1) == 1
    with pytest.raises(ValueError):
        MODULE.reverse_outcome_index(2)


def test_latency_and_address_competition_metrics() -> None:
    rows = [
        {"proposalTimestamp": 100, "disputeTimestamp": 160, "disputer": "0xA"},
        {"proposalTimestamp": 100, "disputeTimestamp": 400, "disputer": "0xa"},
        {"proposalTimestamp": 100, "disputeTimestamp": 1000, "disputer": "0xB"},
    ]
    latency = MODULE.latency_profile(rows)
    concentration = MODULE.address_concentration(rows)
    assert latency["median_minutes"] == 5
    assert latency["share_within_5m"] == pytest.approx(2 / 3)
    assert concentration["unique_disputers"] == 2
    assert concentration["top_1_share"] == pytest.approx(2 / 3)
    assert concentration["same_proposer_disputer_events"] == 0

    signal_rows = [{"proposal_ts": 100, "dispute_ts": 160, "disputer": "0xA"}]
    assert MODULE.latency_profile(signal_rows)["median_minutes"] == 1


@pytest.mark.parametrize(
    ("row", "theme", "mechanism"),
    [
        ({"title": "Map 2 Total Rounds: Over/Under 21.5"}, "sports_esports", "live_contest_or_series_not_final"),
        (
            {
                "title": "Big Bash League: Hobart Hurricanes vs Brisbane Heat (Game 1)",
                "ancillary_text": "Finalized match result from espncricinfo.com",
            },
            "sports_esports",
            "live_contest_or_series_not_final",
        ),
        (
            {"title": "Will the highest temperature in London be 20°C?"},
            "weather_natural",
            "measurement_or_observation_window_not_final",
        ),
        (
            {"title": "Will Candidate A win the presidential election?"},
            "politics_geopolitics",
            "official_result_release_or_certification_pending",
        ),
    ],
)
def test_theme_and_too_early_mechanism(row: dict[str, str], theme: str, mechanism: str) -> None:
    assert MODULE.market_theme(row) == theme
    assert MODULE.too_early_mechanism(row) == mechanism


def test_weather_detail_classification_and_timing() -> None:
    row = {
        "title": "Will the highest temperature in Seoul be 2°C on January 14?",
        "ancillary_text": "Resolution source: https://www.wunderground.com/history/daily/kr/incheon/RKSI",
        "market_id": "1166584",
        "dispute_ts": 1768316520,
        "proposal_ts": 1768316405,
    }
    assert MODULE.weather_subtype(row) == "daily_high_temperature"
    assert MODULE.weather_resolution_source_class(row) == "wunderground"
    assert MODULE.weather_window_key(row) == "daily_high_temperature|Seoul|2026-01-14"
    assert MODULE.temperature_proposal_timing(row) == "target_date_00_05"


@pytest.mark.parametrize(
    ("price", "expected"),
    [(0.01, "00_05c"), (0.05, "05_20c"), (0.20, "20_50c"), (0.50, "50_80c"), (0.80, "80_100c")],
)
def test_reverse_entry_price_band(price: float, expected: str) -> None:
    assert MODULE.reverse_entry_price_band({"entry_price": price}) == expected


def test_repricing_slice_metrics_uses_fixed_clear_rows() -> None:
    rows = [
        {
            "request_settlement_class": "binary_flip",
            "entry_price": 0.30,
            "pre_dispute_price": 0.20,
            "entry_delay_minutes": 1.0,
            "entry_trade_size": 25.0,
            "topic": "other",
            "reversed": True,
            "dispute_ts": 1767225600,
            "dispute_utc": "2026-01-01T00:00:00+00:00",
        },
        {
            "request_settlement_class": "upheld",
            "entry_price": 0.20,
            "pre_dispute_price": 0.10,
            "entry_delay_minutes": 2.0,
            "entry_trade_size": 50.0,
            "topic": "other",
            "reversed": False,
            "dispute_ts": 1767312000,
            "dispute_utc": "2026-01-02T00:00:00+00:00",
        },
    ]
    metrics = MODULE.repricing_slice_metrics(rows)
    assert metrics["markets"] == 2
    assert metrics["binary_flip_rate"] == 0.5
    assert metrics["mean_reverse_entry_price"] == 0.25
    assert metrics["gross_edge_per_priced_market"] == 0.25
    assert metrics["price_coverage"] == 1.0


def test_current_sports_fee_rate_matches_official_schedule() -> None:
    assert MODULE.FEE_SCHEDULE_AS_OF == "2026-08-12"
    assert MODULE.CURRENT_FEE_RATE_BY_TOPIC["sports_esports"] == 0.05


def test_dispute_case_classifier_routes_mechanical_and_semantic() -> None:
    sports = classify_dispute_case(
        {
            "title": "Will Player X record over 3.5 assists?",
            "ancillary_text": "Resolution source: https://www.nba.com/stats",
            "market_theme_v1": "sports_esports",
        }
    )
    semantic = classify_dispute_case(
        {
            "title": 'Will the candidate say "war" during the speech?',
            "ancillary_text": "Resolved using the official video and credible reporting.",
            "market_theme_v1": "speech_social_media",
        }
    )
    assert sports.track == "mechanical"
    assert sports.mechanism == "sports_stat_scope"
    assert sports.source_domains == ("nba.com",)
    assert semantic.track == "semantic"
    assert semantic.mechanism == "speech_or_social_count"


def test_dispute_case_classifier_ignores_bulletin_board_domain() -> None:
    case = classify_dispute_case(
        {
            "title": "Will the two leaders publicly reconcile by Friday?",
            "ancillary_text": "Resolved using credible reporting. Updates at https://polygonscan.com/tx/0xabc",
            "market_theme_v1": "politics_geopolitics",
        }
    )
    assert case.source_domains == ()
    assert case.source_class == "credible_reporting"


def test_speech_route_precedes_sports_keyword() -> None:
    case = classify_dispute_case(
        {
            "title": 'Will Trump post "UFC" this week?',
            "ancillary_text": "Resolution source: https://truthsocial.com/",
            "market_theme_v1": "sports_esports",
        }
    )
    assert case.mechanism == "speech_or_social_count"
    assert case.track == "mixed"


def test_sports_source_and_rules_route_plain_win_title_to_mechanical() -> None:
    case = classify_dispute_case(
        {
            "title": "Will Sporting Khalsa FC win on 2026-08-11?",
            "rules": "All markets settle on the official final result after the first 90 minutes.",
            "resolution_source": "https://www.thefa.com/competitions/thefacup",
        }
    )
    assert case.track == "mechanical"
    assert case.mechanism == "sports_result_scope"


def test_opportunity_cluster_collapses_sports_sibling_markets() -> None:
    total = opportunity_cluster_id(
        {
            "market_id": "1",
            "slug": "efa-nan-kha-2026-08-11-total-3pt5",
            "market_theme_v1": "sports_esports",
        }
    )
    spread = opportunity_cluster_id(
        {
            "market_id": "2",
            "slug": "efa-nan-kha-2026-08-11-spread-away-2pt5",
            "market_theme_v1": "sports_esports",
        }
    )
    assert total == spread == "sports_event:efa-nan-kha-2026-08-11"
    assert opportunity_cluster_id({"market_id": "3", "slug": "same-2026-08-11", "market_theme_v1": "other"}) == "market:3"


def test_executable_vwap_requires_full_depth() -> None:
    levels = [
        {"side": "ask", "price": 0.20, "size": 5},
        {"side": "ask", "price": 0.25, "size": 10},
        {"side": "bid", "price": 0.19, "size": 100},
    ]
    assert executable_vwap(levels, 10) == pytest.approx(0.225)
    assert executable_vwap(levels, 20) is None


def test_first_clear_non_p4_rows_uses_first_market_dispute() -> None:
    rows = [
        {"market_id": "1", "signal_id": "b", "dispute_ts": 20, "final_binary": 1, "proposed_binary": 0, "request_settlement_class": "binary_flip"},
        {"market_id": "1", "signal_id": "a", "dispute_ts": 10, "final_binary": 1, "proposed_binary": 0, "request_settlement_class": "too_early"},
        {"market_id": "2", "signal_id": "c", "dispute_ts": 15, "final_binary": 0, "proposed_binary": 0, "request_settlement_class": "upheld"},
    ]
    selected = first_clear_non_p4_rows(rows)
    assert [row["market_id"] for row in selected] == ["2"]
