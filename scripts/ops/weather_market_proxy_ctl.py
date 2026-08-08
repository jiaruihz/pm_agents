#!/usr/bin/env python3
"""Single control-plane entrypoint for the weather market proxy endpoint."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_proxy_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "socks5", "socks5h"} or not parsed.hostname or not parsed.port:
        raise ValueError(f"invalid proxy URL: {value}")
    return value.strip()


def read_state() -> dict:
    spec = load_production_spec()
    path = spec.market_proxy_state_path
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["proxy_url"] = validate_proxy_url(str(payload["proxy_url"]))
        return payload
    return {"proxy_url": validate_proxy_url(spec.market_proxy_default_url), "source": "production_default"}


def write_state(proxy_url: str, *, reason: str, previous_url: str) -> None:
    spec = load_production_spec()
    path = spec.market_proxy_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "weather_market_proxy_state_v1",
        "proxy_url": validate_proxy_url(proxy_url),
        "previous_url": previous_url,
        "reason": reason,
        "updated_at_utc": utc_now(),
        "updated_by": os.getenv("USER") or "unknown",
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def probe(proxy_url: str, timeout: float = 8.0) -> dict:
    command = [
        "curl", "-sS", "--proxy", proxy_url, "--max-time", str(timeout),
        "-o", "/dev/null", "-w", "%{http_code}",
        "https://gamma-api.polymarket.com/events?limit=1",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    status = (result.stdout or "").strip()
    return {"ok": result.returncode == 0 and status == "200", "http_status": status,
            "returncode": result.returncode, "error": (result.stderr or "").strip()[:240]}


def consumers() -> list:
    spec = load_production_spec()
    rows = [item for item in spec.managed_runtimes if item.uses_market_proxy]
    return sorted(rows, key=lambda row: (row.instance_id != "weather_market_books", row.expected_live, row.instance_id))


def restart_consumer(instance, *, proxy_url: str, reason: str, confirm_live: bool) -> dict:
    command = [sys.executable, str(ROOT / "scripts/ops/weather_production_ctl.py"),
               "restart", "--apply", "--instance", instance.instance_id, "--reason", reason]
    if instance.expected_live:
        if not confirm_live:
            raise RuntimeError(f"--confirm-live required for {instance.instance_id}")
        command.append("--confirm-live")
    env = os.environ.copy()
    env.update({
        "WEATHER_DATA_FEED_MARKET_PROXY": proxy_url,
        "WEATHER_PREDICT_MARKET_PROXY": proxy_url,
        "POLYMARKET_PROXY_URL": proxy_url,
        "LOW_PRICE_YES_INTEGRATED_TAIL_MARKET_PROXY": proxy_url,
        "WEATHER_KNMI_LADDER_MARKET_PROXY": proxy_url,
        "WEATHER_KOREA_FIRST_SEEN_MARKET_PROXY": proxy_url,
        "WEATHER_EVENT_LADDER_MARKET_PROXY": proxy_url,
        "WEATHER_STALE_BOOK_MARKET_PROXY": proxy_url,
    })
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    return {"instance_id": instance.instance_id, "returncode": result.returncode,
            "stdout_tail": (result.stdout or "")[-1000:], "stderr_tail": (result.stderr or "")[-1000:]}


def process_mismatches(proxy_url: str) -> list[str]:
    result = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True, check=True)
    mismatches = []
    for line in result.stdout.splitlines():
        if "weather" not in line and "low_price" not in line:
            continue
        if "--market-proxy " in line or "--book-proxy " in line:
            if proxy_url not in line:
                mismatches.append(line.strip())
    return mismatches


def chain_health() -> dict:
    command = [sys.executable, str(ROOT / "scripts/ops/weather_production_ctl.py"), "health", "--json"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    wanted = {item.instance_id for item in consumers()}
    all_rows = payload.get("runtimes", [])
    runtimes = {row["instance_id"]: row for row in all_rows if row["instance_id"] in wanted}
    required_fresh = {"weather_market_books"} | {
        item.instance_id for item in consumers() if item.expected_live
    }
    blocking = {}
    consumer_health = {}
    for name, row in runtimes.items():
        process_ok = bool(row.get("present"))
        artifact_ok = row.get("status") == "healthy"
        verification_mode = "artifact_fresh" if name in required_fresh else "process_and_proxy_binding"
        ok = process_ok and (artifact_ok if name in required_fresh else True)
        consumer_health[name] = {
            "ok": ok,
            "verification_mode": verification_mode,
            "process_present": process_ok,
            "artifact_status": row.get("status"),
            "artifact_age_sec": row.get("health_age_sec"),
            "issues": row.get("issues") or [],
        }
        if not ok:
            blocking[name] = row.get("issues") or [row.get("status")]
    full_runtime_health = {
        row["instance_id"]: {
            "role": row.get("role"),
            "execution_mode": row.get("execution_mode"),
            "health_trigger": (
                "process_plus_artifact_or_http_freshness"
                if row.get("health_path") or row.get("health_url")
                else "process_presence"
            ),
            "status": row.get("status"),
            "process_present": bool(row.get("present")),
            "artifact_age_sec": row.get("health_age_sec"),
            "dependencies": row.get("dependencies") or [],
            "issues": row.get("issues") or [],
            "uses_market_proxy": row["instance_id"] in wanted,
        }
        for row in all_rows
    }
    return {"manifest_status": payload.get("manifest_status"), "blocking_consumers": blocking,
            "consumer_health": consumer_health, "consumer_count": len(runtimes),
            "full_runtime_health": full_runtime_health,
            "full_runtime_count": len(full_runtime_health)}


def wait_for_chain(proxy_url: str, *, not_before: float, timeout_sec: float = 360.0) -> dict:
    spec = load_production_spec()
    deadline = time.monotonic() + timeout_sec
    latest = {}
    while time.monotonic() < deadline:
        books = spec.resolved_market_books_root() / "latest.json"
        books_fresh = books.exists() and books.stat().st_mtime >= not_before
        latest = {"probe": probe(proxy_url), "process_mismatches": process_mismatches(proxy_url),
                  "chain_health": chain_health(), "market_books_post_switch": books_fresh}
        if (latest["probe"]["ok"] and not latest["process_mismatches"] and books_fresh
                and latest["chain_health"]["manifest_status"] == "healthy"
                and not latest["chain_health"]["blocking_consumers"]):
            return latest
        time.sleep(5)
    raise RuntimeError(f"full-chain verification timed out: {latest}")


def status_payload() -> dict:
    state = read_state()
    url = state["proxy_url"]
    return {"state": state, "probe": probe(url),
            "consumers": [item.instance_id for item in consumers()],
            "process_mismatches": process_mismatches(url),
            "chain_health": chain_health()}


def publish_health(payload: dict) -> Path:
    spec = load_production_spec()
    path = spec.data_feed_runtime_root / "output/market_proxy_control/latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    enriched = {"schema_version": "weather_market_proxy_health_v1",
                "generated_at_utc": utc_now(), **payload}
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(enriched, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    switch = sub.add_parser("switch")
    switch.add_argument("proxy_url")
    switch.add_argument("--apply", action="store_true")
    switch.add_argument("--confirm-live", action="store_true")
    switch.add_argument("--reason", required=True)
    auto = sub.add_parser("auto")
    auto.add_argument("--candidates", nargs="+", default=["http://127.0.0.1:7890", "http://127.0.0.1:7897"])
    auto.add_argument("--apply", action="store_true")
    auto.add_argument("--confirm-live", action="store_true")
    auto.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "status":
        payload = status_payload()
        payload["health_artifact"] = str(publish_health(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        chain = payload["chain_health"]
        return 0 if (payload["probe"]["ok"] and not payload["process_mismatches"]
                     and chain["manifest_status"] == "healthy"
                     and not chain["blocking_consumers"]) else 1

    if args.command == "auto":
        tested = [(validate_proxy_url(url), probe(validate_proxy_url(url))) for url in args.candidates]
        target = next((url for url, result in tested if result["ok"]), "")
        if not target:
            raise SystemExit(f"no healthy proxy candidate: {tested}")
    else:
        target = validate_proxy_url(args.proxy_url)
    target_probe = probe(target)
    current = read_state()["proxy_url"]
    preview = {"action": "switch", "from": current, "to": target,
               "probe": target_probe, "consumers": [x.instance_id for x in consumers()]}
    if not args.apply:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0 if target_probe["ok"] else 1
    if not target_probe["ok"]:
        raise SystemExit(f"target proxy probe failed: {target_probe}")
    if any(x.expected_live for x in consumers()) and not args.confirm_live:
        raise SystemExit("switch affects live runtimes; --confirm-live is required")

    switched_at = time.time()
    write_state(target, reason=args.reason, previous_url=current)
    restarted = []
    try:
        for instance in consumers():
            restarted.append(restart_consumer(instance, proxy_url=target, reason=args.reason,
                                                confirm_live=args.confirm_live))
            time.sleep(1)
        final = wait_for_chain(target, not_before=switched_at)
    except Exception:
        write_state(current, reason=f"rollback:{args.reason}", previous_url=target)
        for instance in consumers():
            restart_consumer(instance, proxy_url=current, reason=f"rollback:{args.reason}",
                             confirm_live=args.confirm_live)
        raise
    result = {**preview, "status": "ok", "restarted": restarted,
              "verification": final}
    result["health_artifact"] = str(publish_health(status_payload()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
