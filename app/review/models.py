from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from documents.choices import VisibilityScope


class ExtractionJobStatus(models.TextChoices):
    PENDING = "pending", "等待中"
    RUNNING = "running", "執行中"
    SUCCEEDED = "succeeded", "成功"
    FAILED = "failed", "失敗"
    CANCELLED = "cancelled", "已取消"


class ExtractionResultType(models.TextChoices):
    SUMMARY = "summary", "摘要"
    FIELD = "field", "研究領域"
    METHOD = "method", "研究方法"
    TAG = "tag", "AI 標籤"
    VARIABLE = "variable", "變數"
    DATASET = "dataset", "資料集"
    RELATIONSHIP = "relationship", "關係"


class ExtractionResultStatus(models.TextChoices):
    CANDIDATE = "candidate", "候選知識"
    SUPERSEDED = "superseded", "已取代"


class ReviewState(models.TextChoices):
    PENDING = "pending", "待審核"
    TEACHER_REVIEW = "teacher_review", "教師審核中"
    APPROVED = "approved", "已核准"
    REJECTED = "rejected", "已拒絕"
    ARCHIVED = "archived", "已封存"


class ReviewAction(models.TextChoices):
    APPROVE = "approve", "核准"
    EDIT = "edit", "編輯並核准"
    REJECT = "reject", "拒絕"
    REQUEST_TEACHER_REVIEW = "request_teacher_review", "請教師審核"
    ARCHIVE = "archive", "封存"


class ReviewTargetType(models.TextChoices):
    RESEARCH_WORK = "research_work", "研究成果"
    PROFESSOR = "professor", "教師"
    TAXONOMY = "taxonomy", "分類"
    KNOWLEDGE_EDGE = "knowledge_edge", "知識關係"


TERMINAL_REVIEW_STATE_VALUES = (
    ReviewState.APPROVED,
    ReviewState.REJECTED,
    ReviewState.ARCHIVED,
)
TERMINAL_REVIEW_STATES = frozenset(TERMINAL_REVIEW_STATE_VALUES)

# A transition is legal only when it is represented here. In particular,
# teacher-review cannot transition to itself and terminal states have no exits.
REVIEW_TRANSITIONS = {
    ReviewState.PENDING: {
        ReviewAction.APPROVE: ReviewState.APPROVED,
        ReviewAction.EDIT: ReviewState.APPROVED,
        ReviewAction.REJECT: ReviewState.REJECTED,
        ReviewAction.REQUEST_TEACHER_REVIEW: ReviewState.TEACHER_REVIEW,
        ReviewAction.ARCHIVE: ReviewState.ARCHIVED,
    },
    ReviewState.TEACHER_REVIEW: {
        ReviewAction.APPROVE: ReviewState.APPROVED,
        ReviewAction.EDIT: ReviewState.APPROVED,
        ReviewAction.REJECT: ReviewState.REJECTED,
        ReviewAction.ARCHIVE: ReviewState.ARCHIVED,
    },
}


_review_transition_context: ContextVar[bool] = ContextVar(
    "review_transition_context",
    default=False,
)


@contextmanager
def _allow_review_transition():
    """Internal capability used only by the row-locked decision service."""

    token = _review_transition_context.set(True)
    try:
        yield
    finally:
        _review_transition_context.reset(token)


def _transition_is_allowed() -> bool:
    return _review_transition_context.get()


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
        verbose_name = "AI 擷取工作"
        verbose_name_plural = "AI 擷取工作"
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
        verbose_name = "AI 擷取結果"
        verbose_name_plural = "AI 擷取結果"
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
        verbose_name = "AI 擷取證據"
        verbose_name_plural = "AI 擷取證據"
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


