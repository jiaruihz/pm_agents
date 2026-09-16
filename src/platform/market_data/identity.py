"""Stable content identities shared by Polymarket market-data contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json_hash(payload: Any) -> str:
    """Return a full SHA256 over canonical JSON while preserving explicit nulls."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
