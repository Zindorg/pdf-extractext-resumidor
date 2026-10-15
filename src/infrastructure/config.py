"""Configuración tipada del microservicio (pydantic-settings + .env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_PROVIDERS = frozenset({"openai", "openai-compatible", "ollama", "vllm", "lm-studio"})


class Settings(BaseSettings):
    """Ajustes de red, chunking, LLM y reintentos. Ninguno contiene texto."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Redis Streams
    redis_url: str = "redis://localhost:6379/0"
    input_stream: str = "events:text-extracted"
    output_stream: str = "events:summary-generated"
    dlq_stream: str = "events:summary-failed"
    consumer_group: str = "summarizer-group"
    consumer_name: str = "summarizer-worker-1"
    block_ms: int = Field(default=5000, ge=0)
    claim_min_idle_ms: int = Field(default=60_000, ge=0)
    read_count: int = Field(default=1, ge=1)

    # LLM (proveedor OpenAI-compatible)
    ai_provider: str = "openai"
    ai_api_key: str = ""
    ai_model: str = "gpt-4o-mini"
    ai_base_url: str = "https://api.openai.com/v1"

    # Chunking / Map-Reduce
    chunk_threshold: int = Field(default=3000, gt=0)
    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    max_output_tokens: int = Field(default=1024, gt=0)

    # Timeouts y reintentos
    llm_timeout_s: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=1)
    backoff_base_s: float = Field(default=1.0, gt=0)
    backoff_cap_s: float = Field(default=8.0, gt=0)

    # Token estimation
    tokens_per_word: float = Field(default=1.3, gt=0)

    # Context window (None = usar default del proveedor)
    max_context_tokens: int | None = Field(default=None, ge=1)

    # HTTP Server
    http_enabled: bool = True
    http_host: str = "0.0.0.0"
    http_port: int = Field(default=8080, ge=1, le=65535)

    # Observabilidad
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _check_chunking(self) -> Settings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self

    @model_validator(mode="after")
    def _normalize_provider(self) -> Settings:
        self.ai_provider = self.ai_provider.lower().strip()
        if self.ai_provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"ai_provider '{self.ai_provider}' not supported; "
                f"use one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
            )
        self.ai_base_url = self.ai_base_url.rstrip("/")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instancia cacheada de la configuración."""
    return Settings()
