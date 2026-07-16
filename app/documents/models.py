from __future__ import annotations

import uuid
from pathlib import PurePath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.signals import post_delete
from django.dispatch import receiver

from accounts.permissions import visible_scopes_for

from .choices import ExtractionStatus, VisibilityScope
from .storage import private_document_storage
from .validators import (
    PDF_MIME_TYPES,
    original_basename,
    sha256_checksum,
    validate_pdf_upload,
)


def private_document_upload_to(instance: "SourceDocument", filename: str) -> str:
    """Use an opaque key so storage paths never disclose the original filename."""

    del filename
    work_id = instance.research_work_id or "unassigned"
    return f"research-works/{work_id}/{uuid.uuid4().hex}.pdf"


class VisibilityQuerySet(models.QuerySet):
    def public(self):
        return self.filter(visibility_scope=VisibilityScope.PUBLIC)

    def for_scopes(self, scopes):
        """Filter by scopes already authorized by the backend permission layer."""

        allowed = {str(getattr(scope, "value", scope)) for scope in scopes}
        return self.filter(visibility_scope__in=allowed)

    def visible_to(self, user):
        return self.for_scopes(visible_scopes_for(user))


class SourceDocumentQuerySet(VisibilityQuerySet):
    immutable_source_fields = frozenset(
        {
            "file",
            "research_work",
            "research_work_id",
            "visibility_scope",
            "uploaded_by",
            "uploaded_by_id",
        }
    )

    def update(self, **kwargs):
        if self.immutable_source_fields.intersection(kwargs):
            raise ValidationError(
                "A source document's file, research work, visibility, and uploader are immutable; "
                "create a new document."
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if self.immutable_source_fields.intersection(fields):
            raise ValidationError(
                "A source document's file, research work, visibility, and uploader are immutable; "
                "create a new document."
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)

class SourceDocument(models.Model):
    """A privately stored source PDF attached to one research work."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    research_work = models.ForeignKey(
        "research.ResearchWork",
        on_delete=models.CASCADE,
        related_name="source_documents",
    )
    file = models.FileField(
        upload_to=private_document_upload_to,
        storage=private_document_storage,
        max_length=500,
    )
    # Sensitive metadata: populated by the model and exposed only in Django admin.
    original_filename = models.CharField(max_length=255, blank=True, editable=False)
    mime_type = models.CharField(max_length=100, blank=True, editable=False)
    file_size = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    checksum = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        db_index=True,
    )
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        default=VisibilityScope.ADMIN,
        db_index=True,
    )
    extraction_status = models.CharField(
        max_length=20,
        choices=ExtractionStatus.choices,
        default=ExtractionStatus.PENDING,
        db_index=True,
    )
    extraction_error = models.TextField(blank=True, editable=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_source_documents",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SourceDocumentQuerySet.as_manager()

    class Meta:
        ordering = ("-uploaded_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(file_size__isnull=True) | Q(file_size__gt=0),
                name="documents_source_file_size_positive",
            ),
            models.UniqueConstraint(
                fields=("research_work", "checksum"),
                condition=~Q(checksum=""),
                name="documents_source_work_checksum_unique",
            ),
        ]

    def __str__(self) -> str:
        # Deliberately excludes original_filename: it may contain student PII.
        return f"Source document {self.id}"

    @property
    def has_pending_upload(self) -> bool:
        return bool(self.file) and not getattr(self.file, "_committed", True)

    @property
    def checksum_sha256(self) -> str:
        """Explicit algorithm alias for code that needs to name the digest type."""

        return self.checksum

    @checksum_sha256.setter
    def checksum_sha256(self, value: str) -> None:
        self.checksum = value

    def _prepare_upload_metadata(self) -> None:
        raw_name = getattr(getattr(self.file, "file", None), "name", self.file.name)
        self.original_filename = original_basename(raw_name)

        raw_mime = getattr(self.file, "content_type", None) or getattr(
            getattr(self.file, "file", None), "content_type", None
        )
        self.mime_type = str(raw_mime or "").split(";", 1)[0].strip().lower()
        self.file_size = self.file.size

        validate_pdf_upload(self.file, mime_type=self.mime_type)
        self.checksum = sha256_checksum(self.file)

    def clean(self) -> None:
        super().clean()
        if self.has_pending_upload:
            validate_pdf_upload(self.file, mime_type=self.mime_type or None)

        if self.mime_type and self.mime_type not in PDF_MIME_TYPES:
            raise ValidationError(
                {"mime_type": "Stored document metadata must describe a PDF."}
            )
        if (
            self.original_filename
            and PurePath(self.original_filename).suffix.lower() != ".pdf"
        ):
            raise ValidationError(
                {"original_filename": "Stored document metadata must use a .pdf filename."}
            )

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            persisted = type(self)._base_manager.only(
                "research_work_id", "file", "visibility_scope", "uploaded_by_id"
            ).get(pk=self.pk)
            if (
                persisted.research_work_id != self.research_work_id
                or persisted.file.name != self.file.name
                or persisted.visibility_scope != self.visibility_scope
                or persisted.uploaded_by_id != self.uploaded_by_id
            ):
                raise ValidationError(
                    "A source document's file, research work, visibility, and uploader "
                    "are immutable; "
                    "create a new document instead."
                )
        if self.has_pending_upload:
            self._prepare_upload_metadata()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {
                    "file",
                    "original_filename",
                    "mime_type",
                    "file_size",
                    "checksum",
                }
        self.full_clean()
        return super().save(*args, **kwargs)


class DocumentChunk(models.Model):
    """A citation-addressable text segment extracted from a source document."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_document = models.ForeignKey(
        SourceDocument,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_index = models.PositiveIntegerField()
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    char_start = models.PositiveBigIntegerField(null=True, blank=True)
    char_end = models.PositiveBigIntegerField(null=True, blank=True)
    text = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    chunk_strategy_version = models.CharField(max_length=50, default="unversioned")
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="Defaults to the source document's scope and may only be stricter.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = VisibilityQuerySet.as_manager()

    class Meta:
        ordering = ("source_document_id", "chunk_index")
        constraints = [
            models.UniqueConstraint(
                fields=("source_document", "chunk_index"),
                name="documents_chunk_document_index_unique",
            ),
            models.CheckConstraint(
                condition=Q(page_start__isnull=True) | Q(page_start__gte=1),
                name="documents_chunk_page_start_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(page_end__isnull=True)
                    | Q(page_start__isnull=True)
                    | Q(page_end__gte=F("page_start"))
                ),
                name="documents_chunk_page_range_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(char_end__isnull=True)
                    | Q(char_start__isnull=True)
                    | Q(char_end__gte=F("char_start"))
                ),
                name="documents_chunk_char_range_valid",
            ),
            models.CheckConstraint(
                condition=~Q(text=""),
                name="documents_chunk_text_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"Chunk {self.chunk_index} of {self.source_document_id}"

    def clean(self) -> None:
        super().clean()
        if not self.source_document_id:
            return

        source_scope = self.source_document.visibility_scope
        if not self.visibility_scope:
            self.visibility_scope = source_scope
            return

        valid_scopes = {choice.value for choice in VisibilityScope}
        if self.visibility_scope not in valid_scopes or source_scope not in valid_scopes:
            return  # Field-level choice validation reports the invalid value.
        if VisibilityScope.rank(self.visibility_scope) < VisibilityScope.rank(source_scope):
            raise ValidationError(
                {
                    "visibility_scope": (
                        "A chunk cannot be less restrictive than its source document."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


@receiver(post_delete, sender=SourceDocument)
def remove_private_blob_after_document_commit(sender, instance, using, **kwargs):
    """Delete a private blob only after the row/cascade deletion commits."""

    del sender, kwargs
    file_name = getattr(instance.file, "name", "")
    if not file_name:
        return
    storage = instance.file.storage

    def remove_if_unreferenced():
        if not SourceDocument.objects.using(using).filter(file=file_name).exists():
            storage.delete(file_name)

    transaction.on_commit(remove_if_unreferenced, using=using)
