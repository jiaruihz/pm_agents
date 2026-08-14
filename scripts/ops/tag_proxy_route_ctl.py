#!/usr/bin/env python3
"""Bounded host-level TAG node benchmark and failover control.

This controller owns only the node selected inside TAG's existing selector.  It
does not change macOS proxy settings, weather route keys, Clash profiles, or
consumer parameters.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml


SCHEMA_VERSION = "tag_proxy_route_control_v1"
DEFAULT_GROUP = "🙂 TAGSS"
DEFAULT_PROXY_URL = "http://127.0.0.1:7890"
DEFAULT_CONTROLLER_URL = "http://127.0.0.1:9090"
DEFAULT_TEST_URL = "https://api.openai.com/v1/models"
DEFAULT_POLYMARKET_GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
DEFAULT_TAG_CONFIG = (
    Path.home()
    / "Library/Application Support/com.tag.lab/mihomo/runtime.yaml"
)
DEFAULT_STATE_ROOT = (
    Path.home()
    / "Library/Application Support/pm_agents/tag_proxy_route_control"
)
ACCEPTED_OPENAI_STATUSES = {200, 401}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


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


def load_secret(config_path: Path) -> str:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    secret = str(payload.get("secret") or "")
    if not secret:
        raise RuntimeError(f"TAG controller secret missing: {config_path}")
    return secret


class TagController:
    def __init__(
        self,
        *,
        config_path: Path = DEFAULT_TAG_CONFIG,
        base_url: str = DEFAULT_CONTROLLER_URL,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = load_secret(config_path)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        timeout: float = 7.0,
    ) -> Any:
        data = None
        headers = {"Authorization": f"Bearer {self.secret}"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"TAG controller HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"TAG controller unavailable: {exc.reason}") from exc
        return json.loads(raw) if raw else None

    def proxies(self) -> dict[str, Any]:
        return (self.request("/proxies") or {}).get("proxies") or {}

    def switch(self, group: str, node: str) -> None:
        self.request(
            "/proxies/" + urllib.parse.quote(group, safe=""),
            method="PUT",
            body={"name": node},
        )

    def delay(
        self,
        node: str,
        *,
        test_url: str = DEFAULT_TEST_URL,
        timeout_ms: int = 4000,
    ) -> int | None:
        path = (
            "/proxies/"
            + urllib.parse.quote(node, safe="")
            + "/delay?timeout="
            + str(timeout_ms)
            + "&url="
            + urllib.parse.quote(test_url, safe="")
        )
        try:
            result = self.request(path, timeout=(timeout_ms / 1000) + 2)
        except RuntimeError:
            return None
        delay = (result or {}).get("delay")
        return int(delay) if isinstance(delay, (int, float)) and delay > 0 else None


def probe_openai(
    proxy_url: str = DEFAULT_PROXY_URL,
    *,
    attempts: int = 3,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _ in range(attempts):
        command = [
            "/usr/bin/curl",
            "--proxy",
            proxy_url,
            "--connect-timeout",
            "2",
            "--max-time",
            str(timeout),
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}\t%{time_total}",
            DEFAULT_TEST_URL,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 2)
        fields = (result.stdout or "").strip().split("\t")
        status = int(fields[0]) if fields and fields[0].isdigit() else 0
        total = float(fields[1]) if len(fields) == 2 else timeout
        rows.append(
            {
                "ok": result.returncode == 0 and status in ACCEPTED_OPENAI_STATUSES,
                "http_status": status,
                "total_sec": round(total, 4),
                "error": (result.stderr or "").strip()[:200],
            }
        )
    return rows


def probe_polymarket_geoblock(
    proxy_url: str = DEFAULT_PROXY_URL,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Return the venue's trading-region verdict for the effective TAG egress."""
    command = [
        "/usr/bin/curl",
        "--proxy",
        proxy_url,
        "--connect-timeout",
        "2",
        "--max-time",
        str(timeout),
        "-fsS",
        DEFAULT_POLYMARKET_GEOBLOCK_URL,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "blocked": None,
            "trading_allowed": False,
            "country": None,
            "region": None,
            "error": f"TimeoutExpired:{exc.timeout}",
        }
    try:
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
    except json.JSONDecodeError:
        payload = {}
    blocked = payload.get("blocked")
    ok = result.returncode == 0 and isinstance(blocked, bool)
    return {
        "ok": ok,
        "blocked": blocked if isinstance(blocked, bool) else None,
        "trading_allowed": bool(ok and blocked is False),
        "country": str(payload.get("country") or "") or None,
        "region": str(payload.get("region") or "") or None,
        "error": "" if ok else (result.stderr or "invalid geoblock response").strip()[:200],
    }


