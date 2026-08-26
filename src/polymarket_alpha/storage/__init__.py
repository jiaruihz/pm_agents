"""Additive SQLite storage owned by Polymarket Alpha P0.

The package deliberately has no default database location.  Callers must pass a
SQLite connection or path they own.
"""

from .migrations import (
    ALPHA_SCHEMA_VERSION,
    RULE_CORPUS_REVISION_MIGRATION_ID,
    migrate,
    rule_corpus_revision_manifest,
    schema_manifest,
)
from .repository import AlphaRepository, ContractConflictError, StoredContractCorruptionError

__all__ = [
    "ALPHA_SCHEMA_VERSION",
    "RULE_CORPUS_REVISION_MIGRATION_ID",
    "AlphaRepository",
    "ContractConflictError",
    "StoredContractCorruptionError",
    "migrate",
    "rule_corpus_revision_manifest",
    "schema_manifest",
]
