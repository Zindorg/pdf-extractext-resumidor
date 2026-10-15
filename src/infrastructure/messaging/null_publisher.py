"""Publicador nulo para modo HTTP (no publica a streams)."""

from __future__ import annotations

from src.domain.models import SummaryResult
from src.domain.ports import EventPublisherInterface


class NullPublisher(EventPublisherInterface):
    """Publicador que descarta los resultados (para modo HTTP síncrono)."""

    async def publish(self, result: SummaryResult) -> str:
        return "http-mode"

    async def publish_failure(self, document_id: str, error_code: str) -> str:
        return "http-mode"
