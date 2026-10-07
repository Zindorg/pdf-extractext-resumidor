"""Publicador de resultados sobre Redis Streams."""

from __future__ import annotations

import logging

import redis.asyncio as redis

from src.domain.models import SummaryResult
from src.domain.ports import EventPublisherInterface
from src.infrastructure.messaging.serialization import failure_fields

logger = logging.getLogger("summarizer.infrastructure.publisher")


class RedisStreamPublisher(EventPublisherInterface):
    """Publica resúmenes exitosos y fallas (dead-letter) con XADD."""

    def __init__(
        self,
        client: redis.Redis,
        *,
        output_stream: str,
        dlq_stream: str,
    ) -> None:
        self._client = client
        self._output_stream = output_stream
        self._dlq_stream = dlq_stream

    async def publish(self, result: SummaryResult) -> str:
        message_id = await self._client.xadd(self._output_stream, result.to_payload())
        logger.info(
            "result published",
            extra={
                "document_id": str(result.document_id),
                "stream": self._output_stream,
                "message_id": message_id,
            },
        )
        return message_id

    async def publish_failure(self, document_id: str, error_code: str) -> str:
        message_id = await self._client.xadd(
            self._dlq_stream, failure_fields(document_id, error_code)
        )
        logger.warning(
            "failure published to dlq",
            extra={
                "document_id": document_id,
                "error_code": error_code,
                "stream": self._dlq_stream,
                "message_id": message_id,
            },
        )
        return message_id
