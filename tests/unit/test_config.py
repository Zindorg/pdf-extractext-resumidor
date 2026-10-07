"""Pruebas de la configuración tipada."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.infrastructure.config import Settings, get_settings


class TestDefaults:
    def test_streams_and_group_defaults(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.input_stream == "events:text-extracted"
        assert settings.output_stream == "events:summary-generated"
        assert settings.dlq_stream == "events:summary-failed"
        assert settings.consumer_group == "summarizer-group"

    def test_chunking_defaults(self) -> None:
        settings = Settings(_env_file=None)
        assert (settings.chunk_threshold, settings.chunk_size, settings.chunk_overlap) == (
            3000,
            1000,
            100,
        )

    def test_timeout_and_retry_defaults(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.llm_timeout_s == 30.0
        assert settings.max_retries == 3


class TestEnvironmentOverrides:
    def test_env_vars_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_PROVIDER", "ollama")
        monkeypatch.setenv("AI_MODEL", "llama3")
        monkeypatch.setenv("CHUNK_THRESHOLD", "500")
        monkeypatch.setenv("CONSUMER_GROUP", "otro-grupo")
        settings = Settings(_env_file=None)
        assert settings.ai_provider == "ollama"
        assert settings.ai_model == "llama3"
        assert settings.chunk_threshold == 500
        assert settings.consumer_group == "otro-grupo"

    def test_base_url_is_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_BASE_URL", "http://localhost:11434/v1/")
        settings = Settings(_env_file=None)
        assert settings.ai_base_url == "http://localhost:11434/v1"


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"ai_provider": "cohere"},
            {"chunk_overlap": 1000, "chunk_size": 1000},
            {"llm_timeout_s": 0},
            {"max_retries": 0},
            {"block_ms": -1},
        ],
    )
    def test_invalid_settings_raise(self, kwargs: dict) -> None:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **kwargs)

    def test_unknown_env_vars_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VARIABLE_RARA", "valor")
        settings = Settings(_env_file=None)
        assert not hasattr(settings, "variable_rara")


class TestCache:
    def test_get_settings_is_cached(self) -> None:
        get_settings.cache_clear()
        first = get_settings()
        second = get_settings()
        assert first is second
        get_settings.cache_clear()
