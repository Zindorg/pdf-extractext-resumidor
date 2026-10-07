"""Configuración de logging con privacidad estricta.

Regla: jamás se registra texto extraído ni resúmenes. Este filtro es la
última línea de defensa: redacta cualquier clave sensible que un logger
reciba como extra (defensa en profundidad contra regresiones).
"""

from __future__ import annotations

import logging

REDACTED = "***REDACTED***"
SENSITIVE_KEYS = frozenset(
    {
        "extracted_text",
        "summary",
        "text",
        "content",
        "prompt",
        "chunk",
        "partial",
        "partials",
    }
)


class PrivacyFilter(logging.Filter):
    """Redacta valores de claves sensibles en extras y args de los registros."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in list(record.__dict__.items()):
            if key.lower() in SENSITIVE_KEYS and value is not None:
                record.__dict__[key] = REDACTED
        if isinstance(record.args, dict):
            record.args = {
                key: REDACTED if key.lower() in SENSITIVE_KEYS else value
                for key, value in record.args.items()
            }
        return True


def setup_logging(level: str = "INFO") -> None:
    """Instala el handler de consola y el filtro de privacidad."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(PrivacyFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # URLs de clientes HTTP: ruido y posible fuga de parámetros sensibles.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    for name, candidate in list(logging.Logger.manager.loggerDict.items()):
        if (
            name.startswith("summarizer")
            and isinstance(candidate, logging.Logger)
            and not any(isinstance(f, PrivacyFilter) for f in candidate.filters)
        ):
            candidate.addFilter(PrivacyFilter())
