"""State materialization for tmax_distribution_v3.

Reuses the replay-v2 raw snapshot cache (single-snapshot ladder boundary,
PIT observation history) and reproduces the v2 clean-state selection exactly,
then enriches every state with full-ladder book geometry and market-prior
features.  A missing rung stays a missingness flag; it is never probability 0.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from .common import (
    OUT_DIR,
    V2,
    V2_OUT_DIR,
    bracket_interval,
    bracket_key,
    bracket_sort_key,
    finite,
    interval_contains,
    parse_utc,
    yes_quote,
)
from weather_data_feed.market_brackets import parse_market_bracket

STATE_CACHE = OUT_DIR / "state_rows_v3.csv"
STATE_CACHE_META = OUT_DIR / "state_rows_v3.meta.json"


def _entropy(probs: list[float]) -> float:
    arr = np.clip(np.asarray(probs, dtype=float), 1e-12, 1.0)
    arr = arr / arr.sum()
    return float(-(arr * np.log(arr)).sum())


def _ladder_book(ladder: list[dict[str, Any]]) -> list[dict[str, Any]]:
    book = []
    for record in ladder:
        bid, ask, mid = yes_quote(record)
        book.append(
            {
                "bracket": bracket_key(record),
                "yes_bid": bid,
                "yes_ask": finite(record.get("yes_best_ask")),
                "yes_eff_ask": ask,
                "yes_mid": mid,
                "no_bid": finite(record.get("no_best_bid")),
                "no_ask": finite(record.get("no_best_ask")),
                "yes_ask_size": finite(record.get("yes_ask_size")),
                "no_ask_size": finite(record.get("no_ask_size")),
            }
        )
    return book


def enriched_state(
    records: list[dict[str, Any]],
    decision: pd.Timestamp,
    history: dict[tuple[str, str], list[dict[str, Any]]],
    winners: dict[tuple[str, str], str],
    priority: int,
    source_path: str,
    allow_unlabeled: bool = False,
) -> dict[str, Any] | None:
    base = V2._native_state(records, decision, history, winners, priority, source_path)
    labeled = base is not None
    if base is None and allow_unlabeled:
        base = _unlabeled_state(records, decision, history, priority, source_path)
    if base is None:
        return None

    ladder = sorted(
        {bracket_key(item): item for item in records if bracket_key(item) is not None}.values(),
        key=bracket_sort_key,
    )
    anchor = int(base["anchor_rung_index"])
    book = _ladder_book(ladder)
    mids = [row["yes_mid"] for row in book]
    quoted = [m for m in mids if m is not None]
    current_row, d1_row, d2_row = book[anchor], book[anchor + 1], book[anchor + 2]

    def spread(row: dict[str, Any]) -> float | None:
        if row["yes_bid"] is None or row["yes_eff_ask"] is None:
            return None
        return float(row["yes_eff_ask"]) - float(row["yes_bid"])

    depth_values = [
        v
        for v in (current_row["no_ask_size"], d1_row["no_ask_size"], d2_row["no_ask_size"])
        if v is not None
    ]
    low, high, bottom, top = bracket_interval(str(base["current_bracket"]))
    running = float(base["running_native"])
    if low is not None and high is not None and not bottom and not top:
        width = max((high - low) + 1.0, 1e-9)
        boundary = float(np.clip((running - low + 0.5) / width, 0.0, 1.0))
    else:
        boundary = math.nan

    anchor_record = ladder[anchor]
    unit = str(base["unit"]).upper()
    current_native = float(base["current_native"])
    running_native = float(base["running_native"])
    base.update(
        {
            "labeled": labeled,
            "settlement_winning_bracket_label": (
                winners.get((str(base["city"]), str(base["target_date"]))) if labeled else None
            ),
            "winner_step": (
                int(base["winner_rung_index"]) - anchor if labeled else np.nan
            ),
            "ladder_book_json": json.dumps(book, ensure_ascii=False),
            "quoted_rung_fraction": float(len(quoted)) / max(len(book), 1),
            "ladder_quote_complete": len(quoted) == len(book),
            "ladder_overround": float(sum(quoted)) if len(quoted) == len(book) else math.nan,
            "market_entropy": (
                _entropy([base[f"market_p_{b}"] for b in ("below", "current", "d1", "d2", "tail")])
                if base.get("market_score_ready")
                else math.nan
            ),
            "current_yes_spread": spread(current_row),
            "d1_yes_spread": spread(d1_row),
            "d2_yes_spread": spread(d2_row),
            "log_depth_min_anchor": float(np.log1p(min(depth_values))) if depth_values else math.nan,
            "rungs_above_count": len(book) - anchor - 3,
            "boundary_pos_in_bracket": boundary,
            "obs_count_today": finite(anchor_record.get("metar_obs_count_today")),
            "forecast_max_native": finite(anchor_record.get("forecast_max_native")),
            "current_temp_f": current_native if unit == "F" else current_native * 9.0 / 5.0 + 32.0,
            "running_max_f": running_native if unit == "F" else running_native * 9.0 / 5.0 + 32.0,
        }
    )
    return base


def _unlabeled_state(
    records: list[dict[str, Any]],
    decision: pd.Timestamp,
    history: dict[tuple[str, str], list[dict[str, Any]]],
    priority: int,
    source_path: str,
) -> dict[str, Any] | None:
    """v2 `_native_state` with a synthetic winner so shadow-forward states of
    not-yet-settled dates materialize; label fields are cleared afterwards."""
    first = records[0]
    city = str(first["city"])
    target_date = str(first.get("target_date") or first.get("event_date") or "")
    ladder = sorted(
        {bracket_key(item): item for item in records if bracket_key(item) is not None}.values(),
        key=bracket_sort_key,
    )
    if not ladder:
        return None
    fake_winner = None
    unit = str(first.get("unit") or "").upper()
    if unit not in {"C", "F"}:
        return None
    path_state = V2._path_asof(history.get((city, target_date), []), decision, unit)
    if path_state is None:
        return None
    anchor = next(
        (
            index
            for index, item in enumerate(ladder)
            if (parsed := parse_market_bracket(str(item.get("bracket") or ""), str(item.get("question") or ""))) is not None
            and interval_contains(parsed, path_state["running_native"])
        ),
        None,
    )
    if anchor is None or anchor + 2 >= len(ladder):
        return None
    fake_winner = bracket_key(ladder[anchor])
    state = V2._native_state(
        records, decision, history, {(city, target_date): fake_winner}, priority, source_path
    )
    if state is None:
        return None
    state["actual_bucket"] = None
    state["winner_rung_index"] = np.nan
    state["label_rule"] = "unlabeled_pending_settlement"
    return state


def materialize_states(freeze_date: str, max_lines: int | None = None) -> pd.DataFrame:
    """Stream the v2 raw cache into enriched v3 states (with disk cache)."""
    cache_path = V2_OUT_DIR / "raw_decision_groups.jsonl"
    events_path = V2_OUT_DIR / "embedded_observation_events.csv"
    if not cache_path.exists() or not events_path.exists():
        raise RuntimeError(
            "replay v2 raw cache missing; run research_tmax_single_snapshot_lineage_replay_v2.py first"
        )
    if STATE_CACHE.exists() and STATE_CACHE_META.exists() and max_lines is None:
        meta = json.loads(STATE_CACHE_META.read_text(encoding="utf-8"))
        if (
            meta.get("freeze_date") == freeze_date
            and meta.get("cache_mtime_ns") == cache_path.stat().st_mtime_ns
        ):
            frame = pd.read_csv(STATE_CACHE, low_memory=False)
            for column in ("market_score_ready", "ladder_quote_complete", "labeled"):
                frame[column] = frame[column].astype(str).str.lower().eq("true")
            return frame

    observations = pd.read_csv(events_path, parse_dates=["obs_ts_utc", "first_seen_snapshot_ts_utc"])
    history = {key: group.to_dict("records") for key, group in observations.groupby(["city", "target_date"])}
    winners = V2.load_settlement_winners()
    rows: list[dict[str, Any]] = []
    with cache_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle):
            if max_lines is not None and line_no >= max_lines:
                break
            group = json.loads(line)
            decision = parse_utc(group.get("snapshot_ts_utc"))
            if decision is None:
                continue
            target_date = str(group.get("target_date") or "")
            allow_unlabeled = target_date > freeze_date
            state = enriched_state(
                group["records"],
                decision,
                history,
                winners,
                int(group["root_priority"]),
                group["source_path"],
                allow_unlabeled=allow_unlabeled,
            )
            if state is not None:
                rows.append(state)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("no v3 states materialized")
    frame = frame.sort_values(
        ["city", "target_date", "decision_snapshot_ts_utc", "snapshot_root_priority"]
    ).drop_duplicates(["city", "target_date", "decision_snapshot_ts_utc"], keep="last")
    assert frame["rung_snapshot_ts_unique"].eq(1).all(), "cross-snapshot ladder mix detected"
    labeled = frame[frame["labeled"]]
    below = labeled[labeled["actual_bucket"].eq("below")]
    if not below.empty:
        assert (below["winner_rung_index"] < below["anchor_rung_index"]).all()
    if max_lines is None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_csv(STATE_CACHE, index=False)
        STATE_CACHE_META.write_text(
            json.dumps(
                {"freeze_date": freeze_date, "cache_mtime_ns": cache_path.stat().st_mtime_ns,
                 "rows": len(frame), "labeled_rows": int(frame["labeled"].sum())}
            )
            + "\n",
            encoding="utf-8",
        )
    return frame


def hourly_last(states: pd.DataFrame) -> pd.DataFrame:
    return (
        states.sort_values(["city", "target_date", "decision_hour_local", "decision_snapshot_ts_utc"])
        .groupby(["city", "target_date", "decision_hour_local"], as_index=False)
        .tail(1)
    )
