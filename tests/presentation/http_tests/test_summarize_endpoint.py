"""Tests de contrato para el endpoint HTTP POST /summarize (workers.md)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from tests.fakes import FakeLLMProvider

from src.application.summarize_use_case import SummarizeUseCase
from src.domain.exceptions import DocumentTooLongError, EmptyTextError
from src.domain.models import SummaryResult
from src.presentation.http.dependencies import get_use_case
from src.presentation.http.main import app


@pytest.fixture
def mock_llm() -> FakeLLMProvider:
    return FakeLLMProvider(responses=["Resumen generado por el modelo."], max_context_tokens=128_000)


@pytest.fixture
def mock_use_case(mock_llm: FakeLLMProvider) -> AsyncMock:
    """Mock del caso de uso que devuelve un resultado controlado."""
    mock = AsyncMock(spec=SummarizeUseCase)
    mock.execute.return_value = SummaryResult(
        document_id=uuid.uuid4(),
        summary="Resumen generado por el modelo.",
        model_used="fake-model-1",
        processing_time_ms=1500,
    )
    return mock


@pytest.fixture
def client(mock_use_case: AsyncMock) -> AsyncClient:
    """Cliente HTTP de prueba con use_case mockeado."""
    app.dependency_overrides[get_use_case] = lambda: mock_use_case
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def make_valid_request(text: str = "Texto a resumir para la prueba.") -> dict:
    return {
        "document_id": str(uuid.uuid4()),
        "extracted_text": text,
        "options": {"length": "medium", "tone": "neutral", "language": "es"},
    }


class TestSummarizeEndpoint200:
    """POST /summarize → 200 OK con {summary, summary_time_ms}."""

    async def test_valid_request_returns_200_with_correct_shape(
        self, client: AsyncClient, mock_use_case: AsyncMock
    ) -> None:
        payload = make_valid_request()
        response = await client.post("/summarize", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "summary_time_ms" in data
        assert isinstance(data["summary"], str)
        assert isinstance(data["summary_time_ms"], int)
        assert data["summary_time_ms"] >= 0
        assert data["summary"] == "Resumen generado por el modelo."

    async def test_response_time_matches_use_case(
        self, client: AsyncClient, mock_use_case: AsyncMock
    ) -> None:
        mock_use_case.execute.return_value = SummaryResult(
            document_id=uuid.uuid4(),
            summary="Test summary",
            model_used="test-model",
            processing_time_ms=2500,
        )
        payload = make_valid_request()
        response = await client.post("/summarize", json=payload)

        assert response.status_code == 200
        assert response.json()["summary_time_ms"] == 2500

    async def test_without_options_field(self, client: AsyncClient) -> None:
        payload = {"document_id": str(uuid.uuid4()), "extracted_text": "Texto sin opciones."}
        response = await client.post("/summarize", json=payload)

        assert response.status_code == 200
        assert "summary" in response.json()


class TestSummarizeEndpoint422:
    """POST /summarize → 422 con {reason, message} según workers.md."""

    async def test_document_too_long_returns_422_with_reason(
        self, client: AsyncClient, mock_use_case: AsyncMock
    ) -> None:
        mock_use_case.execute.side_effect = DocumentTooLongError(
            "document_too_long: estimated 150000 tokens exceeds limit 128000"
        )
        payload = make_valid_request("x " * 100000)  # texto muy largo
        response = await client.post("/summarize", json=payload)

        assert response.status_code == 422
        data = response.json()
        assert data["reason"] == "document_too_long"
        assert "message" in data
        assert isinstance(data["message"], str)

    async def test_empty_text_returns_422_with_reason_empty_text(
        self, client: AsyncClient, mock_use_case: AsyncMock
    ) -> None:
        mock_use_case.execute.side_effect = EmptyTextError("empty_text: extracted_text has no content")
        payload = make_valid_request("   \n  ")  # solo whitespace
        response = await client.post("/summarize", json=payload)

        assert response.status_code == 422
        data = response.json()
        assert data["reason"] == "empty_text"
        assert "message" in data

    async def test_invalid_payload_returns_422(
        self, client: AsyncClient
    ) -> None:
        """FastAPI valida el payload antes de llamar al use_case.
        El formato de error es el estándar de Pydantic/FastAPI, no el nuestro."""
        payload = {"extracted_text": "Texto sin document_id"}  # falta document_id
        response = await client.post("/summarize", json=payload)

        assert response.status_code == 422
        # FastAPI devuelve errores de validación con formato estándar
        data = response.json()
        assert "detail" in data  # Formato estándar de FastAPI


class TestSummarizeEndpoint503:
    """POST /summarize → 503 para otros errores de dominio."""

    async def test_other_domain_error_returns_503(
        self, client: AsyncClient, mock_use_case: AsyncMock
    ) -> None:
        from src.domain.exceptions import DomainError

        class CustomDomainError(DomainError):
            code = "custom_error"

        mock_use_case.execute.side_effect = CustomDomainError("algo falló")
        payload = make_valid_request()
        response = await client.post("/summarize", json=payload)

        assert response.status_code == 503
        data = response.json()
        assert data["reason"] == "summarizer_unavailable"


class TestHealthEndpoint:
    """GET /health → 200 OK."""

    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
