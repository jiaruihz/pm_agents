"""LLM prompts for rule extraction."""

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = (
    "你是 Polymarket 的规则抽取器。"
    "只返回一个严格符合 schema 的 JSON 对象，不要输出任何额外文字。"
    "不要提供交易建议、概率或方向性结论。"
    "不要编造事实；不确定就用 null 或空数组。"
    "列表字段必须输出数组，即使为空也用 []。"
    "可以引用输入里的短片段（<=25 词）作为说明，但要简短。"
)


USER_PROMPT_TEMPLATE = """
请从以下市场信息中抽取规则结构，严格输出符合 schema 的 JSON。

Schema 说明（字段必须齐全）：
{{
  "market_id": "string",
  "slug": "string|null",
  "time_window": {{"start_at_utc": "string|null", "end_at_utc": "string|null", "timezone_source": "string|null"}},
  "settlement_source_type": "official_docs|credible_reporting_consensus|mixed|unknown",
  "trigger_type": "definition_driven|data_print|procedural_vote|military_action|financial_price_level|other",
  "trigger_minimum_conditions": ["string", ...],
  "explicit_exclusions": ["string", ...],
  "entity_definitions": [{{"entity": "string", "definition": "string"}}],
  "ambiguity_flags": ["string", ...],
  "clarity_score": "number(0-1)",
  "dispute_risk_score": "number(0-1)",
  "notes_for_humans": "string",
  "llm_confidence": "number(0-1)"
}}

输入：
Question: {question}
Description: {description}
Rules: {rules}
End time (UTC): {end_at_utc}
Category: {category}
"""


FEW_SHOT_USER = """
Question: XYZ 公司会在 2024-12-31 前完成对 ABC 的并购吗？
Description: 二元市场，判断并购是否完成。
Rules: 若双方发布官方新闻稿确认交割则为 Yes；若交易终止则为 No。
End time (UTC): 2024-12-31T23:59:00Z
Category: business
"""

FEW_SHOT_ASSISTANT = {
    "market_id": "example",
    "slug": "example-slug",
    "time_window": {
        "start_at_utc": None,
        "end_at_utc": "2024-12-31T23:59:00+00:00",
        "timezone_source": "rules"
    },
    "settlement_source_type": "official_docs",
    "trigger_type": "procedural_vote",
    "trigger_minimum_conditions": [
        "Joint press release confirming deal closure"
    ],
    "explicit_exclusions": [
        "Rumors without official confirmation"
    ],
    "entity_definitions": [
        {"entity": "XYZ Corp", "definition": "Acquiring company"},
        {"entity": "ABC", "definition": "Target company"}
    ],
    "ambiguity_flags": [],
    "clarity_score": 0.78,
    "dispute_risk_score": 0.22,
    "notes_for_humans": "Settlement based on official press release confirming closure.",
    "llm_confidence": 0.8
}


def build_messages(question: str, description: str, rules: str, end_at_utc: str, category: str) -> list:
    user_content = USER_PROMPT_TEMPLATE.format(
        question=question or "",
        description=description or "",
        rules=rules or "",
        end_at_utc=end_at_utc or "",
        category=category or "",
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": FEW_SHOT_USER},
        {"role": "assistant", "content": json_dump(FEW_SHOT_ASSISTANT)},
        {"role": "user", "content": user_content},
    ]


def json_dump(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
