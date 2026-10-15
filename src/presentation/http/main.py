"""API HTTP del Resumidor: POST /summarize (workers.md)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from src.application.summarize_use_case import SummarizeUseCase
from src.domain.exceptions import DomainError
from src.domain.models import SummaryHttpResponse
from src.infrastructure.config import get_settings
from src.presentation.http.dependencies import lifespan as http_lifespan, get_use_case
from src.presentation.http.error_handlers import domain_error_handler, generic_error_handler
from src.presentation.http.schemas import SummarizeHttpRequest, SummarizeHttpResponse


app = FastAPI(
    title="Summarizer",
    version="1.0.0",
    lifespan=http_lifespan,
)
app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(Exception, generic_error_handler)


@app.post("/summarize", response_model=SummarizeHttpResponse)
async def summarize_endpoint(
    request: SummarizeHttpRequest,
    use_case: SummarizeUseCase = Depends(get_use_case),
) -> SummarizeHttpResponse:
    """Genera un resumen del texto extraído.

    Contrato: workers.md §Resumidor — POST /summarize
    - Request: {document_id, extracted_text, options?}
    - Response 200: {summary, summary_time_ms}
    - Response 422: {reason, message} — reason ∈ {document_too_long, empty_text, invalid_payload}
    """
    domain_request = request.to_domain()
    result = await use_case.execute(domain_request)
    domain_resp = SummaryHttpResponse.from_domain(result)
    return SummarizeHttpResponse.from_domain(domain_resp)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)
