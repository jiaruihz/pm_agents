"""Versioned data-product registry bound to the production topology.

The registry deliberately contains relative paths only.  It is loaded by the
production loader after the immutable release and managed-runtime contracts
have been validated, so callers cannot invent a parallel physical-root map.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

import yaml

if TYPE_CHECKING:
    from src.strategies.runtime.production import WeatherProductionSpec


REGISTRY_FILENAME = "data_products.yaml"
_DATA_CLASSES = frozenset({"current", "archive", "derived"})
_WRITE_SEMANTICS = frozenset(
    {"append_only", "replace_latest", "immutable_partition", "read_only"}
)
_CUTOVER_STATES = frozenset({"active", "shadow", "legacy_read_only", "planned"})


@dataclass(frozen=True)
class WeatherDataProductSpec:
    product_id: str
    owner_instance_id: str
    producer_release_id: str
    producer_release_sha: str
    schema_version: str
    root_key: str
    relative_path: Path
    data_class: str
    write_semantics: str
    grain: str
    clocks: tuple[tuple[str, str], ...]
    consumers: tuple[str, ...]
    cutover_state: str
    cursor: Mapping[str, Any] | None = None
    writer_lock: str | None = None
    feature_ready: bool | None = None
    notes: str | None = None


@dataclass(frozen=True)
class WeatherDataProductRegistry:
    version: str
    products: tuple[WeatherDataProductSpec, ...]
    _root_paths: Mapping[str, Path]

    def product(self, product_id: str) -> WeatherDataProductSpec:
        for product in self.products:
            if product.product_id == product_id:
                return product
        raise KeyError(product_id)

    def physical_path(self, product_id: str) -> Path:
        product = self.product(product_id)
        return self._root_paths[product.root_key] / product.relative_path

    def cursor_state_path(self, product_id: str) -> Path | None:
        product = self.product(product_id)
        if not product.cursor or not product.cursor.get("state_relative_path"):
            return None
        return self._root_paths[str(product.cursor["state_root_key"])] / Path(
            str(product.cursor["state_relative_path"])
        )

    def summary(self) -> dict[str, Any]:
        """Return a read-only, JSON-ready inventory for preflight callers."""
        rows = [
            {
                "product_id": product.product_id,
                "owner_instance_id": product.owner_instance_id,
                "producer_release_id": product.producer_release_id,
                "producer_release_sha": product.producer_release_sha,
                "schema_version": product.schema_version,
                "data_class": product.data_class,
                "write_semantics": product.write_semantics,
                "grain": product.grain,
                "clocks": dict(product.clocks),
                "consumers": list(product.consumers),
                "cutover_state": product.cutover_state,
                "root_key": product.root_key,
                "relative_path": str(product.relative_path),
                "physical_path": str(self.physical_path(product.product_id)),
                "cursor": dict(product.cursor) if product.cursor else None,
                "cursor_state_path": (
                    str(self.cursor_state_path(product.product_id))
                    if product.cursor and product.cursor.get("state_relative_path")
                    else None
                ),
                "writer_lock": product.writer_lock,
                "feature_ready": product.feature_ready,
                "notes": product.notes,
            }
            for product in self.products
        ]
        payload = {
            "registry_version": self.version,
            "product_count": len(rows),
            "products": rows,
        }
        payload["registry_build_id"] = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return payload

    def audit(self) -> dict[str, Any]:
        """Expose the validated registry as a read-only preflight audit."""
        return {"ok": True, **self.summary()}


def _root_paths(spec: WeatherProductionSpec) -> dict[str, Path]:
    """The only root keys accepted by a registry entry.

    Values are derived from existing ProductionSpec fields/methods, never from
    registry-provided absolute paths.
    """
    return {
        "production_storage_root": spec.production_storage_root,
        "archive_storage_root": spec.archive_storage_root,
        "data_feed_runtime_root": spec.data_feed_runtime_root,
        "pm_runtime_root": spec.pm_runtime_root,
        "market_books_root": spec.resolved_market_books_root(),
        "market_ladder_snapshot_root": spec.resolved_market_ladder_snapshot_root(),
        "strategy_snapshot_root": spec.resolved_strategy_snapshot_root(),
        "forecast_output_root": spec.resolved_forecast_output_root(),
        "historical_data_feed_runtime_root": spec.resolved_historical_data_feed_runtime_root(),
        "historical_full_ladder_root": spec.historical_full_ladder_root(),
        "historical_targeted_root": spec.historical_targeted_root(),
        "historical_paper_snapshot_root": spec.resolved_historical_paper_snapshot_root(),
        "research_artifact_root": spec.research_artifact_root,
    }


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a mapping")
    return value


def _required_string(item: Mapping[str, Any], name: str, product_id: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"data product {product_id} requires non-empty {name}")
    return value.strip()


def _relative_path(value: str, product_id: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"data product relative_path escapes root: {product_id}")
    return path


def load_data_product_registry(
    spec: WeatherProductionSpec, config_dir: Path | None = None
) -> WeatherDataProductRegistry:
    """Load and fail-closed validate the registry for an already loaded spec."""
    source = (config_dir or Path(__file__).parent) / REGISTRY_FILENAME
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    root_paths = _root_paths(spec)
    payload = _mapping(raw, "data product registry")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("data product registry requires version")
    products_raw = payload.get("products")
    if not isinstance(products_raw, list) or not products_raw:
        raise ValueError("data product registry requires non-empty products")

    runtime_by_id = {runtime.instance_id: runtime for runtime in spec.managed_runtimes}
    products: list[WeatherDataProductSpec] = []
    seen_product_ids: set[str] = set()
    mutable_targets: dict[Path, str] = {}
    for raw_item in products_raw:
        item = _mapping(raw_item, "each data product")
        product_id = _required_string(item, "product_id", "<unknown>")
        if product_id in seen_product_ids:
            raise ValueError(f"duplicate data product product_id: {product_id}")
        seen_product_ids.add(product_id)
        owner = _required_string(item, "owner_instance_id", product_id)
        runtime = runtime_by_id.get(owner)
        if runtime is None:
            raise ValueError(f"data product owner is not a managed runtime: {product_id}")
        producer_release_id = _required_string(item, "producer_release_id", product_id)
        if runtime.release_id != producer_release_id:
            raise ValueError(f"data product owner/release binding mismatch: {product_id}")
        producer_release_sha = spec.release(producer_release_id).expected_repo_sha
        root_key = _required_string(item, "root_key", product_id)
        if root_key not in root_paths:
            raise ValueError(f"unknown data product root_key: {product_id}: {root_key}")
        relative_path = _relative_path(
            _required_string(item, "relative_path", product_id), product_id
        )
        data_class = _required_string(item, "data_class", product_id)
        if data_class not in _DATA_CLASSES:
            raise ValueError(f"unsupported data product data_class: {product_id}")
        write_semantics = _required_string(item, "write_semantics", product_id)
        if write_semantics not in _WRITE_SEMANTICS:
            raise ValueError(f"unsupported data product write_semantics: {product_id}")
        cutover_state = _required_string(item, "cutover_state", product_id)
        if cutover_state not in _CUTOVER_STATES:
            raise ValueError(f"unsupported data product cutover_state: {product_id}")
        clocks_raw = _mapping(item.get("clocks"), f"data product clocks: {product_id}")
        if not clocks_raw or not all(isinstance(key, str) and isinstance(value, str) and value for key, value in clocks_raw.items()):
            raise ValueError(f"data product clocks must be a non-empty string mapping: {product_id}")
        consumers_raw = item.get("consumers")
        if not isinstance(consumers_raw, list) or not consumers_raw or not all(isinstance(value, str) and value.strip() for value in consumers_raw):
            raise ValueError(f"data product consumers must be a non-empty string list: {product_id}")
        cursor = item.get("cursor")
        if cursor is not None and not isinstance(cursor, dict):
            raise ValueError(f"data product cursor must be a mapping: {product_id}")
        if write_semantics == "append_only":
            required_cursor = {
                "kind",
                "state_scope",
                "epoch_semantics",
                "recovery_owner",
                "stuck_slo_seconds",
            }
            missing_cursor = sorted(required_cursor - set(cursor or {}))
            if missing_cursor:
                raise ValueError(
                    "append-only data product cursor contract incomplete: "
                    f"{product_id}: {missing_cursor}"
                )
            state_scope = str(cursor["state_scope"])
            if state_scope not in {"single_consumer", "per_consumer"}:
                raise ValueError(f"unsupported cursor state_scope: {product_id}")
            if state_scope == "single_consumer":
                state_root_key = str(cursor.get("state_root_key") or "")
                state_relative_path = str(cursor.get("state_relative_path") or "")
                if not state_root_key or not state_relative_path:
                    raise ValueError(
                        f"single-consumer cursor state path is incomplete: {product_id}"
                    )
                if state_root_key not in root_paths:
                    raise ValueError(
                        f"unknown cursor state_root_key: {product_id}: {state_root_key}"
                    )
                _relative_path(state_relative_path, product_id)
            elif cursor.get("state_root_key") or cursor.get("state_relative_path"):
                raise ValueError(
                    f"per-consumer cursor must not invent one global state path: {product_id}"
                )
            if str(cursor["epoch_semantics"]) != "new_epoch_never_reset_in_place":
                raise ValueError(
                    f"append-only cursor must create a new recovery epoch: {product_id}"
                )
            if not str(cursor["recovery_owner"]).strip():
                raise ValueError(f"append-only cursor recovery_owner required: {product_id}")
            if int(cursor["stuck_slo_seconds"]) <= 0:
                raise ValueError(
                    f"append-only cursor stuck SLO must be positive: {product_id}"
                )
        writer_lock = item.get("writer_lock")
        if writer_lock is not None and (not isinstance(writer_lock, str) or not writer_lock.strip()):
            raise ValueError(f"data product writer_lock must be a non-empty string: {product_id}")
        feature_ready = item.get("feature_ready")
        if feature_ready is not None and not isinstance(feature_ready, bool):
            raise ValueError(f"data product feature_ready must be boolean: {product_id}")
        target = root_paths[root_key] / relative_path
        production_root = spec.production_storage_root.resolve(strict=False)
        archive_root = spec.archive_storage_root.resolve(strict=False)
        resolved_target = target.resolve(strict=False)
        if data_class == "current" and not resolved_target.is_relative_to(production_root):
            raise ValueError(f"current data product must live under production storage: {product_id}")
        if data_class == "archive" and not resolved_target.is_relative_to(archive_root):
            raise ValueError(f"archive data product must live under archive storage: {product_id}")
        if data_class == "derived" and not (
            resolved_target.is_relative_to(production_root)
            or resolved_target.is_relative_to(archive_root)
        ):
            raise ValueError(f"derived data product must live under managed storage: {product_id}")
        if write_semantics != "read_only":
            prior = next(
                (
                    prior_product
                    for prior_target, prior_product in mutable_targets.items()
                    if resolved_target == prior_target
                    or resolved_target.is_relative_to(prior_target)
                    or prior_target.is_relative_to(resolved_target)
                ),
                None,
            )
            if prior is not None:
                raise ValueError(
                    "multiple mutable data product writers overlap physical target: "
                    f"{prior}, {product_id}"
                )
            mutable_targets[resolved_target] = product_id
        products.append(
            WeatherDataProductSpec(
                product_id=product_id,
                owner_instance_id=owner,
                producer_release_id=producer_release_id,
                producer_release_sha=producer_release_sha,
                schema_version=_required_string(item, "schema_version", product_id),
                root_key=root_key,
                relative_path=relative_path,
                data_class=data_class,
                write_semantics=write_semantics,
                grain=_required_string(item, "grain", product_id),
                clocks=tuple(sorted((str(key), str(value)) for key, value in clocks_raw.items())),
                consumers=tuple(str(value) for value in consumers_raw),
                cutover_state=cutover_state,
                cursor=cursor,
                writer_lock=writer_lock.strip() if isinstance(writer_lock, str) else None,
                feature_ready=feature_ready,
                notes=item.get("notes") if isinstance(item.get("notes"), str) else None,
            )
        )
    return WeatherDataProductRegistry(version=version.strip(), products=tuple(products), _root_paths=root_paths)


def production_data_product_registry(spec: WeatherProductionSpec) -> WeatherDataProductRegistry:
    """Return the registry already attached by the sole production loader."""
    registry = spec.data_product_registry
    if not isinstance(registry, WeatherDataProductRegistry):
        raise ValueError("ProductionSpec was not loaded with a data product registry")
    return registry
