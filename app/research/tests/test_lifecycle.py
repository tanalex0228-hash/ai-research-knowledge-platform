from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from accounts.models import User
from research.models import (
    ResearchWork,
    ResearchWorkStatus,
    ResearchWorkTransition,
    ResearchWorkType,
)
from research.services import transition_research_work


class ResearchWorkLifecycleTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(
            username="lifecycle-admin",
            email="lifecycle-admin@example.test",
        )
        self.work = ResearchWork.objects.create(
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            title="Lifecycle research",
            year=2026,
        )

    def test_documented_lifecycle_records_actor_reason_and_request(self):
        sequence = (
            ResearchWorkStatus.UPLOADED,
            ResearchWorkStatus.PARSED,
            ResearchWorkStatus.AI_EXTRACTED,
            ResearchWorkStatus.UNDER_REVIEW,
            ResearchWorkStatus.APPROVED,
            ResearchWorkStatus.PUBLISHED,
            ResearchWorkStatus.UNDER_REVIEW,
            ResearchWorkStatus.REJECTED,
        )

        previous = ResearchWorkStatus.DRAFT
        for index, target in enumerate(sequence, start=1):
            transition = transition_research_work(
                research_work=self.work,
                to_status=target,
                actor=self.actor,
                reason=f"Lifecycle step {index}",
                request_id="request-lifecycle-1",
            )
            self.assertEqual(transition.from_status, previous)
            self.assertEqual(transition.to_status, target)
            self.assertEqual(transition.actor, self.actor)
            self.assertEqual(transition.reason, f"Lifecycle step {index}")
            self.assertEqual(transition.request_id, "request-lifecycle-1")
            previous = target

        self.work.refresh_from_db()
        self.assertEqual(self.work.status, ResearchWorkStatus.REJECTED)
        self.assertEqual(self.work.updated_by, self.actor)
        self.assertEqual(self.work.transitions.count(), len(sequence))

    def test_published_work_can_be_archived(self):
        work = ResearchWork.objects.create(
            work_type=ResearchWorkType.JOURNAL_ARTICLE,
            title="Published archive branch",
            year=2026,
            status=ResearchWorkStatus.PUBLISHED,
        )
        transition_research_work(
            research_work=work,
            to_status=ResearchWorkStatus.ARCHIVED,
            actor=self.actor,
            reason="Retired from catalog",
            request_id="request-archive-1",
        )
        work.refresh_from_db()
        self.assertEqual(work.status, ResearchWorkStatus.ARCHIVED)

    def test_trusted_import_can_create_an_existing_non_draft_state(self):
        imported = ResearchWork.objects.create(
            work_type=ResearchWorkType.MASTER_THESIS,
            title="Imported historical publication",
            year=2020,
            status=ResearchWorkStatus.PUBLISHED,
        )
        self.assertEqual(imported.status, ResearchWorkStatus.PUBLISHED)
        self.assertFalse(imported.transitions.exists())

    def test_illegal_transition_is_atomic(self):
        with self.assertRaises(ValidationError):
            transition_research_work(
                research_work=self.work,
                to_status=ResearchWorkStatus.PUBLISHED,
                actor=self.actor,
                reason="Skip governance",
                request_id="request-invalid-1",
            )

        self.work.refresh_from_db()
        self.assertEqual(self.work.status, ResearchWorkStatus.DRAFT)
        self.assertFalse(self.work.transitions.exists())

    def test_transition_requires_reason_and_request_id(self):
        for reason, request_id in (("", "request-1"), ("Document received", "")):
            with self.subTest(reason=reason, request_id=request_id):
                with self.assertRaises(ValidationError):
                    transition_research_work(
                        research_work=self.work,
                        to_status=ResearchWorkStatus.UPLOADED,
                        actor=self.actor,
                        reason=reason,
                        request_id=request_id,
                    )
        self.assertFalse(self.work.transitions.exists())

    def test_transition_requires_active_authenticated_actor(self):
        inactive = User.objects.create_user(
            username="inactive-lifecycle-actor",
            email="inactive-lifecycle@example.test",
            is_active=False,
        )
        for actor in (None, inactive):
            with self.subTest(actor=actor):
                with self.assertRaises(ValidationError):
                    transition_research_work(
                        research_work=self.work,
                        to_status=ResearchWorkStatus.UPLOADED,
                        actor=actor,
                        reason="Document received",
                        request_id="request-actor-1",
                    )
        self.assertFalse(self.work.transitions.exists())

    def test_direct_model_and_queryset_status_mutation_are_rejected(self):
        self.work.status = ResearchWorkStatus.UPLOADED
        with self.assertRaises(ValidationError):
            self.work.save(update_fields={"status"})

        self.work.refresh_from_db()
        with self.assertRaises(ValidationError):
            ResearchWork.objects.filter(pk=self.work.pk).update(
                status=ResearchWorkStatus.UPLOADED
            )
        self.assertEqual(
            ResearchWork.objects.get(pk=self.work.pk).status,
            ResearchWorkStatus.DRAFT,
        )

        self.work.status = ResearchWorkStatus.UPLOADED
        with self.assertRaises(ValidationError):
            ResearchWork.objects.bulk_update([self.work], ["status"])
        self.assertEqual(
            ResearchWork.objects.get(pk=self.work.pk).status,
            ResearchWorkStatus.DRAFT,
        )

    def test_transition_rows_cannot_be_forged_outside_service(self):
        values = {
            "research_work": self.work,
            "from_status": ResearchWorkStatus.DRAFT,
            "to_status": ResearchWorkStatus.UPLOADED,
            "actor": self.actor,
            "reason": "Forged",
            "request_id": "request-forged-1",
        }
        with self.assertRaises(ValidationError):
            ResearchWorkTransition.objects.create(**values)
        with self.assertRaises(ValidationError):
            ResearchWorkTransition.objects.bulk_create(
                [ResearchWorkTransition(**values)]
            )
        self.assertFalse(ResearchWorkTransition.objects.exists())

    def test_transition_audit_rows_are_immutable(self):
        transition = transition_research_work(
            research_work=self.work,
            to_status=ResearchWorkStatus.UPLOADED,
            actor=self.actor,
            reason="Document received",
            request_id="request-audit-1",
        )
        transition.reason = "Rewritten"

        with self.assertRaises(ValidationError):
            transition.save()
        with self.assertRaises(ValidationError):
            transition.delete()
        with self.assertRaises(ValidationError):
            ResearchWorkTransition.objects.filter(pk=transition.pk).update(
                reason="Rewritten"
            )
        with self.assertRaises(ValidationError):
            ResearchWorkTransition.objects.filter(pk=transition.pk).delete()
        with self.assertRaises(ProtectedError):
            self.actor.delete()

        self.assertEqual(
            ResearchWorkTransition.objects.get(pk=transition.pk).reason,
            "Document received",
        )
