import hashlib
import tempfile
import uuid

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from documents.choices import VisibilityScope
from documents.forms import SourceDocumentUploadForm
from documents.models import DocumentChunk, SourceDocument, private_document_upload_to
from documents.storage import (
    PrivateDocumentStorage,
    PrivateDocumentURLUnavailable,
    private_document_storage,
)
from research.models import ResearchWork


class SourceDocumentSecurityTests(SimpleTestCase):
    pdf_bytes = b"%PDF-1.4\nsecure fixture\n%%EOF"

    def test_upload_metadata_is_derived_and_checksum_is_streamed(self):
        upload = SimpleUploadedFile(
            "student-paper.pdf",
            self.pdf_bytes,
            content_type="application/pdf",
        )
        document = SourceDocument(file=upload)

        document._prepare_upload_metadata()

        self.assertEqual(document.original_filename, "student-paper.pdf")
        self.assertEqual(document.mime_type, "application/pdf")
        self.assertEqual(document.file_size, len(self.pdf_bytes))
        self.assertEqual(
            document.checksum,
            hashlib.sha256(self.pdf_bytes).hexdigest(),
        )

    def test_original_filename_is_not_user_editable_or_in_upload_form(self):
        field = SourceDocument._meta.get_field("original_filename")
        self.assertFalse(field.editable)
        self.assertNotIn("original_filename", SourceDocumentUploadForm._meta.fields)

    def test_storage_key_is_opaque_and_does_not_contain_original_filename(self):
        document = SourceDocument()
        document.research_work_id = uuid.uuid4()

        key = private_document_upload_to(document, "student-name-and-title.pdf")

        self.assertNotIn("student-name-and-title", key)
        self.assertTrue(key.endswith(".pdf"))

    def test_private_storage_refuses_to_create_direct_url(self):
        storage = PrivateDocumentStorage(location="/tmp/private-document-test")

        with self.assertRaises(PrivateDocumentURLUnavailable):
            storage.url("research-works/example.pdf")

    def test_model_string_does_not_disclose_original_filename(self):
        document = SourceDocument(original_filename="student-private-name.pdf")
        self.assertNotIn("student-private-name", str(document))


class DocumentChunkVisibilityTests(SimpleTestCase):
    def source(self, scope: str) -> SourceDocument:
        return SourceDocument(visibility_scope=scope)

    def test_blank_chunk_scope_inherits_source_scope(self):
        chunk = DocumentChunk(
            source_document=self.source(VisibilityScope.TEACHER),
            chunk_index=0,
            text="Evidence",
            visibility_scope="",
        )

        chunk.clean()

        self.assertEqual(chunk.visibility_scope, VisibilityScope.TEACHER)

    def test_chunk_cannot_be_more_public_than_source(self):
        chunk = DocumentChunk(
            source_document=self.source(VisibilityScope.ADMIN),
            chunk_index=0,
            text="Restricted evidence",
            visibility_scope=VisibilityScope.PUBLIC,
        )

        with self.assertRaisesMessage(ValidationError, "less restrictive"):
            chunk.clean()

    def test_chunk_may_be_more_restrictive_than_source(self):
        chunk = DocumentChunk(
            source_document=self.source(VisibilityScope.PUBLIC),
            chunk_index=0,
            text="Restricted derivative evidence",
            visibility_scope=VisibilityScope.ADMIN,
        )

        chunk.clean()


class SourceDocumentPersistenceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.TemporaryDirectory()
        cls.settings_override = override_settings(
            PRIVATE_DOCUMENT_ROOT=cls.private_root.name
        )
        cls.settings_override.enable()
        # FileSystemStorage paths are cached properties and must follow this test's
        # isolated root even if another test accessed the singleton first.
        private_document_storage.__dict__.pop("base_location", None)
        private_document_storage.__dict__.pop("location", None)

    @classmethod
    def tearDownClass(cls):
        private_document_storage.__dict__.pop("base_location", None)
        private_document_storage.__dict__.pop("location", None)
        cls.settings_override.disable()
        cls.private_root.cleanup()
        super().tearDownClass()

    def test_save_derives_metadata_and_uses_an_opaque_storage_key(self):
        work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="Storage integration fixture",
            year=2026,
        )
        document = SourceDocument.objects.create(
            research_work=work,
            file=SimpleUploadedFile(
                "student-private-name.pdf",
                b"%PDF-1.4\nintegration fixture\n%%EOF",
                content_type="application/pdf",
            ),
        )

        self.assertEqual(document.original_filename, "student-private-name.pdf")
        self.assertNotIn("student-private-name", document.file.name)
        self.assertEqual(len(document.checksum), 64)
        with self.assertRaises(PrivateDocumentURLUnavailable):
            _ = document.file.url

    def test_saved_file_and_parent_work_are_immutable_but_visibility_is_administrable(self):
        first_work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="Immutable source fixture",
            year=2026,
        )
        second_work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="Other immutable source fixture",
            year=2026,
        )
        document = SourceDocument.objects.create(
            research_work=first_work,
            file=SimpleUploadedFile(
                "original.pdf",
                b"%PDF-1.4\nimmutable fixture\n%%EOF",
                content_type="application/pdf",
            ),
        )

        document.file = SimpleUploadedFile(
            "replacement.pdf",
            b"%PDF-1.4\nreplacement fixture\n%%EOF",
            content_type="application/pdf",
        )
        with self.assertRaisesMessage(ValidationError, "immutable"):
            document.save()

        document.refresh_from_db()
        document.research_work = second_work
        with self.assertRaisesMessage(ValidationError, "immutable"):
            document.save()

        with self.assertRaisesMessage(ValidationError, "immutable"):
            SourceDocument.objects.filter(pk=document.pk).update(
                research_work=second_work
            )

        document.refresh_from_db()
        document.research_work = second_work
        with self.assertRaisesMessage(ValidationError, "immutable"):
            SourceDocument.objects.bulk_update([document], ["research_work"])

        document.refresh_from_db()
        document.visibility_scope = VisibilityScope.PUBLIC
        document.save(update_fields={"visibility_scope"})
        document.refresh_from_db()
        self.assertEqual(document.visibility_scope, VisibilityScope.PUBLIC)

        self.assertEqual(
            SourceDocument.objects.filter(pk=document.pk).update(
                visibility_scope=VisibilityScope.ADMIN
            ),
            1,
        )
        document.refresh_from_db()
        self.assertEqual(document.visibility_scope, VisibilityScope.ADMIN)

        document.visibility_scope = VisibilityScope.STUDENT
        SourceDocument.objects.bulk_update([document], ["visibility_scope"])
        document.refresh_from_db()
        self.assertEqual(document.visibility_scope, VisibilityScope.STUDENT)

    def test_committed_row_or_cascade_deletion_removes_private_blob(self):
        for delete_parent in (False, True):
            with self.subTest(delete_parent=delete_parent):
                work = ResearchWork.objects.create(
                    work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
                    title=f"Deletion cleanup fixture {delete_parent}",
                    year=2026,
                )
                document = SourceDocument.objects.create(
                    research_work=work,
                    file=SimpleUploadedFile(
                        f"cleanup-{delete_parent}.pdf",
                        f"%PDF-1.4\ncleanup {delete_parent}\n%%EOF".encode(),
                        content_type="application/pdf",
                    ),
                )
                storage = document.file.storage
                file_name = document.file.name
                self.assertTrue(storage.exists(file_name))

                with self.captureOnCommitCallbacks(execute=True):
                    if delete_parent:
                        work.delete()
                    else:
                        SourceDocument.objects.filter(pk=document.pk).delete()

                self.assertFalse(storage.exists(file_name))
