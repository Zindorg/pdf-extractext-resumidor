"""Pruebas del caso de uso de resumen (single-shot, Map-Reduce, reintentos)."""

from __future__ import annotations

import logging

import pytest

from src.application.chunking_service import ChunkingService
from src.application.summarize_use_case import SummarizeUseCase
from src.domain.exceptions import (
    DocumentTooLongError,
    EmptyTextError,
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
    SummarizationFailedError,
)
from src.domain.models import SummaryRequest
from tests.fakes import FakeEventPublisher, FakeLLMProvider, make_payload

SECRET = "INFORMACION_CONFIDENCIAL_NO_LOGUEAR"
FAST_BACKOFF = {"backoff_base_s": 0.01, "backoff_cap_s": 0.02}


def build_use_case(
    llm: FakeLLMProvider,
    publisher: FakeEventPublisher,
    *,
    chunking: ChunkingService | None = None,
    max_retries: int = 3,
    llm_timeout_s: float = 5.0,
    max_context_tokens: int = 128_000,
    tokens_per_word: float = 1.3,
    **extra: object,
) -> SummarizeUseCase:
    kwargs = {
        "llm": llm,
        "publisher": publisher,
        "chunking": chunking or ChunkingService(),
        "max_retries": max_retries,
        "llm_timeout_s": llm_timeout_s,
        "max_context_tokens": max_context_tokens,
        "tokens_per_word": tokens_per_word,
        **FAST_BACKOFF,
        **extra,
    }
    return SummarizeUseCase(**kwargs)


def make_request(text: str, **extra: object) -> SummaryRequest:
    return SummaryRequest.from_payload(make_payload(text, **extra))


class TestSingleShot:
    async def test_short_text_uses_one_llm_call_and_publishes(self) -> None:
        llm = FakeLLMProvider(responses=["Resumen corto"])
        publisher = FakeEventPublisher()
        use_case = build_use_case(llm, publisher)

        result = await use_case.execute(make_request("Texto corto de prueba."))

        assert llm.call_count == 1
        assert len(publisher.published) == 1
        assert result.summary == "Resumen corto"
        assert result.model_used == "fake-model-1"
        assert result.processing_time_ms >= 0
        assert publisher.published[0] is result

    async def test_prompt_contains_text_and_options(self) -> None:
        llm = FakeLLMProvider()
        use_case = build_use_case(llm, FakeEventPublisher())
        request = SummaryRequest.from_payload(
            make_payload("Contenido del documento.", options={"length": "corto", "language": "es"})
        )

        await use_case.execute(request)

        prompt, max_tokens = llm.calls[0]
        assert "Contenido del documento." in prompt
        assert "Longitud deseada: corto." in prompt
        assert "Idioma de salida: es." in prompt
        assert max_tokens == 1024

    async def test_max_output_tokens_is_forwarded(self) -> None:
        llm = FakeLLMProvider()
        use_case = build_use_case(llm, FakeEventPublisher(), max_output_tokens=256)
        await use_case.execute(make_request("Texto."))
        assert llm.calls[0][1] == 256

    async def test_empty_text_is_rejected_without_llm_call(self) -> None:
        llm = FakeLLMProvider()
        publisher = FakeEventPublisher()
        use_case = build_use_case(llm, publisher)

        with pytest.raises(EmptyTextError):
            await use_case.execute(make_request("   \n  "))

        assert llm.call_count == 0
        assert publisher.published == []
        assert publisher.failures == []


class TestMapReduce:
    async def test_long_text_triggers_map_then_reduce(self) -> None:
        llm = FakeLLMProvider()
        publisher = FakeEventPublisher()
        chunking = ChunkingService(threshold_words=10, chunk_size=20, overlap_words=0)
        use_case = build_use_case(llm, publisher, chunking=chunking)
        text = " ".join(f"palabra{i}." for i in range(80))

        await use_case.execute(make_request(text))

        assert llm.call_count >= 4
        map_prompts = [prompt for prompt, _ in llm.calls[:-1]]
        reduce_prompt, _ = llm.calls[-1]
        assert all("Fragmento:" in prompt for prompt in map_prompts)
        assert "Resúmenes parciales:" in reduce_prompt
        assert "palabra0." in map_prompts[0]
        assert len(publisher.published) == 1

    async def test_partial_summaries_feed_the_reduce_step(self) -> None:
        llm = FakeLLMProvider(responses=["Parcial uno.", "Parcial dos.", "Resumen final."])
        chunking = ChunkingService(threshold_words=5, chunk_size=6, overlap_words=0)
        use_case = build_use_case(llm, FakeEventPublisher(), chunking=chunking)
        text = " ".join(f"palabra{i}." for i in range(12))

        result = await use_case.execute(make_request(text))

        reduce_prompt = llm.calls[-1][0]
        assert "Parcial uno." in reduce_prompt
        assert "Parcial dos." in reduce_prompt
        assert result.summary == "Resumen final."


