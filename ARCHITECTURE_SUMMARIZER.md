# Blueprint de Arquitectura — Microservicio Resumidor IA (Python)

> Documento rector de especificación técnica para el desarrollo del Resumidor de Texto.
> Alineado con la arquitectura general del sistema (Hub & Spoke, Zero-Disk, Aislamiento de Redes y Flujo Opcional de IA).

---

## 1. Responsabilidades del Servicio

El **Resumidor IA** es un componente especializado dentro del ecosistema cuyo único propósito es sintetizar texto plano extraído de documentos mediante Modelos de Lenguaje (LLMs):

1. **Recepción de Carga Útil de Texto:** Recibe exclusivamente texto plano pre-extraído y el identificador único del documento (`document_id`). No recibe binarios PDF ni realiza parsing de archivos.
2. **Procesamiento Zero-Disk:** Opera íntegramente en memoria RAM. No almacena texto ni resúmenes en el sistema de archivos del contenedor.
3. **Manejo de Contexto y Chunking:** Evalúa la longitud del texto entrante y aplica estrategias de fragmentación segura si el documento excede el límite de tokens de contexto del modelo.
4. **Inferencia Asíncrona:** Realiza llamadas no bloqueantes (`async/await`) contra el proveedor de IA configurado mediante interfaces abstractas desacopladas.
5. **Aislamiento Estricto:** No dialoga con el Extractor ni accede a la base de datos de persistencia (MongoDB). Solo responde a la coordinación del Orquestador o emite el resultado procesado.

---

## 2. Stack Tecnológico

| Capa | Elección | Justificación |
|---|---|---|
| **Lenguaje** | Python 3.12+ | Estándar de facto en el ecosistema de IA, SDKs oficiales y procesamiento de lenguaje natural |
| **Gestor de Paquetes** | `uv` (Astral) | Resolución ultrarrápida y determinista de dependencias, imágenes Docker livianas |
| **Entrada / Transporte** | FastAPI / Async Worker | Enrutamiento REST asíncrono ASGI con Pydantic v2 o consumidor de Redis Streams |
| **Cliente de Red / IA** | HTTPX / SDK Oficial | Cliente HTTP asíncrono no bloqueante con control de timeouts y políticas de reintento |
| **Testing** | Pytest + Pytest-asyncio | Pruebas unitarias de casos de uso y contratos con implementaciones en memoria (FakeLLM) |
| **Contenedores** | Docker (Distroless / Slim) | Despliegue empaquetado para conectarse a la red interna `mired` |

---

## 3. Especificación de Contratos y DTOs

### Contrato de Entrada (Input Payload)
* **Formato:** JSON
* **Campos:**
  * `document_id` (string UUIDv4, requerido): Identificador del documento procesado.
  * `extracted_text` (string, requerido): Texto extraído del documento sobre el cual inferir.
  * `options` (object, opcional): Parámetros de configuración del resumen (e.g., longitud, tono, idioma).

### Contrato de Salida (Output Payload)
* **Formato:** JSON
* **Campos:**
  * `document_id` (string UUIDv4): El mismo ID correlativo recibido.
  * `summary` (string): Texto del resumen generado por el modelo de IA.
  * `model_used` (string): Identificador del modelo que generó la respuesta.
  * `processing_time_ms` (integer): Tiempo total de cómputo/inferencia en milisegundos.

---

## 4. Contrato HTTP REST Síncrono (POST /summarize)

El microservicio expone una API REST síncrona como contrato principal de integración.

### Endpoint
```
POST /summarize
Content-Type: application/json
```

### Request Body
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

### Response 200 OK
```json
{
  "summary": "Resumen generado por el modelo de IA...",
  "summary_time_ms": 1234
}
```

### Errores 422 Unprocessable Entity
Formato uniforme: `{ "reason": "<codigo>", "message": "<descripción>" }`

| `reason` | Causa |
|---|---|
| `document_too_long` | El texto supera `MAX_CONTEXT_TOKENS` estimado (tokens_per_word × palabras) |
| `empty_text` | `extracted_text` vacío o solo whitespace |
| `invalid_payload` | JSON malformado, UUIDv4 inválido, campos requeridos faltantes |

### Error 503 Service Unavailable
```json
{ "reason": "summarizer_unavailable", "message": "LLM provider timeout / rate limit / network error" }
```

