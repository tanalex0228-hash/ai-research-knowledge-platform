from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from documents.choices import VisibilityScope


class ExtractionJobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class ExtractionResultType(models.TextChoices):
    SUMMARY = "summary", "Summary"
    FIELD = "field", "Research field"
    METHOD = "method", "Research method"
    TAG = "tag", "AI tag"
    VARIABLE = "variable", "Variable"
    DATASET = "dataset", "Dataset"
    RELATIONSHIP = "relationship", "Relationship"


class ExtractionResultStatus(models.TextChoices):
    CANDIDATE = "candidate", "Candidate knowledge"
    SUPERSEDED = "superseded", "Superseded"


class ReviewState(models.TextChoices):
    PENDING = "pending", "Pending"
    TEACHER_REVIEW = "teacher_review", "Teacher review requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    ARCHIVED = "archived", "Archived"


class ReviewAction(models.TextChoices):
    APPROVE = "approve", "Approve"
    EDIT = "edit", "Edit and approve"
    REJECT = "reject", "Reject"
    REQUEST_TEACHER_REVIEW = "request_teacher_review", "Request teacher review"
    ARCHIVE = "archive", "Archive"


class ReviewTargetType(models.TextChoices):
    RESEARCH_WORK = "research_work", "Research work"
    PROFESSOR = "professor", "Professor"
    TAXONOMY = "taxonomy", "Taxonomy"
    KNOWLEDGE_EDGE = "knowledge_edge", "Knowledge edge"


def _validate_scope_not_wider(value: str, lower_bounds: tuple[str, ...]) -> None:
    if value not in VisibilityScope.values:
        return
    if any(
        bound in VisibilityScope.values
        and VisibilityScope.rank(value) < VisibilityScope.rank(bound)
        for bound in lower_bounds
    ):
        raise ValidationError("Visibility cannot be broader than its evidence.")


