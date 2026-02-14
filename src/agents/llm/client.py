"""OpenAI-compatible LLM client wrapper."""

import os
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class LLMClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        load_dotenv()
        resolved_provider = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
        llm_base_url = os.getenv("LLM_BASE_URL")
        llm_api_key = os.getenv("LLM_API_KEY")
        llm_model = os.getenv("LLM_MODEL")
        iflow_base_url = os.getenv("IFLOW_BASE_URL")
        iflow_api_key = os.getenv("IFLOW_API_KEY")
        iflow_model = os.getenv("IFLOW_MODEL")

        if resolved_provider == "iflow":
            resolved_base_url = base_url or iflow_base_url or llm_base_url
            resolved_api_key = api_key or iflow_api_key or llm_api_key
            resolved_model = model or iflow_model or llm_model
        else:
            resolved_base_url = base_url or llm_base_url
            resolved_api_key = api_key or llm_api_key
            resolved_model = model or llm_model

        if not resolved_base_url or not resolved_api_key or not resolved_model:
            raise ValueError("LLM configuration missing (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL)")
        headers = {"Authorization": f"Bearer {resolved_api_key}"}
        self.client = httpx.AsyncClient(
            base_url=resolved_base_url,
            headers=headers,
            timeout=timeout_seconds,
        )
        self.model = resolved_model

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
