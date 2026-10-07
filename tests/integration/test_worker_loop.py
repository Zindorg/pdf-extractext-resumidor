"""Prueba de integración del loop del worker (con dobles, sin Redis real)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.application.chunking_service import ChunkingService
from src.application.summarize_use_case import SummarizeUseCase
from src.infrastructure.config import Settings
from src.presentation.worker import main as worker_main
from src.presentation.worker.main import WorkerComponents, run
from tests.fakes import (
    FakeEventConsumer,
    FakeEventPublisher,
    FakeLLMProvider,
    make_message,
    make_payload,
)


class StopLoop(Exception):
    """Señal interna para salir del loop de consumo en el test."""


class OneShotConsumer(FakeEventConsumer):
    """Falla transitoriamente, entrega la cola pendiente y corta el loop."""

    def __init__(self, messages=None, transient_failures: int = 1) -> None:
        super().__init__(messages)
        self.transient_failures = transient_failures

    async def read(self, count: int = 1) -> list:
        if self.transient_failures:
            self.transient_failures -= 1
            raise RedisTimeoutError("timeout transitorio")
        messages = await super().read(count)
        if messages:
            return messages
        raise StopLoop("cola vacía")


async def test_run_loop_processes_and_releases_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, read_count=5, block_ms=10)
    consumer = OneShotConsumer([make_message(make_payload("Texto de prueba."), message_id="900-1")])
    publisher = FakeEventPublisher()
    llm = FakeLLMProvider(responses=["Resumen del loop"])
    redis_client = AsyncMock()
    use_case = SummarizeUseCase(
        llm=llm,
        publisher=publisher,
        chunking=ChunkingService(),
        backoff_base_s=0.01,
        backoff_cap_s=0.02,
    )
    components = WorkerComponents(use_case, consumer, publisher, llm, redis_client)

    monkeypatch.setattr(worker_main, "build", lambda _settings: components)
    monkeypatch.setattr(worker_main, "setup_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_main, "_STOP_SLEEP_S", 0.01)

    with pytest.raises(StopLoop):
        await run(settings)

    assert consumer.group_ready is True
    assert consumer.transient_failures == 0
    assert consumer.acked == ["900-1"]
    assert len(publisher.published) == 1
    assert publisher.published[0].summary == "Resumen del loop"
    assert llm.closed is True
    redis_client.aclose.assert_awaited_once()
