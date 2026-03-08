"""LLM parsing utilities and schema."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from src.agents.llm.codex_cli_client import codex_cli_available, run_codex_exec_json
from .config import get_settings
from src.agents.llm.client import LLMClient, extract_content
from src.agents.llm.research_prompts import FEW_SHOT_ASSISTANT, FEW_SHOT_USER, PROMPT_VERSION, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, build_messages
from .storage import save_market_rule_parses


class TimeWindow(BaseModel):
    start_at_utc: Optional[str] = None
    end_at_utc: Optional[str] = None
    timezone_source: Optional[str] = None


class EntityDef(BaseModel):
    entity: str
    definition: str


class RuleParse(BaseModel):
    market_id: str
    slug: Optional[str] = None
    time_window: TimeWindow
    settlement_source_type: str
    trigger_type: str
    trigger_minimum_conditions: List[str]
    explicit_exclusions: List[str]
    entity_definitions: List[EntityDef]
    ambiguity_flags: List[str]
    clarity_score: float = Field(ge=0, le=1)
    dispute_risk_score: float = Field(ge=0, le=1)
    notes_for_humans: str
    llm_confidence: float = Field(ge=0, le=1)

    @field_validator("settlement_source_type")
    @classmethod
    def _settlement_enum(cls, v: str) -> str:
        allowed = {"official_docs", "credible_reporting_consensus", "mixed", "unknown"}
        if v not in allowed:
            raise ValueError("invalid settlement_source_type")
        return v


def _rule_parse_output_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "market_id": {"type": "string"},
            "slug": {"type": ["string", "null"]},
            "time_window": {
                "type": "object",
                "properties": {
                    "start_at_utc": {"type": ["string", "null"]},
                    "end_at_utc": {"type": ["string", "null"]},
                    "timezone_source": {"type": ["string", "null"]},
                },
                "required": ["start_at_utc", "end_at_utc", "timezone_source"],
                "additionalProperties": False,
            },
            "settlement_source_type": {
                "type": "string",
                "enum": ["official_docs", "credible_reporting_consensus", "mixed", "unknown"],
            },
            "trigger_type": {
                "type": "string",
                "enum": [
                    "definition_driven",
                    "data_print",
                    "procedural_vote",
                    "military_action",
                    "financial_price_level",
                    "other",
                ],
            },
            "trigger_minimum_conditions": {"type": "array", "items": {"type": "string"}},
            "explicit_exclusions": {"type": "array", "items": {"type": "string"}},
            "entity_definitions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string"},
                        "definition": {"type": "string"},
                    },
                    "required": ["entity", "definition"],
                    "additionalProperties": False,
                },
            },
            "ambiguity_flags": {"type": "array", "items": {"type": "string"}},
            "clarity_score": {"type": "number"},
            "dispute_risk_score": {"type": "number"},
            "notes_for_humans": {"type": "string"},
            "llm_confidence": {"type": "number"},
        },
        "required": [
            "market_id",
            "slug",
            "time_window",
            "settlement_source_type",
            "trigger_type",
            "trigger_minimum_conditions",
            "explicit_exclusions",
            "entity_definitions",
            "ambiguity_flags",
            "clarity_score",
            "dispute_risk_score",
            "notes_for_humans",
            "llm_confidence",
        ],
        "additionalProperties": False,
    }

    @field_validator("trigger_type")
    @classmethod
    def _trigger_enum(cls, v: str) -> str:
        allowed = {
            "definition_driven",
            "data_print",
            "procedural_vote",
            "military_action",
            "financial_price_level",
            "other",
        }
        if v not in allowed:
            raise ValueError("invalid trigger_type")
        return v


def compute_rule_score(parsed: RuleParse) -> Dict[str, Any]:
    components: Dict[str, int] = {}
    score = 0

    time_window = parsed.time_window or TimeWindow()
    components["time_window_end"] = 15 if time_window.end_at_utc else 0
    components["time_window_start"] = 5 if time_window.start_at_utc else 0
    components["timezone_source"] = 5 if time_window.timezone_source else 0
    score += components["time_window_end"] + components["time_window_start"] + components["timezone_source"]

    settlement = parsed.settlement_source_type
    if settlement == "official_docs":
        components["settlement_source"] = 20
    elif settlement == "credible_reporting_consensus":
        components["settlement_source"] = 15
    elif settlement == "mixed":
        components["settlement_source"] = 10
    else:
        components["settlement_source"] = 0
    score += components["settlement_source"]

    components["trigger_type"] = 10 if parsed.trigger_type != "other" else 0
    score += components["trigger_type"]

    components["min_conditions"] = 15 if parsed.trigger_minimum_conditions else 0
    components["explicit_exclusions"] = 10 if parsed.explicit_exclusions else 0
    components["entity_definitions"] = 10 if parsed.entity_definitions else 0
    components["notes"] = 5 if parsed.notes_for_humans else 0
    score += (
        components["min_conditions"]
        + components["explicit_exclusions"]
        + components["entity_definitions"]
        + components["notes"]
    )

    ambiguity_penalty = min(10, len(parsed.ambiguity_flags or []) * 2)
    dispute_penalty = int(round((parsed.dispute_risk_score or 0) * 10))
    components["ambiguity_penalty"] = -ambiguity_penalty
    components["dispute_penalty"] = -dispute_penalty
    score -= ambiguity_penalty + dispute_penalty

    score = max(0, min(100, score))
    return {"score": score, "components": components}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _llm_model_name() -> Optional[str]:
    settings = get_settings()
    provider = (settings.llm_provider or "").strip().lower()
    if provider == "iflow":
        return settings.iflow_model or settings.llm_model
    return settings.llm_model


def clean_json_text(text: str) -> str:
    if text.strip().startswith("```"):
        text = text.strip().strip("`")
    if text.strip().startswith("json"):
        text = text.strip()[4:]
    return text.strip()


async def parse_market_with_llm(market: Dict[str, Any], retry_on_fail: bool = True) -> Optional[RuleParse]:
    settings = get_settings()
    messages = build_messages(
        question=market.get("question", ""),
        description=market.get("description", ""),
        rules=market.get("rules", ""),
        end_at_utc=market.get("end_at_utc", ""),
        category=market.get("category", ""),
    )
    client = LLMClient()
    raw_resp = await client.chat(messages)
    content = extract_content(raw_resp)
    parsed = _validate_content(content, market)
    if not parsed and retry_on_fail:
        retry_messages = messages + [
            {"role": "system", "content": "Return ONLY valid JSON that matches the schema."}
        ]
        raw_resp = await client.chat(retry_messages)
        content = extract_content(raw_resp)
        parsed = _validate_content(content, market)
    await client.aclose()
    return parsed


def build_codex_rule_prompt(market: Dict[str, Any]) -> str:
    user_content = USER_PROMPT_TEMPLATE.format(
        question=market.get("question", ""),
        description=market.get("description", ""),
        rules=market.get("rules", ""),
        end_at_utc=market.get("end_at_utc", ""),
        category=market.get("category", ""),
    )
    return "\n\n".join(
        [
            SYSTEM_PROMPT,
            "以下是 few-shot 示例：",
            FEW_SHOT_USER.strip(),
            json.dumps(FEW_SHOT_ASSISTANT, ensure_ascii=False, indent=2),
            user_content.strip(),
            "只输出符合 schema 的 JSON，不要输出任何解释。",
        ]
    )


def parse_market_with_codex_cli(market: Dict[str, Any]) -> Optional[RuleParse]:
    if not codex_cli_available():
        return None
    prompt = build_codex_rule_prompt(market)
    try:
        data = run_codex_exec_json(
            prompt=prompt,
            output_model=RuleParse,
            output_schema=_rule_parse_output_schema(),
        )
    except Exception:
        return None
    data["market_id"] = market.get("market_id")
    data["slug"] = market.get("slug")
    try:
        return RuleParse(**data)
    except Exception:
        return None


def _validate_content(content: str, market: Dict[str, Any]) -> Optional[RuleParse]:
    text = clean_json_text(content)
    try:
        data = json.loads(text)
    except Exception:
        return None
    data["market_id"] = market.get("market_id")
    data["slug"] = market.get("slug")
    try:
        return RuleParse(**data)
    except Exception:
        return None


def save_market_rule_parses_records(
    market_id: str,
    parsed: RuleParse,
    raw_response: Any,
    strategy_tag: Optional[str] = None,
    alpha_score: Optional[int] = None,
    hard_constraints: Optional[List[str]] = None,
    search_keywords: Optional[List[str]] = None,
) -> int:
    if hard_constraints is None:
        hard_constraints = []
    if search_keywords is None:
        search_keywords = []
    rule_score = compute_rule_score(parsed)
    rows = [
        {
            "market_id": market_id,
            "parsed_at_utc": _now_utc_iso(),
            "prompt_version": PROMPT_VERSION,
            "llm_model": _llm_model_name(),
            "strategy_tag": strategy_tag,
            "alpha_score": alpha_score,
            "rule_score": rule_score["score"],
            "rule_score_components_json": json.dumps(rule_score["components"], ensure_ascii=False),
            "hard_constraints_json": json.dumps(hard_constraints, ensure_ascii=False),
            "search_keywords_json": json.dumps(search_keywords, ensure_ascii=False),
            "parsed_json": json.dumps(parsed.dict(), ensure_ascii=False),
            "raw_response": json.dumps(raw_response, ensure_ascii=False) if not isinstance(raw_response, str) else raw_response,
            "llm_confidence": parsed.llm_confidence,
            "clarity_score": parsed.clarity_score,
            "dispute_risk_score": parsed.dispute_risk_score,
            "ambiguity_flags_json": json.dumps(parsed.ambiguity_flags, ensure_ascii=False),
        }
    ]
    return save_market_rule_parses(rows)


def save_market_rule_parse_failure(
    market_id: str,
    error: str,
    raw_response: Optional[Any] = None,
) -> int:
    row = {
        "market_id": market_id,
        "parsed_at_utc": _now_utc_iso(),
        "prompt_version": PROMPT_VERSION,
        "llm_model": _llm_model_name(),
        "strategy_tag": "PARSE_ERROR",
        "alpha_score": None,
        "rule_score": None,
        "rule_score_components_json": None,
        "hard_constraints_json": json.dumps([], ensure_ascii=False),
        "search_keywords_json": json.dumps([], ensure_ascii=False),
        "parsed_json": None,
        "raw_response": raw_response if isinstance(raw_response, str) else json.dumps(raw_response, ensure_ascii=False)
        if raw_response is not None
        else error,
        "llm_confidence": None,
        "clarity_score": None,
        "dispute_risk_score": None,
        "ambiguity_flags_json": json.dumps([], ensure_ascii=False),
    }
    return save_market_rule_parses([row])
