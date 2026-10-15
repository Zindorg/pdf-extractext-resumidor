"""Worker asíncrono: punto de entrada y composition root.

Consume de ``events:text-extracted`` (grupo ``summarizer-group``), ejecuta el
caso de uso de resumen y publica en ``events:summary-generated``. Los eventos
irrecuperables van al dead-letter ``events:summary-failed`` y siempre se ACKean.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import NamedTuple

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.application.chunking_service import ChunkingService
from src.application.summarize_use_case import SummarizeUseCase
from src.domain.exceptions import DomainError
from src.domain.models import StreamMessage, SummaryRequest
from src.domain.ports import (
    EventConsumerInterface,
    EventPublisherInterface,
    LLMProviderInterface,
)
from src.infrastructure.config import Settings, get_settings
from src.infrastructure.llm_clients.factory import create_llm_provider
from src.infrastructure.messaging.redis_stream_consumer import RedisStreamConsumer
from src.infrastructure.messaging.redis_stream_publisher import RedisStreamPublisher
from src.infrastructure.messaging.serialization import payload_from_fields
from src.presentation.worker.logging_setup import setup_logging

logger = logging.getLogger("summarizer.worker")

_INTERNAL_ERROR = "internal_error"
_STOP_SLEEP_S = 1.0
# redis-py usa 5s por defecto; el socket debe superar el BLOCK del XREADGROUP.
_REDIS_SOCKET_MARGIN_S = 5.0


class WorkerComponents(NamedTuple):
    """Objetos compuestos por el composition root."""

    use_case: SummarizeUseCase
    consumer: EventConsumerInterface
    publisher: EventPublisherInterface
    llm: LLMProviderInterface
    redis_client: redis.Redis


def build(
    settings: Settings,
    client: redis.Redis | None = None,
) -> WorkerComponents:
    """Compone las capas: adapters de infraestructura -> caso de uso."""
    redis_client = client or redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.block_ms / 1000 + _REDIS_SOCKET_MARGIN_S,
        socket_connect_timeout=_REDIS_SOCKET_MARGIN_S,
    )
    consumer = RedisStreamConsumer(
        redis_client,
        stream=settings.input_stream,
        group=settings.consumer_group,
        consumer=settings.consumer_name,
        block_ms=settings.block_ms,
        claim_min_idle_ms=settings.claim_min_idle_ms,
    )
    publisher = RedisStreamPublisher(
        redis_client,
        output_stream=settings.output_stream,
        dlq_stream=settings.dlq_stream,
    )
    llm = create_llm_provider(settings, max_context_tokens_override=settings.max_context_tokens)
    use_case = SummarizeUseCase(
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
    return WorkerComponents(use_case, consumer, publisher, llm, redis_client)


def _safe_document_id(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:64]
    return "unknown"


async def process_message(
    message: StreamMessage,
    *,
    use_case: SummarizeUseCase,
    publisher: EventPublisherInterface,
    consumer: EventConsumerInterface,
) -> None:
    """Parsea, ejecuta y confirma un mensaje. Errores -> DLQ + ACK.

    El ACK siempre se emite al final (salvo que el propio broker falle, en cuyo
    caso el mensaje queda pendiente y será reclamado más tarde).
    """
    document_id = "unknown"
    try:
        payload = payload_from_fields(message.fields)
        document_id = _safe_document_id(payload.get("document_id"))
        request = SummaryRequest.from_payload(payload)
        document_id = str(request.document_id)
        await use_case.execute(request)
    except DomainError as exc:
        logger.error(
            "event failed",
            extra={"document_id": document_id, "error_code": exc.code},
        )
        await publisher.publish_failure(document_id, exc.code)
    except Exception as exc:
        logger.error(
            "event failed unexpectedly",
            extra={"document_id": document_id, "error_type": type(exc).__name__},
        )
        await publisher.publish_failure(document_id, _INTERNAL_ERROR)
    await consumer.ack(message.message_id)


async def run(settings: Settings | None = None) -> None:
    """Loop de consumo con shutdown gracioso (SIGINT/SIGTERM)."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    components = build(settings)
    use_case, consumer, publisher = (
        components.use_case,
        components.consumer,
        components.publisher,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    logger.info(
        "worker starting",
        extra={
            "stream": settings.input_stream,
            "group": settings.consumer_group,
            "provider": settings.ai_provider,
            "model": settings.ai_model,
        },
    )

    try:
        await consumer.ensure_group()
        for message in await consumer.claim_stale():
            await _handle(message, use_case, publisher, consumer)

        while not stop.is_set():
            try:
                messages = await consumer.read(count=settings.read_count)
            except (RedisTimeoutError, RedisConnectionError):
                logger.warning("redis read failed, retrying")
                await asyncio.sleep(_STOP_SLEEP_S)
                continue
            for message in messages:
                if stop.is_set():
                    break
                await _handle(message, use_case, publisher, consumer)
    finally:
        await components.llm.aclose()
        await components.redis_client.aclose()
        logger.info("worker stopped")


async def _handle(
    message: StreamMessage,
    use_case: SummarizeUseCase,
    publisher: EventPublisherInterface,
    consumer: EventConsumerInterface,
) -> None:
    try:
        await process_message(message, use_case=use_case, publisher=publisher, consumer=consumer)
    except Exception as exc:
        logger.error(
            "message handling aborted",
            extra={"error_type": type(exc).__name__},
        )
        await asyncio.sleep(_STOP_SLEEP_S)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
