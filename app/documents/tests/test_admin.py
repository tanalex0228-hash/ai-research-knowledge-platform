import tempfile
from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings

from accounts.models import AuditLog, Role, User, UserRole
from documents.admin import DocumentChunkAdmin, SourceDocumentAdmin
from documents.models import DocumentChunk, SourceDocument
from documents.storage import private_document_storage
from professors.models import Professor
from research.models import ResearchWork, WorkAdvisor


class SourceDocumentAdminPermissionTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_root = tempfile.TemporaryDirectory()
        cls.settings_override = override_settings(
            PRIVATE_DOCUMENT_ROOT=cls.private_root.name
        )
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

    @classmethod
    def setUpTestData(cls):
        teacher_role = Role.objects.get(slug="teacher")
        admin_role = Role.objects.get(slug="admin")
        cls.teacher = User.objects.create_user(
            username="document-teacher",
            email="document-teacher@example.test",
            is_staff=True,
        )
        cls.other_teacher = User.objects.create_user(
            username="other-document-teacher",
            email="other-document-teacher@example.test",
            is_staff=True,
        )
        cls.platform_admin = User.objects.create_user(
            username="document-admin",
            email="document-admin@example.test",
            is_staff=True,
        )
        cls.unassigned_staff = User.objects.create_user(
            username="unassigned-staff",
            email="unassigned-staff@example.test",
            is_staff=True,
        )
        UserRole.objects.create(user=cls.teacher, role=teacher_role)
        UserRole.objects.create(user=cls.other_teacher, role=teacher_role)
        UserRole.objects.create(user=cls.platform_admin, role=admin_role)

        teacher_professor = Professor.objects.create(
            user=cls.teacher,
            display_name="Document Teacher",
        )
        other_professor = Professor.objects.create(
            user=cls.other_teacher,
            display_name="Other Document Teacher",
        )
        cls.teacher_work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="Teacher-owned document fixture",
            year=2026,
        )
        cls.other_work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="Other teacher document fixture",
            year=2026,
        )
        WorkAdvisor.objects.create(
            research_work=cls.teacher_work,
            professor=teacher_professor,
        )
        WorkAdvisor.objects.create(
            research_work=cls.other_work,
            professor=other_professor,
        )
        cls.teacher_document = cls._create_document(
            cls.teacher_work, "teacher-document.pdf"
        )
        cls.other_document = cls._create_document(
            cls.other_work, "other-document.pdf"
        )
        cls.teacher_chunk = DocumentChunk.objects.create(
            source_document=cls.teacher_document,
            chunk_index=0,
            text="Teacher-owned derived evidence.",
        )
        cls.other_chunk = DocumentChunk.objects.create(
            source_document=cls.other_document,
            chunk_index=0,
            text="Other teacher derived evidence.",
        )

    @staticmethod
    def _create_document(work, filename, visibility_scope="teacher"):
        return SourceDocument.objects.create(
            research_work=work,
            file=SimpleUploadedFile(
                filename,
                f"%PDF-1.4\nadmin permission fixture {filename}\n%%EOF".encode(),
                content_type="application/pdf",
            ),
            visibility_scope=visibility_scope,
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = SourceDocumentAdmin(SourceDocument, admin.site)

    def request_for(self, user):
        request = self.factory.get("/admin/documents/sourcedocument/")
        request.user = user
        return request

    def test_teacher_queryset_and_object_permissions_are_advisor_scoped(self):
        request = self.request_for(self.teacher)

        self.assertEqual(
            list(self.model_admin.get_queryset(request)),
            [self.teacher_document],
        )
        self.assertFalse(
            self.model_admin.has_change_permission(request, self.teacher_document)
        )
        self.assertFalse(
            self.model_admin.has_delete_permission(request, self.teacher_document)
        )
        self.assertFalse(
            self.model_admin.has_view_permission(request, self.other_document)
        )
        self.assertFalse(
            self.model_admin.has_change_permission(request, self.other_document)
        )
        self.assertFalse(
            self.model_admin.has_delete_permission(request, self.other_document)
        )

    def test_teacher_add_form_only_offers_advised_works(self):
        request = self.request_for(self.teacher)

        form_class = self.model_admin.get_form(request)
        work_ids = set(
            form_class.base_fields["research_work"].queryset.values_list(
                "id", flat=True
            )
        )

        self.assertTrue(self.model_admin.has_add_permission(request))
        self.assertEqual(work_ids, {self.teacher_work.id})
        self.assertNotIn("uploaded_by", form_class.base_fields)
        scope_values = {
            str(value)
            for value, _label in form_class.base_fields["visibility_scope"].choices
            if value
        }
        self.assertEqual(scope_values, {"teacher"})
        self.assertNotIn("set_selected_visibility", self.model_admin.get_actions(request))

    def test_teacher_cannot_save_document_for_unadvised_work(self):
        request = self.request_for(self.teacher)
        document = SourceDocument(
            research_work=self.other_work,
            file=SimpleUploadedFile(
                "forbidden.pdf",
                b"%PDF-1.4\nforbidden fixture\n%%EOF",
                content_type="application/pdf",
            ),
        )

        with self.assertRaises(PermissionDenied):
            self.model_admin.save_model(request, document, form=None, change=False)

    def test_teacher_upload_is_forced_to_teacher_scope(self):
        request = self.request_for(self.teacher)
        request.request_id = "teacher-admin-upload"
        document = SourceDocument(
            research_work=self.teacher_work,
            file=SimpleUploadedFile(
                "teacher-forced-scope.pdf",
                b"%PDF-1.4\nteacher scope fixture\n%%EOF",
                content_type="application/pdf",
            ),
            visibility_scope="public",
        )

        self.model_admin.save_model(request, document, form=None, change=False)

        document.refresh_from_db()
        self.assertEqual(document.visibility_scope, "teacher")
        self.assertEqual(document.uploaded_by, self.teacher)

    def test_teacher_cannot_view_admin_scope_document_or_chunk(self):
        admin_document = self._create_document(
            self.teacher_work,
            "admin-scope-document.pdf",
            visibility_scope="admin",
        )
        admin_chunk = DocumentChunk.objects.create(
            source_document=admin_document,
            chunk_index=0,
            text="Admin-only derived evidence.",
            visibility_scope="admin",
        )
        request = self.request_for(self.teacher)
        chunk_admin = DocumentChunkAdmin(DocumentChunk, admin.site)

        self.assertNotIn(admin_document, self.model_admin.get_queryset(request))
        self.assertFalse(
            self.model_admin.has_view_permission(request, admin_document)
        )
        self.assertNotIn(admin_chunk, chunk_admin.get_queryset(request))
        self.assertFalse(chunk_admin.has_view_permission(request, admin_chunk))

    def test_platform_admin_sees_all_and_unassigned_staff_sees_none(self):
        admin_request = self.request_for(self.platform_admin)
        staff_request = self.request_for(self.unassigned_staff)

        self.assertEqual(self.model_admin.get_queryset(admin_request).count(), 2)
        self.assertTrue(
            self.model_admin.has_change_permission(
                admin_request, self.other_document
            )
        )
        self.assertTrue(self.model_admin.has_add_permission(admin_request))
        self.assertFalse(self.model_admin.has_module_permission(staff_request))
        self.assertFalse(self.model_admin.get_queryset(staff_request).exists())

    def test_browser_admin_upload_audit_uses_response_request_id(self):
        self.client.force_login(self.platform_admin)

        response = self.client.post(
            "/admin/documents/sourcedocument/add/",
            {
                "research_work": str(self.other_work.id),
                "file": SimpleUploadedFile(
                    "browser-admin.pdf",
                    b"%PDF-1.4\nbrowser admin fixture\n%%EOF",
                    content_type="application/pdf",
                ),
                "visibility_scope": "admin",
                "uploaded_by": "",
                "_save": "Save",
            },
            HTTP_X_REQUEST_ID="admin-upload-trace-789",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["X-Request-ID"], "admin-upload-trace-789")
        document = SourceDocument.objects.get(
            original_filename="browser-admin.pdf"
        )
        audit = AuditLog.objects.get(
            event_type="source_document.uploaded",
            target_id=document.id,
        )
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertEqual(audit.request_id, response["X-Request-ID"])
        self.assertEqual(audit.metadata["source"], "django_admin")
        self.assertNotIn("filename", audit.metadata)

    def test_existing_source_admin_page_does_not_offer_file_or_parent_replacement(self):
        self.client.force_login(self.platform_admin)

        response = self.client.get(
            f"/admin/documents/sourcedocument/{self.teacher_document.id}/change/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "檔案採私有儲存且不可直接覆蓋")
        self.assertNotContains(response, 'input type="file"')
        self.assertNotContains(response, 'name="research_work"')

    def test_platform_admin_can_change_a_document_visibility_without_replacing_file(self):
        self.client.force_login(self.platform_admin)

        response = self.client.post(
            f"/admin/documents/sourcedocument/{self.teacher_document.id}/change/",
            {"visibility_scope": "admin", "_save": "Save"},
            HTTP_X_REQUEST_ID="document-visibility-trace-123",
        )

        self.assertEqual(response.status_code, 302)
        self.teacher_document.refresh_from_db()
        self.assertEqual(self.teacher_document.visibility_scope, "admin")
        audit = AuditLog.objects.get(
            event_type="source_document.visibility_changed",
            target_id=self.teacher_document.id,
        )
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertEqual(audit.request_id, "document-visibility-trace-123")
        self.assertEqual(audit.metadata["from_visibility_scope"], "teacher")
        self.assertEqual(audit.metadata["to_visibility_scope"], "admin")

    def test_platform_admin_can_bulk_change_document_visibility(self):
        self.client.force_login(self.platform_admin)
        url = "/admin/documents/sourcedocument/"

        confirmation = self.client.post(
            url,
            {
                "action": "set_selected_visibility",
                "_selected_action": [
                    str(self.teacher_document.id),
                    str(self.other_document.id),
                ],
            },
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "批量設定原始文件可見性")

        response = self.client.post(
            url,
            {
                "action": "set_selected_visibility",
                "apply": "確認更新",
                "visibility_scope": "admin",
                "_selected_action": [
                    str(self.teacher_document.id),
                    str(self.other_document.id),
                ],
            },
            HTTP_X_REQUEST_ID="document-bulk-visibility-trace-456",
        )
        self.assertEqual(response.status_code, 302)
        self.teacher_document.refresh_from_db()
        self.other_document.refresh_from_db()
        self.assertEqual(self.teacher_document.visibility_scope, "admin")
        self.assertEqual(self.other_document.visibility_scope, "admin")
        audit = AuditLog.objects.get(
            event_type="source_documents.visibility_changed_bulk",
            request_id="document-bulk-visibility-trace-456",
        )
        self.assertEqual(audit.actor, self.platform_admin)
        self.assertEqual(audit.metadata["document_count"], 2)
        self.assertEqual(audit.metadata["changed_count"], 2)

    def test_admin_audit_failure_rolls_back_row_and_removes_blob(self):
        request = self.request_for(self.platform_admin)
        request.request_id = "admin-failure-trace"
        before_ids = set(SourceDocument.objects.values_list("id", flat=True))
        document = SourceDocument(
            research_work=self.other_work,
            file=SimpleUploadedFile(
                "audit-failure.pdf",
                b"%PDF-1.4\naudit failure fixture\n%%EOF",
                content_type="application/pdf",
            ),
            visibility_scope="admin",
        )

        with patch(
            "documents.admin.record_audit_event",
            side_effect=RuntimeError("audit persistence unavailable"),
        ), self.assertRaises(RuntimeError):
            self.model_admin.save_model(
                request,
                document,
                form=None,
                change=False,
            )

        self.assertEqual(
            set(SourceDocument.objects.values_list("id", flat=True)),
            before_ids,
        )
        self.assertFalse(document.file.storage.exists(document.file.name))
        self.assertFalse(
            AuditLog.objects.filter(request_id="admin-failure-trace").exists()
        )

    def test_teacher_queryset_and_view_are_limited_to_advised_parent_work(self):
        request = self.request_for(self.teacher)
        chunk_admin = DocumentChunkAdmin(DocumentChunk, admin.site)

        self.assertEqual(
            list(chunk_admin.get_queryset(request)),
            [self.teacher_chunk],
        )
        self.assertTrue(
            chunk_admin.has_view_permission(request, self.teacher_chunk)
        )
        self.assertFalse(
            chunk_admin.has_view_permission(request, self.other_chunk)
        )

    def test_teacher_cannot_add_change_or_delete_chunks(self):
        request = self.request_for(self.teacher)
        chunk_admin = DocumentChunkAdmin(DocumentChunk, admin.site)

        self.assertFalse(chunk_admin.has_add_permission(request))
        self.assertFalse(
            chunk_admin.has_change_permission(request, self.teacher_chunk)
        )
        self.assertFalse(
            chunk_admin.has_delete_permission(request, self.teacher_chunk)
        )

    def test_platform_admin_has_full_chunk_access(self):
        request = self.request_for(self.platform_admin)
        chunk_admin = DocumentChunkAdmin(DocumentChunk, admin.site)

        self.assertEqual(chunk_admin.get_queryset(request).count(), 2)
        self.assertTrue(chunk_admin.has_add_permission(request))
        self.assertTrue(
            chunk_admin.has_change_permission(request, self.other_chunk)
        )
        self.assertTrue(
            chunk_admin.has_delete_permission(request, self.other_chunk)
        )

    def test_unassigned_staff_has_no_chunk_access(self):
        request = self.request_for(self.unassigned_staff)
        chunk_admin = DocumentChunkAdmin(DocumentChunk, admin.site)

        self.assertFalse(chunk_admin.has_module_permission(request))
        self.assertFalse(chunk_admin.get_queryset(request).exists())
        self.assertFalse(
            chunk_admin.has_view_permission(request, self.teacher_chunk)
        )