def probe_summary(rows: list[dict[str, Any]], *, slow_seconds: float) -> dict[str, Any]:
    successes = [float(row["total_sec"]) for row in rows if row["ok"]]
    failures = len(rows) - len(successes)
    maximum = max(successes) if successes else None
    average = round(sum(successes) / len(successes), 4) if successes else None
    majority_failures = failures >= (len(rows) // 2 + 1)
    degraded_reasons = []
    if majority_failures or not successes:
        degraded_reasons.append("majority_probe_failure")
    if average is not None and average > slow_seconds:
        degraded_reasons.append("sustained_slow_response")
    degraded = bool(degraded_reasons)
    return {
        "ok_count": len(successes),
        "failure_count": failures,
        "max_total_sec": maximum,
        "avg_total_sec": average,
        "slow_threshold_sec": slow_seconds,
        "degraded": degraded,
        "degraded_reasons": degraded_reasons,
        "samples": rows,
    }


def eligible_nodes(
    group: dict[str, Any],
    *,
    regions: tuple[str, ...] = ("JP", "HK", "SG", "DE"),
) -> list[str]:
    suffix = re.compile(r"\b(" + "|".join(re.escape(row) for row in regions) + r")\b")
    return [
        str(name)
        for name in (group.get("all") or [])
        if "1x" in str(name) and suffix.search(str(name))
    ]


def benchmark_nodes(
    controller: TagController,
    nodes: list[str],
    *,
    rounds: int = 3,
) -> list[dict[str, Any]]:
    def benchmark_one(node: str) -> dict[str, Any]:
        delays = [controller.delay(node) for _ in range(rounds)]
        good = [int(value) for value in delays if value is not None]
        failures = rounds - len(good)
        return {
            "node": node,
            "success_count": len(good),
            "failure_count": failures,
            "avg_delay_ms": round(sum(good) / len(good)) if good else None,
            "max_delay_ms": max(good) if good else None,
            "delays_ms": delays,
            "eligible": len(good) >= max(2, rounds - 1),
        }

    # Bound concurrency so a maintenance pass stays below its 120-second
    # schedule without turning the benchmark itself into a burst load test.
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(nodes)))) as executor:
        rows = list(executor.map(benchmark_one, nodes))
    return sorted(
        rows,
        key=lambda row: (
            not row["eligible"],
            row["failure_count"],
            row["avg_delay_ms"] if row["avg_delay_ms"] is not None else 10**9,
            row["node"],
        ),
    )


def group_snapshot(controller: TagController, group_name: str) -> tuple[dict[str, Any], str]:
    group = controller.proxies().get(group_name) or {}
    if str(group.get("type") or "").lower() != "selector":
        raise RuntimeError(f"TAG selector group missing: {group_name}")
    current = str(group.get("now") or "")
    if not current:
        raise RuntimeError(f"TAG selector has no current node: {group_name}")
    return group, current


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "failure_streak": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema_version": SCHEMA_VERSION, "failure_streak": 0}


