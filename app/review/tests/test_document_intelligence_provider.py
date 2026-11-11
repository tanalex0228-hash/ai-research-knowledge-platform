from __future__ import annotations

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError

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
from review.models import ReviewAction, ReviewState
from review.services import create_extraction_job_and_candidates, decide_review_item
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
                "publication_year": {"value": 2010, "confidence": 0.99},
                "research_type": {"value": "master_thesis", "confidence": 0.99},
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

    def relation_item(self, kind, *, matched=True):
        prefix = "relations" if matched else "suggestions"
        return ReviewItem.objects.get(
            target_id=self.work.id,
            field_path=f"{prefix}.{kind}.1",
        )

    def admin_user(self):
        from accounts.models import User

        return User.objects.create_superuser(
            username="reviewer", email="reviewer@example.test", password="test-password"
        )

    def test_one_author_existing_entity_match_and_evidence(self):
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(payload=self.payload()),
        )

        candidate = self.document_intelligence_item().candidate_value

        self.assertIsNone(candidate["metadata"]["publication_year"])
        self.assertIsNone(candidate["metadata"]["research_type"])
        self.assertIsNone(candidate["metadata"]["title"])
        self.assertFalse(ReviewItem.objects.filter(field_path="title").exists())

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
        self.assertFalse(ReviewItem.objects.filter(field_path="authors").exists())
        self.assertTrue(ReviewItem.objects.filter(field_path="relations.work_authors.1").exists())

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

    def test_two_character_chinese_names_match_despite_internal_space(self):
        student = Student.objects.create(display_name="王明", status="active")
        professor = Professor.objects.create(display_name="李安")
        self.chunk1.text += "\n作者：王 明\n指導教授：李 安\n"
        self.chunk1.save(update_fields=["text"])
        payload = self.payload(authors=[{"full_name": "王 明", "role": "Student"}])
        payload["advisors"] = [{"full_name": "李 安", "role": "Advisor"}]
        payload["metadata"]["advisors"] = ["李 安"]
        payload["metadata"]["co_advisors"] = []
        create_extraction_job_and_candidates(self.document, provider=FakeProvider(payload=payload))
        actions = self.document_intelligence_item().candidate_value["database_actions"]
        self.assertEqual(actions["students"][0]["student_id"], str(student.pk))
        self.assertEqual(actions["professors"][0]["professor_id"], str(professor.pk))
        self.assertTrue(actions["students"][0]["evidence"])
        self.assertTrue(actions["professors"][0]["evidence"])

    def test_full_abstract_across_pages_requires_approval(self):
        self.chunk2.text = "摘要\n第一段完整內容。"
        self.chunk2.save(update_fields=["text"])
        chunk3 = DocumentChunk.objects.create(
            source_document=self.document, chunk_index=2, page_start=3, page_end=3,
            text="第二段完整內容。\n關鍵字：金融、實證\n第一章 緒論",
            token_count=20, visibility_scope=VisibilityScope.PUBLIC,
        )
        create_extraction_job_and_candidates(self.document, provider=FakeProvider(payload=self.payload()))
        self.work.refresh_from_db()
        self.assertEqual(self.work.abstract, "")
        item = ReviewItem.objects.get(field_path="abstract")
        self.assertEqual(item.candidate_value["value"], "第一段完整內容。\n第二段完整內容。")
        self.assertEqual(
            [part["chunk_id"] for part in item.candidate_value["evidence"]],
            [str(self.chunk2.pk), str(chunk3.pk)],
        )
        decide_review_item(item=item, reviewer=self.admin_user(), action=ReviewAction.APPROVE)
        self.work.refresh_from_db()
        self.assertEqual(self.work.abstract, "第一段完整內容。\n第二段完整內容。")
        self.assertEqual(self.work.title, "Placeholder title")

    def test_abstract_approval_does_not_overwrite_manual_edit(self):
        self.chunk2.text = "摘要\n原文摘要。\n關鍵字：金融"
        self.chunk2.save(update_fields=["text"])
        create_extraction_job_and_candidates(self.document, provider=FakeProvider(payload=self.payload()))
        self.work.abstract = "管理員人工摘要"
        self.work.save(update_fields=["abstract"])
        item = ReviewItem.objects.get(field_path="abstract")
        with self.assertRaises(ValidationError):
            decide_review_item(item=item, reviewer=self.admin_user(), action=ReviewAction.APPROVE)
        item.refresh_from_db()
        self.assertEqual(item.state, ReviewState.PENDING)

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

    def test_approved_existing_matches_link_only_selected_relationships(self):
        create_extraction_job_and_candidates(
            self.document, provider=FakeProvider(payload=self.payload())
        )
        reviewer = self.admin_user()
        student_item = self.relation_item("work_authors")
        field_item = self.relation_item("work_fields")
        method_item = self.relation_item("work_methods")
        advisor_item = self.relation_item("work_advisors")
        for item in (student_item, field_item, method_item, advisor_item):
            decide_review_item(item=item, reviewer=reviewer, action=ReviewAction.APPROVE)

        self.assertEqual(WorkAuthor.objects.get(research_work=self.work).student, self.student)
        self.assertEqual(WorkAdvisor.objects.get(research_work=self.work).professor, self.professor)
        self.assertEqual(WorkField.objects.get(research_work=self.work).research_field, self.field)
        self.assertEqual(WorkMethod.objects.get(research_work=self.work).research_method, self.method)
        self.assertEqual(self.relation_item("work_authors").state, ReviewState.APPROVED)
        self.work.refresh_from_db()
        self.assertEqual(self.work.title, "Placeholder title")
        self.assertEqual(self.work.year, 2026)
        self.assertEqual(self.work.work_type, ResearchWorkType.MASTER_THESIS)

    def test_unmatched_suggestions_never_create_entities(self):
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(
                payload=self.payload(authors=[
                    {"full_name": "譚孝安", "role": "Student"},
                    {"full_name": "林新生", "role": "Student"},
                ])
            ),
        )
        item = ReviewItem.objects.get(field_path="suggestions.work_authors.2")
        decide_review_item(item=item, reviewer=self.admin_user(), action=ReviewAction.APPROVE)
        self.assertFalse(Student.objects.filter(display_name="林新生").exists())
        self.assertFalse(WorkAuthor.objects.filter(research_work=self.work).exists())

    def test_invalid_evidence_rolls_back_relation_approval(self):
        create_extraction_job_and_candidates(
            self.document, provider=FakeProvider(payload=self.payload())
        )
        item = self.relation_item("work_authors")
        from review.models import ReviewDecision

        item.candidate_value["evidence"] = []
        # Immutable candidate fields cannot be changed after creation.
        with self.assertRaises(ValidationError):
            item.save()
        self.assertFalse(ReviewDecision.objects.filter(review_item=item).exists())

    def test_semantic_taxonomy_match_uses_validated_existing_id(self):
        chinese_method = ResearchMethod.objects.create(
            slug="garch-zh", display_name="廣義自迴歸條件異方差模型"
        )
        self.chunk2.text += "\n第三章 研究方法：使用 GARCH 模型估計波動率。"
        self.chunk2.save(update_fields=["text"])
        payload = self.payload()
        payload["research_methods"].append(
            {
                "name": "GARCH",
                "match": True,
                "id": str(chinese_method.pk),
                "canonical_name": chinese_method.display_name,
                "confidence": 0.91,
            }
        )
        create_extraction_job_and_candidates(
            self.document, provider=FakeProvider(payload=payload)
        )
        method_actions = self.document_intelligence_item().candidate_value[
            "database_actions"
        ]["work_methods"]
        garch = next(action for action in method_actions if action["research_method_name"] == "GARCH")
        self.assertTrue(garch["matched"])
        self.assertEqual(garch["research_method_id"], str(chinese_method.pk))
        self.assertEqual(garch["evidence"][0]["page_number"], 2)

    def test_method_only_in_references_has_no_approvable_relation(self):
        self.chunk2.text = "參考文獻\nGARCH (1986) is cited but not used."
        self.chunk2.save(update_fields=["text"])
        method = ResearchMethod.objects.create(
            slug="garch-zh", display_name="廣義自迴歸條件異方差模型"
        )
        payload = self.payload()
        payload["research_methods"] = [{
            "name": "GARCH", "match": True, "id": str(method.pk), "confidence": 0.99
        }]
        create_extraction_job_and_candidates(
            self.document, provider=FakeProvider(payload=payload)
        )
        self.assertFalse(
            ReviewItem.objects.filter(field_path__startswith="relations.work_methods").exists()
        )

    def test_existing_coadvisor_is_linked_with_separate_role(self):
        coadvisor = Professor.objects.create(display_name="王大明")
        create_extraction_job_and_candidates(
            self.document, provider=FakeProvider(payload=self.payload())
        )
        item = ReviewItem.objects.get(field_path="relations.work_advisors.2")
        decide_review_item(item=item, reviewer=self.admin_user(), action=ReviewAction.APPROVE)
        self.assertEqual(
            WorkAdvisor.objects.get(research_work=self.work).professor, coadvisor
        )
        self.assertEqual(
            WorkAdvisor.objects.get(research_work=self.work).role, "co_advisor"
        )

    def test_ambiguous_student_name_is_not_automatically_linked(self):
        Student.objects.create(display_name="譚孝安")
        create_extraction_job_and_candidates(
            self.document, provider=FakeProvider(payload=self.payload())
        )
        item = ReviewItem.objects.get(field_path="suggestions.work_authors.1")
        self.assertFalse(item.candidate_value["matched"])

    def test_existing_student_user_without_research_identity_can_be_approved(self):
        from accounts.models import Role, User, UserRole

        user = User.objects.create_user(
            username="林新生", email="new-student@example.test"
        )
        UserRole.objects.create(user=user, role=Role.objects.get(slug="student"))
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(payload=self.payload(authors=[
                {"full_name": "林新生", "role": "Student", "confidence": 0.9}
            ])),
        )
        item = self.relation_item("work_authors")
        self.assertEqual(item.candidate_value["user_id"], str(user.pk))
        self.assertIsNone(item.candidate_value["student_id"])

        decide_review_item(item=item, reviewer=self.admin_user(), action=ReviewAction.APPROVE)

        author = WorkAuthor.objects.get(research_work=self.work)
        self.assertEqual(author.user, user)
        self.assertEqual(author.student.user, user)

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

    def test_timeout_keeps_multiline_cover_student_candidates(self):
        self.chunk1.text = (
            "輔仁大學金融與國際企業學系\n"
            "指導教授：蔡麗茹\n"
            "組員姓名：譚孝安\n"
            "             林新生\n"
            "             黃   敏\n"
            "\n中華民國一一四年五月\n"
        )
        self.chunk1.save(update_fields=["text"])
        create_extraction_job_and_candidates(
            self.document,
            provider=FakeProvider(error=DocumentIntelligenceTimeout("timeout")),
        )
        candidate = self.document_intelligence_item().candidate_value
        self.assertEqual(
            [author["full_name"] for author in candidate["authors"]],
            ["譚孝安", "林新生", "黃敏"],
        )
        self.assertEqual(len(candidate["database_actions"]["students"]), 3)
        self.assertTrue(candidate["database_actions"]["students"][0]["matched"])
        self.assertTrue(candidate["database_actions"]["students"][0]["evidence"])
        self.assertFalse(WorkAuthor.objects.filter(research_work=self.work).exists())

    @override_settings(
        DOCUMENT_INTELLIGENCE_PROVIDER="openai",
        OPENAI_API_KEY="",
        AI_API_KEY="",
    )
    def test_missing_api_key_uses_deterministic_fallback_provider(self):
        provider = get_document_intelligence_provider()

        self.assertEqual(provider.provider_name, "deterministic")
        self.assertEqual(provider.fallback_reason, "openai_missing_api_key")
