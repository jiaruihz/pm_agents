#!/usr/bin/env python3
"""Station-basis live-candidate shadow tracker v1.

This wrapper keeps the original v0 tracker intact and writes to a separate
ledger. v1 tracks the tighter candidate found after forward-shadow diagnosis:

- five repaired station-basis cities only: Paris, London, Chicago,
  KualaLumpur, PanamaCity
- no Milan/Jakarta until their forward evidence is repaired
- YES official-bucket at local 16h only, one entry per city-day
- NO d1/d2 exhaustion rules stay at local 13-17h

Shadow only: no orders are placed.
"""

from __future__ import annotations

import os
import subprocess
import sys

import weather_station_basis_shadow as base


base.OUT_DIR = base.DATA_ROOT / "runtime/weather_edge_v1/station_basis_shadow_v1"
base.CITIES = {
    city: spec
    for city, spec in base.CITIES.items()
    if city in {"Paris", "London", "Chicago", "KualaLumpur", "PanamaCity"}
}
base.YES_HOURS = range(16, 17)
base.YES_ONE_PER_CITY_DAY = True
base.YES_ASK_MAX = 0.90
base.NO_ASK_MAX = 0.90


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else None
    dry_run = "--dry-run" in sys.argv
    rc = base.main()
    if (
        rc == 0
        and command in {"cycle", "settle"}
        and not dry_run
        and os.environ.get("STATION_BASIS_SKIP_V1_SIDELOADS") != "1"
    ):
        env = dict(os.environ)
        env["STATION_BASIS_SKIP_V1_SIDELOADS"] = "1"
        sidecars = []
        if command == "cycle":
            sidecars.extend(
                [
                    ["scripts/ops/station_basis_v1_readiness.py"],
                    ["scripts/ops/weather_station_basis_exec_v1.py", "run"],
                ]
            )
        sidecars.append(["scripts/ops/station_basis_v1_pending_monitor.py"])
        for sidecar in sidecars:
            try:
                subprocess.run(
                    [sys.executable, str(base.ROOT / sidecar[0]), *sidecar[1:]],
                    cwd=base.ROOT,
                    env=env,
                    check=False,
                    timeout=90,
                )
            except Exception as exc:  # noqa: BLE001
                base.append_jsonl(
                    base.OUT_DIR / "alerts.jsonl",
                    {
                        "ts_utc": base.datetime.now(base.timezone.utc).isoformat(),
                        "status": "v1_sidecar_failed",
                        "command": command,
                        "sidecar": sidecar,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
    sys.exit(rc)
