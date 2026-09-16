#!/usr/bin/env python3
"""Bounded WIS2 aviation-METAR first-arrival discovery probe."""

from __future__ import annotations

import argparse
import json
import ssl
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TOPICS = (
    "origin/a/wis2/+/data/recommended/aviation/metar",
    "origin/a/wis2/+/data/+/S/A/#",
)
DEFAULT_STATIONS = ("CYYZ", "EDDM", "EHAM", "EFHK", "LEMD", "LFPB", "LLBG", "LTFM", "NZWN", "UUWW")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def mqtt_reason_code_value(reason_code: Any) -> int:
    value = getattr(reason_code, "value", reason_code)
    return int(value)


def notification_station_matches(payload: dict[str, Any], stations: set[str]) -> list[str]:
    properties = payload.get("properties") or {}
    candidates = {
        str(properties.get(key) or "").upper()
        for key in ("icao", "station", "station-id", "wigos-station-identifier")
    }
    raw = json.dumps(payload, ensure_ascii=True).upper()
    return sorted(station for station in stations if station in candidates or station in raw)


def notification_is_aviation_candidate(topic: str) -> bool:
    normalized = f"/{topic.strip('/').lower()}/"
    return "/aviation/metar/" in normalized or "/s/a/" in normalized


def compact_notification(topic: str, payload: dict[str, Any], stations: set[str], received_at_utc: str) -> dict[str, Any]:
    properties = payload.get("properties") or {}
    links = payload.get("links") or []
    return {
        "schema_version": "weather_wis2_metar_probe_v1",
        "received_at_utc": received_at_utc,
        "topic": topic,
        "notification_id": payload.get("id"),
        "matched_stations": notification_station_matches(payload, stations),
        "aviation_candidate": notification_is_aviation_candidate(topic),
        "wigos_station_identifier": properties.get("wigos-station-identifier"),
        "publication_time": properties.get("pubtime") or properties.get("datetime"),
        "canonical_urls": [str(link.get("href")) for link in links if link.get("rel") in {"canonical", "update"} and link.get("href")],
        "has_inline_content": bool(payload.get("content")),
    }


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:  # pragma: no cover - environment dependency
        raise RuntimeError("paho-mqtt is required; install project requirements") from exc

    stations = {value.strip().upper() for value in args.stations if value.strip()}
    messages: list[dict[str, Any]] = []
    topic_counts: Counter[str] = Counter()
    connected = threading.Event()
    connect_error = ""

    def on_connect(client: Any, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any = None) -> None:
        nonlocal connect_error
        if mqtt_reason_code_value(reason_code) != 0:
            connect_error = str(reason_code)
            connected.set()
            return
        for topic in args.topics:
            client.subscribe(topic)
        connected.set()

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        topic_counts[str(message.topic)] += 1
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        compact = compact_notification(str(message.topic), payload, stations, now_utc())
        if compact["matched_stations"] or compact["aviation_candidate"]:
            messages.append(compact)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pm-agents-wis2-probe-{int(time.time())}")
    client.username_pw_set(args.username, args.password)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    client.on_connect = on_connect
    client.on_message = on_message

    started_at = now_utc()
    client.connect(args.broker, args.port, keepalive=max(30, int(args.duration_sec) + 10))
    client.loop_start()
    try:
        if not connected.wait(timeout=args.connect_timeout_sec):
            raise TimeoutError(f"WIS2 broker connect timed out after {args.connect_timeout_sec}s")
        if connect_error:
            raise ConnectionError(f"WIS2 broker rejected connection: {connect_error}")
        time.sleep(args.duration_sec)
    finally:
        client.loop_stop()
        client.disconnect()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        for row in messages:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")

    return {
        "status": "ok",
        "started_at_utc": started_at,
        "finished_at_utc": now_utc(),
        "broker": args.broker,
        "port": args.port,
        "topics": args.topics,
        "stations": sorted(stations),
        "duration_sec": args.duration_sec,
        "notifications_seen": sum(topic_counts.values()),
        "aviation_candidate_notifications": len(messages),
        "target_matched_notifications": sum(bool(row["matched_stations"]) for row in messages),
        "topic_counts": dict(topic_counts),
        "output": str(output_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded WIS2 aviation-METAR discovery probe.")
    parser.add_argument("--broker", default="globalbroker.meteo.fr")
    parser.add_argument("--port", type=int, default=8883)
    parser.add_argument("--username", default="everyone")
    parser.add_argument("--password", default="everyone")
    parser.add_argument("--topics", nargs="+", default=list(DEFAULT_TOPICS))
    parser.add_argument("--stations", nargs="+", default=list(DEFAULT_STATIONS))
    parser.add_argument("--duration-sec", type=float, default=60.0)
    parser.add_argument("--connect-timeout-sec", type=float, default=15.0)
    parser.add_argument("--output", default="runtime/wis2_metar_probe/notifications.jsonl")
    return parser


def main(argv: list[str] | None = None) -> int:
    summary = run_probe(build_parser().parse_args(argv))
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
