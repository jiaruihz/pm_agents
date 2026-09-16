"""Clock health probes for Linux chrony and bounded macOS SNTP fallback."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from typing import Any


CHRONY_OFFSET_RE = re.compile(r"Last offset\s*:\s*([+-]?[0-9.]+)\s+seconds", re.IGNORECASE)
CHRONY_STRATUM_RE = re.compile(r"Stratum\s*:\s*(\d+)", re.IGNORECASE)
CHRONY_LEAP_RE = re.compile(r"Leap status\s*:\s*(.+)", re.IGNORECASE)
SNTP_RE = re.compile(r"^([+-]?[0-9.]+)\s+\+/-\s+([0-9.]+)")


def probe_clock(max_abs_offset_ms: float = 20.0) -> dict[str, Any]:
    sampled_at_ns = time.time_ns()
    sampled_monotonic_ns = time.monotonic_ns()
    if shutil.which("chronyc"):
        try:
            raw = subprocess.check_output(
                ["chronyc", "tracking"], text=True, stderr=subprocess.STDOUT, timeout=5
            ).strip()
            offset_match = CHRONY_OFFSET_RE.search(raw)
            stratum_match = CHRONY_STRATUM_RE.search(raw)
            leap_match = CHRONY_LEAP_RE.search(raw)
            offset_ms = float(offset_match.group(1)) * 1000.0 if offset_match else None
            leap = leap_match.group(1).strip() if leap_match else "unknown"
            valid = offset_ms is not None and abs(offset_ms) <= max_abs_offset_ms and leap.lower() == "normal"
            return {
                "sampled_at_ns": sampled_at_ns,
                "sampled_monotonic_ns": sampled_monotonic_ns,
                "probe_kind": "chronyc_tracking",
                "offset_ms": offset_ms,
                "uncertainty_ms": None,
                "stratum": int(stratum_match.group(1)) if stratum_match else None,
                "leap_status": leap,
                "clock_valid": valid,
                "raw_probe": raw,
            }
        except Exception as exc:  # noqa: BLE001
            raw = f"{type(exc).__name__}: {exc}"
    elif shutil.which("sntp"):
        try:
            raw = subprocess.check_output(
                ["sntp", "-t", "3", "time.cloudflare.com"], text=True, stderr=subprocess.STDOUT, timeout=6
            ).strip()
            match = SNTP_RE.search(raw)
            offset_ms = float(match.group(1)) * 1000.0 if match else None
            uncertainty_ms = float(match.group(2)) * 1000.0 if match else None
            # SNTP fallback is diagnostic. A large uncertainty cannot certify a
            # second-level race even when the point offset is small.
            valid = (
                offset_ms is not None
                and uncertainty_ms is not None
                and abs(offset_ms) <= max_abs_offset_ms
                and uncertainty_ms <= max_abs_offset_ms
            )
            return {
                "sampled_at_ns": sampled_at_ns,
                "sampled_monotonic_ns": sampled_monotonic_ns,
                "probe_kind": "sntp_fallback",
                "offset_ms": offset_ms,
                "uncertainty_ms": uncertainty_ms,
                "stratum": None,
                "leap_status": "unknown",
                "clock_valid": valid,
                "raw_probe": raw,
            }
        except Exception as exc:  # noqa: BLE001
            raw = f"{type(exc).__name__}: {exc}"
    else:
        raw = "chronyc and sntp unavailable"
    return {
        "sampled_at_ns": sampled_at_ns,
        "sampled_monotonic_ns": sampled_monotonic_ns,
        "probe_kind": "unavailable",
        "offset_ms": None,
        "uncertainty_ms": None,
        "stratum": None,
        "leap_status": "unknown",
        "clock_valid": False,
        "raw_probe": raw,
    }

