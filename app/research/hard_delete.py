from __future__ import annotations

from collections.abc import Iterable

from django.contrib.auth import get_user_model
from django.db import connections, router, transaction
from django.db.models import Q, QuerySet

from documents.models import DocumentChunk, SourceDocument
from professors.models import Professor

from .models import Award, ResearchWork, ResearchWorkTransition, WorkAdvisor


def _raw_delete(queryset: QuerySet) -> int:
    """Delete rows while intentionally bypassing append-only model guards."""

    if not queryset.exists():
        return 0
    return queryset._raw_delete(queryset.db)


def _raw_null_foreign_key(*, database: str, model, field_name: str, value) -> None:
    connection = connections[database]
    field = model._meta.get_field(field_name)
    table = connection.ops.quote_name(model._meta.db_table)
    column = connection.ops.quote_name(f"{field_name}_id")
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET {column}=NULL WHERE {column}=%s",
            [field.get_db_prep_save(value.pk, connection=connection)],
        )


def _as_queryset(model, values: Iterable | QuerySet) -> QuerySet:
    if isinstance(values, QuerySet):
        return values
    primary_keys = [value.pk for value in values]
    return model._default_manager.filter(pk__in=primary_keys)


def hard_delete_source_documents(documents: Iterable[SourceDocument] | QuerySet) -> int:
    """Purge source PDFs and derived/protected records for high-risk admin delete."""

    document_queryset = _as_queryset(SourceDocument, documents)
    document_ids = list(document_queryset.values_list("id", flat=True))
    if not document_ids:
        return 0

    database = router.db_for_write(SourceDocument)
    deleted_documents = 0
    with transaction.atomic(using=database):
        source_documents = SourceDocument.objects.using(database).filter(id__in=document_ids)
        chunks = DocumentChunk.objects.using(database).filter(
            source_document_id__in=document_ids
        )

        from rag.models import CitationSource, EmbeddingRecord, VectorChunk
        from review.models import (
            AIExtractionEvidence,
            AIExtractionJob,
            AIExtractionResult,
            ReviewDecision,
            ReviewItem,
        )

        vector_chunks = VectorChunk.objects.using(database).filter(source_chunk__in=chunks)
        EmbeddingRecord.objects.using(database).filter(vector_chunk__in=vector_chunks).delete()
        CitationSource.objects.using(database).filter(document_chunk__in=chunks).delete()

        extraction_results = AIExtractionResult.objects.using(database).filter(
            Q(primary_evidence_chunk__in=chunks) | Q(evidence_links__document_chunk__in=chunks)
        ).distinct()
        review_items = ReviewItem.objects.using(database).filter(
            extraction_result__in=extraction_results
        )
        _raw_delete(ReviewDecision.objects.using(database).filter(review_item__in=review_items))
        _raw_delete(review_items)
        _raw_delete(
            AIExtractionEvidence.objects.using(database).filter(
                Q(document_chunk__in=chunks) | Q(extraction_result__in=extraction_results)
            )
        )
        extraction_result_ids = list(extraction_results.values_list("id", flat=True))
        _raw_delete(
            AIExtractionResult.objects.using(database).filter(id__in=extraction_result_ids)
        )
        AIExtractionJob.objects.using(database).filter(source_document_id__in=document_ids).delete()

        DocumentChunk.objects.using(database).filter(source_document_id__in=document_ids).delete()
        for document in source_documents:
            document.delete()
            deleted_documents += 1
    return deleted_documents


def hard_delete_research_work(research_work: ResearchWork) -> None:
    """Purge a research work even when governance/audit rows would protect it."""

    database = router.db_for_write(ResearchWork, instance=research_work)
    with transaction.atomic(using=database):
        work = ResearchWork.objects.using(database).select_for_update().get(pk=research_work.pk)

        hard_delete_source_documents(
            SourceDocument.objects.using(database).filter(research_work=work)
        )

        from knowledge_graph.models import KnowledgeEdge, KnowledgeNode
        from knowledge_graph.registry import NodeType
        from review.models import ReviewDecision, ReviewItem

        review_items = ReviewItem.objects.using(database).filter(target_id=work.id)
        _raw_delete(ReviewDecision.objects.using(database).filter(review_item__in=review_items))
        _raw_delete(review_items)
        _raw_delete(ResearchWorkTransition.objects.using(database).filter(research_work=work))
        Award.objects.using(database).filter(research_work=work).delete()
        KnowledgeEdge.objects.using(database).filter(evidence_source_id=work.id).delete()
        KnowledgeNode.objects.using(database).filter(
            node_type=NodeType.RESEARCH_WORK,
            object_id=work.id,
        ).delete()
        work.delete()


def hard_delete_professor(professor: Professor) -> None:
    """Purge a professor profile by removing or detaching protected references."""

    database = router.db_for_write(Professor, instance=professor)
    with transaction.atomic(using=database):
        profile = Professor.objects.using(database).select_for_update().get(pk=professor.pk)

        from knowledge_graph.models import KnowledgeNode
        from knowledge_graph.registry import NodeType

        WorkAdvisor.objects.using(database).filter(professor=profile).delete()
        Award.objects.using(database).filter(advisor=profile).update(advisor=None)
        KnowledgeNode.objects.using(database).filter(
            node_type=NodeType.PROFESSOR,
            object_id=profile.id,
        ).delete()
        profile.delete()


def hard_delete_user(user) -> None:
    """Purge a user after detaching/deleting protected governance references."""

    User = get_user_model()
    database = router.db_for_write(User, instance=user)
    with transaction.atomic(using=database):
        target = User.objects.using(database).select_for_update().get(pk=user.pk)

        from review.models import ReviewDecision

        _raw_null_foreign_key(
            database=database,
            model=ResearchWorkTransition,
            field_name="actor",
            value=target,
        )
        _raw_null_foreign_key(
            database=database,
            model=ReviewDecision,
            field_name="assigned_to_snapshot",
            value=target,
        )
        _raw_delete(ReviewDecision.objects.using(database).filter(reviewer=target))
        target.delete()
