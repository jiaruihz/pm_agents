#!/usr/bin/env python3
"""Audit cutover evidence and print only GO/NO_GO; never changes writers or data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from weather_data_feed.cutover import ProductCutoverResult, audit_product
from src.strategies.runtime.data_products import production_data_product_registry
from src.strategies.runtime.production import load_production_spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="JSON object keyed by product")
    parser.add_argument("--production-spec", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    products = payload.get("products", payload)
    if not isinstance(products, dict):
        raise ValueError("cutover evidence must be an object keyed by product_id")
    registry = production_data_product_registry(load_production_spec(args.production_spec))
    registry_build_id = registry.audit()["registry_build_id"]
    results = []
    for product_id, evidence in products.items():
        try:
            product = registry.product(str(product_id))
        except KeyError:
            results.append(
                ProductCutoverResult(
                    str(product_id), "NO_GO", ("unknown_registered_product",)
                )
            )
            continue
        results.append(
            audit_product(
                str(product_id),
                evidence,
                expected_registry_build_id=registry_build_id,
                expected_producer_release_sha=product.producer_release_sha,
            )
        )
    print(json.dumps({"results": [result.__dict__ for result in results]}, default=list, sort_keys=True))
    return 0 if all(result.decision == "GO" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
