from weather_data_feed.source_basis import latest_sources, rmk_source_basis_state
from scripts.ops.weather_rmk_source_basis_opportunity_monitor import classify_opportunity, trade_intent


def test_rmk_source_basis_state_uses_routine_rmk_as_proxy():
    rows = [
        {
            "city": "SanFrancisco",
            "source": "iem_asos_routine_latest",
            "status": "ok",
            "target_date": "2026-06-25",
            "source_report_ts_utc": "2026-06-25T19:56:00+00:00",
            "local_detect_ts_utc": "2026-06-25T20:04:04+00:00",
            "detected_after_report_sec": 484.0,
            "main_round_f": 70,
            "rmk_round_f": 69,
            "temp_round_f": 69,
        },
        {
            "city": "SanFrancisco",
            "source": "iem_asos_madishf_latest",
            "status": "ok",
            "target_date": "2026-06-25",
            "temp_round_f": 70,
            "main_round_f": 70,
            "rmk_round_f": 70,
        },
        {
            "city": "SanFrancisco",
            "source": "weather_com_current",
            "status": "ok",
            "target_date": "2026-06-25",
            "temp_round_f": 69,
            "max_temp_f_since_7am": 69,
        },
    ]

    state = rmk_source_basis_state("SanFrancisco", latest_sources(rows))

    assert state.status == "false_cross"
    assert state.proxy_round_f == 69
    assert state.routine_main_round_f == 70
    assert state.fast_round_f == 70
    assert state.false_cross_sources == {"iem_asos_madishf_latest": 70}
    assert state.wu_max_f_since_7am == 69
    assert not state.wu_current_contradicts_proxy


def test_rmk_source_basis_state_requires_rmk_not_main_temp():
    rows = [
        {
            "city": "Austin",
            "source": "iem_asos_routine_latest",
            "status": "ok",
            "target_date": "2026-06-25",
            "main_round_f": 90,
        },
        {"city": "Austin", "source": "iem_asos_madishf_latest", "status": "ok", "temp_round_f": 90},
    ]

    state = rmk_source_basis_state("Austin", latest_sources(rows))

    assert state.status == "missing_routine_rmk"
    assert state.proxy_round_f is None


def test_classify_opportunity_buys_proxy_yes_when_fast_cross_misprices_it():
    assert (
        classify_opportunity(
            has_false_cross=True,
            wu_current_contradicts_proxy=False,
            market_contains_proxy=True,
            market_contains_fast=False,
            yes_book={"best_ask": 0.08},
        )
        == "candidate_yes_cheap"
    )
    assert (
        classify_opportunity(
            has_false_cross=True,
            wu_current_contradicts_proxy=False,
            market_contains_proxy=True,
            market_contains_fast=False,
            yes_book={"best_ask": 0.55},
        )
        == "candidate_yes_mid"
    )
    assert (
        classify_opportunity(
            has_false_cross=True,
            wu_current_contradicts_proxy=False,
            market_contains_proxy=False,
            market_contains_fast=True,
            yes_book={"best_ask": 0.08},
        )
        == "false_cross_context"
    )


def test_classify_opportunity_separates_wu_current_contradiction():
    assert (
        classify_opportunity(
            has_false_cross=True,
            wu_current_contradicts_proxy=True,
            market_contains_proxy=True,
            market_contains_fast=False,
            yes_book={"best_ask": 0.08},
        )
        == "wu_current_contradicts_proxy"
    )


def test_trade_intent_only_marks_clean_candidate_statuses():
    assert trade_intent("candidate_yes_cheap") == {
        "clean_rmk_basis_candidate": True,
        "candidate_tier": "cheap",
        "paper_action": "BUY_YES_PROXY_BRACKET",
    }
    assert trade_intent("candidate_yes_mid")["clean_rmk_basis_candidate"]
    assert trade_intent("wu_current_contradicts_proxy") == {
        "clean_rmk_basis_candidate": False,
        "candidate_tier": "",
        "paper_action": "NONE",
    }
