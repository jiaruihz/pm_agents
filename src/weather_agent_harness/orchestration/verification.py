"""Immutable evidence capture and trusted, machine-executable verification."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from ..evidence import EvidenceStore
from .contracts import (
    AssertionOperator,
    EvidenceRecord,
    JsonAssertion,
    VerifierKind,
    VerifierResult,
    VerifierSpec,
    WorkOrder,
    WorkResult,
)


SNAPSHOT_MAX_BYTES = 10 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_reference(store: EvidenceStore, reference: str) -> Path:
    candidate = Path(reference)
    choices = (
        candidate if candidate.is_absolute() else store.run_dir / candidate,
        store.artifacts_dir / candidate,
    )
    for choice in choices:
        resolved = choice.resolve()
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(reference)


def capture_evidence(
    store: EvidenceStore,
    *,
    reference: str,
    work_order_id: str,
    attempt: int,
    snapshot_max_bytes: int = SNAPSHOT_MAX_BYTES,
) -> EvidenceRecord:
    source = _resolve_reference(store, reference)
    before = source.stat()
    source_hash = _sha256(source)
    snapshot_uri: str | None = None
    if before.st_size <= snapshot_max_bytes:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source.name) or "evidence"
        snapshot = store.artifact_path(
            f"evidence_snapshots/{work_order_id}/{attempt}/{source_hash}-{safe_name}"
        )
        if source != snapshot:
            shutil.copyfile(source, snapshot)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"evidence changed while being captured: {source}")
        if _sha256(snapshot) != source_hash:
            raise RuntimeError(f"evidence snapshot hash mismatch: {source}")
        snapshot_uri = str(snapshot)
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return EvidenceRecord(
        uri=str(source),
        snapshot_uri=snapshot_uri,
        sha256=source_hash,
        size_bytes=before.st_size,
        media_type=media_type,
        producer_work_order_id=work_order_id,
        producer_attempt=attempt,
    )


def capture_result_evidence(
    store: EvidenceStore, order: WorkOrder, result: WorkResult
) -> tuple[EvidenceRecord, ...]:
    return tuple(
        capture_evidence(
            store,
            reference=reference,
            work_order_id=order.work_order_id,
            attempt=order.attempt,
        )
        for reference in dict.fromkeys(result.evidence_refs)
    )


def verify_evidence_record(record: EvidenceRecord) -> tuple[bool, str]:
    path = Path(record.snapshot_uri or record.uri)
    if not path.is_file():
        return False, f"evidence missing: {path}"
    size = path.stat().st_size
    if size != record.size_bytes:
        return False, f"evidence size changed: {path}"
    actual = _sha256(path)
    if actual != record.sha256:
        return False, f"evidence hash changed: {path}"
    return True, f"evidence hash verified: {path}"


def _record_for(reference: str, records: tuple[EvidenceRecord, ...]) -> EvidenceRecord:
    matches = [
        item
        for item in records
        if reference in {item.uri, item.snapshot_uri}
        or reference == Path(item.uri).name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"evidence_ref must resolve to exactly one captured record: {reference}"
        )
    return matches[0]


def _json_value(payload: Any, path: str) -> tuple[bool, Any]:
    value = payload
    for token in path[2:].split("."):
        match = re.fullmatch(r"([^\[]+)(?:\[(\d+)\])?", token)
        if not match or not isinstance(value, dict) or match.group(1) not in value:
            return False, None
        value = value[match.group(1)]
        if match.group(2) is not None:
            if not isinstance(value, list):
                return False, None
            index = int(match.group(2))
            if index >= len(value):
                return False, None
            value = value[index]
    return True, value


def _assert_json(payload: Any, assertion: JsonAssertion) -> tuple[bool, str]:
    exists, value = _json_value(payload, assertion.path)
    if assertion.operator == AssertionOperator.EXISTS:
        passed = exists
    elif assertion.operator == AssertionOperator.TRUTHY:
        passed = exists and bool(value)
    elif assertion.operator == AssertionOperator.FALSEY:
        passed = exists and not bool(value)
    elif assertion.operator == AssertionOperator.NOT_EQUALS:
        passed = exists and value != assertion.expected
    else:
        passed = exists and value == assertion.expected
    return passed, (
        f"{assertion.path} {assertion.operator.value}: "
        f"actual={value!r} expected={assertion.expected!r}"
    )


class TrustedVerifierRunner:
    def __init__(self, *, repo_root: Path, store: EvidenceStore) -> None:
        self.repo_root = repo_root.resolve()
        self.store = store

    def run(
        self,
        order: WorkOrder,
        result: WorkResult,
        spec: VerifierSpec,
    ) -> VerifierResult:
        if spec.kind == VerifierKind.COMMAND:
            return self._command(order, spec)
        record = _record_for(spec.evidence_ref or "", result.evidence_records)
        intact, summary = verify_evidence_record(record)
        if not intact or spec.kind == VerifierKind.EVIDENCE_HASH:
            return VerifierResult(
                work_order_id=order.work_order_id,
                attempt=order.attempt,
                acceptance_id=spec.acceptance_id,
                kind=spec.kind,
                passed=intact,
                summary=summary,
                evidence_records=(record,),
            )
        source = Path(record.snapshot_uri or record.uri)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return VerifierResult(
                work_order_id=order.work_order_id,
                attempt=order.attempt,
                acceptance_id=spec.acceptance_id,
                kind=spec.kind,
                passed=False,
                summary=f"JSON evidence is not readable: {exc}",
                evidence_records=(record,),
            )
        checks = [_assert_json(payload, item) for item in spec.assertions]
        return VerifierResult(
            work_order_id=order.work_order_id,
            attempt=order.attempt,
            acceptance_id=spec.acceptance_id,
            kind=spec.kind,
            passed=all(item[0] for item in checks),
            summary=(
                "all JSON assertions passed"
                if all(item[0] for item in checks)
                else "one or more JSON assertions failed"
            ),
            evidence_records=(record,),
            details={"assertions": [item[1] for item in checks]},
        )

    def _command(self, order: WorkOrder, spec: VerifierSpec) -> VerifierResult:
        argv = list(spec.argv)
        executable = Path(argv[0])
        if not executable.is_absolute():
            executable = (self.repo_root / executable).resolve()
        if not executable.is_file():
            raise ValueError(f"verifier executable does not exist: {executable}")
        argv[0] = str(executable)
        proc = subprocess.run(
            argv,
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            timeout=spec.timeout_seconds,
            check=False,
        )
        prefix = f"verifiers/{order.work_order_id}/{order.attempt}/{spec.acceptance_id}"
        stdout = self.store.artifact_path(f"{prefix}.stdout.txt")
        stderr = self.store.artifact_path(f"{prefix}.stderr.txt")
        stdout.write_text(proc.stdout or "", encoding="utf-8")
        stderr.write_text(proc.stderr or "", encoding="utf-8")
        output_records = tuple(
            capture_evidence(
                self.store,
                reference=str(path),
                work_order_id=order.work_order_id,
                attempt=order.attempt,
            )
            for path in (stdout, stderr)
        )
        return VerifierResult(
            work_order_id=order.work_order_id,
            attempt=order.attempt,
            acceptance_id=spec.acceptance_id,
            kind=spec.kind,
            passed=proc.returncode == spec.expected_exit_code,
            summary=(
                f"verifier command exited {proc.returncode}; "
                f"expected {spec.expected_exit_code}"
            ),
            evidence_records=output_records,
            output_refs=(str(stdout), str(stderr)),
            details={"argv": argv, "returncode": proc.returncode},
        )


__all__ = [
    "SNAPSHOT_MAX_BYTES",
    "TrustedVerifierRunner",
    "capture_evidence",
    "capture_result_evidence",
    "verify_evidence_record",
]
