"""Cliente LLM genérico compatible con la API ``/chat/completions``.

Sirve para OpenAI, Ollama, vLLM, LM Studio y cualquier backend que exponga
el formato OpenAI. Nunca registra prompts ni respuestas en logs.
"""

from __future__ import annotations

import logging

import httpx

from src.application.prompts import system_prompt
from src.domain.exceptions import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from src.domain.ports import LLMProviderInterface

logger = logging.getLogger("summarizer.infrastructure.llm")

_RETRIABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

_DEFAULT_CONTEXT_TOKENS = {
    "openai": 128_000,
    "openai-compatible": 128_000,
    "vllm": 128_000,
    "lm-studio": 128_000,
    "ollama": 8_192,
}


class OpenAICompatibleLLM(LLMProviderInterface):
    """Adaptador HTTPX asíncrono sobre el contrato OpenAI chat/completions."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_s: float = 30.0,
        max_context_tokens: int | None = None,
        provider_type: str = "openai",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._provider_type = provider_type.lower()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))
        self._max_context_tokens = max_context_tokens or _DEFAULT_CONTEXT_TOKENS.get(
            self._provider_type, 128_000
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_context_tokens(self) -> int:
        return self._max_context_tokens

    async def generate_summary(self, text: str, max_tokens: int | None = None) -> str:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("llm_timeout: request exceeded configured timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"llm_unavailable: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise self._status_error(response.status_code)

        content = self._extract_content(response)
        logger.info(
            "llm response received",
            extra={"model": self._model, "status_code": response.status_code},
        )
        return content

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _status_error(self, status_code: int) -> LLMProviderError:
        retriable = status_code in _RETRIABLE_STATUS
        return LLMProviderError(
            f"llm_http_error: status={status_code}",
            retriable=retriable,
        )

    @staticmethod
    def _extract_content(response: httpx.Response) -> str:
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                "llm_protocol_error: unexpected response shape", retriable=False
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("llm_protocol_error: empty completion", retriable=True)
        return content.strip()
