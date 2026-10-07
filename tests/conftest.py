"""Fixtures compartidas de la batería de pruebas."""

from __future__ import annotations

import pytest

from src.application.chunking_service import ChunkingService
from src.domain.models import SummaryRequest
from tests.fakes import (
    DEFAULT_TEXT,
    FakeEventConsumer,
    FakeEventPublisher,
    FakeLLMProvider,
    make_payload,
)


@pytest.fixture
def sample_payload() -> dict:
    return make_payload(DEFAULT_TEXT)


@pytest.fixture
def sample_request(sample_payload: dict) -> SummaryRequest:
    return SummaryRequest.from_payload(sample_payload)


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def fake_publisher() -> FakeEventPublisher:
    return FakeEventPublisher()


@pytest.fixture
def fake_consumer() -> FakeEventConsumer:
    return FakeEventConsumer()


@pytest.fixture
def chunking() -> ChunkingService:
    return ChunkingService(threshold_words=3000, chunk_size=1000, overlap_words=100)
