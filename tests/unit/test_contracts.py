"""Pruebas de contratos de entrada/salida (ARCHITECTURE_SUMMARIZER.md §3)."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.domain.exceptions import InvalidPayloadError
from src.domain.models import SummaryOptions, SummaryRequest, SummaryResult

UUID1 = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


class TestInputContract:
    def test_valid_payload_is_accepted(self) -> None:
        payload = {
            "document_id": str(uuid4()),
            "extracted_text": "Hola mundo",
            "options": {"length": "corto", "tone": "neutral", "language": "es"},
        }
        request = SummaryRequest.from_payload(payload)
        assert request.extracted_text == "Hola mundo"
        assert request.options is not None
        assert request.options.length == "corto"

    def test_options_are_optional(self) -> None:
        payload = {"document_id": str(uuid4()), "extracted_text": "Hola"}
        request = SummaryRequest.from_payload(payload)
        assert request.options is None

    def test_word_count(self) -> None:
        payload = {"document_id": str(uuid4()), "extracted_text": "uno dos tres"}
        assert SummaryRequest.from_payload(payload).word_count == 3

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"extracted_text": "TEXTO_SECRETO"},
            {"document_id": "no-es-uuid", "extracted_text": "TEXTO_SECRETO"},
            {"document_id": UUID1, "extracted_text": "TEXTO_SECRETO"},
            {"document_id": 42, "extracted_text": "TEXTO_SECRETO"},
            {"document_id": str(uuid4()), "extracted_text": 123},
            {"document_id": str(uuid4()), "extracted_text": None},
            {
                "document_id": str(uuid4()),
                "extracted_text": "TEXTO_SECRETO",
                "options": "corto",
            },
        ],
    )
    def test_invalid_payload_raises_sanitized_error(self, payload: dict) -> None:
        with pytest.raises(InvalidPayloadError) as excinfo:
            SummaryRequest.from_payload(payload)
        assert excinfo.value.code == "invalid_payload"
        assert "TEXTO_SECRETO" not in str(excinfo.value)

    def test_error_does_not_leak_nested_input_values(self) -> None:
        payload = {
            "document_id": str(uuid4()),
            "extracted_text": {"clave": "VALOR_SECRETO"},
        }
        with pytest.raises(InvalidPayloadError) as excinfo:
            SummaryRequest.from_payload(payload)
        assert "VALOR_SECRETO" not in str(excinfo.value)

    def test_direct_model_validation_raises_pydantic_error(self) -> None:
        with pytest.raises(ValidationError):
            SummaryRequest.model_validate({"document_id": "no-uuid", "extracted_text": "x"})


class TestOutputContract:
    def test_result_fields_match_contract(self) -> None:
        document_id = uuid4()
        result = SummaryResult(
            document_id=document_id,
            summary="Resumen breve",
            model_used="gpt-4o-mini",
            processing_time_ms=1234,
        )
        payload = result.to_payload()
        assert payload == {
            "document_id": str(document_id),
            "summary": "Resumen breve",
            "model_used": "gpt-4o-mini",
            "processing_time_ms": "1234",
        }

    def test_result_is_json_serializable(self) -> None:
        result = SummaryResult(
            document_id=uuid4(),
            summary="s",
            model_used="m",
            processing_time_ms=1,
        )
        encoded = json.dumps(result.to_payload())
        assert json.loads(encoded)["summary"] == "s"

    def test_processing_time_must_not_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            SummaryResult(
                document_id=uuid4(),
                summary="s",
                model_used="m",
                processing_time_ms=-1,
            )

    def test_options_defaults_to_none(self) -> None:
        assert SummaryOptions().length is None


class TestDomainExceptions:
    """Tests para excepciones de dominio (códigos estables para workers.md)."""

    def test_document_too_long_error_code(self) -> None:
        from src.domain.exceptions import DocumentTooLongError

        exc = DocumentTooLongError("document_too_long: text too long")
        assert exc.code == "document_too_long"
        assert exc.retriable is False

    def test_invalid_payload_error_code(self) -> None:
        from src.domain.exceptions import InvalidPayloadError

        exc = InvalidPayloadError("invalid_payload: missing field")
        assert exc.code == "invalid_payload"

    def test_empty_text_error_code(self) -> None:
        from src.domain.exceptions import EmptyTextError

        exc = EmptyTextError("empty_text: no content")
        assert exc.code == "empty_text"

    def test_llm_timeout_error_retriable(self) -> None:
        from src.domain.exceptions import LLMTimeoutError

        exc = LLMTimeoutError("timeout")
        assert exc.retriable is True

    def test_summarization_failed_error_includes_cause(self) -> None:
        from src.domain.exceptions import SummarizationFailedError

        exc = SummarizationFailedError("failed", cause_code="llm_timeout")
        assert exc.code == "summarization_failed:llm_timeout"
        assert exc.cause_code == "llm_timeout"
