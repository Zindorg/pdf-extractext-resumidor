# Microservicio Resumidor con IA (pdf-extractext-resumidor)

Servicio especializado para sintetizar texto plano extraído de documentos mediante LLMs.
Parte del ecosistema Hub & Spoke: expone **API REST síncrona (POST /summarize)** como contrato principal y ofrece **worker asíncrono opcional** sobre Redis Streams. Clean Architecture en 4 capas, Zero-Disk, privacidad en logs.

---

## Arquitectura y Principios

### Clean Architecture (4 capas con inversión de dependencias)

```
src/
├── domain/                  # Entidades, puertos y excepciones (sin dependencias externas)
│   ├── models.py            # SummaryRequest / SummaryResult / SummaryHttpResponse
│   ├── ports.py             # LLMProviderInterface, EventConsumer/PublisherInterface
│   └── exceptions.py        # Excepciones tipadas con `code` (DLQ / HTTP 422/503)
├── application/             # Casos de uso (reglas de negocio puras)
│   ├── summarize_use_case.py# Orquestación, timeout 30s, backoff exponencial, chunking
│   ├── chunking_service.py  # Ventanas de palabras (Map-Reduce > 3.000 palabras)
│   └── prompts.py           # Prompts single-shot / Map / Reduce
├── infrastructure/          # Adaptadores concretos (detalles de implementación)
│   ├── config.py            # Settings tipados (pydantic-settings + .env)
│   ├── llm_clients/         # Cliente OpenAI-compatible + factory (OpenAI, Gemini, Ollama, vLLM, LM Studio)
│   └── messaging/           # Consumidor/publisher Redis Streams (worker async)
└── presentation/            # Puntos de entrada (adapters)
    ├── http/                # API REST síncrona (FastAPI)
    │   ├── main.py          # App + POST /summarize + /health
    │   ├── schemas.py       # Request/Response Pydantic
    │   ├── error_handlers.py# DomainError → HTTP 422/503/500
    │   └── dependencies.py  # DI: lifespan, use_case, LLM provider
    └── worker/              # Worker asíncrono Redis Streams
        ├── main.py          # Composition root, loop consumo, logging seguro
        └── logging_setup.py # PrivacyFilter: redacta claves sensibles
```

**Regla de dependencias:** `presentation → application → domain ← infrastructure`

### Principios Fundamentales

| Principio | Implementación |
|---|---|
| **Zero-Disk** | Todo ocurre en RAM: request → use_case → LLM client → response. Sin escrituras a disco intermedias. |
| **Privacidad en logs** | `PrivacyFilter` redacta automáticamente: `extracted_text`, `summary`, `text`, `content`, `api_key`, `authorization`. Solo metadatos (`document_id`, `model_used`, `processing_time_ms`, códigos de error) llegan a stdout/stderr. |
| **Desacoplamiento de IA** | `LLMProviderInterface` en domain; adaptador OpenAI-compatible en infrastructure. Cambio de proveedor sin tocar lógica de negocio. |
| **Timeout + Backoff** | `asyncio.wait_for` 30s por intento + backoff exponencial (base 1s, cap 8s, max 3 reintentos). |
| **Aislamiento de red** | Solo `internal-net` (nunca `db-net` ni `public-net`). |

---

## Contrato HTTP REST Síncrono

### POST /summarize

Genera un resumen del texto extraído. Contrato síncrono de baja latencia para integración directa.

**Request**
```json
{
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "extracted_text": "Texto plano pre-extraído del documento...",
  "options": {
    "length": "corto",
    "tone": "neutral",
    "language": "es"
  }
}
```

**Response 200 OK**
```json
{
  "summary": "Resumen generado por el modelo de IA...",
  "summary_time_ms": 1234
}
```

**Errores 422 Unprocessable Entity**
Formato uniforme: `{ "reason": "<codigo>", "message": "<descripción>" }`

