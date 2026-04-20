"""Factory for creating LLM clients."""

import logging
import os
from typing import Optional

from .base import BaseLLMClient
from .gemini_client import GeminiClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient

logger = logging.getLogger(__name__)


class LLMClientFactory:
    """Factory for creating LLM clients based on backend type."""

    @staticmethod
    def create_client(
        backend: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> BaseLLMClient:
        backend = backend or os.getenv("LLM_BACKEND", "openai")
        backend = backend.lower()

        logger.info("Creating LLM client: backend=%s model=%s", backend, model)

        if backend == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY must be set for OpenAI backend")
            model = model or os.getenv("OPENAI_MODEL", "gpt-4")
            return OpenAIClient(api_key=api_key, model=model)
        elif backend == "gemini":
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY must be set for Gemini backend")
            model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            return GeminiClient(api_key=api_key, model=model)
        elif backend == "ollama":
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            model = model or os.getenv("OLLAMA_MODEL", "llama3.1")
            return OllamaClient(base_url=base_url, model=model)
        else:
            raise ValueError(
                f"Unknown LLM backend: {backend}. " f"Supported: openai, gemini, ollama"
            )
