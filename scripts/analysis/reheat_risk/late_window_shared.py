"""Shared deterministic helpers for the late-window residual replay family."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def rows(
    conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [description[0] for description in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def best_level(raw: Any, side: str) -> tuple[float | None, float | None]:
    if not isinstance(raw, dict):
        return None, None
    levels = raw.get(side)
    if not isinstance(levels, list) or not levels:
        return None, None
    parsed: list[tuple[float, float | None]] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        try:
            price = float(level.get("price"))
        except (TypeError, ValueError):
            continue
        try:
            size = float(level.get("size")) if level.get("size") is not None else None
        except (TypeError, ValueError):
            size = None
        parsed.append((price, size))
    if not parsed:
        return None, None
    return min(parsed, key=lambda item: item[0]) if side == "asks" else max(
        parsed, key=lambda item: item[0]
    )


def summary_num(record: dict[str, Any], key: str) -> float | None:
    summary = record.get("summary")
    if not isinstance(summary, dict):
        return None
    try:
        return float(summary.get(key)) if summary.get(key) is not None else None
    except (TypeError, ValueError):
        return None


def load_today_observation(
    path: Path, city: str = "Chengdu", hour: int = 17
) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records") or []
    record = next(
        (
            item
            for item in records
            if item.get("city") == city and item.get("status") == "ok"
        ),
        None,
    )
    if not record:
        return pd.DataFrame()
    row = {
        "city": record.get("city"),
        "icao": record.get("station"),
        "timezone": record.get("timezone_name"),
        "target_date": str(record.get("target_date")),
        "decision_hour_local": hour,
        "obs_count_day": record.get("record_count"),
        "obs_count_to_decision": record.get("record_count"),
        "decision_last_obs_utc": record.get("last_obs_utc"),
        "current_temp_c": record.get("current_temp_c"),
        "current_temp_f": record.get("tmpf_now"),
        "running_max_c": record.get("running_max_c"),
        "running_max_f": (
            float(record["running_max_c"]) * 9.0 / 5.0 + 32.0
            if record.get("running_max_c") is not None
            else None
        ),
        "decline_from_max_c": record.get("decline_c"),
        "decline_from_max_f": (
            float(record["decline_c"]) * 9.0 / 5.0
            if record.get("decline_c") is not None
            else None
        ),
        "final_max_c": np.nan,
        "final_max_f": np.nan,
        "tmpf_now": record.get("tmpf_now"),
        "dwpf_now": record.get("dwpf_now"),
        "dewpoint_depression_f": record.get("dewpoint_depression_f"),
        "relative_humidity_pct": record.get("relh_now"),
        "wind_speed_kt": record.get("sknt_now"),
        "sky_cover_code": record.get("sky_code_now"),
        "temp_trend_1h_f": record.get("d_tmpf_1h"),
        "temp_trend_3h_f": record.get("d_tmpf_3h"),
        "minutes_since_running_max": record.get("minutes_since_running_max"),
        "cadence_min": record.get("cadence_min"),
        "minutes_to_next_obs": record.get("minutes_to_next_obs"),
        "source": record.get("source"),
        "source_chain": ",".join(record.get("source_chain") or []),
        "asof_generated_at_utc": data.get("generated_at_utc"),
    }
    return pd.DataFrame([row])


def block_ci(
    values: np.ndarray, reps: int = 2000, seed: int = 0
) -> tuple[float | None, float | None]:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if len(finite) < 2:
        return None, None
    rng = np.random.default_rng(seed)
    samples = rng.choice(finite, size=(reps, len(finite)), replace=True).sum(axis=1)
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def basket_rows(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    settled = selected[selected["settled"]].copy()
    if settled.empty:
        return pd.DataFrame()
    return (
        settled.groupby(
            [
                "execution_mode",
                "city",
                "target_date",
                "decision_hour_local",
                "snapshot_ts_utc",
            ],
            as_index=False,
        )
        .agg(
            legs=("leg", "count"),
            leg_set=("leg", lambda values: ",".join(sorted(values))),
            cost=("cost_per_share", "sum"),
            pnl=("pnl_per_share", "sum"),
            worst_leg_pnl=("pnl_per_share", "min"),
            final_winning_bracket=("final_winning_bracket", "first"),
        )
        .assign(roi=lambda frame: frame["pnl"] / frame["cost"])
    )


__all__ = [
    "basket_rows",
    "best_level",
    "block_ci",
    "connect_ro",
    "load_today_observation",
    "round_half_up",
    "rows",
    "scalar",
    "summary_num",
]
