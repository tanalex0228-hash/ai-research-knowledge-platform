import hashlib
import tempfile

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import Role, User, UserRole
from accounts.permissions import VisibilityScope
from documents.models import DocumentChunk, SourceDocument
from rag.models import (
    EmbeddingRecord,
    RetrievalLog,
    RetrievalStatus,
    VectorChunk,
    VectorDocument,
)
from rag.services import (
    DeterministicLocalEmbeddingService,
    PermissionAwareRetrievalService,
    SemanticRetrievalNotImplemented,
    persist_local_embedding,
)
from research.models import ResearchWork, ResearchWorkStatus, ResearchWorkType


class DocumentFixtureMixin:
    def setUp(self):
        super().setUp()
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()
        super().tearDown()

    def make_chunk(
        self,
        *,
        suffix: str,
        text: str,
        visibility: str = VisibilityScope.PUBLIC,
        status: str = ResearchWorkStatus.PUBLISHED,
    ) -> DocumentChunk:
        work = ResearchWork.objects.create(
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            title=f"Work {suffix}",
            abstract="",
            year=2025,
            status=status,
            visibility_scope=visibility,
        )
        upload = SimpleUploadedFile(
            f"{suffix}.pdf",
            f"%PDF-1.4\n{suffix}\n%%EOF".encode(),
            content_type="application/pdf",
        )
        source = SourceDocument.objects.create(
            research_work=work,
            file=upload,
            visibility_scope=visibility,
        )
        return DocumentChunk.objects.create(
            source_document=source,
            chunk_index=0,
            text=text,
            token_count=len(text.split()),
            visibility_scope=visibility,
        )


class VectorModelTests(DocumentFixtureMixin, TestCase):
    def test_local_embedding_round_trips_on_sqlite_with_provenance(self):
        chunk = self.make_chunk(suffix="vector", text="AI finance time series")
        vector_document = VectorDocument.objects.create(source_document=chunk.source_document)
        checksum = hashlib.sha256(chunk.text.encode()).hexdigest()
        vector_chunk = VectorChunk.objects.create(
            vector_document=vector_document,
            source_chunk=chunk,
            content_checksum=checksum,
        )

        record = persist_local_embedding(
            vector_chunk,
            service=DeterministicLocalEmbeddingService(dimensions=16),
        )
        record = EmbeddingRecord.objects.get(pk=record.pk)

        self.assertEqual(record.dimensions, 16)
        self.assertEqual(len(record.embedding), 16)
        self.assertEqual(record.input_checksum, checksum)
        self.assertEqual(record.vector_chunk.source_chunk, chunk)

    def test_vector_document_cannot_widen_source_visibility(self):
        chunk = self.make_chunk(
            suffix="restricted",
            text="restricted evidence",
            visibility=VisibilityScope.STUDENT,
        )
        vector_document = VectorDocument(
            source_document=chunk.source_document,
            visibility_scope=VisibilityScope.PUBLIC,
        )

        with self.assertRaises(ValidationError):
            vector_document.full_clean()


class PermissionAwareRetrievalTests(DocumentFixtureMixin, TestCase):
    def test_visitor_only_retrieves_public_published_evidence(self):
        public = self.make_chunk(suffix="public", text="AI finance public evidence")
        self.make_chunk(
            suffix="student",
            text="AI finance student evidence",
            visibility=VisibilityScope.STUDENT,
        )
        self.make_chunk(
            suffix="draft",
            text="AI finance draft evidence",
            status=ResearchWorkStatus.DRAFT,
        )

        result = PermissionAwareRetrievalService().retrieve_keyword(
            "AI finance",
            user=AnonymousUser(),
        )

        self.assertEqual([citation.document_chunk for citation in result.citations], [public])
        self.assertEqual(result.log.status, RetrievalStatus.COMPLETED)
        self.assertEqual(result.log.result_count, 1)
        self.assertEqual(result.log.permitted_document_scopes, [VisibilityScope.PUBLIC])

    def test_student_scope_is_derived_from_normalized_role(self):
        student_chunk = self.make_chunk(
            suffix="student-role",
            text="panel data evidence",
            visibility=VisibilityScope.STUDENT,
        )
        user = User.objects.create_user(
            username="student-rag",
            email="student-rag@example.test",
        )
        role = Role.objects.get(slug="student")
        UserRole.objects.create(user=user, role=role)

        result = PermissionAwareRetrievalService().retrieve_keyword(
            "panel data",
            user=user,
        )

        self.assertEqual([citation.document_chunk for citation in result.citations], [student_chunk])
        self.assertIn(VisibilityScope.STUDENT, result.log.permitted_document_scopes)

    def test_semantic_attempt_is_audited_and_stops(self):
        service = PermissionAwareRetrievalService()

        with self.assertRaises(SemanticRetrievalNotImplemented):
            service.retrieve_semantic("AI finance", user=AnonymousUser())

        log = RetrievalLog.objects.get()
        self.assertEqual(log.status, RetrievalStatus.NOT_IMPLEMENTED)
        self.assertEqual(log.result_count, 0)

    def test_token_free_query_cannot_return_all_chunks(self):
        self.make_chunk(suffix="no-browse", text="private-ish catalog evidence")

        with self.assertRaises(ValueError):
            PermissionAwareRetrievalService().retrieve_keyword("---", user=None)

        self.assertFalse(RetrievalLog.objects.exists())
