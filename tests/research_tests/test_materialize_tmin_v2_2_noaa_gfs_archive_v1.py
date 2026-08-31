from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/analysis/tmin/materialize_tmin_v2_2_noaa_gfs_archive_v1.py"
SPEC = importlib.util.spec_from_file_location("noaa_gfs_archive", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def meta(key: str, modified: str) -> object:
    return MODULE.ObjectMeta(key, pd.Timestamp(modified), size=1000, etag="fixture")


def test_run_selection_excludes_future_object_and_selects_prior_complete_cycle() -> None:
    checkpoint = pd.Timestamp("2026-08-20T00:00:00Z")
    valid = [pd.Timestamp("2026-08-20T00:00:00Z"), pd.Timestamp("2026-08-20T01:00:00Z")]
    newest = pd.Timestamp("2026-08-19T18:00:00Z")
    older = pd.Timestamp("2026-08-19T12:00:00Z")
    inventory = {
        newest: {
            key: meta(key, "2026-08-20T00:01:00Z")
            for key in MODULE.cycle_required_keys(newest, valid).values()
        },
        older: {
            key: meta(key, "2026-08-19T18:00:00Z")
            for key in MODULE.cycle_required_keys(older, valid).values()
        },
    }
    chosen = MODULE.select_latest_cycle(checkpoint, valid, inventory)
    assert chosen is not None
    issue, values = chosen
    assert issue == older
    assert max(value.last_modified for value in values.values()) <= checkpoint


def test_idx_range_and_rounding_lattice_contract() -> None:
    index = b"1:0:d=2026081900:TMP:2 m above ground:anl:\n2:100:foo\n3:300:bar\n"
    assert MODULE.parse_idx_tmp_message(index, 500) == (0, 99)
    assert MODULE.native_rung(20.49) == 20
    assert MODULE.native_rung(20.50) == 21
    assert MODULE.next_colder_boundary(20) == 19.5


def test_iem_path_state_and_remaining_path_are_deterministic() -> None:
    truth = pd.DataFrame(
        {
            "city": ["Tokyo"] * 4,
            "target_date": ["2026-08-20"] * 4,
            "observation_event_time": pd.to_datetime(
                ["2026-08-19T20:00:00Z", "2026-08-19T21:00:00Z", "2026-08-19T22:00:00Z", "2026-08-20T01:00:00Z"], utc=True
            ),
            "temperature_c": [20.4, 19.6, 19.4, 20.0],
        }
    )
    checkpoint = MODULE.local_checkpoint("Tokyo", "2026-08-20", 6)
    state = MODULE.observation_state(truth.sample(frac=1, random_state=2), "Tokyo", "2026-08-20", checkpoint)
    assert state is not None
    assert state["raw_running_min_native"] == pytest.approx(19.6)
    assert state["current_native_rung"] == 20
    assert state["next_colder_boundary_native"] == 19.5
    assert state["realized_official_remaining_min"] == pytest.approx(19.4)
    assert [value.tz_convert("UTC").hour for value in MODULE.remaining_local_hours("Tokyo", "2026-08-20", 6)[:2]] == [21, 22]


def test_list_cycle_parses_conservative_metadata_without_network() -> None:
    xml = b'''<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated><Contents><Key>gfs.20260819/18/atmos/gfs.t18z.pgrb2.0p25.f006</Key><LastModified>2026-08-19T20:00:00.000Z</LastModified><ETag>"abc"</ETag><Size>123</Size></Contents></ListBucketResult>'''
    captured: list[str] = []

    def fetch(url: str, **_: object) -> bytes:
        captured.append(url)
        return xml

    issue = pd.Timestamp("2026-08-19T18:00:00Z")
    result = MODULE.list_cycle_objects(issue, fetch)
    entry = next(iter(result.values()))
    assert entry.last_modified == pd.Timestamp("2026-08-19T20:00:00Z")
    assert entry.etag == "abc"
    assert "list-type=2" in captured[0]


def test_optional_frozen_collector_is_used_only_when_both_clocks_are_pit_safe(tmp_path: Path) -> None:
    city_dir = tmp_path / "seoul"
    city_dir.mkdir()
    (city_dir / "observations.jsonl").write_text(
        '{"city":"Seoul","target_date":"2026-08-20","last_obs_utc":"2026-08-19T20:59:00Z","available_at_utc":"2026-08-19T20:59:30Z","running_min_c":18.8}\n'
        '{"city":"Seoul","target_date":"2026-08-20","last_obs_utc":"2026-08-19T21:01:00Z","available_at_utc":"2026-08-19T21:01:30Z","running_min_c":1.0}\n',
        encoding="utf-8",
    )
    truth = pd.DataFrame({"city": ["Seoul", "Seoul"], "target_date": ["2026-08-20", "2026-08-20"], "observation_event_time": pd.to_datetime(["2026-08-19T20:00:00Z", "2026-08-19T22:00:00Z"], utc=True), "temperature_c": [20.0, 19.0]})
    checkpoint = MODULE.local_checkpoint("Seoul", "2026-08-20", 6)
    state = MODULE.observation_state(truth, "Seoul", "2026-08-20", checkpoint, MODULE.load_frozen_pit_observations(tmp_path))
    assert state is not None
    assert state["raw_running_min_native"] == pytest.approx(18.8)
    assert state["observation_clock_provenance"] == "FROZEN_COLLECTOR_PIT"
    assert state["observation_available_at"] <= checkpoint


def test_materialize_full_remaining_path_with_fake_http_and_decoder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    truth = pd.DataFrame(
        {
            "city": ["Seoul"] * 4,
            "target_date": ["2026-08-20"] * 4,
            "observation_event_time": pd.to_datetime(
                ["2026-08-19T21:00:00Z", "2026-08-19T22:00:00Z", "2026-08-20T00:00:00Z", "2026-08-20T01:00:00Z"], utc=True
            ),
            "temperature_c": [20.0, 19.9, 19.2, 20.2],
        }
    )
    issue = pd.Timestamp("2026-08-19T18:00:00Z")
    checkpoint = MODULE.local_checkpoint("Seoul", "2026-08-20", 6)
    valid = [item.tz_convert("UTC") for item in MODULE.remaining_local_hours("Seoul", "2026-08-20", 6)]
    objects = {key: meta(key, "2026-08-19T20:00:00Z") for key in MODULE.cycle_required_keys(issue, valid).values()}
    monkeypatch.setattr(MODULE, "candidate_cycles", lambda _checkpoint, max_lookback_hours=36: [issue])
    monkeypatch.setattr(MODULE, "list_cycle_objects", lambda _issue, _fetch: objects)

    def fetch(url: str, *, headers: dict[str, str] | None = None, **_: object) -> bytes:
        if url.endswith(".idx"):
            return b"1:0:d=fixture:TMP:2 m above ground:anl:\n2:10:next\n"
        return b"fake-grib-message"

    calls = {"n": 0}

    def decoder(_: bytes, __: float, ___: float) -> float:
        calls["n"] += 1
        return 20.0 - calls["n"] / 10.0

    archive, points, inventory, evidence = MODULE.materialize(truth, tmp_path, start_date="2026-08-20", end_date="2026-08-20", workers=1, fetch=fetch, decoder=decoder)
    row = archive.loc[(archive.city == "Seoul") & (archive.checkpoint_hour == 6)].iloc[0]
    assert row["checkpoint_time"] == checkpoint
    assert len(row["full_remaining_path"]) == 18
    assert row["forecast_remaining_min"] == pytest.approx(18.2)
    assert row["available_at"] == pd.Timestamp("2026-08-19T20:00:00Z")
    assert row["available_at_provenance"].startswith("NOAA_NODD")
    assert len(points.loc[points.checkpoint_hour.eq(6)]) == 18
    assert evidence
    assert inventory["eligible_rows"] == 2  # Seoul has both fixed 06:00 and 09:00 checkpoints.
