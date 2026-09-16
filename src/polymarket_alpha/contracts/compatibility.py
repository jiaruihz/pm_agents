"""Explicit reader/writer compatibility policy for Alpha P0 contracts."""

from __future__ import annotations

from enum import StrEnum
import re

from .base import ALPHA_CONTRACT_VERSION


class CompatibilityDisposition(StrEnum):
    READ_WRITE = "READ_WRITE"
    REJECT_MALFORMED = "REJECT_MALFORMED"
    REJECT_UNKNOWN_MAJOR = "REJECT_UNKNOWN_MAJOR"
    REJECT_UNRELEASED_MINOR = "REJECT_UNRELEASED_MINOR"


_VERSION_RE = re.compile(r"^alpha_p0_v(?P<major>\d+)\.(?P<minor>\d+)$")
SUPPORTED_SCHEMA_VERSIONS = (ALPHA_CONTRACT_VERSION,)


def compatibility_disposition(schema_version: str) -> CompatibilityDisposition:
    match = _VERSION_RE.fullmatch(schema_version)
    if match is None:
        return CompatibilityDisposition.REJECT_MALFORMED
    current = _VERSION_RE.fullmatch(ALPHA_CONTRACT_VERSION)
    assert current is not None
    if match.group("major") != current.group("major"):
        return CompatibilityDisposition.REJECT_UNKNOWN_MAJOR
    if schema_version != ALPHA_CONTRACT_VERSION:
        return CompatibilityDisposition.REJECT_UNRELEASED_MINOR
    return CompatibilityDisposition.READ_WRITE


def require_supported_schema(schema_version: str) -> None:
    disposition = compatibility_disposition(schema_version)
    if disposition != CompatibilityDisposition.READ_WRITE:
        raise ValueError(f"unsupported schema version {schema_version!r}: {disposition.value}")
