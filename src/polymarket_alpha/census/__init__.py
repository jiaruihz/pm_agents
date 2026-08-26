"""Census subpackage: catalog revision writer for the Gamma adapter (P0-03)."""

from .catalog import (
    CapturedPage,
    CatalogIngestResult,
    DriftReceipt,
    ErrorReceipt,
    GammaCatalogIngestor,
)

__all__ = [
    "CapturedPage",
    "CatalogIngestResult",
    "DriftReceipt",
    "ErrorReceipt",
    "GammaCatalogIngestor",
]
