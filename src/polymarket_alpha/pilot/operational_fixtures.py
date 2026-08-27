"""OP-01 frozen Gamma fixture loader for the read-only pilot preflight.

This module is deliberately an offline-only seam.  It reads a small,
allowlisted fixture set from disk and reuses the P0-03 Gamma normalizer; it
does not import a transport, a collector, or a repository.  The fixtures are
synthetic examples shaped like Gamma responses, not evidence of a live market
or an authorization to call Gamma.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..adapters.gamma_normalize import NormalizedMarket, normalize_market_payload, scan_unknown_fields
from ..contracts import MarketIdentity, bytes_sha256, content_sha256
from ..contracts.base import ensure_utc


class FixtureReceiptCode(StrEnum):
    ACCEPTED = "ACCEPTED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    FIXTURE_INVALID = "FIXTURE_INVALID"


@dataclass(frozen=True)
class FixtureReceipt:
    """A typed, non-persistent OP-01 preflight result for one fixture."""

    fixture_id: str
    code: FixtureReceiptCode
    detail: str
    fixture_sha256: str | None = None
    raw_bytes_sha256: str | None = None
    unknown_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrozenFixture:
    fixture_id: str
    fixture_sha256: str
    raw_bytes_sha256: str
    normalized: NormalizedMarket
    identity_sha256: str


@dataclass(frozen=True)
class FrozenFixtureSet:
    """Normalized fixture-only input to the eventual authorized pilot.

    ``receipts`` is always complete: an identity or JSON failure cannot be
    silently dropped, while schema drift remains explicitly typed.
    """

    fixture_set_id: str
    fixture_set_sha256: str
    manifest_raw_bytes_sha256: str
    fixtures: tuple[FrozenFixture, ...]
    receipts: tuple[FixtureReceipt, ...]


def _load_json(path: Path) -> tuple[Mapping[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON fixture {path.name}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON fixture {path.name} must be an object")
    return value, bytes_sha256(raw)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value.strip()


def _expected_identity(value: Any) -> MarketIdentity:
    if not isinstance(value, Mapping):
        raise ValueError("expected_identity must be an object")
    try:
        return MarketIdentity(**value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected_identity is invalid: {exc}") from exc


def _allowlisted_fixture_path(root: Path, filename: str) -> Path:
    if Path(filename).name != filename or not filename.endswith(".json"):
        raise ValueError("fixture filename must be a simple .json basename")
    path = (root / filename).resolve()
    if path.parent != root.resolve():
        raise ValueError("fixture filename escapes the allowlisted fixture root")
    return path


def _identity_sha256(identity: MarketIdentity) -> str:
    return content_sha256(identity.model_dump(mode="json", exclude_none=False))


def load_frozen_fixture_set(
    fixture_root: Path,
    *,
    source_observed_at: datetime,
    network_provider: Callable[[], object] | None = None,
) -> FrozenFixtureSet:
    """Load exactly the local manifest allowlist and normalize its markets.

    ``network_provider`` is an adversarial test sentinel only.  It is never
    invoked: keeping it in the signature proves this path does not fall back
    to a provider when a fixture is malformed or drifts.  Any caller needing
    a provider is outside OP-01 and must use the separately authorized
    transport path.
    """

    del network_provider
    observed_at = ensure_utc(source_observed_at)
    root = fixture_root.resolve()
    manifest, manifest_raw_bytes_sha256 = _load_json(root / "manifest.json")
    if manifest.get("source_kind") != "SYNTHETIC_FROZEN_OFFLINE":
        raise ValueError("fixture manifest must declare SYNTHETIC_FROZEN_OFFLINE")
    if manifest.get("live_capture") is not False:
        raise ValueError("fixture manifest must explicitly declare live_capture=false")
    fixture_set_id = _text(manifest.get("fixture_set_id"), "fixture_set_id")
    entries = manifest.get("fixtures")
    if not isinstance(entries, list) or not 3 <= len(entries) <= 5:
        raise ValueError("manifest must allowlist between three and five fixtures")

    fixtures: list[FrozenFixture] = []
    receipts: list[FixtureReceipt] = []
    seen_ids: set[str] = set()
    seen_markets: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("fixture allowlist entry must be an object")
        fixture_id = _text(entry.get("fixture_id"), "fixture_id")
        if fixture_id in seen_ids:
            raise ValueError(f"duplicate fixture_id {fixture_id!r}")
        seen_ids.add(fixture_id)
        try:
            path = _allowlisted_fixture_path(root, _text(entry.get("filename"), "filename"))
            payload, raw_bytes_sha256 = _load_json(path)
            fixture_sha256 = content_sha256(payload)
            declared_hash = _text(entry.get("payload_sha256"), "payload_sha256")
            if fixture_sha256 != declared_hash:
                raise ValueError("payload_sha256 does not match fixture content")
            declared_raw_hash = _text(entry.get("raw_bytes_sha256"), "raw_bytes_sha256")
            if raw_bytes_sha256 != declared_raw_hash:
                raise ValueError("raw_bytes_sha256 does not match fixture artifact bytes")
            normalized = normalize_market_payload(payload, source_observed_at=observed_at)
            expected = _expected_identity(entry.get("expected_identity"))
            if normalized.identity != expected:
                receipts.append(
                    FixtureReceipt(
                        fixture_id=fixture_id,
                        code=FixtureReceiptCode.IDENTITY_MISMATCH,
                        detail="normalized canonical identity differs from manifest expectation",
                        fixture_sha256=fixture_sha256,
                        raw_bytes_sha256=raw_bytes_sha256,
                    )
                )
                continue
            if normalized.identity.market_id in seen_markets:
                raise ValueError(f"duplicate canonical market_id {normalized.identity.market_id!r}")
            seen_markets.add(normalized.identity.market_id)
            unknown_fields = scan_unknown_fields(payload)
            if unknown_fields:
                receipts.append(
                    FixtureReceipt(
                        fixture_id=fixture_id,
                        code=FixtureReceiptCode.SCHEMA_DRIFT,
                        detail="unknown top-level Gamma fields retained in fixture payload",
                        fixture_sha256=fixture_sha256,
                        raw_bytes_sha256=raw_bytes_sha256,
                        unknown_fields=unknown_fields,
                    )
                )
                continue
            receipts.append(
                FixtureReceipt(
                    fixture_id=fixture_id,
                    code=FixtureReceiptCode.ACCEPTED,
                    detail="offline fixture normalized and matched manifest identity",
                    fixture_sha256=fixture_sha256,
                    raw_bytes_sha256=raw_bytes_sha256,
                )
            )
            fixtures.append(
                FrozenFixture(
                    fixture_id=fixture_id,
                    fixture_sha256=fixture_sha256,
                    raw_bytes_sha256=raw_bytes_sha256,
                    normalized=normalized,
                    identity_sha256=_identity_sha256(normalized.identity),
                )
            )
        except ValueError as exc:
            receipts.append(
                FixtureReceipt(
                    fixture_id=fixture_id,
                    code=FixtureReceiptCode.FIXTURE_INVALID,
                    detail=str(exc),
                )
            )

    # The manifest is part of the frozen input identity.  Entries are ordered
    # intentionally, so adding/removing an allowlisted market changes it.
    return FrozenFixtureSet(
        fixture_set_id=fixture_set_id,
        fixture_set_sha256=content_sha256(manifest),
        manifest_raw_bytes_sha256=manifest_raw_bytes_sha256,
        fixtures=tuple(fixtures),
        receipts=tuple(receipts),
    )
