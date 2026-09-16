from __future__ import annotations

from datetime import datetime, timezone

from src.strategies.rule_lawyer.dispute_verdict import (
    golgg_penta_negative_summary,
    parse_golgg_inhibitor_timeline,
    parse_golgg_kill_timeline,
    resolve_binance_timestamp,
    resolve_espn_total_corners,
    resolve_golgg_both_teams_inhibitors,
    resolve_golgg_penta_negative,
)


def test_binance_up_down_verdict_uses_candle_beginning_at_title_time() -> None:
    start_ms = int(datetime(2026, 1, 20, 20, tzinfo=timezone.utc).timestamp() * 1000)
    snapshot = {
        "title": "Bitcoin Up or Down - January 20, 3PM ET",
        "outcomes": ["Down", "Up"],
        "proposal": {"proposal_ts": int(datetime(2026, 1, 21, tzinfo=timezone.utc).timestamp())},
        "rules_snapshot": {
            "description": "Close >= open for the BTC/USDT 1 hour candle that begins at the title time.",
            "resolution_source": "https://www.binance.com/en/trade/BTC_USDT",
        },
    }

    def fake_get(_url, params):
        assert params["startTime"] == start_ms
        return [[start_ms, "100", "120", "90", "110", "1", start_ms + 3_600_000 - 1]]

    verdict = resolve_binance_timestamp(
        snapshot,
        get_json=fake_get,
        now=datetime(2026, 1, 21, tzinfo=timezone.utc),
    )
    assert verdict is not None
    assert verdict["status"] == "verified"
    assert verdict["winning_outcome"] == "Up"
    assert verdict["resolver_id"] == "binance_spot_1h_candle_v1"


