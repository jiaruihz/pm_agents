from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from openai import OpenAI


PROMPT_VERSION = "v1"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


def _normalize_base_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.rstrip("/")
    # Many OpenAI-compatible providers expect /v1.
    if not u.endswith("/v1"):
        u = u + "/v1"
    return u


@dataclass(frozen=True)
class LlmDecision:
    item_id: str
    relevant: bool
    relevance: float
    credibility: float
    urgency: float
    direction: str  # "BULLISH" | "BEARISH" | "NEUTRAL" | "UNKNOWN"
    category: str  # "SCOOP" | "OFFICIAL" | "RUMOR" | "ANALYSIS" | "OTHER"
    summary: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "relevant": self.relevant,
            "relevance": float(self.relevance),
            "credibility": float(self.credibility),
            "urgency": float(self.urgency),
            "direction": self.direction,
            "category": self.category,
            "summary": self.summary,
            "reason": self.reason,
        }


class NewsLlmFilter:
    """
    LLM node: understand a news item (title/description) and decide if it matters for a market thesis.

    This is intentionally lightweight:
    - Operates on RSS/snippet content, not full articles.
    - Outputs structured scores that downstream logic can threshold on.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        language: Optional[str] = None,
        timeout_sec: float = 30.0,
    ) -> None:
        api_key = (
            api_key
            or _env("NEWS_LLM_API_KEY")
            or _env("OPENAI_API_KEY")
            or _env("ALIPAY_API_KEY")
        )
        if not api_key:
            raise ValueError("Missing NEWS_LLM_API_KEY (or OPENAI_API_KEY).")

        base_url = _normalize_base_url(base_url or _env("NEWS_LLM_BASE_URL"))
        model = model or _env("NEWS_LLM_MODEL", "gpt-4o-mini")
        language = (language or _env("NEWS_LLM_LANG", "en") or "en").strip().lower()

        self.model = model
        self.language = "zh" if language.startswith("zh") else "en"
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_sec,
        )

    def decide(
        self,
        *,
        item_id: str,
        market_question: str,
        resolution_criteria: str,
        title: str,
        description: str,
        source: str,
        published_at: Optional[str],
        query: Optional[str] = None,
    ) -> LlmDecision:
        """
        Returns a single decision. Raises on transport errors.
        """

        # Keep the schema tight; downstream can be a deterministic thresholding layer.
        schema_desc = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "relevant": {"type": "boolean"},
                "relevance": {"type": "number", "minimum": 0, "maximum": 1},
                "credibility": {"type": "number", "minimum": 0, "maximum": 1},
                "urgency": {"type": "number", "minimum": 0, "maximum": 1},
                "direction": {
                    "type": "string",
                    "enum": ["BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"],
                },
                "category": {
                    "type": "string",
                    "enum": ["SCOOP", "OFFICIAL", "RUMOR", "ANALYSIS", "OTHER"],
                },
                "summary": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": [
                "relevant",
                "relevance",
                "credibility",
                "urgency",
                "direction",
                "category",
                "summary",
                "reason",
            ],
        }

        if self.language == "zh":
            system = (
                "你是预测市场的交易新闻分析师。\n"
                "任务：判断单条新闻是否与给定 market 问题相关/可交易，并给出结构化打分。\n"
                "必须保守：如果不确定或信息不足，relevant=false，并解释原因。\n"
                "要求：只返回符合 schema 的 JSON；summary 和 reason 用中文。\n"
            )
        else:
            system = (
                "You are a trading news analyst for prediction markets.\n"
                "Task: judge if a single news item is relevant/actionable for a given market question.\n"
                "You must be conservative: if unclear, mark relevant=false and explain.\n"
                "Return ONLY valid JSON matching the given schema.\n"
            )

        user_payload = {
            "market_question": market_question,
            "resolution_criteria": resolution_criteria,
            "news_item": {
                "id": item_id,
                "query": query,
                "title": title,
                "description": description,
                "source": source,
                "published_at": published_at,
            },
            "schema": schema_desc,
        }

        # Some OpenAI-compatible providers don't support response_format; fall back if needed.
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
                ],
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{}"
        except Exception:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
                ],
            )
            content = resp.choices[0].message.content or "{}"

        data = json.loads(content)

        return LlmDecision(
            item_id=item_id,
            relevant=bool(data.get("relevant", False)),
            relevance=float(data.get("relevance", 0.0)),
            credibility=float(data.get("credibility", 0.0)),
            urgency=float(data.get("urgency", 0.0)),
            direction=str(data.get("direction", "UNKNOWN")),
            category=str(data.get("category", "OTHER")),
            summary=str(data.get("summary", "")),
            reason=str(data.get("reason", "")),
        )
