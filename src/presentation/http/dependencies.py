"""Inyección de dependencias y ciclo de vida para la API HTTP."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI

from src.application.chunking_service import ChunkingService
from src.application.summarize_use_case import SummarizeUseCase
from src.domain.ports import EventPublisherInterface, LLMProviderInterface
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.discovery.consul import ConsulServiceRegistrar
from src.infrastructure.llm_clients.factory import create_llm_provider
from src.infrastructure.messaging.null_publisher import NullPublisher

logger = logging.getLogger("summarizer.http.lifespan")

_CONSUL_TIMEOUT_S = 5.0


class AppState:
    """Estado global de la aplicación (singleton por proceso)."""

    llm: LLMProviderInterface | None = None
    use_case: SummarizeUseCase | None = None
    consul: ConsulServiceRegistrar | None = None


_app_state = AppState()


async def get_llm() -> LLMProviderInterface:
    """Provee el cliente LLM inicializado en lifespan."""
    if _app_state.llm is None:
        raise RuntimeError("LLM not initialized")
    return _app_state.llm


async def get_use_case(
    llm: Annotated[LLMProviderInterface, Depends(get_llm)],
) -> SummarizeUseCase:
    """Provee el caso de uso con dependencias resueltas."""
    if _app_state.use_case is None:
        settings = get_settings()
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
            max_output_tokens_cap=settings.max_output_tokens_cap,
            max_context_tokens=llm.max_context_tokens,
            tokens_per_word=settings.tokens_per_word,
        )
    return _app_state.use_case


async def _register_in_consul(settings: Settings) -> ConsulServiceRegistrar | None:
    """Registra el servicio en Consul (fail-open: si falla, se loguea y continúa)."""
    registrar = ConsulServiceRegistrar(
        httpx.AsyncClient(timeout=httpx.Timeout(_CONSUL_TIMEOUT_S)), settings=settings
    )
    try:
        await registrar.register()
    except Exception as exc:
        logger.error(
            "consul registration failed, continuing without service discovery",
            extra={"error_type": type(exc).__name__},
        )
        await registrar.aclose()
        return None
    return registrar


async def _deregister_from_consul(registrar: ConsulServiceRegistrar) -> None:
    """Desregistra el servicio de Consul. Nunca rompe el shutdown."""
    try:
        await registrar.deregister()
    except Exception as exc:
        logger.warning(
            "consul deregistration failed",
            extra={"service_id": registrar.service_id, "error_type": type(exc).__name__},
        )
    finally:
        await registrar.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida: init LLM + registro Consul en startup, cleanup en shutdown."""
    settings = get_settings()
    _app_state.llm = create_llm_provider(
        settings, max_context_tokens_override=settings.max_context_tokens
    )
    if settings.consul_enabled:
        _app_state.consul = await _register_in_consul(settings)
    yield
    if _app_state.consul is not None:
        await _deregister_from_consul(_app_state.consul)
        _app_state.consul = None
    if _app_state.llm is not None:
        await _app_state.llm.aclose()
        _app_state.llm = None
    _app_state.use_case = None
