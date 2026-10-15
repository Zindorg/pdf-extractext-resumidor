"""Puertos abstractos (interfaces) del dominio.

Inversión de dependencias: la capa de aplicación/infraestructura depende de
estas interfaces, nunca al revés.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.models import StreamMessage, SummaryResult


class LLMProviderInterface(ABC):
    """Puerto del proveedor de IA."""

    @abstractmethod
    async def generate_summary(self, text: str, max_tokens: int | None = None) -> str:
        """Envía el texto al modelo de IA y retorna la respuesta generada."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identificador del modelo, usado para el campo ``model_used``."""

    @property
    @abstractmethod
    def max_context_tokens(self) -> int:
        """Límite de tokens de la ventana de contexto del modelo."""

    @abstractmethod
    async def aclose(self) -> None:
        """Libera los recursos de red del proveedor."""


class EventConsumerInterface(ABC):
    """Puerto del consumidor de eventos (Redis Streams)."""

    @abstractmethod
    async def ensure_group(self) -> None:
        """Crea el stream y el consumer group si no existen (idempotente)."""

    @abstractmethod
    async def read(self, count: int = 1) -> list[StreamMessage]:
        """Lee mensajes nuevos del grupo; lista vacía si no hay novedades."""

    @abstractmethod
    async def claim_stale(self) -> list[StreamMessage]:
        """Recupera mensajes pendientes con idle elevado (recuperación inicial)."""

    @abstractmethod
    async def ack(self, message_id: str) -> None:
        """Confirma el procesamiento del mensaje."""


class EventPublisherInterface(ABC):
    """Puerto de publicación de resultados."""

    @abstractmethod
    async def publish(self, result: SummaryResult) -> str:
        """Publica un resumen exitoso. Retorna el id del mensaje."""

    @abstractmethod
    async def publish_failure(self, document_id: str, error_code: str) -> str:
        """Publica un fallo en el stream de dead-letter (sin contenido)."""
