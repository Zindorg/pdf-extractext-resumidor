"""Pruebas del filtro de privacidad y del setup de logging."""

from __future__ import annotations

import logging

from src.presentation.worker.logging_setup import (
    REDACTED,
    PrivacyFilter,
    setup_logging,
)


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="summarizer.application",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="evento",
        args=(),
        exc_info=None,
    )
    record.__dict__.update(extra)
    return record


class TestPrivacyFilter:
    def test_redacts_sensitive_extras(self) -> None:
        record = make_record(extracted_text="TEXTO", summary="RESUMEN", document_id="doc-1")
        filter_ = PrivacyFilter()

        assert filter_.filter(record) is True
        assert record.extracted_text == REDACTED
        assert record.summary == REDACTED
        assert record.document_id == "doc-1"

    def test_redacts_sensitive_dict_args(self) -> None:
        record = make_record()
        record.args = {"chunk": "PARCIAL", "attempt": 2}
        PrivacyFilter().filter(record)
        assert record.args == {"chunk": REDACTED, "attempt": 2}

    def test_ignores_none_values(self) -> None:
        record = make_record(summary=None)
        PrivacyFilter().filter(record)
        assert record.summary is None

    def test_always_allows_record(self) -> None:
        assert PrivacyFilter().filter(make_record()) is True


class TestSetupLogging:
    def test_installs_handler_and_level(self) -> None:
        setup_logging("DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert root.handlers

    def test_attaches_filter_to_summarizer_loggers(self) -> None:
        setup_logging("INFO")
        for name in (
            "summarizer.worker",
            "summarizer.application",
            "summarizer.infrastructure.llm",
        ):
            logger = logging.getLogger(name)
            assert any(isinstance(f, PrivacyFilter) for f in logger.filters), name

    def test_records_are_redacted_before_handlers(self) -> None:
        setup_logging("INFO")
        logger = logging.getLogger("summarizer.worker")
        record = make_record(summary="RESUMEN_SECRETO")

        logger.handle(record)

        assert record.summary == REDACTED

    def test_silences_httpx_noise(self) -> None:
        setup_logging("INFO")
        assert logging.getLogger("httpx").level == logging.WARNING

    def test_does_not_touch_unrelated_loggers(self) -> None:
        setup_logging("INFO")
        other = logging.getLogger("otro.servicio")
        assert not any(isinstance(f, PrivacyFilter) for f in other.filters)