class AIExtractionJob(models.Model):
    """Orchestration record only; no worker/model call is enabled in this phase."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_document = models.ForeignKey(
        "documents.SourceDocument",
        on_delete=models.PROTECT,
        related_name="ai_extraction_jobs",
    )
    prompt_version = models.ForeignKey(
        "ai.PromptVersion",
        on_delete=models.PROTECT,
        related_name="extraction_jobs",
    )
    ai_request = models.OneToOneField(
        "ai.AIRequestLog",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="extraction_job",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_extraction_jobs",
    )
    schema_version = models.CharField(max_length=40, default="1")
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        blank=True,
        default="",
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ExtractionJobStatus.choices,
        default=ExtractionJobStatus.PENDING,
        db_index=True,
    )
    error_code = models.CharField(max_length=80, blank=True)
    error_detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(schema_version=""),
                name="review_job_schema_version_not_empty",
            )
        ]

    def __str__(self) -> str:
        return f"Extraction job {self.id}"

    def clean(self) -> None:
        super().clean()
        if not self.source_document_id:
            return
        source_scope = self.source_document.visibility_scope
        if not self.visibility_scope:
            self.visibility_scope = source_scope
        try:
            _validate_scope_not_wider(self.visibility_scope, (source_scope,))
        except ValidationError as exc:
            raise ValidationError({"visibility_scope": exc.messages}) from exc
        if self.ai_request_id:
            if self.ai_request.prompt_version_id != self.prompt_version_id:
                raise ValidationError(
                    {"ai_request": "AI request and extraction job prompts must match."}
                )
            if self.ai_request.purpose != "extraction":
                raise ValidationError(
                    {"ai_request": "Extraction jobs require an extraction request."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AIExtractionResult(models.Model):
    """Unapproved candidate knowledge with mandatory primary evidence."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        AIExtractionJob,
        on_delete=models.CASCADE,
        related_name="results",
    )
    result_type = models.CharField(max_length=30, choices=ExtractionResultType.choices)
    candidate_data = models.JSONField()
    confidence = models.FloatField(
        validators=(MinValueValidator(0.0), MaxValueValidator(1.0))
    )
    primary_evidence_chunk = models.ForeignKey(
        "documents.DocumentChunk",
        on_delete=models.PROTECT,
        related_name="primary_extraction_results",
    )
    evidence_chunks = models.ManyToManyField(
        "documents.DocumentChunk",
        through="AIExtractionEvidence",
        related_name="ai_extraction_results",
    )
    schema_version = models.CharField(max_length=40)
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        blank=True,
        default="",
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ExtractionResultStatus.choices,
        default=ExtractionResultStatus.CANDIDATE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("job_id", "created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(schema_version=""),
                name="review_result_schema_version_not_empty",
            )
        ]

    def __str__(self) -> str:
        return f"{self.result_type} candidate {self.id}"

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.candidate_data, dict) or not self.candidate_data:
            raise ValidationError(
                {"candidate_data": "Candidate knowledge must be a non-empty object."}
            )
        if not self.job_id or not self.primary_evidence_chunk_id:
            return
        if (
            self.primary_evidence_chunk.source_document_id
            != self.job.source_document_id
        ):
            raise ValidationError(
                {"primary_evidence_chunk": "Evidence must come from the job document."}
            )
        if self.schema_version != self.job.schema_version:
            raise ValidationError(
                {"schema_version": "Result and extraction job schemas must match."}
            )
        if not self.visibility_scope:
            self.visibility_scope = self.job.visibility_scope
        try:
            _validate_scope_not_wider(
                self.visibility_scope,
                (
                    self.job.visibility_scope,
                    self.primary_evidence_chunk.visibility_scope,
                ),
            )
        except ValidationError as exc:
            raise ValidationError({"visibility_scope": exc.messages}) from exc

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AIExtractionEvidence(models.Model):
    """Explicit result-to-chunk provenance; evidence cannot cross documents."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    extraction_result = models.ForeignKey(
        AIExtractionResult,
        on_delete=models.CASCADE,
        related_name="evidence_links",
    )
    document_chunk = models.ForeignKey(
        "documents.DocumentChunk",
        on_delete=models.PROTECT,
        related_name="extraction_evidence_links",
    )
    excerpt = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("extraction_result_id", "document_chunk__chunk_index", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("extraction_result", "document_chunk"),
                name="review_result_evidence_chunk_unique",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if not self.extraction_result_id or not self.document_chunk_id:
            return
        if (
            self.document_chunk.source_document_id
            != self.extraction_result.job.source_document_id
        ):
            raise ValidationError(
                {"document_chunk": "Evidence must come from the extraction job document."}
            )
        try:
            _validate_scope_not_wider(
                self.extraction_result.visibility_scope,
                (self.document_chunk.visibility_scope,),
            )
        except ValidationError as exc:
            raise ValidationError({"document_chunk": exc.messages}) from exc

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReviewItem(models.Model):
    """Review state only; approval does not directly mutate governed metadata."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    extraction_result = models.OneToOneField(
        AIExtractionResult,
        on_delete=models.PROTECT,
        related_name="review_item",
    )
    target_type = models.CharField(max_length=30, choices=ReviewTargetType.choices)
    target_id = models.UUIDField(null=True, blank=True)
    field_path = models.CharField(max_length=200)
    current_value = models.JSONField(default=dict, blank=True)
    candidate_value = models.JSONField()
    state = models.CharField(
        max_length=24,
        choices=ReviewState.choices,
        default=ReviewState.PENDING,
        db_index=True,
    )
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        blank=True,
        default="",
        db_index=True,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_review_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("state", "created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(field_path=""),
                name="review_item_field_path_not_empty",
            )
        ]

    def __str__(self) -> str:
        return f"Review {self.field_path} ({self.state})"

    def clean(self) -> None:
        super().clean()
        if not self.extraction_result_id:
            return
        if not self.visibility_scope:
            self.visibility_scope = self.extraction_result.visibility_scope
        try:
            _validate_scope_not_wider(
                self.visibility_scope,
                (self.extraction_result.visibility_scope,),
            )
        except ValidationError as exc:
            raise ValidationError({"visibility_scope": exc.messages}) from exc

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReviewDecision(models.Model):
    """Append-only human decision audit entry."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review_item = models.ForeignKey(
        ReviewItem,
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="review_decisions",
    )
    action = models.CharField(max_length=30, choices=ReviewAction.choices)
    previous_state = models.CharField(max_length=24, choices=ReviewState.choices)
    resulting_state = models.CharField(max_length=24, choices=ReviewState.choices)
    decided_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("review_item_id", "created_at", "id")

    def __str__(self) -> str:
        return f"{self.action} by {self.reviewer_id}"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Review decisions are append-only.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Review decisions are append-only and cannot be deleted.")

