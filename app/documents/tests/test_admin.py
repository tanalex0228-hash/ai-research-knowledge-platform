import tempfile
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from accounts.models import AuditLog, User
from documents.admin import DocumentChunkAdmin, SourceDocumentAdmin
from documents.models import DocumentChunk, SourceDocument
from documents.storage import private_document_storage
from research.models import ResearchWork, ResearchWorkType


class DocumentAdminGroupPermissionTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.TemporaryDirectory()
        cls.settings_override = override_settings(PRIVATE_DOCUMENT_ROOT=cls.private_root.name)
        cls.settings_override.enable()
        private_document_storage.__dict__.pop("base_location", None)
        private_document_storage.__dict__.pop("location", None)

    @classmethod
    def tearDownClass(cls):
        private_document_storage.__dict__.pop("base_location", None)
        private_document_storage.__dict__.pop("location", None)
        cls.settings_override.disable()
        cls.private_root.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.factory = RequestFactory()
        self.group = Group.objects.create(name="Lab_Member")
        self.user = User.objects.create_user(
            username="document-lab-member", email="document-lab@example.test", is_staff=True
        )
        self.user.groups.add(self.group)
        self.work = ResearchWork.objects.create(
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            title="Unrelated research work",
            year=2026,
        )
        self.document = SourceDocument.objects.create(
            research_work=self.work,
            file=SimpleUploadedFile(
                "fixture.pdf", b"%PDF-1.4\nfixture\n%%EOF", content_type="application/pdf"
            ),
        )

    def grant(self, model, *actions):
        content_type = ContentType.objects.get_for_model(model)
        self.group.permissions.add(
            *Permission.objects.filter(
                content_type=content_type,
                codename__in=[f"{action}_{model._meta.model_name}" for action in actions],
            )
        )
        self.user = User.objects.get(pk=self.user.pk)

    def request(self):
        request = self.factory.get("/admin/documents/")
        request.user = self.user
        return request

    def test_source_document_crud_is_granted_by_group_without_advisor_link(self):
        self.grant(SourceDocument, "view", "add", "change", "delete")
        model_admin = SourceDocumentAdmin(SourceDocument, admin.site)
        request = self.request()

        self.assertIn(self.document, model_admin.get_queryset(request))
        self.assertTrue(model_admin.has_module_permission(request))
        self.assertTrue(model_admin.has_view_permission(request, self.document))
        self.assertTrue(model_admin.has_add_permission(request))
        self.assertTrue(model_admin.has_change_permission(request, self.document))
        self.assertTrue(model_admin.has_delete_permission(request, self.document))
        self.assertIn("set_selected_visibility", model_admin.get_actions(request))

    def test_document_chunk_crud_is_granted_by_group_permissions(self):
        chunk = DocumentChunk.objects.create(source_document=self.document, chunk_index=0, text="evidence")
        self.grant(DocumentChunk, "view", "add", "change", "delete")
        model_admin = DocumentChunkAdmin(DocumentChunk, admin.site)
        request = self.request()

        self.assertIn(chunk, model_admin.get_queryset(request))
        self.assertTrue(model_admin.has_add_permission(request))
        self.assertTrue(model_admin.has_change_permission(request, chunk))
        self.assertTrue(model_admin.has_delete_permission(request, chunk))

    def test_upload_audit_uses_request_id_and_rolls_back_on_audit_failure(self):
        self.grant(SourceDocument, "add")
        model_admin = SourceDocumentAdmin(SourceDocument, admin.site)
        request = self.request()
        request.request_id = "lab-upload-request-id"
        document = SourceDocument(
            research_work=self.work,
            file=SimpleUploadedFile(
                "uploaded.pdf", b"%PDF-1.4\nuploaded\n%%EOF", content_type="application/pdf"
            ),
        )

        with patch("documents.services.queue_document_extraction"):
            model_admin.save_model(request, document, form=None, change=False)

        audit = AuditLog.objects.get(target_id=document.id, event_type="source_document.uploaded")
        self.assertEqual(audit.actor, self.user)
        self.assertEqual(audit.request_id, "lab-upload-request-id")

        failed_document = SourceDocument(
            research_work=self.work,
            file=SimpleUploadedFile(
                "failed.pdf", b"%PDF-1.4\nfailed\n%%EOF", content_type="application/pdf"
            ),
        )
        with patch("documents.admin.record_audit_event", side_effect=RuntimeError("audit failed")), self.assertRaises(RuntimeError):
            model_admin.save_model(request, failed_document, form=None, change=False)
        self.assertFalse(SourceDocument.objects.filter(original_filename="failed.pdf").exists())