def maintain(
    controller: TagController,
    *,
    state_root: Path = DEFAULT_STATE_ROOT,
    group_name: str = DEFAULT_GROUP,
    apply: bool,
    force_evaluate: bool = False,
    reason: str,
    slow_seconds: float = 1.5,
    cooldown_seconds: int = 900,
    required_degraded_cycles: int = 2,
) -> dict[str, Any]:
    state_path = state_root / "latest.json"
    lock_path = state_root / "maintain.lock"
    with exclusive_lock(lock_path) as acquired:
        if not acquired:
            return {"status": "already_running", "switched": False}

        group, current = group_snapshot(controller, group_name)
        probe = probe_summary(probe_openai(), slow_seconds=slow_seconds)
        geoblock = probe_polymarket_geoblock()
        blocked_region = geoblock.get("blocked") is True
        state = read_state(state_path)
        previous_node = str(state.get("current_node") or "")
        streak = int(state.get("failure_streak") or 0)
        if previous_node and previous_node != current:
            streak = 0
        streak = streak + 1 if probe["degraded"] else 0
        now_epoch = int(time.time())
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        audit_path = state_root / f"switches-{month}.jsonl"
        latest = {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": utc_now(),
            "current_node": current,
            "failure_streak": streak,
            "last_switch_epoch": int(state.get("last_switch_epoch") or 0),
            "probe": probe,
            "polymarket_geoblock": geoblock,
        }
        if apply:
            atomic_write_json(state_path, latest)
            append_jsonl(
                state_root / f"probes-{month}.jsonl",
                {
                    "schema_version": SCHEMA_VERSION,
                    "ts_utc": latest["generated_at_utc"],
                    "group": group_name,
                    "current_node": current,
                    "failure_streak": streak,
                    "ok_count": probe["ok_count"],
                    "failure_count": probe["failure_count"],
                    "avg_total_sec": probe["avg_total_sec"],
                    "max_total_sec": probe["max_total_sec"],
                    "degraded": probe["degraded"],
                    "polymarket_blocked": geoblock.get("blocked"),
                    "polymarket_country": geoblock.get("country"),
                },
            )

        preview = {
            "status": "healthy" if not probe["degraded"] else "degraded",
            "switched": False,
            "apply": apply,
            "group": group_name,
            "current_node": current,
            "failure_streak": streak,
            "probe": probe,
            "polymarket_geoblock": geoblock,
        }
        if not force_evaluate and not probe["degraded"] and not blocked_region:
            return preview
        if not force_evaluate and not blocked_region and streak < required_degraded_cycles:
            return {**preview, "status": "awaiting_confirmation_cycle"}
        elapsed = now_epoch - int(state.get("last_switch_epoch") or 0)
        if (
            not force_evaluate
            and not blocked_region
            and elapsed < cooldown_seconds
            and probe["ok_count"] > 0
        ):
            return {**preview, "status": "cooldown", "cooldown_remaining_sec": cooldown_seconds - elapsed}

        candidates = eligible_nodes(group)
        benchmark = benchmark_nodes(controller, candidates)
        selected_rows = [
            row
            for row in benchmark
            if row["eligible"] and row["node"] != current
        ][:12]
        decision = {**preview, "status": "switch_required", "benchmark": benchmark}
        if not selected_rows:
            return {**decision, "status": "no_healthy_candidate"}
        if not apply:
            return {**decision, "selected_node": selected_rows[0]["node"]}

        selected = ""
        post_probe: dict[str, Any] = {}
        switch_attempts = []
        for selected_row in selected_rows:
            attempted = str(selected_row["node"])
            controller.switch(group_name, attempted)
            time.sleep(0.5)
            candidate_probe = probe_summary(probe_openai(), slow_seconds=slow_seconds)
            candidate_geoblock = probe_polymarket_geoblock()
            accepted = (
                candidate_probe["failure_count"] == 0
                and not candidate_probe["degraded"]
                and candidate_geoblock["trading_allowed"]
            )
            switch_attempts.append(
                {
                    "node": attempted,
                    "accepted": accepted,
                    "probe": candidate_probe,
                    "polymarket_geoblock": candidate_geoblock,
                }
            )
            post_probe = candidate_probe
            if accepted:
                selected = attempted
                break
        accepted = bool(selected)
        rolled_back = False
        if not accepted:
            controller.switch(group_name, current)
            rolled_back = True
        event = {
            "schema_version": SCHEMA_VERSION,
            "ts_utc": utc_now(),
            "reason": reason,
            "group": group_name,
            "before_node": current,
            "selected_node": selected,
            "attempted_nodes": [row["node"] for row in switch_attempts],
            "rolled_back": rolled_back,
            "pre_probe": probe,
            "pre_polymarket_geoblock": geoblock,
            "post_probe": post_probe,
            "switch_attempts": switch_attempts,
            "benchmark": benchmark,
        }
        append_jsonl(audit_path, event)
        latest.update(
            {
                "generated_at_utc": utc_now(),
                "current_node": selected if accepted else current,
                "failure_streak": 0 if accepted else streak,
                "last_switch_epoch": now_epoch if accepted else int(state.get("last_switch_epoch") or 0),
                "last_switch": event,
                "polymarket_geoblock": (
                    switch_attempts[-1]["polymarket_geoblock"]
                    if accepted
                    else geoblock
                ),
            }
        )
        atomic_write_json(state_path, latest)
        return {
            **decision,
            "status": "switched" if accepted else "rolled_back",
            "switched": accepted,
            "selected_node": selected,
            "attempted_nodes": [row["node"] for row in switch_attempts],
            "post_probe": post_probe,
            "post_polymarket_geoblock": (
                switch_attempts[-1]["polymarket_geoblock"]
                if accepted
                else geoblock
            ),
            "switch_attempts": switch_attempts,
            "audit_path": str(audit_path),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_TAG_CONFIG)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--rounds", type=int, default=3)
    maintain_parser = sub.add_parser("maintain")
    maintain_parser.add_argument("--apply", action="store_true")
    maintain_parser.add_argument("--force-evaluate", action="store_true")
    maintain_parser.add_argument("--reason", required=True)
    maintain_parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    controller = TagController(config_path=args.config)
    if args.command == "status":
        group, current = group_snapshot(controller, args.group)
        result = {
            "group": args.group,
            "current_node": current,
            "eligible_1x_nodes": eligible_nodes(group),
            "probe": probe_summary(probe_openai(), slow_seconds=1.5),
            "polymarket_geoblock": probe_polymarket_geoblock(),
            "state": read_state(args.state_root / "latest.json"),
        }
    elif args.command == "benchmark":
        group, current = group_snapshot(controller, args.group)
        result = {
            "group": args.group,
            "current_node": current,
            "benchmark": benchmark_nodes(
                controller,
                eligible_nodes(group),
                rounds=max(2, args.rounds),
            ),
        }
    elif args.command == "maintain":
        result = maintain(
            controller,
            state_root=args.state_root,
            group_name=args.group,
            apply=bool(args.apply),
            force_evaluate=bool(args.force_evaluate),
            reason=str(args.reason),
        )
        if args.quiet:
            return 0 if result.get("status") not in {
                "rolled_back",
                "no_healthy_candidate",
            } else 1
    else:  # pragma: no cover
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"rolled_back", "no_healthy_candidate"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
