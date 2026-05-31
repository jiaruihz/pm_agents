#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.notification.telegram import send_telegram_message_sync
from src.strategies.weather_edge_v1.tools.live_state import read_live_state

DEFAULT_MARKET_DATA = ROOT / "runtime" / "weather_edge_v1" / "market_data"
DEFAULT_STATE_DIR = ROOT / "runtime" / "weather_edge_v1" / "live_cycle"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _latest_snapshot(snapshot_dir: Path) -> Optional[Path]:
    files = sorted(snapshot_dir.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _file_age_seconds(path: Path) -> Optional[float]:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _read_pid(path: Path) -> Optional[int]:
    try:
        text = path.read_text(encoding="utf-8").strip()
        pid = int(text)
    except Exception:
        return None
    return pid if pid > 0 else None


def _pid_running(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _running_pid_files(paths: List[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        pid = _read_pid(path)
        rows.append(
            {
                "pid_file": str(path),
                "pid": pid,
                "ok": _pid_running(pid),
            }
        )
    return rows


def _http_check(name: str, url: str, timeout: float) -> Dict[str, Any]:
    started = time.time()
    try:
        resp = requests.head(url, timeout=timeout, headers={"User-Agent": "pm-agent-weather-doctor/1.0"})
        status = int(resp.status_code)
        return {"name": name, "ok": 200 <= status < 500, "status": status, "elapsed_sec": round(time.time() - started, 3)}
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            "elapsed_sec": round(time.time() - started, 3),
        }


def _dns_check(host: str) -> Dict[str, Any]:
    started = time.time()
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addrs = sorted({row[4][0] for row in infos if row and row[4]})
        return {"host": host, "ok": bool(addrs), "addresses": addrs[:6], "elapsed_sec": round(time.time() - started, 3)}
    except Exception as exc:
        return {
            "host": host,
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            "elapsed_sec": round(time.time() - started, 3),
        }


def _run(cmd: List[str], timeout: int) -> Dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "elapsed_sec": round(time.time() - started, 3),
            "output": proc.stdout[-4000:],
        }
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_sec": round(time.time() - started, 3),
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        }


def _doctor(args: argparse.Namespace) -> Dict[str, Any]:
    market_data = Path(args.market_data)
    state_dir = Path(args.state_dir)
    snapshot = _latest_snapshot(market_data / "paper_snapshots")
    snapshot_age = _file_age_seconds(snapshot) if snapshot else None
    max_snapshot_age = float(args.max_snapshot_age_minutes) * 60.0

    live_loop_files = sorted(state_dir.glob("live_*.pid")) + [state_dir / "daemon.pid"]
    live_loops = _running_pid_files(live_loop_files)
    telegram_pid = _read_pid(state_dir / "telegram_control.pid")
    checks: Dict[str, Any] = {
        "snapshot": {
            "ok": bool(snapshot and snapshot_age is not None and snapshot_age <= max_snapshot_age),
            "path": str(snapshot) if snapshot else "",
            "age_minutes": round((snapshot_age or 0.0) / 60.0, 2) if snapshot_age is not None else None,
            "max_age_minutes": float(args.max_snapshot_age_minutes),
        },
        "live_state": read_live_state(state_dir),
        "live_loop": {
            "ok": any(row.get("ok") for row in live_loops),
            "loops": live_loops,
        },
        "telegram_control": {
            "ok": _pid_running(telegram_pid),
            "pid": telegram_pid,
        },
        "dns": [
            _dns_check("gamma-api.polymarket.com"),
            _dns_check("clob.polymarket.com"),
            _dns_check("api.telegram.org"),
        ],
        "http": [
            _http_check("gamma", "https://gamma-api.polymarket.com/markets?limit=1", float(args.http_timeout)),
            _http_check("clob", "https://clob.polymarket.com", float(args.http_timeout)),
            _http_check("telegram", "https://api.telegram.org", float(args.http_timeout)),
        ],
    }
    if args.sync_dry_run:
        checks["sync_dry_run"] = _run(["bash", "scripts/ops/sync_weather_remote.sh", "--dry-run"], timeout=120)

    failures: List[str] = []
    if not checks["snapshot"]["ok"]:
        failures.append("snapshot_stale_or_missing")
    if args.require_live_loop and not checks["live_loop"]["ok"]:
        failures.append("live_loop_not_running")
    if args.require_telegram_control and not checks["telegram_control"]["ok"]:
        failures.append("telegram_control_not_running")
    failures.extend(f"dns_{row['host']}" for row in checks["dns"] if not row.get("ok"))
    failures.extend(f"http_{row['name']}" for row in checks["http"] if not row.get("ok"))
    if checks.get("sync_dry_run") and not checks["sync_dry_run"].get("ok"):
        failures.append("sync_dry_run_failed")

    return {
        "record_type": "weather_live_doctor",
        "checked_at_utc": _iso(_utc_now()),
        "ok": not failures,
        "failures": failures,
        "checks": checks,
    }


def _message(report: Dict[str, Any]) -> str:
    if report.get("ok"):
        return "【天气策略健康检查】通过。"
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    snapshot = checks.get("snapshot") if isinstance(checks.get("snapshot"), dict) else {}
    lines = [
        "【天气策略健康检查】失败",
        "",
        "失败项：" + ", ".join(str(x) for x in report.get("failures", [])),
    ]
    if snapshot:
        lines.append(f"最新快照：{snapshot.get('path') or '-'}，age={snapshot.get('age_minutes')} min")
    for row in checks.get("http", []):
        if isinstance(row, dict) and not row.get("ok"):
            lines.append(f"{row.get('name')}：{row.get('error') or row.get('status')}，耗时 {row.get('elapsed_sec')}s")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Health-check weather live trading runtime.")
    parser.add_argument("--market-data", default=str(DEFAULT_MARKET_DATA))
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--max-snapshot-age-minutes", type=float, default=75.0)
    parser.add_argument("--http-timeout", type=float, default=6.0)
    parser.add_argument("--sync-dry-run", action="store_true")
    parser.add_argument("--require-live-loop", action="store_true")
    parser.add_argument("--require-telegram-control", action="store_true")
    parser.add_argument("--telegram-on-fail", action="store_true")
    return parser


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass
    args = _parser().parse_args()
    report = _doctor(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.telegram_on_fail and not report.get("ok"):
        try:
            send_telegram_message_sync(_message(report))
        except Exception as exc:
            print(f"[WARN] telegram alert failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
