"""Serialización de mensajes de stream: JSON <-> campos de Redis Streams."""

from __future__ import annotations

import json
from typing import Any

from src.domain.exceptions import InvalidPayloadError


def payload_from_fields(fields: dict[str, str]) -> dict[str, Any]:
    """Extrae el payload de entrada de un mensaje de stream.

    Acepta dos formatos (interop con el orquestador):
    - ``{"payload": "<json>"}`` (mensaje de un solo campo), o
    - los campos del contrato directamente sobre el hash del stream.
    """
    raw = fields.get("payload")
    if raw is None:
        return dict(fields)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise InvalidPayloadError("invalid_payload: payload is not valid JSON") from None
    if not isinstance(payload, dict):
        raise InvalidPayloadError("invalid_payload: payload must be a JSON object")
    return payload


def failure_fields(document_id: str, error_code: str) -> dict[str, str]:
    """Campos del dead-letter: solo identificadores y códigos de error."""
    return {
        "document_id": document_id,
        "error_code": error_code,
        "status": "failed",
    }