### Error 500 Internal Server Error
```json
{ "reason": "internal_error", "message": "Internal server error" }
```

---

## 5. Worker Asíncrono Secundario (Redis Streams)

Como modo de operación alternativo y desacoplado, el servicio incluye un worker que consume de Redis Streams:

```
events:text-extracted  →  worker  →  events:summary-generated
     (grupo summarizer-group)  ↓
                         events:summary-failed   (dead-letter: solo metadatos)
```

* **Entrada:** `XREADGROUP` sobre `events:text-extracted` (grupo `summarizer-group`)
* **Validación:** Contrato de entrada (`document_id` UUIDv4 + `extracted_text`)
* **Chunking:** Si el texto supera `CHUNK_THRESHOLD` (3.000 palabras) aplica **Map-Reduce**
* **Timeout:** 30s por intento con backoff exponencial (`MAX_RETRIES`)
* **Salida:** `XADD` del resultado y `XACK`
* **Dead-letter:** `XADD` a `events:summary-failed` con códigos de error y `XACK` (poison pill no bloquea al grupo)
* **Privacidad:** Dead-letter **nunca** contiene texto ni resúmenes, solo metadatos

---

## 6. Estructura Interna del Proyecto (Clean Architecture / 4 Capas)

```text
pdf-extractext-resumidor/
├── pyproject.toml              # Definición del proyecto y dependencias con UV
├── uv.lock                     # Lockfile reproducible
├── Dockerfile                  # Empaquetado optimizado en Python 3.12-slim
├── src/
│   ├── domain/                 # Entidades, DTOs y Puertos abstractos (sin dependencias externas)
│   │   ├── models.py           # Modelos de dominio (SummaryRequest, SummaryResult, SummaryHttpResponse)
│   │   ├── ports.py            # Interfaces de proveedores (LLMProviderInterface, EventConsumer/PublisherInterface)
│   │   └── exceptions.py       # Excepciones de dominio tipadas con `code` apto para DLQ/HTTP
│   ├── application/            # Casos de uso de la lógica de resumen (reglas de negocio puras)
│   │   ├── summarize_use_case.py # Orquestación del prompt, validación, chunking, timeout 30s + backoff
│   │   ├── chunking_service.py   # Servicio de segmentación por ventanas de tokens/ palabras
│   │   └── prompts.py            # Prompts single-shot / Map / Reduce
│   ├── infrastructure/         # Adaptadores externos y conectores (detalles de implementación)
│   │   ├── config.py           # Configuración tipada (Pydantic Settings / .env)
│   │   ├── llm_clients/        # Implementación concreta de LLMs (OpenAI-compatible: OpenAI, Gemini, Ollama, vLLM, LM Studio)
│   │   │   ├── factory.py      # Factory para instanciar el proveedor según AI_PROVIDER
│   │   │   └── openai_compatible.py # Cliente HTTPX asíncrono con timeouts y reintentos
│   │   └── messaging/          # Consumidor/publisher de Redis Streams (worker asíncrono)
│   └── presentation/           # Puntos de entrada de la aplicación (adapters)
│       ├── http/               # API REST síncrona (FastAPI)
│       │   ├── main.py         # App FastAPI + endpoint POST /summarize + health
│       │   ├── schemas.py      # Pydantic models request/response HTTP
│       │   ├── error_handlers.py # Mapeo DomainError → HTTP 422/503/500
│       │   └── dependencies.py # Inyección de dependencias (lifespan, use_case, LLM provider)
│       └── worker/             # Loop de consumo asíncrono (Redis Streams)
│           ├── main.py         # Composition root, loop de consumo, logging seguro
│           └── logging_setup.py # PrivacyFilter: redacta claves sensibles en logs
└── tests/                      # Batería de pruebas unitarias y de integración (154 tests)
    ├── conftest.py             # Fixtures y mocks de clientes (FakeLLMProvider, etc.)
    ├── unit/                   # Tests de casos de uso sin I/O real
    └── integration/            # Pruebas de serialización, endpoints y flujo E2E
```

**Regla de dependencias:** `presentation → application → domain ← infrastructure`

---

## 7. Contratos de Dominio e Interfaces (Puertos)

