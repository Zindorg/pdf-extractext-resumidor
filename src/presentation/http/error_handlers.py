"""Manejadores de excepciones para la API HTTP."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from src.domain.exceptions import DomainError

_KNOWN_422_CODES = frozenset({"document_too_long", "empty_text", "invalid_payload"})


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    """Mapea DomainError a respuesta HTTP según workers.md."""
    if exc.code in _KNOWN_422_CODES:
        return JSONResponse(
            status_code=422,
            content={"reason": exc.code, "message": str(exc)},
        )
    # Otros errores de dominio -> 503 (servicio no disponible)
    return JSONResponse(
        status_code=503,
        content={"reason": "summarizer_unavailable", "message": str(exc)},
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Maneja excepciones no esperadas -> 500."""
    return JSONResponse(
        status_code=500,
        content={"reason": "internal_error", "message": "Internal server error"},
    )
