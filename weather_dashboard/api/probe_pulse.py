"""Pure helpers for probe health: read latest_summary.json, normalize, classify freshness.

Kept side-effect-free (filesystem read only) so the FastAPI router stays thin and
the freshness/normalization logic is unit-testable without a DB or app.
"""

from __future__ import annotations

import json
from pathlib import Path


def classify_freshness(age_min: float | None, warn_min: float, bad_min: float) -> str:
    """Map an age in minutes to a traffic-light freshness band."""
    if age_min is None:
        return "unknown"
    if age_min <= warn_min:
        return "fresh"
    if age_min <= bad_min:
        return "aging"
    return "stale"


def read_summary_at(path: Path) -> dict | None:
    """Read a latest_summary.json at an explicit path, or None if missing/bad."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_latest_summary(root: Path, instance: str) -> dict | None:
    """Read <root>/<instance>/latest_summary.json, or None if missing/empty/bad."""
    return read_summary_at(root / instance / "latest_summary.json")


def _snap_age(summary: dict) -> float | None:
    if summary.get("snapshot_age_min") is not None:
        return summary["snapshot_age_min"]
    return (summary.get("meta") or {}).get("snapshot_age_min")


def _snap_ts(summary: dict) -> str | None:
    if summary.get("snapshot_ts_utc"):
        return summary["snapshot_ts_utc"]
    return (summary.get("meta") or {}).get("snapshot_ts_utc")


def _top_audit(summary: dict) -> str | None:
    counts = summary.get("audit_counts") or (summary.get("meta") or {}).get("audit_counts") or {}
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _first_present(summary: dict, keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in summary and summary.get(key) is not None:
            return summary.get(key)
    return None


def normalize_probe_row(
    registry_row: dict,
    summary: dict | None,
    warn_min: float = 20.0,
    bad_min: float = 120.0,
) -> dict:
    """Merge a runtime-registry row with its latest_summary.json into one UI row.

    When the pulse file is missing the row still returns with status
    ``no_pulse_file`` so the dashboard surfaces it explicitly rather than
    silently dropping the probe.
    """
    base = {
        "strategy_instance": registry_row.get("strategy_instance"),
        "lifecycle_status": registry_row.get("lifecycle_status"),
        "health_status": registry_row.get("health_status"),
        "heartbeat_age_min": registry_row.get("heartbeat_age_min"),
    }
    if summary is None:
        base.update({
            "status": "no_pulse_file",
            "snapshot_age_min": None,
            "snapshot_ts_utc": None,
            "freshness": "unknown",
            "candidate_rows": None,
            "execution_eligible": None,
            "alert_count": None,
            "critical_alerts": None,
            "warning_alerts": None,
            "top_audit": None,
            "caps": None,
        })
        return base

    age = _snap_age(summary)
    base.update({
        "status": summary.get("status") or ("no_order" if summary.get("no_order_placed") else "ran"),
        "snapshot_age_min": age,
        "snapshot_ts_utc": _snap_ts(summary),
        "freshness": classify_freshness(age, warn_min, bad_min),
        "candidate_rows": _first_present(summary, ("candidate_rows", "pre_fresh_candidates", "accepted_candidates")),
        "execution_eligible": _first_present(summary, ("execution_eligible", "plans", "plans_written")),
        "alert_count": summary.get("alert_count"),
        "critical_alerts": summary.get("critical_alerts"),
        "warning_alerts": summary.get("warning_alerts"),
        "top_audit": _top_audit(summary),
        "caps": summary.get("caps") or {
            k: summary.get(k) for k in ("base_notional", "daily_gross_cap") if k in summary
        },
    })
    return base
