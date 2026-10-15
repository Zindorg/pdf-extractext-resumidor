"""Caso de uso: síntesis de texto con estrategia Map-Reduce.

Orquesta: validación → chunking condicional → inferencia con timeout y
backoff exponencial → publicación del resultado.

Privacidad: solo se registran metadatos (document_id, conteos, duraciones,
códigos de error). Jamás el texto extraído ni el resumen.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.application import prompts
from src.application.chunking_service import ChunkingService
from src.domain.exceptions import (
    DocumentTooLongError,
    EmptyTextError,
    LLMProviderError,
    LLMTimeoutError,
    SummarizationFailedError,
)
from src.domain.models import SummaryRequest, SummaryResult
from src.domain.ports import EventPublisherInterface, LLMProviderInterface

logger = logging.getLogger("summarizer.application")

_CONTEXT_EXCEEDED_KEYWORDS = frozenset(
    {"context", "max_tokens", "token limit", "context length", "413"}
)


class SummarizeUseCase:
    """Ejecuta el caso de uso de resumen sobre un ``SummaryRequest``."""

    def __init__(
        self,
        llm: LLMProviderInterface,
        publisher: EventPublisherInterface,
        chunking: ChunkingService,
        *,
        max_retries: int = 3,
        llm_timeout_s: float = 30.0,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 8.0,
        max_output_tokens: int | None = 1024,
        max_context_tokens: int = 128_000,
        tokens_per_word: float = 1.3,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        if llm_timeout_s <= 0:
            raise ValueError("llm_timeout_s must be > 0")
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be >= 1")
        if tokens_per_word <= 0:
            raise ValueError("tokens_per_word must be > 0")
        self._llm = llm
        self._publisher = publisher
        self._chunking = chunking
        self._max_retries = max_retries
        self._llm_timeout_s = llm_timeout_s
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._max_output_tokens = max_output_tokens
        self._max_context_tokens = max_context_tokens
        self._tokens_per_word = tokens_per_word

    async def execute(self, request: SummaryRequest) -> SummaryResult:
        started = time.monotonic()
        text = request.extracted_text.strip()
        if not text:
            raise EmptyTextError("empty_text: extracted_text has no content")

        document_id = str(request.document_id)
        word_count = len(text.split())
        estimated_tokens = int(word_count * self._tokens_per_word)

        if estimated_tokens > self._max_context_tokens:
            logger.warning(
                "document too long",
                extra={
                    "document_id": document_id,
                    "word_count": word_count,
                    "estimated_tokens": estimated_tokens,
                    "max_context_tokens": self._max_context_tokens,
                },
            )
            raise DocumentTooLongError(
                f"document_too_long: estimated {estimated_tokens} tokens exceeds limit {self._max_context_tokens}"
            )

        logger.info(
            "document received",
            extra={"document_id": document_id, "word_count": word_count, "estimated_tokens": estimated_tokens},
        )

        if self._chunking.needs_chunking(text):
            chunks = self._chunking.split(text)
            logger.info(
                "map-reduce applied",
                extra={"document_id": document_id, "chunk_count": len(chunks)},
            )
            partials = [
                await self._generate(document_id, prompts.map_prompt(chunk, request.options))
                for chunk in chunks
            ]
            summary = await self._generate(
                document_id, prompts.reduce_prompt(partials, request.options)
            )
        else:
            summary = await self._generate(
                document_id, prompts.single_prompt(text, request.options)
            )

        processing_time_ms = int((time.monotonic() - started) * 1000)
        result = SummaryResult(
            document_id=request.document_id,
            summary=summary,
            model_used=self._llm.model_name,
            processing_time_ms=processing_time_ms,
        )
        await self._publisher.publish(result)
        logger.info(
            "summary published",
            extra={
                "document_id": document_id,
                "processing_time_ms": processing_time_ms,
                "model_used": result.model_used,
            },
        )
        return result

    async def _generate(self, document_id: str, prompt: str) -> str:
        """Una llamada al LLM con timeout por intento y backoff exponencial."""
        attempt = 0
        while True:
            attempt += 1
            error: LLMProviderError
            try:
                return await asyncio.wait_for(
                    self._llm.generate_summary(prompt, self._max_output_tokens),
                    timeout=self._llm_timeout_s,
                )
            except TimeoutError:
                error = LLMTimeoutError("llm_timeout: provider did not respond in time")
            except LLMProviderError as exc:
                error = exc

            # Mapear errores de contexto excedido del proveedor a DocumentTooLongError
            error_msg = str(error).lower()
            if any(keyword in error_msg for keyword in _CONTEXT_EXCEEDED_KEYWORDS):
                logger.warning(
                    "context length exceeded",
                    extra={"document_id": document_id, "error": str(error)},
                )
                raise DocumentTooLongError("document_too_long: context length exceeded") from error

            if not error.retriable or attempt >= self._max_retries:
                raise SummarizationFailedError(
                    f"summarization_failed: after {attempt} attempt(s)",
                    cause_code=error.code,
                ) from error

            delay = min(self._backoff_base_s * (2 ** (attempt - 1)), self._backoff_cap_s)
            logger.warning(
                "llm call failed, retrying",
                extra={
                    "document_id": document_id,
                    "attempt": attempt,
                    "delay_s": delay,
                    "error_code": error.code,
                },
            )
            await asyncio.sleep(delay)
