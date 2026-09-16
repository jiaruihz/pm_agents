#!/usr/bin/env python3
"""Gate R WP2 offline sidecar helper.

It intentionally has no provider SDK, HTTP client, browser, subprocess, or
credential handling.  Operators may use its JSON schema/work-order routines in
an external approved environment and then import supplied bytes locally.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def provider_return_schema() -> dict:
    review_properties = {
        "topic": {"type": "string", "minLength": 1},
        "entities": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True},
        "relevant_clocks": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True},
        "source_type_hints": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True},
        "researchability": {"type": "string", "minLength": 1},
        "ambiguities": {"type": "array", "items": {"type": "string", "minLength": 1}, "uniqueItems": True},
        "independent_disposition_proposal": {"enum": ["ADVANCE", "REVIEW", "DEFER"]},
    }
    proposal_properties = {
        "field_name": {"type": "string", "minLength": 1},
        "proposed_value": {"type": "string", "minLength": 1},
        "source_segment_id": {"type": "string", "minLength": 1},
        "exact_quote": {"type": "string", "minLength": 1},
        "proposal_confidence_milli": {"type": "integer", "minimum": 0, "maximum": 1000},
        "unresolved": {"type": "boolean"},
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ["independent_semantic_review", "structured_rule_parse_proposal"],
        "properties": {
            "independent_semantic_review": {
                "type": "object", "additionalProperties": False,
                "required": sorted(review_properties), "properties": review_properties,
            },
            "structured_rule_parse_proposal": {
                "type": "object", "additionalProperties": False,
                "required": ["proposals"], "properties": {"proposals": {
                    "type": "array", "minItems": 1,
                    "items": {"type": "object", "additionalProperties": False,
                        "required": sorted(proposal_properties), "properties": proposal_properties},
                }},
            },
        },
    }


def _validate_payload_shape(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"independent_semantic_review", "structured_rule_parse_proposal"}:
        raise SystemExit("invalid provider namespaces")
    review = value["independent_semantic_review"]
    expected_review = {
        "topic", "entities", "relevant_clocks", "source_type_hints", "researchability",
        "ambiguities", "independent_disposition_proposal",
    }
    if not isinstance(review, dict) or set(review) != expected_review:
        raise SystemExit("invalid independent review shape")
    proposal = value["structured_rule_parse_proposal"]
    expected_item = {
        "field_name", "proposed_value", "source_segment_id", "exact_quote",
        "proposal_confidence_milli", "unresolved",
    }
    if not isinstance(proposal, dict) or set(proposal) != {"proposals"}:
        raise SystemExit("invalid proposal envelope")
    items = proposal["proposals"]
    if not isinstance(items, list) or not items or any(not isinstance(item, dict) or set(item) != expected_item for item in items):
        raise SystemExit("invalid proposal item shape")


def main() -> int:
    parser = argparse.ArgumentParser(description="offline GLM-5.3 independent-review schema helper")
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument("--validate-json", type=Path)
    args = parser.parse_args()
    if args.print_schema:
        print(json.dumps(provider_return_schema(), sort_keys=True, separators=(",", ":")))
        return 0
    if args.validate_json:
        value = json.loads(args.validate_json.read_text(encoding="utf-8"))
        _validate_payload_shape(value)
        print("offline provider schema validation passed")
        return 0
    parser.error("choose --print-schema or --validate-json")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
