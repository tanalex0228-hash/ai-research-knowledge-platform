from __future__ import annotations

from typing import Any

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User, UserRole
from ai.models import AssistantMessage, AssistantSession, AssistantSessionStatus
from ai.services.research_navigator import ResearchNavigatorService
from professors.models import Professor
from research.models import ResearchWork, WorkAdvisor, WorkField, WorkMethod
from taxonomy.models import ResearchField, ResearchMethod


class RecordingProvider:
    provider_name = "test"
    model_name = "test-model"

    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    def generate(self, *, prompt_payload: dict[str, Any]) -> str:
        self.payloads.append(prompt_payload)
        return "測試回覆 [work:test]"


@override_settings(AI_PROVIDER="disabled", OPENAI_API_KEY="", AI_API_KEY="")
class AssistantApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.field = ResearchField.objects.create(
            slug="assistant-ai-finance",
            display_name="AI 金融",
            aliases=["FinTech"],
            visibility_scope="public",
        )
        cls.method = ResearchMethod.objects.create(
            slug="assistant-machine-learning",
            display_name="機器學習",
            aliases=["machine learning"],
            visibility_scope="public",
        )
        cls.professor = Professor.objects.create(
            display_name="林示範",
            title="教授",
            profile_summary="研究 AI 金融、投資科技與金融預測。",
            visibility_scope="public",
        )
        cls.work = ResearchWork.objects.create(
            work_type="undergraduate_project",
            title="AI 金融投資預測研究",
            abstract="人工智慧與機器學習應用於金融市場預測。",
            year=2026,
            status="published",
            visibility_scope="public",
        )
        cls.restricted_work = ResearchWork.objects.create(
            work_type="master_thesis",
            title="限制研究",
            abstract="限制內容不應出現在訪客 AI context。",
            year=2026,
            status="published",
            visibility_scope="student",
        )
        WorkAdvisor.objects.create(research_work=cls.work, professor=cls.professor)
        WorkField.objects.create(
            research_work=cls.work,
            research_field=cls.field,
            relevance="core",
            is_primary=True,
        )
        WorkMethod.objects.create(research_work=cls.work, research_method=cls.method)
        cls.student = User.objects.create_user(
            username="assistant-student",
            email="assistant-student@example.edu",
            password="test-pass-123",
        )
        UserRole.objects.create(user=cls.student, role=Role.objects.get(slug="student"))
        cls.other_student = User.objects.create_user(
            username="assistant-other",
            email="assistant-other@example.edu",
            password="test-pass-123",
        )
        UserRole.objects.create(
            user=cls.other_student,
            role=Role.objects.get(slug="student"),
        )

    def test_session_starts_empty_and_message_is_saved(self):
        initial = self.client.get(reverse("api-v1:assistant-session"))
        self.assertEqual(initial.status_code, 200)
        self.assertIsNone(initial.json()["data"]["session"])

        response = self.client.post(
            reverse("api-v1:assistant-message"),
            data={
                "message": "我想研究 AI 金融",
                "page_context": {"type": "home", "url": "/"},
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()["data"]
        self.assertEqual(payload["session"]["status"], "active")
        self.assertIn("AI 金融", payload["assistant_message"]["content"])
        self.assertEqual(AssistantSession.objects.count(), 1)
        self.assertEqual(AssistantMessage.objects.count(), 2)

        reload_response = self.client.get(reverse("api-v1:assistant-session"))
        self.assertEqual(len(reload_response.json()["data"]["messages"]), 2)

    def test_close_is_frontend_only_and_end_marks_session_ended(self):
        self.client.post(
            reverse("api-v1:assistant-message"),
            data={"message": "AI 金融", "page_context": {"type": "home"}},
            content_type="application/json",
        )
        session = AssistantSession.objects.get()
        self.assertEqual(session.status, AssistantSessionStatus.ACTIVE)

        response = self.client.post(
            reverse("api-v1:assistant-end"),
            data={},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, AssistantSessionStatus.ENDED)
        self.assertIsNotNone(session.ended_at)
        after_end = self.client.get(reverse("api-v1:assistant-session"))
        self.assertIsNone(after_end.json()["data"]["session"])

    def test_authenticated_chat_sessions_are_isolated_by_user(self):
        self.client.force_login(self.student)
        response = self.client.post(
            reverse("api-v1:assistant-message"),
            data={"message": "AI 金融", "page_context": {"type": "home"}},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

        other_client = Client()
        other_client.force_login(self.other_student)
        other_response = other_client.get(reverse("api-v1:assistant-session"))
        self.assertEqual(other_response.status_code, 200)
        self.assertIsNone(other_response.json()["data"]["session"])
        self.assertEqual(other_response.json()["data"]["messages"], [])

    def test_anonymous_chat_sessions_are_isolated_by_browser_session(self):
        first = Client()
        second = Client()
        first.post(
            reverse("api-v1:assistant-message"),
            data={"message": "AI 金融", "page_context": {"type": "home"}},
            content_type="application/json",
        )

        first_session = first.get(reverse("api-v1:assistant-session")).json()["data"]
        second_session = second.get(reverse("api-v1:assistant-session")).json()["data"]

        self.assertEqual(len(first_session["messages"]), 2)
        self.assertIsNone(second_session["session"])
        self.assertEqual(second_session["messages"], [])

    def test_restricted_page_context_is_not_exposed_to_visitor(self):
        response = self.client.post(
            reverse("api-v1:assistant-message"),
            data={
                "message": "解釋這篇研究",
                "page_context": {
                    "type": "research_work",
                    "id": str(self.restricted_work.id),
                    "url": f"/research-works/{self.restricted_work.id}/",
                },
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        server_context = response.json()["data"]["page_context"]
        self.assertNotIn("research_work", server_context)
        self.assertNotIn("限制研究", response.json()["data"]["assistant_message"]["content"])


@override_settings(AI_PROVIDER="disabled", OPENAI_API_KEY="", AI_API_KEY="")
class ResearchNavigatorServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.field = ResearchField.objects.create(
            slug="service-ai-finance",
            display_name="AI 金融",
            aliases=["FinTech"],
            visibility_scope="public",
        )
        cls.method = ResearchMethod.objects.create(
            slug="service-lstm",
            display_name="LSTM",
            aliases=["Long Short-Term Memory"],
            visibility_scope="public",
        )
        cls.professor = Professor.objects.create(
            display_name="王導航",
            profile_summary="專長包含 AI 金融、LSTM 與投資預測。",
            visibility_scope="public",
        )
        cls.work = ResearchWork.objects.create(
            work_type="master_thesis",
            title="LSTM 應用於 AI 金融投資預測",
            abstract="本研究以 LSTM 分析金融市場時間序列。",
            year=2025,
            status="published",
            visibility_scope="public",
        )
        WorkAdvisor.objects.create(research_work=cls.work, professor=cls.professor)
        WorkField.objects.create(
            research_work=cls.work,
            research_field=cls.field,
            relevance="core",
            is_primary=True,
        )
        WorkMethod.objects.create(research_work=cls.work, research_method=cls.method)

    def test_current_page_context_injection_and_prompt_construction(self):
        provider = RecordingProvider()
        service = ResearchNavigatorService(provider=provider)

        result = service.answer(
            user=None,
            question="請解釋這篇研究",
            page_context={"type": "research_work", "id": str(self.work.id)},
            conversation_history=[],
        )

        self.assertEqual(result["page_context"]["research_work"]["title"], self.work.title)
        payload = provider.payloads[0]
        self.assertEqual(payload["current_page"]["research_work"]["title"], self.work.title)
        self.assertEqual(payload["current_user_question"], "請解釋這篇研究")

    def test_recommends_professor_work_field_and_method_from_visible_records(self):
        service = ResearchNavigatorService()

        result = service.answer(
            user=None,
            question="我想研究 AI 金融和 LSTM",
            page_context={"type": "home"},
            conversation_history=[],
        )

        recommendations = result["recommendations"]
        self.assertEqual(recommendations["fields"][0]["name"], "AI 金融")
        self.assertEqual(recommendations["methods"][0]["name"], "LSTM")
        self.assertEqual(recommendations["works"][0]["title"], self.work.title)
        self.assertEqual(recommendations["professors"][0]["name"], self.professor.display_name)

    def test_fallback_when_no_recommendation_exists(self):
        service = ResearchNavigatorService()

        result = service.answer(
            user=None,
            question="火星地質採樣",
            page_context={"type": "home"},
            conversation_history=[],
        )

        self.assertIn("目前平台資料不足", result["answer"])
        self.assertFalse(any(result["recommendations"].values()))
