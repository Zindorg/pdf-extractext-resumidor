"""Tests del ciclo de vida HTTP: registro/desregistro en Consul (fail-open)."""

from __future__ import annotations

from typing import ClassVar

import httpx
import pytest
from tests.fakes import FakeLLMProvider

from src.infrastructure.config import Settings
from src.presentation.http import dependencies
from src.presentation.http.dependencies import _app_state, lifespan
from src.presentation.http.main import app


class FakeRegistrar:
    """Doble del registrar de Consul: registra llamadas y fallos configurables."""

    created: ClassVar[list[FakeRegistrar]] = []
    fail_on_register: ClassVar[bool] = False
    fail_on_deregister: ClassVar[bool] = False

    def __init__(self, client: httpx.AsyncClient, *, settings: Settings) -> None:
        self.registered = False
        self.deregistered = False
        self.closed = False
        FakeRegistrar.created.append(self)

    @property
    def service_id(self) -> str:
        return "fake-service-id"

    async def register(self) -> None:
        if FakeRegistrar.fail_on_register:
            raise RuntimeError("consul down")
        self.registered = True

    async def deregister(self) -> None:
        if FakeRegistrar.fail_on_deregister:
            raise RuntimeError("consul down")
        self.deregistered = True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fake_registrar():
    yield
    FakeRegistrar.created.clear()
    FakeRegistrar.fail_on_register = False
    FakeRegistrar.fail_on_deregister = False
    _app_state.consul = None
    _app_state.llm = None
    _app_state.use_case = None


@pytest.fixture
def wired_lifespan(monkeypatch: pytest.MonkeyPatch) -> FakeLLMProvider:
    """Lifespan con LLM fake, settings sin .env y registrar doble inyectado."""
    fake_llm = FakeLLMProvider()
    monkeypatch.setattr(dependencies, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(dependencies, "create_llm_provider", lambda *args, **kwargs: fake_llm)
    monkeypatch.setattr(dependencies, "ConsulServiceRegistrar", FakeRegistrar)
    return fake_llm


class TestLifespanConsul:
    async def test_registers_on_startup_and_deregisters_on_shutdown(
        self, wired_lifespan: FakeLLMProvider
    ) -> None:
        async with lifespan(app):
            registrar = FakeRegistrar.created[-1]
            assert registrar.registered
            assert _app_state.consul is registrar

        assert registrar.deregistered
        assert registrar.closed
        assert _app_state.consul is None
        assert wired_lifespan.closed
        assert _app_state.llm is None

    async def test_registration_failure_keeps_service_running(
        self, wired_lifespan: FakeLLMProvider
    ) -> None:
        """Fail-open: si Consul no responde, la app arranca igual."""
        FakeRegistrar.fail_on_register = True

        async with lifespan(app):
            registrar = FakeRegistrar.created[-1]
            assert not registrar.registered
            assert registrar.closed
            assert _app_state.consul is None
            assert _app_state.llm is wired_lifespan

        assert not registrar.deregistered

    async def test_deregistration_failure_does_not_break_shutdown(
        self, wired_lifespan: FakeLLMProvider
    ) -> None:
        FakeRegistrar.fail_on_deregister = True

        async with lifespan(app):
            registrar = FakeRegistrar.created[-1]
            assert registrar.registered

        assert not registrar.deregistered
        assert registrar.closed
        assert _app_state.consul is None
        assert _app_state.llm is None

    async def test_consul_disabled_skips_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_llm = FakeLLMProvider()
        monkeypatch.setattr(
            dependencies, "get_settings", lambda: Settings(_env_file=None, consul_enabled=False)
        )
        monkeypatch.setattr(dependencies, "create_llm_provider", lambda *args, **kwargs: fake_llm)
        monkeypatch.setattr(dependencies, "ConsulServiceRegistrar", FakeRegistrar)

        async with lifespan(app):
            assert FakeRegistrar.created == []
            assert _app_state.consul is None
            assert _app_state.llm is fake_llm

        assert _app_state.llm is None
