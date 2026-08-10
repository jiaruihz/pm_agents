#!/usr/bin/env python3
"""Single control-plane entrypoint for the weather market proxy endpoint."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def load_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def controller_secret() -> str:
    spec = load_production_spec()
    failover = spec.market_proxy_failover
    if failover is None:
        return ""
    value = os.getenv(failover.controller_secret_env, "").strip()
    if value:
        return value
    if spec.canonical_refresh_checkout_root is not None:
        value = load_env_value(
            spec.canonical_refresh_checkout_root / ".env",
            failover.controller_secret_env,
        )
        if value:
            return value
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-w",
            "-s",
            failover.controller_secret_keychain_service,
            "-a",
            failover.controller_secret_keychain_account,
        ],
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode == 0:
        return (result.stdout or "").strip()
    return ""


def controller_json(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    spec = load_production_spec()
    failover = spec.market_proxy_failover
    if failover is None:
        raise RuntimeError("market proxy node failover is not configured")
    secret = controller_secret()
    if not secret:
        raise RuntimeError(
            f"missing controller secret in {failover.controller_secret_env}"
        )
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {secret}"}
    request = urllib.request.Request(
        failover.controller_url.rstrip("/") + path,
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else None


def node_control_status() -> dict[str, Any]:
    spec = load_production_spec()
    failover = spec.market_proxy_failover
    if failover is None:
        return {"configured": False, "enabled": False, "reachable": False}
    base = {
        "configured": True,
        "enabled": failover.enabled,
        "controller_url": failover.controller_url,
        "group": failover.group,
    }
    try:
        payload = controller_json("/proxies")
        group = (payload.get("proxies") or {}).get(failover.group) or {}
        candidates = [str(item) for item in (group.get("all") or []) if str(item)]
        if not group:
            raise RuntimeError(f"proxy group not found: {failover.group}")
        return {
            **base,
            "reachable": True,
            "current_node": str(group.get("now") or ""),
            "candidate_count": len(candidates),
            "candidates": candidates,
        }
    except Exception as exc:  # noqa: BLE001 - surface controller state without secret.
        return {
            **base,
            "reachable": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def switch_node(node: str) -> None:
    failover = load_production_spec().market_proxy_failover
    if failover is None:
        raise RuntimeError("market proxy node failover is not configured")
    controller_json(
        "/proxies/" + urllib.parse.quote(failover.group, safe=""),
        method="PUT",
        body={"name": node},
    )


@contextmanager
def failover_lock(path: Path) -> Iterator[bool]:
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


def confirmed_proxy_failure(proxy_url: str) -> tuple[bool, list[dict[str, Any]]]:
    failover = load_production_spec().market_proxy_failover
    confirmations = failover.failure_confirmations if failover is not None else 1
    timeout = failover.probe_timeout_sec if failover is not None else 8.0
    checks: list[dict[str, Any]] = []
    for index in range(confirmations):
        result = probe(proxy_url, timeout=timeout)
        checks.append(result)
        if result["ok"]:
            return False, checks
        if index + 1 < confirmations:
            time.sleep(0.5)
    return True, checks


def recover_node(*, apply: bool, reason: str, trigger: str) -> dict[str, Any]:
    spec = load_production_spec()
    failover = spec.market_proxy_failover
    if failover is None:
        raise RuntimeError("market proxy node failover is not configured")
    proxy_url = read_state()["proxy_url"]
    initial_failed, initial_probes = confirmed_proxy_failure(proxy_url)
    preview: dict[str, Any] = {
        "action": "maintain_node",
        "reason": reason,
        "trigger": trigger,
        "proxy_url": proxy_url,
        "initial_probes": initial_probes,
        "apply": apply,
    }
    if not initial_failed:
        return {**preview, "status": "healthy", "switched": False}
    control = node_control_status()
    preview["node_control"] = control
    if not apply:
        return {**preview, "status": "switch_required", "switched": False}
    if not failover.enabled:
        raise RuntimeError("market proxy automatic node failover is disabled")
    if not control.get("reachable"):
        raise RuntimeError(f"market proxy node controller unavailable: {control}")

    with failover_lock(failover.lock_path) as acquired:
        if not acquired:
            return {**preview, "status": "already_running", "switched": False}
        failed_under_lock, locked_probes = confirmed_proxy_failure(proxy_url)
        if not failed_under_lock:
            return {
                **preview,
                "status": "recovered_before_switch",
                "switched": False,
                "locked_probes": locked_probes,
            }
        control = node_control_status()
        if not control.get("reachable"):
            raise RuntimeError(f"market proxy node controller unavailable: {control}")
        original = str(control.get("current_node") or "")
        candidates = [
            str(item)
            for item in (control.get("candidates") or [])
            if str(item) and str(item) != original and str(item) not in {"DIRECT", "REJECT"}
        ]
        attempts: list[dict[str, Any]] = []
        selected = ""
        for node in candidates:
            try:
                switch_node(node)
                time.sleep(failover.settle_sec)
                result = probe(proxy_url, timeout=failover.probe_timeout_sec)
                attempts.append({"node": node, "probe": result})
                if result["ok"]:
                    selected = node
                    break
            except Exception as exc:  # noqa: BLE001 - continue through declared candidates.
                attempts.append(
                    {"node": node, "error": f"{type(exc).__name__}: {exc}"}
                )
        restored = False
        if not selected and original:
            try:
                switch_node(original)
                restored = True
            except Exception as exc:  # noqa: BLE001 - report failed rollback explicitly.
                attempts.append(
                    {
                        "node": original,
                        "restore_error": f"{type(exc).__name__}: {exc}",
                    }
                )
        row = {
            "schema_version": "weather_market_proxy_node_failover_v1",
            "ts_utc": utc_now(),
            "status": "switched" if selected else "failed",
            "reason": reason,
            "trigger": trigger,
            "proxy_url": proxy_url,
            "before_node": original,
            "selected_node": selected,
            "restored_original": restored,
            "attempts": attempts,
        }
        append_jsonl(failover.audit_path, row)
        atomic_write_json(failover.state_path, row)
        if not selected:
            raise RuntimeError(f"no healthy market proxy node: {row}")
        return {
            **preview,
            "status": "switched",
            "switched": True,
            "before_node": original,
            "selected_node": selected,
            "attempts": attempts,
            "locked_probes": locked_probes,
            "audit_path": str(failover.audit_path),
        }


def consumers() -> list:
    spec = load_production_spec()
    rows = [item for item in spec.managed_runtimes if item.uses_market_proxy]
    return sorted(rows, key=lambda row: (row.instance_id != "weather_market_books", row.expected_live, row.instance_id))


def restart_consumer(instance, *, proxy_url: str, reason: str, confirm_live: bool) -> dict:
    command = [sys.executable, str(ROOT / "scripts/ops/weather_production_ctl.py"),
               "restart", "--apply", "--json", "--instance", instance.instance_id, "--reason", reason]
    if instance.expected_live:
        if not confirm_live:
            raise RuntimeError(f"--confirm-live required for {instance.instance_id}")
        command.append("--confirm-live")
    env = os.environ.copy()
    env["WEATHER_DATA_FEED_MARKET_PROXY"] = proxy_url
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    try:
        controller = json.loads(result.stdout)
    except json.JSONDecodeError:
        controller = {}
    action = (controller.get("actions") or [{}])[0]
    return {"instance_id": instance.instance_id, "returncode": result.returncode,
            "action_status": action.get("status"),
            "controller_global_status": controller.get("status"),
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
    spec = load_production_spec()
    state = read_state()
    url = state["proxy_url"]
    failover_state: dict[str, Any] = {}
    if spec.market_proxy_failover is not None and spec.market_proxy_failover.state_path.exists():
        try:
            loaded = json.loads(spec.market_proxy_failover.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                failover_state = loaded
        except Exception as exc:  # noqa: BLE001 - status must surface corrupt state.
            failover_state = {"parse_error": f"{type(exc).__name__}: {exc}"}
    return {"state": state, "probe": probe(url),
            "node_control": node_control_status(),
            "node_failover_state": failover_state,
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
    auto.add_argument("--candidates", nargs="+")
    auto.add_argument("--apply", action="store_true")
    auto.add_argument("--confirm-live", action="store_true")
    auto.add_argument("--reason", required=True)
    maintain = sub.add_parser("maintain-node")
    maintain.add_argument("--apply", action="store_true")
    maintain.add_argument("--confirm-live", action="store_true")
    maintain.add_argument("--reason", required=True)
    maintain.add_argument("--trigger", default="manual")
    args = parser.parse_args()
    if args.command == "status":
        payload = status_payload()
        payload["health_artifact"] = str(publish_health(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        chain = payload["chain_health"]
        node_control = payload["node_control"]
        return 0 if (payload["probe"]["ok"] and not payload["process_mismatches"]
                     and (not node_control.get("enabled") or node_control.get("reachable"))
                     and chain["manifest_status"] == "healthy"
                     and not chain["blocking_consumers"]) else 1

    if args.command == "maintain-node":
        if args.apply and any(x.expected_live for x in consumers()) and not args.confirm_live:
            raise SystemExit("node failover affects live traffic; --confirm-live is required")
        result = recover_node(
            apply=bool(args.apply),
            reason=str(args.reason),
            trigger=str(args.trigger),
        )
        payload = status_payload()
        payload["maintenance"] = result
        payload["health_artifact"] = str(publish_health(payload))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        node_control = payload["node_control"]
        return 0 if result["status"] in {
            "healthy", "switched", "recovered_before_switch", "already_running"
        } and (not node_control.get("enabled") or node_control.get("reachable")) else 1

    if args.command == "auto":
        candidate_urls = args.candidates or [read_state()["proxy_url"]]
        tested = [(validate_proxy_url(url), probe(validate_proxy_url(url))) for url in candidate_urls]
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
