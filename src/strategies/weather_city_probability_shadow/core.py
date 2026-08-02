from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[3]
CONFIG_SCHEMA_VERSION = "weather_city_probability_shadow_config_v2"
OUTPUT_SCHEMA_VERSION = "weather_city_probability_shadow_v2"
AUTHORITATIVE_CONFIG_SCHEMA_VERSION = "weather_city_probability_runtime_config_v3"
AUTHORITATIVE_OUTPUT_SCHEMA_VERSION = "weather_city_probability_runtime_v3"
ADAPTER_CONTRACT_VERSION = "weather_city_probability_adapter_v1"
FRAMEWORK_ID = "weather_city_intraday_runtime_v1"
STRATEGY_FAMILY = "weather.city_intraday_probability"

OUTPUT_SCHEMA = {
    "record_kind": "evaluation|checkpoint_blocker|error|summary|paper_intent",
    "schema_version": OUTPUT_SCHEMA_VERSION,
    "schema_fingerprint": "sha256",
    "runtime_identity": {
        "runtime_instance_id": "sha256",
        "repo_head": "git_sha",
        "repo_dirty_tracked": "bool",
        "config_sha256": "sha256",
        "loaded_module_sha256": "mapping[path,sha256]",
        "artifact_sha256": "mapping[path,sha256]",
        "upstream_producer_identity": "mapping[journal,runtime_identity]",
    },
}
OUTPUT_SCHEMA_FINGERPRINT = hashlib.sha256(
    json.dumps(OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()

AUTHORITATIVE_OUTPUT_SCHEMA = {
    "record_kind": "decision_bundle|checkpoint_blocker|runtime_error|summary|trade_intent",
    "schema_version": AUTHORITATIVE_OUTPUT_SCHEMA_VERSION,
    "decision_contracts": [
        "weather_city_model_output_v1",
        "weather_city_signal_candidate_v1",
        "weather_city_trade_intent_v1",
    ],
    "execution_mode": "zero_notional_shadow",
    "legacy_journals": "deprecated_read_only",
}
AUTHORITATIVE_OUTPUT_SCHEMA_FINGERPRINT = hashlib.sha256(
    json.dumps(
        AUTHORITATIVE_OUTPUT_SCHEMA, sort_keys=True, separators=(",", ":")
    ).encode()
).hexdigest()


@dataclass(frozen=True)
class CityScore:
    city: str
    target_date: str
    decision_ts_utc: str
    source_obs_ts_utc: str
    current_bracket: int
    market_side: str
    market_probability: float | None
    market_entry_price: float | None
    model_probability: float | None
    model_id: str
    feature_coverage: float
    missing_features: list[str]
    features: dict[str, float | None]
    market: dict[str, Any]
    lineage: dict[str, Any]
    evaluation_status: str = "scored"
    not_scorable_reason: str | None = None


class CityAdapter(Protocol):
    def score(self, profile: dict[str, Any], now: datetime) -> list[CityScore]: ...


class DecisionSink(Protocol):
    @property
    def source_evaluation_ids(self) -> set[str]: ...
    @property
    def intent_dedupe_keys(self) -> set[str]: ...
    @property
    def checkpoint_ids(self) -> set[str]: ...
    def record_evaluation(self, row: dict[str, Any]) -> Any: ...
    def record_paper_intent(self, row: dict[str, Any]) -> Any: ...
    def record_checkpoint_blocker(self, row: dict[str, Any]) -> Any: ...


class InputNotReady(RuntimeError):
    """Expected coverage state that must be journaled, not counted as an error."""

    def __init__(
        self,
        reason: str,
        *,
        city: str,
        target_date: str,
        decision_ts_utc: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(reason)
        self.reason = reason
        self.city = city
        self.target_date = target_date
        self.decision_ts_utc = decision_ts_utc
        self.details = details or {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def migrate_evaluation_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy v1 evaluations without inventing missing lineage."""

    if row.get("schema_version") in {
        OUTPUT_SCHEMA_VERSION,
        AUTHORITATIVE_OUTPUT_SCHEMA_VERSION,
    }:
        return dict(row)
    if row.get("schema_version") != "weather_city_probability_shadow_v1":
        raise ValueError(f"unsupported evaluation schema: {row.get('schema_version')}")
    migrated = dict(row)
    migrated["source_schema_version"] = "weather_city_probability_shadow_v1"
    migrated["schema_version"] = OUTPUT_SCHEMA_VERSION
    migrated["schema_fingerprint"] = OUTPUT_SCHEMA_FINGERPRINT
    migrated["record_kind"] = "evaluation"
    migrated["migration_status"] = "legacy_runtime_identity_unavailable"
    migrated["runtime_identity"] = None
    if "evaluation_status" not in migrated:
        scorable = (
            migrated.get("market_probability") is not None
            and migrated.get("model_probability") is not None
        )
        migrated["evaluation_status"] = "scored" if scorable else "not_scorable"
        migrated["not_scorable_reason"] = (
            None if scorable else "legacy_v1_missing_probability_unclassified"
        )
    return migrated


def iter_compatible_evaluations(paths: list[Path]):
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield migrate_evaluation_row(row)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def load_jsonl_keys(path: Path, field: str) -> set[str]:
    if not path.exists():
        return set()
    values: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line).get(field)
            except json.JSONDecodeError:
                continue
            if value:
                values.add(str(value))
    return values


JOURNAL_FILENAMES = {
    "evaluations": "evaluations.jsonl",
    "paper_intents": "paper_intents.jsonl",
    "checkpoints": "checkpoints.jsonl",
    "errors": "errors.jsonl",
}


def resolve_journal_catalog(config: dict[str, Any]) -> dict[str, list[Path]]:
    """Return one logical journal catalog while preserving schema-versioned files."""

    output_dir = Path(config["output_dir"])
    declared = config.get("journal_catalog") or {}
    result: dict[str, list[Path]] = {}
    for kind, filename in JOURNAL_FILENAMES.items():
        current = output_dir / filename
        paths = [Path(value) for value in declared.get(kind, [current])]
        if not paths or paths[0] != current:
            raise ValueError(f"journal_catalog.{kind} must start with current journal {current}")
        if len({str(path) for path in paths}) != len(paths):
            raise ValueError(f"journal_catalog.{kind} contains duplicate paths")
        result[kind] = paths
    return result


class ShadowRuntime:
    """Model-agnostic journal runtime. It has deliberately no execution client."""

    def __init__(
        self,
        config: dict[str, Any],
        adapters: dict[str, CityAdapter],
        *,
        config_path: Path | None = None,
        entrypoint_path: Path | None = None,
        decision_sink: DecisionSink | None = None,
    ):
        if config.get("execution_mode") != "zero_notional_shadow":
            raise ValueError("execution_mode must be zero_notional_shadow")
        if config.get("orders_submitted") != 0:
            raise ValueError("orders_submitted must be exactly zero")
        config_schema = config.get("schema_version")
        if config_schema not in {
            CONFIG_SCHEMA_VERSION,
            AUTHORITATIVE_CONFIG_SCHEMA_VERSION,
        }:
            raise ValueError(
                "config schema must be a supported city probability runtime schema, "
                f"got {config_schema}"
            )
        self.authoritative_decision_output = (
            config_schema == AUTHORITATIVE_CONFIG_SCHEMA_VERSION
        )
        if self.authoritative_decision_output:
            if config.get("framework_id") != FRAMEWORK_ID:
                raise ValueError(f"framework_id must be {FRAMEWORK_ID}")
            if config.get("strategy_family") != STRATEGY_FAMILY:
                raise ValueError(f"strategy_family must be {STRATEGY_FAMILY}")
        expected_output_schema = (
            AUTHORITATIVE_OUTPUT_SCHEMA_VERSION
            if self.authoritative_decision_output
            else OUTPUT_SCHEMA_VERSION
        )
        expected_output_fingerprint = (
            AUTHORITATIVE_OUTPUT_SCHEMA_FINGERPRINT
            if self.authoritative_decision_output
            else OUTPUT_SCHEMA_FINGERPRINT
        )
        expected_schema = config.get("output_schema_version")
        if expected_schema != expected_output_schema:
            raise ValueError(
                "output schema handshake failed: "
                f"expected {expected_output_schema}, got {expected_schema}"
            )
        expected_fingerprint = config.get("output_schema_fingerprint")
        if expected_fingerprint != expected_output_fingerprint:
            raise ValueError("output schema fingerprint handshake failed")
        self.config = config
        self.adapters = adapters
        self.output_dir = Path(config["output_dir"])
        self.journal_catalog = (
            {} if self.authoritative_decision_output else resolve_journal_catalog(config)
        )
        self.evaluations = self.output_dir / "evaluations.jsonl"
        self.intents = self.output_dir / "paper_intents.jsonl"
        self.checkpoints = self.output_dir / "checkpoints.jsonl"
        self.errors = self.output_dir / (
            "runtime_errors.jsonl" if self.authoritative_decision_output else "errors.jsonl"
        )
        self.decision_sink = decision_sink
        if self.authoritative_decision_output:
            if self.decision_sink is None:
                raise ValueError("authoritative runtime requires decision contract output")
            if self.decision_sink.output_dir != self.output_dir.resolve():
                raise ValueError("authoritative decision journal must equal runtime output_dir")
        self.upstream_producer_identity = self._validate_producer_contracts()
        self.runtime_identity = self._build_runtime_identity(config_path, entrypoint_path)

    def _validate_producer_contracts(self) -> dict[str, Any]:
        declarations = self.config.get("producer_contracts") or []
        if self.config.get("require_producer_contracts") and not declarations:
            raise RuntimeError("producer contract declarations are required")
        declared_journals = {str(Path(row["journal_path"]).resolve()) for row in declarations}
        if self.config.get("require_producer_contracts"):
            required = {
                str(Path(profile["source_journal"]).resolve())
                for profile in self.config.get("profiles", [])
                if profile.get("enabled", True) and profile.get("source_journal")
            }
            missing = sorted(required - declared_journals)
            if missing:
                raise RuntimeError(f"missing producer contract declaration: {missing}")
        identities: dict[str, Any] = {}
        for declaration in declarations:
            latest_path = Path(declaration["latest_path"])
            if not latest_path.is_file():
                raise RuntimeError(f"producer contract latest is missing: {latest_path}")
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
            expected_schema = declaration["schema_version"]
            expected_fingerprint = declaration["schema_fingerprint"]
            if payload.get("schema_version") != expected_schema:
                raise RuntimeError(f"producer schema handshake failed: {latest_path}")
            if payload.get("schema_fingerprint") != expected_fingerprint:
                raise RuntimeError(f"producer fingerprint handshake failed: {latest_path}")
            identity = payload.get("producer_identity")
            if not isinstance(identity, dict) or not identity.get("runtime_instance_id"):
                raise RuntimeError(f"producer runtime identity missing: {latest_path}")
            if identity.get("output_schema_version") != expected_schema:
                raise RuntimeError(f"producer identity schema mismatch: {latest_path}")
            if identity.get("output_schema_fingerprint") != expected_fingerprint:
                raise RuntimeError(f"producer identity fingerprint mismatch: {latest_path}")
            identities[str(Path(declaration["journal_path"]).resolve())] = identity
        return identities

    def _build_runtime_identity(
        self, config_path: Path | None, entrypoint_path: Path | None
    ) -> dict[str, Any]:
        module_hashes: dict[str, str] = {}
        module_names = {self.__class__.__module__, "weather_data_feed.input_catalog"}
        module_names.update(adapter.__class__.__module__ for adapter in self.adapters.values())
        if self.decision_sink is not None:
            module_names.add(self.decision_sink.__class__.__module__)
        for module_name in sorted(module_names):
            spec = importlib.util.find_spec(module_name)
            if spec is None or not spec.origin:
                raise RuntimeError(f"cannot resolve loaded module: {module_name}")
            path = Path(spec.origin).resolve()
            module_hashes[str(path)] = _sha256_file(path)
        if entrypoint_path is not None:
            resolved_entrypoint = entrypoint_path.resolve()
            module_hashes[str(resolved_entrypoint)] = _sha256_file(resolved_entrypoint)

        canonical_config = json.dumps(
            self.config, sort_keys=True, separators=(",", ":")
        ).encode()
        config_hash = hashlib.sha256(canonical_config).hexdigest()
        if config_path is not None:
            disk_config = json.loads(config_path.read_text(encoding="utf-8"))
            disk_hash = hashlib.sha256(
                json.dumps(disk_config, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if disk_hash != config_hash:
                raise RuntimeError("loaded config differs from config path contents")

        artifacts: dict[str, str] = {}
        for profile in self.config.get("profiles", []):
            for declaration in (profile.get("artifacts") or {}).values():
                for path_key, hash_key in (("path", "sha256"), ("spec_path", "spec_sha256")):
                    value = declaration.get(path_key)
                    if not value:
                        continue
                    path = Path(value).resolve()
                    if not path.is_file():
                        raise RuntimeError(f"declared artifact is missing: {path}")
                    actual = _sha256_file(path)
                    expected = declaration.get(hash_key)
                    if actual != expected:
                        raise RuntimeError(f"declared artifact hash mismatch: {path}")
                    artifacts[str(path)] = actual

        repo_head = _git_value("rev-parse", "HEAD")
        dirty = bool(_git_value("status", "--short", "--untracked-files=no"))
        identity_payload = {
            "framework_id": self.config.get("framework_id"),
            "strategy_family": self.config.get("strategy_family"),
            "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
            "repo_root": str(ROOT),
            "repo_head": repo_head,
            "repo_dirty_tracked": dirty,
            "config_path": str(config_path.resolve()) if config_path else None,
            "config_sha256": config_hash,
            "loaded_module_sha256": module_hashes,
            "artifact_sha256": artifacts,
            "upstream_producer_identity": self.upstream_producer_identity,
            "output_schema_version": self.output_schema_version,
            "output_schema_fingerprint": self.output_schema_fingerprint,
            "legacy_journals": (
                "deprecated_read_only"
                if self.authoritative_decision_output
                else "active_legacy_compatibility"
            ),
        }
        return {
            **identity_payload,
            "runtime_instance_id": hashlib.sha256(
                json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }

    def _contract_fields(self, record_kind: str) -> dict[str, Any]:
        return {
            "record_kind": record_kind,
            "schema_version": self.output_schema_version,
            "schema_fingerprint": self.output_schema_fingerprint,
            "runtime_identity": self.runtime_identity,
        }

    @staticmethod
    def fee_per_share(price: float) -> float:
        return 0.05 * price * (1.0 - price)

    @property
    def output_schema_version(self) -> str:
        return (
            AUTHORITATIVE_OUTPUT_SCHEMA_VERSION
            if self.authoritative_decision_output
            else OUTPUT_SCHEMA_VERSION
        )

    @property
    def output_schema_fingerprint(self) -> str:
        return (
            AUTHORITATIVE_OUTPUT_SCHEMA_FINGERPRINT
            if self.authoritative_decision_output
            else OUTPUT_SCHEMA_FINGERPRINT
        )

    def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        if self.authoritative_decision_output:
            assert self.decision_sink is not None
            seen = self.decision_sink.source_evaluation_ids
            first_intents = self.decision_sink.intent_dedupe_keys
            seen_checkpoints = self.decision_sink.checkpoint_ids
        else:
            seen = set().union(*(
                load_jsonl_keys(path, "evaluation_id")
                for path in self.journal_catalog["evaluations"]
            ))
            first_intents = set().union(*(
                load_jsonl_keys(path, "position_key")
                for path in self.journal_catalog["paper_intents"]
            ))
            seen_checkpoints = load_jsonl_keys(self.checkpoints, "checkpoint_id")
        evaluated = written = intents = errors = scored = not_scorable = blockers = 0
        decision_output = {
            "written_bundles": 0,
            "written_intents": 0,
            "written_blockers": 0,
            "written_intent_blockers": 0,
            "conversion_errors": 0,
        }

        def record_decision_output(result: Any) -> None:
            if result is None:
                return
            values = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            for key in decision_output:
                decision_output[key] += int(values.get(key, 0))
        for profile in self.config["profiles"]:
            if not profile.get("enabled", True):
                continue
            adapter_id = profile["adapter"]
            try:
                scores = self.adapters[adapter_id].score(profile, now)
            except InputNotReady as exc:
                blockers += 1
                checkpoint_key = "|".join((
                    exc.city,
                    exc.target_date,
                    adapter_id,
                    exc.reason,
                    json.dumps(exc.details, sort_keys=True, separators=(",", ":")),
                ))
                checkpoint_id = hashlib.sha256(checkpoint_key.encode()).hexdigest()
                if checkpoint_id not in seen_checkpoints:
                    blocker_row = {
                        **self._contract_fields("checkpoint_blocker"),
                        "checkpoint_id": checkpoint_id,
                        "incident_id": checkpoint_id,
                        "decision_ts_utc": exc.decision_ts_utc,
                        "city": exc.city,
                        "target_date": exc.target_date,
                        "adapter": adapter_id,
                        "checkpoint_status": "not_scorable",
                        "blocker_reason": exc.reason,
                        "details": exc.details,
                    }
                    if not self.authoritative_decision_output:
                        append_jsonl(self.checkpoints, blocker_row)
                    if self.decision_sink is not None:
                        record_decision_output(
                            self.decision_sink.record_checkpoint_blocker(blocker_row)
                        )
                    seen_checkpoints.add(checkpoint_id)
                continue
            except Exception as exc:  # persistent telemetry, never silent fallback
                errors += 1
                error_key = "|".join((
                    str(profile.get("city") or ""),
                    adapter_id,
                    type(exc).__name__,
                    str(exc),
                ))
                append_jsonl(self.errors, {
                    **self._contract_fields("error"),
                    "ts_utc": now.isoformat(),
                    "city": profile.get("city"),
                    "adapter": adapter_id,
                    "incident_id": hashlib.sha256(error_key.encode()).hexdigest(),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            profile_rows: list[tuple[dict[str, Any], str]] = []
            for score in scores:
                evaluated += 1
                if score.evaluation_status == "scored":
                    if score.market_probability is None or score.model_probability is None:
                        raise ValueError("scored evaluation requires market and model probabilities")
                    scored += 1
                elif score.evaluation_status == "not_scorable":
                    if not score.not_scorable_reason:
                        raise ValueError("not_scorable evaluation requires a reason")
                    not_scorable += 1
                else:
                    raise ValueError(f"unsupported evaluation_status: {score.evaluation_status}")
                key = "|".join((score.city, score.target_date, score.source_obs_ts_utc,
                                str(score.current_bracket), score.market_side, score.model_id))
                evaluation_id = hashlib.sha256(key.encode()).hexdigest()
                if evaluation_id in seen:
                    continue
                fee = (
                    self.fee_per_share(score.market_entry_price)
                    if score.market_entry_price is not None
                    else None
                )
                effective_cost = (
                    score.market_entry_price + fee
                    if score.market_entry_price is not None and fee is not None
                    else None
                )
                edge_threshold = float(profile.get("edge_threshold", 0.0))
                edge = (
                    score.model_probability - effective_cost
                    if score.evaluation_status == "scored"
                    and score.model_probability is not None
                    and effective_cost is not None
                    else None
                )
                would_enter = edge is not None and edge >= edge_threshold
                row = {
                    **self._contract_fields("evaluation"),
                    "execution_mode": "zero_notional_shadow",
                    "orders_submitted": 0,
                    "evaluation_id": evaluation_id,
                    **asdict(score),
                    "fee_per_share": fee,
                    "effective_cost_per_share": effective_cost,
                    "edge_after_fee": edge,
                    "edge_threshold": edge_threshold,
                    "would_enter": would_enter,
                }
                if not self.authoritative_decision_output:
                    append_jsonl(self.evaluations, row)
                if self.decision_sink is not None:
                    record_decision_output(self.decision_sink.record_evaluation(row))
                written += 1
                seen.add(evaluation_id)
                position_parts = [
                    score.city,
                    score.target_date,
                    str(score.current_bracket),
                ]
                position_scope = profile.get(
                    "position_scope", "city_date_bracket_side_model"
                )
                if position_scope == "city_date_bracket_side_model":
                    position_parts.append(score.market_side)
                elif position_scope == "city_date_model":
                    position_parts = [score.city, score.target_date]
                elif position_scope != "city_date_bracket_model":
                    raise ValueError(f"unsupported position_scope: {position_scope}")
                position_parts.append(score.model_id)
                position_key = "|".join(position_parts)
                profile_rows.append((row, position_key))
            # YES and NO are competing expressions of the same exact bracket.
            # Select the best net edge for each configured position before
            # journaling an intent; row order must never decide the side.
            best_by_position: dict[str, dict[str, Any]] = {}
            for row, position_key in profile_rows:
                if not row["would_enter"] or position_key in first_intents:
                    continue
                previous = best_by_position.get(position_key)
                if previous is None or float(row["edge_after_fee"]) > float(
                    previous["edge_after_fee"]
                ):
                    best_by_position[position_key] = row
            if profile.get("emit_paper_intents", True):
                for position_key, row in best_by_position.items():
                    intent_row = {
                        **row,
                        "record_kind": "paper_intent",
                        "position_key": position_key,
                        "intent_kind": "first_best_net_edge",
                        "notional_usd": 0.0,
                        "shares": 0.0,
                    }
                    if not self.authoritative_decision_output:
                        append_jsonl(self.intents, intent_row)
                    if self.decision_sink is not None:
                        record_decision_output(
                            self.decision_sink.record_paper_intent(intent_row)
                        )
                    intents += 1
                    first_intents.add(position_key)
        summary = {
            **self._contract_fields("summary"),
            "framework_id": self.config.get("framework_id"),
            "strategy_family": self.config.get("strategy_family"),
            "execution_mode": "zero_notional_shadow",
            "orders_submitted": 0,
            "generated_at_utc": now.isoformat(),
            "evaluated": evaluated,
            "scored": scored,
            "not_scorable": not_scorable,
            "checkpoint_blockers": blockers,
            "new_evaluations": written,
            "new_paper_intents": intents,
            "errors": errors,
            "decision_contract_output": decision_output,
            "legacy_journals": (
                "deprecated_read_only"
                if self.authoritative_decision_output
                else "active_legacy_compatibility"
            ),
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "latest_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary
