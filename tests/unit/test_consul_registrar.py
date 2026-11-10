"""Tests del adaptador de registro en Consul (API HTTP del agente)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from src.infrastructure.config import Settings
from src.infrastructure.discovery.consul import ConsulServiceRegistrar

Handler = Callable[[httpx.Request], httpx.Response]


def make_registrar(
    handler: Handler,
    **settings_overrides: object,
) -> tuple[ConsulServiceRegistrar, list[httpx.Request]]:
    """Crea un registrar con un ``httpx.MockTransport`` que captura los requests."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    settings = Settings(_env_file=None, **settings_overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    return ConsulServiceRegistrar(client, settings=settings), captured


def ok_handler(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200)


class TestRegister:
    async def test_register_puts_payload_against_agent_api(self) -> None:
        registrar, captured = make_registrar(ok_handler)

        await registrar.register()

        assert len(captured) == 1
        request = captured[0]
        assert request.method == "PUT"
        assert str(request.url) == "http://localhost:8500/v1/agent/service/register"
        payload = json.loads(request.content)
        assert payload["ID"] == registrar.service_id
        assert payload["Name"] == "pdf-extractext-resumidor"
        assert payload["Address"] == "0.0.0.0"
        assert payload["Port"] == 8080
        assert payload["Check"]["HTTP"] == "http://0.0.0.0:8080/health"
        assert payload["Check"]["Interval"] == "10s"
        assert payload["Check"]["DeregisterCriticalServiceAfter"] == "1m"

    async def test_register_uses_settings_overrides(self) -> None:
        registrar, captured = make_registrar(
            ok_handler,
            consul_host="consul.internal",
            consul_port=8501,
            service_name="resumidor-prod",
            service_host="10.0.0.5",
            service_port=9090,
        )

        await registrar.register()

        request = captured[0]
        assert str(request.url) == "http://consul.internal:8501/v1/agent/service/register"
        payload = json.loads(request.content)
        assert payload["Name"] == "resumidor-prod"
        assert payload["Address"] == "10.0.0.5"
        assert payload["Port"] == 9090
        assert payload["Check"]["HTTP"] == "http://10.0.0.5:9090/health"

    async def test_register_raises_when_consul_fails(self) -> None:
        registrar, _ = make_registrar(lambda _: httpx.Response(500))

        with pytest.raises(httpx.HTTPStatusError):
            await registrar.register()


class TestDeregister:
    async def test_deregister_uses_registered_service_id(self) -> None:
        registrar, captured = make_registrar(ok_handler)
        await registrar.register()
        await registrar.deregister()

        assert len(captured) == 2
        request = captured[1]
        assert request.method == "PUT"
        assert (
            str(request.url)
            == f"http://localhost:8500/v1/agent/service/deregister/{registrar.service_id}"
        )

    async def test_deregister_raises_when_consul_fails(self) -> None:
        registrar, _ = make_registrar(lambda _: httpx.Response(503))

        with pytest.raises(httpx.HTTPStatusError):
            await registrar.deregister()


class TestServiceId:
    def test_service_id_is_prefixed_with_service_name(self) -> None:
        registrar, _ = make_registrar(ok_handler, service_name="resumidor-prod")
        assert registrar.service_id.startswith("resumidor-prod-")

    def test_service_id_is_unique_per_instance(self) -> None:
        first, _ = make_registrar(ok_handler)
        second, _ = make_registrar(ok_handler)
        assert first.service_id != second.service_id