| `reason` | Causa | Código HTTP |
|---|---|---|
| `document_too_long` | Texto supera `MAX_CONTEXT_TOKENS` estimado (`tokens_per_word` × palabras) | 422 |
| `empty_text` | `extracted_text` vacío o solo whitespace | 422 |
| `invalid_payload` | JSON malformado, UUIDv4 inválido, campos requeridos faltantes | 422 |

**Error 503 Service Unavailable**
```json
{ "reason": "summarizer_unavailable", "message": "LLM provider timeout / rate limit / network error" }
```

**Error 500 Internal Server Error**
```json
{ "reason": "internal_error", "message": "Internal server error" }
```

### GET /health
Health check para sondas de infraestructura.
```json
{ "status": "ok" }
```

---

## Worker Asíncrono Secundario (Redis Streams)

Modo de operación desacoplado para integración vía eventos (event-driven).

### Flujo
```
events:text-extracted  →  worker  →  events:summary-generated
     (grupo summarizer-group)  ↓
                         events:summary-failed   (dead-letter: solo metadatos)
```

### Detalles
* **Entrada:** `XREADGROUP` sobre `events:text-extracted` (grupo `summarizer-group`)
* **Validación:** Contrato de entrada (`document_id` UUIDv4 + `extracted_text`)
* **Chunking:** Si el texto supera `CHUNK_THRESHOLD` (3.000 palabras) aplica **Map-Reduce**
* **Timeout:** 30s por intento con backoff exponencial (`MAX_RETRIES`)
* **Salida:** `XADD` del resultado a `events:summary-generated` + `XACK`
* **Dead-letter:** `XADD` a `events:summary-failed` con `error_code` + `XACK` (poison pill no bloquea al grupo)
* **Privacidad:** Dead-letter **nunca** contiene texto ni resúmenes, solo metadatos

### Contrato de Entrada (Stream)
```json
{
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "extracted_text": "Texto plano pre-extraído...",
  "options": { "length": "corto", "tone": "neutral", "language": "es" }
}
```
Se acepta tanto JSON en campo `payload` como campos directos sobre el hash del stream.

### Contrato de Salida (Stream)
Campos: `document_id`, `summary`, `model_used`, `processing_time_ms`

### Dead-Letter (Stream)
Campos: `document_id`, `error_code`, `status` — **nunca** texto ni resumen.

---

## Variables de Entorno (`.env`)

Todas las variables están documentadas en [`.env.example`](./.env.example).

### Requeridas para HTTP API
| Variable | Descripción | Ejemplo |
|---|---|---|
| `HTTP_PORT` | Puerto servidor HTTP (FastAPI) | `8080` |
| `AI_PROVIDER` | Proveedor LLM: `openai`, `openai-compatible`, `ollama`, `vllm`, `lm-studio` | `openai` |
| `AI_API_KEY` | API Key del proveedor (obligatoria salvo Ollama local) | `sk-...` |
| `AI_BASE_URL` | Base URL endpoint OpenAI-compatible | `https://api.openai.com/v1` |
| `AI_MODEL` | Nombre del modelo | `gpt-4o-mini` |
| `TOKENS_PER_WORD` | Factor estimación tokens/palabra para chunking | `1.3` |
| `MAX_CONTEXT_TOKENS` | Límite ventana contexto (None = default proveedor) | `128000` |

### Worker Async (Redis Streams)
| Variable | Descripción | Default |
|---|---|---|
| `REDIS_URL` | Conexión Redis | `redis://localhost:6379/0` |
| `INPUT_STREAM` | Stream entrada | `events:text-extracted` |
| `OUTPUT_STREAM` | Stream salida | `events:summary-generated` |
| `DLQ_STREAM` | Stream dead-letter | `events:summary-failed` |
| `CONSUMER_GROUP` | Grupo consumidores | `summarizer-group` |
| `CONSUMER_NAME` | Nombre consumidor | `summarizer-worker-1` |

### Chunking / Map-Reduce
| Variable | Descripción | Default |
|---|---|---|
| `CHUNK_THRESHOLD` | Umbral palabras para Map-Reduce | `3000` |
| `CHUNK_SIZE` | Tamaño chunk (palabras) | `1000` |
| `CHUNK_OVERLAP` | Solapamiento entre chunks | `100` |
| `MAX_OUTPUT_TOKENS` | Tokens máximos salida por llamada | `1024` |

