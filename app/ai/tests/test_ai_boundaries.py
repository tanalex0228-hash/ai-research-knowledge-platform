from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ai.models import AIRequestLog, AIRequestPurpose, AIRequestStatus, PromptVersion
from ai.services import TeacherMatchingNotImplemented, TeacherMatchingService


class TeacherMatchingBoundaryTests(SimpleTestCase):
    def test_matching_is_explicitly_not_implemented(self):
        with self.assertRaises(TeacherMatchingNotImplemented):
            TeacherMatchingService().match(
                intent="I want to study AI finance",
                user=None,
            )


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

