"""Ollama client implementation for local LLMs."""

import logging
from typing import List, Optional

import httpx

from .base import BaseLLMClient, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class OllamaClient(BaseLLMClient):
    """Ollama API client for local LLM deployment."""

    def __init__(
        self, base_url: str = "http://localhost:11434", model: str = "llama3.1"
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = httpx.AsyncClient(timeout=120.0)

    async def create_completion(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        ollama_messages = [msg.to_dict() for msg in messages]

        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        url = f"{self.base_url}/api/chat"
        response = await self.client.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        content = data.get("message", {}).get("content", "")
        usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
            "total_tokens": data.get("prompt_eval_count", 0)
            + data.get("eval_count", 0),
        }

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.model,
            finish_reason=data.get("done_reason", "stop"),
        )

    def get_model_name(self) -> str:
        return self.model
