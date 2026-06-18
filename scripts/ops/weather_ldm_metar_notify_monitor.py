#!/usr/bin/env python3
"""Probe and parse Unidata LDM/IDD METAR products.

This script has two jobs:
1. verify whether an upstream LDM server will serve us IDS/DDPLUS METAR data;
2. parse METAR text coming from an LDM product queue into append-only JSONL.

The full LDM daemon path still depends on upstream feed access. When access is
granted, run ldmd against a product queue, then use the ``run-pqcat`` command
here to consume station-level METAR rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

OPS = Path(__file__).resolve().parent
ROOT = OPS.parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.official_observation_feed.source_registry import (  # noqa: E402
    load_source_profiles,
)


DATA_ROOT = Path(os.environ.get("LDM_METAR_DATA_ROOT") or os.environ.get("DATA_PROJECT_DIR") or ROOT)
OUT_DIR = DATA_ROOT / "runtime/weather_edge_v1/ldm_metar"
DEFAULT_LDM_BUNDLE_ROOT = Path(os.environ.get("LDM_BUNDLE_ROOT", "~/.local/ldm-docker-6.15.0")).expanduser()
DEFAULT_LDM_UPSTREAM = os.environ.get("LDM_UPSTREAM", "idd.unidata.ucar.edu")
DEFAULT_FEED = os.environ.get("LDM_FEED", "IDS|DDPLUS")
DEFAULT_PATTERN = os.environ.get("LDM_PRODUCT_PATTERN", "^S[AP]")

METAR_RE = re.compile(
    r"(?P<prefix>\b(?:METAR|SPECI)\s+)?(?P<station>[A-Z][A-Z0-9]{3})\s+"
    r"(?P<report_ddhhmm>\d{6})Z\b(?P<body>.*?)(?:=|\s*)$"
)
TEMP_RE = re.compile(r"\s(?P<temp>M?\d{2})/(?P<dew>M?\d{2}|//)\b")
LDM_LOG_TS_RE = re.compile(r"^(?P<stamp>\d{8}T\d{6}(?:\.\d+)?Z)\s+")


@dataclass(frozen=True)
class StationTarget:
    station: str
    city: str
    live_eligible: bool


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ldm_log_ts(line: str) -> datetime | None:
    match = LDM_LOG_TS_RE.match(line)
    if not match:
        return None
    raw = match.group("stamp")
    for fmt in ("%Y%m%dT%H%M%S.%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_metar_temp_c(raw: str) -> float | None:
    match = TEMP_RE.search(f" {raw}")
    if not match:
        return None
    token = match.group("temp")
    return float(-int(token[1:]) if token.startswith("M") else int(token))


def parse_report_time_utc(report_ddhhmm: str, reference_utc: datetime) -> datetime | None:
    if len(report_ddhhmm) != 6 or not report_ddhhmm.isdigit():
        return None
    day = int(report_ddhhmm[:2])
    hour = int(report_ddhhmm[2:4])
    minute = int(report_ddhhmm[4:6])
    try:
        candidate = reference_utc.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None
    if candidate - reference_utc > timedelta(days=15):
        month = 12 if candidate.month == 1 else candidate.month - 1
        year = candidate.year - 1 if candidate.month == 1 else candidate.year
        try:
            candidate = candidate.replace(year=year, month=month)
        except ValueError:
            return None
    elif reference_utc - candidate > timedelta(days=15):
        month = 1 if candidate.month == 12 else candidate.month + 1
        year = candidate.year + 1 if candidate.month == 12 else candidate.year
        try:
            candidate = candidate.replace(year=year, month=month)
        except ValueError:
            return None
    return candidate


def stable_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_station_targets(*, include_research_cities: bool, only_cities: set[str] | None) -> dict[str, StationTarget]:
    profiles = load_source_profiles()
    targets: dict[str, StationTarget] = {}
    normalized_cities = {city.lower() for city in only_cities} if only_cities else None
    for profile in profiles.values():
        if normalized_cities and profile.city.lower() not in normalized_cities:
            continue
        if not include_research_cities and not profile.live_eligible:
            continue
        station = (profile.official_station_or_feed or profile.configured_icao).strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4}", station):
            continue
        targets[station] = StationTarget(station=station, city=profile.city, live_eligible=profile.live_eligible)
    return targets


def parse_metar_lines(
    text: str,
    *,
    station_targets: dict[str, StationTarget] | None = None,
    detect_ts_utc: datetime | None = None,
    source_name: str = "unidata_ldm_pqcat",
) -> list[dict[str, Any]]:
    detect_ts_utc = detect_ts_utc or utc_now()
    station_targets = station_targets or {}
    rows: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("\x03")
        if not line:
            continue
        if line.startswith(("METAR ", "SPECI ")):
            line = line.split(maxsplit=1)[1]
        match = METAR_RE.match(line)
        if not match:
            continue
        station = match.group("station").upper()
        if station_targets and station not in station_targets:
            continue
        temp_c = parse_metar_temp_c(line)
        if temp_c is None:
            continue
        report_ts = parse_report_time_utc(match.group("report_ddhhmm"), detect_ts_utc)
        if report_ts is None:
            continue
        target = station_targets.get(station)
        lag_sec = round((detect_ts_utc - report_ts).total_seconds(), 3)
        rows.append(
            {
                "source_name": source_name,
                "station": station,
                "city": target.city if target else "",
                "live_eligible": target.live_eligible if target else None,
                "source_report_ts_utc": iso_utc(report_ts),
                "local_detect_ts_utc": iso_utc(detect_ts_utc),
                "detected_after_report_sec": lag_sec,
                "temp_c": temp_c,
                "raw_metar": line,
                "raw_payload_hash": stable_hash(line),
            }
        )
    return rows


def apply_changed_flags(rows: list[dict[str, Any]], state_path: Path) -> list[dict[str, Any]]:
    state = read_json(state_path, {})
    for row in rows:
        key = row["station"]
        prev_hash = state.get(key, {}).get("raw_payload_hash")
        row["changed_since_last"] = prev_hash != row["raw_payload_hash"]
        state[key] = {
            "raw_payload_hash": row["raw_payload_hash"],
            "source_report_ts_utc": row["source_report_ts_utc"],
            "local_detect_ts_utc": row["local_detect_ts_utc"],
        }
    write_json(state_path, state)
    return rows


def ldm_env(bundle_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    ldm_home = bundle_root / "home/ldm"
    ldm_bin = ldm_home / "ldm-6.15.0/bin"
    ldm_lib = ldm_home / "ldm-6.15.0/lib"
    xml_lib = bundle_root / "usr/lib64"
    env["LDMHOME"] = str(ldm_home)
    env["PATH"] = f"{ldm_bin}:{env.get('PATH', '')}"
    env["LD_LIBRARY_PATH"] = f"{ldm_lib}:{xml_lib}:{env.get('LD_LIBRARY_PATH', '')}"
    return env


def resolve_binary(binary: str, env: dict[str, str]) -> str:
    found = shutil.which(binary, path=env.get("PATH"))
    if not found:
        raise FileNotFoundError(f"{binary} not found; set LDM_BUNDLE_ROOT or install LDM")
    return found


def classify_probe_output(output: str) -> str:
    lowered = output.lower()
    if "access denied" in lowered:
        return "access_denied"
    if "connection reset by peer" in lowered:
        return "connection_reset"
    if "starting up" in lowered and (" ok" in lowered or "note  ok" in lowered):
        return "connected_no_products"
    return "unknown"


def run_probe(args: argparse.Namespace) -> int:
    env = ldm_env(Path(args.bundle_root).expanduser())
    binary = resolve_binary(args.tool, env)
    cmd = [
        binary,
        "-vl-",
        "-h",
        args.upstream,
        "-f",
        args.feed,
        "-p",
        args.pattern,
        "-o",
        str(args.offset_sec),
        "-t",
        str(args.rpc_timeout_sec),
        "-T",
        str(args.total_timeout_sec),
    ]
    started = utc_now()
    timed_out = False
    proc = subprocess.Popen(
        cmd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=args.total_timeout_sec + args.outer_grace_sec)
        returncode: int | None = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            output, _ = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = proc.communicate()
        if not output:
            raw_output = exc.output or exc.stdout or ""
            output = raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else raw_output
        returncode = proc.returncode
    ended = utc_now()
    status = classify_probe_output(output)
    if timed_out and status == "unknown":
        status = "timeout"
    row = {
        "tool": args.tool,
        "upstream": args.upstream,
        "feed": args.feed,
        "pattern": args.pattern,
        "started_at_utc": iso_utc(started),
        "ended_at_utc": iso_utc(ended),
        "elapsed_sec": round((ended - started).total_seconds(), 3),
        "returncode": returncode,
        "status": status,
        "timed_out": timed_out,
        "output_tail": output[-4000:],
    }
    append_jsonl(Path(args.out_dir) / "ldm_probe.jsonl", row)
    print(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if row["status"] not in {"unknown"} else 1


def write_config(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    queue = out_dir / "queue/ldm.pq"
    data_dir = out_dir / "data"
    conf_dir = out_dir / "etc"
    conf_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    queue.parent.mkdir(parents=True, exist_ok=True)
    ldmd_conf = conf_dir / "ldmd.conf"
    ldmd_conf.write_text(
        "\n".join(
            [
                f'REQUEST {args.feed} "{args.pattern}" {args.upstream}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    runbook = {
        "create_queue": f"pqcreate -f -c -s {args.queue_size_mb}m -q {queue}",
        "run_ldmd": f"ldmd -l - -q {queue} -o {args.offset_sec} -m {args.max_latency_sec} {ldmd_conf}",
        "consume_queue": (
            f"{Path(__file__).name} run-pqcat --queue {queue} --feed {args.feed!r} "
            f"--pattern {args.pattern!r} --out-dir {out_dir}"
        ),
        "note": "The upstream must allow this host before ldmd/feedme will receive products.",
    }
    (conf_dir / "runbook.json").write_text(json.dumps(runbook, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ldmd_conf": str(ldmd_conf), "runbook": runbook}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def emit_rows(rows: list[dict[str, Any]], *, out_dir: Path, state_name: str, print_rows: bool) -> int:
    state_path = out_dir / state_name
    rows = apply_changed_flags(rows, state_path)
    for row in rows:
        append_jsonl(out_dir / "metar_observations.jsonl", row)
        if print_rows:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return len(rows)


def run_parse_file(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8", errors="replace")
    targets = load_station_targets(include_research_cities=args.include_research_cities, only_cities=set(args.city or []))
    detect_ts = datetime.fromisoformat(args.detect_ts_utc.replace("Z", "+00:00")) if args.detect_ts_utc else utc_now()
    rows = parse_metar_lines(text, station_targets=targets if not args.all_stations else {}, detect_ts_utc=detect_ts)
    count = emit_rows(rows, out_dir=Path(args.out_dir), state_name="parse_state.json", print_rows=True)
    print(json.dumps({"parsed_rows": count}, ensure_ascii=False, sort_keys=True))
    return 0


def iter_lines_from_process(cmd: list[str], env: dict[str, str]) -> Iterable[str]:
    with subprocess.Popen(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line
        rc = proc.wait()
        if rc not in (0, None):
            raise subprocess.CalledProcessError(rc, cmd)


def run_pqcat(args: argparse.Namespace) -> int:
    env = ldm_env(Path(args.bundle_root).expanduser())
    binary = resolve_binary("pqcat", env)
    cmd = [
        binary,
        "-f",
        args.feed,
        "-p",
        args.pattern,
        "-q",
        args.queue,
        "-i",
        str(args.interval_sec),
        "-o",
        str(args.offset_sec),
    ]
    targets = load_station_targets(include_research_cities=args.include_research_cities, only_cities=set(args.city or []))
    out_dir = Path(args.out_dir)
    processed = 0
    for line in iter_lines_from_process(cmd, env):
        detect_ts = parse_ldm_log_ts(line) or utc_now()
        rows = parse_metar_lines(line, station_targets=targets if not args.all_stations else {}, detect_ts_utc=detect_ts)
        if rows:
            processed += emit_rows(rows, out_dir=out_dir, state_name="pqcat_state.json", print_rows=True)
    return 0 if processed or args.allow_empty else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(func=None)
    parser.add_argument("--out-dir", default=str(OUT_DIR))

    probe = parser.add_subparsers(dest="command", required=True)

    probe_cmd = probe.add_parser("probe", help="Run notifyme/feedme against an upstream and write probe JSONL")
    probe_cmd.add_argument("--tool", choices=["notifyme", "feedme"], default="feedme")
    probe_cmd.add_argument("--bundle-root", default=str(DEFAULT_LDM_BUNDLE_ROOT))
    probe_cmd.add_argument("--upstream", default=DEFAULT_LDM_UPSTREAM)
    probe_cmd.add_argument("--feed", default=DEFAULT_FEED)
    probe_cmd.add_argument("--pattern", default=DEFAULT_PATTERN)
    probe_cmd.add_argument("--offset-sec", type=int, default=300)
    probe_cmd.add_argument("--rpc-timeout-sec", type=int, default=5)
    probe_cmd.add_argument("--total-timeout-sec", type=int, default=25)
    probe_cmd.add_argument("--outer-grace-sec", type=int, default=10)
    probe_cmd.set_defaults(func=run_probe)

    config_cmd = probe.add_parser("write-config", help="Write a minimal ldmd.conf and runbook for a product queue")
    config_cmd.add_argument("--upstream", default=DEFAULT_LDM_UPSTREAM)
    config_cmd.add_argument("--feed", default=DEFAULT_FEED)
    config_cmd.add_argument("--pattern", default=DEFAULT_PATTERN)
    config_cmd.add_argument("--queue-size-mb", type=int, default=128)
    config_cmd.add_argument("--offset-sec", type=int, default=300)
    config_cmd.add_argument("--max-latency-sec", type=int, default=3600)
    config_cmd.set_defaults(func=write_config)

    parse_cmd = probe.add_parser("parse-file", help="Parse METAR text from a file or stdin")
    parse_cmd.add_argument("input")
    parse_cmd.add_argument("--city", action="append", default=[])
    parse_cmd.add_argument("--include-research-cities", action="store_true")
    parse_cmd.add_argument("--all-stations", action="store_true")
    parse_cmd.add_argument("--detect-ts-utc", default="")
    parse_cmd.set_defaults(func=run_parse_file)

    pqcat_cmd = probe.add_parser("run-pqcat", help="Consume a populated LDM product queue via pqcat")
    pqcat_cmd.add_argument("--bundle-root", default=str(DEFAULT_LDM_BUNDLE_ROOT))
    pqcat_cmd.add_argument("--queue", required=True)
    pqcat_cmd.add_argument("--feed", default=DEFAULT_FEED)
    pqcat_cmd.add_argument("--pattern", default=DEFAULT_PATTERN)
    pqcat_cmd.add_argument("--interval-sec", type=int, default=1)
    pqcat_cmd.add_argument("--offset-sec", type=int, default=0)
    pqcat_cmd.add_argument("--city", action="append", default=[])
    pqcat_cmd.add_argument("--include-research-cities", action="store_true")
    pqcat_cmd.add_argument("--all-stations", action="store_true")
    pqcat_cmd.add_argument("--allow-empty", action="store_true")
    pqcat_cmd.set_defaults(func=run_pqcat)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func is None:
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
