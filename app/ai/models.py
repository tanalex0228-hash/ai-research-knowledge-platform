from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class PromptStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    RETIRED = "retired", "Retired"


class AIRequestPurpose(models.TextChoices):
    EXTRACTION = "extraction", "Metadata extraction"
    ASSISTANT = "assistant", "Research assistant"
    RESEARCH_NAVIGATION = "research_navigation", "Research navigation"
    TEACHER_MATCHING = "teacher_matching", "Teacher matching"
    CHART_EXPLANATION = "chart_explanation", "Chart explanation"


class AIRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    BLOCKED = "blocked", "Blocked by policy"
    NOT_IMPLEMENTED = "not_implemented", "Not implemented"


TERMINAL_AI_STATUSES = {
    AIRequestStatus.SUCCEEDED,
    AIRequestStatus.FAILED,
    AIRequestStatus.BLOCKED,
    AIRequestStatus.NOT_IMPLEMENTED,
}


class PromptVersion(models.Model):
    """Immutable-in-use, versioned prompt template and output contract."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.SlugField(max_length=120)
    version = models.PositiveIntegerField()
    template = models.TextField()
    input_schema = models.JSONField(default=dict, blank=True)
    output_schema = models.JSONField(default=dict, blank=True)
    schema_version = models.CharField(max_length=40, default="1")
    status = models.CharField(
        max_length=20,
        choices=PromptStatus.choices,
        default=PromptStatus.DRAFT,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_prompt_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("key", "version"),
                name="ai_prompt_key_version_unique",
            ),
            models.CheckConstraint(
                condition=Q(version__gt=0),
                name="ai_prompt_version_positive",
            ),
            models.CheckConstraint(
                condition=~Q(template=""),
                name="ai_prompt_template_not_empty",
            ),
            models.CheckConstraint(
                condition=~Q(schema_version=""),
                name="ai_prompt_schema_version_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key}@{self.version}"

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.input_schema, dict):
            raise ValidationError({"input_schema": "Input schema must be an object."})
        if not isinstance(self.output_schema, dict):
            raise ValidationError({"output_schema": "Output schema must be an object."})
        if self.status == PromptStatus.ACTIVE and not self.output_schema:
            raise ValidationError(
                {"output_schema": "An active prompt requires a structured output schema."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AIRequestLog(models.Model):
    """Request/output provenance; successful evidence-bound calls require citations."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    purpose = models.CharField(max_length=40, choices=AIRequestPurpose.choices)
    prompt_version = models.ForeignKey(
        PromptVersion,
        on_delete=models.PROTECT,
        related_name="request_logs",
    )
    retrieval_log = models.ForeignKey(
        "rag.RetrievalLog",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ai_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_request_logs",
    )
    provider = models.CharField(max_length=80, blank=True)
    model_name = models.CharField(max_length=160, blank=True)
    input_payload = models.JSONField(default=dict)
    output_payload = models.JSONField(default=dict, blank=True)
    page_context = models.JSONField(default=dict, blank=True)
    evidence_required = models.BooleanField(default=True)
    status = models.CharField(
        max_length=24,
        choices=AIRequestStatus.choices,
        default=AIRequestStatus.PENDING,
        db_index=True,
    )
    error_code = models.CharField(max_length=80, blank=True)
    error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "id")

    def __str__(self) -> str:
        return f"{self.purpose} request {self.request_id}"

    def clean(self) -> None:
        super().clean()
        for field_name in ("input_payload", "output_payload", "page_context"):
            if not isinstance(getattr(self, field_name), dict):
                raise ValidationError({field_name: "Value must be a JSON object."})

        if self.status in TERMINAL_AI_STATUSES and self.completed_at is None:
            raise ValidationError(
                {"completed_at": "Terminal AI requests require a completion time."}
            )
        if self.status not in TERMINAL_AI_STATUSES and self.completed_at is not None:
            raise ValidationError(
                {"completed_at": "Non-terminal AI requests cannot be completed."}
            )
        if self.status == AIRequestStatus.SUCCEEDED:
            if not self.output_payload:
                raise ValidationError(
                    {"output_payload": "A successful request requires structured output."}
                )
            if self.evidence_required:
                if not self.retrieval_log_id:
                    raise ValidationError(
                        {"retrieval_log": "Evidence-bound output requires retrieval."}
                    )
                if self._state.db and not self.retrieval_log.citations.exists():
                    raise ValidationError(
                        {"retrieval_log": "Evidence-bound output requires citations."}
                    )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def mark_succeeded(self, output_payload: dict) -> None:
        self.output_payload = output_payload
        self.status = AIRequestStatus.SUCCEEDED
        self.completed_at = timezone.now()
        self.save(update_fields={"output_payload", "status", "completed_at"})

    def mark_failed(self, *, code: str, detail: str = "") -> None:
        self.status = AIRequestStatus.FAILED
        self.error_code = code
        self.error_detail = detail
        self.completed_at = timezone.now()
        self.save(
            update_fields={"status", "error_code", "error_detail", "completed_at"}
        )


class AssistantSessionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ENDED = "ended", "Ended"


class AssistantMessageRole(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"


class AssistantSession(models.Model):
    """A page-aware research navigator conversation isolated per user/session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assistant_sessions",
    )
    django_session_key = models.CharField(max_length=80, blank=True, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=AssistantSessionStatus.choices,
        default=AssistantSessionStatus.ACTIVE,
        db_index=True,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-created_at")
        indexes = [
            models.Index(fields=("user", "status", "-updated_at")),
            models.Index(fields=("django_session_key", "status", "-updated_at")),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(user__isnull=False) | ~Q(django_session_key=""),
                name="ai_assistant_session_has_owner_or_browser_session",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status=AssistantSessionStatus.ACTIVE, ended_at__isnull=True)
                    | Q(status=AssistantSessionStatus.ENDED, ended_at__isnull=False)
                ),
                name="ai_assistant_session_status_ended_at_consistent",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.user_id is None and not self.django_session_key:
            raise ValidationError("Anonymous assistant sessions require a session key.")
        if self.status == AssistantSessionStatus.ACTIVE and self.ended_at is not None:
            raise ValidationError({"ended_at": "Active sessions cannot have ended_at."})
        if self.status == AssistantSessionStatus.ENDED and self.ended_at is None:
            raise ValidationError({"ended_at": "Ended sessions require ended_at."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def end(self) -> None:
        self.status = AssistantSessionStatus.ENDED
        self.ended_at = timezone.now()
        self.save(update_fields={"status", "ended_at", "updated_at"})

    def __str__(self) -> str:
        owner = self.user_id or self.django_session_key
        return f"Assistant session {self.id} ({owner})"


class AssistantMessage(models.Model):
    """Persisted chat record; assistant content may include citations."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        AssistantSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=AssistantMessageRole.choices)
    content = models.TextField()
    page_context = models.JSONField(default=dict, blank=True)
    citations = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=("session", "created_at")),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(content=""),
                name="ai_assistant_message_content_not_empty",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.page_context, dict):
            raise ValidationError({"page_context": "Page context must be an object."})
        if not isinstance(self.citations, list):
            raise ValidationError({"citations": "Citations must be a list."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
