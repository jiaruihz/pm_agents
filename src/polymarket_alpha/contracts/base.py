"""Canonical primitives shared by Polymarket Alpha P0 contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ALPHA_CONTRACT_VERSION = "alpha_p0_v1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Normalize an aware timestamp to UTC and reject ambiguous naive values."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def canonical_decimal(value: Decimal) -> str:
    """Render numerically equivalent decimals with identical bytes."""

    if not value.is_finite():
        raise ValueError("decimal must be finite")
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    rendered = format(normalized, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def canonical_datetime(value: datetime) -> str:
    value = ensure_utc(value)
    if value.microsecond:
        main = value.strftime("%Y-%m-%dT%H:%M:%S")
        fraction = f"{value.microsecond:06d}".rstrip("0")
        return f"{main}.{fraction}Z"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, datetime):
        return canonical_datetime(value)
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not canonical")
        raise TypeError("float is forbidden in canonical contracts; use Decimal")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mapping keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in result:
                raise ValueError("mapping keys collide after Unicode normalization")
            result[normalized_key] = _canonicalize(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item) for item in value]
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_data(value: Any) -> Any:
    """Return JSON-compatible canonical data or fail closed."""

    return _canonicalize(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_data(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_record_id(record_type: str, *identity_parts: Any) -> str:
    """Build a deterministic, namespaced id from canonical identity parts."""

    record_type = record_type.strip().lower().replace(" ", "_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,47}", record_type):
        raise ValueError("record_type must be a bounded snake_case identifier")
    digest = content_sha256({"record_type": record_type, "identity": identity_parts})
    return f"{record_type}:{digest}"


def validate_sha256(value: str) -> str:
    value = value.strip().lower()
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("value must be a lowercase SHA-256 hex digest")
    return value


def normalize_rule_text(value: str) -> str:
    """Apply only non-semantic Unicode/newline/whitespace stabilization."""

    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    compact: list[str] = []
    for line in lines:
        if line or not compact or compact[-1]:
            compact.append(line)
    return "\n".join(compact)


def rule_sha256(raw_rule: str) -> str:
    return hashlib.sha256(normalize_rule_text(raw_rule).encode("utf-8")).hexdigest()


class AlphaContract(BaseModel):
    """Strict immutable base for every versioned Alpha contract."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        str_strip_whitespace=False,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_float_inputs(cls, value: Any) -> Any:
        def walk(item: Any, path: str) -> None:
            if isinstance(item, float):
                raise ValueError(f"float is forbidden at {path}; use Decimal or a decimal string")
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    walk(nested, f"{path}.{key}")
            elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                for index, nested in enumerate(item):
                    walk(nested, f"{path}[{index}]")

        if not isinstance(value, BaseModel):
            walk(value, "$")
        return value


class ProvenanceRef(AlphaContract):
    source_artifact_id: str
    relation: str
    content_sha256: str | None = None
    source_observed_at: datetime | None = None

    @field_validator("source_artifact_id", "relation")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("content_sha256")
    @classmethod
    def sha_is_valid(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("source_observed_at")
    @classmethod
    def observed_at_is_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value is not None else None


class CommonEnvelope(AlphaContract):
    schema_version: str = ALPHA_CONTRACT_VERSION
    record_id: str
    run_id: str
    created_at: datetime = Field(default_factory=utc_now)
    source: str
    source_version: str
    provenance: tuple[ProvenanceRef, ...] = ()
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: str) -> str:
        if value != ALPHA_CONTRACT_VERSION:
            raise ValueError(f"unsupported schema_version: {value}")
        return value

    @field_validator("record_id")
    @classmethod
    def record_id_is_bounded(cls, value: str) -> str:
        value = value.strip()
        if not _RECORD_ID_RE.fullmatch(value):
            raise ValueError("record_id must be a bounded stable identifier")
        return value

    @field_validator("run_id", "source", "source_version")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("extensions")
    @classmethod
    def extensions_are_canonical(cls, value: dict[str, Any]) -> dict[str, Any]:
        canonical_data(value)
        return value

    @property
    def canonical_sha256(self) -> str:
        return content_sha256(self)
