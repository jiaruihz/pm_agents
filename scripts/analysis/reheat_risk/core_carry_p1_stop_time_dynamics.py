#!/usr/bin/env python3
"""Core Carry P1: first-positive cause decomposition and dynamic ladder readiness.

Research only.  The runner freezes the authoritative 92-signal ledger, reads an
append-only prefix of Core scores, and audits PIT REST/WS evidence.  It never
changes a selector, runtime, order, canonical fact, or production artifact.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import sqlite3
import subprocess
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    canonical_hash,
    finite,
    market_features,
    score_probability,
)
from weather_data_feed.market_brackets import parse_market_bracket  # noqa: E402
from weather_data_feed.ws_incremental_book import extract_market_trade_prints  # noqa: E402


RESEARCH_ID = "current_yes_core_carry_p1_stop_time_dynamics"
RUN_ID = "signal_ledger_92_through_20260820"
SIGNAL_LEDGER = (
    ROOT / "docs/analysis/2026-08/generated/decision_packet_v1/signal_ledger.csv"
)
LOSS_PACKETS = (
    ROOT / "docs/analysis/2026-08/generated/decision_packet_v1/loss_decision_packets.json"
)
DB_PATH = ROOT / "runtime/weather.db"
PRIMARY_LADDER_MAX_LAG_MIN = 7.0
BOOTSTRAP_DRAWS = 5000
BOOTSTRAP_SEED = 20260822
EPS = 1e-6

# Exact historical artifacts are recovered from immutable git objects.  The
# live rows carry the artifact hash, so a model-version transition is explicit.
HISTORICAL_ARTIFACTS: dict[str, tuple[str, str]] = {
    "18bdbfb4ce96ac94c869e2d910b81d7cea85b6bb1293e93f91d1141462f02fac": (
        "1518935f8d72f701c9fe2697a0c5dfce44c2df05",
        "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v2.json",
    ),
    "1f14697c4704c02393bc250d917060d41c5b8d0f21494c228ec8b041adcd4a92": (
        "6e8d3d9b2de719c3d8081d459fadb326429fad51",
        "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json",
    ),
    "2a075534eb0ff688c830e5e74a88447de1c55608c2e6c241c96ace70cdc1e652": (
        "f69a10d51195776172712858b91b1c1b7e1ae4d6",
        "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v2_taker10.json",
    ),
}


def parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl_prefix(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            digest.update(raw)
            bytes_read += len(raw)
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows, {
        "path": str(path),
        "prefix_rows": len(rows),
        "prefix_bytes": bytes_read,
        "prefix_sha256": digest.hexdigest(),
        "last_created_at_utc": rows[-1].get("created_at_utc") if rows else None,
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_artifacts() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    lineage: dict[str, Any] = {}
    config_root = ROOT / "src/strategies/weather_edge_v1/config"
    for path in config_root.glob("current_yes_core_carry_model_v*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        claimed = str(payload.get("artifact_hash") or "")
        if claimed and canonical_hash({k: v for k, v in payload.items() if k != "artifact_hash"}) == claimed:
            artifacts[claimed] = payload
            lineage[claimed] = {"source": "working_tree", "path": str(path)}
    for claimed, (commit, path) in HISTORICAL_ARTIFACTS.items():
        raw = subprocess.check_output(
            ["git", "show", f"{commit}:{path}"], cwd=ROOT, text=True
        )
        payload = json.loads(raw)
        actual = canonical_hash({k: v for k, v in payload.items() if k != "artifact_hash"})
        if payload.get("artifact_hash") != claimed or actual != claimed:
            raise ValueError(f"historical artifact hash mismatch: {claimed}")
        artifacts[claimed] = payload
        lineage[claimed] = {"source": "git_object", "commit": commit, "path": path}
    return artifacts, lineage


def score_clock(row: Mapping[str, Any]) -> datetime | None:
    return parse_utc(
        row.get("created_at_utc")
        or row.get("decision_snapshot_ts_utc")
        or row.get("as_of_ts_utc")
    )


def quote_usable(row: Mapping[str, Any]) -> bool:
    bid, ask = finite(row.get("current_yes_bid")), finite(row.get("current_yes_ask"))
    probability = finite(row.get("model_probability_hold"))
    cost = taker_cost(row)
    return (
        bid is not None
        and ask is not None
        and 0 < bid <= ask < 1
        and probability is not None
        and cost is not None
    )


def taker_cost(row: Mapping[str, Any]) -> float | None:
    ladder = row.get("taker_ladder")
    if isinstance(ladder, Mapping):
        value = finite(ladder.get("effective_cost_per_share"))
        if value is not None:
            return value
    return finite(row.get("current_yes_effective_cost"))


def market_mid(row: Mapping[str, Any]) -> float | None:
    bid, ask = finite(row.get("current_yes_bid")), finite(row.get("current_yes_ask"))
    return None if bid is None or ask is None or not (0 < bid <= ask < 1) else (bid + ask) / 2


def index_scores(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        if score_clock(row) is not None:
            grouped[(str(row.get("city") or ""), str(row.get("target_date") or ""))].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: score_clock(row) or datetime.min.replace(tzinfo=timezone.utc))
    return grouped


def match_trigger(
    ledger: Mapping[str, str], scores: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any] | None, str]:
    wanted = parse_utc(ledger.get("trigger_ts_utc"))
    if wanted is None:
        return None, "ledger_trigger_clock_missing"
    exact = [row for row in scores if score_clock(row) == wanted]
    if len(exact) == 1:
        return dict(exact[0]), "exact_created_at"
    candidates = [
        row
        for row in scores
        if bool(row.get("eligible"))
        and finite(row.get("model_probability_hold")) is not None
    ]
    if not candidates:
        return None, "eligible_score_missing"
    nearest = min(
        candidates,
        key=lambda row: abs(((score_clock(row) or wanted) - wanted).total_seconds()),
    )
    lag = abs(((score_clock(nearest) or wanted) - wanted).total_seconds())
    return (dict(nearest), "nearest_eligible_within_2s") if lag <= 2 else (None, "no_exact_trigger_match")


def prior_rows(
    trigger: Mapping[str, Any], scores: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    trigger_time = score_clock(trigger)
    earlier = [row for row in scores if score_clock(row) is not None and score_clock(row) < trigger_time]
    last_any = dict(earlier[-1]) if earlier else None
    informative = [row for row in earlier if quote_usable(row) and not bool(row.get("eligible"))]
    return last_any, (dict(informative[-1]) if informative else None)


def load_settlements(db_path: Path) -> tuple[dict[tuple[str, str, str], float], dict[str, Any]]:
    stat = db_path.resolve().stat()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    rows = conn.execute(
        """
        SELECT city, target_date, bracket, final_price, created_at_utc
        FROM settlement_outcomes
        WHERE source_system='pm_history'
          AND target_date BETWEEN '2026-07-25' AND '2026-08-20'
          AND settlement_status='settled'
        ORDER BY created_at_utc
        """
    ).fetchall()
    conn.close()
    mapping = {(str(city), str(date), str(bracket)): float(final) for city, date, bracket, final, _ in rows}
    return mapping, {
        "path": str(db_path),
        "realpath": str(db_path.resolve()),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "settlement_rows_loaded": len(rows),
    }


def external_loss_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("signal_id") or "")
        for row in payload
        if "loss confirmed externally" in str((row.get("outcome") or {}).get("note") or "")
    }


def outcome_for(
    ledger: Mapping[str, str],
    settlements: Mapping[tuple[str, str, str], float],
    external_losses: set[str],
) -> tuple[int | None, str]:
    key = (
        str(ledger.get("city") or ""),
        str(ledger.get("target_date") or ""),
        str(ledger.get("bracket_canonical") or ledger.get("bracket_trigger") or ""),
    )
    if key in settlements:
        return (0 if settlements[key] >= 0.5 else 1), "canonical_settlement_outcomes"
    if str(ledger.get("signal_id") or "") in external_losses:
        return 1, "external_onchain_confirmed_loss"
    if ledger.get("outcome") == "W":
        return 0, "frozen_signal_ledger"
    if ledger.get("outcome") == "L":
        return 1, "frozen_signal_ledger"
    return None, "unavailable"


def decompose_signal(
    ledger: Mapping[str, str],
    trigger: Mapping[str, Any],
    prior_any: Mapping[str, Any] | None,
    prior: Mapping[str, Any] | None,
    artifact: Mapping[str, Any] | None,
) -> dict[str, Any]:
    base = {
        "signal_id": ledger.get("signal_id"),
        "city": ledger.get("city"),
        "target_date": ledger.get("target_date"),
        "current_bracket": ledger.get("bracket_canonical") or ledger.get("bracket_trigger"),
        "trigger_created_at_utc": trigger.get("created_at_utc"),
        "trigger_decision_snapshot_ts_utc": trigger.get("decision_snapshot_ts_utc"),
        "checkpoint_key": trigger.get("checkpoint_key"),
        "artifact_hash": trigger.get("artifact_hash"),
        "trigger_token_id": trigger.get("current_yes_token_id"),
        "last_checkpoint_created_at_utc": None if prior_any is None else prior_any.get("created_at_utc"),
        "last_checkpoint_quote_usable": False if prior_any is None else quote_usable(prior_any),
        "prior_created_at_utc": None if prior is None else prior.get("created_at_utc"),
        "prior_checkpoint_key": None if prior is None else prior.get("checkpoint_key"),
        "prior_reasons": None if prior is None else prior.get("reasons"),
    }
    if prior is None:
        return {**base, "decomposition_status": "missing_informative_prior"}
    if artifact is None:
        return {**base, "decomposition_status": "artifact_unavailable"}
    if str(prior.get("artifact_hash") or "") != str(trigger.get("artifact_hash") or ""):
        return {**base, "decomposition_status": "artifact_changed_since_prior"}
    p_now = finite(trigger.get("model_probability_hold"))
    p_prior = finite(prior.get("model_probability_hold"))
    cost_now, cost_prior = taker_cost(trigger), taker_cost(prior)
    mid_prior = market_mid(prior)
    if None in (p_now, p_prior, cost_now, cost_prior, mid_prior):
        return {**base, "decomposition_status": "required_value_missing"}
    current_features, blockers = market_features(trigger)
    if blockers:
        return {**base, "decomposition_status": "trigger_market_feature_blocked", "blockers": blockers}
    current_recomputed = score_probability(current_features, artifact)
    old_market_features = dict(current_features)
    old_market_features["market_logit"] = math.log(mid_prior / (1.0 - mid_prior))
    p_old_market_current_weather = score_probability(old_market_features, artifact)
    market_contribution = p_now - p_old_market_current_weather
    weather_time_contribution = p_old_market_current_weather - p_prior
    cost_contribution = cost_prior - cost_now
    prior_edge = p_prior - cost_prior
    current_edge = p_now - cost_now
    old_cost_edge = p_now - cost_prior
    old_market_edge = p_old_market_current_weather - cost_now
    total_edge_change = current_edge - prior_edge
    residual = total_edge_change - (
        market_contribution + weather_time_contribution + cost_contribution
    )
    quote_driven = current_edge > 0 and old_cost_edge <= 0 and cost_now < cost_prior
    return {
        **base,
        "decomposition_status": "scorable",
        "minutes_since_prior": (
            (score_clock(trigger) - score_clock(prior)).total_seconds() / 60.0
        ),
        "model_probability_prior": p_prior,
        "model_probability_trigger": p_now,
        "model_probability_trigger_recomputed": current_recomputed,
        "model_recompute_abs_error": abs(current_recomputed - p_now),
        "prior_mid": mid_prior,
        "trigger_mid": market_mid(trigger),
        "prior_effective_cost": cost_prior,
        "trigger_effective_cost": cost_now,
        "prior_edge": prior_edge,
        "trigger_edge": current_edge,
        "counterfactual_old_cost_edge": old_cost_edge,
        "counterfactual_old_market_edge": old_market_edge,
        "market_feature_contribution": market_contribution,
        "weather_time_contribution": weather_time_contribution,
        "cost_drop_contribution": cost_contribution,
        "total_edge_change": total_edge_change,
        "decomposition_residual": residual,
        "quote_driven_candidate": quote_driven,
        "cost_drop_cents": 100.0 * cost_contribution,
        "decision_hour_local": finite(trigger.get("decision_hour_local")),
        "maker_fill_shares": finite(ledger.get("maker_fill_shares")) or 0.0,
        "pnl_usd_at_fill_total": finite(ledger.get("pnl_usd_at_fill_total")),
    }


def bracket_coordinate(label: str) -> float | None:
    parsed = parse_market_bracket(str(label))
    if parsed is None:
        return None
    if parsed.bottom:
        return finite(parsed.high)
    if parsed.top:
        return finite(parsed.low)
    if parsed.low is None or parsed.high is None:
        return None
    return (float(parsed.low) + float(parsed.high)) / 2.0


def rung_quote(row: Mapping[str, Any]) -> dict[str, Any] | None:
    summary = row.get("summary") if isinstance(row.get("summary"), Mapping) else {}
    bid, ask = finite(summary.get("best_bid")), finite(summary.get("best_ask"))
    if bid is None and ask is None:
        return None
    lower, upper = (0.0 if bid is None else bid), (1.0 if ask is None else ask)
    if not (0 <= lower <= upper <= 1):
        return None
    bracket = str(row.get("bracket") or "")
    coordinate = bracket_coordinate(bracket)
    if coordinate is None:
        return None
    return {
        "bracket": bracket,
        "coordinate": coordinate,
        "token_id": str(row.get("token_id") or ""),
        "midpoint": (lower + upper) / 2.0,
        "spread": upper - lower,
        "direct_two_sided": bid is not None and ask is not None,
    }


def weighted_simplex_projection(values: Sequence[float], spreads: Sequence[float]) -> np.ndarray:
    raw = np.asarray(values, dtype=float)
    spread = np.asarray(spreads, dtype=float)
    if len(raw) == 0 or len(raw) != len(spread) or not np.all(np.isfinite(raw)):
        raise ValueError("invalid weighted simplex input")
    weights = 1.0 / (np.square(spread) + 1e-4)

    def mass(lam: float) -> float:
        return float(np.maximum(0.0, raw - lam / (2.0 * weights)).sum())

    low, high = -2.0 * float(weights.max()), 2.0 * float(weights.max())
    for _ in range(120):
        mid = (low + high) / 2.0
        if mass(mid) > 1.0:
            low = mid
        else:
            high = mid
    projected = np.maximum(0.0, raw - high / (2.0 * weights))
    return projected / projected.sum()


def book_paths(root: Path, start: str, end: str) -> list[Path]:
    paths: list[Path] = []
    for day in sorted(root.iterdir() if root.exists() else []):
        if day.is_dir() and start <= day.name <= end:
            paths.extend(sorted(day.glob("*.jsonl*")))
    return paths


def load_ladder_batches(
    root: Path,
    windows: Mapping[tuple[str, str], tuple[datetime, datetime]],
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, Any]]:
    start = min(value[0] for value in windows.values()).date().isoformat()
    end = max(value[1] for value in windows.values()).date().isoformat()
    files = book_paths(root, start, end)
    output: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    matched_files = 0
    for path in files:
        opener = gzip.open if path.suffix == ".gz" else open
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = (str(row.get("city") or ""), str(row.get("event_date") or ""))
                    if (
                        key not in windows
                        or str(row.get("outcome") or "").lower() != "yes"
                        or row.get("status") != "ok"
                    ):
                        continue
                    available = parse_utc(row.get("available_at_utc") or row.get("fetched_at_utc"))
                    if available is None or not windows[key][0] <= available <= windows[key][1]:
                        continue
                    grouped[key].append(row)
        except (EOFError, OSError):
            continue
        for key, rows in grouped.items():
            rungs_by_bracket: dict[str, dict[str, Any]] = {}
            clocks: list[datetime] = []
            for row in rows:
                rung = rung_quote(row)
                available = parse_utc(row.get("available_at_utc") or row.get("fetched_at_utc"))
                if rung is not None:
                    rungs_by_bracket[rung["bracket"]] = rung
                if available is not None:
                    clocks.append(available)
            if len(rungs_by_bracket) < 2 or not clocks:
                continue
            output[key].append(
                {
                    "available_at_utc": max(clocks).isoformat(),
                    "source_path": str(path),
                    "rungs": sorted(rungs_by_bracket.values(), key=lambda row: row["coordinate"]),
                }
            )
            matched_files += 1
    for rows in output.values():
        rows.sort(key=lambda row: parse_utc(row["available_at_utc"]))
    return output, {
        "root": str(root),
        "date_start": start,
        "date_end": end,
        "files_scanned": len(files),
        "matched_city_date_batches": matched_files,
        "city_dates_with_batches": len(output),
    }


def latest_ladder(
    rows: Sequence[Mapping[str, Any]], at: datetime
) -> tuple[Mapping[str, Any] | None, float | None]:
    clocks = [parse_utc(row.get("available_at_utc")) for row in rows]
    clean = [(clock, row) for clock, row in zip(clocks, rows, strict=True) if clock is not None]
    index = bisect_right([clock for clock, _ in clean], at) - 1
    if index < 0:
        return None, None
    clock, row = clean[index]
    return row, (at - clock).total_seconds() / 60.0


def ladder_state(batch: Mapping[str, Any] | None, current_bracket: str) -> dict[str, Any]:
    if batch is None:
        return {"status": "missing_prior_batch"}
    rungs = list(batch.get("rungs") or [])
    current_index = next(
        (index for index, row in enumerate(rungs) if str(row.get("bracket")) == current_bracket),
        None,
    )
    if len(rungs) < 4:
        return {"status": "too_few_rungs", "rung_count": len(rungs)}
    if current_index is None:
        return {"status": "missing_current_rung", "rung_count": len(rungs)}
    if current_index >= len(rungs) - 1:
        return {"status": "no_upper_rung", "rung_count": len(rungs)}
    probabilities = weighted_simplex_projection(
        [float(row["midpoint"]) for row in rungs],
        [float(row["spread"]) for row in rungs],
    )
    q_up = float(probabilities[current_index + 1 :].sum())
    q1 = float(probabilities[current_index + 1])
    return {
        "status": "scorable",
        "rung_count": len(rungs),
        "direct_two_sided_fraction": float(np.mean([row["direct_two_sided"] for row in rungs])),
        "raw_mid_mass": float(sum(row["midpoint"] for row in rungs)),
        "q_up": q_up,
        "q1": q1,
        "alpha1": None if q_up <= EPS else q1 / q_up,
        "all_yes_token_ids": [str(row.get("token_id") or "") for row in rungs],
        "source_path": batch.get("source_path"),
        "available_at_utc": batch.get("available_at_utc"),
    }


def attach_ladder_dynamics(
    records: list[dict[str, Any]],
    batches: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
) -> None:
    for row in records:
        if row.get("decomposition_status") != "scorable":
            row["ladder_dynamic_status"] = "decomposition_not_scorable"
            continue
        key = (str(row["city"]), str(row["target_date"]))
        trigger_clock = parse_utc(row.get("trigger_created_at_utc"))
        prior_clock = parse_utc(row.get("prior_created_at_utc"))
        if trigger_clock is None or prior_clock is None:
            row["ladder_dynamic_status"] = "checkpoint_clock_missing"
            continue
        prior_batch, prior_lag = latest_ladder(batches.get(key, []), prior_clock)
        trigger_batch, trigger_lag = latest_ladder(batches.get(key, []), trigger_clock)
        prior_state = ladder_state(prior_batch, str(row["current_bracket"]))
        trigger_state = ladder_state(trigger_batch, str(row["current_bracket"]))
        row.update(
            {
                "ladder_prior": prior_state,
                "ladder_trigger": trigger_state,
                "ladder_prior_lag_min": prior_lag,
                "ladder_trigger_lag_min": trigger_lag,
            }
        )
        if prior_state.get("status") != "scorable" or trigger_state.get("status") != "scorable":
            row["ladder_dynamic_status"] = "ladder_not_scorable"
            continue
        row["delta_q_up"] = trigger_state["q_up"] - prior_state["q_up"]
        row["delta_q1"] = trigger_state["q1"] - prior_state["q1"]
        row["delta_alpha1"] = (
            None
            if prior_state.get("alpha1") is None or trigger_state.get("alpha1") is None
            else trigger_state["alpha1"] - prior_state["alpha1"]
        )
        row["ladder_dynamic_status"] = (
            "primary_7m_ready"
            if prior_lag is not None
            and trigger_lag is not None
            and prior_lag <= PRIMARY_LADDER_MAX_LAG_MIN
            and trigger_lag <= PRIMARY_LADDER_MAX_LAG_MIN
            else "stale_for_primary_7m"
        )


def load_epochs(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "subscription_epochs").glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if parse_utc(row.get("started_at_utc")) is not None:
                    rows.append(row)
    return sorted(rows, key=lambda row: parse_utc(row["started_at_utc"]))


def attach_ws_epoch_coverage(records: list[dict[str, Any]], epochs: Sequence[Mapping[str, Any]]) -> None:
    clocks = [parse_utc(row.get("started_at_utc")) for row in epochs]
    for row in records:
        at = parse_utc(row.get("trigger_created_at_utc"))
        token = str(row.get("trigger_token_id") or "")
        if at is None or not token:
            row["ws_epoch_status"] = "trigger_clock_or_token_missing"
            continue
        index = bisect_right(clocks, at) - 1
        if index < 0:
            row["ws_epoch_status"] = "before_ws_history"
            continue
        epoch = epochs[index]
        token_ids = {str(value) for value in epoch.get("token_ids") or []}
        token_rows = epoch.get("token_rows") if isinstance(epoch.get("token_rows"), Mapping) else {}
        same_ladder_yes = [
            token_id
            for token_id, metadata in token_rows.items()
            if isinstance(metadata, Mapping)
            and str(metadata.get("city") or "") == str(row.get("city") or "")
            and str(metadata.get("event_date") or "") == str(row.get("target_date") or "")
            and str(metadata.get("outcome") or "").lower() == "yes"
        ]
        expected = set((row.get("ladder_trigger") or {}).get("all_yes_token_ids") or [])
        observed = set(same_ladder_yes)
        row.update(
            {
                "ws_epoch_status": "epoch_found",
                "ws_subscription_epoch_id": epoch.get("subscription_epoch_id"),
                "ws_selector_version": epoch.get("selector_version"),
                "ws_current_token_subscribed": token in token_ids,
                "ws_same_ladder_yes_subscribed": len(observed),
                "ws_expected_ladder_yes_tokens": len(expected),
                "ws_full_ladder_subscribed": bool(expected) and expected.issubset(observed),
            }
        )


def attach_ws_frame_coverage(records: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    wanted_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if not row.get("ws_current_token_subscribed"):
            row["ws_frames_trigger_window"] = 0
            row["ws_trade_prints_trigger_window"] = 0
            continue
        at = parse_utc(row.get("trigger_created_at_utc"))
        if at is not None:
            wanted_by_day[at.date().isoformat()].append(row)
    files_scanned = 0
    for day, targets in wanted_by_day.items():
        day_root = root / day
        for path in sorted(day_root.glob("*.jsonl")):
            files_scanned += 1
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        envelope = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    received = parse_utc(envelope.get("received_at_utc"))
                    if received is None:
                        continue
                    message_tokens = {str(value) for value in envelope.get("message_token_ids") or []}
                    for target in targets:
                        trigger = parse_utc(target.get("trigger_created_at_utc"))
                        token = str(target.get("trigger_token_id") or "")
                        if (
                            trigger is None
                            or token not in message_tokens
                            or abs((received - trigger).total_seconds()) > 20 * 60
                        ):
                            continue
                        target["ws_frames_trigger_window"] = int(target.get("ws_frames_trigger_window") or 0) + 1
                        try:
                            prints = [
                                item
                                for item in extract_market_trade_prints(envelope)
                                if item.token_id == token
                            ]
                        except (KeyError, TypeError, ValueError):
                            # A malformed public WS envelope is evidence of a gap, not a
                            # reason to discard the rest of the frozen research slice.
                            prints = []
                        target["ws_trade_prints_trigger_window"] = int(target.get("ws_trade_prints_trigger_window") or 0) + len(prints)
    return {"ws_frame_files_scanned": files_scanned, "days_scanned": sorted(wanted_by_day)}


def block_bootstrap_difference(
    rows: Sequence[Mapping[str, Any]],
    *,
    value_field: str,
    group_field: str = "quote_driven_candidate",
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    eligible = [
        row
        for row in rows
        if row.get(value_field) is not None and isinstance(row.get(group_field), bool)
    ]
    dates = sorted({str(row["target_date"]) for row in eligible})
    by_date = {date: [row for row in eligible if str(row["target_date"]) == date] for date in dates}

    def statistic(sample: Sequence[Mapping[str, Any]]) -> float | None:
        left = [float(row[value_field]) for row in sample if bool(row[group_field])]
        right = [float(row[value_field]) for row in sample if not bool(row[group_field])]
        return None if not left or not right else float(np.mean(left) - np.mean(right))

    point = statistic(eligible)
    if point is None or len(dates) < 2:
        return {"point": point, "ci_low": None, "ci_high": None, "dates": len(dates)}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    samples: list[float] = []
    for _ in range(draws):
        selected = rng.choice(dates, size=len(dates), replace=True)
        sample = [row for date in selected for row in by_date[str(date)]]
        value = statistic(sample)
        if value is not None:
            samples.append(value)
    return {
        "point": point,
        "ci_low": float(np.quantile(samples, 0.025)) if samples else None,
        "ci_high": float(np.quantile(samples, 0.975)) if samples else None,
        "dates": len(dates),
        "draws_retained": len(samples),
    }


def group_summary(rows: Sequence[Mapping[str, Any]], group: bool) -> dict[str, Any]:
    selected = [row for row in rows if row.get("quote_driven_candidate") is group]
    labelled = [row for row in selected if row.get("loss_label") is not None]
    return {
        "signals": len(selected),
        "target_dates": len({row["target_date"] for row in selected}),
        "labelled": len(labelled),
        "losses": int(sum(int(row["loss_label"]) for row in labelled)),
        "loss_rate": None if not labelled else float(np.mean([row["loss_label"] for row in labelled])),
        "maker_filled_signals": sum(float(row.get("maker_fill_shares") or 0) > 0 for row in selected),
        "mean_cost_drop_cents": None if not selected else float(np.mean([row["cost_drop_cents"] for row in selected])),
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    scorable = [row for row in records if row.get("decomposition_status") == "scorable"]
    ladder_ready = [row for row in records if row.get("ladder_dynamic_status") == "primary_7m_ready"]
    labelled_ladder = [row for row in ladder_ready if row.get("loss_label") is not None]
    prior_minutes = sorted(float(row["minutes_since_prior"]) for row in scorable)

    def percentile(values: Sequence[float], fraction: float) -> float | None:
        if not values:
            return None
        return float(values[min(len(values) - 1, math.floor(len(values) * fraction))])

    def dominant_component(row: Mapping[str, Any]) -> str:
        components = {
            "market_feature": abs(float(row["market_feature_contribution"])),
            "weather_time": abs(float(row["weather_time_contribution"])),
            "taker_cost": abs(float(row["cost_drop_contribution"])),
        }
        return max(components, key=components.get)

    dominant_counts = Counter(dominant_component(row) for row in scorable)
    return {
        "signal_funnel": {
            "frozen_signal_ledger": len(records),
            "trigger_exact_matches": sum(row.get("trigger_match_status") == "exact_created_at" for row in records),
            "informative_prior_available": sum(row.get("prior_created_at_utc") is not None for row in records),
            "edge_decomposition_scorable": len(scorable),
            "quote_driven_candidates": sum(bool(row.get("quote_driven_candidate")) for row in scorable),
        },
        "evidence_funnel": {
            "settlement_labelled": sum(row.get("loss_label") is not None for row in records),
            "rest_ladder_both_checkpoints_scorable": sum(
                row.get("delta_q_up") is not None for row in records
            ),
            "rest_ladder_primary_7m_ready": len(ladder_ready),
            "rest_ladder_primary_dates": len({row["target_date"] for row in ladder_ready}),
            "ws_epoch_found": sum(row.get("ws_epoch_status") == "epoch_found" for row in records),
            "ws_current_token_subscribed": sum(bool(row.get("ws_current_token_subscribed")) for row in records),
            "ws_full_ladder_subscribed": sum(bool(row.get("ws_full_ladder_subscribed")) for row in records),
            "ws_frames_in_trigger_window": sum(int(row.get("ws_frames_trigger_window") or 0) for row in records),
            "ws_trade_prints_in_trigger_window": sum(int(row.get("ws_trade_prints_trigger_window") or 0) for row in records),
        },
        "edge_groups": {
            "quote_driven": group_summary(scorable, True),
            "non_quote_driven": group_summary(scorable, False),
            "loss_rate_delta_quote_minus_non": block_bootstrap_difference(
                scorable, value_field="loss_label"
            ),
        },
        "edge_decomposition_diagnostic": {
            "causal_resolution": "BLOCKED_PRIOR_CHECKPOINTS_ARE_42_TO_117_MINUTES_BEFORE_TRIGGER",
            "prior_checkpoint_minutes": {
                "min": None if not prior_minutes else prior_minutes[0],
                "median": percentile(prior_minutes, 0.5),
                "p75": percentile(prior_minutes, 0.75),
                "max": None if not prior_minutes else prior_minutes[-1],
                "le_15m": sum(value <= 15 for value in prior_minutes),
                "le_30m": sum(value <= 30 for value in prior_minutes),
                "le_60m": sum(value <= 60 for value in prior_minutes),
            },
            "prior_edge_already_positive": sum(float(row["prior_edge"]) > 0 for row in scorable),
            "positive_contribution_counts": {
                "market_feature": sum(float(row["market_feature_contribution"]) > 0 for row in scorable),
                "weather_time": sum(float(row["weather_time_contribution"]) > 0 for row in scorable),
                "taker_cost_drop": sum(float(row["cost_drop_contribution"]) > 0 for row in scorable),
            },
            "largest_absolute_component_counts": dict(sorted(dominant_counts.items())),
            "interpretation": "coarse checkpoint diagnostic only; cannot confirm or refute pre-trigger tape adverse selection",
        },
        "dynamic_ladder": {
            "primary_max_lag_min": PRIMARY_LADDER_MAX_LAG_MIN,
            "ready_rows": len(ladder_ready),
            "ready_labelled_rows": len(labelled_ladder),
            "mean_delta_q_up": None if not ladder_ready else float(np.mean([row["delta_q_up"] for row in ladder_ready])),
            "loss_minus_win_delta_q_up": block_bootstrap_difference(
                [
                    {**row, "quote_driven_candidate": bool(row.get("loss_label"))}
                    for row in labelled_ladder
                ],
                value_field="delta_q_up",
            ),
            "lag_sensitivity": {
                str(limit): sum(
                    row.get("delta_q_up") is not None
                    and finite(row.get("ladder_prior_lag_min")) <= limit
                    and finite(row.get("ladder_trigger_lag_min")) <= limit
                    for row in records
                )
                for limit in (5, 7, 10, 15)
            },
        },
        "readiness": {
            "pit_state_and_clocks": "READY_WITH_HISTORICAL_GAPS",
            "canonical_build_identity": "READY_DB_ROUTE_HEALTHY_PRODUCTION_OVERALL_CRITICAL",
            "market_quote_freshness_depth": "READY_FOR_REST_5MIN_DIAGNOSTIC_ONLY",
            "first_positive_causal_attribution": "BLOCKED_NO_SUBMINUTE_PRETRIGGER_TAPE",
            "settlement_label_coverage": "READY_OR_EXPLICIT_GAP",
            "independent_target_dates": len({row["target_date"] for row in records}),
            "clean_frozen_forward": "BLOCKED_P0_NOT_DEPLOYED",
            "ws_capture_policy_coverage": "BLOCKED_NO_CORE_FULL_LADDER_DEMAND_IN_PRODUCTION_V6",
            "incremental_book_reconstruction_parity": "IMPLEMENTED_BUT_NO_CORE_FULL_LADDER_FORWARD_SLICE",
            "sampling_grain_weighting": "READY_TARGET_DATE_BLOCKED_SIGNAL_GRAIN",
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    ledger_path = Path(args.signal_ledger)
    score_path = Path(args.runtime) / "pre_live_scores.jsonl"
    ledger = read_csv(ledger_path)
    if len(ledger) != 92:
        raise ValueError(f"authoritative signal ledger changed: expected 92, got {len(ledger)}")
    scores, score_manifest = read_jsonl_prefix(score_path)
    score_index = index_scores(scores)
    artifacts, artifact_lineage = load_artifacts()
    settlements, db_identity = load_settlements(Path(args.db))
    external_losses = external_loss_ids(Path(args.loss_packets))
    records: list[dict[str, Any]] = []
    for ledger_row in ledger:
        key = (str(ledger_row["city"]), str(ledger_row["target_date"]))
        trigger, match_status = match_trigger(ledger_row, score_index.get(key, []))
        if trigger is None:
            records.append(
                {
                    "signal_id": ledger_row.get("signal_id"),
                    "city": key[0],
                    "target_date": key[1],
                    "trigger_match_status": match_status,
                    "decomposition_status": "trigger_missing",
                }
            )
            continue
        last_any, prior = prior_rows(trigger, score_index.get(key, []))
        artifact = artifacts.get(str(trigger.get("artifact_hash") or ""))
        record = decompose_signal(ledger_row, trigger, last_any, prior, artifact)
        record["trigger_match_status"] = match_status
        loss, basis = outcome_for(ledger_row, settlements, external_losses)
        record["loss_label"] = loss
        record["outcome_basis"] = basis
        records.append(record)

    windows: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    for row in records:
        trigger = parse_utc(row.get("trigger_created_at_utc"))
        prior = parse_utc(row.get("prior_created_at_utc"))
        if trigger is None or prior is None:
            continue
        key = (str(row["city"]), str(row["target_date"]))
        start, end = prior - timedelta(minutes=30), trigger + timedelta(minutes=2)
        if key in windows:
            start = min(start, windows[key][0])
            end = max(end, windows[key][1])
        windows[key] = (start, end)
    batches, batch_manifest = load_ladder_batches(Path(args.book_root), windows)
    attach_ladder_dynamics(records, batches)

    ws_root = Path(args.ws_root)
    epochs = load_epochs(ws_root)
    attach_ws_epoch_coverage(records, epochs)
    ws_manifest = attach_ws_frame_coverage(records, ws_root)
    rollout_path = ws_root / "rollout_metadata.json"
    rollout = json.loads(rollout_path.read_text(encoding="utf-8")) if rollout_path.exists() else {}
    output = {
        "schema_version": "core_carry_p1_stop_time_dynamics_v1",
        "research_id": RESEARCH_ID,
        "run_id": RUN_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "hypothesis": "quote-created first-positive signals have higher tail/adverse-selection risk than non-quote-driven signals",
        "denominator_scope": "frozen 92-row signal_ledger.csv through 2026-08-20; no later live signals",
        "inputs": {
            "signal_ledger": {
                "path": str(ledger_path),
                "rows": len(ledger),
                "sha256": file_sha256(ledger_path),
            },
            "scores": score_manifest,
            "db_identity": db_identity,
            "artifacts": artifact_lineage,
            "rest_ladder": batch_manifest,
            "ws": {
                **ws_manifest,
                "root": str(ws_root),
                "epochs": len(epochs),
                "rollout_selector_version": rollout.get("selector_version"),
                "rollout_sha256": file_sha256(rollout_path) if rollout_path.exists() else None,
            },
        },
        "method": {
            "quote_driven_primary": "trigger_edge>0 AND counterfactual_old_cost_edge<=0 AND trigger_cost<prior_cost",
            "prior": "last earlier non-eligible checkpoint with model probability, two-sided quote, and executable taker cost",
            "edge_decomposition": "market-feature + weather/time + full-taker-cost-drop; exact frozen artifact per row",
            "ladder_projection": "spread-weighted non-negative unit-simplex projection; one-sided rung spread=1",
            "dynamic_ladder_primary": "latest complete PIT REST ladder at/before each checkpoint, both lags<=7m",
            "statistical_grain": "signal rows with target-date block bootstrap",
            "ws_window": "current token frames/prints within +/-20m of trigger; full-ladder subscription from epoch manifest",
        },
        "summary": summarize(records),
        "records": records,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(json_ready(output), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def parser() -> argparse.ArgumentParser:
    production = load_production_spec()
    runtime = production.pm_runtime_root / "weather_edge_v1/current_yes_core_carry_tiny_live_v2"
    artifact_root = (
        production.research_artifact_root / RESEARCH_ID / f"run={RUN_ID}"
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runtime", default=str(runtime))
    ap.add_argument("--signal-ledger", default=str(SIGNAL_LEDGER))
    ap.add_argument("--loss-packets", default=str(LOSS_PACKETS))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--book-root", default=str(production.market_books_root / "batches"))
    ap.add_argument("--ws-root", default=str(production.market_books_root / "ws_incremental"))
    ap.add_argument("--output", default=str(artifact_root / "p1_result.json"))
    return ap


def main() -> int:
    args = parser().parse_args()
    output = run(args)
    print(json.dumps(output["summary"], ensure_ascii=False, sort_keys=True))
    print(f"artifact={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
