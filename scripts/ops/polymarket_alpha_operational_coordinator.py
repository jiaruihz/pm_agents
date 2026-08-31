#!/usr/bin/env python
"""Explicit-stage CLI for the Alpha operational coordinator (GLM-OP-04).

Every invocation names exactly one stage, a temporary artifact root and a
temporary Alpha SQLite database, plus caller-supplied manifest/result files.
The CLI refuses production locations (``/Volumes/jrs``, canonical runtime
databases), any proxy environment variable, and defines no command hook of
any kind.  It runs offline only.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.polymarket_alpha.decision import RankConfig  # noqa: E402
from src.polymarket_alpha.operational import (  # noqa: E402
    BlindResumeStageInputs,
    BlindStageInputs,
    BookStageInputs,
    CoordinatorBlocked,
    GammaIngestStageInputs,
    GammaResponseReceipt,
    MarketResumeStageInputs,
    OwnerBookLegSubmission,
    ScanStageInputs,
    run_blind_resume_stage,
    run_blind_stage,
    run_book_stage,
    run_gamma_ingest_stage,
    run_market_resume_stage,
    run_scan_stage,
)
from src.polymarket_alpha.rules.models import RuleCompilationRequest  # noqa: E402
from src.polymarket_alpha.storage import AlphaRepository  # noqa: E402


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
FORBIDDEN_PATH_MARKERS = ("/Volumes/jrs", "runtime/weather.db", "research.db")
ALLOWED_ROOT_PREFIXES = (
    "/tmp/polymarket-alpha-pilot/",
    "/private/tmp/polymarket-alpha-pilot/",
)


def _allowed_pilot_root(path: Path) -> Path | None:
    for raw in ALLOWED_ROOT_PREFIXES:
        try:
            root = Path(raw.rstrip("/")).resolve(strict=True)
        except FileNotFoundError:
            continue
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return root
    return None


def _reject_symlink_components(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            if current == Path("/tmp") and current.resolve() == Path("/private/tmp"):
                continue
            raise SystemExit(f"{label} must not contain symlink components: {current}")


def _canonical_pilot_path(value: str, *, label: str, must_exist: bool) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{label} must be an absolute path")
    if any(marker in str(path) for marker in FORBIDDEN_PATH_MARKERS):
        raise SystemExit(f"{label} must not reference production locations: {path}")
    _reject_symlink_components(path, label=label)
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as error:
        raise SystemExit(f"{label} cannot be resolved safely: {error}") from error
    if _allowed_pilot_root(resolved) is None:
        raise SystemExit(
            f"{label} must live under {' or '.join(ALLOWED_ROOT_PREFIXES)}: {resolved}"
        )
    if any(marker in str(resolved) for marker in FORBIDDEN_PATH_MARKERS):
        raise SystemExit(f"{label} must not reference production locations: {resolved}")
    return resolved


def refuse_unsafe_environment() -> None:
    active = [key for key in PROXY_ENV_KEYS if os.environ.get(key)]
    if active:
        raise SystemExit(
            f"refusing to run with proxy environment variables set: {', '.join(active)}"
        )


def require_pilot_path(value: str, *, label: str, must_exist: bool) -> Path:
    path = _canonical_pilot_path(value, label=label, must_exist=must_exist)
    if must_exist and not path.is_dir():
        raise SystemExit(f"{label} must be an existing directory: {path}")
    root = _allowed_pilot_root(path)
    if root is None or path.parent != root:
        raise SystemExit(f"{label} must be a direct child of the fixed pilot root: {path}")
    return path


def _require_alpha_db_path(value: str, *, artifact_root: Path) -> Path:
    path = _canonical_pilot_path(value, label="--alpha-db", must_exist=False)
    if path.parent != artifact_root:
        raise SystemExit("--alpha-db must be a direct child of --artifact-root")
    if path.exists():
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SystemExit("--alpha-db must be absent or an existing non-symlink regular file")
    return path


def _require_readable_input_path(value: str, *, label: str) -> Path:
    """Return one canonical, non-symlink input inside the fixed pilot root."""

    path = _canonical_pilot_path(str(value), label=label, must_exist=True)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"{label} must be an existing non-symlink regular file: {path}")
    return path


def _read_input_bytes(value: str, *, label: str) -> bytes:
    """Read through an allowlisted dirfd chain without following symlinks."""

    path = _require_readable_input_path(value, label=label)
    root = _allowed_pilot_root(path)
    if root is None:
        raise SystemExit(f"{label} is outside the pilot root")
    relative = path.relative_to(root)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(root, directory_flags)
    file_fd = -1
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        file_fd = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current_fd,
        )
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise SystemExit(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1 << 20)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as error:
        raise SystemExit(f"cannot read {label} safely: {error}") from error
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(current_fd)


def _parse_datetime(value: Any, field: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit(f"{field} must be a timezone-aware ISO timestamp")
    return parsed


def _json_file(path: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(_read_input_bytes(path, label="--manifest").decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read manifest {path}: {error}") from error
    if not isinstance(payload, Mapping):
        raise SystemExit(f"manifest {path} must be a JSON object")
    return payload


def _source_contents(entries: Mapping[str, str]) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    for name, path in dict(entries).items():
        try:
            contents[str(name)] = _read_input_bytes(
                str(path), label=f"source artifact {name}"
            )
        except OSError as error:
            raise SystemExit(f"cannot read source artifact {name}={path}: {error}") from error
    return contents


def _repository(alpha_db: Path) -> AlphaRepository:
    repository = AlphaRepository(alpha_db)
    repository.migrate()
    return repository


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str))


def _stage_ingest_gamma(args: argparse.Namespace) -> None:
    manifest = _json_file(args.manifest)
    try:
        raw = _read_input_bytes(
            str(manifest["response_path"]), label="captured response"
        )
    except OSError as error:
        raise SystemExit(f"cannot read captured response: {error}") from error
    receipt_payload = manifest["receipt"]
    receipt = GammaResponseReceipt(
        request_method=str(receipt_payload["request_method"]),
        endpoint_host=str(receipt_payload["endpoint_host"]),
        endpoint_path=str(receipt_payload["endpoint_path"]),
        http_status=int(receipt_payload["http_status"]),
        response_bytes_sha256=str(receipt_payload["response_bytes_sha256"]),
        response_byte_length=int(receipt_payload["response_byte_length"]),
        response_received_at=_parse_datetime(
            receipt_payload["response_received_at"], "receipt.response_received_at"
        ),
    )
    inputs = GammaIngestStageInputs(
        raw_response=raw,
        response_receipt=receipt,
        run_id=str(manifest["run_id"]),
        observed_at=_parse_datetime(manifest["observed_at"], "observed_at"),
        ingested_at=_parse_datetime(manifest["ingested_at"], "ingested_at"),
        page_budget=int(manifest["page_budget"]),
        requested_offset=int(manifest.get("requested_offset") or 0),
        requested_limit=(
            int(manifest["requested_limit"]) if manifest.get("requested_limit") is not None else None
        ),
    )
    result = run_gamma_ingest_stage(_repository(args.alpha_db), args.artifact_root, inputs)
    catalog = result.ingest.catalog
    _emit(
        {
            "stage": result.state.stage.value if result.state else None,
            "state_locator": result.state.locator() if result.state else None,
            "snapshot_ids": list(result.state.snapshot_ids) if result.state else [],
            "flattened_markets": result.ingest.flattened_market_count,
            "catalog_error_receipts": len(catalog.error_receipts) if catalog else None,
            "catalog_drift_receipts": len(catalog.drift_receipts) if catalog else None,
        }
    )


def _stage_scan(args: argparse.Namespace) -> None:
    manifest = _json_file(args.manifest)
    inputs = ScanStageInputs(
        run_id=str(manifest["run_id"]),
        as_of=_parse_datetime(manifest["as_of"], "as_of"),
    )
    result = run_scan_stage(_repository(args.alpha_db), args.artifact_root, inputs)
    _emit(
        {
            "stage": result.state.stage.value,
            "state_locator": result.state.locator(),
            "candidate_id": result.candidate.record_id,
            "hit_ids": list(result.state.hit_ids),
        }
    )


def _stage_blind(args: argparse.Namespace) -> None:
    manifest = _json_file(args.manifest)
    try:
        rule_request = RuleCompilationRequest.model_validate(manifest["rule_request"])
    except ValueError as error:
        raise SystemExit(f"rule request manifest is invalid: {error}") from error
    inputs = BlindStageInputs(
        rule_request=rule_request,
        gate_evaluated_at=_parse_datetime(manifest["gate_evaluated_at"], "gate_evaluated_at"),
        packet_created_at=_parse_datetime(manifest["packet_created_at"], "packet_created_at"),
        handoff_created_at=_parse_datetime(manifest["handoff_created_at"], "handoff_created_at"),
    )
    result = run_blind_stage(_repository(args.alpha_db), args.artifact_root, inputs)
    _emit(
        {
            "stage": result.state.stage.value,
            "state_locator": result.state.locator(),
            "blind_packet_id": result.blind.blind_packet.record_id,
            "packet_locator": result.blind.packet_manifest.packet_locator,
        }
    )


def _stage_blind_resume(args: argparse.Namespace) -> None:
    manifest = _json_file(args.manifest)
    inputs = BlindResumeStageInputs(
        result_locator=str(manifest["result_locator"]),
        receipt_locator=str(manifest["receipt_locator"]),
        source_contents=_source_contents(manifest.get("source_contents") or {}),
        imported_at=_parse_datetime(manifest["imported_at"], "imported_at"),
        demand_requested_at=_parse_datetime(
            manifest["demand_requested_at"], "demand_requested_at"
        ),
        demand_valid_until=_parse_datetime(
            manifest["demand_valid_until"], "demand_valid_until"
        ),
        max_staleness_seconds=int(manifest["max_staleness_seconds"]),
        target_sizes=tuple(Decimal(str(item)) for item in manifest["target_sizes"]),
    )
    result = run_blind_resume_stage(_repository(args.alpha_db), args.artifact_root, inputs)
    _emit(
        {
            "stage": result.state.stage.value if result.state else None,
            "accepted": result.outcome.accepted is not None,
            "demand_id": result.state.demand_id if result.state else None,
            "outbox_bundle_id": result.outbox_receipt.bundle_id if result.outbox_receipt else None,
        }
    )


def _leg_submission(payload: Mapping[str, Any]) -> OwnerBookLegSubmission:
    try:
        raw = _read_input_bytes(
            str(payload["raw_book_path"]), label="owner raw book bytes"
        )
    except OSError as error:
        raise SystemExit(f"cannot read owner raw book bytes: {error}") from error
    return OwnerBookLegSubmission(
        locator=str(payload["locator"]),
        raw_book_bytes=raw,
        capture=dict(payload["capture"]),
        raw_artifact_id=str(payload["raw_artifact_id"]),
    )


def _stage_book(args: argparse.Namespace) -> None:
    manifest = _json_file(args.manifest)
    inputs = BookStageInputs(
        owner_receipt=dict(manifest["owner_receipt"]),
        yes_submission=_leg_submission(manifest["yes_leg"]),
        no_submission=_leg_submission(manifest["no_leg"]),
        received_at=_parse_datetime(manifest["received_at"], "received_at"),
        packet_created_at=_parse_datetime(manifest["packet_created_at"], "packet_created_at"),
        handoff_created_at=_parse_datetime(manifest["handoff_created_at"], "handoff_created_at"),
    )
    result = run_book_stage(_repository(args.alpha_db), args.artifact_root, inputs)
    _emit(
        {
            "stage": result.state.stage.value if result.state else None,
            "bridge_accepted": result.bridge.accepted,
            "bridge_failure": (
                {"code": result.bridge.failure.code.value, "detail": result.bridge.failure.detail}
                if result.bridge.failure is not None
                else None
            ),
            "market_packet_id": result.state.market_packet_id if result.state else None,
        }
    )


def _stage_market_resume(args: argparse.Namespace) -> None:
    manifest = _json_file(args.manifest)
    rank_payload = manifest["rank_config"]
    rank_config = RankConfig(
        version=str(rank_payload["version"]),
        watch_threshold=Decimal(str(rank_payload["watch_threshold"])),
        simulate_threshold=Decimal(str(rank_payload["simulate_threshold"])),
        fee_slippage_cost_policy_id=str(
            rank_payload["fee_slippage_cost_policy_id"]
        ),
        fee_slippage_cost_policy_version=str(
            rank_payload["fee_slippage_cost_policy_version"]
        ),
        fee_rate=Decimal(str(rank_payload["fee_rate"])),
        slippage_buffer=Decimal(str(rank_payload["slippage_buffer"])),
    )
    inputs = MarketResumeStageInputs(
        result_locator=str(manifest["result_locator"]),
        receipt_locator=str(manifest["receipt_locator"]),
        source_contents=_source_contents(manifest.get("source_contents") or {}),
        imported_at=_parse_datetime(manifest["imported_at"], "imported_at"),
        gate_b_evaluated_at=_parse_datetime(
            manifest["gate_b_evaluated_at"], "gate_b_evaluated_at"
        ),
        decision_as_of=_parse_datetime(manifest["decision_as_of"], "decision_as_of"),
        rank_config=rank_config,
        rule_risk_reasons=tuple(str(item) for item in manifest.get("rule_risk_reasons") or ()),
    )
    result = run_market_resume_stage(_repository(args.alpha_db), args.artifact_root, inputs)
    final = result.outcome.final
    _emit(
        {
            "stage": result.state.stage.value if result.state else None,
            "gate_b_decision": final.gate_b.decision if final is not None else None,
            "decision_id": result.state.decision_id if result.state else None,
            "prediction_id": result.state.prediction_id if result.state else None,
            "execution": (
                final.ranked.decision.execution
                if final is not None and final.ranked is not None
                else None
            ),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root",
        required=True,
        help="temporary pilot artifact root (must live under /tmp/polymarket-alpha-pilot/)",
    )
    parser.add_argument(
        "--alpha-db",
        required=True,
        help="temporary Alpha SQLite DB (must live under /tmp/polymarket-alpha-pilot/)",
    )
    subparsers = parser.add_subparsers(dest="stage", required=True)
    for name, handler, manifest_help in (
        ("ingest-gamma", _stage_ingest_gamma, "captured response path + sealed receipt manifest"),
        ("scan", _stage_scan, "scan run/as_of manifest"),
        ("blind", _stage_blind, "rule compilation request + stage clocks"),
        ("blind-resume", _stage_blind_resume, "Blind result locators + demand clocks"),
        ("book", _stage_book, "owner receipt + paired leg submissions"),
        ("market-resume", _stage_market_resume, "Market result locators + rank config"),
    ):
        sub = subparsers.add_parser(name, help=manifest_help)
        sub.add_argument("--manifest", required=True, help="JSON manifest path")
        sub.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    refuse_unsafe_environment()
    args = build_parser().parse_args(argv)
    artifact_root = require_pilot_path(args.artifact_root, label="--artifact-root", must_exist=True)
    alpha_db_path = _require_alpha_db_path(args.alpha_db, artifact_root=artifact_root)
    args.artifact_root = artifact_root
    args.alpha_db = alpha_db_path
    try:
        args.handler(args)
    except CoordinatorBlocked as error:
        print(f"coordinator blocked: {error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