class TestRetriesAndBackoff:
    async def test_transient_failures_are_retried_until_success(self) -> None:
        llm = FakeLLMProvider(
            failures=[LLMUnavailableError(), LLMTimeoutError()],
            responses=["Ok tras reintentos"],
        )
        use_case = build_use_case(llm, FakeEventPublisher(), max_retries=3)

        result = await use_case.execute(make_request("Texto."))

        assert llm.call_count == 3
        assert result.summary == "Ok tras reintentos"

    async def test_retries_are_exhausted_and_raised(self) -> None:
        llm = FakeLLMProvider(failures=[LLMUnavailableError()] * 5)
        publisher = FakeEventPublisher()
        use_case = build_use_case(llm, publisher, max_retries=3)

        with pytest.raises(SummarizationFailedError) as excinfo:
            await use_case.execute(make_request("Texto."))

        assert llm.call_count == 3
        assert excinfo.value.cause_code == "llm_unavailable"
        assert publisher.published == []

    async def test_non_retriable_error_fails_fast(self) -> None:
        llm = FakeLLMProvider(
            failures=[LLMProviderError("llm_http_error: status=400", retriable=False)]
        )
        use_case = build_use_case(llm, FakeEventPublisher(), max_retries=5)

        with pytest.raises(SummarizationFailedError) as excinfo:
            await use_case.execute(make_request("Texto."))

        assert llm.call_count == 1
        assert excinfo.value.cause_code == "llm_error"

    async def test_timeout_is_enforced_per_attempt(self) -> None:
        llm = FakeLLMProvider(delay_s=1.0)
        use_case = build_use_case(llm, FakeEventPublisher(), max_retries=2, llm_timeout_s=0.05)

        with pytest.raises(SummarizationFailedError) as excinfo:
            await use_case.execute(make_request("Texto."))

        assert llm.call_count == 2
        assert excinfo.value.cause_code == "llm_timeout"


