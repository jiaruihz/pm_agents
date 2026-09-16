"""Provider metadata registry for provider-neutral RecallHit ingestion."""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts import AlphaContract, RecallHit, RecallerType


class ProviderDescriptor(AlphaContract):
    provider_id: str
    recaller: RecallerType
    recaller_version: str
    enabled: bool = True
    requires_book: bool = False


class ProviderRegistry:
    """Immutable registry; it never executes provider code or performs I/O."""

    def __init__(self, descriptors: Iterable[ProviderDescriptor] = ()) -> None:
        indexed: dict[str, ProviderDescriptor] = {}
        for descriptor in descriptors:
            if descriptor.provider_id in indexed:
                raise ValueError(f"duplicate provider_id: {descriptor.provider_id}")
            indexed[descriptor.provider_id] = descriptor
        self._descriptors = indexed

    def get(self, provider_id: str) -> ProviderDescriptor | None:
        return self._descriptors.get(provider_id)

    def active(self, *, include_book: bool = False) -> tuple[ProviderDescriptor, ...]:
        return tuple(
            descriptor
            for descriptor in sorted(self._descriptors.values(), key=lambda item: item.provider_id)
            if descriptor.enabled and (include_book or not descriptor.requires_book)
        )

    def validate_hit(
        self,
        provider_id: str,
        hit: RecallHit,
        *,
        include_book: bool,
    ) -> str | None:
        descriptor = self.get(provider_id)
        if descriptor is None:
            return "UNKNOWN_PROVIDER"
        if not descriptor.enabled:
            return "PROVIDER_DISABLED"
        if descriptor.requires_book and not include_book:
            return "BOOK_PROVIDER_SKIPPED"
        if hit.source != provider_id:
            return "SOURCE_PROVIDER_MISMATCH"
        if hit.recaller != descriptor.recaller:
            return "RECALLER_TYPE_MISMATCH"
        if hit.recaller_version != descriptor.recaller_version:
            return "RECALLER_VERSION_MISMATCH"
        return None
