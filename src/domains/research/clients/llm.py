"""OpenAI-compatible LLM client wrapper."""

from typing import Any, Dict, List

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import get_settings


class LLMClient:
    def __init__(self) -> None:
        settings = get_settings()
        provider = (settings.llm_provider or "").strip().lower()
        if provider == "iflow":
            base_url = settings.iflow_base_url or settings.llm_base_url
            api_key = settings.iflow_api_key or settings.llm_api_key
            model = settings.iflow_model or settings.llm_model
        else:
            base_url = settings.llm_base_url
            api_key = settings.llm_api_key
            model = settings.llm_model

        if not base_url or not api_key or not model:
            raise ValueError("LLM configuration missing (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL)")
        headers = {"Authorization": f"Bearer {api_key}"}
        self.client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=60)
        self.model = model

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def chat(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload = {"model": self.model, "messages": messages, "temperature": 0}
        resp = await self.client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self.client.aclose()


def extract_content(resp_json: Dict[str, Any]) -> str:
    choices = resp_json.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""
