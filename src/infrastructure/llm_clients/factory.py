"""Factory de proveedores LLM a partir de la configuración."""

from __future__ import annotations

from src.domain.ports import LLMProviderInterface
from src.infrastructure.config import Settings
from src.infrastructure.llm_clients.openai_compatible import OpenAICompatibleLLM


def create_llm_provider(
    settings: Settings,
    *,
    max_context_tokens_override: int | None = None,
) -> LLMProviderInterface:
    """Instancia el adaptador OpenAI-compatible según ``ai_provider``.

    Todos los proveedores soportados hablan el mismo contrato HTTP; la
    variación vive en ``ai_base_url``/``ai_model``/``ai_api_key``.

    Args:
        settings: Configuración de la aplicación.
        max_context_tokens_override: Si no es None, sobrescribe el límite
            de tokens de contexto por defecto del proveedor.
    """
    provider_type = settings.ai_provider.lower()
    effective_max_tokens = max_context_tokens_override or settings.max_context_tokens

    return OpenAICompatibleLLM(
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        base_url=settings.ai_base_url,
        timeout_s=settings.llm_timeout_s,
        max_context_tokens=effective_max_tokens,
        provider_type=provider_type,
    )
