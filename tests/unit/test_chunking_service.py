"""Pruebas del servicio de chunking (estrategia Map-Reduce)."""

from __future__ import annotations

import pytest

from src.application.chunking_service import ChunkingService
from src.domain.exceptions import ChunkingError


def _synthetic_text(word_count: int) -> str:
    """Texto con palabras únicas (cada una termina en oración) para medir cobertura."""
    return " ".join(f"palabra{i}." for i in range(word_count))


class TestConfiguration:
    def test_defaults(self) -> None:
        service = ChunkingService()
        assert (service.threshold_words, service.chunk_size, service.overlap_words) == (
            3000,
            1000,
            100,
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"threshold_words": 0},
            {"chunk_size": -1},
            {"overlap_words": -5},
            {"chunk_size": 100, "overlap_words": 100},
            {"chunk_size": 100, "overlap_words": 101},
        ],
    )
    def test_invalid_configuration_raises(self, kwargs: dict) -> None:
        with pytest.raises(ChunkingError):
            ChunkingService(**kwargs)


class TestThreshold:
    @pytest.mark.parametrize("words,expected", [(1, False), (3000, False), (3001, True)])
    def test_needs_chunking(self, words: int, expected: bool) -> None:
        service = ChunkingService()
        assert service.needs_chunking(_synthetic_text(words)) is expected

    def test_empty_text_does_not_need_chunking(self) -> None:
        assert ChunkingService().needs_chunking("") is False


class TestSplit:
    def test_empty_text_returns_no_chunks(self) -> None:
        assert ChunkingService().split("") == []
        assert ChunkingService().split("   \n\t  ") == []

    def test_short_text_returns_single_chunk_unchanged(self) -> None:
        text = "una  dos\t tres"
        assert ChunkingService().split(text) == ["una dos tres"]

    def test_long_text_is_split_into_multiple_chunks(self) -> None:
        service = ChunkingService(threshold_words=100, chunk_size=50, overlap_words=10)
        chunks = service.split(_synthetic_text(500))
        assert len(chunks) > 1

    def test_no_word_is_lost(self) -> None:
        service = ChunkingService(threshold_words=100, chunk_size=50, overlap_words=10)
        original = _synthetic_text(500).split()
        chunks = [chunk.split() for chunk in service.split(_synthetic_text(500))]
        covered = {word for chunk in chunks for word in chunk}
        assert covered == set(original)

    def test_chunks_never_exceed_chunk_size(self) -> None:
        service = ChunkingService(threshold_words=100, chunk_size=50, overlap_words=10)
        for chunk in service.split(_synthetic_text(997)):
            assert len(chunk.split()) <= 50

    def test_consecutive_chunks_share_overlap(self) -> None:
        service = ChunkingService(threshold_words=100, chunk_size=50, overlap_words=10)
        chunks = [chunk.split() for chunk in service.split(_synthetic_text(500))]
        for previous, current in zip(chunks, chunks[1:], strict=False):
            overlap = set(previous[-10:]) & set(current[:10])
            assert overlap

    def test_first_and_last_words_are_preserved(self) -> None:
        service = ChunkingService(threshold_words=100, chunk_size=50, overlap_words=10)
        original = _synthetic_text(500).split()
        chunks = service.split(_synthetic_text(500))
        assert chunks[0].split()[0] == original[0]
        assert chunks[-1].split()[-1] == original[-1]

    def test_cuts_on_sentence_boundaries(self) -> None:
        service = ChunkingService(threshold_words=10, chunk_size=20, overlap_words=0)
        sentences = " ".join(f"Oración numero {i}. " for i in range(50))
        chunks = service.split(sentences)
        for chunk in chunks[:-1]:
            assert chunk.rstrip().endswith(".")

    def test_text_without_punctuation_still_splits(self) -> None:
        service = ChunkingService(threshold_words=10, chunk_size=20, overlap_words=0)
        text = " ".join(f"palabra{i}" for i in range(100))
        chunks = service.split(text)
        assert len(chunks) >= 5
        assert sum(len(c.split()) for c in chunks) == 100

    def test_progress_terminates_on_pathological_overlap(self) -> None:
        service = ChunkingService(threshold_words=10, chunk_size=5, overlap_words=4)
        chunks = service.split(_synthetic_text(60))
        assert len(chunks) <= 60
        assert chunks[-1].split()[-1] == "palabra59."
