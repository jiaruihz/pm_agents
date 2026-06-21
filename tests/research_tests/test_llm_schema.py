from src.strategies.rule_lawyer.parser import RuleParse, TimeWindow, EntityDef
from src.agents.llm.codex_cli_client import _strict_json_schema


def test_ruleparse_validation_accepts_schema():
    data = {
        "market_id": "m1",
        "slug": "s1",
        "time_window": {"start_at_utc": None, "end_at_utc": "2024-12-31T00:00:00Z", "timezone_source": "rules"},
        "settlement_source_type": "official_docs",
        "trigger_type": "procedural_vote",
        "trigger_minimum_conditions": ["official press release"],
        "explicit_exclusions": ["rumors"],
        "entity_definitions": [
            {"entity": "EntityA", "definition": "desc"}
        ],
        "ambiguity_flags": [],
        "clarity_score": 0.9,
        "dispute_risk_score": 0.1,
        "notes_for_humans": "uses official sources",
        "llm_confidence": 0.8,
    }
    parsed = RuleParse(**data)
    assert parsed.market_id == "m1"
    assert parsed.trigger_type == "procedural_vote"
    assert parsed.time_window.end_at_utc.startswith("2024")


def test_codex_cli_strict_schema_requires_all_object_properties():
    schema = {
        "type": "object",
        "properties": {
            "decision": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        },
    }

    strict = _strict_json_schema(schema)

    assert strict["additionalProperties"] is False
    assert strict["required"] == ["decision", "nested"]
    assert strict["properties"]["nested"]["additionalProperties"] is False
    assert strict["properties"]["nested"]["required"] == ["reason"]
