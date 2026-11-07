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
    LLMTruncatedError,
    SummarizationFailedError,
)
from src.domain.models import SummaryOptions, SummaryRequest, SummaryResult
from src.domain.ports import EventPublisherInterface, LLMProviderInterface

logger = logging.getLogger("summarizer.application")

_CONTEXT_EXCEEDED_KEYWORDS = frozenset(
    {"context", "max_tokens", "token limit", "context length", "413"}
)
_MAX_REDUCE_DEPTH = 5


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
        max_output_tokens: int = 4096,
        max_output_tokens_cap: int | None = None,
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
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1")
        output_cap = max_output_tokens_cap or max_output_tokens
        if output_cap < max_output_tokens:
            raise ValueError("max_output_tokens_cap must be >= max_output_tokens")
        self._llm = llm
        self._publisher = publisher
        self._chunking = chunking
        self._max_retries = max_retries
        self._llm_timeout_s = llm_timeout_s
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._max_output_tokens = max_output_tokens
        self._max_output_tokens_cap = output_cap
        self._max_context_tokens = max_context_tokens
        self._tokens_per_word = tokens_per_word
        self._effective_output_cap = min(
            self._max_output_tokens_cap, max(1, self._max_context_tokens // 2)
        )

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
            extra={
                "document_id": document_id,
                "word_count": word_count,
                "estimated_tokens": estimated_tokens,
            },
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
            summary = await self._reduce(document_id, partials, request.options)
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

    async def _reduce(
        self,
        document_id: str,
        partials: list[str],
        options: SummaryOptions | None,
        depth: int = 0,
    ) -> str:
        """Reduce parciales; si no entran en contexto, divide jerárquicamente."""
        prompt = prompts.reduce_prompt(partials, options)
        if self._fits_context(prompt):
            return await self._generate(document_id, prompt)

        if len(partials) == 1:
            logger.warning(
                "reduce input exceeds context",
                extra={
                    "document_id": document_id,
                    "depth": depth,
                    "partial_count": 1,
                },
            )
            raise DocumentTooLongError("document_too_long: reduce input exceeds context")

        if depth >= _MAX_REDUCE_DEPTH:
            logger.warning(
                "reduce depth limit reached",
                extra={
                    "document_id": document_id,
                    "depth": depth,
                    "partial_count": len(partials),
                },
            )
            raise DocumentTooLongError("document_too_long: reduce depth limit exceeded")

        midpoint = len(partials) // 2
        left = await self._reduce(document_id, partials[:midpoint], options, depth + 1)
        right = await self._reduce(document_id, partials[midpoint:], options, depth + 1)
        return await self._reduce(document_id, [left, right], options, depth + 1)

    def _fits_context(self, prompt: str) -> bool:
        estimated_tokens = int(len(prompt.split()) * self._tokens_per_word)
        return estimated_tokens + self._effective_output_cap <= self._max_context_tokens

    async def _generate(self, document_id: str, prompt: str) -> str:
        """Una llamada al LLM con timeout por intento y backoff exponencial."""
        attempt = 0
        max_tokens = self._max_output_tokens
        while True:
            attempt += 1
            error: LLMProviderError
            try:
                return await asyncio.wait_for(
                    self._llm.generate_summary(prompt, max_tokens),
                    timeout=self._llm_timeout_s,
                )
            except TimeoutError:
                error = LLMTimeoutError("llm_timeout: provider did not respond in time")
            except LLMTruncatedError as exc:
                error = exc
                next_limit = min(max_tokens * 2, self._effective_output_cap)
                if attempt < self._max_retries and next_limit > max_tokens:
                    logger.warning(
                        "llm output truncated, retrying with larger limit",
                        extra={
                            "document_id": document_id,
                            "attempt": attempt,
                            "previous_max_tokens": max_tokens,
                            "next_max_tokens": next_limit,
                            "error_code": exc.code,
                        },
                    )
                    max_tokens = next_limit
                    continue
                logger.warning(
                    "llm output truncated and cannot grow further",
                    extra={
                        "document_id": document_id,
                        "attempt": attempt,
                        "max_tokens": max_tokens,
                        "error_code": exc.code,
                    },
                )
                raise SummarizationFailedError(
                    f"summarization_failed: after {attempt} attempt(s)",
                    cause_code=exc.code,
                ) from exc
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
