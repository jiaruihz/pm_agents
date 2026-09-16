from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from src.strategies.schema import StrategyManifest


ROOT_DIR = Path(__file__).resolve().parent


def _manifest_paths() -> List[Path]:
    return sorted(ROOT_DIR.glob("*/manifest.yaml"), key=lambda p: p.parent.name)


def _parse_scalar(raw: str) -> object:
    text = raw.strip()
    if text == "":
        return ""
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    return text


def _parse_manifest(path: Path) -> Dict[str, object]:
    data: Dict[str, object] = {}
    meta: Dict[str, object] = {}
    in_meta = False
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.rstrip()
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        if raw.startswith("meta:"):
            in_meta = True
            continue
        if in_meta and raw.startswith("  ") and ":" in raw:
            key, value = raw.strip().split(":", 1)
            meta[key.strip()] = _parse_scalar(value)
            continue
        in_meta = False
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        data[key.strip()] = _parse_scalar(value)
    data["meta"] = meta
    return data


def load_strategy_catalog() -> List[StrategyManifest]:
    out: List[StrategyManifest] = []
    for path in _manifest_paths():
        payload = _parse_manifest(path)
        item = StrategyManifest(**payload)
        if item.strategy_key != path.parent.name:
            raise ValueError(
                f"manifest key mismatch: dir={path.parent.name} key={item.strategy_key}"
            )
        out.append(item)
    return out


def load_strategy_rows() -> List[Dict[str, object]]:
    return [x.as_runtime_row() for x in load_strategy_catalog()]
