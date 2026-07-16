from celery import shared_task


@shared_task(name="documents.extract_source_document")
def extract_source_document(document_id: str) -> None:
    del document_id
    raise NotImplementedError("PDF parsing and OCR are intentionally deferred to Phase 2.")

