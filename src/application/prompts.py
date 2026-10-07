"""Construcción de prompts para las rutas single-shot, Map y Reduce.

Los prompts son puro texto de control: nunca se registran en logs.
"""

from __future__ import annotations

from src.domain.models import SummaryOptions

_SYSTEM_PROMPT = (
    "Eres un motor de síntesis de texto. Produces resúmenes fieles, precisos "
    "y sin inventar información. Respondes únicamente con el resumen."
)


def system_prompt() -> str:
    return _SYSTEM_PROMPT


def _style_directives(options: SummaryOptions | None) -> str:
    if options is None:
        return ""
    directives = []
    if options.length:
        directives.append(f"Longitud deseada: {options.length}.")
    if options.tone:
        directives.append(f"Tono: {options.tone}.")
    if options.language:
        directives.append(f"Idioma de salida: {options.language}.")
    return " " + " ".join(directives) if directives else ""


def single_prompt(text: str, options: SummaryOptions | None = None) -> str:
    return f"Resume el siguiente texto.{_style_directives(options)}\n\nTexto:\n{text}"


def map_prompt(chunk: str, options: SummaryOptions | None = None) -> str:
    return (
        "Resume el siguiente fragmento de un documento más largo. "
        "El fragmento continúa en un texto del que no ves el inicio ni el final; "
        "conserva los datos y nombres propios relevantes."
        f"{_style_directives(options)}\n\nFragmento:\n{chunk}"
    )


def reduce_prompt(partials: list[str], options: SummaryOptions | None = None) -> str:
    joined = "\n\n---\n\n".join(partials)
    return (
        "Estos son los resúmenes parciales, en orden, de un mismo documento. "
        "Combínalos en un único resumen coherente y sin repeticiones, "
        "respetando el orden del documento original."
        f"{_style_directives(options)}\n\nResúmenes parciales:\n\n{joined}"
    )
