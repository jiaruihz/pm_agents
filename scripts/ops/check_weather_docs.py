#!/usr/bin/env python3
"""Read-only consistency checks for weather documentation.

This intentionally checks only current-source docs and handoff entrypoints.
Historical analysis snapshots may keep old wording as time-point evidence.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def section(text: str, start: str, end: str) -> str:
    start_idx = text.index(start)
    end_idx = text.index(end, start_idx)
    return text[start_idx:end_idx]


def check_entrypoints(errors: list[str]) -> None:
    for path in ("AGENTS.md", "CLAUDE.md"):
        text = read(path)
        if "docs/WEATHER_DOCS_INDEX.md" not in text:
            fail(errors, f"{path}: missing docs/WEATHER_DOCS_INDEX.md link")


def check_current_strategy_language(errors: list[str]) -> None:
    paths = [
        "AGENTS.md",
        "CLAUDE.md",
        "docs/WEATHER_STRATEGY_ENTRYPOINT.md",
        "docs/WEATHER_CITY_POOL_DECISIONS.md",
        "skills/weather-strategy-deploy/SKILL.md",
    ]
    forbidden = [
        "N100 should run exactly three",
        "默认三",
        "三条明确的",
    ]
    for path in paths:
        text = read(path)
        for phrase in forbidden:
            if phrase in text:
                fail(errors, f"{path}: forbidden current-production phrase {phrase!r}")


def check_city_pool_docs(errors: list[str]) -> None:
    text = read("docs/WEATHER_CITY_POOL_DECISIONS.md")
    current_t1 = section(text, "### 当前 T1 城市", "### 当前不在 T1 的重点城市")
    current_not_t1 = section(text, "### 当前不在 T1 的重点城市", "## 这次 v3 怎么改")

    for city in ("Amsterdam", "BuenosAires"):
        if city in current_t1:
            fail(errors, f"WEATHER_CITY_POOL_DECISIONS.md: {city} appears in current T1 section")
        if city not in current_not_t1:
            fail(errors, f"WEATHER_CITY_POOL_DECISIONS.md: {city} missing from current non-T1 section")

    if "Madrid" not in current_t1:
        fail(errors, "WEATHER_CITY_POOL_DECISIONS.md: Madrid missing from current T1 section")
    if re.search(r"\\|\\s*Madrid\\s*\\|\\s*T2 / research only", current_not_t1):
        fail(errors, "WEATHER_CITY_POOL_DECISIONS.md: Madrid still marked T2 in current non-T1 table")
    if "2458696" not in text:
        fail(errors, "WEATHER_CITY_POOL_DECISIONS.md: missing deployed pm_agent SHA 2458696")


def check_index_links(errors: list[str]) -> None:
    index_path = ROOT / "docs" / "WEATHER_DOCS_INDEX.md"
    index = index_path.read_text(encoding="utf-8")
    for match in re.finditer(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)", index):
        target = match.group(1)
        if target.startswith(("http://", "https://")):
            continue
        resolved = (index_path.parent / target).resolve()
        if not resolved.exists():
            fail(errors, f"WEATHER_DOCS_INDEX.md: broken link {target}")


def main() -> int:
    errors: list[str] = []
    check_entrypoints(errors)
    check_current_strategy_language(errors)
    check_city_pool_docs(errors)
    check_index_links(errors)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("weather docs check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
