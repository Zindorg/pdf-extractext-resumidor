"""Pruebas de integración: flujo completo evento -> resumen -> publicación."""

from __future__ import annotations

import json

from src.application.chunking_service import ChunkingService
from src.domain.models import SummaryRequest
from src.presentation.worker.main import process_message
from tests.fakes import (
    FakeLLMProvider,
    make_message,
    make_payload,
    make_stack,
)

SECRET = "DOCUMENTO_RESERVADO_NO_DEBE_FUGARSE"

CONTRACT_KEYS = {"document_id", "summary", "model_used", "processing_time_ms"}


async def drain(consumer, use_case, publisher) -> int:
    """Simula el ciclo de lectura del worker hasta vaciar la cola."""
    processed = 0
    while True:
        messages = await consumer.read(count=10)
        if not messages:
            break
        for message in messages:
            await process_message(
                message, use_case=use_case, publisher=publisher, consumer=consumer
            )
            processed += 1
    return processed


class TestHappyPath:
    async def test_event_to_published_result(self) -> None:
        use_case, publisher, consumer, llm = make_stack()
        payload = make_payload(SECRET)
        consumer.queue.append(make_message(payload, message_id="100-1"))

        processed = await drain(consumer, use_case, publisher)

        assert processed == 1
        assert consumer.acked == ["100-1"]
        assert consumer.queue == []
        assert len(publisher.published) == 1

        result = publisher.published[0]
        assert str(result.document_id) == payload["document_id"]
        assert result.model_used == "fake-model-1"
        assert result.processing_time_ms >= 0
        assert llm.call_count == 1

    async def test_output_contract_json_round_trip(self) -> None:
        use_case, publisher, consumer, _ = make_stack()
        payload = make_payload(SECRET)
        consumer.queue.append(make_message(payload))

        await drain(consumer, use_case, publisher)

        fields = publisher.published[0].to_payload()
        assert set(fields) == CONTRACT_KEYS
        decoded = json.loads(json.dumps(fields))
        assert decoded["document_id"] == payload["document_id"]
        assert decoded["processing_time_ms"].isdigit()

    async def test_input_text_does_not_leak_into_output_fields(self) -> None:
        use_case, publisher, consumer, _ = make_stack(
            llm=FakeLLMProvider(responses=["Resumen sintetico"])
        )
        consumer.queue.append(make_message(make_payload(SECRET)))

        await drain(consumer, use_case, publisher)

        fields = publisher.published[0].to_payload()
        assert all(SECRET not in value for value in fields.values())
        assert "extracted_text" not in fields

    async def test_multiple_events_are_processed_in_order(self) -> None:
        use_case, publisher, consumer, llm = make_stack(
            llm=FakeLLMProvider(responses=["R1", "R2", "R3"])
        )
        ids = [f"{i}-0" for i in range(1, 4)]
        for index, message_id in enumerate(ids):
            consumer.queue.append(
                make_message(make_payload(f"Documento {index}"), message_id=message_id)
            )

        processed = await drain(consumer, use_case, publisher)

        assert processed == 3
        assert consumer.acked == ids
        assert [r.summary for r in publisher.published] == ["R1", "R2", "R3"]
        assert llm.call_count == 3


class TestMapReduceFlow:
    async def test_long_document_runs_map_and_reduce_end_to_end(self) -> None:
        chunking = ChunkingService(threshold_words=20, chunk_size=30, overlap_words=5)
        use_case, publisher, consumer, llm = make_stack(chunking=chunking)
        text = " ".join(f"frase numero {i}." for i in range(120))
        consumer.queue.append(make_message(make_payload(text), message_id="200-1"))

        processed = await drain(consumer, use_case, publisher)

        assert processed == 1
        assert llm.call_count >= 3
        assert len(publisher.published) == 1
        assert consumer.acked == ["200-1"]


class TestFailureFlow:
    async def test_llm_outage_ends_in_dlq_with_ack(self) -> None:
        from src.domain.exceptions import LLMUnavailableError

        use_case, publisher, consumer, _ = make_stack(
            llm=FakeLLMProvider(failures=[LLMUnavailableError()] * 10)
        )
        payload = make_payload(SECRET)
        consumer.queue.append(make_message(payload, message_id="300-1"))

        processed = await drain(consumer, use_case, publisher)

        assert processed == 1
        assert publisher.published == []
        assert publisher.failures == [
            (payload["document_id"], "summarization_failed:llm_unavailable")
        ]
        assert consumer.acked == ["300-1"]

    async def test_invalid_event_reaches_dlq_without_llm_call(self) -> None:
        use_case, publisher, consumer, llm = make_stack()
        consumer.queue.append(
            make_message(
                {"document_id": "mal-formado", "extracted_text": SECRET},
                message_id="301-1",
            )
        )

        await drain(consumer, use_case, publisher)

        assert publisher.failures[0][1] == "invalid_payload"
        assert llm.call_count == 0
        assert consumer.acked == ["301-1"]

    async def test_dead_letter_payload_has_no_content(self) -> None:
        use_case, publisher, consumer, _ = make_stack()
        payload = make_payload(SECRET, document_id="doc-999")
        consumer.queue.append(make_message(payload))

        await drain(consumer, use_case, publisher)

        document_id, error_code = publisher.failures[0]
        assert document_id == "doc-999"
        assert SECRET not in error_code
        assert isinstance(error_code, str)


class TestClaimRecovery:
    async def test_stale_messages_are_reprocessed(self) -> None:
        use_case, publisher, consumer, _ = make_stack()
        message = make_message(make_payload(SECRET), message_id="400-1")
        consumer.stale.append(message)

        for stale in await consumer.claim_stale():
            await process_message(stale, use_case=use_case, publisher=publisher, consumer=consumer)

        assert consumer.acked == ["400-1"]
        assert len(publisher.published) == 1

    async def test_round_trip_through_request_model(self) -> None:
        payload = make_payload(SECRET)
        request = SummaryRequest.from_payload(payload)
        assert request.extracted_text == payload["extracted_text"]
        assert str(request.document_id) == payload["document_id"]
