"""Inyección de dependencias y ciclo de vida para la API HTTP."""

from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI
from src.application.chunking_service import ChunkingService
from src.application.summarize_use_case import SummarizeUseCase
from src.domain.ports import EventPublisherInterface, LLMProviderInterface
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.llm_clients.factory import create_llm_provider
from src.infrastructure.messaging.null_publisher import NullPublisher


class AppState:
    """Estado global de la aplicación (singleton por proceso)."""

    llm: LLMProviderInterface | None = None
    use_case: SummarizeUseCase | None = None


_app_state = AppState()


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    return get_settings()


async def get_llm() -> LLMProviderInterface:
    """Provee el cliente LLM inicializado en lifespan."""
    if _app_state.llm is None:
        raise RuntimeError("LLM not initialized")
    return _app_state.llm


async def get_use_case(llm: LLMProviderInterface = Depends(get_llm)) -> SummarizeUseCase:
    """Provee el caso de uso con dependencias resueltas."""
    if _app_state.use_case is None:
        settings = _get_settings()
        publisher: EventPublisherInterface = NullPublisher()
        _app_state.use_case = SummarizeUseCase(
            llm=llm,
            publisher=publisher,
            chunking=ChunkingService(
                threshold_words=settings.chunk_threshold,
                chunk_size=settings.chunk_size,
                overlap_words=settings.chunk_overlap,
            ),
            max_retries=settings.max_retries,
            llm_timeout_s=settings.llm_timeout_s,
            backoff_base_s=settings.backoff_base_s,
            backoff_cap_s=settings.backoff_cap_s,
            max_output_tokens=settings.max_output_tokens,
            max_context_tokens=llm.max_context_tokens,
            tokens_per_word=settings.tokens_per_word,
        )
    return _app_state.use_case


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida de la aplicación HTTP: init LLM en startup, close en shutdown."""
    settings = _get_settings()
    _app_state.llm = create_llm_provider(settings, max_context_tokens_override=settings.max_context_tokens)
    yield
    if _app_state.llm is not None:
        await _app_state.llm.aclose()
        _app_state.llm = None
    _app_state.use_case = None
