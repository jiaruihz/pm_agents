"""Offline Alpha adapters for the existing single-owner market-book service."""

from .adapter import (
    BOOK_ADAPTER_VERSION,
    CAPTURE_OWNER,
    FrozenOwnerBookArtifact,
    OwnerDemandBundle,
    PairedBookNormalization,
    build_formal_review_demand,
    build_owner_capture_demands,
    build_sensing_demand,
    normalize_paired_owner_books,
)

__all__ = [
    "BOOK_ADAPTER_VERSION",
    "CAPTURE_OWNER",
    "FrozenOwnerBookArtifact",
    "OwnerDemandBundle",
    "PairedBookNormalization",
    "build_formal_review_demand",
    "build_owner_capture_demands",
    "build_sensing_demand",
    "normalize_paired_owner_books",
]
