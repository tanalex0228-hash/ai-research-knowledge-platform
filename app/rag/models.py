from __future__ import annotations

import math
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from documents.choices import VisibilityScope

from .fields import PortableVectorField


class VectorStatus(models.TextChoices):
    PENDING = "pending", "等待中"
    READY = "ready", "就緒"
    STALE = "stale", "過期"
    FAILED = "failed", "失敗"
    DISABLED = "disabled", "停用"


class RetrievalKind(models.TextChoices):
    KEYWORD = "keyword", "權限感知關鍵字證據"
    SEMANTIC = "semantic", "語意向量檢索"


class RetrievalStatus(models.TextChoices):
    STARTED = "started", "開始"
    COMPLETED = "completed", "完成"
    DENIED = "denied", "拒絕"
    FAILED = "failed", "失敗"
    NOT_IMPLEMENTED = "not_implemented", "尚未實作"


def _scope_rank(value: str) -> int:
    return VisibilityScope.rank(value)


class VectorDocument(models.Model):
    """Vector-index state for one permission-governed source document."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_document = models.OneToOneField(
        "documents.SourceDocument",
        on_delete=models.CASCADE,
        related_name="vector_document",
    )
    status = models.CharField(
        max_length=20,
        choices=VectorStatus.choices,
        default=VectorStatus.PENDING,
        db_index=True,
    )
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="Inherited from the source document; it may only be stricter.",
    )
    chunk_strategy_version = models.CharField(max_length=50, default="unversioned")
    indexed_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=80, blank=True)
    failure_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "向量文件"
        verbose_name_plural = "向量文件"
        ordering = ("-created_at", "id")

    def __str__(self) -> str:
        return f"Vector document {self.source_document_id}"

    def clean(self) -> None:
        super().clean()
        if not self.source_document_id:
            return
        source_scope = self.source_document.visibility_scope
        if not self.visibility_scope:
            self.visibility_scope = source_scope
        if (
            self.visibility_scope in VisibilityScope.values
            and source_scope in VisibilityScope.values
            and _scope_rank(self.visibility_scope) < _scope_rank(source_scope)
        ):
            raise ValidationError(
                {"visibility_scope": "A vector document cannot widen source access."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class VectorChunk(models.Model):
    """Index state for a citation-addressable ``DocumentChunk``."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vector_document = models.ForeignKey(
        VectorDocument,
        on_delete=models.CASCADE,
        related_name="vector_chunks",
    )
    source_chunk = models.OneToOneField(
        "documents.DocumentChunk",
        on_delete=models.CASCADE,
        related_name="vector_chunk",
    )
    status = models.CharField(
        max_length=20,
        choices=VectorStatus.choices,
        default=VectorStatus.PENDING,
        db_index=True,
    )
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        blank=True,
        default="",
        db_index=True,
    )
    content_checksum = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "向量段落"
        verbose_name_plural = "向量段落"
        ordering = ("vector_document_id", "source_chunk__chunk_index", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(content_checksum=""),
                name="rag_vector_chunk_checksum_not_empty",
            )
        ]

    def __str__(self) -> str:
        return f"Vector chunk {self.source_chunk_id}"

    def clean(self) -> None:
        super().clean()
        if not self.vector_document_id or not self.source_chunk_id:
            return
        if self.source_chunk.source_document_id != self.vector_document.source_document_id:
            raise ValidationError(
                {"source_chunk": "The chunk must belong to the indexed source document."}
            )
        source_scope = self.source_chunk.visibility_scope
        if not self.visibility_scope:
            self.visibility_scope = source_scope
        lower_bounds = (
            source_scope,
            self.vector_document.visibility_scope,
        )
        if self.visibility_scope in VisibilityScope.values and any(
            scope in VisibilityScope.values
            and _scope_rank(self.visibility_scope) < _scope_rank(scope)
            for scope in lower_bounds
        ):
            raise ValidationError(
                {"visibility_scope": "A vector chunk cannot widen evidence access."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class EmbeddingRecord(models.Model):
    """Versioned embedding with enough metadata to detect stale vectors."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vector_chunk = models.ForeignKey(
        VectorChunk,
        on_delete=models.CASCADE,
        related_name="embeddings",
    )
    provider = models.CharField(max_length=80)
    model_name = models.CharField(max_length=160)
    dimensions = models.PositiveIntegerField()
    embedding = PortableVectorField(
        dimensions=None,
        help_text="Native pgvector on PostgreSQL; JSON-compatible text on SQLite.",
    )
    input_checksum = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Embedding 紀錄"
        verbose_name_plural = "Embedding 紀錄"
        ordering = ("-created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("vector_chunk", "provider", "model_name", "input_checksum"),
                name="rag_embedding_input_version_unique",
            ),
            models.CheckConstraint(
                condition=Q(dimensions__gt=0),
                name="rag_embedding_dimensions_positive",
            ),
            models.CheckConstraint(
                condition=~Q(input_checksum=""),
                name="rag_embedding_checksum_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}/{self.model_name} ({self.dimensions})"

    def clean(self) -> None:
        super().clean()
        try:
            vector = [float(value) for value in self.embedding]
        except (TypeError, ValueError) as exc:
            raise ValidationError({"embedding": "Embedding must be numeric."}) from exc
        if len(vector) != self.dimensions:
            raise ValidationError(
                {"embedding": "Embedding length must equal the dimensions field."}
            )
        if not vector or any(not math.isfinite(value) for value in vector):
            raise ValidationError(
                {"embedding": "Embedding must contain finite numeric values."}
            )
        if self.vector_chunk_id and self.input_checksum != self.vector_chunk.content_checksum:
            raise ValidationError(
                {"input_checksum": "Embedding input must match the indexed chunk version."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class RetrievalLog(models.Model):
    """Auditable record of every evidence retrieval attempt."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retrieval_logs",
    )
    query_text = models.TextField()
    retrieval_kind = models.CharField(max_length=20, choices=RetrievalKind.choices)
    permitted_document_scopes = models.JSONField(default=list)
    permitted_research_scopes = models.JSONField(default=list)
    filters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=RetrievalStatus.choices,
        default=RetrievalStatus.STARTED,
        db_index=True,
    )
    result_count = models.PositiveIntegerField(default=0)
    failure_code = models.CharField(max_length=80, blank=True)
    failure_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "檢索紀錄"
        verbose_name_plural = "檢索紀錄"
        ordering = ("-created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(query_text=""),
                name="rag_retrieval_query_not_empty",
            )
        ]

    def __str__(self) -> str:
        return f"{self.retrieval_kind} retrieval {self.request_id}"


