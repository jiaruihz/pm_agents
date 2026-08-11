#!/usr/bin/env python3
"""Single control-plane entrypoint for the weather market proxy endpoint."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


@contextmanager
def exclusive_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def gateway_overlay_files() -> tuple[dict[str, Any], list[tuple[Path, bytes]]]:
    spec = load_production_spec()
    root = spec.market_proxy_gateway_config_root
    if root is None:
        raise RuntimeError("market proxy gateway config root is not configured")
    profiles_path = root / "profiles.yaml"
    profiles_payload = yaml.safe_load(profiles_path.read_text(encoding="utf-8")) or {}
    current_uid = str(profiles_payload.get("current") or "")
    items = profiles_payload.get("items") or []
    active = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("uid") or "") == current_uid
        ),
        None,
    )
    if not active or str(active.get("name") or "") != "Allblue 加速器":
        raise RuntimeError("active Clash profile must be Allblue 加速器")
    option = active.get("option") or {}
    required = {name: str(option.get(name) or "") for name in ("merge", "proxies", "groups")}
    if not all(required.values()):
        raise RuntimeError("active Clash profile is missing enhancement file identities")

    paths = {name: root / "profiles" / f"{uid}.yaml" for name, uid in required.items()}
    payloads = {
        name: (yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        for name, path in paths.items()
    }
    stable_route = next(
        route for route in spec.market_proxy_routes if route.route_key == "stable"
    )
    upstream = urlparse(spec.market_proxy_stable_upstream_url or "")

    merge = payloads["merge"]
    listeners = [
        row
        for row in (merge.get("listeners") or [])
        if not isinstance(row, dict) or row.get("name") != "pm-stable-in"
    ]
    listeners.append(
        {
            "name": "pm-stable-in",
            "type": "mixed",
            "listen": "127.0.0.1",
            "port": urlparse(stable_route.proxy_url).port,
            "proxy": stable_route.clash_group,
        }
    )
    merge["listeners"] = listeners

    proxies = payloads["proxies"]
    proxy_prepend = [
        row
        for row in (proxies.get("prepend") or [])
        if not isinstance(row, dict) or row.get("name") != "TAG-LOCAL"
    ]
    proxy_prepend.insert(
        0,
        {
            "name": "TAG-LOCAL",
            "type": upstream.scheme,
            "server": upstream.hostname,
            "port": upstream.port,
        },
    )
    proxies.update(
        {
            "prepend": proxy_prepend,
            "append": proxies.get("append") or [],
            "delete": proxies.get("delete") or [],
        }
    )

    groups = payloads["groups"]
    group_prepend = [
        row
        for row in (groups.get("prepend") or [])
        if not isinstance(row, dict) or row.get("name") != stable_route.clash_group
    ]
    default_group = next(
        route.clash_group for route in spec.market_proxy_routes if route.route_key == "default"
    )
    group_prepend.insert(
        0,
        {
            "name": stable_route.clash_group,
            "type": "fallback",
            "proxies": ["TAG-LOCAL", default_group],
            "url": "https://clob.polymarket.com/time",
            "interval": 60,
            "lazy": False,
        },
    )
    groups.update(
        {
            "prepend": group_prepend,
            "append": groups.get("append") or [],
            "delete": groups.get("delete") or [],
        }
    )

    outputs = [
        (
            paths[name],
            yaml.safe_dump(payloads[name], allow_unicode=True, sort_keys=False).encode("utf-8"),
        )
        for name in ("merge", "proxies", "groups")
    ]
    plan = {
        "schema_version": "weather_market_proxy_gateway_overlay_v1",
        "active_profile_uid": current_uid,
        "active_profile_name": active.get("name"),
        "stable_route": stable_route.route_key,
        "stable_proxy_url": stable_route.proxy_url,
        "stable_group": stable_route.clash_group,
        "files": [
            {
                "path": str(path),
                "before_sha256": sha256_bytes(path.read_bytes()),
                "after_sha256": sha256_bytes(value),
                "changed": path.read_bytes() != value,
            }
            for path, value in outputs
        ],
    }
    return plan, outputs


def apply_gateway_overlay() -> dict[str, Any]:
    spec = load_production_spec()
    root = spec.market_proxy_gateway_config_root
    if root is None:
        raise RuntimeError("market proxy gateway config root is not configured")
    plan, outputs = gateway_overlay_files()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_root = root / "pm_agents_backups" / stamp
    for path, value in outputs:
        relative = path.relative_to(root)
        atomic_write_bytes(backup_root / relative, path.read_bytes())
        atomic_write_bytes(path, value)
    return {**plan, "applied": True, "backup_root": str(backup_root)}


def controller_json(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    spec = load_production_spec()
    socket = spec.market_proxy_controller_unix_socket
    if socket is None:
        raise RuntimeError("market proxy controller unix socket is not configured")
    command = [
        "/usr/bin/curl",
        "-sS",
        "--fail",
        "--unix-socket",
        str(socket),
        "--max-time",
        "5",
        "-X",
        method,
    ]
    if body is not None:
        command.extend(
            [
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                json.dumps(body, ensure_ascii=False),
            ]
        )
    command.append("http://localhost" + path)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "controller request failed").strip()
        )
    return json.loads(result.stdout) if result.stdout else None


def switch_group_node(group: str, node: str) -> None:
    controller_json(
        "/proxies/" + urllib.parse.quote(group, safe=""),
        method="PUT",
        body={"name": node},
    )


def route_status() -> dict[str, Any]:
    spec = load_production_spec()
    base = {
        "configured": True,
        "controller_unix_socket": str(spec.market_proxy_controller_unix_socket),
    }
    try:
        payload = controller_json("/proxies")
        proxies = payload.get("proxies") or {}
        routes = []
        for route in spec.market_proxy_routes:
            group = proxies.get(route.clash_group) or {}
            routes.append(
                {
                    "route_key": route.route_key,
                    "proxy_url": route.proxy_url,
                    "clash_group": route.clash_group,
                    "group_present": bool(group),
                    "group_type": group.get("type"),
                    "current_node": group.get("now"),
                    "probe": probe(route.proxy_url),
                }
            )
        return {
            **base,
            "reachable": True,
            "routes": routes,
            "healthy": all(row["group_present"] and row["probe"]["ok"] for row in routes),
        }
    except Exception as exc:  # noqa: BLE001 - status must surface local controller failure.
        return {
            **base,
            "reachable": False,
            "healthy": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _default_route_candidates(payload: dict[str, Any]) -> tuple[str, list[str]]:
    spec = load_production_spec()
    default_route = next(
        route for route in spec.market_proxy_routes if route.route_key == "default"
    )
    proxies = payload.get("proxies") or {}
    group = proxies.get(default_route.clash_group) or {}
    if str(group.get("type") or "").lower() != "selector":
        raise RuntimeError(
            f"default Clash group is not a selector: {default_route.clash_group}"
        )
    original = str(group.get("now") or "")

    def cached_delay(name: str) -> float:
        history = (proxies.get(name) or {}).get("history") or []
        delay = (history[-1].get("delay") if history else 0) or 0
        return float(delay) if isinstance(delay, (int, float)) and delay > 0 else float("inf")

    excluded = {"", original, "DIRECT", "REJECT", "PASS"}
    candidates = [
        str(name)
        for name in (group.get("all") or [])
        if str(name) not in excluded
    ]
    candidates.sort(key=lambda name: (cached_delay(name), name))
    return original, candidates


def maintain_default_route(*, apply: bool, reason: str, trigger: str) -> dict[str, Any]:
    spec = load_production_spec()
    default_route = next(
        route for route in spec.market_proxy_routes if route.route_key == "default"
    )
    initial_probes = [probe(default_route.proxy_url, timeout=5.0)]
    if initial_probes[-1]["ok"]:
        return {
            "status": "healthy",
            "switched": False,
            "route_key": "default",
            "initial_probes": initial_probes,
        }
    time.sleep(0.5)
    initial_probes.append(probe(default_route.proxy_url, timeout=5.0))
    if initial_probes[-1]["ok"]:
        return {
            "status": "recovered_before_switch",
            "switched": False,
            "route_key": "default",
            "initial_probes": initial_probes,
        }

    payload = controller_json("/proxies")
    original, candidates = _default_route_candidates(payload)
    preview = {
        "status": "switch_required",
        "switched": False,
        "route_key": "default",
        "group": default_route.clash_group,
        "original_node": original,
        "candidate_count": len(candidates),
        "bounded_candidate_count": min(12, len(candidates)),
        "initial_probes": initial_probes,
        "apply": apply,
    }
    if not apply:
        return preview

    lock_path = spec.data_feed_runtime_root / "config/market_proxy_route_maintenance.lock"
    with exclusive_lock(lock_path) as acquired:
        if not acquired:
            return {**preview, "status": "already_running"}
        locked_probe = probe(default_route.proxy_url, timeout=5.0)
        if locked_probe["ok"]:
            return {
                **preview,
                "status": "recovered_before_switch",
                "locked_probe": locked_probe,
            }
        attempts: list[dict[str, Any]] = []
        selected = ""
        for node in candidates[:12]:
            try:
                switch_group_node(default_route.clash_group, node)
                time.sleep(0.4)
                result = probe(default_route.proxy_url, timeout=5.0)
                attempts.append({"node": node, "probe": result})
                if result["ok"]:
                    selected = node
                    break
            except Exception as exc:  # noqa: BLE001 - try the next declared candidate.
                attempts.append(
                    {"node": node, "error": f"{type(exc).__name__}: {exc}"}
                )
        restored = False
        if not selected and original:
            try:
                switch_group_node(default_route.clash_group, original)
                restored = True
            except Exception as exc:  # noqa: BLE001 - preserve failed rollback evidence.
                attempts.append(
                    {
                        "node": original,
                        "restore_error": f"{type(exc).__name__}: {exc}",
                    }
                )
        audit = {
            "schema_version": "weather_market_proxy_route_switch_v1",
            "ts_utc": utc_now(),
            "reason": reason,
            "trigger": trigger,
            "route_key": "default",
            "group": default_route.clash_group,
            "before_node": original,
            "selected_node": selected,
            "restored_original": restored,
            "attempts": attempts,
        }
        audit_path = (
            spec.data_feed_runtime_root
            / "output/market_proxy_control/route_switches.jsonl"
        )
        append_jsonl(audit_path, audit)
        if not selected:
            raise RuntimeError(f"no healthy default route candidate: {audit}")
        return {
            **preview,
            "status": "switched",
            "switched": True,
            "selected_node": selected,
            "attempts": attempts,
            "audit_path": str(audit_path),
        }
def validate_proxy_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "socks5", "socks5h"} or not parsed.hostname or not parsed.port:
        raise ValueError(f"invalid proxy URL: {value}")
    return value.strip()


def read_state() -> dict:
    spec = load_production_spec()
    path = spec.market_proxy_state_path
    default_route = next(
        route for route in spec.market_proxy_routes if route.route_key == "default"
    )
    canonical_url = validate_proxy_url(default_route.proxy_url)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        legacy_url = validate_proxy_url(str(payload["proxy_url"]))
        payload["legacy_proxy_url"] = legacy_url
        payload["legacy_state_drift"] = legacy_url != canonical_url
        payload["proxy_url"] = canonical_url
        payload["source"] = "production_named_default_route"
        return payload
    return {
        "proxy_url": canonical_url,
        "source": "production_named_default_route",
        "legacy_state_drift": False,
    }


def probe(proxy_url: str, timeout: float = 8.0) -> dict:
    checks = []
    for name, url in (
        ("gamma", "https://gamma-api.polymarket.com/events?limit=1"),
        ("clob", "https://clob.polymarket.com/time"),
    ):
        command = [
            "curl", "-sS", "--proxy", proxy_url, "--max-time", str(timeout),
            "-o", "/dev/null", "-w", "%{http_code}", url,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        status = (result.stdout or "").strip()
        checks.append({
            "name": name,
            "url": url,
            "ok": result.returncode == 0 and status == "200",
            "http_status": status,
            "returncode": result.returncode,
            "error": (result.stderr or "").strip()[:240],
        })
    return {"ok": all(row["ok"] for row in checks), "checks": checks}


def consumers() -> list:
    spec = load_production_spec()
    rows = [item for item in spec.managed_runtimes if item.uses_market_proxy]
    return sorted(rows, key=lambda row: (row.instance_id != "weather_market_books", row.expected_live, row.instance_id))


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


def status_payload() -> dict:
    state = read_state()
    url = state["proxy_url"]
    return {"state": state, "probe": probe(url),
            "route_control": route_status(),
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
    gateway = sub.add_parser("gateway-overlay")
    gateway.add_argument("--apply", action="store_true")
    gateway.add_argument("--confirm-network-change", action="store_true")
    maintain = sub.add_parser("maintain")
    maintain.add_argument("--apply", action="store_true")
    maintain.add_argument("--confirm-live", action="store_true")
    maintain.add_argument("--reason", required=True)
    maintain.add_argument("--trigger", default="manual")
    args = parser.parse_args()
    if args.command == "gateway-overlay":
        if args.apply:
            if not args.confirm_network_change:
                raise SystemExit(
                    "gateway overlay changes persistent local network routing; "
                    "--confirm-network-change is required"
                )
            payload = apply_gateway_overlay()
        else:
            payload, _ = gateway_overlay_files()
            payload["applied"] = False
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "maintain":
        if args.apply and any(item.expected_live for item in consumers()) and not args.confirm_live:
            raise SystemExit("route maintenance affects live traffic; --confirm-live is required")
        maintenance = maintain_default_route(
            apply=bool(args.apply),
            reason=str(args.reason),
            trigger=str(args.trigger),
        )
        payload = status_payload()
        payload["maintenance"] = maintenance
        payload["health_artifact"] = str(publish_health(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        route_control = payload["route_control"]
        return 0 if maintenance["status"] in {
            "healthy",
            "recovered_before_switch",
            "switched",
            "already_running",
        } and route_control.get("healthy") else 1
    if args.command == "status":
        payload = status_payload()
        payload["health_artifact"] = str(publish_health(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        chain = payload["chain_health"]
        route_control = payload["route_control"]
        return 0 if (payload["probe"]["ok"] and not payload["process_mismatches"]
                     and route_control.get("healthy")
                     and chain["manifest_status"] == "healthy"
                     and not chain["blocking_consumers"]) else 1

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
