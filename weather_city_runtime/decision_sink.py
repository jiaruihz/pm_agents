"""Append-only shared decision journals for city probability runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from weather_data_feed.information_events import canonical_json_hash

from .legacy_adapters import (
    legacy_bundle_from_evaluation,
    legacy_trade_intent_from_paper_intent,
)


DECISION_JOURNAL_SCHEMA_VERSION = "weather_city_decision_journal_v1"
# Compatibility export for the Phase-3A transition commit. New code must use the
# authoritative journal name above.
DUAL_WRITE_SCHEMA_VERSION = DECISION_JOURNAL_SCHEMA_VERSION


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        )


def _load_keys(path: Path, field: str) -> set[str]:
    if not path.is_file():
        return set()
    values: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            value = row.get(field)
            if value:
                values.add(str(value))
    return values


@dataclass(frozen=True)
class SinkResult:
    written_bundles: int = 0
    written_intents: int = 0
    written_blockers: int = 0
    written_intent_blockers: int = 0
    conversion_errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "written_bundles": self.written_bundles,
            "written_intents": self.written_intents,
            "written_blockers": self.written_blockers,
            "written_intent_blockers": self.written_intent_blockers,
            "conversion_errors": self.conversion_errors,
        }


class DecisionContractJournalSink:
    """Persist the shared Phase-2 contracts as the city runtime authority.

    The sink deliberately has no venue/execution dependency.  A paper intent can only
    become a zero-size ``TradeIntent``; live authority remains outside this package.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        cities: Iterable[str],
        legacy_output_dir: Path | None = None,
        execution_profile: str = "city_probability_zero_notional_v1",
    ) -> None:
        self.output_dir = output_dir.resolve()
        self.cities = frozenset(str(city) for city in cities)
        self.execution_profile = execution_profile
        if not self.cities:
            raise ValueError("decision journal requires at least one city")
        if legacy_output_dir is not None and self.output_dir == legacy_output_dir.resolve():
            raise ValueError("decision journal output must differ from legacy output")
        if self.output_dir.suffix in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("decision output must be a journal directory")

        self.bundle_path = self.output_dir / "decision_bundles.jsonl"
        self.intent_path = self.output_dir / "trade_intents.jsonl"
        self.blocker_path = self.output_dir / "checkpoint_blockers.jsonl"
        self.intent_blocker_path = self.output_dir / "trade_intent_blockers.jsonl"
        self.error_path = self.output_dir / "conversion_errors.jsonl"
        self._bundle_keys = _load_keys(self.bundle_path, "bundle_record_id")
        self._intent_keys = _load_keys(self.intent_path, "intent_id")
        self._blocker_keys = _load_keys(self.blocker_path, "checkpoint_id")
        self._intent_blocker_keys = _load_keys(
            self.intent_blocker_path, "source_evaluation_id"
        )
        self._source_evaluation_ids = _load_keys(
            self.bundle_path, "source_evaluation_id"
        )
        self._intent_dedupe_keys = _load_keys(self.intent_path, "dedupe_key")

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        legacy_output_dir: Path | None = None,
    ) -> "DecisionContractJournalSink | None":
        declaration = config.get("decision_contract_output") or {}
        if not declaration.get("enabled", False):
            return None
        if declaration.get("authority") != "active":
            raise ValueError("decision contract output authority must be active")
        if declaration.get("execution_mode") != "zero_notional":
            raise ValueError("decision output execution_mode must be zero_notional")
        return cls(
            Path(str(declaration["output_dir"])),
            cities=declaration.get("cities") or (),
            legacy_output_dir=legacy_output_dir,
            execution_profile=str(
                declaration.get("execution_profile")
                or "city_probability_zero_notional_v1"
            ),
        )

    @property
    def source_evaluation_ids(self) -> set[str]:
        return set(self._source_evaluation_ids)

    @property
    def intent_dedupe_keys(self) -> set[str]:
        return set(self._intent_dedupe_keys)

    @property
    def checkpoint_ids(self) -> set[str]:
        return set(self._blocker_keys)

    def _included(self, row: Mapping[str, Any]) -> bool:
        return str(row.get("city") or "") in self.cities

    def record_evaluation(self, row: Mapping[str, Any]) -> SinkResult:
        if not self._included(row):
            return SinkResult()
        try:
            bundle = legacy_bundle_from_evaluation(row)
            selected = bundle.signal_candidate.selected
            bundle_record_id = canonical_json_hash(
                {
                    "candidate_id": bundle.signal_candidate.candidate_id,
                    "selected": selected,
                }
            )
            if bundle_record_id in self._bundle_keys:
                return SinkResult()
            payload = {
                "schema_version": DECISION_JOURNAL_SCHEMA_VERSION,
                "bundle_record_id": bundle_record_id,
                "source_evaluation_id": row.get("evaluation_id"),
                "source_record_kind": row.get("record_kind"),
                "information_event": bundle.information_event,
                "state_checkpoint": bundle.state_checkpoint,
                "model_output": bundle.model_output.to_dict(),
                "signal_candidate": bundle.signal_candidate.to_dict(),
            }
            _append_jsonl(self.bundle_path, payload)
            self._bundle_keys.add(bundle_record_id)
            if row.get("evaluation_id"):
                self._source_evaluation_ids.add(str(row["evaluation_id"]))
            return SinkResult(written_bundles=1)
        except Exception as exc:
            return self._record_conversion_error("evaluation", row, exc)

    def record_paper_intent(self, row: Mapping[str, Any]) -> SinkResult:
        if not self._included(row):
            return SinkResult()
        bundle_result = self.record_evaluation(row)
        try:
            intent = legacy_trade_intent_from_paper_intent(
                row, execution_profile=self.execution_profile
            )
            if intent.intent_id in self._intent_keys:
                return bundle_result
            _append_jsonl(self.intent_path, intent.to_dict())
            self._intent_keys.add(intent.intent_id)
            self._intent_dedupe_keys.add(intent.dedupe_key)
            return SinkResult(
                written_bundles=bundle_result.written_bundles,
                written_intents=1,
                conversion_errors=bundle_result.conversion_errors,
            )
        except ValueError as exc:
            blocker = self._record_intent_blocker(row, exc)
            return SinkResult(
                written_bundles=bundle_result.written_bundles,
                written_intent_blockers=blocker.written_intent_blockers,
                conversion_errors=bundle_result.conversion_errors
                + blocker.conversion_errors,
            )
        except Exception as exc:
            error = self._record_conversion_error("paper_intent", row, exc)
            return SinkResult(
                written_bundles=bundle_result.written_bundles,
                conversion_errors=bundle_result.conversion_errors
                + error.conversion_errors,
            )

    def record_checkpoint_blocker(self, row: Mapping[str, Any]) -> SinkResult:
        if not self._included(row):
            return SinkResult()
        checkpoint_id = str(row.get("checkpoint_id") or "")
        if not checkpoint_id:
            return self._record_conversion_error(
                "checkpoint_blocker", row, ValueError("checkpoint_id is required")
            )
        if checkpoint_id in self._blocker_keys:
            return SinkResult()
        payload = {
            "schema_version": DECISION_JOURNAL_SCHEMA_VERSION,
            "record_kind": "checkpoint_blocker",
            "checkpoint_id": checkpoint_id,
            "city": row.get("city"),
            "target_date": row.get("target_date"),
            "decision_ts_utc": row.get("decision_ts_utc"),
            "blocker_reason": row.get("blocker_reason"),
            "details": row.get("details") or {},
            "source_row_hash": canonical_json_hash(row),
        }
        _append_jsonl(self.blocker_path, payload)
        self._blocker_keys.add(checkpoint_id)
        return SinkResult(written_blockers=1)

    def _record_conversion_error(
        self, stage: str, row: Mapping[str, Any], exc: Exception
    ) -> SinkResult:
        payload = {
            "schema_version": DECISION_JOURNAL_SCHEMA_VERSION,
            "record_kind": "conversion_error",
            "stage": stage,
            "city": row.get("city"),
            "source_evaluation_id": row.get("evaluation_id"),
            "source_row_hash": canonical_json_hash(row),
            "error": f"{type(exc).__name__}: {exc}",
        }
        payload["incident_id"] = canonical_json_hash(payload)
        _append_jsonl(self.error_path, payload)
        return SinkResult(conversion_errors=1)

    def _record_intent_blocker(
        self, row: Mapping[str, Any], exc: ValueError
    ) -> SinkResult:
        source_evaluation_id = str(row.get("evaluation_id") or "")
        if source_evaluation_id in self._intent_blocker_keys:
            return SinkResult()
        payload = {
            "schema_version": DECISION_JOURNAL_SCHEMA_VERSION,
            "record_kind": "trade_intent_blocker",
            "city": row.get("city"),
            "target_date": row.get("target_date"),
            "source_evaluation_id": source_evaluation_id,
            "position_key": row.get("position_key"),
            "blocker_reason": str(exc),
            "source_row_hash": canonical_json_hash(row),
        }
        payload["blocker_id"] = canonical_json_hash(payload)
        _append_jsonl(self.intent_blocker_path, payload)
        if source_evaluation_id:
            self._intent_blocker_keys.add(source_evaluation_id)
        return SinkResult(written_intent_blockers=1)
