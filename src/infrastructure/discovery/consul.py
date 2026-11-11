"""Adaptador de Service Discovery contra la API HTTP de HashiCorp Consul.

Registra el microservicio en el agente Consul al arrancar (con health check
HTTP sobre ``/health``) y lo desregistra al apagar. El cliente ``httpx`` se
inyecta para mantener el adaptador testeable sin red.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import httpx

from src.infrastructure.config import Settings

logger = logging.getLogger("summarizer.infrastructure.consul")

_REGISTER_PATH = "/v1/agent/service/register"
_DEREGISTER_PATH = "/v1/agent/service/deregister/{service_id}"
_CHECK_INTERVAL_S = "10s"
_DEREGISTER_CRITICAL_AFTER = "1m"


class ConsulServiceRegistrar:
    """Registra/desregistra el servicio en el agente Consul vía HTTP API.

    Endpoints usados (agente local, puerto 8500 por defecto):
    - ``PUT /v1/agent/service/register``
    - ``PUT /v1/agent/service/deregister/{service_id}``

    Si hay token ACL configurado, se envía en el header ``X-Consul-Token``.
    """

    def __init__(self, client: httpx.AsyncClient, *, settings: Settings) -> None:
        self._client = client
        self._base_url = f"http://{settings.consul_host}:{settings.consul_port}"
        self._service_id = f"{settings.service_name}-{uuid4().hex[:12]}"
        token = settings.consul_http_token.get_secret_value()
        self._headers = {"X-Consul-Token": token} if token else {}
        self._registration = {
            "ID": self._service_id,
            "Name": settings.service_name,
            "Address": settings.service_host,
            "Port": settings.service_port,
            "Check": {
                "HTTP": f"http://{settings.service_host}:{settings.service_port}/health",
                "Interval": _CHECK_INTERVAL_S,
                "DeregisterCriticalServiceAfter": _DEREGISTER_CRITICAL_AFTER,
            },
        }

    @property
    def service_id(self) -> str:
        """Identificador único de esta instancia ante Consul."""
        return self._service_id

    async def register(self) -> None:
        """Registra el servicio con su health check. Lanza si Consul falla."""
        response = await self._client.put(
            f"{self._base_url}{_REGISTER_PATH}",
            json=self._registration,
            headers=self._headers,
        )
        response.raise_for_status()
        logger.info(
            "service registered in consul",
            extra={
                "service_id": self._service_id,
                "service": self._registration["Name"],
                "consul": self._base_url,
            },
        )

    async def deregister(self) -> None:
        """Elimina el registro del servicio. Lanza si Consul falla."""
        response = await self._client.put(
            f"{self._base_url}{_DEREGISTER_PATH.format(service_id=self._service_id)}",
            headers=self._headers,
        )
        response.raise_for_status()
        logger.info("service deregistered from consul", extra={"service_id": self._service_id})

    async def aclose(self) -> None:
        """Libera los recursos de red del cliente inyectado."""
        await self._client.aclose()