def test_binance_above_verdict_uses_candle_ending_at_title_time() -> None:
    start_ms = int(datetime(2026, 4, 16, 19, tzinfo=timezone.utc).timestamp() * 1000)
    snapshot = {
        "title": "Ethereum above 2,285 on April 16, 4PM ET?",
        "outcomes": ["Yes", "No"],
        "proposal": {"proposal_ts": int(datetime(2026, 4, 17, tzinfo=timezone.utc).timestamp())},
        "rules_snapshot": {
            "description": 'Yes if Close is higher than 2,285 for the ETH/USDT 1 hour candle that ends at the title time.',
            "resolution_source": "https://www.binance.com/en/trade/ETH_USDT with \"1h\" selected",
        },
    }

    def fake_get(_url, params):
        assert params["startTime"] == start_ms
        return [[start_ms, "2300", "2310", "2200", "2286", "1", start_ms + 3_600_000 - 1]]

    verdict = resolve_binance_timestamp(
        snapshot,
        get_json=fake_get,
        now=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    assert verdict is not None
    assert verdict["status"] == "verified"
    assert verdict["winning_outcome"] == "Yes"


def test_golgg_timeline_parser_and_conservative_penta_negative() -> None:
    def row(clock: str, killer: str, victim: str) -> str:
        return (
            f"<tr><td>{clock}</td><td>side</td><td>{killer}</td><td>champ</td>"
            f"<td><img src='kill-icon.png'></td><td>champ</td><td>{victim}</td></tr>"
        )

    timeline = "<table>" + "".join(
        [
            row("10:00", "Alpha", "V1"),
            row("10:08", "Alpha", "V2"),
            row("10:20", "Alpha", "V3"),
            row("20:00", "Beta", "V4"),
            row("20:02", "Beta", "V5"),
        ]
    ) + "</table>"
    events = parse_golgg_kill_timeline(timeline)
    assert len(events) == 5
    summary = golgg_penta_negative_summary(events)
    assert summary["verified_no_penta"]
    assert summary["max_kills_in_window"] == 3


def test_golgg_resolver_requires_reconciled_complete_timeline() -> None:
    game_html = """
        <title>Alpha vs Beta game 1 - Games of Legends</title>
        2026-05-14
        Game Time<br/><h1>26:48</h1>
        <img src='kills.png' alt='Kills'/> 2
        <img src='kills.png' alt='Kills'/> 3
    """

    def row(clock: str, killer: str, victim: str) -> str:
        return (
            f"<tr><td>{clock}</td><td>side</td><td>{killer}</td><td>champ</td>"
            f"<td><img src='kill-icon.png'></td><td>champ</td><td>{victim}</td></tr>"
        )

    timeline_html = "".join(
        [
            row("10:00", "A", "V1"),
            row("10:08", "A", "V2"),
            row("20:00", "B", "V3"),
            row("21:00", "C", "V4"),
            row("22:00", "D", "V5"),
        ]
    )
    snapshot = {
        "title": "Game 1: Any Player Penta Kill?",
        "outcomes": ["Yes", "No"],
        "rules_snapshot": {
            "description": "Resolves Yes if any player gets a Penta Kill.",
            "resolution_source": "https://gol.gg/esports/home",
        },
    }

    def fake_get(url: str) -> str:
        return timeline_html if "timeline" in url else game_html

    verdict = resolve_golgg_penta_negative(
        snapshot,
        game_id=123,
        get_text=fake_get,
        now=datetime(2026, 5, 15, tzinfo=timezone.utc),
    )
    assert verdict is not None
    assert verdict["status"] == "verified"
    assert verdict["winning_outcome"] == "No"
    assert verdict["confidence"] == 0.995
    assert verdict["evidence"]["kill_events"] == 5


def test_golgg_both_teams_inhibitor_resolver_selects_no_for_one_side() -> None:
    game_html = """
        <title>Alpha vs Beta game 2 - Games of Legends</title>
        2026-08-07
        Game Time<br/><h1>24:49</h1>
    """
    timeline_html = """
        Game Time
        <tr><td>22:58</td><td><img src='redside-icon.png'></td><td>Player</td>
        <td><img src='inhib-icon.png'> INHIB MID</td></tr>
    """
    events = parse_golgg_inhibitor_timeline(timeline_html)
    assert events == [
        {"clock": "22:58", "seconds": 1378, "side": "red", "row_text": "22:58 Player INHIB MID"}
    ]
    snapshot = {
        "title": "Game 2: Both Teams Destroy Inhibitors?",
        "outcomes": ["Yes", "No"],
        "rules_snapshot": {
            "description": "Yes if both teams destroy an inhibitor.",
            "resolution_source": "https://gol.gg/esports/home",
        },
    }

    def fake_get(url: str) -> str:
        return timeline_html if "timeline" in url else game_html

    verdict = resolve_golgg_both_teams_inhibitors(
        snapshot,
        game_id=456,
        get_text=fake_get,
        now=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    assert verdict is not None
    assert verdict["status"] == "verified"
    assert verdict["winning_outcome"] == "No"
    assert verdict["evidence"]["destroying_sides"] == ["red"]


def test_espn_total_corners_resolver_honors_fallback_wait_and_line() -> None:
    snapshot = {
        "title": "Alpha vs. Beta: O/U 13.5 Total Corners",
        "outcomes": ["Over", "Under"],
        "market_status": {"end_date": "2026-08-09T14:00:00Z"},
        "rules_snapshot": {
            "description": "If official stats are not published within 24 hours, credible reporting may be used."
        },
    }
    payload = {
        "header": {
            "shortName": "BETA @ ALPHA",
            "competitions": [
                {"date": "2026-08-09T14:00Z", "status": {"type": {"completed": True}}}
            ],
        },
        "boxscore": {
            "teams": [
                {
                    "homeAway": "home",
                    "team": {"displayName": "Alpha"},
                    "statistics": [{"name": "wonCorners", "displayValue": "11"}],
                },
                {
                    "homeAway": "away",
                    "team": {"displayName": "Beta"},
                    "statistics": [{"name": "wonCorners", "displayValue": "3"}],
                },
            ]
        },
    }

    def fake_get(_url, params):
        assert params == {"event": "789"}
        return payload

    pending = resolve_espn_total_corners(
        snapshot,
        event_id=789,
        league="test.1",
        get_json=fake_get,
        now=datetime(2026, 8, 10, 13, 59, tzinfo=timezone.utc),
    )
    assert pending is not None
    assert pending["status"] == "pending_source_finality"
    verdict = resolve_espn_total_corners(
        snapshot,
        event_id=789,
        league="test.1",
        get_json=fake_get,
        now=datetime(2026, 8, 10, 14, 1, tzinfo=timezone.utc),
    )
    assert verdict is not None
    assert verdict["status"] == "verified"
    assert verdict["winning_outcome"] == "Over"
    assert verdict["evidence"]["total_corners"] == 14
