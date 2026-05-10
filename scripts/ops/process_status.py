#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    match: str
    restart_after_reboot: bool
    log_path: str
    start_hint: str


SERVICES: List[ServiceSpec] = [
    ServiceSpec(
        name="telegram_research_bot",
        match="scripts/ops/telegram_research_bot.py",
        restart_after_reboot=True,
        log_path="runtime/logs/telegram_research_bot.log",
        start_hint="scripts/ops/telegram_research_bot_ctl.sh start",
    ),
    ServiceSpec(
        name="strategy_dashboard_server",
        match="src.interfaces.web.strategy_dashboard_server",
        restart_after_reboot=True,
        log_path="stdout / terminal session",
        start_hint="PYTHONPATH=. .venv/bin/python -m src.interfaces.web.strategy_dashboard_server --host 127.0.0.1 --port 8011 --artifacts-dir src/strategies/pmm/backtest/.artifacts --runtime-dir runtime",
    ),
    ServiceSpec(
        name="weather_position_monitor",
        match="scripts/ops/weather_position_monitor.py",
        restart_after_reboot=True,
        log_path="stdout / terminal session",
        start_hint="Use the last known command from ps output, because this service usually carries market-specific args.",
    ),
    ServiceSpec(
        name="pmm_main",
        match="src.strategies.pmm.main",
        restart_after_reboot=False,
        log_path="runtime/logs/pmm_paper_live.log",
        start_hint="See docs/pmm/PAPER_RUNBOOK.md",
    ),
]


def _find_process(match: str) -> Optional[str]:
    proc = subprocess.run(
        ["ps", "-ef"],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    for line in (proc.stdout or "").splitlines():
        if match in line and "process_status.py" not in line:
            return line.strip()
    return None


def main() -> None:
    print("pm_agent process status")
    print(f"root: {ROOT}")
    print("")
    for spec in SERVICES:
        line = _find_process(spec.match)
        status = "RUNNING" if line else "STOPPED"
        restart = "yes" if spec.restart_after_reboot else "no"
        print(f"[{status}] {spec.name}")
        print(f"  reboot_restart: {restart}")
        print(f"  log: {spec.log_path}")
        print(f"  start: {spec.start_hint}")
        if line:
            print(f"  ps: {line}")
        print("")


if __name__ == "__main__":
    main()
