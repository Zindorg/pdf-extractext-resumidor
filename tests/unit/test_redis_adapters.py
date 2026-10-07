"""Pruebas de los adaptadores Redis Streams (consumer group, XADD, ACK)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ResponseError

from src.domain.models import SummaryResult
from src.infrastructure.messaging.redis_stream_consumer import RedisStreamConsumer
from src.infrastructure.messaging.redis_stream_publisher import RedisStreamPublisher
from src.infrastructure.messaging.serialization import payload_from_fields

BUSYGROUP = "BUSYGROUP Consumer Group name already exists"


def make_consumer(client: AsyncMock) -> RedisStreamConsumer:
    return RedisStreamConsumer(
        client,
        stream="events:text-extracted",
        group="summarizer-group",
        consumer="worker-1",
        block_ms=100,
        claim_min_idle_ms=1000,
    )


def make_publisher(client: AsyncMock) -> RedisStreamPublisher:
    return RedisStreamPublisher(
        client,
        output_stream="events:summary-generated",
        dlq_stream="events:summary-failed",
    )


class TestConsumer:
    async def test_ensure_group_creates_group_and_stream(self) -> None:
        client = AsyncMock()
        consumer = make_consumer(client)
        await consumer.ensure_group()
        client.xgroup_create.assert_awaited_once_with(
            "events:text-extracted", "summarizer-group", id="0", mkstream=True
        )

    async def test_ensure_group_is_idempotent(self) -> None:
        client = AsyncMock()
        client.xgroup_create.side_effect = ResponseError(BUSYGROUP)
        consumer = make_consumer(client)
        await consumer.ensure_group()

    async def test_ensure_group_propagates_unexpected_errors(self) -> None:
        client = AsyncMock()
        client.xgroup_create.side_effect = ResponseError("OOM command not allowed")
        consumer = make_consumer(client)
        with pytest.raises(ResponseError):
            await consumer.ensure_group()

    async def test_read_parses_stream_entries(self) -> None:
        client = AsyncMock()
        client.xreadgroup.return_value = [
            [
                "events:text-extracted",
                [
                    ("1700000000000-0", {"payload": '{"document_id": "x"}', "extra": "1"}),
                ],
            ]
        ]
        consumer = make_consumer(client)

        messages = await consumer.read(count=5)

        client.xreadgroup.assert_awaited_once_with(
            "summarizer-group",
            "worker-1",
            {"events:text-extracted": ">"},
            count=5,
            block=100,
        )
        assert len(messages) == 1
        assert messages[0].message_id == "1700000000000-0"
        assert messages[0].fields["payload"] == '{"document_id": "x"}'

    async def test_read_returns_empty_when_no_messages(self) -> None:
        client = AsyncMock()
        client.xreadgroup.return_value = []
        assert await make_consumer(client).read() == []

    async def test_ack_confirms_message_with_group(self) -> None:
        client = AsyncMock()
        await make_consumer(client).ack("1700000000000-0")
        client.xack.assert_awaited_once_with(
            "events:text-extracted", "summarizer-group", "1700000000000-0"
        )

    async def test_claim_stale_parses_entries(self) -> None:
        client = AsyncMock()
        client.xautoclaim.return_value = (
            "0-0",
            [],
            [("2-0", {"payload": "{}"})],
            [],
        )
        messages = await make_consumer(client).claim_stale()
        assert [m.message_id for m in messages] == ["2-0"]

    async def test_claim_stale_supports_redis_70_three_element_reply(self) -> None:
        client = AsyncMock()
        client.xautoclaim.return_value = ("0-0", [], [("3-0", {"payload": "{}"})])
        messages = await make_consumer(client).claim_stale()
        assert [m.message_id for m in messages] == ["3-0"]

    async def test_claim_stale_tolerates_unexpected_reply(self) -> None:
        client = AsyncMock()
        client.xautoclaim.return_value = []
        assert await make_consumer(client).claim_stale() == []

    async def test_claim_stale_tolerates_unsupported_server(self) -> None:
        client = AsyncMock()
        client.xautoclaim.side_effect = ResponseError("unknown command")
        assert await make_consumer(client).claim_stale() == []


class TestPublisher:
    async def test_publish_sends_contract_fields(self) -> None:
        client = AsyncMock()
        client.xadd.return_value = "1-0"
        result = SummaryResult(
            document_id=uuid4(),
            summary="Resumen",
            model_used="gpt-x",
            processing_time_ms=42,
        )

        message_id = await make_publisher(client).publish(result)

        assert message_id == "1-0"
        stream, fields = client.xadd.await_args.args
        assert stream == "events:summary-generated"
        assert fields == {
            "document_id": str(result.document_id),
            "summary": "Resumen",
            "model_used": "gpt-x",
            "processing_time_ms": "42",
        }

    async def test_publish_failure_sends_only_metadata_to_dlq(self) -> None:
        client = AsyncMock()
        client.xadd.return_value = "2-0"

        await make_publisher(client).publish_failure("doc-123", "llm_timeout")

        stream, fields = client.xadd.await_args.args
        assert stream == "events:summary-failed"
        assert fields == {
            "document_id": "doc-123",
            "error_code": "llm_timeout",
            "status": "failed",
        }


class TestPayloadParsing:
    def test_reads_single_field_json_payload(self) -> None:
        payload = payload_from_fields({"payload": '{"document_id": "abc"}'})
        assert payload == {"document_id": "abc"}

    def test_reads_direct_fields(self) -> None:
        payload = payload_from_fields({"document_id": "abc", "extracted_text": "hola"})
        assert payload == {"document_id": "abc", "extracted_text": "hola"}

    def test_invalid_json_raises(self) -> None:
        from src.domain.exceptions import InvalidPayloadError

        with pytest.raises(InvalidPayloadError):
            payload_from_fields({"payload": "{no-json"})

    def test_non_object_json_raises(self) -> None:
        from src.domain.exceptions import InvalidPayloadError

        with pytest.raises(InvalidPayloadError):
            payload_from_fields({"payload": '"soy una cadena"'})
