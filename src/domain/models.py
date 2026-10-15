"""Modelos de dominio del servicio resumidor.

Contratos de entrada/salida definidos en ARCHITECTURE_SUMMARIZER.md §3.
Ningún modelo loguea ni expone el texto en su representación en cadenas.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.domain.exceptions import InvalidPayloadError

SENSITIVE_FIELDS = frozenset({"extracted_text", "summary", "text", "content"})


class SummaryOptions(BaseModel):
    """Parámetros opcionales de estilo del resumen (longitud, tono, idioma)."""

    length: str | None = None
    tone: str | None = None
    language: str | None = None


class SummaryRequest(BaseModel):
    """Contrato de entrada: texto plano pre-extraído + identificador."""

    document_id: UUID
    extracted_text: str
    options: SummaryOptions | None = None

    @field_validator("document_id")
    @classmethod
    def _must_be_uuidv4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("document_id must be a UUIDv4")
        return value

    @property
    def word_count(self) -> int:
        return len(self.extracted_text.split())

    @classmethod
    def from_payload(cls, payload: object) -> SummaryRequest:
        """Valida un payload crudo sanitizando el error (nunca incluye el texto)."""
        try:
            return cls.model_validate(payload)
        except ValidationError as exc:
            details = ",".join(
                ".".join(str(part) for part in error["loc"]) + ":" + error["type"]
                for error in exc.errors()
            )
            raise InvalidPayloadError(f"invalid_payload: {details}") from None


class SummaryResult(BaseModel):
    """Contrato de salida: resumen generado + metadatos de trazabilidad."""

    document_id: UUID
    summary: str
    model_used: str
    processing_time_ms: int = Field(ge=0)

    def to_payload(self) -> dict[str, str]:
        """Representación como campos de stream (igual al contrato JSON)."""
        return {
            "document_id": str(self.document_id),
            "summary": self.summary,
            "model_used": self.model_used,
            "processing_time_ms": str(self.processing_time_ms),
        }


class StreamMessage(BaseModel):
    """Mensaje crudo leído de un Redis Stream (aún sin validar)."""

    message_id: str
    fields: dict[str, str]


class SummaryHttpResponse(BaseModel):
    """Contrato de salida HTTP (workers.md §Resumidor): summary + summary_time_ms."""

    summary: str
    summary_time_ms: int = Field(ge=0)

    @classmethod
    def from_domain(cls, result: "SummaryResult") -> "SummaryHttpResponse":
        return cls(summary=result.summary, summary_time_ms=result.processing_time_ms)
