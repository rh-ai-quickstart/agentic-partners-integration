"""Factory for creating LLM clients."""

import os
from typing import Optional

from shared_models import configure_logging

from .base import BaseLLMClient, InstrumentedLLMClient
from .gemini_client import GeminiClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient

logger = configure_logging("agent-service")


def _get_api_key_with_fallback(provider: str) -> str:
    """Get API key with new AI_* variables and backward compatibility.

    Resolution order:
    1. AI_API_KEY (universal)
    2. AI_{PROVIDER}_API_KEY (provider-specific)
    3. Legacy keys (GOOGLE_API_KEY, OPENAI_API_KEY) with deprecation warning

    Args:
        provider: Provider name (openai, gemini, ollama)

    Returns:
        API key string

    Raises:
        ValueError: If no API key found for provider
    """
    # 1. Check universal AI_API_KEY
    if api_key := os.getenv("AI_API_KEY"):
        return api_key

    # 2. Check provider-specific AI_{PROVIDER}_API_KEY
    provider_key = f"AI_{provider.upper()}_API_KEY"
    if api_key := os.getenv(provider_key):
        return api_key

    # 3. Check legacy keys with deprecation warning
    legacy_map = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
    }

    if legacy_var := legacy_map.get(provider):
        if api_key := os.getenv(legacy_var):
            logger.warning(
                f"{legacy_var} is deprecated. Use AI_API_KEY or {provider_key} instead.",
                provider=provider,
                legacy_var=legacy_var,
                recommended_vars=["AI_API_KEY", provider_key],
            )
            return api_key

    # No API key found
    raise ValueError(
        f"No API key found for {provider} provider. "
        f"Set AI_API_KEY, {provider_key}, or {legacy_map.get(provider, 'provider-specific key')}"
    )


class LLMClientFactory:
    """Factory for creating LLM clients based on backend type.

    Supports:
    - OpenAI (GPT-4, GPT-3.5, etc.)
    - Google Gemini (via Google AI API)
    - Ollama (local LLMs)

    Configuration via environment variables:
        AI_PROVIDER: openai|gemini|ollama (replaces LLM_BACKEND)
        AI_API_KEY: Universal API key for any provider
        AI_OPENAI_API_KEY: OpenAI-specific API key (optional)
        AI_GEMINI_API_KEY: Gemini-specific API key (optional)
        AI_MODEL: Universal model name (replaces provider-specific vars)

        Deprecated (maintained for backward compatibility):
        LLM_BACKEND: openai|gemini|ollama (use AI_PROVIDER)
        OPENAI_API_KEY: OpenAI API key (use AI_API_KEY or AI_OPENAI_API_KEY)
        GOOGLE_API_KEY: Google API key (use AI_API_KEY or AI_GEMINI_API_KEY)
        GEMINI_MODEL: Gemini model name (use AI_MODEL)
        OPENAI_MODEL: OpenAI model name (use AI_MODEL)
        OLLAMA_BASE_URL: Ollama server URL
        OLLAMA_MODEL: Ollama model name (use AI_MODEL)
        LLM_INSTRUMENTATION: Set to "true" to wrap clients with latency tracking
    """

    @staticmethod
    def create_client(
        backend: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> BaseLLMClient:
        """Create an LLM client based on backend type.

        When LLM_INSTRUMENTATION=true, the returned client is automatically
        wrapped with InstrumentedLLMClient, which records latency_ms on
        every LLMResponse. This is transparent to all calling code.

        Args:
            backend: LLM backend type (openai, gemini, ollama)
                     If None, uses LLM_BACKEND env var (default: openai)
            model: Model name to use
                   If None, uses backend-specific env var
            **kwargs: Additional backend-specific arguments

        Returns:
            Initialized LLM client (optionally instrumented)

        Raises:
            ValueError: If backend is unknown or required credentials missing
        """
        # Support new AI_PROVIDER with fallback to LLM_BACKEND
        backend = backend or os.getenv("AI_PROVIDER") or os.getenv("LLM_BACKEND", "openai")

        # Log deprecation warning if using LLM_BACKEND
        if not backend and os.getenv("LLM_BACKEND"):
            logger.warning(
                "LLM_BACKEND is deprecated. Use AI_PROVIDER instead.",
                backend=os.getenv("LLM_BACKEND"),
            )

        backend = backend.lower()

        logger.info("Creating LLM client", backend=backend, model=model)

        if backend == "openai":
            client = LLMClientFactory._create_openai_client(model, **kwargs)
        elif backend == "gemini":
            client = LLMClientFactory._create_gemini_client(model, **kwargs)
        elif backend == "ollama":
            client = LLMClientFactory._create_ollama_client(model, **kwargs)
        else:
            raise ValueError(
                f"Unknown LLM backend: {backend}. "
                f"Supported backends: openai, gemini, ollama"
            )

        if os.getenv("LLM_INSTRUMENTATION", "").lower() == "true":
            logger.info(
                "LLM instrumentation enabled, wrapping client",
                backend=backend,
                model=model,
            )
            client = InstrumentedLLMClient(client)

        return client

    @staticmethod
    def _create_openai_client(model: Optional[str] = None, **kwargs) -> OpenAIClient:
        """Create OpenAI client.

        Args:
            model: Model name (default from OPENAI_MODEL env var)
            **kwargs: Additional arguments

        Returns:
            Initialized OpenAI client

        Raises:
            ValueError: If OPENAI_API_KEY not set
        """
        api_key = _get_api_key_with_fallback("openai")

        model = model or os.getenv("OPENAI_MODEL", "gpt-4")

        return OpenAIClient(api_key=api_key, model=model)

    @staticmethod
    def _create_gemini_client(model: Optional[str] = None, **kwargs) -> GeminiClient:
        """Create Gemini client.

        Args:
            model: Model name (default from GEMINI_MODEL env var)
            **kwargs: Additional arguments

        Returns:
            Initialized Gemini client

        Raises:
            ValueError: If GOOGLE_API_KEY not set
        """
        api_key = _get_api_key_with_fallback("gemini")

        model = model or os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

        return GeminiClient(api_key=api_key, model=model)

    @staticmethod
    def _create_ollama_client(model: Optional[str] = None, **kwargs) -> OllamaClient:
        """Create Ollama client.

        Args:
            model: Model name (default from OLLAMA_MODEL env var)
            **kwargs: Additional arguments

        Returns:
            Initialized Ollama client
        """
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = model or os.getenv("OLLAMA_MODEL", "llama3.1")

        return OllamaClient(base_url=base_url, model=model)
