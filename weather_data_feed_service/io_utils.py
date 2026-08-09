"""Shared file I/O helpers for weather data-feed service producers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_latest_and_daily_jsonl(
    *,
    output_dir: Path,
    latest_payload: dict[str, Any],
    rows: list[dict[str, Any]],
    jsonl_name: str,
    day: str | None = None,
    write_aggregate: bool = True,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    day_key = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if write_aggregate:
        append_jsonl(output_dir / jsonl_name, rows)
    append_jsonl(output_dir / day_key / jsonl_name, rows)
    write_json(output_dir / "latest.json", latest_payload)
