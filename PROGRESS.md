# Progreso de implementación — resumen truncado

## Fase 0 — Baseline
- Completada.
- Baseline: `154 passed`, `ruff check` OK.

## Fase 1 — Detección de truncamiento del proveedor
- Completada.
- Archivos: `src/domain/exceptions.py`, `src/infrastructure/llm_clients/openai_compatible.py`,
  `tests/unit/test_llm_client.py`.
- Resultado: `finish_reason` `length`/`max_tokens` lanza `LLMTruncatedError`; no se devuelve el parcial.

## Fase 2 — Configuración y reintento adaptativo
- Completada.
- Archivos: `src/infrastructure/config.py`, `src/application/summarize_use_case.py`,
  `src/presentation/http/dependencies.py`, `src/presentation/worker/main.py`,
  `tests/unit/test_config.py`, `tests/unit/test_summarize_use_case.py`.
- Resultado: `MAX_OUTPUT_TOKENS` default 4096, `MAX_OUTPUT_TOKENS_CAP` default 8192;
  reintento con mayor límite y fallo controlado `llm_truncated`.

## Fase 3 — Reduce jerárquico
- Completada.
- Archivos: `src/application/summarize_use_case.py`, `tests/fakes.py`,
  `tests/unit/test_summarize_use_case.py`.
- Resultado: reduce por mitades cuando no entra en contexto; profundidad máxima 5;
  al superarla lanza `DocumentTooLongError`.

## Fase 4 — `cause_code` en 503 y pruebas de integración
- Completada.
- Archivos: `src/presentation/http/error_handlers.py`,
  `tests/presentation/http_tests/test_summarize_endpoint.py`,
  `tests/integration/test_end_to_end.py`.
- Resultado: 503 conserva `reason`/`message` y agrega `cause_code` solo para
  `SummarizationFailedError`; truncamiento persistente va a DLQ sin publicar parcial.

## Fase 5 — Barrido de configuración y documentación
- Completada.
- Archivos: `.env`, `.env.example`, `README.md`.
- Resultado: no queda `MAX_OUTPUT_TOKENS=1024` en el repo; `MAX_OUTPUT_TOKENS_CAP`
  documentado con la advertencia de ajustarlo al límite real de salida del modelo.
- `docker-compose.yml` y `Dockerfile` no definen la variable. No se tocó ningún
  compose compartido fuera del repo.

## Fase 6 — Verificación final
- Completada.
- `uv run pytest`: `169 passed`.
- `uv run ruff check .`: `All checks passed!`
- Siguiente paso: revisión manual y control de versiones por el usuario (sin git add/commit/push).
