"""Content lineage helpers for compressed research artifacts."""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path


def gzip_content_sha256(path: Path) -> str:
    """Hash decompressed bytes so gzip header timestamps do not alter lineage."""

    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