class TestPrivacy:
    async def test_no_content_is_ever_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        llm = FakeLLMProvider(responses=[f"Resumen de {SECRET}"])
        use_case = build_use_case(llm, FakeEventPublisher())

        await use_case.execute(make_request(SECRET))

        assert SECRET not in caplog.text
        for record in caplog.records:
            rendered = " ".join(str(value) for value in record.__dict__.values())
            assert SECRET not in rendered

    async def test_logs_only_metadata(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        use_case = build_use_case(FakeLLMProvider(), FakeEventPublisher())
        request = make_request("Documento con palabras.")

        await use_case.execute(request)

        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert infos
        for record in infos:
            assert getattr(record, "document_id", None) == str(request.document_id)


class TestUseCaseConfiguration:
    def test_invalid_parameters_raise(self) -> None:
        kwargs = {
            "llm": FakeLLMProvider(),
            "publisher": FakeEventPublisher(),
            "chunking": ChunkingService(),
        }
        with pytest.raises(ValueError):
            SummarizeUseCase(**kwargs, max_retries=0)
        with pytest.raises(ValueError):
            SummarizeUseCase(**kwargs, llm_timeout_s=0)

    async def test_options_are_forwarded_to_both_steps(self) -> None:
        llm = FakeLLMProvider()
        chunking = ChunkingService(threshold_words=5, chunk_size=6, overlap_words=0)
        use_case = build_use_case(llm, FakeEventPublisher(), chunking=chunking)
        request = SummaryRequest.from_payload(
            make_payload(
                " ".join(f"palabra{i}." for i in range(12)),
                options={"tone": "formal"},
            )
        )

        await use_case.execute(request)

        assert all("Tono: formal." in prompt for prompt, _ in llm.calls)


class TestDocumentTooLong:
    """Pruebas para DocumentTooLongError (workers.md: reason=document_too_long)."""

    async def test_estimated_tokens_exceed_limit_raises(self) -> None:
        """Si la estimación de tokens supera el límite, lanza DocumentTooLongError."""
        llm = FakeLLMProvider(max_context_tokens=100)  # límite muy bajo
        publisher = FakeEventPublisher()
        use_case = build_use_case(
            llm,
            publisher,
            max_context_tokens=100,
            tokens_per_word=1.3,
            **FAST_BACKOFF,
        )
        # ~80 palabras * 1.3 = 104 tokens > 100
        text = " ".join(f"palabra{i}." for i in range(80))

        with pytest.raises(DocumentTooLongError) as excinfo:
            await use_case.execute(make_request(text))

        assert excinfo.value.code == "document_too_long"
        assert llm.call_count == 0
        assert publisher.published == []

    async def test_exact_limit_allows_processing(self) -> None:
        """Si la estimación está justo en el límite, procesa normalmente."""
        llm = FakeLLMProvider(responses=["Resumen OK"], max_context_tokens=100)
        publisher = FakeEventPublisher()
        use_case = build_use_case(
            llm,
            publisher,
            max_context_tokens=100,
            tokens_per_word=1.0,  # 100 palabras = 100 tokens
            **FAST_BACKOFF,
        )
        text = " ".join(f"palabra{i}." for i in range(100))

        result = await use_case.execute(make_request(text))

        assert result.summary == "Resumen OK"
        assert llm.call_count == 1

    async def test_provider_context_error_mapped_to_document_too_long(self) -> None:
        """Error 413/context length del proveedor se mapea a DocumentTooLongError."""
        llm = FakeLLMProvider(
            failures=[LLMProviderError("llm_http_error: status=413 context length exceeded")],
            max_context_tokens=128_000,
        )
        publisher = FakeEventPublisher()
        use_case = build_use_case(
            llm,
            publisher,
            max_context_tokens=128_000,
            **FAST_BACKOFF,
        )

        with pytest.raises(DocumentTooLongError) as excinfo:
            await use_case.execute(make_request("Texto corto."))

        assert excinfo.value.code == "document_too_long"
        assert llm.call_count == 1

    async def test_provider_max_tokens_error_mapped(self) -> None:
        """Error 'max_tokens' del proveedor se mapea a DocumentTooLongError."""
        llm = FakeLLMProvider(
            failures=[LLMProviderError("llm_http_error: max_tokens exceeded")],
            max_context_tokens=128_000,
        )
        publisher = FakeEventPublisher()
        use_case = build_use_case(
            llm,
            publisher,
            max_context_tokens=128_000,
            **FAST_BACKOFF,
        )

        with pytest.raises(DocumentTooLongError) as excinfo:
            await use_case.execute(make_request("Texto corto."))

        assert excinfo.value.code == "document_too_long"

    async def test_use_case_validation_invalid_max_context_tokens(self) -> None:
        """Parámetro max_context_tokens < 1 levanta ValueError."""
        from src.application.chunking_service import ChunkingService
        from src.domain.ports import LLMProviderInterface

        class DummyLLM(LLMProviderInterface):
            @property
            def model_name(self) -> str:
                return "dummy"

            @property
            def max_context_tokens(self) -> int:
                return 128_000

            async def generate_summary(self, text: str, max_tokens: int | None = None) -> str:
                return "dummy"

            async def aclose(self) -> None:
                pass

        with pytest.raises(ValueError, match="max_context_tokens must be >= 1"):
            SummarizeUseCase(
                llm=DummyLLM(),
                publisher=FakeEventPublisher(),
                chunking=ChunkingService(),
                max_context_tokens=0,
            )

    async def test_use_case_validation_invalid_tokens_per_word(self) -> None:
        """Parámetro tokens_per_word <= 0 levanta ValueError."""
        from src.application.chunking_service import ChunkingService
        from src.domain.ports import LLMProviderInterface

        class DummyLLM(LLMProviderInterface):
            @property
            def model_name(self) -> str:
                return "dummy"

            @property
            def max_context_tokens(self) -> int:
                return 128_000

            async def generate_summary(self, text: str, max_tokens: int | None = None) -> str:
                return "dummy"

            async def aclose(self) -> None:
                pass

        with pytest.raises(ValueError, match="tokens_per_word must be > 0"):
            SummarizeUseCase(
                llm=DummyLLM(),
                publisher=FakeEventPublisher(),
                chunking=ChunkingService(),
                max_context_tokens=128_000,
                tokens_per_word=0,
            )
