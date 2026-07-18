import fitz
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from documents.choices import ExtractionStatus
from documents.models import DocumentChunk, SourceDocument
from documents.tasks import extract_source_document
from research.models import ResearchWork
from taxonomy.models import ResearchField, ResearchMethod, TaxonomyStatus
from review.models import ReviewItem, ReviewState, AIExtractionJob


class PDFExtractionTaskTests(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin-extractor",
            email="admin@example.test",
            password="password",
        )
        self.work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="Test Extraction Work",
            year=2026,
            status="draft",
        )
        # Seed a research field and method to verify candidate matching
        self.field = ResearchField.objects.create(
            slug="esg",
            display_name="ESG",
            status=TaxonomyStatus.ACTIVE,
        )
        self.method = ResearchMethod.objects.create(
            slug="deep-learning",
            display_name="Deep Learning",
            status=TaxonomyStatus.ACTIVE,
        )

    def create_text_pdf(self, text: str) -> bytes:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), text)
        return doc.write()

    def test_successful_text_pdf_extraction_and_candidate_creation(self):
        pdf_content = self.create_text_pdf("This study focuses on ESG principles using Deep Learning algorithms. " * 5)
        uploaded = SimpleUploadedFile(
            "paper.pdf",
            pdf_content,
            content_type="application/pdf",
        )
        document = SourceDocument.objects.create(
            research_work=self.work,
            file=uploaded,
            uploaded_by=self.user,
        )
        self.assertEqual(self.work.status, "draft")

        from research.services import transition_research_work
        transition_research_work(
            research_work=self.work,
            to_status="uploaded",
            actor=self.user,
            reason="Simulate upload transition.",
            request_id="test-upload-001",
        )

        # Simulate celery task run
        extract_source_document(str(document.pk))

        # Refresh
        document.refresh_from_db()
        self.work.refresh_from_db()

        # Check status and chunks
        if document.extraction_status != ExtractionStatus.SUCCEEDED:
            print("EXTRACTION ERROR:", document.extraction_error)
        self.assertEqual(document.extraction_status, ExtractionStatus.SUCCEEDED)
        self.assertEqual(document.extraction_error, "")
        self.assertEqual(document.chunks.count(), 1)
        chunk = document.chunks.first()
        self.assertIn("ESG", chunk.text)

        # Check VectorDocument, VectorChunk, and EmbeddingRecord
        from rag.models import VectorDocument
        vdoc = VectorDocument.objects.filter(source_document=document).first()
        self.assertIsNotNone(vdoc)
        self.assertEqual(vdoc.status, "ready")
        self.assertEqual(vdoc.vector_chunks.count(), 1)
        vchunk = vdoc.vector_chunks.first()
        self.assertEqual(vchunk.source_chunk, chunk)
        self.assertEqual(vchunk.status, "ready")
        self.assertEqual(vchunk.embeddings.filter(is_active=True).count(), 1)
        emb = vchunk.embeddings.filter(is_active=True).first()
        self.assertEqual(emb.dimensions, 32)

        # Check ResearchWork status transitions
        self.assertEqual(self.work.status, "under_review")

        # Check AIExtractionJob
        job = AIExtractionJob.objects.filter(source_document=document).first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, "succeeded")

        # Check ReviewItems
        review_items = list(ReviewItem.objects.filter(target_id=self.work.id))
        self.assertEqual(len(review_items), 6)
        self.assertFalse(any(item.field_path == "year" for item in review_items))
        self.assertFalse(any(item.field_path == "work_type" for item in review_items))
        field_review = next(item for item in review_items if "fields" in item.field_path)
        self.assertEqual(field_review.candidate_value, {"slug": "esg"})
        self.assertEqual(field_review.state, ReviewState.PENDING)
        intelligence_review = next(
            item for item in review_items if item.field_path == "document_intelligence"
        )
        self.assertIn("database_actions", intelligence_review.candidate_value)
        self.assertTrue(
            intelligence_review.candidate_value["database_actions"]["research_fields"][0][
                "matched"
            ]
        )

    def test_scanned_pdf_enters_ocr_fallback(self):
        # Empty text PDF
        pdf_content = self.create_text_pdf("   ")
        uploaded = SimpleUploadedFile(
            "scanned.pdf",
            pdf_content,
            content_type="application/pdf",
        )
        document = SourceDocument.objects.create(
            research_work=self.work,
            file=uploaded,
            uploaded_by=self.user,
        )

        extract_source_document(str(document.pk))

        document.refresh_from_db()
        self.assertEqual(document.extraction_status, ExtractionStatus.FAILED)
        self.assertEqual(document.extraction_error, "ocr_unavailable")

    def test_corrupt_pdf_fails_closed(self):
        uploaded = SimpleUploadedFile(
            "corrupt.pdf",
            b"%PDF-1.4\ninvalid pdf data\n",
            content_type="application/pdf",
        )
        # Note: validate_pdf_upload raises ValidationError on magic mismatch.
        # But if somehow it is saved, extraction task should catch fitz exceptions.
        # Let's bypass validate_pdf_upload checks for this test by creating the doc manually
        # with an invalid signature using a patch/mock, or just write random bytes that fitz can't parse
        # but has correct magic signature.
        pdf_with_magic = b"%PDF-1.4\ncorrupt content\n"
        uploaded_corrupt = SimpleUploadedFile(
            "corrupt.pdf",
            pdf_with_magic,
            content_type="application/pdf",
        )
        document = SourceDocument.objects.create(
            research_work=self.work,
            file=uploaded_corrupt,
            uploaded_by=self.user,
        )

        extract_source_document(str(document.pk))

        document.refresh_from_db()
        self.assertEqual(document.extraction_status, ExtractionStatus.FAILED)
        self.assertNotEqual(document.extraction_error, "")
        self.assertNotEqual(document.extraction_error, "ocr_unavailable")
