"""Factory de proveedores LLM a partir de la configuración."""

from __future__ import annotations

from src.domain.ports import LLMProviderInterface
from src.infrastructure.config import Settings
from src.infrastructure.llm_clients.openai_compatible import OpenAICompatibleLLM


def create_llm_provider(settings: Settings) -> LLMProviderInterface:
    """Instancia el adaptador OpenAI-compatible según ``ai_provider``.

    Todos los proveedores soportados hablan el mismo contrato HTTP; la
    variación vive en ``ai_base_url``/``ai_model``/``ai_api_key``.
    """
    return OpenAICompatibleLLM(
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        base_url=settings.ai_base_url,
        timeout_s=settings.llm_timeout_s,
    )
