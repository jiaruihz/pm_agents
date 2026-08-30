#!/usr/bin/env python3
"""Tiny-live adapter for the current-YES residual carry probe.

The signal/checkpoint contract remains owned by
``weather_current_yes_core_carry_pre_live_v1.py`` and the no-age/no-peak-clock
v3 artifact.
For each first positive-EV city-day signal this adapter submits two separately
attributed children:

* taker: fresh full-ladder ten-share EV is revalidated immediately before send;
* shared maker: one five-share order at best bid + one tick.  If it remains
  unfilled for five minutes, it is cancelled first and only then replaced once
  at the static pullback price (entry ask minus two cents).  The two phases
  share one exposure budget and can never coexist.

Maker replacements never cross the ask and never convert to taker. They are
cancelled 90 seconds before the next expected source report, when an unexpected
observation epoch arrives, or when the 15-minute parent TTL expires.  The clock
is intentionally based on source-report time rather than this collector's later
availability: another participant may receive the report first.  A maker first
seen inside that blackout is deferred until a new weather epoch is observed,
then re-scored against the frozen Core model and a fresh executable ladder.  It
is armed only when the exact-bracket token is unchanged, taker net-EV remains
positive, its limit is below both the current and parent taker ask, and the
original city-day remains below the fixed twenty-share cap.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_current_yes_core_carry_pre_live_v1 as signal_runner  # noqa: E402
from scripts.ops import weather_current_yes_heat_death_shadow_v1 as weather_state  # noqa: E402
from scripts.ops.weather_core_carry_order_runtime import (  # noqa: E402
    execute_core_carry_plans,
)
from scripts.ops.weather_market_proxy import market_httpx_client  # noqa: E402
from src.platform.market_data.capture_demand import CaptureDemand  # noqa: E402
from src.strategies.runtime import runtime_state  # noqa: E402
from src.strategies.weather_edge_v1.execution.engine import (  # noqa: E402
    allocate_profile_shares,
    build_core_carry_legacy_plan_compatibility,
)
from src.strategies.weather_edge_v1.execution import near_core_maker_probe  # noqa: E402
from src.strategies.weather_edge_v1.execution.profiles import (  # noqa: E402
    execution_config_id_for_profile,
    get_execution_profile,
)
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    evaluate_entry,
    load_artifact,
    maker_resting_price,
)
from weather_data_feed.observation_cache import index_observation_cache  # noqa: E402


STRATEGY_ID = "current_yes_core_carry_v3"
STRATEGY_INSTANCE = "current_yes_core_carry_tiny_live_v2"
CONFIG_ID = "current_yes_core_carry_model_v3_10_taker_5_shared_maker_v7"
EXECUTION_PROFILE = "split_taker_shared_maker_staged_to_pullback_v7"
DEPLOYMENT_CONTRACT_VERSION = "core_carry_v3_10t5m_shared_staged_pullback_v7"
MODEL_VERSION = "current_yes_core_carry_model_v3_no_peak_clock"
FROZEN_TAKER_SHARES = 10.0
FROZEN_MAKER_SHARES = 5.0
FROZEN_PULLBACK_MAKER_SHARES = 0.0
OUTPUT_DIR = ROOT / "runtime/weather_edge_v1" / STRATEGY_INSTANCE
ARTIFACT_PATH = ROOT / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json"
LEGACY_FAMILY_LIVE_ORDER_FILES = (
    ROOT
    / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_h1_late_carry_v1/live_orders.jsonl",
    ROOT
    / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_h2_early_dislocation_v1/live_orders.jsonl",
    ROOT / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_v1/live_orders.jsonl",
)
BJ = timezone(timedelta(hours=8))
RETRYABLE_MAKER_POST_FAILURES = {
    "current_yes_residual_maker_no_resting_price",
    "maker_only_no_resting_price",
    "maker_only_price_would_cross",
    "post_only_crosses_book",
}
CRITICAL_SOURCE_PATHS = (
    "scripts/ops/weather_current_yes_core_carry_tiny_live_v2.py",
    "scripts/ops/weather_current_yes_core_carry_pre_live_v1.py",
    "scripts/ops/weather_current_yes_heat_death_shadow_v1.py",
    "scripts/ops/weather_core_carry_order_runtime.py",
    "scripts/ops/weather_polymarket_live_transport.py",
    "weather_data_feed/observation_cache.py",
    "weather_data_feed/physical_features.py",
    "weather_feature_layer/builders.py",
    "src/strategies/weather_edge_v1/execution/contracts.py",
    "src/strategies/weather_edge_v1/execution/engine.py",
    "src/strategies/weather_edge_v1/execution/profiles.py",
    "src/strategies/weather_edge_v1/execution/lifecycle.py",
    "src/strategies/weather_edge_v1/execution/near_core_maker_probe.py",
    "src/strategies/weather_edge_v1/execution/venue/polymarket.py",
    "src/strategies/weather_edge_v1/runtime/execution_journal.py",
    "src/strategies/weather_edge_v1/runtime/order_runtime.py",
    "src/strategies/weather_edge_v1/tools/current_yes_core_carry.py",
    "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v3.json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def deployment_metadata() -> dict[str, Any]:
    sha = ""
    dirty = True
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = (
            subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "--", *CRITICAL_SOURCE_PATHS],
                cwd=ROOT,
                check=False,
            ).returncode
            != 0
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "deployment_contract_version": DEPLOYMENT_CONTRACT_VERSION,
        "deployed_repo_sha": sha,
        "critical_source_dirty": dirty,
        "source_checkout_root": str(ROOT),
    }


DEPLOYMENT_METADATA = deployment_metadata()


def assert_runtime_contract() -> None:
    """Fail closed before scoring or ordering if the deployed PIT semantics drift."""

    if not DEPLOYMENT_METADATA["deployed_repo_sha"]:
        raise RuntimeError("deployment contract failed: repository SHA unavailable")
    if DEPLOYMENT_METADATA["critical_source_dirty"]:
        raise RuntimeError("deployment contract failed: critical strategy source is dirty")
    selected = index_observation_cache(
        {
            "decision_as_of_utc": "2026-07-26T12:38:10Z",
            "records": [
                {
                    "city": "London",
                    "target_date": "2026-07-26",
                    "fetched_at_utc": "2026-07-26T12:33:00Z",
                    "last_obs_utc": "2026-07-26T12:20:00Z",
                    "current_temp_c": 26.0,
                    "age_min": 13.0,
                },
                {
                    "city": "London",
                    "target_date": "2026-07-26",
                    "fetched_at_utc": "2026-07-25T23:58:00Z",
                    "last_obs_utc": "2026-07-25T23:50:00Z",
                    "current_temp_c": 20.0,
                    "age_min": 8.0,
                },
            ],
        }
    ).get(("London", "2026-07-26"))
    expected_age = 18.0 + 10.0 / 60.0
    if (
        not selected
        or selected.get("current_temp_c") != 26.0
        or abs(float(selected.get("obs_age_minutes") or 0.0) - expected_age) > 1e-9
        or selected.get("obs_age_clock_source") != "decision_asof_minus_source_report"
    ):
        raise RuntimeError("deployment contract failed: observation PIT clock semantics drifted")


def parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


DIRECT_BOOK_CLOCK_STATUS = "direct_clob_response_clock_v1"


def near_core_book_freshness(
    row: Mapping[str, Any],
    *,
    now: datetime,
    max_age_sec: float,
) -> dict[str, Any]:
    """Fail closed on the direct CLOB quote clock used to build an entry.

    Rows written before the direct response-clock lineage was added carry a
    paper-snapshot response clock next to a newer direct-fetch BBO.  For those
    legacy rows the conservative direct-fetch timestamp is the only clock that
    belongs to the scored bid/ask.
    """

    lineage_status = str(row.get("current_yes_book_clock_lineage_status") or "")
    if lineage_status == DIRECT_BOOK_CLOCK_STATUS:
        clock_value = row.get("current_yes_book_response_received_at_utc")
        clock_source = "direct_response_received_at_utc"
    else:
        clock_value = row.get("current_yes_book_fetched_at_utc")
        clock_source = "legacy_direct_fetch_clock_utc"
    clock = parse_utc(clock_value)
    book_status = str(row.get("current_yes_book_status") or "")
    age_sec = (
        (now.astimezone(timezone.utc) - clock).total_seconds()
        if clock is not None
        else None
    )
    common = {
        "near_core_book_clock_utc": clock.isoformat() if clock is not None else "",
        "near_core_book_clock_source": clock_source,
        "near_core_book_status": book_status,
        "near_core_book_max_age_sec": float(max_age_sec),
    }
    if book_status != "ok":
        return {
            **common,
            "near_core_book_age_sec": (
                round(age_sec, 6) if age_sec is not None else None
            ),
            "near_core_book_fresh": False,
            "near_core_book_freshness_status": "direct_book_status_not_ok",
        }
    if clock is None:
        return {
            **common,
            "near_core_book_age_sec": None,
            "near_core_book_fresh": False,
            "near_core_book_freshness_status": "missing_direct_book_clock",
        }
    assert age_sec is not None
    if age_sec < 0.0:
        status = "direct_book_clock_in_future"
        fresh = False
    elif age_sec > float(max_age_sec):
        status = "stale_direct_book_quote"
        fresh = False
    else:
        status = "fresh_direct_book_quote"
        fresh = True
    return {
        **common,
        "near_core_book_age_sec": round(age_sec, 6),
        "near_core_book_fresh": fresh,
        "near_core_book_freshness_status": status,
    }


def stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


MAKER_STATE_SCHEMA_VERSION = "core_carry_maker_weather_state_v1"
MAKER_STATE_EPOCH_PREFIX = "core-weather-state-v1:"


def forecast_curve_hash(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("forecast_values_hash") or "").strip()
    if explicit:
        return explicit
    curve = row.get("hourly_curve")
    if not isinstance(curve, list) or not curve:
        return ""
    return stable_hash({"hourly_curve": curve})


def weather_state_signature(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "observation_epoch": str(row.get("source_report_ts_utc") or ""),
        "observation_source": str(row.get("obs_source") or ""),
        "forecast_curve_hash": forecast_curve_hash(row),
        "forecast_source": str(row.get("forecast_source") or ""),
        "bracket": str(row.get("current_bracket") or row.get("bracket") or ""),
        "token_id": str(
            row.get("current_yes_token_id") or row.get("token_id") or ""
        ),
    }


def weather_state_epoch_ref(row: Mapping[str, Any]) -> str:
    signature = weather_state_signature(row)
    if not signature["observation_epoch"] or not signature["forecast_curve_hash"]:
        return ""
    return MAKER_STATE_EPOCH_PREFIX + stable_hash(signature)


def weather_state_transition_types(
    order: Mapping[str, Any], latest: Mapping[str, Any]
) -> list[str]:
    previous = {
        "observation_epoch": str(order.get("source_report_ts_utc") or ""),
        "observation_source": str(order.get("maker_observation_source") or ""),
        "forecast_curve_hash": str(order.get("maker_forecast_curve_hash") or ""),
        "forecast_source": str(order.get("maker_forecast_source") or ""),
        "bracket": str(order.get("bracket") or ""),
        "token_id": str(order.get("token_id") or ""),
    }
    current = weather_state_signature(latest)
    transitions: list[str] = []
    if (
        current["observation_epoch"] != previous["observation_epoch"]
        or current["observation_source"] != previous["observation_source"]
    ):
        source = current["observation_source"].lower()
        transitions.append(
            "new_metar"
            if "metar" in source or "aviation" in source
            else "new_observation"
        )
    if (
        current["forecast_curve_hash"] != previous["forecast_curve_hash"]
        or current["forecast_source"] != previous["forecast_source"]
    ):
        transitions.append("forecast_revision")
    if (current["bracket"], current["token_id"]) != (
        previous["bracket"],
        previous["token_id"],
    ):
        transitions.append("exact_bracket_transition")
    return transitions


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


DECISION_PACKET_SCHEMA_VERSION = "current_yes_core_carry_decision_packet_v1"
CAPTURE_DEMAND_TTL_MINUTES = 30
CAPTURE_DEMAND_MAX_LADDER_TOKENS = 24
CAPTURE_DEMAND_STRATEGY_KEY = "reheat_risk.current_yes"
CANDIDATE_CAPTURE_MAX_CURRENT_TOKENS = 8
CANDIDATE_CAPTURE_TTL_MINUTES = 30


def _decision_packet_json_default(value: Any) -> str:
    """Preserve Decimal values losslessly in the immutable journal."""

    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def append_decision_packet(path: Path, packet: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                dict(packet),
                ensure_ascii=False,
                sort_keys=True,
                default=_decision_packet_json_default,
            )
            + "\n"
        )


@dataclass
class _JsonlTailFile:
    """Process-local view of an append-only JSONL file.

    The offset advances only past complete newline-terminated records.  This is
    deliberately not persisted: a restarted runner takes the same full replay
    path as the old implementation, while a long-lived loop avoids rereading
    its growing journals every interval.
    """

    identity: tuple[int, int] | None = None
    offset: int = 0
    indexed_size: int = 0
    mtime_ns: int | None = None
    rows: list[dict[str, Any]] | None = None

    def refresh(self, path: Path) -> tuple[list[dict[str, Any]], bool]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            changed = self.identity is not None or bool(self.rows)
            self.identity = None
            self.offset = 0
            self.indexed_size = 0
            self.mtime_ns = None
            self.rows = []
            return [], changed

        identity = (stat.st_dev, stat.st_ino)
        reset = (
            self.identity != identity
            or stat.st_size < self.offset
            # A same-size mtime change cannot be an append.  Treat it as a
            # rewrite so a truncate-and-replace that preserves byte length is
            # never missed.  Normal appends have a larger size.
            or (
                self.identity == identity
                and stat.st_size == self.indexed_size
                and self.mtime_ns is not None
                and stat.st_mtime_ns != self.mtime_ns
            )
        )
        if reset:
            self.offset = 0
            self.rows = []

        rows = self.rows if self.rows is not None else []
        new_rows: list[dict[str, Any]] = []
        complete_offset = self.offset
        with path.open("rb") as handle:
            handle.seek(self.offset)
            while True:
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break
                complete_offset = handle.tell()
                try:
                    row = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(row, dict):
                    parsed = dict(row)
                    rows.append(parsed)
                    new_rows.append(parsed)

        self.identity = identity
        self.offset = complete_offset
        self.indexed_size = stat.st_size
        self.mtime_ns = stat.st_mtime_ns
        self.rows = rows
        return new_rows, reset


_HISTORICAL_SCORE_FIELDS = (
    "checkpoint_key",
    "city",
    "target_date",
    "decision_snapshot_ts_utc",
    "as_of_ts_utc",
    "created_at_utc",
    "model_probability_hold",
    "current_yes_bid",
    "current_yes_ask",
    "eligible",
    "reasons",
)


@dataclass(frozen=True)
class _ScoreRowReference:
    offset: int
    length: int
    compact: dict[str, Any]


class _ScoreTailFile:
    """Compact byte-offset index over the large pre-live score journal."""

    def __init__(self) -> None:
        self.identity: tuple[int, int] | None = None
        self.offset = 0
        self.indexed_size = 0
        self.mtime_ns: int | None = None
        self.path: Path | None = None
        self.references: list[_ScoreRowReference] = []
        self.latest_by_checkpoint: dict[str, _ScoreRowReference] = {}

    def refresh(self, path: Path) -> bool:
        try:
            stat = path.stat()
        except FileNotFoundError:
            changed = self.identity is not None or bool(self.references)
            self.identity = None
            self.offset = 0
            self.indexed_size = 0
            self.mtime_ns = None
            self.path = path
            self.references = []
            self.latest_by_checkpoint = {}
            return changed

        identity = (stat.st_dev, stat.st_ino)
        reset = (
            self.identity != identity
            or stat.st_size < self.offset
            or (
                self.identity == identity
                and stat.st_size == self.indexed_size
                and self.mtime_ns is not None
                and stat.st_mtime_ns != self.mtime_ns
            )
        )
        if reset:
            self.offset = 0
            self.references = []
            self.latest_by_checkpoint = {}

        complete_offset = self.offset
        with path.open("rb") as handle:
            handle.seek(self.offset)
            while True:
                start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break
                complete_offset = handle.tell()
                try:
                    row = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                compact = {
                    field: row.get(field) for field in _HISTORICAL_SCORE_FIELDS
                }
                reference = _ScoreRowReference(
                    offset=start,
                    length=len(raw),
                    compact=compact,
                )
                self.references.append(reference)
                checkpoint_key = str(row.get("checkpoint_key") or "")
                if checkpoint_key:
                    self.latest_by_checkpoint[checkpoint_key] = reference

        self.identity = identity
        self.offset = complete_offset
        self.indexed_size = stat.st_size
        self.mtime_ns = stat.st_mtime_ns
        self.path = path
        return reset

    def historical_scores(self) -> list[dict[str, Any]]:
        return [reference.compact for reference in self.references]

    def trigger(self, checkpoint_key: str) -> dict[str, Any] | None:
        reference = self.latest_by_checkpoint.get(checkpoint_key)
        if reference is None or self.path is None:
            return None
        with self.path.open("rb") as handle:
            handle.seek(reference.offset)
            raw = handle.read(reference.length)
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return dict(row) if isinstance(row, dict) else None


class DecisionPacketTailIndex:
    """In-memory tail index for the first-positive decision-packet path."""

    def __init__(self) -> None:
        self._scores = _ScoreTailFile()
        self._would_orders = _JsonlTailFile()
        self._journal = _JsonlTailFile()
        self._pending_would_positions: set[int] = set()

    def refresh(
        self, output_dir: Path
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str], bool]:
        scores_reset = self._scores.refresh(output_dir / "pre_live_scores.jsonl")
        would_new, would_reset = self._would_orders.refresh(output_dir / "would_orders.jsonl")
        _journal_new, journal_reset = self._journal.refresh(output_dir / "decision_packets.jsonl")
        scores = self._scores.historical_scores()
        would_orders = self._would_orders.rows or []
        existing = {
            str(row.get("packet_id") or "")
            for row in (self._journal.rows or [])
        }
        if scores_reset or would_reset or journal_reset:
            self._pending_would_positions = set(range(len(would_orders)))
        else:
            start = len(would_orders) - len(would_new)
            self._pending_would_positions.update(range(start, len(would_orders)))
        pending = [would_orders[index] for index in sorted(self._pending_would_positions)]
        return scores, pending, existing, bool(scores_reset or would_reset or journal_reset)

    def trigger(self, checkpoint_key: str) -> dict[str, Any] | None:
        return self._scores.trigger(checkpoint_key)

    def mark_processed(self, would: Mapping[str, Any]) -> None:
        # Identity is stable because ``pending`` contains references from the
        # retained row list; equality would be ambiguous for duplicate rows.
        for index in self._pending_would_positions.copy():
            if (self._would_orders.rows or [])[index] is would:
                self._pending_would_positions.remove(index)
                return


def _packet_value(
    row: Mapping[str, Any],
    *fields: str,
) -> dict[str, Any]:
    """Return only an already-loaded value, with missing lineage explicit."""

    for field in fields:
        value = row.get(field)
        if value is not None and value != "":
            return {"status": "available", "source_field": field, "value": value}
    return {
        "status": "unavailable",
        "source_field": None,
        "value": None,
        "reason": "missing_in_decision_row",
    }


def _quote_usable(row: Mapping[str, Any]) -> bool:
    ask = finite(row.get("current_yes_ask"))
    return ask is not None and ask >= 0.01


def _checkpoint_packet_ref(row: Mapping[str, Any] | None, *, reason: str) -> dict[str, Any]:
    if row is None:
        return {"status": "unavailable", "reason": reason, "checkpoint": None}
    return {
        "status": "available",
        "reason": None,
        "checkpoint": {
            "checkpoint_key": row.get("checkpoint_key"),
            "created_at_utc": row.get("created_at_utc"),
            "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc"),
            "as_of_ts_utc": row.get("as_of_ts_utc"),
            "model_probability_hold": row.get("model_probability_hold"),
            "current_yes_bid": row.get("current_yes_bid"),
            "current_yes_ask": row.get("current_yes_ask"),
            "eligible": row.get("eligible"),
            "reasons": row.get("reasons"),
            "quote_usable": _quote_usable(row),
        },
    }


def decision_packet_id(row: Mapping[str, Any]) -> str:
    """Stable first-signal identity; it deliberately excludes mutable book data."""

    return "decision-packet-" + stable_hash(
        {
            "schema_version": DECISION_PACKET_SCHEMA_VERSION,
            "strategy_instance": STRATEGY_INSTANCE,
            "config_id": CONFIG_ID,
            "city": str(row.get("city") or ""),
            "target_date": str(row.get("target_date") or ""),
        }
    )


def _prior_checkpoints(trigger: Mapping[str, Any], scores: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Use persisted decision rows only; never reconstruct or fetch evidence."""

    city_day = (str(trigger.get("city") or ""), str(trigger.get("target_date") or ""))
    trigger_clock = parse_utc(
        trigger.get("decision_snapshot_ts_utc") or trigger.get("as_of_ts_utc") or trigger.get("created_at_utc")
    )
    result: list[dict[str, Any]] = []
    for candidate in scores:
        if (str(candidate.get("city") or ""), str(candidate.get("target_date") or "")) != city_day:
            continue
        candidate_clock = parse_utc(
            candidate.get("decision_snapshot_ts_utc")
            or candidate.get("as_of_ts_utc")
            or candidate.get("created_at_utc")
        )
        if candidate_clock is None or (trigger_clock is not None and candidate_clock >= trigger_clock):
            continue
        result.append(dict(candidate))
    return result


