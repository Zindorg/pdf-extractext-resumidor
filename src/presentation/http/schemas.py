"""Esquemas Pydantic para la API HTTP del Resumidor."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from src.domain.models import SummaryHttpResponse, SummaryOptions, SummaryRequest


class SummarizeHttpRequest(BaseModel):
    """Request del endpoint POST /summarize (workers.md §Resumidor)."""

    document_id: UUID
    extracted_text: str
    options: SummaryOptions | None = None

    def to_domain(self) -> SummaryRequest:
        return SummaryRequest(
            document_id=self.document_id,
            extracted_text=self.extracted_text,
            options=self.options,
        )


class SummarizeHttpResponse(BaseModel):
    """Response 200 OK del endpoint POST /summarize (workers.md §Resumidor)."""

    summary: str
    summary_time_ms: int = Field(ge=0)

    @classmethod
    def from_domain(cls, domain_resp: SummaryHttpResponse) -> "SummarizeHttpResponse":
        return cls(summary=domain_resp.summary, summary_time_ms=domain_resp.summary_time_ms)
