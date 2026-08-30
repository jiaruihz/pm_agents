"""CLI for bounded collection, replay audit, and report generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import yaml

from us_fast_weather_lab.awc import AwcCollector, AwcConfig
from us_fast_weather_lab.clock import probe_clock
from us_fast_weather_lab.commercial_ws import (
    MetarWsCollector,
    MetarWsConfig,
    SynopticPushCollector,
    SynopticPushConfig,
)
from us_fast_weather_lab.model import metar_event
from us_fast_weather_lab.lab_to_source_events import materialize_lab_events
from us_fast_weather_lab.metar_market_capture import materialize_capture_demands
from us_fast_weather_lab.reports import generate_reports
from us_fast_weather_lab.storage import EvidenceStore, canonical_json
from us_fast_weather_lab.wis2 import Wis2Collector, discover_brokers


LAB_ROOT = Path(__file__).resolve().parent
DEFAULT_RUNTIME = Path("runtime/us_fast_weather_lab")
DEFAULT_REPORTS = LAB_ROOT / "reports"
COMMERCIAL_CONFIG_PATH = LAB_ROOT / "config" / "commercial_streams.yaml"
CONFIG_PATHS = [
    LAB_ROOT / "config" / "airports.yaml",
    LAB_ROOT / "config" / "acceptance_contract.yaml",
    LAB_ROOT / "config" / "sources.yaml",
    LAB_ROOT / "config" / "full_closure_acceptance.yaml",
    LAB_ROOT / "config" / "source_ledger_seed.csv",
    LAB_ROOT / "config" / "airport_direct_sensor_inventory.csv",
    COMMERCIAL_CONFIG_PATH,
]


def load_yaml(path: Path) -> dict[str, Any]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8")))


def config_hash() -> str:
    digest = hashlib.sha256()
    for path in CONFIG_PATHS:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def airports() -> set[str]:
    config = load_yaml(CONFIG_PATHS[0])
    return {str(row["icao"]).upper() for row in config["primary_20"]}


def command_init(args: argparse.Namespace) -> int:
    store = EvidenceStore(Path(args.runtime_root))
    store.close()
    print(canonical_json({"status": "initialized", "runtime_root": str(Path(args.runtime_root).resolve())}))
    return 0


def command_replay(args: argparse.Namespace) -> int:
    store = EvidenceStore(Path(args.runtime_root))
    runs = store.query("SELECT run_id FROM collector_run ORDER BY started_at_ns DESC LIMIT 1")
    if not runs:
        store.close()
        raise RuntimeError("no collector run exists for replay")
    store.run_id = str(runs[0]["run_id"])
    rows = store.query(
        """SELECT DISTINCT o.observation_version_id, o.normalized_raw_text,
                  o.normalized_fields_json,
                  o.observation_time, o.station_id, o.report_kind,
                  o.air_temperature_c
           FROM observation_event AS o
           JOIN source_observation_seen AS s
             ON s.observation_version_id = o.observation_version_id
           JOIN transport_message AS t
             ON t.transport_message_id = s.transport_message_id
           WHERE t.run_id = ?
           ORDER BY o.observation_version_id""",
        (store.run_id,),
    )
    stable = 0
    checked = 0
    raw_records = 0
    mismatches: list[dict[str, str]] = []
    for row in rows:
        raw = str(row["normalized_raw_text"] or "")
        raw_records += int(bool(raw))
        fields = json.loads(str(row["normalized_fields_json"]))
        reference_ns = int(time.time_ns())
        try:
            obs_dt = str(row["observation_time"]).replace("+00:00", "Z")
            import datetime as dt

            reference_ns = int(dt.datetime.fromisoformat(obs_dt.replace("Z", "+00:00")).timestamp() * 1e9)
        except Exception:  # noqa: BLE001
            pass
        event = metar_event(
            str(raw),
            reference_ns=reference_ns,
            override={
                **fields,
                "force_report_kind": row["report_kind"]
                if row["report_kind"] not in {"METAR", "SPECI"}
                else None,
            },
        )
        checked += 1
        if event and event["observation_version_id"] == row["observation_version_id"]:
            stable += 1
        else:
            mismatches.append(
                {
                    "stored": str(row["observation_version_id"]),
                    "replayed": str(event["observation_version_id"] if event else "NONE"),
                }
            )
    details = {
        "raw_records": raw_records,
        "replayed_records": checked,
        "observations": len(rows),
        "stable": stable,
        "stable_identity": checked == stable == len(rows),
        "mismatches": mismatches[:20],
    }
    store.record_replay_audit(details)
    store.close()
    print(canonical_json(details))
    return 0 if details["stable_identity"] else 1


def command_report(args: argparse.Namespace) -> int:
    manifest = generate_reports(Path(args.runtime_root), Path(args.reports_root), config_paths=CONFIG_PATHS)
    print(canonical_json({"status": "reported", "counts": manifest["counts"], "disposition": manifest["disposition"]}))
    return 0


def command_smoke(args: argparse.Namespace) -> int:
    if args.duration_sec <= 0:
        raise ValueError("duration-sec must be positive")
    runtime_root = Path(args.runtime_root)
    reports_root = Path(args.reports_root)
    capture_values = (
        getattr(args, "source_events_root", None),
        getattr(args, "market_books_latest", None),
        getattr(args, "market_capture_demands_jsonl", None),
    )
    if any(capture_values) and not all(capture_values):
        raise ValueError(
            "market capture requires --source-events-root, --market-books-latest, "
            "and --market-capture-demands-jsonl together"
        )
    capture_enabled = all(capture_values)
    source_config = load_yaml(CONFIG_PATHS[2])
    commercial_config = load_yaml(COMMERCIAL_CONFIG_PATH)
    acceptance = load_yaml(CONFIG_PATHS[1])
    station_set = airports()
    metar_ws_key: str | None = None
    synoptic_token: str | None = None
    if getattr(args, "enable_metar_ws", False):
        metar_ws_env = str(commercial_config["metar_ws"]["api_key_env"])
        metar_ws_key = os.environ.get(metar_ws_env)
        if not metar_ws_key:
            raise RuntimeError(f"--enable-metar-ws requires the {metar_ws_env} environment variable")
    if getattr(args, "enable_synoptic_push", False):
        synoptic_env = str(commercial_config["synoptic_push"]["api_token_env"])
        synoptic_token = os.environ.get(synoptic_env)
        if not synoptic_token:
            raise RuntimeError(f"--enable-synoptic-push requires the {synoptic_env} environment variable")
    enable_wis2 = not bool(getattr(args, "disable_wis2", False))
    enable_awc = not bool(getattr(args, "disable_awc", False))
    if not enable_wis2 and not enable_awc and not (metar_ws_key or synoptic_token):
        raise RuntimeError("no collectors enabled; enable WIS2/AWC or an authenticated commercial collector")

    max_offset = float(acceptance["clock"]["max_abs_offset_ms_for_valid_sample"])
    clock_interval = float(acceptance["clock"]["health_sample_interval_seconds"])
    store: EvidenceStore | None = None
    run_id: str | None = None
    wis2: Wis2Collector | None = None
    awc: AwcCollector | None = None
    commercial_collectors: list[MetarWsCollector | SynopticPushCollector] = []
    interrupted = False
    termination_reason = "duration_complete"
    cleanup_errors: list[str] = []
    wis2_started = False
    awc_started = False
    commercial_started: list[MetarWsCollector | SynopticPushCollector] = []
    previous_int: Any = None
    previous_term: Any = None
    signals_installed = False
    summary: dict[str, Any] = {}

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True

    try:
        store = EvidenceStore(runtime_root)
        run_id = store.start_run(config_hash=config_hash(), vantage_id=args.vantage_id)
        store.record_clock(probe_clock(max_offset))

        if enable_wis2:
            wis2_config = source_config["wis2"]
            brokers, discovery_evidence = discover_brokers(
                str(wis2_config["discovery_url"]),
                fallback_brokers=dict(wis2_config["fallback_brokers"]),
                authoritative_brokers=dict(wis2_config.get("authoritative_brokers") or {}),
                username=str(wis2_config["username"]),
                password=str(wis2_config["password"]),
            )
            store.record_access(
                source_family="WIS2",
                endpoint="wis2-gdc.weather.gc.ca",
                phase="dynamic_discovery",
                status=str(discovery_evidence["status"]),
                request={"url": wis2_config["discovery_url"]},
                response=discovery_evidence,
            )
            wis2 = Wis2Collector(
                store,
                brokers=brokers,
                topic_base=str(wis2_config["topic_base"]),
                stations=station_set,
                vantage_id=args.vantage_id,
                fetch_timeout_seconds=args.http_timeout_sec,
            )

        if enable_awc:
            awc_raw = source_config["awc"]
            awc = AwcCollector(
                store,
                config=AwcConfig(
                    api_url=str(awc_raw["api_url"]),
                    cache_url=str(awc_raw["cache_url"]),
                    poll_interval_seconds=float(awc_raw["poll_interval_seconds"]),
                    cache_interval_seconds=float(awc_raw["cache_interval_seconds"]),
                    user_agent=str(awc_raw["user_agent"]),
                    max_requests_per_minute=int(awc_raw["max_requests_per_minute"]),
                ),
                stations=station_set,
                vantage_id=args.vantage_id,
                timeout_seconds=args.http_timeout_sec,
            )

        if metar_ws_key:
            metar_raw = commercial_config["metar_ws"]
            commercial_station_set = station_set | {
                str(value).strip().upper()
                for value in metar_raw.get("benchmark_stations", ())
                if str(value).strip()
            }
            channels: list[str] = []
            if bool(metar_raw.get("subscribe_primary_official")):
                channels.extend(
                    f"metar.obs.{station.lower()}" for station in sorted(commercial_station_set)
                )
            if bool(metar_raw.get("subscribe_primary_hfmetar")):
                channels.extend(
                    f"metar.obs10.{station.lower()}" for station in sorted(commercial_station_set)
                )
            channels.extend(f"metar.atis.{str(station).lower()}" for station in metar_raw["datis_stations"])
            commercial_collectors.append(
                MetarWsCollector(
                    store,
                    config=MetarWsConfig(
                        endpoint=str(metar_raw["endpoint"]),
                        reconnect_seconds=float(metar_raw["reconnect_seconds"]),
                        channels=tuple(channels),
                    ),
                    api_key=metar_ws_key,
                    stations=commercial_station_set,
                    vantage_id=args.vantage_id,
                )
            )
        if synoptic_token:
            synoptic_raw = commercial_config["synoptic_push"]
            commercial_collectors.append(
                SynopticPushCollector(
                    store,
                    config=SynopticPushConfig(
                        endpoint_base=str(synoptic_raw["endpoint_base"]),
                        reconnect_seconds=float(synoptic_raw["reconnect_seconds"]),
                        stations=tuple(sorted(station_set)),
                        variables=tuple(str(value) for value in synoptic_raw["variables"]),
                        units=str(synoptic_raw["units"]),
                    ),
                    api_token=synoptic_token,
                    vantage_id=args.vantage_id,
                )
            )

        previous_int = signal.signal(signal.SIGINT, request_stop)
        previous_term = signal.signal(signal.SIGTERM, request_stop)
        signals_installed = True
        started = time.monotonic()
        next_clock = started + clock_interval
        next_heartbeat = started
        next_market_capture = started
        if wis2:
            wis2_started = True
            wis2.start()
        if awc:
            awc_started = True
            awc.start()
        for collector in commercial_collectors:
            commercial_started.append(collector)
            collector.start()

        while not interrupted and time.monotonic() - started < args.duration_sec:
            now = time.monotonic()
            if now >= next_clock:
                store.record_clock(probe_clock(max_offset))
                next_clock += clock_interval
            if capture_enabled and now >= next_market_capture:
                materialize_lab_events(store.db_path, Path(args.source_events_root))
                materialize_capture_demands(
                    Path(args.source_events_root),
                    Path(args.market_books_latest),
                    Path(args.market_capture_demands_jsonl),
                    resolution_jsonl=(
                        Path(args.market_capture_resolution_jsonl)
                        if args.market_capture_resolution_jsonl else None
                    ),
                )
                next_market_capture += 1.0
            if now >= next_heartbeat:
                print(
                    canonical_json(
                        {
                            "status": "running",
                            "run_id": run_id,
                            "elapsed_sec": round(now - started, 1),
                            "clock_valid": store.clock_state.valid,
                            "wis2": wis2.summary() if wis2 else {"status": "disabled"},
                            "awc": awc.summary() if awc else {"status": "disabled"},
                            "commercial": {
                                collector.source_id: collector.summary() for collector in commercial_collectors
                            },
                        }
                    ),
                    flush=True,
                )
                next_heartbeat += 10.0
            time.sleep(0.1)
    except BaseException:  # noqa: BLE001
        termination_reason = "exception"
        raise
    finally:
        if interrupted:
            termination_reason = "signal"
        for collector in reversed(commercial_started):
            try:
                collector.stop()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(f"{collector.source_id}: {type(exc).__name__}")
        if awc and awc_started:
            try:
                awc.stop()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(f"AWC: {type(exc).__name__}")
        if wis2 and wis2_started:
            try:
                wis2.stop()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(f"WIS2: {type(exc).__name__}")
        if store is not None and run_id is not None:
            try:
                store.end_run(termination_reason)
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(f"run_end: {type(exc).__name__}")
        summary = {
            "run_id": run_id,
            "wis2": wis2.summary() if wis2 else {"status": "disabled"},
            "awc": awc.summary() if awc else {"status": "disabled"},
            "commercial": {collector.source_id: collector.summary() for collector in commercial_collectors},
            "interrupted": interrupted,
            "termination_reason": termination_reason,
            "cleanup_errors": cleanup_errors,
        }
        if store is not None:
            try:
                (runtime_root / "last_run_summary.json").write_text(
                    json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(f"summary_write: {type(exc).__name__}")
            try:
                store.close()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(f"store_close: {type(exc).__name__}")
        if signals_installed:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)

    if cleanup_errors:
        raise RuntimeError(f"smoke cleanup failed after evidence seal: {', '.join(cleanup_errors)}")
    replay_args = argparse.Namespace(runtime_root=str(runtime_root))
    replay_status = command_replay(replay_args)
    manifest = generate_reports(runtime_root, reports_root, config_paths=CONFIG_PATHS)
    print(
        canonical_json(
            {
                "status": "complete",
                **summary,
                "replay_status": replay_status,
                "counts": manifest["counts"],
                "disposition": manifest["disposition"],
                "reports_root": str(reports_root.resolve()),
            }
        ),
        flush=True,
    )
    return replay_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="U.S. fast weather append-only benchmark")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    init.set_defaults(func=command_init)
    replay = sub.add_parser("replay")
    replay.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    replay.set_defaults(func=command_replay)
    report = sub.add_parser("report")
    report.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    report.add_argument("--reports-root", default=str(DEFAULT_REPORTS))
    report.set_defaults(func=command_report)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME))
    smoke.add_argument("--reports-root", default=str(DEFAULT_REPORTS))
    smoke.add_argument("--vantage-id", required=True)
    smoke.add_argument("--duration-sec", type=float, default=180.0)
    smoke.add_argument("--http-timeout-sec", type=float, default=10.0)
    smoke.add_argument("--enable-metar-ws", action="store_true")
    smoke.add_argument("--enable-synoptic-push", action="store_true")
    smoke.add_argument("--disable-wis2", action="store_true")
    smoke.add_argument("--disable-awc", action="store_true")
    smoke.add_argument("--source-events-root")
    smoke.add_argument("--market-books-latest")
    smoke.add_argument("--market-capture-demands-jsonl")
    smoke.add_argument("--market-capture-resolution-jsonl")
    smoke.set_defaults(func=command_smoke)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
