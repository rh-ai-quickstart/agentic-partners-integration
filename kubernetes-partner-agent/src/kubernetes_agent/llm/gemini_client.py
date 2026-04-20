"""Google Gemini client implementation."""

import logging
from typing import List, Optional

from google import genai

from .base import BaseLLMClient, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class GeminiClient(BaseLLMClient):
    """Google Gemini API client."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model

    async def create_completion(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        contents = []
        system_instruction = None

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            elif msg.role == "user":
                contents.append({"role": "user", "parts": [{"text": msg.content}]})
            elif msg.role == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg.content}]})

        config = genai.types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        content = response.text if hasattr(response, "text") else ""
        usage = {
            "prompt_tokens": getattr(
                getattr(response, "usage_metadata", None), "prompt_token_count", 0
            )
            or 0,
            "completion_tokens": getattr(
                getattr(response, "usage_metadata", None), "candidates_token_count", 0
            )
            or 0,
            "total_tokens": getattr(
                getattr(response, "usage_metadata", None), "total_token_count", 0
            )
            or 0,
        }

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.model_name,
            finish_reason="stop",
        )

    def get_model_name(self) -> str:
        return self.model_name
