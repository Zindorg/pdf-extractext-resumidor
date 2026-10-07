"""Servicio de segmentación por ventanas de palabras (Map-Reduce).

Estrategia: textos con más de ``threshold_words`` palabras se parten en
chunks de ``chunk_size`` palabras con ``overlap_words`` de solape, cortando
en frontera de oración cuando es posible. Sin I/O: 100% en memoria.
"""

from __future__ import annotations

from src.domain.exceptions import ChunkingError

_SENTENCE_ENDINGS = (".", "!", "?")
_CLOSING_QUOTES = "\"')]}»"


def _is_sentence_end(word: str) -> bool:
    """True si la palabra cierra una oración (permite comillas/cierres detrás)."""
    return word.rstrip(_CLOSING_QUOTES).endswith(_SENTENCE_ENDINGS)


class ChunkingService:
    """Segmenta texto largo en ventanas solapadas para la estrategia Map-Reduce."""

    def __init__(
        self,
        threshold_words: int = 3000,
        chunk_size: int = 1000,
        overlap_words: int = 100,
    ) -> None:
        if threshold_words <= 0 or chunk_size <= 0:
            raise ChunkingError("chunking_config: values must be positive")
        if overlap_words < 0 or overlap_words >= chunk_size:
            raise ChunkingError("chunking_config: overlap must be in [0, chunk_size)")
        self.threshold_words = threshold_words
        self.chunk_size = chunk_size
        self.overlap_words = overlap_words

    @staticmethod
    def word_count(text: str) -> int:
        return len(text.split())

    def needs_chunking(self, text: str) -> bool:
        return self.word_count(text) > self.threshold_words

    def split(self, text: str) -> list[str]:
        """Divide ``text`` en chunks; para texto corto retorna un único elemento."""
        words = text.split()
        if not words:
            return []
        if len(words) <= self.chunk_size:
            return [" ".join(words)]

        chunks: list[str] = []
        start = 0
        total = len(words)
        while start < total:
            end = min(start + self.chunk_size, total)
            if end < total:
                end = self._snap_to_sentence(words, start, end)
            chunks.append(" ".join(words[start:end]))
            if end >= total:
                break
            next_start = end - self.overlap_words
            start = next_start if next_start > start else start + 1
        return chunks

    @staticmethod
    def _snap_to_sentence(words: list[str], start: int, end: int) -> int:
        """Retrocede desde ``end`` hasta la última frontera de oración del tramo."""
        minimum = start + max(1, (end - start) // 2)
        for index in range(end - 1, minimum - 1, -1):
            if _is_sentence_end(words[index]):
                return index + 1
        return end
