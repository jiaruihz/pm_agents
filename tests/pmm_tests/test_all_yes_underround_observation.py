import argparse
import json

from scripts.ops.all_yes_underround_observation_v0 import cycle, monitor


def _candidate(*, city: str, underround: float, prices=None):
    prices = prices or [0.10, 0.15, 0.20, 0.22, round(0.33 - underround, 6)]
    legs = [
        {
            "condition_id": f"{city}-condition-{idx}",
            "bracket": str(20 + idx),
            "best_ask": price,
            "best_bid": max(price - 0.01, 0.001),
            "ask_size": 10.0,
        }
        for idx, price in enumerate(prices)
    ]
    return {
        "event_date": "2026-06-14",
        "city": city,
        "event_slug": f"highest-temperature-in-{city.lower()}-on-june-14-2026",
        "underround": underround,
        "total_yes_ask_cost": round(1.0 - underround, 6),
        "max_yes_spread": 0.01,
        "snapshot_ts_utc": "2026-06-14T06:00:00Z",
        "legs_detail": legs,
    }


def _args(scan_json, run_dir):
    return argparse.Namespace(
        command="cycle",
        scan_json=str(scan_json),
        run_dir=str(run_dir),
        shares_per_leg=5.0,
        max_baskets_per_cycle=4,
        max_basket_cost_usd=5.0,
        min_underround=0.01,
        formal_min_underround=0.02,
        max_spread=0.05,
        max_snapshot_age_seconds=None,
    )


def test_observation_cycle_records_lower_band_without_formal_candidates(tmp_path):
    scan_json = tmp_path / "scan.json"
    run_dir = tmp_path / "run"
    scan_json.write_text(
        json.dumps(
            {
                "snapshot_path": "/tmp/snapshot.jsonl.gz",
                "snapshot_summary": {"snapshot_ts_utc_max": "2026-06-14T06:00:00Z"},
                "paper_shadow_candidates": [
                    _candidate(city="NearMiss", underround=0.015),
                    _candidate(city="Formal", underround=0.025),
                    _candidate(city="TooLow", underround=0.005),
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = cycle(_args(scan_json, run_dir))

    assert result["appended_baskets"] == 1
    assert result["appended_leg_quotes"] == 5
    assert result["skipped_formal_candidates"][0]["city"] == "Formal"
    rows = [json.loads(line) for line in (run_dir / "observation_baskets.jsonl").read_text().splitlines()]
    assert rows[0]["city"] == "NearMiss"
    assert rows[0]["execution_mode"] == "zero_notional_observation"
    assert rows[0]["no_order_placed"] is True
    assert rows[0]["observation_min_underround"] == 0.01
    assert rows[0]["formal_min_underround"] == 0.02


def test_observation_monitor_counts_active_dates_and_cities(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "observation_baskets.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"event_date": "2026-06-14", "city": "A"}),
                json.dumps({"event_date": "2026-06-14", "city": "B"}),
                json.dumps({"event_date": "2026-06-15", "city": "A"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = monitor(argparse.Namespace(run_dir=str(run_dir)))

    assert result["observation_baskets"] == 3
    assert result["observation_active_event_dates"] == 2
    assert result["observation_by_event_date"] == {"2026-06-14": 2, "2026-06-15": 1}
    assert result["observation_by_city"] == {"A": 2, "B": 1}
