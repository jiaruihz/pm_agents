"""Additive SQLite storage owned by Polymarket Alpha P0.

The package deliberately has no default database location.  Callers must pass a
SQLite connection or path they own.
"""

from .migrations import ALPHA_SCHEMA_VERSION, migrate, schema_manifest
from .repository import AlphaRepository, ContractConflictError, StoredContractCorruptionError

__all__ = [
    "ALPHA_SCHEMA_VERSION",
    "AlphaRepository",
    "ContractConflictError",
    "StoredContractCorruptionError",
    "migrate",
    "schema_manifest",
]
