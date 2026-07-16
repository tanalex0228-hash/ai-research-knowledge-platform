from django.core.exceptions import PermissionDenied
from django.test import TestCase

from documents.services import get_accessible_document
from research.models import ResearchWork


class AccessibleDocumentServiceTests(TestCase):
    def work(self, *, title: str, status="published", visibility_scope="public"):
        return ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title=title,
            year=2026,
            status=status,
            visibility_scope=visibility_scope,
        )

    def document(self, work, *, visibility_scope="public"):
        return work.source_documents.create(
            file="opaque-test-key.pdf",
            visibility_scope=visibility_scope,
        )

    def test_returns_only_when_document_and_published_parent_are_in_scope(self):
        document = self.document(self.work(title="Accessible service fixture"))

        resolved = get_accessible_document(
            document_id=document.id,
            allowed_scopes=("public",),
        )

        self.assertEqual(resolved, document)
        self.assertEqual(resolved.research_work.status, "published")

    def test_rejects_public_document_attached_to_restricted_parent(self):
        document = self.document(
            self.work(
                title="Restricted parent service fixture",
                visibility_scope="teacher",
            )
        )

        with self.assertRaisesMessage(PermissionDenied, "unavailable"):
            get_accessible_document(
                document_id=document.id,
                allowed_scopes=("public",),
            )

    def test_rejects_document_attached_to_unpublished_parent(self):
        document = self.document(
            self.work(title="Draft parent service fixture", status="draft")
        )

        with self.assertRaisesMessage(PermissionDenied, "unavailable"):
            get_accessible_document(
                document_id=document.id,
                allowed_scopes=("public",),
            )

