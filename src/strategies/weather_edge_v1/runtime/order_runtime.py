from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping


def json_ready(value: Any) -> Any:
    """Return a JSON-serializable representation for strategy runtime ledgers."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(v) for v in value]
    if hasattr(value, "item"):
        try:
            return json_ready(value.item())
        except Exception:
            pass
    try:
        if math.isnan(value):  # type: ignore[arg-type]
            return None
    except Exception:
        pass
    return str(value)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_ready(dict(payload)), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(json_ready(dict(row)), ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_ready(dict(row)), ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def parse_executor_json(stdout: str) -> dict[str, Any] | None:
    try:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(stdout[start : end + 1])
            return payload if isinstance(payload, dict) else None
    except Exception:
        return None
    return None


def executor_proxy_env(proxy_url: str = "") -> dict[str, str]:
    env = os.environ.copy()
    proxy = str(proxy_url or "").strip()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["ALL_PROXY"] = proxy
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
    return env


def weather_order_executor_cmd(
    *,
    root: Path,
    plans_path: Path,
    paper_out: Path,
    live_out: Path,
    live: bool,
    confirm_live: bool,
    allow_taker: bool = False,
    cancel_expired: bool = False,
    no_telegram: bool = True,
    python_executable: str | None = None,
) -> list[str]:
    py = python_executable or sys.executable
    cmd = [
        py,
        "scripts/ops/weather_order_executor.py",
        "--plans",
        str(plans_path),
        "--paper-out",
        str(paper_out),
        "--live-out",
        str(live_out),
    ]
    if live:
        cmd.extend(["--live", "--confirm-live"])
    if allow_taker:
        cmd.append("--allow-taker")
    if cancel_expired:
        cmd.append("--cancel-expired")
    if no_telegram:
        cmd.append("--no-telegram")
    return cmd


def run_weather_order_executor(
    *,
    root: Path,
    plans_path: Path,
    paper_out: Path,
    live_out: Path,
    live: bool,
    confirm_live: bool,
    allow_taker: bool = False,
    cancel_expired: bool = False,
    no_telegram: bool = True,
    timeout_sec: float = 180.0,
    env: dict[str, str] | None = None,
    python_executable: str | None = None,
) -> dict[str, Any] | None:
    if not live:
        return None
    if not confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    cmd = weather_order_executor_cmd(
        root=root,
        plans_path=plans_path,
        paper_out=paper_out,
        live_out=live_out,
        live=live,
        confirm_live=confirm_live,
        allow_taker=allow_taker,
        cancel_expired=cancel_expired,
        no_telegram=no_telegram,
        python_executable=python_executable,
    )
    proc = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_sec,
    )
    parsed = parse_executor_json(proc.stdout)
    output_tail = proc.stdout[-8000:]
    return {
        "executor_cmd": cmd,
        "executor_returncode": proc.returncode,
        "executor_output": output_tail,
        "executor_output_tail": output_tail,
        "executor_result": parsed,
        "cmd": cmd,
        "returncode": proc.returncode,
        "output_tail": output_tail,
        "parsed": parsed,
    }
