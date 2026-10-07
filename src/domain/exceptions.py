"""Excepciones de dominio tipadas.

Todas llevan un ``code`` estable y apto para logging/DLQ (nunca contienen
texto del documento ni resúmenes).
"""

from __future__ import annotations


class DomainError(Exception):
    """Error base del dominio."""

    code: str = "domain_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class InvalidPayloadError(DomainError):
    """Payload de entrada mal formado. El mensaje es sanitizado (sin inputs)."""

    code = "invalid_payload"


class EmptyTextError(DomainError):
    """El documento no contiene texto extraíble."""

    code = "empty_text"


class ChunkingError(DomainError):
    """No es posible segmentar el texto."""

    code = "chunking_error"


class LLMProviderError(DomainError):
    """Fallo del proveedor de IA. ``retriable`` decide si aplica backoff."""

    code = "llm_error"
    retriable: bool = True

    def __init__(self, message: str | None = None, *, retriable: bool | None = None) -> None:
        super().__init__(message)
        if retriable is not None:
            self.retriable = retriable


class LLMTimeoutError(LLMProviderError):
    """El proveedor no respondió dentro del timeout configurado."""

    code = "llm_timeout"
    retriable = True


class LLMUnavailableError(LLMProviderError):
    """El proveedor no está alcanzable (red / 5xx / 429)."""

    code = "llm_unavailable"
    retriable = True


class SummarizationFailedError(DomainError):
    """Se agotaron los reintentos del caso de uso."""

    code = "summarization_failed"

    def __init__(self, message: str | None = None, *, cause_code: str = "unknown") -> None:
        self.cause_code = cause_code
        self.code = f"{self.code}:{cause_code}"
        super().__init__(message or self.code)
