from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional


WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
CONDITION_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def normalize_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            return [parsed]
        except Exception:
            return [value]
    return []


def safe_iso_parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    except Exception:
        return None


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify_target(value: str, default: str = "item") -> str:
    text = (value or "").strip().rstrip("/")
    if text.startswith("http://") or text.startswith("https://"):
        text = text.split("/")[-1]
    text = text.lstrip("@").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or default


def short_verdict_label(verdict: str) -> str:
    text = (verdict or "").strip().lower()
    if not text:
        return "unknown"
    if "high_rule_risk" in text:
        return "high_rule_risk"
    if "leans_yes" in text or "yes" in text:
        return "leans_yes"
    if "leans_no" in text or "no" in text:
        return "leans_no"
    if "mixed" in text:
        return "mixed"
    if "insufficient" in text:
        return "insufficient_edge"
    return "neutral"
