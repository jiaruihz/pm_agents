"""Deterministic event-time replay shared by all city model adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .contracts import (
    EventEnvelope,
    PREDICTION_SCHEMA_VERSION,
    parse_utc,
    stable_sha256,
    utc_text,
    validate_prediction_row,
)


class InputProvider(Protocol):
    def events(self) -> Iterable[EventEnvelope]: ...


class CheckpointBuilder(Protocol):
    def build(
        self,
        *,
        event: EventEnvelope,
        decision_ts_utc: datetime,
        checkpoint_id: str,
        state: Mapping[str, Any],
    ) -> Iterable[Mapping[str, Any]]: ...


@dataclass
class VirtualClock:
    """Monotone clock advanced only by an event's available timestamp."""

    current: datetime | None = None

    def advance_to(self, value: str | datetime) -> datetime:
        target = value if isinstance(value, datetime) else parse_utc(value)
        if self.current is not None and target < self.current:
            raise ValueError("virtual clock cannot move backwards")
        self.current = target
        return target


@dataclass(frozen=True)
class ReplayResult:
    rows: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]


class ReplayRunner:
    """Fold PIT events and ask the injected city adapter for prediction rows."""

    def __init__(
        self,
        *,
        input_provider: InputProvider,
        checkpoint_builder: CheckpointBuilder,
        clock: VirtualClock | None = None,
    ):
        self.input_provider = input_provider
        self.checkpoint_builder = checkpoint_builder
        self.clock = clock or VirtualClock()

    def run(self) -> ReplayResult:
        events = sorted(
            self.input_provider.events(),
            key=lambda item: (parse_utc(item.available_at_utc), item.event_id),
        )
        state: dict[tuple[str, str], dict[str, Any]] = {}
        rows: list[dict[str, Any]] = []
        checkpoint_ids: list[str] = []
        seen_event_ids: set[str] = set()
        for event in events:
            if event.event_id in seen_event_ids:
                raise ValueError(f"duplicate event_id: {event.event_id}")
            if (
                event.revision_of_event_id is not None
                and event.revision_of_event_id not in seen_event_ids
            ):
                raise ValueError(
                    "revision parent was not available before child: "
                    f"{event.revision_of_event_id}"
                )
            seen_event_ids.add(event.event_id)
            decision = self.clock.advance_to(event.available_at_utc)
            group_key = (event.city, event.target_date)
            group_state = state.setdefault(group_key, {})
            group_state[event.state_key] = event.to_dict()
            if not event.material_state_change:
                continue
            checkpoint_id = stable_sha256(
                {
                    "city": event.city,
                    "target_date": event.target_date,
                    "event_id": event.event_id,
                    "decision_ts_utc": utc_text(decision),
                    "state_event_ids": sorted(
                        value["event_id"] for value in group_state.values()
                    ),
                }
            )
            checkpoint_ids.append(checkpoint_id)
            built = self.checkpoint_builder.build(
                event=event,
                decision_ts_utc=decision,
                checkpoint_id=checkpoint_id,
                state=dict(group_state),
            )
            rows.extend(validate_prediction_row(row) for row in built)
        rows.sort(
            key=lambda row: (
                row["decision_ts_utc"],
                row["city"],
                row["target_id"],
                row["checkpoint_id"],
            )
        )
        output_hash = stable_sha256(rows)
        manifest = {
            "schema_version": "weather_city_replay_manifest_v1",
            "event_count": len(events),
            "checkpoint_count": len(checkpoint_ids),
            "prediction_row_count": len(rows),
            "cities": sorted({event.city for event in events}),
            "payload_kinds": sorted({event.payload_kind for event in events}),
            "event_stream_hash": stable_sha256(
                [event.to_dict() for event in events]
            ),
            "checkpoint_hash": stable_sha256(checkpoint_ids),
            "output_hash": output_hash,
        }
        return ReplayResult(rows=tuple(rows), manifest=manifest)


