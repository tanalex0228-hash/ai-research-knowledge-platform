import fitz  # PyMuPDF
from celery import shared_task
from django.contrib.auth import get_user_model
from django.db import transaction
from documents.choices import ExtractionStatus
from documents.models import DocumentChunk, SourceDocument
from research.services import transition_research_work
from review.services import create_extraction_job_and_candidates


@shared_task(name="documents.extract_source_document")
def extract_source_document(document_id: str) -> None:
    try:
        with transaction.atomic():
            document = SourceDocument.objects.select_for_update().get(pk=document_id)
            if document.extraction_status not in (ExtractionStatus.PENDING, ExtractionStatus.QUEUED):
                return
            document.extraction_status = ExtractionStatus.PROCESSING
            document.save(update_fields=["extraction_status"])
    except SourceDocument.DoesNotExist:
        return

    try:
        work = document.research_work
        actor = document.uploaded_by
        if not actor:
            User = get_user_model()
            actor = User.objects.filter(is_superuser=True).first()

        if work.status == "uploaded":
            transition_research_work(
                research_work=work,
                to_status="parsed",
                actor=actor,
                reason="PDF text content parsing started.",
                request_id="task-extraction-001",
            )

        file_bytes = document.file.read()
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.page_count <= 0:
            raise ValueError("The uploaded PDF is empty.")

        total_text = ""
        page_texts = []
        for i, page in enumerate(doc):
            text = page.get_text("text")
            page_texts.append((i + 1, text))
            total_text += text

        if len(total_text.strip()) < 100:
            with transaction.atomic():
                document = SourceDocument.objects.select_for_update().get(pk=document_id)
                document.extraction_status = ExtractionStatus.FAILED
                document.extraction_error = "ocr_unavailable"
                document.save(update_fields=["extraction_status", "extraction_error"])
            return

        with transaction.atomic():
            document = SourceDocument.objects.select_for_update().get(pk=document_id)
            document.chunks.all().delete()

            from django.utils import timezone
            import hashlib
            from rag.models import VectorDocument, VectorChunk, VectorStatus
            from rag.services.embeddings import persist_local_embedding

            vector_doc, _ = VectorDocument.objects.update_or_create(
                source_document=document,
                defaults={
                    "status": VectorStatus.READY,
                    "visibility_scope": document.visibility_scope,
                    "chunk_strategy_version": "page_v1",
                    "indexed_at": timezone.now(),
                }
            )
            
            for page_num, text in page_texts:
                if not text.strip():
                    continue
                chunk = DocumentChunk.objects.create(
                    source_document=document,
                    chunk_index=page_num - 1,
                    page_start=page_num,
                    page_end=page_num,
                    char_start=0,
                    char_end=len(text),
                    text=text,
                    token_count=len(text.split()),
                    chunk_strategy_version="page_v1",
                    visibility_scope=document.visibility_scope,
                )

                checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
                vchunk = VectorChunk.objects.create(
                    vector_document=vector_doc,
                    source_chunk=chunk,
                    status=VectorStatus.READY,
                    visibility_scope=document.visibility_scope,
                    content_checksum=checksum,
                )
                persist_local_embedding(vchunk)

            document.extraction_status = ExtractionStatus.SUCCEEDED
            document.extraction_error = ""
            document.save(update_fields=["extraction_status", "extraction_error"])

        work.refresh_from_db()
        if work.status == "parsed":
            transition_research_work(
                research_work=work,
                to_status="ai_extracted",
                actor=actor,
                reason="Deterministic metadata candidates extracted.",
                request_id="task-extraction-001",
            )

        document = SourceDocument.objects.get(pk=document_id)
        create_extraction_job_and_candidates(document)

        work.refresh_from_db()
        if work.status == "ai_extracted":
            transition_research_work(
                research_work=work,
                to_status="under_review",
                actor=actor,
                reason="Candidates entered review queue.",
                request_id="task-extraction-001",
            )

    except Exception as exc:
        with transaction.atomic():
            try:
                document = SourceDocument.objects.select_for_update().get(pk=document_id)
                document.extraction_status = ExtractionStatus.FAILED
                document.extraction_error = str(exc)
                document.save(update_fields=["extraction_status", "extraction_error"])
            except SourceDocument.DoesNotExist:
                pass

