import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import User
from accounts.permissions import VisibilityScope
from ai.models import PromptVersion
from documents.models import DocumentChunk, SourceDocument
from research.models import ResearchWork, ResearchWorkStatus, ResearchWorkType
from review.models import (
    AIExtractionJob,
    AIExtractionResult,
    ExtractionResultType,
    ReviewAction,
    ReviewItem,
    ReviewState,
    ReviewTargetType,
)
from review.services import decide_review_item


class ReviewWorkflowTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.prompt = PromptVersion.objects.create(
            key="extract-metadata",
            version=1,
            template="Extract candidate metadata and cite evidence.",
        )

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    def make_chunk(self, suffix: str) -> DocumentChunk:
        work = ResearchWork.objects.create(
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            title=f"Review work {suffix}",
            year=2025,
            status=ResearchWorkStatus.PUBLISHED,
            visibility_scope=VisibilityScope.PUBLIC,
        )
        source = SourceDocument.objects.create(
            research_work=work,
            file=SimpleUploadedFile(
                f"{suffix}.pdf",
                f"%PDF-1.4\n{suffix}\n%%EOF".encode(),
                content_type="application/pdf",
            ),
            visibility_scope=VisibilityScope.PUBLIC,
        )
        return DocumentChunk.objects.create(
            source_document=source,
            chunk_index=0,
            text=f"Evidence for {suffix}",
            token_count=3,
            visibility_scope=VisibilityScope.PUBLIC,
        )

    def make_review_item(self) -> ReviewItem:
        chunk = self.make_chunk("candidate")
        job = AIExtractionJob.objects.create(
            source_document=chunk.source_document,
            prompt_version=self.prompt,
            schema_version="1",
        )
        result = AIExtractionResult.objects.create(
            job=job,
            result_type=ExtractionResultType.FIELD,
            candidate_data={"slug": "ai-finance"},
            confidence=0.82,
            primary_evidence_chunk=chunk,
            schema_version="1",
        )
        return ReviewItem.objects.create(
            extraction_result=result,
            target_type=ReviewTargetType.RESEARCH_WORK,
            target_id=chunk.source_document.research_work_id,
            field_path="fields",
            current_value={},
            candidate_value=result.candidate_data,
        )

    def test_candidate_primary_evidence_cannot_cross_documents(self):
        first = self.make_chunk("first")
        second = self.make_chunk("second")
        job = AIExtractionJob.objects.create(
            source_document=first.source_document,
            prompt_version=self.prompt,
            schema_version="1",
        )
        candidate = AIExtractionResult(
            job=job,
            result_type=ExtractionResultType.METHOD,
            candidate_data={"slug": "var"},
            confidence=0.7,
            primary_evidence_chunk=second,
            schema_version="1",
        )

        with self.assertRaises(ValidationError):
            candidate.full_clean()

    def test_admin_decision_updates_state_and_is_append_only(self):
        item = self.make_review_item()
        admin = User.objects.create_superuser(
            username="review-admin",
            email="review-admin@example.test",
            password="test-password",
        )

        decision = decide_review_item(
            item=item,
            reviewer=admin,
            action=ReviewAction.APPROVE,
            reason="Evidence supports this candidate.",
        )
        item.refresh_from_db()

        self.assertEqual(item.state, ReviewState.APPROVED)
        self.assertEqual(decision.previous_state, ReviewState.PENDING)
        decision.reason = "Rewritten audit"
        with self.assertRaises(ValidationError):
            decision.save()

