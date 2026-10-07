"""Pruebas del punto de entrada: composición y manejo de mensajes del worker."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.application.summarize_use_case import SummarizeUseCase
from src.domain.models import StreamMessage
from src.infrastructure.config import Settings
from src.presentation.worker.main import _safe_document_id, build, process_message
from tests.fakes import (
    FakeLLMProvider,
    make_message,
    make_payload,
    make_stack,
)

SECRET = "TEXTO_CONFIDENCIAL_DEL_DOCUMENTO"


class TestProcessMessage:
    async def test_successful_event_is_published_and_acked(self) -> None:
        use_case, publisher, consumer, llm = make_stack()
        payload = make_payload(SECRET)
        message = make_message(payload, message_id="10-1")

        await process_message(message, use_case=use_case, publisher=publisher, consumer=consumer)

        assert consumer.acked == ["10-1"]
        assert len(publisher.published) == 1
        assert str(publisher.published[0].document_id) == payload["document_id"]
        assert llm.call_count == 1

    async def test_accepts_direct_field_format(self) -> None:
        use_case, publisher, consumer, _ = make_stack()
        message = make_message(make_payload(SECRET), message_id="11-1", raw=True)

        await process_message(message, use_case=use_case, publisher=publisher, consumer=consumer)

        assert len(publisher.published) == 1
        assert consumer.acked == ["11-1"]

    async def test_invalid_payload_goes_to_dlq_and_is_acked(self) -> None:
        use_case, publisher, consumer, llm = make_stack()
        message = StreamMessage(
            message_id="12-1",
            fields={"payload": json.dumps({"document_id": "no-uuid", "extracted_text": SECRET})},
        )

        await process_message(message, use_case=use_case, publisher=publisher, consumer=consumer)

        assert publisher.failures == [("no-uuid", "invalid_payload")]
        assert publisher.published == []
        assert consumer.acked == ["12-1"]
        assert llm.call_count == 0

    async def test_malformed_json_goes_to_dlq(self) -> None:
        use_case, publisher, consumer, _ = make_stack()
        message = StreamMessage(message_id="13-1", fields={"payload": "{corrupto"})

        await process_message(message, use_case=use_case, publisher=publisher, consumer=consumer)

        assert publisher.failures == [("unknown", "invalid_payload")]
        assert consumer.acked == ["13-1"]

    async def test_empty_text_goes_to_dlq(self) -> None:
        use_case, publisher, consumer, _ = make_stack()
        message = make_message(make_payload("   "))

        await process_message(message, use_case=use_case, publisher=publisher, consumer=consumer)

        assert len(publisher.failures) == 1
        assert publisher.failures[0][1] == "empty_text"
        assert consumer.acked == [message.message_id]

    async def test_unexpected_error_goes_to_dlq_as_internal(self) -> None:
        llm = FakeLLMProvider(failures=[RuntimeError("bug interno")])
        use_case, publisher, consumer, _ = make_stack(llm)
        message = make_message(make_payload(SECRET))

        await process_message(message, use_case=use_case, publisher=publisher, consumer=consumer)

        assert publisher.failures[0][1] == "internal_error"
        assert consumer.acked == [message.message_id]

    async def test_exhausted_retries_go_to_dlq_with_cause_code(self) -> None:
        from src.domain.exceptions import LLMUnavailableError

        llm = FakeLLMProvider(failures=[LLMUnavailableError()] * 10)
        use_case, publisher, consumer, _ = make_stack(llm)
        message = make_message(make_payload(SECRET))

        await process_message(message, use_case=use_case, publisher=publisher, consumer=consumer)

        assert publisher.failures[0][1] == "summarization_failed:llm_unavailable"
        assert consumer.acked == [message.message_id]

    async def test_broker_outage_propagates_without_ack(self) -> None:
        use_case, publisher, consumer, _ = make_stack()
        publisher.broker_error = ConnectionError("redis caído")
        message = make_message(make_payload(SECRET))

        with pytest.raises(ConnectionError):
            await process_message(
                message, use_case=use_case, publisher=publisher, consumer=consumer
            )

        assert consumer.acked == []


class TestBuild:
    def test_wires_all_layers_from_settings(self) -> None:
        settings = Settings(_env_file=None)
        client = AsyncMock()

        components = build(settings, client=client)

        assert components.redis_client is client
        assert components.llm.model_name == settings.ai_model
        assert isinstance(components.use_case, SummarizeUseCase)
        assert components.consumer is not None
        assert components.publisher is not None

    def test_client_socket_timeout_exceeds_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.presentation.worker import main as worker_main

        captured: dict = {}

        def fake_from_url(url: str, **kwargs: object) -> AsyncMock:
            captured["url"] = url
            captured.update(kwargs)
            return AsyncMock()

        monkeypatch.setattr(worker_main.redis, "from_url", fake_from_url)

        build(Settings(_env_file=None, block_ms=5000))

        assert captured["decode_responses"] is True
        assert captured["socket_timeout"] > 5.0
        assert captured["socket_connect_timeout"] > 0


class TestSafeDocumentId:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("abc-123", "abc-123"),
            ("  abc  ", "abc"),
            ("", "unknown"),
            (None, "unknown"),
            (42, "unknown"),
            ({"a": 1}, "unknown"),
            ("x" * 200, "x" * 64),
        ],
    )
    def test_extracts_safe_identifier(self, value: object, expected: str) -> None:
        assert _safe_document_id(value) == expected