### Timeouts y Reintentos
| Variable | Descripción | Default |
|---|---|---|
| `LLM_TIMEOUT_S` | Timeout por llamada LLM | `30` |
| `MAX_RETRIES` | Reintentos con backoff | `3` |
| `BACKOFF_BASE_S` | Base backoff exponencial | `1.0` |
| `BACKOFF_CAP_S` | Tope backoff | `8.0` |

### Observabilidad
| Variable | Descripción | Default |
|---|---|---|
| `LOG_LEVEL` | Nivel logging | `INFO` |

---

## Guía de Ejecución Local

### Prerrequisitos
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (gestor de paquetes ultrarrápido)
- Redis (solo para worker asíncrono)

### Instalación
```bash
cp .env.example .env     # completar AI_API_KEY y demás
uv sync                  # instala dependencias + lock reproducible
```

### Ejecutar API HTTP (contrato principal)
```bash
uv run uvicorn src.presentation.http.main:app --host 0.0.0.0 --port 8080
# o simplemente:
uv run python -m src.presentation.http.main
```
Endpoints disponibles:
- `POST http://localhost:8080/summarize`
- `GET  http://localhost:8080/health`

### Ejecutar Worker Asíncrono (modo secundario)
```bash
uv run python -m src.presentation.worker.main
```
Requiere Redis accesible en `REDIS_URL`.

### Tests
```bash
uv run pytest                    # batería completa (154 tests: unit + integration)
uv run pytest --cov=src          # con cobertura
uv run pytest tests/unit/        # solo unitarios
uv run pytest tests/integration/ # solo integración
```

### Lint y Formato
```bash
uv run ruff check .              # verifica estilo y errores
uv run ruff check . --fix        # auto-fix (imports, quotes, unused, etc.)
uv run ruff format .             # formatea código
```

---

## Docker

```bash
# Build y levantamiento (redis + summarizer en internal-net)
docker compose up --build

# Logs del summarizer
docker compose logs -f summarizer
```

El `Dockerfile` usa Python 3.12-slim, instala dependencias vía `uv sync --frozen` y expone el puerto `HTTP_PORT`.

---

## Estructura de Tests (154 tests)

| Archivo | Qué prueba |
|---|---|
| `tests/unit/test_contracts.py` | Contratos entrada/salida, sanitización errores |
| `tests/unit/test_chunking_service.py` | Umbrales, cobertura sin pérdidas, fronteras de oración |
| `tests/unit/test_summarize_use_case.py` | Single-shot, Map-Reduce, reintentos, timeout, privacidad |
| `tests/unit/test_worker.py` | Manejo mensajes, DLQ, composición |
| `tests/unit/test_llm_client.py` | Cliente OpenAI-compatible, timeouts, reintentos |
| `tests/unit/test_privacy_filter.py` | Redacción de claves sensibles en logs |
| `tests/unit/test_redis_adapters.py` | Serialización/deserialización streams |
| `tests/unit/test_config.py` | Validación settings, normalización provider |
| `tests/integration/test_end_to_end.py` | Flujo completo evento → resumen → publicación (FakeLLMProvider) |
| `tests/integration/test_worker_loop.py` | Loop worker con dobles, sin Redis real |
| `tests/presentation/http_tests/test_summarize_endpoint.py` | Contrato POST /summarize: 200, 422 (document_too_long, empty_text, invalid_payload), 503 |

Todos los tests corren **sin red, sin Redis real y sin proveedor real de IA** (usan `FakeLLMProvider`).

---

## Referencias

- Documento rector: [`ARCHITECTURE_SUMMARIZER.md`](./ARCHITECTURE_SUMMARIZER.md)
- Variables de entorno: [`.env.example`](./.env.example)
- Especificación del orquestador: `ARCHITECTURE_ORCHESTRATOR.md` (repo padre)
