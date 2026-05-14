from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_legacy_timestamp(text: str) -> Dict[str, Any]:
    value = text.strip()
    return {"paused": bool(value), "paused_at_utc": value, "reason": "", "source": "legacy"}


def read_live_state(state_dir: Path) -> Dict[str, Any]:
    paused_path = state_dir / "PAUSED"
    if not paused_path.exists():
        return {"paused": False, "paused_at_utc": "", "reason": "", "source": ""}

    raw = paused_path.read_text(encoding="utf-8").strip()
    if not raw:
        return {"paused": True, "paused_at_utc": "", "reason": "", "source": "unknown"}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _parse_legacy_timestamp(raw)
    if not isinstance(payload, dict):
        return _parse_legacy_timestamp(raw)

    return {
        "paused": True,
        "paused_at_utc": str(payload.get("paused_at_utc") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "source": str(payload.get("source") or "").strip(),
    }


def pause_live(
    state_dir: Path,
    *,
    reason: str = "",
    source: str = "manual",
    paused_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "paused_at_utc": paused_at_utc or utc_now_iso(),
        "reason": reason.strip(),
        "source": source.strip() or "manual",
    }
    paused_path = state_dir / "PAUSED"
    paused_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return read_live_state(state_dir)


def resume_live(state_dir: Path) -> Dict[str, Any]:
    paused_path = state_dir / "PAUSED"
    if paused_path.exists():
        paused_path.unlink()
    return read_live_state(state_dir)


def status_text(state: Dict[str, Any]) -> str:
    if state.get("paused"):
        lines = ["Weather 实盘状态：已暂停。"]
        paused_at = str(state.get("paused_at_utc") or "").strip()
        reason = str(state.get("reason") or "").strip()
        source = str(state.get("source") or "").strip()
        if paused_at:
            lines.append(f"暂停时间：{paused_at}")
        if reason:
            lines.append(f"原因：{reason}")
        if source:
            lines.append(f"来源：{source}")
        return "\n".join(lines)
    return "Weather 实盘状态：允许运行。下一轮若有合格计划，会按 maker-only 规则尝试挂单。"
