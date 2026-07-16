"""Embedding interfaces with a deterministic, offline implementation."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from django.db import transaction

from rag.models import EmbeddingRecord, VectorChunk, VectorStatus


TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


class EmbeddingService(ABC):
    """Minimal provider-neutral interface; implementations must name provenance."""

    provider: str
    model_name: str
    dimensions: int

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Return exactly ``dimensions`` finite floating-point components."""

    def embed_many(self, texts: Iterable[str]) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]


class DeterministicLocalEmbeddingService(EmbeddingService):
    """Feature-hashing embedding for tests and offline development.

    It is intentionally not presented as a quality substitute for a production
    embedding model.  It performs no network or external model call.
    """

    provider = "local"
    model_name = "deterministic-feature-hash-v1"

    def __init__(self, *, dimensions: int = 32):
        if not 8 <= dimensions <= 4096:
            raise ValueError("dimensions must be between 8 and 4096")
        self.dimensions = dimensions

    @staticmethod
    def _features(text: str) -> Sequence[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        tokens = TOKEN_PATTERN.findall(normalized)
        if not tokens:
            raise ValueError("Cannot embed empty or token-free text.")
        bigrams = [f"{left}\u241f{right}" for left, right in zip(tokens, tokens[1:])]
        return [*tokens, *bigrams]

    def embed_text(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        vector = [0.0] * self.dimensions
        for feature in self._features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[bucket] += sign

        magnitude = math.sqrt(sum(component * component for component in vector))
        if magnitude == 0:
            # Hash cancellation is possible for very small dimensions.  A stable
            # fallback preserves a useful, finite vector without random state.
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dimensions] = 1.0
            magnitude = 1.0
        return [component / magnitude for component in vector]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@transaction.atomic
def persist_local_embedding(
    vector_chunk: VectorChunk,
    *,
    service: DeterministicLocalEmbeddingService | None = None,
) -> EmbeddingRecord:
    """Create an active offline embedding tied to the exact source text version."""

    if not vector_chunk.pk:
        raise ValueError("vector_chunk must be saved before it can be embedded")
    if vector_chunk.status == VectorStatus.DISABLED:
        raise ValueError("disabled evidence cannot be embedded")

    text = vector_chunk.source_chunk.text
    checksum = _sha256_text(text)
    if checksum != vector_chunk.content_checksum:
        raise ValueError("vector chunk is stale; refresh its checksum before embedding")

    local_service = service or DeterministicLocalEmbeddingService()
    vector = local_service.embed_text(text)
    EmbeddingRecord.objects.filter(vector_chunk=vector_chunk, is_active=True).update(
        is_active=False
    )
    record, _created = EmbeddingRecord.objects.get_or_create(
        vector_chunk=vector_chunk,
        provider=local_service.provider,
        model_name=local_service.model_name,
        input_checksum=checksum,
        defaults={
            "dimensions": local_service.dimensions,
            "embedding": vector,
            "is_active": True,
        },
    )
    if not record.is_active:
        record.is_active = True
        record.save(update_fields={"is_active"})
    return record