def build_decision_packet(
    trigger: Mapping[str, Any],
    *,
    historical_scores: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create a self-contained immutable record from decision-time state only."""

    prior = _prior_checkpoints(trigger, historical_scores)
    last_checkpoint = prior[-1] if prior else None
    negatives = [row for row in prior if not bool(row.get("eligible"))]
    last_negative = negatives[-1] if negatives else None
    informative_quote_usable = [
        row
        for row in negatives
        if finite(row.get("model_probability_hold")) is not None and _quote_usable(row)
    ]
    last_usable_negative = informative_quote_usable[-1] if informative_quote_usable else None
    snapshot_clock = _packet_value(
        trigger, "decision_snapshot_ts_utc", "snapshot_available_at_utc", "snapshot_ts_utc"
    )
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "packet_id": decision_packet_id(trigger),
        "record_type": "current_yes_core_carry_decision_packet",
        "strategy_identity": {
            "strategy_id": STRATEGY_ID,
            "strategy_instance": STRATEGY_INSTANCE,
            "config_id": CONFIG_ID,
            "model_version": MODEL_VERSION,
            "artifact_hash": trigger.get("artifact_hash"),
            "deployment_contract_version": DEPLOYMENT_CONTRACT_VERSION,
        },
        "signal": {
            "signal_id": signal_id(trigger),
            "city": trigger.get("city"),
            "target_date": trigger.get("target_date"),
            "bracket": trigger.get("current_bracket"),
            "condition_id": trigger.get("condition_id") or trigger.get("current_condition_id"),
            "token_id": trigger.get("token_id") or trigger.get("current_yes_token_id"),
            "positive_taker_ev": True,
        },
        "decision_clocks": {
            "created_at_utc": _packet_value(trigger, "created_at_utc"),
            "as_of_ts_utc": _packet_value(trigger, "as_of_ts_utc", "decision_as_of_utc"),
            "snapshot_ts_utc": snapshot_clock,
        },
        "trigger": {"status": "available", "payload": dict(trigger)},
        "prior_checkpoints": {
            "last_checkpoint": _checkpoint_packet_ref(
                last_checkpoint, reason="no_prior_checkpoint_in_decision_history"
            ),
            "last_negative_checkpoint": _checkpoint_packet_ref(
                last_negative, reason="no_prior_negative_checkpoint_in_decision_history"
            ),
            "last_informative_quote_usable_negative_checkpoint": _checkpoint_packet_ref(
                last_usable_negative,
                reason="no_prior_informative_quote_usable_negative_checkpoint",
            ),
        },
        "event_references": {
            "observation": {
                "event_ts_utc": _packet_value(trigger, "source_report_ts_utc", "obs_last_obs_utc"),
                "available_at_utc": _packet_value(trigger, "obs_ingested_at_utc", "observation_available_at_utc", "available_at_utc"),
                "receive_at_utc": _packet_value(trigger, "obs_received_at_utc"),
                "row_id": _packet_value(trigger, "observation_history_id", "obs_row_id"),
            },
            "book": {
                "exchange_ts_utc": _packet_value(trigger, "book_exchange_ts_utc", "current_yes_book_exchange_ts_utc"),
                "available_at_utc": _packet_value(trigger, "book_available_at_utc", "current_yes_book_parsed_at_utc"),
                "receive_at_utc": _packet_value(trigger, "book_received_at_utc", "current_yes_book_response_received_at_utc"),
                "request_started_at_utc": _packet_value(trigger, "current_yes_book_request_started_at_utc"),
                "capture_id": _packet_value(trigger, "book_capture_id", "current_yes_book_batch_capture_id"),
                "snapshot_id": _packet_value(trigger, "book_snapshot_id"),
                "archive_path": _packet_value(trigger, "current_yes_book_archive_path"),
                "clock_lineage_status": _packet_value(trigger, "current_yes_book_clock_lineage_status"),
            },
            "forecast": {
                "values_hash": _packet_value(trigger, "forecast_values_hash"),
                "first_seen_utc": _packet_value(trigger, "forecast_first_seen_utc"),
                "receive_source": _packet_value(trigger, "forecast_receive_source", "forecast_curve_evidence"),
                "source": _packet_value(trigger, "forecast_source"),
                "model": _packet_value(trigger, "forecast_model"),
                "archive_path": _packet_value(trigger, "forecast_curve_archive_path"),
                "model_init_utc_estimated": _packet_value(trigger, "model_init_utc_estimated"),
                "model_init_basis": _packet_value(trigger, "model_init_basis"),
                "valid_from_local": _packet_value(trigger, "forecast_valid_from_local"),
                "valid_to_local": _packet_value(trigger, "forecast_valid_to_local"),
                "lineage_status": _packet_value(trigger, "forecast_lineage_status", "forecast_run_lineage_status"),
                "first_seen_lookup_status": _packet_value(trigger, "forecast_first_seen_lookup_status"),
                "issue_ts_utc": _packet_value(trigger, "forecast_issue_ts_utc"),
            },
        },
        "snapshot_references": {
            "feature_row_id": _packet_value(trigger, "fact_signal_candidate_id", "feature_row_id"),
            "snapshot_file": _packet_value(trigger, "snapshot_file"),
            "data_epoch_refs_json": _packet_value(trigger, "data_epoch_refs_json"),
            "snapshot_capture_id": _packet_value(trigger, "snapshot_capture_id"),
            "snapshot_producer_build_id": _packet_value(trigger, "snapshot_producer_build_id"),
            "book_snapshot": _packet_value(trigger, "book_snapshot_id", "book_snapshot_file", "current_yes_book_archive_path"),
        },
        "order_linkage": {
            "status": "unavailable_at_decision",
            "reason": "packet_is_written_before_order_planning_and_execution",
            "execution_ids": [],
            "order_ids": [],
        },
    }


def write_new_decision_packets(
    output_dir: Path,
    *,
    tail_index: DecisionPacketTailIndex | None = None,
) -> dict[str, Any]:
    """Append first positive signals, while preserving the execution path on failure."""

    journal = output_dir / "decision_packets.jsonl"
    # Direct callers deliberately get a cold full replay, matching a process
    # restart.  The service loop supplies one shared index for hot iterations.
    index = tail_index or DecisionPacketTailIndex()
    scores, would_orders, existing, _reset = index.refresh(output_dir)
    written = 0
    written_packets: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    for would in would_orders:
        checkpoint_key = str(would.get("checkpoint_key") or "")
        trigger = index.trigger(checkpoint_key)
        if trigger is None:
            alerts.append({"status": "alert", "reason": "decision_packet_trigger_score_missing", "checkpoint_key": checkpoint_key})
            continue
        packet = build_decision_packet(trigger, historical_scores=scores)
        if packet["packet_id"] in existing:
            index.mark_processed(would)
            continue
        try:
            append_decision_packet(journal, packet)
        except OSError as exc:
            alerts.append(
                {
                    "status": "alert",
                    "reason": "decision_packet_write_failed",
                    "packet_id": packet["packet_id"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        existing.add(packet["packet_id"])
        index.mark_processed(would)
        written += 1
        written_packets.append(packet)
    for alert in alerts:
        try:
            append_jsonl(output_dir / "decision_packet_alerts.jsonl", alert)
        except OSError:
            pass
    return {"written": written, "alerts": alerts, "written_packets": written_packets}


def write_capture_demands(
    output_dir: Path,
    *,
    packets: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Request a bounded full-ladder YES tape window for newly written packets."""

    journal = output_dir / "capture_demands.jsonl"
    existing = {str(row.get("demand_id") or "") for row in iter_jsonl(journal)}
    written = 0
    alerts: list[dict[str, Any]] = []
    for packet in packets:
        trigger = dict((packet.get("trigger") or {}).get("payload") or {})
        ladder = [dict(row) for row in trigger.get("full_ladder_yes_tokens") or [] if isinstance(row, Mapping)]
        if not ladder:
            alerts.append({
                "status": "alert",
                "reason": "capture_demand_full_ladder_tokens_missing",
                "packet_id": packet.get("packet_id"),
            })
            continue
        if len(ladder) > CAPTURE_DEMAND_MAX_LADDER_TOKENS:
            alerts.append({
                "status": "alert",
                "reason": "capture_demand_full_ladder_token_budget_exceeded",
                "packet_id": packet.get("packet_id"),
                "token_count": len(ladder),
                "max_tokens": CAPTURE_DEMAND_MAX_LADDER_TOKENS,
            })
            continue
        requested = parse_utc(trigger.get("created_at_utc")) or datetime.now(timezone.utc)
        expires = requested + timedelta(minutes=CAPTURE_DEMAND_TTL_MINUTES)
        for rung in ladder:
            token_id = str(rung.get("token_id") or "")
            condition_id = str(rung.get("condition_id") or "")
            if not token_id or not condition_id:
                alerts.append({
                    "status": "alert",
                    "reason": "capture_demand_ladder_identity_missing",
                    "packet_id": packet.get("packet_id"),
                    "bracket": rung.get("bracket"),
                })
                continue
            try:
                demand = CaptureDemand.create(
                    consumer_id=STRATEGY_INSTANCE,
                    strategy_key=CAPTURE_DEMAND_STRATEGY_KEY,
                    condition_id=condition_id,
                    token_id=token_id,
                    reason="core_carry_first_positive_full_ladder_tape",
                    priority="P0",
                    requested_at_utc=requested.isoformat(),
                    expires_at_utc=expires.isoformat(),
                    desired_transport="WS",
                    requested_checkpoints_seconds=(0, 60, 300, 900, 1800),
                    trigger_event_id=str(packet.get("packet_id") or ""),
                    metadata={
                        "city": trigger.get("city"),
                        "target_date": trigger.get("target_date"),
                        "bracket": rung.get("bracket"),
                        "market_id": rung.get("market_id"),
                        "ladder_scope": "all_yes_outcome_tokens",
                        "requested_window_seconds": CAPTURE_DEMAND_TTL_MINUTES * 60,
                        "stop_condition": "demand_expiry",
                        "active_token_budget": CAPTURE_DEMAND_MAX_LADDER_TOKENS,
                        "retention_owner": "canonical_market_books_ws_raw",
                    },
                )
            except ValueError as exc:
                alerts.append({
                    "status": "alert",
                    "reason": "capture_demand_contract_invalid",
                    "packet_id": packet.get("packet_id"),
                    "bracket": rung.get("bracket"),
                    "error": str(exc),
                })
                continue
            if demand.demand_id in existing:
                continue
            try:
                append_jsonl(journal, demand.to_dict())
            except OSError as exc:
                alerts.append({
                    "status": "alert",
                    "reason": "capture_demand_write_failed",
                    "packet_id": packet.get("packet_id"),
                    "demand_id": demand.demand_id,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            existing.add(demand.demand_id)
            written += 1
    for alert in alerts:
        try:
            append_jsonl(output_dir / "capture_demand_alerts.jsonl", alert)
        except OSError:
            pass
    return {"written": written, "alerts": alerts}


def write_candidate_capture_demands(
    output_dir: Path, *, now: datetime | None = None
) -> dict[str, Any]:
    """Declare bounded pre-trigger tape for structurally valid near-core candidates.

    A row is a candidate only when the frozen selector's sole blocker is
    ``non_positive_taker_ev``.  This creates research evidence and never changes
    signal eligibility or order planning.  Current-token coverage is capped at
    eight distinct candidates; only the highest-edge candidate receives an
    atomic full-ladder request.
    """

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    journal = output_dir / "capture_demands.jsonl"
    existing = {str(row.get("demand_id") or "") for row in iter_jsonl(journal)}
    latest_by_token: dict[str, dict[str, Any]] = {}
    for source in iter_jsonl(output_dir / "pre_live_scores.jsonl"):
        row = dict(source)
        token_id = str(row.get("current_yes_token_id") or "")
        if not token_id or set(row.get("reasons") or ()) != {"non_positive_taker_ev"}:
            continue
        edge = row.get("model_edge_after_fee_and_depth")
        requested = parse_utc(row.get("created_at_utc"))
        if (
            requested is None
            or requested > current
            or requested + timedelta(minutes=CANDIDATE_CAPTURE_TTL_MINUTES) <= current
        ):
            continue
        try:
            edge_value = float(edge)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(edge_value) or edge_value > 0:
            continue
        prior = latest_by_token.get(token_id)
        if prior is None or str(row.get("created_at_utc") or "") > str(
            prior.get("created_at_utc") or ""
        ):
            latest_by_token[token_id] = row

    candidates = sorted(
        latest_by_token.values(),
        key=lambda row: (
            float(row["model_edge_after_fee_and_depth"]),
            str(row.get("created_at_utc") or ""),
        ),
        reverse=True,
    )[:CANDIDATE_CAPTURE_MAX_CURRENT_TOKENS]
    written = 0
    alerts: list[dict[str, Any]] = []

    def declare(row: Mapping[str, Any], rung: Mapping[str, Any], *, reason: str, group: str) -> None:
        nonlocal written
        token_id = str(rung.get("token_id") or "")
        condition_id = str(rung.get("condition_id") or "")
        requested = parse_utc(row.get("created_at_utc"))
        if requested is None or not token_id or not condition_id:
            alerts.append(
                {
                    "status": "alert",
                    "reason": "candidate_capture_identity_or_clock_missing",
                    "checkpoint_key": row.get("checkpoint_key"),
                    "token_id": token_id or None,
                }
            )
            return
        try:
            demand = CaptureDemand.create(
                consumer_id=STRATEGY_INSTANCE,
                strategy_key=CAPTURE_DEMAND_STRATEGY_KEY,
                condition_id=condition_id,
                token_id=token_id,
                reason=reason,
                priority="P1",
                requested_at_utc=requested.isoformat(),
                expires_at_utc=(
                    requested + timedelta(minutes=CANDIDATE_CAPTURE_TTL_MINUTES)
                ).isoformat(),
                desired_transport="WS",
                requested_checkpoints_seconds=(0, 30, 60, 120, 300, 900, 1800),
                trigger_event_id=group,
                metadata={
                    "city": row.get("city"),
                    "target_date": row.get("target_date"),
                    "bracket": rung.get("bracket") or row.get("current_bracket"),
                    "market_id": rung.get("market_id") or row.get("current_market_id"),
                    "checkpoint_key": row.get("checkpoint_key"),
                    "candidate_edge": row.get("model_edge_after_fee_and_depth"),
                    "candidate_definition": "sole_blocker_non_positive_taker_ev",
                    "research_only": True,
                    "active_token_budget": CAPTURE_DEMAND_MAX_LADDER_TOKENS,
                    "retention_owner": "canonical_market_books_ws_raw",
                },
            )
        except ValueError as exc:
            alerts.append(
                {
                    "status": "alert",
                    "reason": "candidate_capture_demand_contract_invalid",
                    "checkpoint_key": row.get("checkpoint_key"),
                    "error": str(exc),
                }
            )
            return
        if demand.demand_id in existing:
            return
        try:
            append_jsonl(journal, demand.to_dict())
        except OSError as exc:
            alerts.append(
                {
                    "status": "alert",
                    "reason": "candidate_capture_demand_write_failed",
                    "checkpoint_key": row.get("checkpoint_key"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return
        existing.add(demand.demand_id)
        written += 1

    for row in candidates:
        checkpoint = str(row.get("checkpoint_key") or "")
        created = str(row.get("created_at_utc") or "")
        declare(
            row,
            {
                "token_id": row.get("current_yes_token_id"),
                "condition_id": row.get("current_condition_id"),
                "market_id": row.get("current_market_id"),
                "bracket": row.get("current_bracket"),
            },
            reason="core_carry_candidate_current_token_tape",
            group=f"candidate-current|{checkpoint}|{created}",
        )

    ladder_candidate = next(
        (row for row in candidates if row.get("full_ladder_yes_tokens")), None
    )
    if ladder_candidate is not None:
        top = ladder_candidate
        ladder = [
            dict(row)
            for row in top.get("full_ladder_yes_tokens") or ()
            if isinstance(row, Mapping)
        ]
        if ladder and len(ladder) <= CAPTURE_DEMAND_MAX_LADDER_TOKENS:
            group = (
                f"candidate-ladder|{top.get('checkpoint_key')}|"
                f"{top.get('created_at_utc')}"
            )
            for rung in ladder:
                declare(
                    top,
                    rung,
                    reason="core_carry_candidate_full_ladder_tape",
                    group=group,
                )
        elif ladder:
            alerts.append(
                {
                    "status": "alert",
                    "reason": "candidate_capture_full_ladder_token_budget_exceeded",
                    "checkpoint_key": top.get("checkpoint_key"),
                    "token_count": len(ladder),
                    "max_tokens": CAPTURE_DEMAND_MAX_LADDER_TOKENS,
                }
            )

    for alert in alerts:
        try:
            append_jsonl(output_dir / "capture_demand_alerts.jsonl", alert)
        except OSError:
            pass
    return {"written": written, "alerts": alerts, "candidate_count": len(candidates)}


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def next_runtime_telemetry_rows(conn: sqlite3.Connection) -> int:
    """Advance the operational counter without rescanning the growing journal."""

    row = conn.execute(
        "SELECT telemetry_rows FROM strategy_instance_runtime WHERE instance_id = ?",
        (STRATEGY_INSTANCE,),
    ).fetchone()
    return int(row[0] or 0) + 1 if row else 1


def repo_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def publish_runtime_state(
    args: argparse.Namespace,
    output_dir: Path,
    summary: Mapping[str, Any],
    signal_summary: Mapping[str, Any],
) -> None:
    db_path = Path(args.runtime_db)
    now = str(summary.get("generated_at_utc") or utc_now())
    candidate_rows = int(
        signal_summary.get("checkpoint_candidates")
        or signal_summary.get("scores_written")
        or 0
    )
    plan_rows = int(summary.get("entry_plans") or 0) + int(
        summary.get("maker_lifecycle_plans") or 0
    )
    with sqlite3.connect(db_path, timeout=0.25) as conn:
        conn.execute("PRAGMA busy_timeout=250")
        runtime_state.push_runtime_state(
            conn,
            instance_id=STRATEGY_INSTANCE,
            process_status="running",
            health_status="healthy" if summary.get("status") == "ok" else "blocked",
            pid=os.getpid(),
            heartbeat_at_utc=now,
            last_tick_ts_utc=now,
            last_data_ts_utc=str(signal_summary.get("generated_at_utc") or now),
            latest_summary_ts_utc=now,
            latest_artifact_mtime_utc=now,
            heartbeat_age_min=0.0,
            candidate_rows=candidate_rows,
            plan_rows=plan_rows,
            live_order_rows=line_count(output_dir / "live_orders.jsonl"),
            paper_order_rows=line_count(output_dir / "paper_orders.jsonl"),
            telemetry_rows=next_runtime_telemetry_rows(conn),
            live_enabled=int(bool(summary.get("live_enabled"))),
            summary_path=repo_path(output_dir / "latest_summary.json"),
            primary_journal_path=repo_path(output_dir / "live_orders.jsonl"),
            blocker_count=0,
            blockers_json=[],
            summary_json=dict(summary),
            refreshed_at_utc=now,
        )
        conn.commit()


def publish_runtime_state_best_effort(
    args: argparse.Namespace,
    output_dir: Path,
    summary: dict[str, Any],
    signal_summary: Mapping[str, Any],
) -> None:
    """Keep telemetry contention from changing the trading-loop result."""

    try:
        publish_runtime_state(args, output_dir, summary, signal_summary)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            summary["status"] = "runtime_state_error"
            summary["runtime_state_error"] = f"{type(exc).__name__}: {exc}"
            return
        summary["runtime_state_publish_status"] = "deferred_db_busy"
        summary["runtime_state_publish_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "runtime_state_error"
        summary["runtime_state_error"] = f"{type(exc).__name__}: {exc}"
    else:
        summary["runtime_state_publish_status"] = "published"


def live_order_id(row: Mapping[str, Any]) -> str:
    response = row.get("exchange_response") if isinstance(row.get("exchange_response"), Mapping) else {}
    place = response.get("place") if isinstance(response.get("place"), Mapping) else {}
    for payload in (row, place, response):
        for key in ("order_id", "orderID", "clob_order_id", "id"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    return ""


def signal_id(row: Mapping[str, Any]) -> str:
    return "current-yes-core-carry-" + stable_hash(
        {
            "strategy_instance": STRATEGY_INSTANCE,
            "city": str(row.get("city") or ""),
            "target_date": str(row.get("target_date") or ""),
        }
    )


def latest_rows_by_checkpoint(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        key = str(row.get("checkpoint_key") or "")
        if key:
            latest[key] = row
    return latest


WEATHER_EPOCH_INDEX_SCHEMA_VERSION = "weather_epoch_index_v1"


def weather_epoch_index_path(source_path: Path) -> Path:
    return source_path.with_name("runtime_state_index.sqlite3")


def _weather_epoch_index_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in conn.execute("SELECT key, value FROM index_metadata")
    }


def _prepare_weather_epoch_index(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS index_metadata "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS latest_weather_epochs (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            decision_snapshot_ts_utc TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (city, target_date)
        )
        """
    )


def _set_weather_epoch_index_metadata(
    conn: sqlite3.Connection, values: Mapping[str, Any]
) -> None:
    conn.executemany(
        """
        INSERT INTO index_metadata(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ((str(key), str(value)) for key, value in values.items()),
    )


def _refresh_weather_epoch_index(
    source_path: Path, conn: sqlite3.Connection
) -> None:
    try:
        stat = source_path.stat()
    except FileNotFoundError:
        conn.execute("DELETE FROM latest_weather_epochs")
        conn.execute("DELETE FROM index_metadata")
        conn.commit()
        return

    metadata = _weather_epoch_index_metadata(conn)
    source_identity = f"{stat.st_dev}:{stat.st_ino}"
    try:
        indexed_offset = int(metadata.get("indexed_offset", "0"))
    except ValueError:
        indexed_offset = 0
    reset_required = (
        metadata.get("schema_version") != WEATHER_EPOCH_INDEX_SCHEMA_VERSION
        or metadata.get("source_identity") != source_identity
        or stat.st_size < indexed_offset
    )
    if reset_required:
        conn.execute("DELETE FROM latest_weather_epochs")
        conn.execute("DELETE FROM index_metadata")
        indexed_offset = 0

    complete_offset = indexed_offset
    with source_path.open("rb") as handle:
        handle.seek(indexed_offset)
        while True:
            raw = handle.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                break
            complete_offset = handle.tell()
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            stamp = parse_utc(row.get("decision_snapshot_ts_utc"))
            if not city or not target_date or stamp is None:
                continue
            stamp_text = stamp.isoformat()
            conn.execute(
                """
                INSERT INTO latest_weather_epochs(
                    city, target_date, decision_snapshot_ts_utc, payload_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(city, target_date) DO UPDATE SET
                    decision_snapshot_ts_utc = excluded.decision_snapshot_ts_utc,
                    payload_json = excluded.payload_json
                WHERE excluded.decision_snapshot_ts_utc
                    > latest_weather_epochs.decision_snapshot_ts_utc
                """,
                (
                    city,
                    target_date,
                    stamp_text,
                    json.dumps(row, ensure_ascii=False, sort_keys=True),
                ),
            )

    _set_weather_epoch_index_metadata(
        conn,
        {
            "schema_version": WEATHER_EPOCH_INDEX_SCHEMA_VERSION,
            "source_path": str(source_path.resolve()),
            "source_identity": source_identity,
            "indexed_offset": complete_offset,
        },
    )
    conn.commit()


def latest_weather_epochs(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    index_path = weather_epoch_index_path(path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(index_path, timeout=30.0) as conn:
        _prepare_weather_epoch_index(conn)
        _refresh_weather_epoch_index(path, conn)
        return {
            (str(city), str(target_date)): json.loads(payload_json)
            for city, target_date, payload_json in conn.execute(
                "SELECT city, target_date, payload_json FROM latest_weather_epochs"
            )
        }


def maker_lifecycle_root(row: Mapping[str, Any]) -> str:
    root_created = str(row.get("maker_lifecycle_root_created_at_utc") or "")
    signal = str(row.get("signal_id") or "")
    comparison = str(row.get("comparison_group_id") or "")
    maker_arm = str(row.get("maker_arm") or "legacy_staged")
    return "|".join((signal, comparison, maker_arm, root_created))


def maker_lifecycle_heads(path: Path) -> list[dict[str, Any]]:
    """Return the latest journal row for every maker intent."""

    heads: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for index, row in enumerate(iter_jsonl(path)):
        if (
            str(row.get("strategy_instance") or "") != STRATEGY_INSTANCE
            or not bool(row.get("maker_only"))
        ):
            continue
        root = maker_lifecycle_root(row)
        created = parse_utc(row.get("created_at_utc"))
        if not root or created is None:
            continue
        prior = heads.get(root)
        if prior is None or (created, index) > (prior[0], prior[1]):
            heads[root] = (created, index, row)
    return [item[2] for item in heads.values()]


def execution_journal_order_states(path: Path) -> dict[str, dict[str, Any]]:
    """Recover venue-order terminal state from side-effect outcomes.

    The execution journal is written before the legacy ``live_orders`` projection.
    A process interruption between those writes must not leave an already-cancelled
    order looking active forever.
    """

    states: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if str(row.get("event_type") or "") != "outcome":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        kind = str(payload.get("kind") or "")
        status = str(payload.get("status") or "").lower()
        raw = payload.get("raw_response") if isinstance(payload.get("raw_response"), Mapping) else {}
        recorded_at = str(row.get("recorded_at_utc") or "")
        if kind == "submit" and status in {"submitted", "accepted", "filled", "reconciled"}:
            order_id = str(
                payload.get("venue_order_id")
                or raw.get("orderID")
                or raw.get("order_id")
                or raw.get("id")
                or ""
            )
            if order_id:
                states[order_id] = {
                    "status": "filled" if status == "filled" else "submitted",
                    "recorded_at_utc": recorded_at,
                    "journal_payload": dict(payload),
                }
        if kind not in {"cancel", "cancel_before_replacement"}:
            continue
        if status not in {"cancelled", "canceled", "expired", "reconciled"}:
            continue
        cancelled = raw.get("canceled") or raw.get("cancelled") or ()
        for value in cancelled:
            order_id = str(value or "")
            if order_id:
                states[order_id] = {
                    "status": "cancelled",
                    "recorded_at_utc": recorded_at,
                    "journal_payload": dict(payload),
                }
    return states


def recover_journal_terminal_makers(output_dir: Path) -> int:
    """Project journal-confirmed terminal heads into the legacy order stream."""

    live_orders = output_dir / "live_orders.jsonl"
    states = execution_journal_order_states(output_dir / "execution_journal.jsonl")
    existing_execution_ids = {
        str(row.get("execution_id") or "") for row in iter_jsonl(live_orders)
    }
    written = 0
    for head in maker_lifecycle_heads(live_orders):
        if str(head.get("status") or "") != "submitted":
            continue
        order_id = live_order_id(head)
        evidence = states.get(order_id)
        if not order_id or not evidence or evidence["status"] not in {"cancelled", "filled"}:
            continue
        execution_id = "journal-recovery-" + stable_hash(
            {"order_id": order_id, "terminal_status": evidence["status"]}
        )
        if execution_id in existing_execution_ids:
            continue
        terminal = {
            **dict(head),
            "record_type": "weather_edge_live_order",
            "created_at_utc": evidence["recorded_at_utc"] or utc_now(),
            "status": evidence["status"],
            "child_order_role": "core_carry_maker_terminal",
            "execution_action": "core_carry_maker_terminal",
            "execution_id": execution_id,
            "plan_id": "plan-" + execution_id,
            "replacement_of_order_id": order_id,
            "source_order_id": order_id,
            "exchange_response": {
                "quote_status": evidence["status"],
                "quote_reason": "execution_journal_terminal_recovery",
                "authoritative_execution_journal": evidence["journal_payload"],
            },
        }
        append_jsonl(live_orders, terminal)
        existing_execution_ids.add(execution_id)
        written += 1
    return written


def retryable_maker_post_failure(row: Mapping[str, Any]) -> bool:
    if str(row.get("maker_arm") or "") == "pullback":
        return False
    if str(row.get("status") or "") != "error":
        return False
    response = row.get("exchange_response") if isinstance(row.get("exchange_response"), Mapping) else {}
    classification = str(response.get("error_classification") or "")
    return classification in RETRYABLE_MAKER_POST_FAILURES


def family_live_order_files(output_dir: Path) -> tuple[Path, ...]:
    return (*LEGACY_FAMILY_LIVE_ORDER_FILES, output_dir / "live_orders.jsonl")


def submitted_city_days(paths: Iterable[Path]) -> set[tuple[str, str]]:
    return {
        (str(row.get("city") or ""), str(row.get("target_date") or ""))
        for path in paths
        for row in iter_jsonl(path)
        if str(row.get("status") or "") == "submitted"
        and str(row.get("strategy_instance") or "")
        != near_core_maker_probe.STRATEGY_INSTANCE
        and str(row.get("city") or "")
        and str(row.get("target_date") or "")
    }


def attempted_signal_ids(output_dir: Path) -> set[str]:
    attempted = {
        str(row.get("signal_id") or "")
        for row in iter_jsonl(output_dir / "entry_attempts.jsonl")
        if str(row.get("signal_id") or "")
        and str(row.get("status") or "") == "blocked"
    }
    attempted.update(
        str(row.get("signal_id") or "")
        for row in iter_jsonl(output_dir / "live_orders.jsonl")
        if str(row.get("signal_id") or "")
    )
    return attempted


def attempted_child_roles(
    output_dir: Path, *, now: datetime | None = None
) -> dict[str, set[str]]:
    """Return terminally attempted entry children, without collapsing the batch.

    A taker outcome must not consume a maker child that never reached a durable
    order outcome.  Conversely, any projected maker outcome is owned by the
    maker lifecycle and must not be recreated by the entry path.
    """

    attempted: dict[str, set[str]] = {}
    for row in iter_jsonl(output_dir / "entry_attempts.jsonl"):
        sid = str(row.get("signal_id") or "")
        if not sid:
            continue
        if str(row.get("status") or "") == "blocked":
            attempted.setdefault(sid, set()).update(
                {"taker", "maker_staged", "maker_pullback"}
            )
        elif str(row.get("maker_live_action") or "") == "skip_terminal":
            if bool(row.get("maker_rearm_terminal")):
                attempted.setdefault(sid, set()).update(
                    {"maker_staged", "maker_pullback"}
                )
                continue
            created = parse_utc(row.get("created_at_utc"))
            rearm_enabled = bool(
                get_execution_profile(EXECUTION_PROFILE).fixed_parameters.get(
                    "post_update_live_rearm"
                )
            )
            rearm_max_age = maker_profile_parameter("post_update_rearm_max_age_sec")
            if (
                rearm_enabled
                and now is not None
                and created is not None
                and 0 <= (now - created).total_seconds() <= rearm_max_age
            ):
                # A pre-v6 terminal skip inside the bounded migration window is
                # reinterpreted as deferred.  It still requires a newer weather
                # epoch and a fresh positive-EV score before any order exists.
                continue
            arm = str(row.get("maker_arm") or "")
            if arm in {"staged", "pullback"}:
                attempted.setdefault(sid, set()).add(f"maker_{arm}")
            else:
                attempted.setdefault(sid, set()).update(
                    {"maker_staged", "maker_pullback"}
                )
    for row in iter_jsonl(output_dir / "live_orders.jsonl"):
        sid = str(row.get("signal_id") or "")
        if not sid:
            continue
        role = str(row.get("child_order_role") or "")
        if role == "taker":
            attempted.setdefault(sid, set()).add("taker")
        elif role == "maker":
            # Legacy single-maker signals must not receive a retrospective
            # pullback child when the dual-maker profile is deployed.
            attempted.setdefault(sid, set()).update(
                {"maker_staged", "maker_pullback"}
            )
        elif role in {"maker_staged", "maker_pullback"}:
            attempted.setdefault(sid, set()).add(role)
        elif role.startswith("core_carry_maker_"):
            arm = str(row.get("maker_arm") or "")
            if arm in {"staged", "pullback"}:
                attempted.setdefault(sid, set()).add(f"maker_{arm}")
            else:
                attempted.setdefault(sid, set()).update(
                    {"maker_staged", "maker_pullback"}
                )
    return attempted


def daily_family_usage(paths: Iterable[Path], now: datetime) -> tuple[int, float]:
    day = now.astimezone(BJ).date()
    city_days: set[tuple[str, str]] = set()
    posted = 0.0
    for path in paths:
        for row in iter_jsonl(path):
            if str(row.get("status") or "") != "submitted":
                continue
            if (
                str(row.get("strategy_instance") or "")
                == near_core_maker_probe.STRATEGY_INSTANCE
            ):
                continue
            created = parse_utc(row.get("created_at_utc"))
            if created is None or created.astimezone(BJ).date() != day:
                continue
            if str(row.get("execution_action") or "").startswith("core_carry_maker_"):
                continue
            city = str(row.get("city") or "")
            target_date = str(row.get("target_date") or "")
            if city and target_date:
                city_days.add((city, target_date))
            posted += finite(row.get("posted_notional")) or finite(row.get("notional")) or 0.0
    return len(city_days), posted


def entry_cost_reservation(args: argparse.Namespace, row: Mapping[str, Any]) -> float:
    """Reserve the taker plus the one shared maker exposure budget."""

    ask = finite(row.get("current_yes_ask")) or 1.0
    return (
        float(args.taker_shares)
        + float(args.maker_shares)
        + float(args.pullback_maker_shares)
    ) * ask


def entry_plan_cost_reservation(plans: Iterable[Mapping[str, Any]]) -> float:
    """Reserve only children that can actually reach the executor."""

    return sum(finite(plan.get("order_notional_cap")) or 0.0 for plan in plans)


def max_live_child_notional_usd(args: argparse.Namespace) -> float:
    """Maximum principal for one child at the binary-market price ceiling."""

    return max(
        float(args.taker_shares),
        float(args.maker_shares),
        float(args.pullback_maker_shares),
    )


def maker_profile_parameter(name: str) -> float:
    profile = get_execution_profile(EXECUTION_PROFILE)
    value = finite(profile.fixed_parameters.get(name))
    if value is None:
        raise RuntimeError(f"missing numeric maker profile parameter: {name}")
    return value


def maker_low_price_band_halt_min() -> float:
    try:
        return maker_profile_parameter("low_price_band_halt_min_posted_price")
    except RuntimeError:
        return 0.0


def maker_max_reprices(maker_arm: str = "staged") -> int:
    profile = get_execution_profile(EXECUTION_PROFILE)
    role = "maker_pullback" if maker_arm == "pullback" else "maker_staged"
    maker_leg = next((leg for leg in profile.legs if leg.role == role), None)
    if maker_leg is None or maker_leg.max_reprices is None:
        raise RuntimeError("core carry maker profile requires a finite max_reprices")
    return int(maker_leg.max_reprices)


def maker_edge_price_cap(
    *,
    best_ask: float,
    tick_size: float,
    model_probability: float,
    parent_taker_ask: float | None = None,
) -> float:
    retained_edge = maker_profile_parameter("retained_edge")
    improvement_ticks = maker_profile_parameter("minimum_taker_improvement_ticks")
    tick = Decimal(str(tick_size))
    caps = [
        Decimal(str(model_probability)) - Decimal(str(retained_edge)),
        Decimal(str(best_ask)) - Decimal(str(improvement_ticks)) * tick,
    ]
    if parent_taker_ask is not None and parent_taker_ask > 0:
        caps.append(
            Decimal(str(parent_taker_ask))
            - Decimal(str(improvement_ticks)) * tick
        )
    raw_cap = min(caps)
    if raw_cap <= 0 or tick <= 0:
        return 0.0
    return float((raw_cap // tick) * tick)


def pullback_maker_resting_price(
    *, best_ask: float, tick_size: float, price_cap: float
) -> float:
    tick = Decimal(str(tick_size))
    if tick <= 0:
        return 0.0
    raw = min(
        Decimal(str(best_ask)) - Decimal(str(maker_profile_parameter("pullback_offset"))),
        Decimal(str(price_cap)),
    )
    if raw <= 0:
        return 0.0
    return float((raw // tick) * tick)


def maker_clock_assessment(
    row: Mapping[str, Any],
    *,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any]:
    profile = get_execution_profile(EXECUTION_PROFILE)
    source_epoch = parse_utc(row.get("source_report_ts_utc"))
    cadence_min = finite(row.get("observation_cadence_min"))
    state_signature = weather_state_signature(row)
    state_epoch_ref = weather_state_epoch_ref(row)
    common = {
        "maker_clock_basis": str(profile.fixed_parameters.get("clock_basis") or ""),
        "maker_post_update_live_rearm": bool(
            profile.fixed_parameters.get("post_update_live_rearm")
        ),
        "maker_post_update_shadow_revalidation": bool(
            profile.fixed_parameters.get("post_update_shadow_revalidation")
        ),
        "post_update_reprice_required": False,
        "maker_state_schema_version": MAKER_STATE_SCHEMA_VERSION,
        "maker_observation_source": state_signature["observation_source"],
        "maker_forecast_curve_hash": state_signature["forecast_curve_hash"],
        "maker_forecast_source": state_signature["forecast_source"],
        "maker_state_epoch_components": str(
            profile.fixed_parameters.get("state_epoch_components") or ""
        ),
        "maker_replacement_price_policy": str(
            profile.fixed_parameters.get("replacement_price_policy") or ""
        ),
    }
    if (
        source_epoch is None
        or cadence_min is None
        or cadence_min <= 0
        or not state_epoch_ref
    ):
        return {
            **common,
            "maker_clock_status": "invalid_source_clock",
            "maker_live_eligible": False,
            "maker_live_skip_reason": (
                "missing_event_state_signature"
                if not state_epoch_ref
                else "missing_source_epoch_or_cadence"
            ),
            "maker_shadow_policy": "not_scorable_missing_source_clock",
            "seconds_to_next_source_report": None,
        }
    next_update = source_epoch + timedelta(minutes=cadence_min)
    cancel_before_update = next_update - timedelta(seconds=profile.cancel_buffer_sec)
    ttl_deadline = now + timedelta(minutes=order_ttl_min)
    deadline = min(cancel_before_update, ttl_deadline)
    eligible = deadline > now
    seconds_to_next_report = (next_update - now).total_seconds()
    clock_fields = {
        **common,
        "data_update_source": str(row.get("obs_source") or "weather_observation"),
        "data_epoch_ref": state_epoch_ref,
        "data_epoch_ts_utc": source_epoch.isoformat(timespec="seconds"),
        "next_data_update_due_utc": next_update.isoformat(timespec="seconds"),
        "next_source_report_due_utc": next_update.isoformat(timespec="seconds"),
        "cancel_before_data_update_utc": cancel_before_update.isoformat(timespec="seconds"),
        "cancel_before_source_report_utc": cancel_before_update.isoformat(timespec="seconds"),
        "cancel_buffer_sec": profile.cancel_buffer_sec,
        "cancel_reason": "pre_data_update",
        "maker_clock_guard_reason": "pre_source_report",
        "seconds_to_next_source_report": round(seconds_to_next_report, 6),
        "maker_clock_status": (
            "eligible_before_source_report" if eligible else "pre_source_report_blackout"
        ),
        "maker_live_eligible": eligible,
        "maker_live_skip_reason": "" if eligible else "source_report_deadline_elapsed",
        "maker_shadow_policy": (
            "none_live_maker_active"
            if eligible
            else "first_post_update_positive_ev_replay_only_v1"
        ),
    }
    if deadline <= now:
        return clock_fields
    return {
        **clock_fields,
        "expires_at_utc": deadline.isoformat(timespec="seconds"),
        "maker_lifecycle_deadline_utc": deadline.isoformat(timespec="seconds"),
    }


def maker_deadline_fields(
    row: Mapping[str, Any],
    *,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any] | None:
    assessment = maker_clock_assessment(
        row,
        now=now,
        order_ttl_min=order_ttl_min,
    )
    return assessment if bool(assessment.get("maker_live_eligible")) else None


def staged_maker_resting_price(
    order: Mapping[str, Any],
    *,
    best_bid: float,
    best_ask: float,
    tick_size: float,
    price_cap: float,
    now: datetime,
) -> tuple[float, str]:
    base = maker_resting_price(
        best_bid=best_bid,
        best_ask=best_ask,
        tick_size=tick_size,
        price_cap=price_cap,
    )
    root_created = parse_utc(
        order.get("maker_lifecycle_root_created_at_utc") or order.get("created_at_utc")
    )
    age_sec = max(0.0, (now - root_created).total_seconds()) if root_created else 0.0
    midpoint_after = maker_profile_parameter("stage_midpoint_after_sec")
    near_ask_after = maker_profile_parameter("stage_near_ask_after_sec")
    if age_sec >= near_ask_after:
        target = min(best_ask - tick_size, price_cap)
        stage = "near_ask"
    elif age_sec >= midpoint_after:
        midpoint = math.floor((((best_bid + best_ask) / 2.0) + 1e-12) / tick_size) * tick_size
        target = min(max(base, midpoint), best_ask - tick_size, price_cap)
        stage = "midpoint"
    else:
        target = base
        stage = "queue"
    if target <= 0 or target >= best_ask:
        return 0.0, stage
    return round(target, 6), stage


def base_plan_fields(
    row: Mapping[str, Any],
    *,
    child_order_role: str,
    shares: float,
    live_enabled: bool,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any]:
    bid = finite(row.get("current_yes_bid")) or 0.0
    ask = finite(row.get("current_yes_ask")) or 0.0
    tick = finite(row.get("current_yes_tick_size")) or 0.001
    probability = finite(row.get("model_probability_hold")) or 0.0
    maker_cap = maker_edge_price_cap(
        best_ask=ask,
        tick_size=tick,
        model_probability=probability,
        parent_taker_ask=finite(row.get("maker_rearm_parent_taker_ask")),
    )
    sid = signal_id(row)
    maker = child_order_role == "maker" or child_order_role.startswith("maker_")
    profile = get_execution_profile(EXECUTION_PROFILE)
    profile_leg = next(
        (leg for leg in profile.legs if leg.role == child_order_role),
        None,
    )
    if profile_leg is None:
        raise RuntimeError(f"execution profile missing child role: {child_order_role}")
    maker_arm = (
        "pullback" if child_order_role == "maker_pullback" else "staged"
    ) if maker else ""
    limit = (
        pullback_maker_resting_price(
            best_ask=ask,
            tick_size=tick,
            price_cap=maker_cap,
        )
        if maker_arm == "pullback"
        else maker_resting_price(
            best_bid=bid,
            best_ask=ask,
            tick_size=tick,
            price_cap=maker_cap,
        )
        if maker
        else ask
    )
    maker_clock = (
        maker_clock_assessment(row, now=now, order_ttl_min=order_ttl_min)
        if maker
        else {}
    )
    halt_min = maker_low_price_band_halt_min()
    if maker and 0.0 < halt_min and 0.0 < limit < halt_min:
        maker_clock = {
            **maker_clock,
            "maker_live_eligible": False,
            "maker_live_skip_reason": "low_price_band_halt_shadow_only",
            "maker_low_price_band_halt": True,
            "maker_low_price_band_would_be_price": round(limit, 6),
        }
    maker_timing = maker_clock if bool(maker_clock.get("maker_live_eligible")) else None
    expires = now + timedelta(minutes=order_ttl_min)
    return {
        "strategy": "weather_edge_v1",
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "config_id": CONFIG_ID,
        "strategy_family": "reheat_risk.current_yes",
        "decision_mode": "frozen_core_v3_first_positive_ten_share_taker_ev",
        "execution_mode": "tiny_live_10_taker_5_shared_maker",
        "execution_profile": EXECUTION_PROFILE,
        "comparison_group_id": stable_hash({"signal_id": sid, "token_id": row.get("token_id")}),
        "city": str(row.get("city") or ""),
        "city_pool": "all_canonical_weather_state_v2",
        "target_date": str(row.get("target_date") or ""),
        "market_id": str(row.get("current_market_id") or ""),
        "question": str(row.get("current_question") or ""),
        "condition_id": str(row.get("condition_id") or row.get("current_condition_id") or ""),
        "bracket": str(row.get("current_bracket") or ""),
        "token_id": str(row.get("token_id") or row.get("current_yes_token_id") or ""),
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "child_order_role": child_order_role,
        "maker_arm": maker_arm,
        "maker_experiment_id": str(
            get_execution_profile(EXECUTION_PROFILE).fixed_parameters.get(
                "maker_experiment_id"
            )
            or ""
        ) if maker else "",
        "maker_budget_mode": str(
            profile.fixed_parameters.get("maker_budget_mode") or ""
        ) if maker else "",
        "execution_policy": profile_leg.execution_policy,
        "order_lifecycle_policy": profile_leg.order_lifecycle_policy,
        "maker_only": maker,
        "allow_duplicate_signal_id": True,
        "market_price": round(ask, 6),
        "best_bid": round(bid, 6),
        "best_ask": round(ask, 6),
        "limit_price": round(limit, 6),
        "quote_best_bid": round(bid, 6),
        "quote_best_ask": round(ask, 6),
        "quote_tick_size": round(tick, 6),
        "quote_mode": (
            "entry_ask_minus_2c_static_post_only_edge_capped"
            if maker_arm == "pullback"
            else "fresh_bid_improve_one_tick_post_only_retained_edge_capped"
            if maker
            else "fresh_full_ladder_taker_ev_recheck"
        ),
        "size": round(shares, 6),
        "fixed_order_shares": round(shares, 6),
        "max_order_shares": round(shares, 6),
        "notional": round(shares * max(0.0, limit), 6),
        "order_notional_cap": round(shares * max(0.0, maker_cap if maker else ask), 6),
        "sizing_mode": "fixed_shares",
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "model_token_probability": round(probability, 12),
        "required_quote_edge": 0.0,
        "model_version": MODEL_VERSION,
        "artifact_hash": str(row.get("artifact_hash") or ""),
        "checkpoint_key": str(row.get("checkpoint_key") or ""),
        "decision_snapshot_ts_utc": str(row.get("decision_snapshot_ts_utc") or ""),
        "source_snapshot_file": str(row.get("snapshot_file") or ""),
        "source_report_ts_utc": str(row.get("source_report_ts_utc") or ""),
        "obs_status": str(row.get("obs_status") or ""),
        "station_gap_state": str(row.get("station_gap_state") or ""),
        "expires_at_utc": (
            maker_timing["expires_at_utc"]
            if maker_timing
            else expires.isoformat(timespec="seconds")
        ),
        "maker_price_cap": round(maker_cap, 6) if maker else 0.0,
        "maker_rearm_parent_taker_ask": (
            finite(row.get("maker_rearm_parent_taker_ask")) or 0.0
        ),
        "maker_rearm_state_ref": str(row.get("maker_rearm_state_ref") or ""),
        "maker_rearm_model_edge_after_fee_and_depth": (
            finite(row.get("maker_rearm_model_edge_after_fee_and_depth")) or 0.0
        ),
        "maker_price_cap_policy": (
            "model_probability_retained_edge_and_taker_improvement"
            if maker
            else ""
        ),
        "maker_retained_edge": (
            maker_profile_parameter("retained_edge") if maker else 0.0
        ),
        "maker_minimum_taker_improvement_ticks": (
            maker_profile_parameter("minimum_taker_improvement_ticks")
            if maker
            else 0.0
        ),
        "maker_lifecycle_root_created_at_utc": now.isoformat(timespec="seconds") if maker else "",
        "maker_lifecycle_deadline_utc": (
            maker_timing["maker_lifecycle_deadline_utc"]
            if maker_timing
            else ""
        ),
        "maker_lifecycle_reprice_count": 0,
        "maker_last_reprice_stage": "",
        **maker_clock,
    }


def build_entry_plans(
    row: Mapping[str, Any],
    *,
    live_enabled: bool,
    now: datetime,
    taker_shares: float,
    maker_shares: float,
    pullback_maker_shares: float = FROZEN_PULLBACK_MAKER_SHARES,
    order_ttl_min: float,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    sid = signal_id(row)
    profile = get_execution_profile(EXECUTION_PROFILE)
    leg_overrides = {
        "taker": taker_shares,
        "maker_staged": maker_shares,
    }
    if taker_shares > 0 and maker_shares > 0:
        allocation = allocate_profile_shares(
            profile=profile,
            total_shares=taker_shares + maker_shares + pullback_maker_shares,
            leg_share_overrides=leg_overrides,
        )
    else:
        # A filled near-Core probe consumes the shared maker allowance.  The
        # existing Core route may therefore be taker-only (or retain only the
        # positive maker remainder) without inventing a zero-sized child.
        allocation = tuple(
            (role, Decimal(str(shares)))
            for role, shares in (
                ("taker", taker_shares),
                ("maker_staged", maker_shares),
            )
            if shares > 0
        )
        if not allocation:
            return []
    for role, allocated_shares in allocation:
        shares = float(allocated_shares)
        fields = base_plan_fields(
            row,
            child_order_role=role,
            shares=shares,
            live_enabled=live_enabled,
            now=now,
            order_ttl_min=order_ttl_min,
        )
        if role.startswith("maker_") and (
            not bool(fields.get("maker_live_eligible"))
            or (finite(fields["limit_price"]) or 0.0) <= 0
        ):
            continue
        plans.append(
            {
                "record_type": "weather_edge_trade_plan",
                "plan_id": "plan-" + stable_hash({**fields, "signal_id": sid, "role": role}),
                "signal_id": sid,
                "opportunity_id": sid,
                "created_at_utc": now.isoformat(timespec="seconds"),
                "status": "accepted",
                "risk_status": "passed",
                "risk_reason": "",
                **fields,
            }
        )
    if plans:
        compatibility = build_core_carry_legacy_plan_compatibility(legacy_plans=plans)
        for plan, intent in zip(plans, compatibility.intents):
            plan.update(
                {
                    "execution_schema_version": intent.execution_schema_version,
                    "resolved_execution_profile": intent.resolved_execution_profile,
                    "execution_config_id": intent.execution_config_id,
                    "plan_dedupe_key": intent.plan_dedupe_key,
                    "live_exposure_key": intent.live_exposure_key,
                }
            )
    return plans


def near_core_signal_id(row: Mapping[str, Any]) -> str:
    return "current-yes-core-carry-near-core-" + stable_hash(
        {
            "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
            "city": str(row.get("city") or ""),
            "target_date": str(row.get("target_date") or ""),
            "checkpoint_key": str(row.get("checkpoint_key") or ""),
        }
    )


def latest_near_core_candidates(
    path: Path, *, now: datetime, max_age_sec: float
) -> list[dict[str, Any]]:
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for source in iter_jsonl(path):
        row = dict(source)
        if not near_core_maker_probe.is_candidate(row):
            continue
        clock = parse_utc(
            row.get("decision_snapshot_ts_utc")
            or row.get("as_of_ts_utc")
            or row.get("created_at_utc")
        )
        if clock is None or clock > now:
            continue
        age = (now - clock).total_seconds()
        if age < 0 or age > max_age_sec:
            continue
        key = str(row.get("checkpoint_key") or "")
        prior = latest.get(key)
        if prior is None or clock > prior[0]:
            latest[key] = (clock, row)
    # The newest checkpoint owns the one city-day order.  Older simultaneous
    # checkpoints remain explicit blocked denominator rows.
    return [
        item[1]
        for item in sorted(latest.values(), key=lambda item: item[0], reverse=True)
    ]


def build_near_core_entry_plan(
    row: Mapping[str, Any],
    *,
    live_enabled: bool,
    now: datetime,
    order_ttl_min: float,
) -> dict[str, Any] | None:
    fields = base_plan_fields(
        row,
        child_order_role="maker_staged",
        shares=near_core_maker_probe.FIXED_SHARES,
        live_enabled=live_enabled,
        now=now,
        order_ttl_min=order_ttl_min,
    )
    if (
        not bool(fields.get("maker_live_eligible"))
        or (finite(fields.get("limit_price")) or 0.0) <= 0
    ):
        return None
    sid = near_core_signal_id(row)
    fields.update(
        {
            "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
            "config_id": near_core_maker_probe.CONFIG_ID,
            "execution_profile": near_core_maker_probe.EXECUTION_PROFILE,
            "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
            "near_core_experiment_id": near_core_maker_probe.EXPERIMENT_ID,
            "maker_experiment_id": near_core_maker_probe.EXPERIMENT_ID,
            "decision_mode": "frozen_core_v3_sole_non_positive_taker_ev_fallback",
            "execution_mode": "near_core_ws1_fixed_rest_5_share",
            "execution_policy": "core_carry_near_core_fixed_rest_maker_v1",
            "order_lifecycle_policy": "near_core_fixed_rest_safety_cancel_only_v1",
            "maker_budget_mode": "separate_near_core_fixed_5_share",
            "client_order_prefix": near_core_maker_probe.CLIENT_ORDER_PREFIX,
            "blocker": near_core_maker_probe.SOLE_BLOCKER,
            "near_core_policy_arm": "WS1_BASELINE_FIXED_REST",
            "economic_ws_cancel_enabled": False,
            "fixed_order_shares": near_core_maker_probe.FIXED_SHARES,
            "max_order_shares": near_core_maker_probe.FIXED_SHARES,
        }
    )
    plan = {
        "record_type": "weather_edge_trade_plan",
        "plan_id": "plan-"
        + stable_hash(
            {
                "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
                "signal_id": sid,
                "role": "maker_staged",
                "checkpoint_key": row.get("checkpoint_key"),
            }
        ),
        "signal_id": sid,
        "opportunity_id": sid,
        "created_at_utc": now.isoformat(timespec="seconds"),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **fields,
    }
    plan["comparison_group_id"] = stable_hash(
        {
            "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
            "signal_id": sid,
            "token_id": plan.get("token_id"),
        }
    )
    compatibility = build_core_carry_legacy_plan_compatibility(legacy_plans=[plan])
    intent = compatibility.intents[0]
    plan.update(
        {
            "execution_schema_version": intent.execution_schema_version,
            "resolved_execution_profile": intent.resolved_execution_profile,
            "execution_config_id": intent.execution_config_id,
            "plan_dedupe_key": intent.plan_dedupe_key,
            "live_exposure_key": intent.live_exposure_key,
        }
    )
    return plan


def near_core_maker_lifecycle_heads(path: Path) -> list[dict[str, Any]]:
    heads: dict[str, tuple[datetime, int, dict[str, Any]]] = {}
    for index, row in enumerate(iter_jsonl(path)):
        if (
            str(row.get("strategy_instance") or "")
            != near_core_maker_probe.STRATEGY_INSTANCE
            or not bool(row.get("maker_only"))
        ):
            continue
        root = maker_lifecycle_root(row)
        created = parse_utc(row.get("created_at_utc"))
        if not root or created is None:
            continue
        prior = heads.get(root)
        if prior is None or (created, index) > (prior[0], prior[1]):
            heads[root] = (created, index, row)
    return [item[2] for item in heads.values()]


def recover_near_core_journal_terminal_makers(output_dir: Path) -> int:
    """Recover a terminal near-Core projection after a journal/write crash."""

    live_orders = output_dir / "live_orders.jsonl"
    states = execution_journal_order_states(output_dir / "execution_journal.jsonl")
    existing_execution_ids = {
        str(row.get("execution_id") or "") for row in iter_jsonl(live_orders)
    }
    written = 0
    for head in near_core_maker_lifecycle_heads(live_orders):
        if str(head.get("status") or "") != "submitted":
            continue
        order_id = live_order_id(head)
        evidence = states.get(order_id)
        if not order_id or not evidence or evidence["status"] not in {"cancelled", "filled"}:
            continue
        execution_id = "near-core-journal-recovery-" + stable_hash(
            {"order_id": order_id, "terminal_status": evidence["status"]}
        )
        if execution_id in existing_execution_ids:
            continue
        terminal = {
            **dict(head),
            "record_type": "weather_edge_live_order",
            "created_at_utc": evidence["recorded_at_utc"] or utc_now(),
            "status": evidence["status"],
            "child_order_role": "near_core_maker_terminal",
            "execution_action": "near_core_maker_terminal",
            "execution_id": execution_id,
            "plan_id": "plan-" + execution_id,
            "replacement_of_order_id": order_id,
            "source_order_id": order_id,
            "exchange_response": {
                "quote_status": evidence["status"],
                "quote_reason": "execution_journal_terminal_recovery",
                "authoritative_execution_journal": evidence["journal_payload"],
            },
        }
        append_jsonl(live_orders, terminal)
        existing_execution_ids.add(execution_id)
        written += 1
    return written


def near_core_daily_usage(output_dir: Path, now: datetime) -> tuple[int, float]:
    day = now.astimezone(BJ).date()
    city_days: set[tuple[str, str]] = set()
    posted = 0.0
    for row in iter_jsonl(output_dir / "live_orders.jsonl"):
        if (
            str(row.get("strategy_instance") or "")
            != near_core_maker_probe.STRATEGY_INSTANCE
            or str(row.get("status") or "") != "submitted"
        ):
            continue
        created = parse_utc(row.get("created_at_utc"))
        if created is None or created.astimezone(BJ).date() != day:
            continue
        key = near_core_maker_probe.city_day(row)
        if all(key):
            city_days.add(key)
        posted += finite(row.get("posted_notional")) or finite(row.get("notional")) or 0.0
    return len(city_days), posted


def near_core_entry_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
    core_actionable_city_days: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not bool(args.near_core_maker_probe_enabled):
        return [], []
    live_rows = list(iter_jsonl(output_dir / "live_orders.jsonl"))
    existing_family = submitted_city_days(family_live_order_files(output_dir))
    near_consumed = near_core_maker_probe.consumed_city_days(live_rows)
    used_city_days, used_cost = near_core_daily_usage(output_dir, now)
    plans: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for row in latest_near_core_candidates(
        output_dir / "pre_live_scores.jsonl",
        now=now,
        max_age_sec=float(args.near_core_candidate_max_age_sec),
    ):
        key = near_core_maker_probe.city_day(row)
        book_freshness = near_core_book_freshness(
            row,
            now=now,
            max_age_sec=float(args.near_core_book_max_age_sec),
        )
        reason = ""
        if not bool(book_freshness["near_core_book_fresh"]):
            reason = str(book_freshness["near_core_book_freshness_status"])
        elif key in core_actionable_city_days:
            reason = "existing_core_actionable_priority"
        elif key in existing_family:
            reason = "existing_core_family_exposure"
        elif key in near_consumed:
            reason = "near_core_city_day_exposure_already_consumed"
        elif used_city_days >= int(args.near_core_max_city_days_per_bj_day):
            reason = "near_core_daily_city_day_cap"
        plan = None if reason else build_near_core_entry_plan(
            row,
            live_enabled=bool(
                args.live
                and args.confirm_live
                and args.confirm_near_core_maker_probe_live
            ),
            now=now,
            order_ttl_min=float(args.near_core_order_ttl_min),
        )
        if not reason and plan is None:
            reason = "near_core_maker_safety_not_eligible"
        planned_cost = entry_plan_cost_reservation([plan] if plan else [])
        if (
            not reason
            and used_cost + planned_cost > float(args.near_core_max_daily_cost_usd)
        ):
            reason = "near_core_daily_cost_cap"
            plan = None
            planned_cost = 0.0
        ledger.append(
            {
                "schema_version": near_core_maker_probe.LEDGER_SCHEMA_VERSION,
                "record_type": "core_carry_near_core_maker_decision",
                "ledger_id": "near-core-ledger-"
                + stable_hash(
                    {
                        "checkpoint_key": row.get("checkpoint_key"),
                        "status": "blocked" if reason else "planned",
                        "reason": reason,
                    }
                ),
                "created_at_utc": now.isoformat(timespec="seconds"),
                "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
                "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
                "eligible_checkpoint": True,
                "city": key[0],
                "target_date": key[1],
                "checkpoint_key": row.get("checkpoint_key"),
                "signal_id": near_core_signal_id(row),
                "sole_blocker": near_core_maker_probe.SOLE_BLOCKER,
                "status": "blocked" if reason else "planned",
                "reason": reason,
                "plan_id": plan.get("plan_id") if plan else "",
                "fixed_shares": near_core_maker_probe.FIXED_SHARES,
                "client_order_prefix": near_core_maker_probe.CLIENT_ORDER_PREFIX,
                "policy_arm": "WS1_BASELINE_FIXED_REST",
                "economic_ws_cancel_enabled": False,
                "live_enabled": bool(plan and plan.get("live_enabled")),
                **book_freshness,
            }
        )
        if plan is not None:
            plans.append(plan)
            near_consumed.add(key)
            used_city_days += 1
            used_cost += planned_cost
    return plans, ledger


def near_core_lifecycle_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
    core_actionable_city_days: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[tuple[str, str]]]:
    epochs = latest_weather_epochs(output_dir / "state_decisions.jsonl")
    plans: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    superseded: set[tuple[str, str]] = set()
    candidates = [
        row
        for row in near_core_maker_lifecycle_heads(output_dir / "live_orders.jsonl")
        if str(row.get("status") or "") == "submitted" and bool(live_order_id(row))
    ]
    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for order in candidates:
            key = near_core_maker_probe.city_day(order)
            deadline = parse_utc(
                order.get("maker_lifecycle_deadline_utc") or order.get("expires_at_utc")
            )
            source_ref = str(
                order.get("data_epoch_ref") or order.get("source_report_ts_utc") or ""
            )
            latest = epochs.get(key)
            latest_ref = weather_state_epoch_ref(latest or {})
            action = ""
            blocker = "fixed_rest_no_economic_action"
            if key in core_actionable_city_days:
                action = "near_core_maker_cancel_core_supersession"
                blocker = "existing_core_became_actionable"
                superseded.add(key)
            elif not bool(args.near_core_maker_probe_enabled):
                action = "near_core_maker_cancel_feature_disabled"
                blocker = "near_core_feature_disabled"
            elif deadline is None or now >= deadline:
                action = "near_core_maker_cancel_safety_deadline"
                blocker = "near_core_common_deadline_elapsed"
            elif not latest_ref or latest_ref != source_ref:
                action = "near_core_maker_cancel_weather_state"
                blocker = (
                    "latest_weather_state_unavailable"
                    if not latest_ref
                    else "weather_state_changed_requires_fresh_candidate"
                )
            else:
                quote = weather_state._fetch_token_book(  # noqa: SLF001
                    client, str(order.get("token_id") or "")
                )
                bid = finite(quote.get("bid")) or 0.0
                ask = finite(quote.get("ask")) or 0.0
                if (
                    str(quote.get("book_status") or "") != "ok"
                    or bid <= 0
                    or ask <= bid
                ):
                    action = "near_core_maker_cancel_stale_market_state"
                    blocker = "no_fresh_valid_two_sided_book"
            decision = {
                "schema_version": near_core_maker_probe.LEDGER_SCHEMA_VERSION,
                "record_type": "core_carry_near_core_maker_lifecycle_decision",
                "ledger_id": "near-core-ledger-"
                + stable_hash(
                    {
                        "source_order_id": live_order_id(order),
                        "action": action,
                        "latest_ref": latest_ref,
                    }
                ),
                "created_at_utc": now.isoformat(timespec="seconds"),
                "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
                "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
                "eligible_checkpoint": False,
                "city": key[0],
                "target_date": key[1],
                "signal_id": order.get("signal_id"),
                "source_order_id": live_order_id(order),
                "status": "cancel_planned" if action else "resting",
                "action": action,
                "reason": blocker,
                "economic_ws_cancel_enabled": False,
            }
            decisions.append(decision)
            if action:
                plans.append(
                    build_maker_lifecycle_plan(
                        order,
                        action=action,
                        limit_price=0.0,
                        cancel_only=True,
                        cancel_source_order=True,
                        now=now,
                        live_enabled=bool(args.live and args.confirm_live),
                    )
                )
    return plans, decisions, superseded


def append_near_core_ledger(output_dir: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path = output_dir / "near_core_maker_ledger.jsonl"
    existing = {str(row.get("ledger_id") or "") for row in iter_jsonl(path)}
    written = 0
    for source in rows:
        row = dict(source)
        ledger_id = str(row.get("ledger_id") or "")
        if not ledger_id or ledger_id in existing:
            continue
        append_jsonl(path, row)
        existing.add(ledger_id)
        written += 1
    return written


def sync_near_core_order_ledger(output_dir: Path) -> int:
    rows: list[dict[str, Any]] = []
    for order in iter_jsonl(output_dir / "live_orders.jsonl"):
        if (
            str(order.get("strategy_instance") or "")
            != near_core_maker_probe.STRATEGY_INSTANCE
        ):
            continue
        execution_id = str(order.get("execution_id") or "")
        if not execution_id:
            continue
        rows.append(
            {
                "schema_version": near_core_maker_probe.LEDGER_SCHEMA_VERSION,
                "record_type": "core_carry_near_core_maker_order_state",
                "ledger_id": "near-core-order-" + execution_id,
                "created_at_utc": order.get("created_at_utc"),
                "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
                "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
                "eligible_checkpoint": False,
                "city": order.get("city"),
                "target_date": order.get("target_date"),
                "signal_id": order.get("signal_id"),
                "plan_id": order.get("plan_id"),
                "execution_id": execution_id,
                "client_order_id": order.get("client_order_id"),
                "status": order.get("status"),
                "matched_shares": near_core_maker_probe.matched_shares(order),
                "economic_ws_cancel_enabled": False,
            }
        )
    return append_near_core_ledger(output_dir, rows)


def write_near_core_runtime_artifacts(
    args: argparse.Namespace, output_dir: Path
) -> dict[str, Any]:
    ledger = list(iter_jsonl(output_dir / "near_core_maker_ledger.jsonl"))
    manifest = {
        "schema_version": near_core_maker_probe.MANIFEST_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "source_sleeve": near_core_maker_probe.SOURCE_SLEEVE,
        "strategy_instance": near_core_maker_probe.STRATEGY_INSTANCE,
        "config_id": near_core_maker_probe.CONFIG_ID,
        "experiment_id": near_core_maker_probe.EXPERIMENT_ID,
        "feature_enabled": bool(args.near_core_maker_probe_enabled),
        "live_authorized": bool(
            args.live
            and args.confirm_live
            and args.confirm_near_core_maker_probe_live
        ),
        "fixed_shares": near_core_maker_probe.FIXED_SHARES,
        "client_order_prefix": near_core_maker_probe.CLIENT_ORDER_PREFIX,
        "risk_budget": {
            "max_city_days_per_bj_day": int(args.near_core_max_city_days_per_bj_day),
            "max_daily_cost_usd": float(args.near_core_max_daily_cost_usd),
        },
        "policy_arm": "WS1_BASELINE_FIXED_REST",
        "economic_ws_cancel_enabled": False,
        "ws_control_readiness_manifest": str(
            args.near_core_ws_control_manifest or ""
        ),
        "ledger_path": str(output_dir / "near_core_maker_ledger.jsonl"),
        **DEPLOYMENT_METADATA,
    }
    report = near_core_maker_probe.report(ledger)
    gate = near_core_maker_probe.promotion_gate(ledger)
    write_json(output_dir / "near_core_maker_manifest.json", manifest)
    write_json(output_dir / "near_core_maker_latest_report.json", report)
    write_json(output_dir / "near_core_maker_promotion_gate.json", gate)
    return {"manifest": manifest, "report": report, "promotion_gate": gate}


def execute_plans(args: argparse.Namespace, plans: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    plans_path = output_dir / "current_plans.jsonl"
    write_jsonl(plans_path, plans)
    result = execute_core_carry_plans(
        plans=plans,
        output_dir=output_dir,
        live=bool(args.live),
        market_proxy=args.market_proxy,
        max_child_shares=max_live_child_notional_usd(args),
        max_batch_cost_usd=(
            float(args.max_daily_cost_usd)
            + (
                float(args.near_core_max_daily_cost_usd)
                if bool(args.near_core_maker_probe_enabled)
                else 0.0
            )
        ),
        code_commit=str(DEPLOYMENT_METADATA["deployed_repo_sha"]),
    )
    return {
        "exit_code": 0 if int(result.get("live_errors") or 0) == 0 else 1,
        "result": result,
        "stderr": "" if int(result.get("live_errors") or 0) == 0 else "shared runtime execution error",
    }


def build_maker_lifecycle_plan(
    order: Mapping[str, Any],
    *,
    action: str,
    limit_price: float,
    cancel_only: bool,
    cancel_source_order: bool,
    now: datetime,
    live_enabled: bool,
    reprice_stage: str = "",
    decision_best_bid: float = 0.0,
    decision_best_ask: float = 0.0,
    decision_tick_size: float = 0.0,
) -> dict[str, Any]:
    live_source_id = live_order_id(order)
    lineage_source_id = live_source_id or str(order.get("source_order_id") or "")
    shares = finite(order.get("size")) or 5.0
    fields = {
        **dict(order),
        "record_type": "weather_edge_trade_plan",
        "created_at_utc": now.isoformat(timespec="seconds"),
        "child_order_role": action,
        "execution_action": action,
        "execution_policy": str(
            order.get("execution_policy")
            or "current_yes_residual_carry_staged_maker_v3"
        ),
        "order_lifecycle_policy": str(
            order.get("order_lifecycle_policy")
            or "maker_event_validated_staged_until_update_or_ttl_v3"
        ),
        "limit_price": round(limit_price, 6),
        "notional": round(shares * limit_price, 6),
        "order_notional_cap": round(shares * limit_price, 6),
        "maker_only": True,
        "paper_enabled": False,
        "live_enabled": bool(live_enabled),
        "cancel_before_order_id": live_source_id if cancel_source_order else "",
        "source_order_id": lineage_source_id,
        "source_execution_id": str(order.get("execution_id") or ""),
        "source_plan_id": str(order.get("plan_id") or ""),
        "source_posted_price": finite(order.get("posted_price")) or finite(order.get("limit_price")) or 0.0,
        "source_remaining_shares": shares,
        "replacement_requires_order_state": bool(cancel_source_order and not cancel_only),
        "cancel_only": cancel_only,
        "allow_duplicate_signal_id": True,
        "maker_lifecycle_reprice_count": int(
            finite(order.get("maker_lifecycle_reprice_count")) or 0
        )
        + (1 if action == "core_carry_maker_reprice" else 0),
        "maker_last_reprice_stage": (
            reprice_stage
            if action == "core_carry_maker_reprice"
            else str(order.get("maker_last_reprice_stage") or "")
        ),
        "maker_reprice_decision_best_bid": round(decision_best_bid, 6),
        "maker_reprice_decision_best_ask": round(decision_best_ask, 6),
        "maker_reprice_decision_tick_size": round(decision_tick_size, 6),
        "maker_replacement_max_quote_drift_ticks": (
            maker_profile_parameter("replacement_max_quote_drift_ticks")
        ),
    }
    fields["plan_id"] = "plan-" + stable_hash(
        {
            "source_order_id": lineage_source_id,
            "maker_arm": str(order.get("maker_arm") or "staged"),
            "action": action,
            "limit_price": limit_price,
            "created_at_utc": fields["created_at_utc"],
        }
    )
    fields["status"] = "accepted"
    fields["risk_status"] = "passed"
    fields["risk_reason"] = ""
    return fields


def maker_lifecycle_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profile = get_execution_profile(EXECUTION_PROFILE)
    live_orders = output_dir / "live_orders.jsonl"
    epochs = latest_weather_epochs(output_dir / "state_decisions.jsonl")
    candidates: list[tuple[dict[str, Any], bool]] = []
    for row in maker_lifecycle_heads(live_orders):
        active_order = str(row.get("status") or "") == "submitted" and bool(live_order_id(row))
        detached_retry = retryable_maker_post_failure(row)
        if not active_order and not detached_retry:
            continue
        created = parse_utc(row.get("created_at_utc"))
        if created is None or (now - created).total_seconds() < float(args.maker_refresh_sec):
            continue
        candidates.append((row, active_order))

    plans: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for order, active_order in candidates:
            order_created = parse_utc(order.get("created_at_utc"))
            city_day = (str(order.get("city") or ""), str(order.get("target_date") or ""))
            latest = epochs.get(city_day)
            source_epoch = str(order.get("source_report_ts_utc") or "")
            latest_epoch = str((latest or {}).get("source_report_ts_utc") or "")
            source_state_ref = str(
                order.get("data_epoch_ref") or order.get("source_report_ts_utc") or ""
            )
            latest_state_ref = weather_state_epoch_ref(latest or {})
            event_state_managed = (
                str(order.get("maker_state_schema_version") or "")
                == MAKER_STATE_SCHEMA_VERSION
                and source_state_ref.startswith(MAKER_STATE_EPOCH_PREFIX)
            )
            state_transitions = (
                weather_state_transition_types(order, latest)
                if event_state_managed and latest
                else []
            )
            deadline = parse_utc(order.get("maker_lifecycle_deadline_utc") or order.get("expires_at_utc"))
            action = ""
            blocker = ""
            next_price = 0.0
            best_bid = 0.0
            best_ask = 0.0
            tick = 0.0
            cancel_only = False
            reprice_stage = ""
            reprice_count = int(
                finite(order.get("maker_lifecycle_reprice_count")) or 0
            )
            last_reprice_stage = str(order.get("maker_last_reprice_stage") or "")
            maker_arm = str(order.get("maker_arm") or "staged")
            max_reprices = maker_max_reprices(maker_arm)
            if deadline is None or now >= deadline:
                if active_order:
                    cancel_before_update = parse_utc(
                        order.get("cancel_before_data_update_utc")
                    )
                    action = (
                        "core_carry_maker_cancel_pre_data_update"
                        if cancel_before_update is not None
                        and now >= cancel_before_update
                        else "core_carry_maker_cancel_ttl"
                    )
                    cancel_only = True
                else:
                    blocker = "detached_maker_retry_ttl_expired"
            elif event_state_managed and (
                not latest_state_ref or latest_state_ref != source_state_ref
            ):
                blocker = (
                    "latest_weather_state_unavailable"
                    if not latest_state_ref
                    else "weather_state_changed_requires_fresh_score"
                )
                if active_order:
                    action = "core_carry_maker_cancel_weather_state"
                    cancel_only = True
            elif not event_state_managed and (
                not latest or not latest_epoch or latest_epoch != source_epoch
            ):
                if not latest or not latest_epoch:
                    blocker = "latest_observation_state_unavailable"
                else:
                    blocker = "new_observation_requires_fresh_entry"
                if active_order:
                    action = "core_carry_maker_cancel_new_observation"
                    cancel_only = True
            elif maker_arm == "pullback":
                blocker = "legacy_pullback_static_resting_no_reprice"
            else:
                quote = weather_state._fetch_token_book(client, str(order.get("token_id") or ""))  # noqa: SLF001
                best_bid = finite(quote.get("bid")) or 0.0
                best_ask = finite(quote.get("ask")) or 0.0
                tick = finite(quote.get("tick_size")) or finite(order.get("quote_tick_size")) or 0.001
                cap = min(
                    finite(order.get("maker_price_cap")) or 0.0,
                    finite(order.get("model_token_probability")) or 0.0,
                )
                posted = finite(order.get("posted_price")) or finite(order.get("limit_price")) or 0.0
                if str(quote.get("book_status") or "") != "ok" or best_bid <= 0 or best_ask <= best_bid:
                    blocker = "bad_fresh_book"
                elif str(profile.fixed_parameters.get("maker_budget_mode") or "") == "single_active_order_staged_then_pullback":
                    age_sec = (
                        max(0.0, (now - order_created).total_seconds())
                        if order_created
                        else 0.0
                    )
                    handoff_after = maker_profile_parameter("shared_maker_handoff_after_sec")
                    if not active_order:
                        next_price = finite(order.get("limit_price")) or finite(order.get("posted_price")) or 0.0
                        reprice_stage = last_reprice_stage
                        if next_price > 0:
                            action = "core_carry_maker_repost"
                        else:
                            blocker = "detached_maker_retry_missing_price"
                    elif age_sec < handoff_after:
                        blocker = "shared_maker_staged_queue_window"
                    elif reprice_count >= max_reprices or last_reprice_stage == "pullback_handoff":
                        blocker = "shared_maker_pullback_handoff_already_used"
                    else:
                        next_price = pullback_maker_resting_price(
                            best_ask=best_ask,
                            tick_size=tick,
                            price_cap=cap,
                        )
                        reprice_stage = "pullback_handoff"
                        if next_price <= 0:
                            blocker = "shared_maker_no_pullback_price"
                        elif next_price < maker_low_price_band_halt_min():
                            action = "core_carry_maker_cancel_low_price_handoff"
                            cancel_only = True
                            blocker = "low_price_band_halt_shadow_only"
                        elif active_order:
                            action = "core_carry_maker_reprice"
                        else:
                            action = "core_carry_maker_repost"
                else:
                    next_price, reprice_stage = staged_maker_resting_price(
                        order,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        tick_size=tick,
                        price_cap=cap,
                        now=now,
                    )
                    if not active_order and next_price > 0:
                        action = "core_carry_maker_repost"
                    elif reprice_stage == "queue":
                        blocker = "queue_preserving_stage"
                    elif reprice_count >= max_reprices:
                        blocker = "maker_reprice_limit_reached"
                    elif last_reprice_stage == reprice_stage:
                        blocker = "maker_reprice_stage_already_used"
                    elif next_price > posted + tick / 2.0:
                        action = "core_carry_maker_reprice"
                    elif best_bid <= posted + tick / 2.0:
                        blocker = "own_or_same_level_best_bid_keep_queue"
                    else:
                        blocker = "already_at_best_allowed_price"
            decision = {
                "record_type": "current_yes_core_carry_maker_lifecycle_decision",
                "created_at_utc": now.isoformat(timespec="seconds"),
                "source_order_id": live_order_id(order),
                "city": city_day[0],
                "target_date": city_day[1],
                "source_report_ts_utc": source_epoch,
                "latest_source_report_ts_utc": latest_epoch,
                "source_weather_state_ref": source_state_ref,
                "latest_weather_state_ref": latest_state_ref,
                "weather_state_transition_types": state_transitions,
                "event_state_managed": event_state_managed,
                "posted_price": finite(order.get("posted_price")) or 0.0,
                "maker_price_cap": finite(order.get("maker_price_cap")) or 0.0,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "next_price": next_price,
                "action": action,
                "blocker": blocker,
                "active_exchange_order": active_order,
                "new_observation_revalidation_status": "",
                "new_observation_revalidation_reasons": [],
                "reprice_stage": reprice_stage,
                "maker_reprice_count": reprice_count,
                "maker_max_reprices": max_reprices,
                "maker_last_reprice_stage": last_reprice_stage,
                "maker_arm": maker_arm,
            }
            decisions.append(decision)
            if action:
                plan = build_maker_lifecycle_plan(
                    order,
                    action=action,
                    limit_price=next_price,
                    cancel_only=cancel_only,
                    cancel_source_order=active_order,
                    reprice_stage=reprice_stage,
                    decision_best_bid=best_bid,
                    decision_best_ask=best_ask,
                    decision_tick_size=tick,
                    now=now,
                    live_enabled=bool(args.live and args.confirm_live),
                )
                plans.append(plan)
    return plans, decisions


def maker_rearm_attempts(output_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the first defer and latest rearm evaluation for each signal."""

    attempts: dict[str, dict[str, dict[str, Any]]] = {}
    for row in iter_jsonl(output_dir / "entry_attempts.jsonl"):
        sid = str(row.get("signal_id") or "")
        if not sid:
            continue
        action = str(row.get("maker_live_action") or "")
        if action not in {"skip_terminal", "defer_post_update_rearm"}:
            continue
        bucket = attempts.setdefault(sid, {})
        if "deferred" not in bucket:
            bucket["deferred"] = row
        bucket["latest"] = row
    return attempts


def post_update_maker_rearm_score(
    *,
    args: argparse.Namespace,
    output_dir: Path,
    original_score: Mapping[str, Any],
    deferred_attempt: Mapping[str, Any],
    latest_attempt: Mapping[str, Any],
    now: datetime,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    """Re-score a deferred Core maker on a strictly newer weather epoch.

    This does not create a new Core signal.  It only gives the missing maker
    children of an already selected city-day one bounded chance per new state.
    """

    created = parse_utc(deferred_attempt.get("created_at_utc"))
    max_age_sec = maker_profile_parameter("post_update_rearm_max_age_sec")
    common = {
        "maker_rearm_parent_taker_ask": (
            finite(original_score.get("current_yes_ask")) or 0.0
        ),
        "maker_rearm_original_state_ref": str(
            deferred_attempt.get("data_epoch_ref") or ""
        ),
        "maker_rearm_max_age_sec": max_age_sec,
    }
    if created is None or (now - created).total_seconds() > max_age_sec:
        return "terminal", None, {
            **common,
            "maker_rearm_status": "expired",
            "maker_rearm_terminal": True,
            "maker_rearm_reason": "post_update_rearm_window_expired",
        }

    city_day = (
        str(original_score.get("city") or ""),
        str(original_score.get("target_date") or ""),
    )
    latest = latest_weather_epochs(output_dir / "state_decisions.jsonl").get(city_day)
    if latest is None:
        return "waiting", None, {
            **common,
            "maker_rearm_status": "waiting",
            "maker_rearm_reason": "latest_weather_state_unavailable",
        }
    original_ref = str(deferred_attempt.get("data_epoch_ref") or "")
    latest_ref = weather_state_epoch_ref(latest)
    if not latest_ref or latest_ref == original_ref:
        return "waiting", None, {
            **common,
            "maker_rearm_status": "waiting",
            "maker_rearm_state_ref": latest_ref,
            "maker_rearm_reason": "new_weather_epoch_not_observed",
        }
    original_observation_epoch = parse_utc(
        deferred_attempt.get("data_epoch_ts_utc")
        or original_score.get("source_report_ts_utc")
    )
    latest_observation_epoch = parse_utc(latest.get("source_report_ts_utc"))
    if (
        original_observation_epoch is None
        or latest_observation_epoch is None
        or latest_observation_epoch <= original_observation_epoch
    ):
        return "waiting", None, {
            **common,
            "maker_rearm_status": "waiting",
            "maker_rearm_state_ref": latest_ref,
            "maker_rearm_reason": "new_source_report_not_observed",
        }
    if str(latest_attempt.get("maker_rearm_evaluated_state_ref") or "") == latest_ref:
        return "waiting", None, {
            **common,
            "maker_rearm_status": "waiting",
            "maker_rearm_state_ref": latest_ref,
            "maker_rearm_reason": "weather_epoch_already_re_scored",
        }

    original_token = str(
        original_score.get("current_yes_token_id")
        or original_score.get("token_id")
        or ""
    )
    latest_token = str(
        latest.get("current_yes_token_id") or latest.get("token_id") or ""
    )
    original_bracket = str(
        original_score.get("current_bracket") or original_score.get("bracket") or ""
    )
    latest_bracket = str(
        latest.get("current_bracket") or latest.get("bracket") or ""
    )
    if latest_token != original_token or latest_bracket != original_bracket:
        return "terminal", None, {
            **common,
            "maker_rearm_status": "invalidated",
            "maker_rearm_terminal": True,
            "maker_rearm_evaluated_state_ref": latest_ref,
            "maker_rearm_reason": "exact_bracket_or_token_changed",
        }

    freshness_ok, freshness_reason = signal_runner.observation_freshness_valid(latest)
    if not freshness_ok:
        return "evaluated", None, {
            **common,
            "maker_rearm_status": "not_eligible",
            "maker_rearm_evaluated_state_ref": latest_ref,
            "maker_rearm_reason": freshness_reason,
        }

    with market_httpx_client(
        args.book_proxy, timeout=float(args.book_timeout_sec)
    ) as client:
        book = signal_runner.fetch_full_book(client, latest_token)
    enriched = {
        **dict(latest),
        "current_yes_bid": book.get("bid"),
        "current_yes_ask": book.get("ask"),
        "current_yes_bid_size": book.get("bid_size"),
        "current_yes_ask_size": book.get("ask_size"),
        "current_yes_tick_size": book.get("tick_size"),
        "current_yes_book_status": book.get("status"),
        "current_yes_book_fetched_at_utc": book.get("fetched_at_utc"),
        "checkpoint_key": str(original_score.get("checkpoint_key") or ""),
        "artifact_hash": str(original_score.get("artifact_hash") or ""),
    }
    result = evaluate_entry(
        enriched,
        book.get("asks") or [],
        load_artifact(Path(args.artifact)),
    )
    meta = {
        **common,
        "maker_rearm_status": "eligible" if bool(result.get("eligible")) else "not_eligible",
        "maker_rearm_evaluated_state_ref": latest_ref,
        "maker_rearm_state_ref": latest_ref,
        "maker_rearm_reason": (
            "fresh_positive_taker_net_ev"
            if bool(result.get("eligible"))
            else ",".join(map(str, result.get("reasons") or ["core_not_eligible"]))
        ),
        "maker_rearm_model_edge_after_fee_and_depth": (
            finite(result.get("model_edge_after_fee_and_depth")) or 0.0
        ),
        "maker_rearm_current_taker_ask": finite(book.get("ask")) or 0.0,
    }
    if not bool(result.get("eligible")):
        return "evaluated", None, meta
    return "eligible", {**enriched, **result, **meta}, meta


def new_entry_plans(
    args: argparse.Namespace,
    output_dir: Path,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scores = latest_rows_by_checkpoint(output_dir / "pre_live_scores.jsonl")
    attempted_roles = attempted_child_roles(output_dir, now=now)
    rearm_attempts = maker_rearm_attempts(output_dir)
    family_paths = family_live_order_files(output_dir)
    family_city_days = submitted_city_days(family_paths)
    near_core_filled = near_core_maker_probe.filled_exposure_by_city_day(
        iter_jsonl(output_dir / "live_orders.jsonl")
    )
    used_city_days, used_cost = daily_family_usage(family_paths, now)
    plans: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for would in iter_jsonl(output_dir / "would_orders.jsonl"):
        row = scores.get(str(would.get("checkpoint_key") or ""))
        if row is None:
            continue
        sid = signal_id(row)
        city_day = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        existing_roles = attempted_roles.get(sid, set())
        expected_roles = {"taker", "maker_staged"}
        if expected_roles.issubset(existing_roles):
            continue
        planning_row: Mapping[str, Any] = row
        rearm_meta: dict[str, Any] = {}
        missing_maker_roles = {"maker_staged"} - existing_roles
        if "taker" in existing_roles and missing_maker_roles:
            history = rearm_attempts.get(sid, {})
            deferred = history.get("deferred")
            latest_attempt = history.get("latest", deferred or {})
            if deferred is None:
                # Missing maker children without an explicit clock defer are not
                # a post-update rearm.  Preserve the existing same-epoch recovery
                # path for a taker-only partial batch.
                rearm_status, rearmed_row = "same_epoch_recovery", None
            else:
                rearm_status, rearmed_row, rearm_meta = post_update_maker_rearm_score(
                    args=args,
                    output_dir=output_dir,
                    original_score=row,
                    deferred_attempt=deferred,
                    latest_attempt=latest_attempt,
                    now=now,
                )
            if rearm_status == "waiting":
                continue
            if rearm_status in {"evaluated", "terminal"}:
                attempts.append(
                    {
                        "record_type": "current_yes_core_carry_entry_attempt",
                        "created_at_utc": now.isoformat(timespec="seconds"),
                        "signal_id": sid,
                        "city": city_day[0],
                        "target_date": city_day[1],
                        "checkpoint_key": row.get("checkpoint_key"),
                        "model_probability_hold": row.get("model_probability_hold"),
                        "status": "planned",
                        "reason": "",
                        "live_enabled": bool(args.live and args.confirm_live),
                        "maker_requested_shares": float(args.maker_shares)
                        + float(args.pullback_maker_shares),
                        "maker_planned_shares": 0.0,
                        "staged_maker_planned": False,
                        "pullback_maker_planned": False,
                        "maker_live_action": (
                            "skip_terminal"
                            if rearm_status == "terminal"
                            else "defer_post_update_rearm"
                        ),
                        "maker_post_update_live_rearm": True,
                        **rearm_meta,
                    }
                )
                continue
            if rearm_status == "eligible" and rearmed_row is None:
                continue
            if rearmed_row is not None:
                planning_row = rearmed_row
        reason = ""
        if not existing_roles and (
            bool(would.get("family_city_day_conflict")) or city_day in family_city_days
        ):
            reason = "family_city_day_conflict"
        elif not existing_roles and used_city_days >= int(args.max_city_days_per_bj_day):
            reason = "daily_city_day_cap"
        existing_near_core_fill = min(
            float(args.maker_shares), near_core_filled.get(city_day, 0.0)
        )
        remaining_core_maker_shares = max(
            0.0, float(args.maker_shares) - existing_near_core_fill
        )
        entry_plans = (
            []
            if reason
            else build_entry_plans(
                planning_row,
                live_enabled=bool(args.live and args.confirm_live),
                now=now,
                taker_shares=float(args.taker_shares),
                maker_shares=remaining_core_maker_shares,
                pullback_maker_shares=float(args.pullback_maker_shares),
                order_ttl_min=float(args.order_ttl_min),
            )
        )
        entry_plans = [
            plan
            for plan in entry_plans
            if str(plan.get("child_order_role") or "") not in existing_roles
        ]
        if (
            not reason
            and "taker" not in existing_roles
            and not any(plan.get("child_order_role") == "taker" for plan in entry_plans)
        ):
            reason = "missing_taker_plan"
            entry_plans = []
        planned_cost = entry_plan_cost_reservation(entry_plans)
        if (
            not reason
            and used_cost + planned_cost > float(args.max_daily_cost_usd)
        ):
            reason = "daily_cost_cap"
            entry_plans = []
            planned_cost = 0.0
        maker_clock = maker_clock_assessment(
            planning_row,
            now=now,
            order_ttl_min=float(args.order_ttl_min),
        )
        maker_planned_roles = {
            str(plan.get("child_order_role") or "")
            for plan in entry_plans
            if str(plan.get("child_order_role") or "").startswith("maker_")
        }
        attempts.append(
            {
                "record_type": "current_yes_core_carry_entry_attempt",
                "created_at_utc": now.isoformat(timespec="seconds"),
                "signal_id": sid,
                "city": city_day[0],
                "target_date": city_day[1],
                "checkpoint_key": row.get("checkpoint_key"),
                "model_probability_hold": row.get("model_probability_hold"),
                "status": "blocked" if reason else "planned",
                "reason": reason,
                "live_enabled": bool(args.live and args.confirm_live),
                "maker_requested_shares": float(args.maker_shares)
                + float(args.pullback_maker_shares),
                "near_core_filled_shares_counted_as_existing_exposure": existing_near_core_fill,
                "remaining_core_maker_shares": remaining_core_maker_shares,
                "maker_planned_shares": sum(
                    float(plan.get("size") or 0.0)
                    for plan in entry_plans
                    if str(plan.get("child_order_role") or "").startswith("maker_")
                ),
                "staged_maker_planned": "maker_staged" in maker_planned_roles,
                "pullback_maker_planned": "maker_pullback" in maker_planned_roles,
                "maker_live_action": (
                    "entry_blocked"
                    if reason
                    else (
                        "post"
                        if maker_planned_roles
                        else (
                            "defer_post_update_rearm"
                            if bool(maker_clock.get("maker_post_update_live_rearm"))
                            and maker_clock.get("maker_clock_status")
                            == "pre_source_report_blackout"
                            else "skip_terminal"
                        )
                    )
                ),
                "maker_shadow_revalidation_shares": (
                    float(args.maker_shares) + float(args.pullback_maker_shares)
                    if not reason
                    and not maker_planned_roles
                    and maker_clock.get("maker_clock_status")
                    == "pre_source_report_blackout"
                    else 0.0
                ),
                **maker_clock,
                **rearm_meta,
            }
        )
        attempted_roles.setdefault(sid, set()).update(
            str(plan.get("child_order_role") or "") for plan in entry_plans
        )
        defer_missing_makers = bool(
            maker_clock.get("maker_post_update_live_rearm")
        ) and maker_clock.get("maker_clock_status") == "pre_source_report_blackout"
        if not reason and not defer_missing_makers:
            for maker_role in {"maker_staged"} - maker_planned_roles:
                attempted_roles[sid].add(maker_role)
        if entry_plans:
            plans.extend(entry_plans)
            if not existing_roles:
                used_city_days += 1
            used_cost += planned_cost
    return plans, attempts


def configure_signal_runner(output_dir: Path) -> None:
    signal_runner.STRATEGY_ID = STRATEGY_ID
    signal_runner.STRATEGY_INSTANCE = STRATEGY_INSTANCE
    signal_runner.DECISION_MODE = "frozen_core_probability_first_positive_ten_share_taker_ev"
    signal_runner.OUTPUT_DIR = output_dir
    signal_runner.ARTIFACT_PATH = ARTIFACT_PATH


def validate_runtime_arguments(args: argparse.Namespace) -> None:
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if float(args.near_core_book_max_age_sec) <= 0:
        raise RuntimeError("--near-core-book-max-age-sec must be positive")
    if (
        args.live
        and args.near_core_maker_probe_enabled
        and not args.confirm_near_core_maker_probe_live
    ):
        raise RuntimeError(
            "live near-Core probe requires --confirm-near-core-maker-probe-live"
        )
    if args.live and args.near_core_maker_probe_enabled:
        readiness_path = Path(str(args.near_core_ws_control_manifest or ""))
        if not str(args.near_core_ws_control_manifest or "") or not readiness_path.is_file():
            raise RuntimeError(
                "live near-Core probe requires an E1 WS control-readiness manifest"
            )
        try:
            readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"near-Core WS control-readiness manifest unreadable: {exc}"
            ) from exc
        if not isinstance(readiness, Mapping):
            raise RuntimeError("near-Core WS control-readiness manifest must be an object")
        readiness_failures = near_core_maker_probe.ws_control_readiness_failures(
            readiness
        )
        if readiness_failures:
            raise RuntimeError(
                "near-Core WS control-readiness failed: "
                + ",".join(readiness_failures)
            )
    if (
        float(args.taker_shares) != FROZEN_TAKER_SHARES
        or float(args.maker_shares) != FROZEN_MAKER_SHARES
        or float(args.pullback_maker_shares) != FROZEN_PULLBACK_MAKER_SHARES
    ):
        raise RuntimeError(
            "frozen tiny-live split requires exactly 10 taker + one shared 5-share maker budget"
        )
    if float(args.near_core_maker_shares) != near_core_maker_probe.FIXED_SHARES:
        raise RuntimeError("near-Core maker probe requires exactly 5 shares")


def run_once(
    args: argparse.Namespace,
    *,
    decision_packet_tail_index: DecisionPacketTailIndex | None = None,
) -> dict[str, Any]:
    assert_runtime_contract()
    validate_runtime_arguments(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_signal_runner(output_dir)
    signal_summary = signal_runner.run_once(args)
    now = datetime.now(timezone.utc)
    decision_packets = write_new_decision_packets(
        output_dir, tail_index=decision_packet_tail_index
    )
    capture_demands = write_capture_demands(
        output_dir,
        packets=decision_packets["written_packets"],
    )
    candidate_capture_demands = write_candidate_capture_demands(output_dir)
    journal_terminal_recoveries = recover_journal_terminal_makers(output_dir)
    near_core_journal_terminal_recoveries = (
        recover_near_core_journal_terminal_makers(output_dir)
    )
    entry_plans, attempts = new_entry_plans(args, output_dir, now=now)
    core_actionable_city_days = {
        near_core_maker_probe.city_day(plan) for plan in entry_plans
    }
    near_lifecycle_plans, near_lifecycle_decisions, superseded_city_days = (
        near_core_lifecycle_plans(
            args,
            output_dir,
            now=now,
            core_actionable_city_days=core_actionable_city_days,
        )
    )
    if superseded_city_days:
        entry_plans = [
            plan
            for plan in entry_plans
            if near_core_maker_probe.city_day(plan) not in superseded_city_days
        ]
        for attempt in attempts:
            if near_core_maker_probe.city_day(attempt) not in superseded_city_days:
                continue
            attempt.update(
                {
                    "status": "blocked",
                    "reason": "near_core_cancel_must_confirm_before_core_submit",
                    "maker_live_action": "defer_until_near_core_cancel_terminal",
                    "maker_planned_shares": 0.0,
                    "staged_maker_planned": False,
                    "pullback_maker_planned": False,
                }
            )
    remaining_core_city_days = {
        near_core_maker_probe.city_day(plan) for plan in entry_plans
    }
    near_entry_plans, near_entry_decisions = near_core_entry_plans(
        args,
        output_dir,
        now=now,
        core_actionable_city_days=remaining_core_city_days,
    )
    lifecycle_plans, lifecycle_decisions = maker_lifecycle_plans(args, output_dir, now=now)
    for decision in lifecycle_decisions:
        append_jsonl(output_dir / "maker_lifecycle_decisions.jsonl", decision)
    append_near_core_ledger(
        output_dir, [*near_lifecycle_decisions, *near_entry_decisions]
    )
    plans = [
        *near_lifecycle_plans,
        *lifecycle_plans,
        *entry_plans,
        *near_entry_plans,
    ]
    execution = execute_plans(args, plans, output_dir)
    near_core_order_ledger_rows = sync_near_core_order_ledger(output_dir)
    near_core_artifacts = write_near_core_runtime_artifacts(args, output_dir)
    for attempt in attempts:
        append_jsonl(output_dir / "entry_attempts.jsonl", attempt)
    summary = {
        "status": "ok" if execution["exit_code"] == 0 else "executor_error",
        "generated_at_utc": utc_now(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "config_id": CONFIG_ID,
        "execution_profile": EXECUTION_PROFILE,
        "resolved_execution_profile": EXECUTION_PROFILE,
        "execution_config_id": execution_config_id_for_profile(EXECUTION_PROFILE),
        "mode": "tiny_live" if args.live else "paper_would_order",
        "live_enabled": bool(args.live and args.confirm_live),
        "artifact_hash": load_artifact(ARTIFACT_PATH)["artifact_hash"],
        "signal_status": signal_summary.get("status"),
        "signal_snapshot_file": signal_summary.get("snapshot_file"),
        "decision_packets_written": decision_packets["written"],
        "decision_packet_alerts": decision_packets["alerts"],
        "capture_demands_written": capture_demands["written"],
        "capture_demand_alerts": capture_demands["alerts"],
        "candidate_capture_demands_written": candidate_capture_demands["written"],
        "candidate_capture_demand_alerts": candidate_capture_demands["alerts"],
        "candidate_capture_count": candidate_capture_demands["candidate_count"],
        "entry_attempts": len(attempts),
        "entry_plans": len(entry_plans),
        "near_core_entry_plans": len(near_entry_plans),
        "near_core_lifecycle_plans": len(near_lifecycle_plans),
        "near_core_lifecycle_decisions": len(near_lifecycle_decisions),
        "near_core_superseded_core_city_days": sorted(
            [list(key) for key in superseded_city_days]
        ),
        "near_core_order_ledger_rows_written": near_core_order_ledger_rows,
        "near_core_feature_enabled": bool(args.near_core_maker_probe_enabled),
        "near_core_live_authorized": bool(
            args.live
            and args.confirm_live
            and args.confirm_near_core_maker_probe_live
        ),
        "near_core_manifest_file": str(output_dir / "near_core_maker_manifest.json"),
        "near_core_report_file": str(output_dir / "near_core_maker_latest_report.json"),
        "near_core_promotion_gate": near_core_artifacts["promotion_gate"],
        "maker_lifecycle_plans": len(lifecycle_plans),
        "maker_lifecycle_decisions": len(lifecycle_decisions),
        "journal_terminal_recoveries": journal_terminal_recoveries,
        "near_core_journal_terminal_recoveries": (
            near_core_journal_terminal_recoveries
        ),
        "taker_shares": float(args.taker_shares),
        "maker_shares": float(args.maker_shares),
        "pullback_maker_shares": float(args.pullback_maker_shares),
        "maximum_signal_shares": (
            float(args.taker_shares)
            + float(args.maker_shares)
            + float(args.pullback_maker_shares)
        ),
        "maker_budget_mode": "single_active_order_staged_then_pullback",
        "shared_maker_budget_shares": float(args.maker_shares),
        "maker_refresh_sec": float(args.maker_refresh_sec),
        "maker_reprice_limit": maker_max_reprices(),
        "near_core_book_max_age_sec": float(args.near_core_book_max_age_sec),
        "maker_cancel_buffer_sec": get_execution_profile(
            EXECUTION_PROFILE
        ).cancel_buffer_sec,
        "maker_retained_edge": maker_profile_parameter("retained_edge"),
        "maker_stage_midpoint_after_sec": maker_profile_parameter(
            "stage_midpoint_after_sec"
        ),
        "maker_stage_near_ask_after_sec": maker_profile_parameter(
            "stage_near_ask_after_sec"
        ),
        "max_city_days_per_bj_day": int(args.max_city_days_per_bj_day),
        "max_daily_cost_usd": float(args.max_daily_cost_usd),
        "execution": execution,
        **DEPLOYMENT_METADATA,
    }
    publish_runtime_state_best_effort(args, output_dir, summary, signal_summary)
    write_json(output_dir / "latest_summary.json", summary)
    append_jsonl(output_dir / "summary_history.jsonl", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    ap = signal_runner.parser()
    ap.description = __doc__
    ap.set_defaults(
        output_dir=str(OUTPUT_DIR),
        artifact=str(ARTIFACT_PATH),
        interval_seconds=15.0,
        summary_filename="signal_latest_summary.json",
        summary_history_filename="signal_summary_history.jsonl",
    )
    ap.add_argument("--taker-shares", type=float, default=FROZEN_TAKER_SHARES)
    ap.add_argument("--maker-shares", type=float, default=FROZEN_MAKER_SHARES)
    ap.add_argument(
        "--pullback-maker-shares",
        type=float,
        default=FROZEN_PULLBACK_MAKER_SHARES,
    )
    ap.add_argument("--maker-refresh-sec", type=float, default=15.0)
    ap.add_argument("--order-ttl-min", type=float, default=15.0)
    ap.add_argument("--max-city-days-per-bj-day", type=int, default=10)
    ap.add_argument("--max-daily-cost-usd", type=float, default=100.0)
    ap.add_argument("--executor-timeout-sec", type=float, default=60.0)
    ap.add_argument("--near-core-book-max-age-sec", type=float, default=90.0)
    ap.add_argument("--market-proxy", default=None)
    ap.add_argument("--runtime-db", default=str(ROOT / "runtime/weather.db"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--confirm-live", action="store_true")
    ap.add_argument("--near-core-maker-probe-enabled", action="store_true")
    ap.add_argument(
        "--confirm-near-core-maker-probe-live", action="store_true"
    )
    ap.add_argument(
        "--near-core-maker-shares",
        type=float,
        default=near_core_maker_probe.FIXED_SHARES,
    )
    ap.add_argument("--near-core-order-ttl-min", type=float, default=15.0)
    ap.add_argument("--near-core-candidate-max-age-sec", type=float, default=90.0)
    ap.add_argument("--near-core-max-city-days-per-bj-day", type=int, default=1)
    ap.add_argument("--near-core-max-daily-cost-usd", type=float, default=5.0)
    ap.add_argument("--near-core-ws-control-manifest", default="")
    return ap


def publish_loop_error(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    error = {
        "status": "error",
        "generated_at_utc": utc_now(),
        "strategy_instance": STRATEGY_INSTANCE,
        "mode": "tiny_live" if args.live else "paper_would_order",
        "live_enabled": bool(args.live and args.confirm_live),
        "error": f"{type(exc).__name__}: {exc}",
    }
    publish_runtime_state_best_effort(args, output_dir, error, {})
    write_json(output_dir / "latest_summary.json", error)
    append_jsonl(output_dir / "summary_history.jsonl", error)
    return error


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass
    args = parser().parse_args()
    if args.command == "run":
        print(json.dumps(run_once(args), ensure_ascii=False, sort_keys=True))
        return 0
    decision_packet_tail_index = DecisionPacketTailIndex()
    while True:
        try:
            print(
                json.dumps(
                    run_once(args, decision_packet_tail_index=decision_packet_tail_index),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            error = publish_loop_error(args, exc)
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), flush=True)
        time.sleep(max(10.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
