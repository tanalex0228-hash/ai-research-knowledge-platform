import tempfile

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Role, User, UserRole
from accounts.permissions import VisibilityScope
from ai.models import PromptVersion
from documents.models import DocumentChunk, SourceDocument
from professors.models import Professor
from research.models import (
    ResearchWork,
    ResearchWorkStatus,
    ResearchWorkType,
    WorkAdvisor,
)
from review.admin import ReviewItemAdmin
from review.models import (
    AIExtractionJob,
    AIExtractionResult,
    ExtractionResultType,
    ReviewAction,
    ReviewDecision,
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

    def make_review_item(
        self,
        *,
        target_type: str = ReviewTargetType.RESEARCH_WORK,
        target_id=None,
    ) -> ReviewItem:
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
            target_type=target_type,
            target_id=target_id or chunk.source_document.research_work_id,
            field_path="fields",
            current_value={},
            candidate_value=result.candidate_data,
        )

    def make_teacher(self, suffix: str, *, active: bool = True):
        user = User.objects.create_user(
            username=f"teacher-{suffix}",
            email=f"teacher-{suffix}@example.test",
            is_active=active,
        )
        teacher_role = Role.objects.get(slug="teacher")
        UserRole.objects.create(user=user, role=teacher_role)
        professor = Professor.objects.create(
            user=user,
            display_name=f"Teacher {suffix}",
        )
        return user, professor

    def make_admin(self) -> User:
        return User.objects.create_superuser(
            username=f"review-admin-{User.objects.count()}",
            email=f"review-admin-{User.objects.count()}@example.test",
            password="test-password",
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
        admin = self.make_admin()

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

        with self.assertRaises(ValidationError):
            ReviewDecision.objects.filter(pk=decision.pk).update(reason="Bypass")
        with self.assertRaises(ValidationError):
            ReviewDecision.objects.filter(pk=decision.pk).delete()

    def test_governance_fields_cannot_be_mutated_directly(self):
        item = self.make_review_item()
        item.state = ReviewState.APPROVED
        item.resolved_at = timezone.now()

        with self.assertRaises(ValidationError):
            item.save()
        item.refresh_from_db()
        self.assertEqual(item.state, ReviewState.PENDING)

        item.candidate_value = {"slug": "silently-rewritten"}
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(ValidationError):
            ReviewItem.objects.filter(pk=item.pk).update(state=ReviewState.ARCHIVED)

        bypass = ReviewItem(
            extraction_result=item.extraction_result,
            target_type=item.target_type,
            target_id=item.target_id,
            field_path=item.field_path,
            candidate_value=item.candidate_value,
            state=ReviewState.APPROVED,
            resolved_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            ReviewItem.objects.bulk_create([bypass])

    def test_decision_cannot_be_appended_outside_service(self):
        item = self.make_review_item()
        admin = self.make_admin()
        decision = ReviewDecision(
            review_item=item,
            reviewer=admin,
            action=ReviewAction.APPROVE,
            previous_state=ReviewState.PENDING,
            resulting_state=ReviewState.APPROVED,
        )

        with self.assertRaises(ValidationError):
            decision.save()
        self.assertFalse(ReviewDecision.objects.exists())

    def test_teacher_review_and_terminal_transition_matrix(self):
        item = self.make_review_item()
        admin = self.make_admin()
        teacher, professor = self.make_teacher("owner")
        WorkAdvisor.objects.create(
            research_work_id=item.target_id,
            professor=professor,
        )

        first = decide_review_item(
            item=item,
            reviewer=admin,
            action=ReviewAction.REQUEST_TEACHER_REVIEW,
            assign_to=teacher,
            reason="Request subject-owner review.",
        )
        item.refresh_from_db()
        self.assertEqual(item.state, ReviewState.TEACHER_REVIEW)
        self.assertEqual(item.assigned_to, teacher)
        self.assertIsNone(item.resolved_at)
        self.assertEqual(first.resulting_state, ReviewState.TEACHER_REVIEW)
        self.assertEqual(first.assigned_to_snapshot, teacher)

        with self.assertRaises(ValidationError):
            decide_review_item(
                item=item,
                reviewer=admin,
                action=ReviewAction.REQUEST_TEACHER_REVIEW,
                assign_to=teacher,
            )

        second = decide_review_item(
            item=item,
            reviewer=admin,
            action=ReviewAction.APPROVE,
        )
        item.refresh_from_db()
        self.assertEqual(item.state, ReviewState.APPROVED)
        self.assertIsNone(item.assigned_to)
        self.assertIsNotNone(item.resolved_at)
        self.assertEqual(second.previous_state, ReviewState.TEACHER_REVIEW)
        self.assertEqual(first.assigned_to_snapshot, teacher)
        self.assertIsNone(second.assigned_to_snapshot)
        self.assertEqual(item.decisions.count(), 2)

        with self.assertRaises(ValidationError):
            decide_review_item(
                item=item,
                reviewer=admin,
                action=ReviewAction.ARCHIVE,
            )
        self.assertEqual(item.decisions.count(), 2)

    def test_teacher_assignment_requires_active_role_and_work_ownership(self):
        item = self.make_review_item()
        admin = self.make_admin()
        teacher, _professor = self.make_teacher("not-advisor")

        with self.assertRaises(ValidationError):
            decide_review_item(
                item=item,
                reviewer=admin,
                action=ReviewAction.REQUEST_TEACHER_REVIEW,
                assign_to=teacher,
            )

        inactive_teacher, inactive_professor = self.make_teacher(
            "inactive",
            active=False,
        )
        WorkAdvisor.objects.create(
            research_work_id=item.target_id,
            professor=inactive_professor,
        )
        with self.assertRaises(ValidationError):
            decide_review_item(
                item=item,
                reviewer=admin,
                action=ReviewAction.REQUEST_TEACHER_REVIEW,
                assign_to=inactive_teacher,
            )

    def test_professor_target_requires_profile_owner(self):
        owner, owner_profile = self.make_teacher("profile-owner")
        other, _other_profile = self.make_teacher("other-profile")
        item = self.make_review_item(
            target_type=ReviewTargetType.PROFESSOR,
            target_id=owner_profile.pk,
        )
        admin = self.make_admin()

        with self.assertRaises(ValidationError):
            decide_review_item(
                item=item,
                reviewer=admin,
                action=ReviewAction.REQUEST_TEACHER_REVIEW,
                assign_to=other,
            )

        decide_review_item(
            item=item,
            reviewer=admin,
            action=ReviewAction.REQUEST_TEACHER_REVIEW,
            assign_to=owner,
        )
        item.refresh_from_db()
        self.assertEqual(item.assigned_to, owner)

    def test_teacher_review_rejects_targets_without_teacher_ownership_model(self):
        teacher, _profile = self.make_teacher("taxonomy-target")
        item = self.make_review_item(target_type=ReviewTargetType.TAXONOMY)

        with self.assertRaises(ValidationError):
            decide_review_item(
                item=item,
                reviewer=self.make_admin(),
                action=ReviewAction.REQUEST_TEACHER_REVIEW,
                assign_to=teacher,
            )

    def test_inactive_superuser_cannot_decide_review_item(self):
        admin = self.make_admin()
        admin.is_active = False
        admin.save(update_fields={"is_active"})

        with self.assertRaises(PermissionDenied):
            decide_review_item(
                item=self.make_review_item(),
                reviewer=admin,
                action=ReviewAction.APPROVE,
            )

    def test_review_admin_exposes_only_governed_actions(self):
        model_admin = ReviewItemAdmin(ReviewItem, AdminSite())

        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))
        for field_name in (
            "state",
            "assigned_to",
            "resolved_at",
            "candidate_value",
            "current_value",
        ):
            self.assertIn(field_name, model_admin.readonly_fields)
        self.assertEqual(
            model_admin.actions,
            ("approve_selected", "reject_selected", "archive_selected"),
        )

    def test_review_item_explainability_fields(self):
        from review.services import create_extraction_job_and_candidates
        # Set up a source document with a dummy file containing text
        work = ResearchWork.objects.create(
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            title="Explainability Test Work",
            year=2025,
            status=ResearchWorkStatus.UPLOADED,
            visibility_scope=VisibilityScope.PUBLIC,
        )
        doc = SourceDocument.objects.create(
            research_work=work,
            file=SimpleUploadedFile(
                "explainability.pdf",
                b"%PDF-1.4\nExplainability Test Content\n%%EOF",
                content_type="application/pdf",
            ),
            visibility_scope=VisibilityScope.PUBLIC,
        )
        # Create a document chunk to simulate parsing
        chunk = DocumentChunk.objects.create(
            source_document=doc,
            chunk_index=0,
            text="Explainability Test Content - Abstract: This is about research fields.",
            token_count=10,
            visibility_scope=VisibilityScope.PUBLIC,
        )
        # Call candidate creation
        job = create_extraction_job_and_candidates(doc)
        
        # Verify that review items have confidence, model, evidence, source_text, and extraction_reason populated
        items = ReviewItem.objects.filter(target_id=work.id)
        self.assertTrue(items.exists())
        for item in items:
            self.assertIsNotNone(item.confidence)
            self.assertIsNotNone(item.model)
            self.assertIsNotNone(item.evidence)
            self.assertIsNotNone(item.source_text)
            self.assertIsNotNone(item.extraction_reason)
