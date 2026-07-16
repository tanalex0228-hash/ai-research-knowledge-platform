from __future__ import annotations

from collections.abc import Iterable

from django.core.exceptions import PermissionDenied
from django.db import transaction

from .choices import VisibilityScope
from .models import SourceDocument


@transaction.atomic
def create_source_document(
    *,
    research_work,
    uploaded_file,
    uploaded_by,
    visibility_scope: str = VisibilityScope.ADMIN,
) -> SourceDocument:
    """Create a validated private document without returning a storage URL."""

    document = SourceDocument(
        research_work=research_work,
        file=uploaded_file,
        uploaded_by=uploaded_by,
        visibility_scope=visibility_scope,
    )
    try:
        document.save()
    except Exception:
        # File storage is outside the database transaction. If model persistence
        # fails after storage writes the blob, remove the ungoverned orphan.
        if document.file and getattr(document.file, "_committed", False):
            document.file.delete(save=False)
        raise
    return document


def get_accessible_document(
    *, document_id, allowed_scopes: Iterable[str]
) -> SourceDocument:
    """Resolve a document after the caller's permission layer derives its scopes.

    The identical denial is used for missing and unauthorized documents so callers
    do not leak the existence of restricted resources.
    """

    allowed = tuple(allowed_scopes)
    try:
        return (
            SourceDocument.objects.for_scopes(allowed)
            .select_related("research_work")
            .get(
                pk=document_id,
                research_work__status="published",
                research_work__visibility_scope__in=allowed,
            )
        )
    except SourceDocument.DoesNotExist as exc:
        raise PermissionDenied("Document unavailable.") from exc


def open_document_for_download(document: SourceDocument):
    """Return a binary file handle for an already-authorized response."""

    document.file.open("rb")
    return document.file


def queue_document_extraction(document: SourceDocument) -> None:
    del document
    raise NotImplementedError("PDF extraction is scheduled for Phase 2.")
