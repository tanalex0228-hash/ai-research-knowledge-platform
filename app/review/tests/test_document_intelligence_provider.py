from __future__ import annotations

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.permissions import VisibilityScope
from ai.models import AIRequestLog, AIRequestStatus
from documents.models import DocumentChunk, SourceDocument
from professors.models import Professor, ProfessorAlias
from research.models import (
    ResearchWork,
    ResearchWorkType,
    WorkAdvisor,
    WorkAuthor,
    WorkField,
    WorkMethod,
    Student,
)
from review.document_intelligence_providers import (
    DocumentIntelligenceMalformedOutput,
    DocumentIntelligenceTimeout,
    get_document_intelligence_provider,
)
from review.models import ReviewItem
from review.services import create_extraction_job_and_candidates
from taxonomy.models import ResearchField, ResearchMethod


class FakeProvider:
    provider_name = "fake-llm"
    model_name = "fake-document-model"
    is_configured = True

    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error

    def extract(self, **kwargs):
        if self.error:
            raise self.error
        return self.payload


class DocumentIntelligenceProviderTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.work = ResearchWork.objects.create(
            work_type=ResearchWorkType.MASTER_THESIS,
            title="Placeholder title",
            year=2026,
            status="uploaded",
            visibility_scope=VisibilityScope.PUBLIC,
        )
        self.document = SourceDocument.objects.create(
            research_work=self.work,
            file=SimpleUploadedFile(
                "paper.pdf",
                b"%PDF-1.4\nDocument intelligence provider fixture\n%%EOF",
                content_type="application/pdf",
            ),
            visibility_scope=VisibilityScope.PUBLIC,
        )
        self.chunk1 = DocumentChunk.objects.create(
            source_document=self.document,
            chunk_index=0,
            page_start=1,
            page_end=1,
            text=(
                "題名：AI金融投資策略研究\n"
                "作者：譚孝安、林新生\n"
                "指導教授：蔡麗茹\n"
                "共同指導教授：王大明\n"
                "研究領域：總體金融、新興金融科技\n"
            ),
            token_count=30,
            visibility_scope=VisibilityScope.PUBLIC,
        )
        self.chunk2 = DocumentChunk.objects.create(
            source_document=self.document,
            chunk_index=1,
            page_start=2,
            page_end=2,
            text=(
                "摘要：本研究使用 VAR 與事件研究法分析 AI 金融投資策略。\n"
                "參考文獻：Fama and French (1993) 為被引用學者，並非本論文作者。"
            ),
            token_count=25,
            visibility_scope=VisibilityScope.PUBLIC,
        )
        self.student = Student.objects.create(
            display_name="譚孝安",
            status="active",
            visibility_scope=VisibilityScope.PUBLIC,
        )
        self.professor = Professor.objects.create(display_name="蔡麗茹")
        ProfessorAlias.objects.create(professor=self.professor, alias="Tsai Li-Ju")
        self.field = ResearchField.objects.create(
            slug="macro-finance",
            display_name="總體金融",
            aliases=["Macro Finance"],
        )
        self.method = ResearchMethod.objects.create(
            slug="var",
            display_name="VAR",
            aliases=["Vector Autoregression"],
        )

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    def payload(self, *, authors=None):
        return {
            "metadata": {
                "title": {"value": "AI金融投資策略研究", "confidence": 0.94},
                "publication_year": None,
                "student_authors": authors or ["譚孝安"],
                "advisors": ["蔡麗茹"],
                "co_advisors": ["王大明"],
                "research_topics": ["AI金融投資策略"],
            },
            "authors": authors
            if authors is not None
            else [{"full_name": "譚孝安", "role": "Student", "confidence": 0.96}],
            "advisors": [
                {"full_name": "蔡麗茹", "role": "Advisor", "confidence": 0.97},
                {"full_name": "王大明", "role": "Co-advisor", "confidence": 0.82},
            ],
            "research_fields": [
                {"name": "總體金融", "confidence": 0.91},
                {"name": "新興金融科技", "confidence": 0.74},
            ],
            "research_methods": [
                {"name": "VAR", "confidence": 0.93},
                {"name": "事件研究法", "confidence": 0.77},
            ],
            "keywords": ["AI金融", "投資策略", "VAR", "事件研究法"],
            "variables": [],
            "organizations": [],
            "companies": [],
            "technologies": [],
            "knowledge_graph": [],
            "academic_summary": {
                "summary": "本研究探討 AI 金融投資策略。",
                "keywords": ["AI金融", "VAR"],
                "background": "AI 金融投資已成為重要研究議題。",
                "methodology": "研究使用 VAR 與事件研究法。",
                "findings": "研究發現需經人工審核確認。",
                "limitations": "資料範圍仍有限。",
                "conclusion": "本研究提供投資策略分析參考。",
            },
            "confidence": {},
            "database_actions": {},
        }

    def document_intelligence_item(self):
        return ReviewItem.objects.get(
            target_id=self.work.id,
            field_path="document_intelligence",
        )

    def test_one_author_existing_entity_match_and_evidence(self):
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(payload=self.payload()),
        )

        candidate = self.document_intelligence_item().candidate_value

        self.assertEqual(len(candidate["authors"]), 1)
        self.assertEqual(candidate["authors"][0]["full_name"], "譚孝安")
        self.assertTrue(candidate["database_actions"]["students"][0]["matched"])
        self.assertEqual(
            candidate["database_actions"]["students"][0]["student_id"],
            str(self.student.pk),
        )
        evidence = candidate["authors"][0]["evidence"][0]
        self.assertEqual(evidence["page_number"], 1)
        self.assertEqual(evidence["chunk_id"], str(self.chunk1.pk))
        self.assertIn("譚孝安", evidence["source_excerpt"])

    def test_multiple_authors_and_referenced_scholars_are_not_authors(self):
        authors = [
            {"full_name": "譚孝安", "role": "Student", "confidence": 0.96},
            {"full_name": "林新生", "role": "Student", "confidence": 0.84},
            {"full_name": "Fama", "role": "Researcher", "confidence": 0.99},
        ]

        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(payload=self.payload(authors=authors)),
        )

        candidate = self.document_intelligence_item().candidate_value
        names = [item["full_name"] for item in candidate["authors"]]
        self.assertEqual(names, ["譚孝安", "林新生"])
        self.assertNotIn("Fama", names)
        new_student = candidate["database_actions"]["students"][1]
        self.assertFalse(new_student["matched"])
        self.assertTrue(new_student["proposed_new_entity"])
        self.assertEqual(new_student["action"], "propose_new_student")

    def test_advisor_and_co_advisor_roles_are_separate(self):
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(payload=self.payload()),
        )

        actions = self.document_intelligence_item().candidate_value["database_actions"][
            "work_advisors"
        ]

        self.assertEqual(actions[0]["role"], "primary")
        self.assertEqual(actions[0]["professor_id"], str(self.professor.pk))
        self.assertEqual(actions[1]["role"], "co_advisor")
        self.assertFalse(actions[1]["matched"])
        self.assertEqual(actions[1]["action"], "create_work_advisor")

    def test_topic_field_and_method_extraction_with_existing_and_new_entities(self):
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(payload=self.payload()),
        )

        candidate = self.document_intelligence_item().candidate_value
        fields = candidate["database_actions"]["research_fields"]
        methods = candidate["database_actions"]["research_methods"]

        self.assertEqual(candidate["research_topics"][0]["name"], "AI金融投資策略")
        self.assertTrue(fields[0]["matched"])
        self.assertEqual(fields[0]["research_field_id"], str(self.field.pk))
        self.assertFalse(fields[1]["matched"])
        self.assertEqual(fields[1]["action"], "propose_new_research_field")
        self.assertTrue(methods[0]["matched"])
        self.assertEqual(methods[0]["research_method_id"], str(self.method.pk))
        self.assertFalse(methods[1]["matched"])
        self.assertEqual(methods[1]["action"], "propose_new_research_method")

    def test_review_item_is_not_automatically_promoted(self):
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(payload=self.payload()),
        )

        self.assertFalse(Student.objects.filter(display_name="林新生").exists())
        self.assertFalse(Professor.objects.filter(display_name="王大明").exists())
        self.assertFalse(ResearchField.objects.filter(display_name="新興金融科技").exists())
        self.assertFalse(ResearchMethod.objects.filter(display_name="事件研究法").exists())
        self.assertFalse(WorkAuthor.objects.filter(research_work=self.work).exists())
        self.assertFalse(WorkAdvisor.objects.filter(research_work=self.work).exists())
        self.assertFalse(WorkField.objects.filter(research_work=self.work).exists())
        self.assertFalse(WorkMethod.objects.filter(research_work=self.work).exists())

    def test_malformed_provider_json_falls_back_and_records_failed_request(self):
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(
                error=DocumentIntelligenceMalformedOutput("not a JSON object")
            ),
        )

        candidate = self.document_intelligence_item().candidate_value
        self.assertEqual(
            candidate["provider_fallback_reason"],
            "DocumentIntelligenceMalformedOutput",
        )
        self.assertTrue(
            AIRequestLog.objects.filter(
                provider="fake-llm",
                status=AIRequestStatus.FAILED,
                error_code="DocumentIntelligenceMalformedOutput",
            ).exists()
        )

    def test_provider_timeout_falls_back_and_records_failed_request(self):
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(error=DocumentIntelligenceTimeout("timeout")),
        )

        candidate = self.document_intelligence_item().candidate_value
        self.assertEqual(candidate["provider_fallback_reason"], "DocumentIntelligenceTimeout")
        self.assertTrue(
            AIRequestLog.objects.filter(
                provider="fake-llm",
                status=AIRequestStatus.FAILED,
                error_code="DocumentIntelligenceTimeout",
            ).exists()
        )

    @override_settings(
        DOCUMENT_INTELLIGENCE_PROVIDER="openai",
        OPENAI_API_KEY="",
        AI_API_KEY="",
    )
    def test_missing_api_key_uses_deterministic_fallback_provider(self):
        provider = get_document_intelligence_provider()

        self.assertEqual(provider.provider_name, "deterministic")
        self.assertEqual(provider.fallback_reason, "openai_missing_api_key")
