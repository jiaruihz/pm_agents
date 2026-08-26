"""Additive SQLite storage owned by Polymarket Alpha P0.

The package deliberately has no default database location.  Callers must pass a
SQLite connection or path they own.
"""

from .migrations import (
    ALPHA_SCHEMA_VERSION,
    CATALOG_INTEGRITY_MIGRATION_ID,
    P0_01R2_PROJECTION_MIGRATION_ID,
    RULE_CONTRACT_INSTANCE_MIGRATION_ID,
    RULE_CORPUS_REVISION_MIGRATION_ID,
    catalog_integrity_manifest,
    migrate,
    p0_01r2_projection_manifest,
    rule_corpus_revision_manifest,
    rule_contract_instance_manifest,
    schema_manifest,
)
from .repository import AlphaRepository, ContractConflictError, StoredContractCorruptionError

__all__ = [
    "ALPHA_SCHEMA_VERSION",
    "CATALOG_INTEGRITY_MIGRATION_ID",
    "P0_01R2_PROJECTION_MIGRATION_ID",
    "RULE_CONTRACT_INSTANCE_MIGRATION_ID",
    "RULE_CORPUS_REVISION_MIGRATION_ID",
    "AlphaRepository",
    "ContractConflictError",
    "StoredContractCorruptionError",
    "migrate",
    "catalog_integrity_manifest",
    "p0_01r2_projection_manifest",
    "rule_corpus_revision_manifest",
    "rule_contract_instance_manifest",
    "schema_manifest",
]
