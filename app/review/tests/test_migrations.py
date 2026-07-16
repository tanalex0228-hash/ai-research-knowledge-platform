from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ReviewGovernanceMigrationTests(TransactionTestCase):
    migrate_from = [("review", "0001_initial")]
    migrate_to = [("review", "0002_review_governance_constraints")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self.legacy_ids = self._create_legacy_rows(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    @staticmethod
    def _create_legacy_rows(apps):
        User = apps.get_model("accounts", "User")
        ResearchWork = apps.get_model("research", "ResearchWork")
        SourceDocument = apps.get_model("documents", "SourceDocument")
        DocumentChunk = apps.get_model("documents", "DocumentChunk")
        PromptVersion = apps.get_model("ai", "PromptVersion")
        Job = apps.get_model("review", "AIExtractionJob")
        Result = apps.get_model("review", "AIExtractionResult")
        ReviewItem = apps.get_model("review", "ReviewItem")
        ReviewDecision = apps.get_model("review", "ReviewDecision")

        reviewer = User.objects.create(
            username="legacy-reviewer",
            email="legacy-reviewer@example.test",
            password="!",
        )
        work = ResearchWork.objects.create(
            work_type="undergraduate_project",
            title="Legacy governance migration fixture",
            normalized_title="legacy governance migration fixture",
            year=2025,
            visibility_scope="teacher",
        )
        source = SourceDocument.objects.create(
            research_work=work,
            file="legacy-governance.pdf",
            original_filename="legacy-governance.pdf",
            mime_type="application/pdf",
            file_size=10,
            checksum="a" * 64,
            visibility_scope="teacher",
            uploaded_by=reviewer,
        )
        chunk = DocumentChunk.objects.create(
            source_document=source,
            chunk_index=0,
            text="Legacy evidence",
            token_count=2,
            visibility_scope="teacher",
        )
        prompt = PromptVersion.objects.create(
            key="legacy-governance",
            version=1,
            template="Legacy migration fixture",
        )
        job = Job.objects.create(
            source_document=source,
            prompt_version=prompt,
            schema_version="1",
            visibility_scope="teacher",
        )

        def make_item(suffix, *, state, assigned_to=None, resolved_at=None):
            result = Result.objects.create(
                job=job,
                result_type="field",
                candidate_data={"slug": f"legacy-{suffix}"},
                confidence=0.8,
                primary_evidence_chunk=chunk,
                schema_version="1",
                visibility_scope="teacher",
            )
            return ReviewItem.objects.create(
                extraction_result=result,
                target_type="research_work",
                target_id=work.id,
                field_path=f"fields.{suffix}",
                candidate_value=result.candidate_data,
                state=state,
                visibility_scope="teacher",
                assigned_to=assigned_to,
                resolved_at=resolved_at,
            )

        teacher_review = make_item(
            "teacher-review",
            state="teacher_review",
            assigned_to=reviewer,
        )
        legacy_decision = ReviewDecision.objects.create(
            review_item=teacher_review,
            reviewer=reviewer,
            action="request_teacher_review",
            previous_state="teacher_review",
            resulting_state="teacher_review",
            reason="Legacy service permitted a repeated request.",
        )
        terminal = make_item(
            "terminal",
            state="approved",
            assigned_to=reviewer,
            resolved_at=timezone.now(),
        )
        missing_resolution = make_item(
            "missing-resolution",
            state="rejected",
            assigned_to=reviewer,
        )
        orphan_assignment = make_item(
            "orphan-assignment",
            state="teacher_review",
        )
        return {
            "teacher_review": teacher_review.id,
            "legacy_decision": legacy_decision.id,
            "terminal": terminal.id,
            "missing_resolution": missing_resolution.id,
            "orphan_assignment": orphan_assignment.id,
        }

    def test_upgrade_repairs_item_state_and_preserves_legacy_decision(self):
        ReviewItem = self.apps.get_model("review", "ReviewItem")
        ReviewDecision = self.apps.get_model("review", "ReviewDecision")

        teacher_review = ReviewItem.objects.get(pk=self.legacy_ids["teacher_review"])
        self.assertEqual(teacher_review.state, "teacher_review")
        self.assertIsNotNone(teacher_review.assigned_to_id)
        self.assertIsNone(teacher_review.resolved_at)

        terminal = ReviewItem.objects.get(pk=self.legacy_ids["terminal"])
        self.assertIsNone(terminal.assigned_to_id)
        self.assertIsNotNone(terminal.resolved_at)

        missing = ReviewItem.objects.get(pk=self.legacy_ids["missing_resolution"])
        self.assertIsNone(missing.assigned_to_id)
        self.assertIsNotNone(missing.resolved_at)

        orphan = ReviewItem.objects.get(pk=self.legacy_ids["orphan_assignment"])
        self.assertEqual(orphan.state, "pending")
        self.assertIsNone(orphan.resolved_at)

        self.assertTrue(
            ReviewDecision.objects.filter(
                pk=self.legacy_ids["legacy_decision"],
                previous_state="teacher_review",
                action="request_teacher_review",
                resulting_state="teacher_review",
                assigned_to_snapshot_id=teacher_review.assigned_to_id,
            ).exists()
        )
