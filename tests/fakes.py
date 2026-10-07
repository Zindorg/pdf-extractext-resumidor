"""Dobles en memoria (fakes) para las pruebas unitarias e de integración."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from src.application.chunking_service import ChunkingService
from src.application.summarize_use_case import SummarizeUseCase
from src.domain.models import StreamMessage, SummaryResult
from src.domain.ports import (
    EventConsumerInterface,
    EventPublisherInterface,
    LLMProviderInterface,
)

DEFAULT_TEXT = (
    "El proyecto consiste en un microservicio que procesa documentos PDF "
    "y genera resúmenes con inteligencia artificial. La arquitectura es "
    "Hexagonal con Clean Architecture de cuatro capas. El equipo de desarrollo "
    "mantiene el código en Git y ejecuta pruebas automatizadas antes de cada release."
)


def make_payload(text: str = DEFAULT_TEXT, document_id: str | None = None, **extra: object) -> dict:
    """Payload de entrada válido según el contrato de ARCHITECTURE_SUMMARIZER.md."""
    payload: dict = {
        "document_id": document_id or str(uuid4()),
        "extracted_text": text,
    }
    payload.update(extra)
    return payload


def make_message(payload: dict, message_id: str = "1-1", raw: bool = False) -> StreamMessage:
    """Mensaje de stream; ``raw`` simula el formato de campos directos."""
    if raw:
        fields = {key: str(value) for key, value in payload.items()}
    else:
        import json

        fields = {"payload": json.dumps(payload)}
    return StreamMessage(message_id=message_id, fields=fields)


class FakeLLMProvider(LLMProviderInterface):
    """Proveedor falso: respuestas en cola, fallos en cola y latencia opcional."""

    def __init__(
        self,
        *,
        responses: list[str] | None = None,
        failures: list[Exception] | None = None,
        delay_s: float = 0.0,
        model: str = "fake-model-1",
    ) -> None:
        self.responses = list(responses or [])
        self.failures = list(failures or [])
        self.delay_s = delay_s
        self.calls: list[tuple[str, int | None]] = []
        self.closed = False
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def generate_summary(self, text: str, max_tokens: int | None = None) -> str:
        self.calls.append((text, max_tokens))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.failures:
            raise self.failures.pop(0)
        if self.responses:
            return self.responses.pop(0)
        return f"FAKE_SUMMARY_{len(self.calls)}"

    async def aclose(self) -> None:
        self.closed = True


class FakeEventPublisher(EventPublisherInterface):
    """Publicador en memoria: registra éxitos y fallos."""

    def __init__(self) -> None:
        self.published: list[SummaryResult] = []
        self.failures: list[tuple[str, str]] = []
        self.broker_error: Exception | None = None

    async def publish(self, result: SummaryResult) -> str:
        if self.broker_error is not None:
            raise self.broker_error
        self.published.append(result)
        return f"out-{len(self.published)}-0"

    async def publish_failure(self, document_id: str, error_code: str) -> str:
        if self.broker_error is not None:
            raise self.broker_error
        self.failures.append((document_id, error_code))
        return f"dlq-{len(self.failures)}-0"


class FakeEventConsumer(EventConsumerInterface):
    """Consumidor en memoria con cola de mensajes y registro de ACKs."""

    def __init__(self, messages: list[StreamMessage] | None = None) -> None:
        self.queue: list[StreamMessage] = list(messages or [])
        self.acked: list[str] = []
        self.group_ready = False
        self.stale: list[StreamMessage] = []

    async def ensure_group(self) -> None:
        self.group_ready = True

    async def read(self, count: int = 1) -> list[StreamMessage]:
        taken = self.queue[:count]
        del self.queue[:count]
        return taken

    async def claim_stale(self) -> list[StreamMessage]:
        return list(self.stale)

    async def ack(self, message_id: str) -> None:
        self.acked.append(message_id)


def make_stack(
    llm: FakeLLMProvider | None = None,
    publisher: FakeEventPublisher | None = None,
    consumer: FakeEventConsumer | None = None,
    chunking: ChunkingService | None = None,
    **use_case_kwargs: object,
) -> tuple[SummarizeUseCase, FakeEventPublisher, FakeEventConsumer, FakeLLMProvider]:
    """Compone el caso de uso completo con doblos en memoria."""
    fake_llm = llm or FakeLLMProvider(responses=["Resumen generado"])
    fake_publisher = publisher or FakeEventPublisher()
    fake_consumer = consumer or FakeEventConsumer()
    use_case = SummarizeUseCase(
        llm=fake_llm,
        publisher=fake_publisher,
        chunking=chunking or ChunkingService(),
        max_retries=use_case_kwargs.pop("max_retries", 2),
        llm_timeout_s=use_case_kwargs.pop("llm_timeout_s", 5.0),
        backoff_base_s=use_case_kwargs.pop("backoff_base_s", 0.01),
        backoff_cap_s=use_case_kwargs.pop("backoff_cap_s", 0.02),
        **use_case_kwargs,
    )
    return use_case, fake_publisher, fake_consumer, fake_llm