class ReviewItemQuerySet(models.QuerySet):
    governed_fields = frozenset(
        {
            "state",
            "assigned_to",
            "assigned_to_id",
            "resolved_at",
            "candidate_value",
            "current_value",
            "extraction_result",
            "extraction_result_id",
            "target_type",
            "target_id",
            "field_path",
            "visibility_scope",
        }
    )

    def update(self, **kwargs):
        if self.governed_fields.intersection(kwargs) and not _transition_is_allowed():
            raise ValidationError(
                "Review governance fields may only change through decide_review_item()."
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if self.governed_fields.intersection(fields) and not _transition_is_allowed():
            raise ValidationError(
                "Review governance fields may only change through decide_review_item()."
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, **kwargs):
        objects = list(objs)
        for review_item in objects:
            review_item.full_clean()
        return super().bulk_create(
            objects,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            **kwargs,
        )


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

    confidence = models.FloatField(
        validators=(MinValueValidator(0.0), MaxValueValidator(1.0)),
        null=True,
        blank=True,
        default=1.0,
    )
    model = models.CharField(max_length=100, blank=True, default="deterministic-heuristic")
    evidence = models.TextField(blank=True, default="")
    source_text = models.TextField(blank=True, default="")
    extraction_reason = models.TextField(blank=True, default="")

    objects = ReviewItemQuerySet.as_manager()

    class Meta:
        verbose_name = "審核項目"
        verbose_name_plural = "審核項目"
        ordering = ("state", "created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(field_path=""),
                name="review_item_field_path_not_empty",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        state=ReviewState.PENDING,
                        assigned_to__isnull=True,
                        resolved_at__isnull=True,
                    )
                    | Q(
                        state=ReviewState.TEACHER_REVIEW,
                        assigned_to__isnull=False,
                        resolved_at__isnull=True,
                    )
                    | Q(
                        state__in=TERMINAL_REVIEW_STATE_VALUES,
                        assigned_to__isnull=True,
                        resolved_at__isnull=False,
                    )
                ),
                name="review_item_state_fields_consistent",
            ),
        ]

    def __str__(self) -> str:
        return f"Review {self.field_path} ({self.state})"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self._state.adding:
            if self.state != ReviewState.PENDING:
                errors["state"] = "A review item must be created in pending state."
            if self.assigned_to_id is not None:
                errors["assigned_to"] = "A new review item cannot be pre-assigned."
            if self.resolved_at is not None:
                errors["resolved_at"] = "A new review item cannot be pre-resolved."
        elif self.state == ReviewState.PENDING:
            if self.assigned_to_id is not None:
                errors["assigned_to"] = "Pending review items cannot be assigned."
            if self.resolved_at is not None:
                errors["resolved_at"] = "Pending review items cannot be resolved."
        elif self.state == ReviewState.TEACHER_REVIEW:
            if self.assigned_to_id is None:
                errors["assigned_to"] = "Teacher review requires an assignee."
            if self.resolved_at is not None:
                errors["resolved_at"] = "Teacher review is not a terminal state."
        elif self.state in TERMINAL_REVIEW_STATES:
            if self.assigned_to_id is not None:
                errors["assigned_to"] = "Resolved review items cannot remain assigned."
            if self.resolved_at is None:
                errors["resolved_at"] = "Terminal review states require a resolution time."
        if errors:
            raise ValidationError(errors)

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
        if not self._state.adding and not _transition_is_allowed():
            persisted = type(self).objects.only(
                "state",
                "assigned_to_id",
                "resolved_at",
                "candidate_value",
                "current_value",
                "extraction_result_id",
                "target_type",
                "target_id",
                "field_path",
                "visibility_scope",
            ).get(pk=self.pk)
            immutable_fields = (
                "state",
                "assigned_to_id",
                "resolved_at",
                "candidate_value",
                "current_value",
                "extraction_result_id",
                "target_type",
                "target_id",
                "field_path",
                "visibility_scope",
            )
            if any(
                getattr(self, field_name) != getattr(persisted, field_name)
                for field_name in immutable_fields
            ):
                raise ValidationError(
                    "Review governance fields may only change through "
                    "decide_review_item()."
                )
        self.full_clean()
        return super().save(*args, **kwargs)


class ReviewDecisionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        del kwargs
        raise ValidationError("Review decisions are append-only.")

    def bulk_update(self, objs, fields, batch_size=None):
        del objs, fields, batch_size
        raise ValidationError("Review decisions are append-only.")

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, **kwargs):
        del objs, batch_size, ignore_conflicts, kwargs
        raise ValidationError(
            "Review decisions may only be appended by decide_review_item()."
        )

    def delete(self):
        raise ValidationError("Review decisions are append-only and cannot be deleted.")


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
    assigned_to_snapshot = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="review_assignment_decisions",
        help_text="Immutable snapshot of the teacher selected by a review request.",
    )
    action = models.CharField(max_length=30, choices=ReviewAction.choices)
    previous_state = models.CharField(max_length=24, choices=ReviewState.choices)
    resulting_state = models.CharField(max_length=24, choices=ReviewState.choices)
    decided_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ReviewDecisionQuerySet.as_manager()

    class Meta:
        verbose_name = "審核決策"
        verbose_name_plural = "審核決策"
        ordering = ("review_item_id", "created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        Q(previous_state=ReviewState.PENDING)
                        & (
                            Q(
                                action__in=(ReviewAction.APPROVE, ReviewAction.EDIT),
                                resulting_state=ReviewState.APPROVED,
                            )
                            | Q(
                                action=ReviewAction.REJECT,
                                resulting_state=ReviewState.REJECTED,
                            )
                            | Q(
                                action=ReviewAction.REQUEST_TEACHER_REVIEW,
                                resulting_state=ReviewState.TEACHER_REVIEW,
                            )
                            | Q(
                                action=ReviewAction.ARCHIVE,
                                resulting_state=ReviewState.ARCHIVED,
                            )
                        )
                    )
                    | (
                        Q(previous_state=ReviewState.TEACHER_REVIEW)
                        & (
                            Q(
                                action__in=(ReviewAction.APPROVE, ReviewAction.EDIT),
                                resulting_state=ReviewState.APPROVED,
                            )
                            | Q(
                                action=ReviewAction.REJECT,
                                resulting_state=ReviewState.REJECTED,
                            )
                            | Q(
                                action=ReviewAction.ARCHIVE,
                                resulting_state=ReviewState.ARCHIVED,
                            )
                            # Legacy Phase 0/1 decisions could re-request a teacher
                            # review. The service now rejects this self-transition,
                            # but the DB constraint preserves historical audit rows.
                            | Q(
                                action=ReviewAction.REQUEST_TEACHER_REVIEW,
                                resulting_state=ReviewState.TEACHER_REVIEW,
                            )
                        )
                    )
                ),
                name="review_decision_transition_legal",
            )
        ]

    def __str__(self) -> str:
        return f"{self.action} by {self.reviewer_id}"

    def clean(self) -> None:
        super().clean()
        if not self.review_item_id:
            return
        expected_state = REVIEW_TRANSITIONS.get(self.previous_state, {}).get(self.action)
        if expected_state is None or expected_state != self.resulting_state:
            raise ValidationError({"action": "This review transition is not legal."})
        if self.review_item.state != self.previous_state:
            raise ValidationError(
                {"previous_state": "Decision state does not match the locked review item."}
            )
        if self.action == ReviewAction.EDIT and self.decided_value is None:
            raise ValidationError(
                {"decided_value": "Edit decisions require a replacement value."}
            )
        if self.action == ReviewAction.REJECT and not self.reason.strip():
            raise ValidationError({"reason": "Reject decisions require a reason."})
        if (
            self.action == ReviewAction.REQUEST_TEACHER_REVIEW
            and self.assigned_to_snapshot_id is None
        ):
            raise ValidationError(
                {"assigned_to_snapshot": "Teacher-review decisions require an assignee snapshot."}
            )
        if (
            self.action != ReviewAction.REQUEST_TEACHER_REVIEW
            and self.assigned_to_snapshot_id is not None
        ):
            raise ValidationError(
                {"assigned_to_snapshot": "Only teacher-review decisions record an assignee."}
            )

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Review decisions are append-only.")
        if self._state.adding and not _transition_is_allowed():
            raise ValidationError(
                "Review decisions may only be appended by decide_review_item()."
            )
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Review decisions are append-only and cannot be deleted.")