```python
# src/domain/ports.py
from abc import ABC, abstractmethod
from src.domain.models import SummaryResult

class LLMProviderInterface(ABC):
    @abstractmethod
    async def generate_summary(self, text: str, max_tokens: int | None = None) -> str:
        """Envía el texto al modelo de IA y retorna la respuesta generada."""
        pass

class EventConsumerInterface(ABC):
    @abstractmethod
    async def consume(self, handler): ...

class EventPublisherInterface(ABC):
    @abstractmethod
    async def publish(self, stream: str, fields: dict): ...
```

La **inversión de dependencias** permite:
- Testear `SummarizeUseCase` con `FakeLLMProvider` (sin red, sin API keys)
- Cambiar proveedor de IA (OpenAI, Gemini, Ollama, vLLM, LM Studio) sin tocar lógica de negocio
- Ejecutar el worker con `NullPublisher` en tests de integración

---

## 8. Zero-Disk y Privacidad en Logs

| Principio | Implementación |
|---|---|
| **Zero-Disk** | Todo ocurre en RAM: request → use_case → LLM client → response. Sin escrituras a disco intermedias. |
| **Privacidad en logs** | `PrivacyFilter` (logging filter) redacta automáticamente claves sensibles: `extracted_text`, `summary`, `text`, `content`, `api_key`, `authorization`. Solo metadatos (`document_id`, `model_used`, `processing_time_ms`, códigos de error) llegan a stdout/stderr. |

---

## 9. Especificación de Redes y Despliegue en Docker

El microservicio Resumidor se ejecuta de manera aislada en la red interna:

* **Red asignada:** `mired` (o `internal-net`)
* **Regla Estricta:** El Resumidor **NUNCA** se conecta a `db-net` ni a `public-net`. Solo se comunica a través del Orquestador o del broker Redis.

```yaml
services:
  summarizer:
    build:
      context: ./pdf-extractext-resumidor
      dockerfile: Dockerfile
    container_name: ms_summarizer
    environment:
      - HTTP_PORT=8080
      - AI_PROVIDER=gemini # o openai / ollama / vllm / lm-studio / openai-compatible
      - AI_API_KEY=${AI_API_KEY}
      - AI_BASE_URL=https://api.openai.com/v1
      - AI_MODEL=gpt-4o-mini
      - TOKENS_PER_WORD=1.3
      - MAX_CONTEXT_TOKENS=128000
    networks:
      - internal-net
```

---

## 10. Variables de Entorno Requeridas (`.env`)

| Variable | Descripción | Ejemplo / Default |
|---|---|---|
| `HTTP_PORT` | Puerto del servidor HTTP (FastAPI) | `8080` |
| `AI_PROVIDER` | Proveedor LLM: `openai`, `openai-compatible`, `ollama`, `vllm`, `lm-studio` | `openai` |
| `AI_API_KEY` | API Key del proveedor (obligatoria salvo Ollama local) | `sk-...` |
| `AI_BASE_URL` | Base URL del endpoint OpenAI-compatible | `https://api.openai.com/v1` |
| `AI_MODEL` | Nombre del modelo a usar | `gpt-4o-mini` |
| `TOKENS_PER_WORD` | Factor de estimación tokens/palabra para chunking | `1.3` |
| `MAX_CONTEXT_TOKENS` | Límite de ventana de contexto del modelo (None = default proveedor) | `128000` |
| `REDIS_URL` | Conexión a Redis Streams (worker async) | `redis://localhost:6379/0` |
| `INPUT_STREAM` | Stream de entrada | `events:text-extracted` |
| `OUTPUT_STREAM` | Stream de salida | `events:summary-generated` |
| `DLQ_STREAM` | Stream dead-letter | `events:summary-failed` |
| `CONSUMER_GROUP` | Grupo de consumidores Redis | `summarizer-group` |
| `CHUNK_THRESHOLD` | Umbral palabras para activar Map-Reduce | `3000` |
| `CHUNK_SIZE` | Tamaño de chunk en palabras | `1000` |
| `CHUNK_OVERLAP` | Solapamiento entre chunks | `100` |
| `LLM_TIMEOUT_S` | Timeout por llamada al LLM | `30` |
| `MAX_RETRIES` | Reintentos con backoff exponencial | `3` |
| `LOG_LEVEL` | Nivel de logging | `INFO` |

Ver [`.env.example`](./.env.example) para lista completa con valores por defecto.
