from pathlib import Path

from scripts.ops import weather_live_cross_observations_loop as loop
from weather_data_feed.observation_fast_lane import parse_observation_fast_lane


def test_fast_lane_omits_aggregate_but_main_keeps_it(monkeypatch, tmp_path: Path) -> None:
    args = loop.build_parser().parse_args(
        [
            "--output-dir",
            str(tmp_path / "live_cross"),
            "--notify-path",
            str(tmp_path / "notify.json"),
            "--sources",
            "amos_runway",
            "--cities",
            "Busan",
        ]
    )
    payload = {
        "schema_version": "weather_high_frequency_observations_v1",
        "schema_fingerprint": "fixture",
        "producer_identity": {},
        "generated_at_utc": "2026-08-10T00:00:00Z",
        "rows": 0,
        "new_observation_count": 0,
    }
    writes: list[tuple[Path, bool]] = []
    monkeypatch.setattr(loop, "build_payload", lambda _args: payload)
    monkeypatch.setattr(
        loop,
        "write_outputs",
        lambda _payload, output_dir, *, write_aggregate: writes.append(
            (output_dir, write_aggregate)
        ),
    )
    monkeypatch.setattr(loop, "write_new_observation_notification", lambda *_args: False)
    monkeypatch.setattr(loop, "update_state", lambda *_args: None)

    loop.run_cycle(args)
    loop.run_cycle(args, parse_observation_fast_lane("amos_core=amos_runway:Busan@20"))

    assert writes[0] == (tmp_path / "live_cross", True)
    assert writes[1][0] == tmp_path / "live_cross" / "fast_lanes" / "amos_core"
    assert writes[1][1] is False
