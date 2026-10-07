"""Consumidor de Redis Streams sobre un consumer group."""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ResponseError

from src.domain.models import StreamMessage
from src.domain.ports import EventConsumerInterface

logger = logging.getLogger("summarizer.infrastructure.consumer")

ACK_BLOCK_MS = 0  # no bloqueante en claim; el bloqueo vive en read()


class RedisStreamConsumer(EventConsumerInterface):
    """Lee eventos de un stream con XREADGROUP y confirma con XACK."""

    def __init__(
        self,
        client: redis.Redis,
        *,
        stream: str,
        group: str,
        consumer: str,
        block_ms: int = 5000,
        claim_min_idle_ms: int = 60_000,
    ) -> None:
        self._client = client
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._block_ms = block_ms
        self._claim_min_idle_ms = claim_min_idle_ms

    async def ensure_group(self) -> None:
        try:
            await self._client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        logger.info(
            "consumer group ready",
            extra={"stream": self._stream, "group": self._group},
        )

    async def read(self, count: int = 1) -> list[StreamMessage]:
        response: Any = await self._client.xreadgroup(
            self._group,
            self._consumer,
            {self._stream: ">"},
            count=count,
            block=self._block_ms,
        )
        return self._parse(response)

    async def claim_stale(self) -> list[StreamMessage]:
        try:
            response = await self._client.xautoclaim(
                self._stream,
                self._group,
                self._consumer,
                min_idle_time=self._claim_min_idle_ms,
                start_id="0-0",
                count=100,
            )
        except ResponseError:
            logger.warning("xautoclaim unsupported by server", extra={"stream": self._stream})
            return []
        # Redis 7.0 devuelve 3 elementos; 7.2+ agrega un cuarto. Las entradas
        # reclamadas siempre están en la posición 2.
        if not isinstance(response, (list, tuple)) or len(response) < 3:
            return []
        return self._to_messages(response[2])

    async def ack(self, message_id: str) -> None:
        await self._client.xack(self._stream, self._group, message_id)

    def _parse(self, response: Any) -> list[StreamMessage]:
        messages: list[StreamMessage] = []
        if not response:
            return messages
        for _stream_name, entries in response:
            messages.extend(self._to_messages(entries))
        return messages

    @staticmethod
    def _to_messages(entries: Any) -> list[StreamMessage]:
        return [
            StreamMessage(
                message_id=message_id,
                fields={key: value for key, value in fields.items()},
            )
            for message_id, fields in (entries or [])
        ]
