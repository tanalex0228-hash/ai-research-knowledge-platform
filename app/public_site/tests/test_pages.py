from __future__ import annotations

import tempfile
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User, UserRole
from documents.models import SourceDocument
from documents.storage import private_document_storage
from professors.models import Professor
from research.models import (
    Award,
    FeaturedWork,
    ResearchWork,
    WorkAdvisor,
    WorkField,
    WorkMethod,
)
from taxonomy.models import ResearchField, ResearchMethod


class PrivateStorageTestMixin:
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


class PublicPageTests(PrivateStorageTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.professor = Professor.objects.create(
            display_name="林教授",
            title="教授",
            email="private@example.edu",
            email_is_public=False,
            profile_summary="人工智慧與金融研究",
            visibility_scope="public",
        )
        cls.private_professor = Professor.objects.create(
            display_name="內部教師",
            visibility_scope="teacher",
        )
        cls.field = ResearchField.objects.create(
            slug="ai-finance",
            display_name="AI 金融",
            aliases=["FinTech"],
            visibility_scope="public",
        )
        cls.method = ResearchMethod.objects.create(
            slug="lstm",
            display_name="LSTM",
            aliases=["Long Short-Term Memory"],
            visibility_scope="public",
        )
        cls.work = ResearchWork.objects.create(
            work_type="undergraduate_project",
            title="LSTM 在投資組合的應用",
            abstract="使用機器學習預測市場變化。",
            year=2026,
            status="published",
            visibility_scope="public",
        )
        cls.student_work = ResearchWork.objects.create(
            work_type="master_thesis",
            title="校內可見研究",
            abstract="僅供登入學生探索。",
            year=2025,
            status="published",
            visibility_scope="student",
        )
        cls.draft_work = ResearchWork.objects.create(
            work_type="research_project",
            title="尚未發布研究",
            abstract="草稿不得公開。",
            year=2026,
            status="draft",
            visibility_scope="public",
        )
        WorkAdvisor.objects.create(research_work=cls.work, professor=cls.professor)
        WorkAdvisor.objects.create(
            research_work=cls.work,
            professor=cls.private_professor,
            position=2,
        )
        WorkField.objects.create(
            research_work=cls.work,
            research_field=cls.field,
            relevance="core",
            is_primary=True,
        )
        WorkMethod.objects.create(research_work=cls.work, research_method=cls.method)
        FeaturedWork.objects.create(research_work=cls.work, headline="本月精選")
        Award.objects.create(
            research_work=cls.work,
            advisor=cls.professor,
            name="最佳專題",
            award_type="best_project",
            award_year=2026,
            visibility_scope="public",
        )
        cls.student_role = Role.objects.get(slug="student")
        cls.student = User.objects.create_user(
            username="student", email="student@example.edu", password="test-pass-123"
        )
        UserRole.objects.create(user=cls.student, role=cls.student_role)

    def test_home_is_research_entry_and_hides_non_public_records(self):
        response = self.client.get(reverse("public_site:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "LSTM 在投資組合的應用")
        self.assertContains(response, "最佳專題")
        self.assertContains(response, "AI 金融")
        self.assertNotContains(response, "校內可見研究")
        self.assertNotContains(response, "尚未發布研究")
        self.assertNotContains(response, "內部教師")

    def test_professor_page_has_related_work_chart_and_private_contact_policy(self):
        response = self.client.get(
            reverse("public_site:professor-detail", args=[self.professor.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.work.title)
        self.assertEqual(response.context["distribution"]["labels"], ["AI 金融"])
        self.assertNotContains(response, "private@example.edu")
        self.assertContains(response, "field-distribution-data")

    def test_work_page_hides_non_public_advisor(self):
        response = self.client.get(
            reverse("public_site:work-detail", args=[self.work.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.professor.display_name)
        self.assertNotContains(response, self.private_professor.display_name)
        self.assertContains(response, self.field.display_name)
        self.assertContains(response, self.method.display_name)

    def test_keyword_search_expands_taxonomy_aliases(self):
        response = self.client.get(
            reverse("public_site:search"), {"q": "Long Short-Term Memory"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.work.title)
        self.assertNotContains(response, self.student_work.title)

    def test_visitor_cannot_infer_non_public_work(self):
        restricted = self.client.get(
            reverse("public_site:work-detail", args=[self.student_work.id])
        )
        missing = self.client.get(
            reverse("public_site:work-detail", args=[uuid.uuid4()])
        )

        self.assertEqual(restricted.status_code, 404)
        self.assertEqual(missing.status_code, 404)

    def test_student_role_can_view_student_scope_work(self):
        self.client.force_login(self.student)
        response = self.client.get(
            reverse("public_site:work-detail", args=[self.student_work.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.student_work.title)

    def test_download_is_permission_checked_and_filename_is_opaque(self):
        public_document = SourceDocument.objects.create(
            research_work=self.work,
            file=SimpleUploadedFile(
                "student-private-name.pdf",
                b"%PDF-1.4\n%%EOF",
                content_type="application/pdf",
            ),
            visibility_scope="public",
        )
        restricted_document = SourceDocument.objects.create(
            research_work=self.student_work,
            file=SimpleUploadedFile(
                "restricted.pdf",
                b"%PDF-1.4\n%%EOF",
                content_type="application/pdf",
            ),
            visibility_scope="student",
        )

        public_response = self.client.get(
            reverse("public_site:document-download", args=[public_document.id])
        )
        self.assertEqual(public_response.status_code, 200)
        self.assertNotIn(
            "student-private-name", public_response["Content-Disposition"]
        )
        self.assertIn(str(self.work.id), public_response["Content-Disposition"])
        self.assertTrue(b"".join(public_response.streaming_content).startswith(b"%PDF-"))

        denied = self.client.get(
            reverse("public_site:document-download", args=[restricted_document.id])
        )
        self.assertEqual(denied.status_code, 404)

        self.client.force_login(self.student)
        allowed = self.client.get(
            reverse("public_site:document-download", args=[restricted_document.id])
        )
        self.assertEqual(allowed.status_code, 200)
        b"".join(allowed.streaming_content)
