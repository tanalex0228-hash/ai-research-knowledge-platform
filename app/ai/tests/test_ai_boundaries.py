from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from ai.models import AIRequestLog, AIRequestPurpose, AIRequestStatus, PromptVersion
from ai.services import TeacherMatchingService


class TeacherMatchingBoundaryTests(TestCase):
    def test_matching_returns_structured_empty_result_without_catalog_data(self):
        result = TeacherMatchingService().match(
            intent="I want to study AI finance",
            user=None,
        )

        self.assertIn("query_id", result)
        self.assertEqual(result["recommendations"], [])

    def test_matching_rejects_empty_intent(self):
        with self.assertRaises(ValueError):
            TeacherMatchingService().match(intent="", user=None)


class AIProvenanceModelTests(TestCase):
    def test_active_prompt_requires_output_schema(self):
        prompt = PromptVersion(
            key="assistant",
            version=1,
            template="Use only cited evidence.",
            status="active",
        )

        with self.assertRaises(ValidationError):
            prompt.full_clean()

    def test_successful_evidence_bound_request_requires_retrieval(self):
        prompt = PromptVersion.objects.create(
            key="assistant",
            version=1,
            template="Use only cited evidence.",
        )
        request_log = AIRequestLog(
            purpose=AIRequestPurpose.ASSISTANT,
            prompt_version=prompt,
            input_payload={"question": "What is this about?"},
            output_payload={"answer": "Unsupported"},
            status=AIRequestStatus.SUCCEEDED,
            completed_at=timezone.now(),
        )

        with self.assertRaises(ValidationError):
            request_log.full_clean()
