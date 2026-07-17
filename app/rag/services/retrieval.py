"""Permission-first evidence retrieval.

There is deliberately no method that retrieves all chunks.  Every public entry
point derives both document and research visibility from a concrete user (or
``None`` for a visitor) before constructing the queryset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.permissions import visible_scopes_for
from documents.models import DocumentChunk
from rag.models import (
    CitationSource,
    RetrievalKind,
    RetrievalLog,
    RetrievalStatus,
)


class SemanticRetrievalNotImplemented(NotImplementedError):
    """Raised until pgvector ranking and a relevance benchmark are approved."""


@dataclass(frozen=True)
class RetrievalAccess:
    actor: object | None
    document_scopes: tuple[str, ...]
    research_scopes: tuple[str, ...]

    @classmethod
    def for_user(cls, user: object | None) -> "RetrievalAccess":
        authenticated = bool(user is not None and getattr(user, "is_authenticated", False))
        scopes = tuple(str(scope) for scope in visible_scopes_for(user))

        return cls(
            actor=user if authenticated else None,
            document_scopes=scopes,
            research_scopes=scopes,
        )


@dataclass(frozen=True)
class RetrievalResult:
    log: RetrievalLog
    citations: tuple[CitationSource, ...]


class PermissionAwareRetrievalService:
    max_limit = 50

    @staticmethod
    def _tokens(query: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"\w+", query.casefold(), re.UNICODE)))

    @staticmethod
    def _excerpt(text: str, *, maximum: int = 500) -> str:
        cleaned = " ".join(text.split())
        return cleaned if len(cleaned) <= maximum else f"{cleaned[: maximum - 1]}\u2026"

    @transaction.atomic
    def retrieve_keyword(
        self,
        query: str,
        *,
        user: object | None,
        limit: int = 10,
        filters: dict | None = None,
    ) -> RetrievalResult:
        query = " ".join(str(query).split())
        if not query:
            raise ValueError("query must not be empty")
        if not 1 <= limit <= self.max_limit:
            raise ValueError(f"limit must be between 1 and {self.max_limit}")
        if filters is not None and not isinstance(filters, dict):
            raise TypeError("filters must be a dictionary")
        filters = filters or {}
        unsupported_filters = set(filters) - {"research_work_id", "year"}
        if unsupported_filters:
            raise ValueError(
                "unsupported retrieval filters: "
                + ", ".join(sorted(unsupported_filters))
            )
        tokens = self._tokens(query)
        if not tokens:
            raise ValueError("query must contain at least one searchable term")

        access = RetrievalAccess.for_user(user)
        log = RetrievalLog.objects.create(
            requested_by=access.actor,
            query_text=query,
            retrieval_kind=RetrievalKind.KEYWORD,
            permitted_document_scopes=list(access.document_scopes),
            permitted_research_scopes=list(access.research_scopes),
            filters=filters,
        )

        text_query = Q()
        for token in tokens:
            text_query |= Q(text__icontains=token)

        queryset = DocumentChunk.objects.select_related(
            "source_document__research_work"
        ).filter(
            text_query,
            visibility_scope__in=access.document_scopes,
            source_document__visibility_scope__in=access.document_scopes,
            source_document__research_work__visibility_scope__in=access.research_scopes,
            source_document__research_work__status="published",
        )
        if "research_work_id" in filters:
            queryset = queryset.filter(
                source_document__research_work_id=filters["research_work_id"]
            )
        if "year" in filters:
            queryset = queryset.filter(
                source_document__research_work__year=filters["year"]
            )
        queryset = (
            queryset.order_by("source_document_id", "chunk_index")[:limit]
        )

        citations: list[CitationSource] = []
        for rank, chunk in enumerate(queryset, start=1):
            text_casefold = chunk.text.casefold()
            matched = sum(token in text_casefold for token in tokens)
            score = matched / max(len(tokens), 1)
            citation = CitationSource.objects.create(
                retrieval_log=log,
                document_chunk=chunk,
                rank=rank,
                score=score,
                excerpt=self._excerpt(chunk.text),
                visibility_scope=chunk.visibility_scope,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
            )
            citations.append(citation)

        log.status = RetrievalStatus.COMPLETED
        log.result_count = len(citations)
        log.completed_at = timezone.now()
        log.save(update_fields={"status", "result_count", "completed_at"})
        return RetrievalResult(log=log, citations=tuple(citations))

    def retrieve_semantic(
        self,
        query: str,
        *,
        user: object | None,
        limit: int = 10,
        filters: dict | None = None,
    ) -> RetrievalResult:
        query = " ".join(str(query).split())
        if not query:
            raise ValueError("query must not be empty")
        if not 1 <= limit <= self.max_limit:
            raise ValueError(f"limit must be between 1 and {self.max_limit}")
        if filters is not None and not isinstance(filters, dict):
            raise TypeError("filters must be a dictionary")
        filters = filters or {}
        unsupported_filters = set(filters) - {"research_work_id", "year"}
        if unsupported_filters:
            raise ValueError(
                "unsupported retrieval filters: "
                + ", ".join(sorted(unsupported_filters))
            )

        access = RetrievalAccess.for_user(user)
        log = RetrievalLog.objects.create(
            requested_by=access.actor,
            query_text=query,
            retrieval_kind=RetrievalKind.SEMANTIC,
            permitted_document_scopes=list(access.document_scopes),
            permitted_research_scopes=list(access.research_scopes),
            filters=filters,
        )

        from rag.services.embeddings import DeterministicLocalEmbeddingService
        embedder = DeterministicLocalEmbeddingService()
        query_vector = embedder.embed_text(query)

        from django.db import connection as db_connection
        from rag.models import EmbeddingRecord

        embedding_qs = EmbeddingRecord.objects.select_related(
            "vector_chunk__source_chunk__source_document__research_work"
        ).filter(
            is_active=True,
            vector_chunk__status="ready",
            vector_chunk__visibility_scope__in=access.document_scopes,
            vector_chunk__vector_document__visibility_scope__in=access.document_scopes,
            vector_chunk__source_chunk__source_document__visibility_scope__in=access.document_scopes,
            vector_chunk__source_chunk__source_document__research_work__visibility_scope__in=access.research_scopes,
            vector_chunk__source_chunk__source_document__research_work__status="published",
        )

        if "research_work_id" in filters:
            embedding_qs = embedding_qs.filter(
                vector_chunk__source_chunk__source_document__research_work_id=filters["research_work_id"]
            )
        if "year" in filters:
            embedding_qs = embedding_qs.filter(
                vector_chunk__source_chunk__source_document__research_work__year=filters["year"]
            )

        if db_connection.vendor == "postgresql":
            from pgvector.django import CosineDistance
            embedding_qs = embedding_qs.annotate(
                distance=CosineDistance("embedding", query_vector)
            ).order_by("distance")[:limit]

            records = list(embedding_qs)
            results_data = []
            for r in records:
                score = max(0.0, min(1.0, 1.0 - float(r.distance)))
                results_data.append((r.vector_chunk.source_chunk, score))
        else:
            records = list(embedding_qs)
            scored_records = []
            for r in records:
                vec = r.embedding
                dot_product = sum(a * b for a, b in zip(query_vector, vec))
                score = max(0.0, min(1.0, dot_product))
                scored_records.append((r, score))
            scored_records.sort(key=lambda x: x[1], reverse=True)
            results_data = [(item[0].vector_chunk.source_chunk, item[1]) for item in scored_records[:limit]]

        citations: list[CitationSource] = []
        for rank, (chunk, score) in enumerate(results_data, start=1):
            citation = CitationSource.objects.create(
                retrieval_log=log,
                document_chunk=chunk,
                rank=rank,
                score=score,
                excerpt=self._excerpt(chunk.text),
                visibility_scope=chunk.visibility_scope,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
            )
            citations.append(citation)

        log.status = RetrievalStatus.COMPLETED
        log.result_count = len(citations)
        log.completed_at = timezone.now()
        log.save(update_fields={"status", "result_count", "completed_at"})
        return RetrievalResult(log=log, citations=tuple(citations))
