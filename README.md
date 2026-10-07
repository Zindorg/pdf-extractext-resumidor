# pdf-extractext-resumidor

Worker asíncrono que sintetiza texto plano extraído de documentos mediante LLMs.
Parte del ecosistema Hub & Spoke: recibe eventos de Redis Streams, resume en
memoria (Zero-Disk) y publica el resultado. Sin FastAPI, sin HTTP de entrada y
sin MongoDB.

Documento rector: [`ARCHITECTURE_SUMMARIZER.md`](./ARCHITECTURE_SUMMARIZER.md).

---

## Arquitectura

Clean Architecture en 4 capas con inversión de dependencias:

```text
src/
├── domain/                  # Entidades, puertos y excepciones (sin dependencias)
│   ├── models.py            # SummaryRequest / SummaryResult / StreamMessage
│   ├── ports.py             # LLMProviderInterface, EventConsumer/PublisherInterface
│   └── exceptions.py        # Excepciones tipadas con `code` apto para DLQ
├── application/             # Lógica de negocio
│   ├── summarize_use_case.py# Orquestación, timeout 30s y backoff exponencial
│   ├── chunking_service.py  # Ventanas de palabras (Map-Reduce > 3.000)
│   └── prompts.py           # Prompts single-shot / Map / Reduce
├── infrastructure/          # Adaptadores concretos
│   ├── config.py            # Settings tipados (pydantic-settings + .env)
│   ├── llm_clients/         # Cliente OpenAI-compatible + factory
│   └── messaging/           # Consumidor/publisher de Redis Streams
└── presentation/
    └── worker/              # Composition root, loop de consumo y logging seguro
```

Dependencias: `presentation -> application -> domain <- infrastructure`.

## Flujo

```text
events:text-extracted  →  worker  →  events:summary-generated
     (grupo summarizer-group)  ↓
                       events:summary-failed   (dead-letter: solo metadatos)
```

1. `XREADGROUP` sobre `events:text-extracted` (grupo `summarizer-group`).
2. Validación del contrato de entrada (`document_id` UUIDv4 + `extracted_text`).
3. Si el texto supera `CHUNK_THRESHOLD` (3.000 palabras) aplica **Map-Reduce**:
   segmenta con `ChunkingService`, resume cada chunk y reduce los parciales.
4. Timeout por intento de 30s con backoff exponencial (`MAX_RETRIES`).
5. `XADD` del resultado y `XACK`. Si se agotan los reintentos o el payload es
   inválido: `XADD` al dead-letter con códigos de error y `XACK` (poison pill
   no bloquea al grupo).

## Contratos

Entrada (`events:text-extracted`):

```json
{
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "extracted_text": "Texto plano pre-extraído...",
  "options": { "length": "corto", "tone": "neutral", "language": "es" }
}
```

Se acepta tanto el JSON en un campo `payload` como los campos directos sobre
el hash del stream (interop con el orquestador).

Salida (`events:summary-generated`): campos `document_id`, `summary`,
`model_used`, `processing_time_ms`.

Dead-letter (`events:summary-failed`): `document_id`, `error_code`, `status`.
**Nunca** contiene texto ni resúmenes.

## Reglas del servicio

| Regla | Implementación |
|---|---|
| Zero-Disk | Todo ocurre en RAM; no hay escrituras a disco |
| Privacidad en logs | Solo metadatos; `PrivacyFilter` redacta claves sensibles |
| Timeout 30s + backoff | `asyncio.wait_for` por intento + backoff exponencial con tope |
| Desacoplamiento de IA | `LLMProviderInterface` en domain; adaptador OpenAI-compatible |
| Aislamiento de red | Solo `internal-net` (nunca `db-net` ni `public-net`) |

## Desarrollo

Requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env     # completar AI_API_KEY y demás
uv sync                  # instala dependencias + lock reproducible

uv run pytest            # batería completa (unit + integration)
uv run pytest --cov=src  # cobertura
uv run ruff check .      # lint
uv run ruff format .     # formato
```

### Ejecutar localmente

```bash
uv run python -m src.presentation.worker.main
```

### Docker

```bash
docker compose up --build   # redis + worker en internal-net
docker compose logs -f summarizer
```

## Configuración

Todas las variables están documentadas en [`.env.example`](./.env.example):
streams y grupo de consumo, proveedor/modelo/base URL del LLM, umbrales de
chunking, timeout, reintentos y nivel de log.

## Tests

- `tests/unit/test_contracts.py` — contratos de entrada/salida y sanitización de errores.
- `tests/unit/test_chunking_service.py` — umbrales, cobertura sin pérdidas y fronteras de oración.
- `tests/unit/test_summarize_use_case.py` — single-shot, Map-Reduce, reintentos, timeout y privacidad.
- `tests/unit/test_worker.py` — manejo de mensajes, DLQ y composición.
- `tests/integration/test_end_to_end.py` — flujo completo con `FakeLLMProvider`.

Los tests corren sin red, sin Redis y sin proveedor real de IA.
