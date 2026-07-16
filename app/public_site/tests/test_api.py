from __future__ import annotations

import tempfile
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import AuditLog, Role, User, UserRole
from documents.models import SourceDocument
from documents.storage import private_document_storage
from professors.models import Professor
from research.models import ResearchWork, WorkAdvisor


class ApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._storage_directory = tempfile.TemporaryDirectory()
        cls._storage_override = override_settings(
            PRIVATE_DOCUMENT_ROOT=cls._storage_directory.name
        )
        cls._storage_override.enable()
        private_document_storage.__dict__.pop("base_location", None)
        private_document_storage.__dict__.pop("location", None)

    @classmethod
    def tearDownClass(cls):
        private_document_storage.__dict__.pop("base_location", None)
        private_document_storage.__dict__.pop("location", None)
        cls._storage_override.disable()
        cls._storage_directory.cleanup()
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        cls.professor = Professor.objects.create(
            display_name="公開教師",
            email="hidden@example.edu",
            email_is_public=False,
            visibility_scope="public",
        )
        cls.work = ResearchWork.objects.create(
            work_type="journal_article",
            title="公開研究 API",
            abstract="公開摘要",
            year=2026,
            status="published",
            visibility_scope="public",
        )
        cls.restricted_work = ResearchWork.objects.create(
            work_type="master_thesis",
            title="限制研究 API",
            abstract="不應公開",
            year=2026,
            status="published",
            visibility_scope="teacher",
        )
        WorkAdvisor.objects.create(research_work=cls.work, professor=cls.professor)
        cls.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.edu",
            password="admin-pass-123",
        )
        cls.regular_user = User.objects.create_user(
            username="regular",
            email="regular@example.edu",
            password="regular-pass-123",
        )
        cls.teacher = User.objects.create_user(
            username="api-teacher",
            email="api-teacher@example.edu",
            password="teacher-pass-123",
        )
        UserRole.objects.create(
            user=cls.teacher,
            role=Role.objects.get(slug="teacher"),
        )
        cls.professor.user = cls.teacher
        cls.professor.save(update_fields={"user"})

    def test_public_list_and_detail_do_not_leak_restricted_work_or_email(self):
        listing = self.client.get(reverse("api-v1:research-work-list"))
        professor = self.client.get(
            reverse("api-v1:professor-detail", args=[self.professor.id])
        )
        restricted = self.client.get(
            reverse("api-v1:research-work-detail", args=[self.restricted_work.id])
        )

        self.assertEqual(listing.status_code, 200)
        titles = {item["title"] for item in listing.json()["results"]}
        self.assertIn(self.work.title, titles)
        self.assertNotIn(self.restricted_work.title, titles)
        self.assertEqual(professor.status_code, 200)
        self.assertNotIn("email", professor.json()["data"])
        self.assertEqual(restricted.status_code, 404)

    def test_document_upload_is_admin_only(self):
        url = reverse("api-v1:research-work-document-upload", args=[self.work.id])
        self.client.force_login(self.regular_user)
        denied = self.client.post(
            url,
            {
                "file": SimpleUploadedFile(
                    "paper.pdf", b"%PDF-1.4\n%%EOF", content_type="application/pdf"
                )
            },
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(SourceDocument.objects.count(), 0)

        self.client.force_login(self.admin)
        request_id = "upload-trace-123"
        allowed = self.client.post(
            url,
            {
                "file": SimpleUploadedFile(
                    "private-student-name.pdf",
                    b"%PDF-1.4\n%%EOF",
                    content_type="application/pdf",
                ),
                "visibility_scope": "admin",
            },
            HTTP_X_REQUEST_ID=request_id,
        )
        self.assertEqual(allowed.status_code, 201)
        self.assertNotIn("original_filename", allowed.json()["data"])
        self.assertEqual(SourceDocument.objects.count(), 1)
        audit = AuditLog.objects.get(event_type="source_document.uploaded")
        self.assertEqual(audit.actor, self.admin)
        self.assertNotIn("filename", audit.metadata)
        self.assertEqual(audit.request_id, request_id)
        self.assertEqual(allowed.json()["request_id"], request_id)
        self.assertEqual(allowed["X-Request-ID"], request_id)

    def test_document_and_audit_are_database_atomic(self):
        self.client.force_login(self.admin)
        url = reverse("api-v1:research-work-document-upload", args=[self.work.id])

        with patch(
            "public_site.api_views.record_audit_event",
            side_effect=RuntimeError("audit persistence unavailable"),
        ), self.assertRaises(RuntimeError):
            self.client.post(
                url,
                {
                    "file": SimpleUploadedFile(
                        "atomic.pdf",
                        b"%PDF-1.4\natomic fixture\n%%EOF",
                        content_type="application/pdf",
                    )
                },
            )

        self.assertEqual(SourceDocument.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_document_upload_rejects_non_pdf_content(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("api-v1:research-work-document-upload", args=[self.work.id]),
            {
                "file": SimpleUploadedFile(
                    "pretend.pdf", b"not a pdf", content_type="application/pdf"
                )
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error_code"], "INVALID_DOCUMENT")

    def test_teacher_upload_is_limited_to_advised_work_without_existence_leak(self):
        self.client.force_login(self.teacher)
        allowed = self.client.post(
            reverse("api-v1:research-work-document-upload", args=[self.work.id]),
            {
                "file": SimpleUploadedFile(
                    "teacher-owned.pdf",
                    b"%PDF-1.4\nteacher fixture\n%%EOF",
                    content_type="application/pdf",
                ),
                "visibility_scope": "teacher",
            },
        )
        denied = self.client.post(
            reverse(
                "api-v1:research-work-document-upload",
                args=[self.restricted_work.id],
            ),
            {
                "file": SimpleUploadedFile(
                    "unrelated.pdf",
                    b"%PDF-1.4\nunrelated fixture\n%%EOF",
                    content_type="application/pdf",
                )
            },
        )

        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(allowed.json()["data"]["visibility_scope"], "teacher")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json()["error_code"], "NOT_FOUND")
        self.assertEqual(SourceDocument.objects.count(), 1)
        audit = AuditLog.objects.get(event_type="source_document.uploaded")
        self.assertEqual(audit.actor, self.teacher)
        self.assertEqual(audit.request_id, allowed.json()["request_id"])
        self.assertEqual(audit.request_id, allowed["X-Request-ID"])
        uuid.UUID(audit.request_id)

    def test_teacher_upload_defaults_to_teacher_scope_and_cannot_publish_pdf(self):
        self.client.force_login(self.teacher)
        url = reverse("api-v1:research-work-document-upload", args=[self.work.id])

        denied = self.client.post(
            url,
            {
                "file": SimpleUploadedFile(
                    "teacher-public.pdf",
                    b"%PDF-1.4\npublic bypass fixture\n%%EOF",
                    content_type="application/pdf",
                ),
                "visibility_scope": "public",
            },
        )
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(denied.json()["error_code"], "INVALID_VISIBILITY_SCOPE")
        self.assertFalse(SourceDocument.objects.exists())

        allowed = self.client.post(
            url,
            {
                "file": SimpleUploadedFile(
                    "teacher-default.pdf",
                    b"%PDF-1.4\nteacher default fixture\n%%EOF",
                    content_type="application/pdf",
                )
            },
        )
        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(allowed.json()["data"]["visibility_scope"], "teacher")

    def test_inactive_professor_link_cannot_authorize_teacher_upload(self):
        self.professor.status = "inactive"
        self.professor.save(update_fields={"status"})
        self.client.force_login(self.teacher)

        response = self.client.post(
            reverse("api-v1:research-work-document-upload", args=[self.work.id]),
            {
                "file": SimpleUploadedFile(
                    "inactive-owner.pdf",
                    b"%PDF-1.4\ninactive owner fixture\n%%EOF",
                    content_type="application/pdf",
                )
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error_code"], "NOT_FOUND")
        self.assertFalse(SourceDocument.objects.exists())

    def test_deferred_ai_endpoints_are_explicit_stubs(self):
        semantic = self.client.post(reverse("api-v1:semantic-search"), data={})
        matching = self.client.post(reverse("api-v1:teacher-matching"), data={})

        self.assertEqual(semantic.status_code, 501)
        self.assertEqual(
            semantic.json()["error_code"], "SEMANTIC_SEARCH_NOT_IMPLEMENTED"
        )
        self.assertEqual(matching.status_code, 501)
        self.assertEqual(
            matching.json()["error_code"], "TEACHER_MATCHING_NOT_IMPLEMENTED"
        )

        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            csrf_client.post(reverse("api-v1:semantic-search"), data={}).status_code,
            501,
        )
        self.assertEqual(
            csrf_client.post(reverse("api-v1:teacher-matching"), data={}).status_code,
            501,
        )

    def test_unknown_resources_have_generic_not_found_response(self):
        response = self.client.get(
            reverse("api-v1:research-work-detail", args=[uuid.uuid4()])
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error_code"], "NOT_FOUND")
        self.assertEqual(response.json()["request_id"], response["X-Request-ID"])

    def test_api_csrf_failure_uses_json_contract_and_request_id(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        response = csrf_client.post(
            reverse("api-v1:research-work-document-upload", args=[self.work.id]),
            {
                "file": SimpleUploadedFile(
                    "csrf.pdf",
                    b"%PDF-1.4\ncsrf fixture\n%%EOF",
                    content_type="application/pdf",
                )
            },
            HTTP_X_REQUEST_ID="csrf-trace-456",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error_code"], "CSRF_FAILED")
        self.assertEqual(response.json()["request_id"], "csrf-trace-456")
        self.assertEqual(response["X-Request-ID"], "csrf-trace-456")
