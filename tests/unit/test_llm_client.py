"""Pruebas del adaptador LLM OpenAI-compatible (sin red real, MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from src.domain.exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from src.infrastructure.llm_clients.openai_compatible import OpenAICompatibleLLM

SECRET = "SECRETO_NO_DEBE_APARECER_EN_ERRORES"


def make_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_llm(handler) -> tuple[OpenAICompatibleLLM, httpx.AsyncClient]:
    client = make_client(handler)
    llm = OpenAICompatibleLLM(
        api_key="test-key",
        model="modelo-test",
        base_url="https://api.example.com/v1/",
        timeout_s=5.0,
        client=client,
    )
    return llm, client


def success_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"model": "modelo-test", "choices": [{"message": {"content": "  Resumen  "}}]},
    )


class TestSuccess:
    async def test_returns_stripped_content(self) -> None:
        llm, client = make_llm(success_handler)
        try:
            result = await llm.generate_summary("texto", max_tokens=128)
        finally:
            await client.aclose()
        assert result == "Resumen"
        assert llm.model_name == "modelo-test"

    async def test_request_follows_openai_contract(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.content)
            return success_handler(request)

        llm, client = make_llm(handler)
        try:
            await llm.generate_summary("el texto", max_tokens=64)
        finally:
            await client.aclose()

        assert captured["path"] == "/v1/chat/completions"
        assert captured["authorization"] == "Bearer test-key"
        assert captured["body"]["model"] == "modelo-test"
        assert captured["body"]["max_tokens"] == 64
        assert captured["body"]["messages"][-1]["content"] == "el texto"

    async def test_max_tokens_is_omitted_when_none(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return success_handler(request)

        llm, client = make_llm(handler)
        try:
            await llm.generate_summary("el texto")
        finally:
            await client.aclose()
        assert "max_tokens" not in captured["body"]


class TestLifecycle:
    async def test_aclose_closes_owned_client(self) -> None:
        llm = OpenAICompatibleLLM(
            api_key="k", model="m", base_url="http://localhost:9", timeout_s=1.0
        )
        assert llm._owns_client is True

        await llm.aclose()

        assert llm._client.is_closed

    async def test_aclose_keeps_external_client(self) -> None:
        llm, client = make_llm(success_handler)

        await llm.aclose()

        assert not client.is_closed
        await client.aclose()


class TestFailures:
    @pytest.mark.parametrize("status", [500, 502, 503, 429, 408])
    async def test_retriable_status_codes(self, status: int) -> None:
        llm, client = make_llm(lambda request: httpx.Response(status, json={}))
        try:
            with pytest.raises(LLMProviderError) as excinfo:
                await llm.generate_summary(SECRET)
        finally:
            await client.aclose()
        assert excinfo.value.retriable is True
        assert SECRET not in str(excinfo.value)

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_non_retriable_status_codes(self, status: int) -> None:
        llm, client = make_llm(lambda request: httpx.Response(status, json={}))
        try:
            with pytest.raises(LLMProviderError) as excinfo:
                await llm.generate_summary(SECRET)
        finally:
            await client.aclose()
        assert excinfo.value.retriable is False
        assert SECRET not in str(excinfo.value)

    async def test_timeout_maps_to_llm_timeout_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        llm, client = make_llm(handler)
        try:
            with pytest.raises(LLMTimeoutError):
                await llm.generate_summary(SECRET)
        finally:
            await client.aclose()

    async def test_connection_error_maps_to_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network down")

        llm, client = make_llm(handler)
        try:
            with pytest.raises(LLMUnavailableError):
                await llm.generate_summary(SECRET)
        finally:
            await client.aclose()

    async def test_malformed_body_is_protocol_error(self) -> None:
        llm, client = make_llm(lambda request: httpx.Response(200, json={"choices": []}))
        try:
            with pytest.raises(LLMProviderError) as excinfo:
                await llm.generate_summary(SECRET)
        finally:
            await client.aclose()
        assert excinfo.value.retriable is False

    async def test_empty_completion_is_retriable(self) -> None:
        body = {"choices": [{"message": {"content": "   "}}]}
        llm, client = make_llm(lambda request: httpx.Response(200, json=body))
        try:
            with pytest.raises(LLMProviderError) as excinfo:
                await llm.generate_summary(SECRET)
        finally:
            await client.aclose()
        assert excinfo.value.retriable is True
        assert SECRET not in str(excinfo.value)