class FixtureInputCatalog:
    """Normalize frozen Phase 0 fixture envelopes into a common event stream."""

    def __init__(self, paths: Sequence[Path]):
        self.paths = tuple(sorted(Path(path) for path in paths))

    @staticmethod
    def _available(kind: str, row: Mapping[str, Any]) -> str:
        fields = {
            "point_observation": ("source_first_seen_at_utc", "fetched_at_utc"),
            "interval_initial": ("available_at_utc",),
            "interval_revision_pair": ("available_at_utc",),
            "one_sided_book": ("book_fetched_at_utc",),
            "cross_day_partition": ("fetched_at_utc",),
            "multi_anchor_mismatch": ("book_fetched_at_utc", "fetched_at_utc"),
        }[kind]
        for field in fields:
            value = row.get(field)
            if value is not None:
                return str(value)
        raise ValueError(f"fixture {kind} has no availability clock")

    @staticmethod
    def _observed(row: Mapping[str, Any]) -> str | None:
        for field in (
            "observation_time_utc",
            "measurement_interval_end_utc",
            "last_obs_utc",
        ):
            if row.get(field) is not None:
                return str(row[field])
        return None

    def events(self) -> Iterable[EventEnvelope]:
        seen_payloads: dict[str, str] = {}
        for path in self.paths:
            fixture = json.loads(path.read_text(encoding="utf-8"))
            kind = str(fixture["fixture_kind"])
            raw_records = fixture["records"]
            records = raw_records if isinstance(raw_records, list) else [raw_records]
            for index, original in enumerate(records):
                row = dict(original)
                if kind == "multi_anchor_mismatch":
                    row = {**row, **row["book"]}
                available = self._available(kind, row)
                city = str(row.get("city") or row.get("book", {}).get("city"))
                target_date = str(
                    row.get("target_date") or row.get("book", {}).get("target_date")
                )
                event_id = str(
                    row.get("information_event_id")
                    or stable_sha256(
                        {
                            "fixture": path.name,
                            "index": index,
                            "record": original,
                        }
                    )
                )
                payload_hash = stable_sha256(original)
                previous_hash = seen_payloads.get(event_id)
                if previous_hash is not None:
                    if previous_hash != payload_hash:
                        raise ValueError(
                            f"fixture event_id has conflicting payloads: {event_id}"
                        )
                    continue
                seen_payloads[event_id] = payload_hash
                yield EventEnvelope(
                    event_id=event_id,
                    city=city,
                    target_date=target_date,
                    payload_kind=(
                        "revision"
                        if row.get("event_role") == "revision"
                        else "interval"
                        if kind.startswith("interval")
                        else kind
                    ),
                    state_key=(
                        f"interval:{row.get('source')}:{row.get('provider_item_id')}"
                        if kind.startswith("interval")
                        else f"book:{row.get('token_id') or row.get('condition_id')}"
                        if kind in {"one_sided_book", "multi_anchor_mismatch"}
                        else f"point:{row.get('source')}:{row.get('station')}"
                        if kind == "point_observation"
                        else f"status:{row.get('source')}:{row.get('station')}"
                    ),
                    source=str(row.get("source") or "fixture"),
                    available_at_utc=utc_text(available),
                    observed_at_utc=(
                        utc_text(self._observed(row))
                        if self._observed(row) is not None
                        else None
                    ),
                    first_seen_at_utc=utc_text(
                        row.get("first_seen_at_utc")
                        or row.get("source_first_seen_at_utc")
                        or available
                    ),
                    material_state_change=bool(
                        row.get("material_state_change", True)
                    ),
                    revision_of_event_id=row.get("revision_of_event_id"),
                    physical_ref={
                        "fixture_path": str(path),
                        "fixture_kind": kind,
                        "fixture_record_index": index,
                        "physical_shard": fixture.get("physical_shard"),
                    },
                    payload=original,
                )


class FixtureCheckpointBuilder:
    """Structural adapter used to prove fixture replay without fake probabilities."""

    def build(
        self,
        *,
        event: EventEnvelope,
        decision_ts_utc: datetime,
        checkpoint_id: str,
        state: Mapping[str, Any],
    ) -> Iterable[Mapping[str, Any]]:
        payload = event.payload
        market_p = None
        if event.payload_kind in {"one_sided_book", "multi_anchor_mismatch"}:
            summary = (
                payload.get("summary")
                or payload.get("book", {}).get("summary")
                or {}
            )
            bid = summary.get("best_bid")
            ask = summary.get("best_ask")
            if bid is not None and ask is not None:
                market_p = (float(bid) + float(ask)) / 2.0
        coverage_status = (
            "coverage_gap:one_sided_book"
            if event.payload_kind == "one_sided_book"
            else "coverage_gap:awaiting_first_observation"
            if event.payload_kind == "cross_day_partition"
            else "diagnostic:anchor_mismatch"
            if event.payload_kind == "multi_anchor_mismatch"
            else "input_only:no_model_output"
        )
        yield {
            "schema_version": PREDICTION_SCHEMA_VERSION,
            "city": event.city,
            "target_date": event.target_date,
            "decision_ts_utc": utc_text(decision_ts_utc),
            "target_id": f"fixture:{event.payload_kind}",
            "target_kind": (
                "market_expression"
                if event.payload_kind in {"one_sided_book", "multi_anchor_mismatch"}
                else "physical_path"
            ),
            "p_model": None,
            "label": None,
            "split": "fixture",
            "model_id": "fixture_contract_only",
            "feature_set_id": "fixture_contract_only",
            "pit_provenance": "live_capture",
            "checkpoint_id": checkpoint_id,
            "scorable_status": "not_available:model_or_label",
            "coverage_status": coverage_status,
            "market_p": market_p,
            "market_feature_role": "none",
            "market_feature_clock": "none",
            "feature_book_snapshot_id": None,
            "execution_book_snapshot_id": None,
            "expression_side": payload.get("outcome"),
            "executable_cost": None,
            "market_snapshot_ts_utc": (
                event.available_at_utc if "book" in event.payload_kind else None
            ),
            "event_id": event.event_id,
            "event_payload_kind": event.payload_kind,
            "event_available_at_utc": event.available_at_utc,
            "input_refs": [dict(event.physical_ref)],
            "runtime_lineage": {
                "replay_runtime": "weather_model_evaluation.ReplayRunner",
                "state_event_ids": sorted(
                    value["event_id"] for value in state.values()
                ),
            },
        }
