#!/usr/bin/env python3
"""Health-check and fail over the local market proxy for weather data collection."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_DIR = Path.home() / "projects/weather_data_feed_service_runtime/targeted_output/paper_snapshots"
DEFAULT_LOG = Path.home() / "projects/weather_data_feed_service_runtime/loop/market_proxy_failover.jsonl"
DEFAULT_GROUP = "🙂 TAGSS"
DEFAULT_CANDIDATES = [
    "🇭🇰 香港 01丨1x HK",
    "🇭🇰 香港 02丨1x HK",
    "🇭🇰 香港 03丨1x HK",
    "🇭🇰 香港家宽 01丨1x HK",
    "🇭🇰 香港家宽 02丨1x HK",
    "🇭🇰 香港家宽 03丨1x HK",
    "🇭🇰 香港家宽 04丨1x HK",
    "🇯🇵 日本 01丨1x JP",
    "🇯🇵 日本 02丨1x JP",
    "🇯🇵 日本 03丨1x JP",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def latest_health_slug(snapshot_dir: Path) -> str:
    env_slug = os.getenv("WEATHER_MARKET_PROXY_HEALTH_SLUG", "").strip()
    if env_slug:
        return env_slug
    for path in sorted(snapshot_dir.glob("snapshot_*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in payload.get("records") or []:
            slug = str(row.get("event_slug") or "").strip()
            if slug:
                return slug
    return "highest-temperature-in-amsterdam-on-july-2-2026"


def controller_json(controller: str, path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    url = controller.rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def current_node(controller: str, group: str) -> str:
    payload = controller_json(controller, "/proxies")
    return str((payload.get("proxies") or {}).get(group, {}).get("now") or "")


def switch_node(controller: str, group: str, node: str) -> None:
    controller_json(
        controller,
        "/proxies/" + urllib.parse.quote(group, safe=""),
        method="PUT",
        body={"name": node},
    )


def probe_gamma(proxy: str, slug: str, timeout: int) -> dict[str, Any]:
    tmp = Path("/tmp/weather_market_proxy_probe.json")
    url = f"https://gamma-api.polymarket.com/events?slug={urllib.parse.quote(slug, safe='')}"
    cmd = [
        "curl",
        "-x",
        proxy,
        "--max-time",
        str(timeout),
        "-sS",
        "-w",
        "\n%{http_code} %{size_download} %{time_total}",
        "-o",
        str(tmp),
        url,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    status_line = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
    status = None
    bytes_download = None
    elapsed = None
    parts = status_line.split()
    if len(parts) >= 3:
        try:
            status = int(parts[0])
            bytes_download = int(float(parts[1]))
            elapsed = float(parts[2])
        except ValueError:
            pass
    markets = None
    json_len = None
    if status == 200 and tmp.exists():
        try:
            data = json.loads(tmp.read_text(encoding="utf-8"))
            json_len = len(data) if isinstance(data, list) else None
            if isinstance(data, list) and data:
                markets = len(data[0].get("markets") or [])
        except Exception:
            pass
    ok = bool(status == 200 and markets and markets > 0)
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "status": status,
        "bytes": bytes_download,
        "elapsed_sec": elapsed,
        "json_len": json_len,
        "markets": markets,
        "stderr": (proc.stderr or "").strip()[:240],
    }


def send_telegram(text: str, env: dict[str, str]) -> None:
    token = env.get("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    api_base = env.get("TELEGRAM_API_BASE_URL") or os.getenv("TELEGRAM_API_BASE_URL") or "https://api.telegram.org"
    if not token or not chat_id:
        return
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(f"{api_base.rstrip('/')}/bot{token}/sendMessage", data=data, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:
        print(f"telegram_alert_failed={type(exc).__name__}:{exc}", file=sys.stderr)


def parse_candidates(raw: str | None) -> list[str]:
    if not raw:
        return DEFAULT_CANDIDATES
    return [item.strip() for item in raw.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail over local 1x market proxy nodes for Gamma health.")
    parser.add_argument("--controller", default=os.getenv("WEATHER_MARKET_PROXY_CONTROLLER", "http://127.0.0.1:9090"))
    parser.add_argument("--group", default=os.getenv("WEATHER_MARKET_PROXY_GROUP", DEFAULT_GROUP))
    parser.add_argument("--proxy", default=os.getenv("WEATHER_DATA_FEED_MARKET_PROXY") or os.getenv("WEATHER_PREDICT_MARKET_PROXY") or "http://127.0.0.1:7890")
    parser.add_argument("--snapshot-dir", type=Path, default=Path(os.getenv("WEATHER_DATA_FEED_SNAPSHOT_DIR", str(DEFAULT_SNAPSHOT_DIR))))
    parser.add_argument("--log", type=Path, default=Path(os.getenv("WEATHER_MARKET_PROXY_FAILOVER_LOG", str(DEFAULT_LOG))))
    parser.add_argument("--timeout-sec", type=int, default=int(os.getenv("WEATHER_MARKET_PROXY_PROBE_TIMEOUT_SEC", "5")))
    parser.add_argument("--candidates", default=os.getenv("WEATHER_MARKET_PROXY_1X_CANDIDATES", ""))
    parser.add_argument("--alert", action="store_true", default=os.getenv("WEATHER_MARKET_PROXY_ALERT", "1") == "1")
    parser.add_argument("--no-switch", action="store_true", help="Probe current node only.")
    args = parser.parse_args()

    env = load_env_file(ROOT / ".env")
    slug = latest_health_slug(args.snapshot_dir)
    candidates = parse_candidates(args.candidates)
    started = utc_now()
    attempts: list[dict[str, Any]] = []
    try:
        before = current_node(args.controller, args.group)
    except Exception as exc:
        row = {
            "ts_utc": started,
            "status": "controller_error",
            "error": f"{type(exc).__name__}: {exc}",
            "group": args.group,
            "proxy": args.proxy,
            "slug": slug,
        }
        append_jsonl(args.log, row)
        if args.alert:
            send_telegram(f"【weather proxy】controller error: {row['error']}", env)
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        return 2

    if args.no_switch:
        nodes = [before]
    elif before in candidates:
        nodes = [before] + [n for n in candidates if n != before]
    else:
        nodes = candidates
    selected = ""
    for node in nodes:
        if node:
            try:
                switch_node(args.controller, args.group, node)
                time.sleep(0.4)
            except Exception as exc:
                attempts.append({"node": node, "switch_error": f"{type(exc).__name__}: {exc}"})
                continue
        probe = probe_gamma(args.proxy, slug, args.timeout_sec)
        attempt = {"node": node, **probe}
        attempts.append(attempt)
        print(json.dumps({"attempt": attempt, "slug": slug}, ensure_ascii=False, sort_keys=True))
        if probe["ok"]:
            selected = node
            break

    row = {
        "ts_utc": started,
        "status": "ok" if selected else "failed",
        "group": args.group,
        "before_node": before,
        "selected_node": selected or current_node(args.controller, args.group),
        "proxy": args.proxy,
        "slug": slug,
        "attempts": attempts,
    }
    append_jsonl(args.log, row)

    if selected:
        if selected != before and args.alert:
            send_telegram(f"【weather proxy】market proxy switched: {before} -> {selected}", env)
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        return 0

    if args.alert:
        send_telegram(f"【weather proxy】all 1x nodes failed for Gamma; data-feed snapshot skipped. before={before}", env)
    print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