class CitationSource(models.Model):
    """Immutable evidence selected by a retrieval, with access snapshot."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    retrieval_log = models.ForeignKey(
        RetrievalLog,
        on_delete=models.CASCADE,
        related_name="citations",
    )
    document_chunk = models.ForeignKey(
        "documents.DocumentChunk",
        on_delete=models.PROTECT,
        related_name="retrieval_citations",
    )
    rank = models.PositiveIntegerField()
    score = models.FloatField(
        validators=(MinValueValidator(0.0), MaxValueValidator(1.0))
    )
    excerpt = models.TextField()
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        help_text="Snapshot of the evidence scope when it was retrieved.",
    )
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "引用來源"
        verbose_name_plural = "引用來源"
        ordering = ("retrieval_log_id", "rank", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("retrieval_log", "rank"),
                name="rag_citation_rank_unique",
            ),
            models.UniqueConstraint(
                fields=("retrieval_log", "document_chunk"),
                name="rag_citation_chunk_unique",
            ),
            models.CheckConstraint(
                condition=Q(rank__gt=0),
                name="rag_citation_rank_positive",
            ),
            models.CheckConstraint(
                condition=~Q(excerpt=""),
                name="rag_citation_excerpt_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"Citation {self.rank} for {self.retrieval_log_id}"

    def clean(self) -> None:
        super().clean()
        if not self.document_chunk_id:
            return
        if self.visibility_scope != self.document_chunk.visibility_scope:
            raise ValidationError(
                {"visibility_scope": "Citation scope must snapshot the source chunk."}
            )
        if self.retrieval_log_id:
            permitted = set(self.retrieval_log.permitted_document_scopes)
            if self.visibility_scope not in permitted:
                raise ValidationError(
                    {"document_chunk": "Citation is outside the audited access scope."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Citation evidence is append-only and cannot be deleted.")
