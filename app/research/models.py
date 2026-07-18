from __future__ import annotations

import unicodedata
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from accounts.permissions import VisibilityScope, visible_scopes_for


def normalize_research_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class ResearchWorkType(models.TextChoices):
    UNDERGRADUATE_PROJECT = "undergraduate_project", "大學部專題"
    MASTER_THESIS = "master_thesis", "碩士論文"
    RESEARCH_PROJECT = "research_project", "研究計畫"
    JOURNAL_ARTICLE = "journal_article", "期刊論文"
    CONFERENCE_PAPER = "conference_paper", "研討會論文"
    OTHER = "other", "其他"


class ResearchWorkStatus(models.TextChoices):
    DRAFT = "draft", "草稿"
    UPLOADED = "uploaded", "已上傳"
    PARSED = "parsed", "已解析"
    AI_EXTRACTED = "ai_extracted", "AI 已擷取"
    UNDER_REVIEW = "under_review", "審核中"
    APPROVED = "approved", "已核准"
    PUBLISHED = "published", "已發布"
    ARCHIVED = "archived", "已封存"
    REJECTED = "rejected", "已退回"


class SourceQuality(models.TextChoices):
    UNKNOWN = "unknown", "未知"
    LOW = "low", "低"
    MEDIUM = "medium", "中"
    HIGH = "high", "高"


class ResearchWorkQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if "status" in kwargs:
            raise ValidationError(
                "Research work status must be changed through transition_research_work()."
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if "status" in fields:
            raise ValidationError(
                "Research work status must be changed through transition_research_work()."
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def published(self):
        return self.filter(status=ResearchWorkStatus.PUBLISHED)

    def visible_to(self, user):
        return self.filter(visibility_scope__in=visible_scopes_for(user))

    def discoverable_to(self, user):
        """Safe default for public pages, search retrieval, and AI evidence."""

        return self.published().visible_to(user)

    def with_catalog_relations(self):
        return self.prefetch_related(
            "advisor_links__professor",
            "field_links__research_field",
            "method_links__research_method",
            "awards",
        )


class ResearchWork(models.Model):
    # Convenient namespaced access for forms/templates without duplicating choices.
    WorkType = ResearchWorkType
    Status = ResearchWorkStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_type = models.CharField(max_length=32, choices=ResearchWorkType.choices, db_index=True)
    title = models.CharField(max_length=500)
    normalized_title = models.CharField(max_length=500, editable=False, db_index=True)
    abstract = models.TextField(blank=True)
    year = models.PositiveSmallIntegerField(db_index=True)
    language = models.CharField(max_length=16, default="zh-Hant")
    status = models.CharField(
        max_length=24,
        choices=ResearchWorkStatus.choices,
        default=ResearchWorkStatus.DRAFT,
        db_index=True,
    )
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        default=VisibilityScope.PUBLIC,
        db_index=True,
    )
    source_quality = models.CharField(
        max_length=16,
        choices=SourceQuality.choices,
        default=SourceQuality.UNKNOWN,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_works_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="research_works_updated",
    )
    professors = models.ManyToManyField(
        "professors.Professor",
        through="WorkAdvisor",
        related_name="research_works",
        blank=True,
    )
    authors = models.ManyToManyField(
        "Student",
        through="WorkAuthor",
        related_name="research_works",
        blank=True,
    )
    fields = models.ManyToManyField(
        "taxonomy.ResearchField",
        through="WorkField",
        related_name="research_works",
        blank=True,
    )
    methods = models.ManyToManyField(
        "taxonomy.ResearchMethod",
        through="WorkMethod",
        related_name="research_works",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ResearchWorkQuerySet.as_manager()

    class Meta:
        verbose_name = "研究成果"
        verbose_name_plural = "研究成果"
        ordering = ("-year", "title", "id")
        permissions = (
            (
                "hard_delete_researchwork",
                "可直接刪除研究成果與受保護治理紀錄",
            ),
        )
        constraints = [
            models.UniqueConstraint(
                fields=("normalized_title", "year", "work_type"),
                name="research_work_title_year_type_unique",
            ),
            models.CheckConstraint(
                condition=~Q(title=""),
                name="research_work_title_not_empty",
            ),
            models.CheckConstraint(
                condition=~Q(normalized_title=""),
                name="research_work_normalized_title_not_empty",
            ),
            models.CheckConstraint(
                condition=Q(year__gte=1900) & Q(year__lte=2100),
                name="research_work_year_valid",
            ),
        ]

    def save(self, *args, _allow_status_transition=False, **kwargs):
        # Trusted imports/fixtures may create a record at an existing lifecycle
        # state. Once persisted, every status change must use the transition service.
        if self.pk and not self._state.adding and not _allow_status_transition:
            persisted_status = (
                type(self)._base_manager.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
            if persisted_status is not None and persisted_status != self.status:
                raise ValidationError(
                    {"status": "Use transition_research_work() to change lifecycle state."}
                )
        self.normalized_title = normalize_research_text(self.title)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"normalized_title"}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.title} ({self.year})"


class ResearchWorkTransitionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        del kwargs
        raise ValidationError("Research work transition records are immutable.")

    def delete(self):
        raise ValidationError("Research work transition records are immutable.")

    def bulk_create(self, objs, **kwargs):
        del objs, kwargs
        raise ValidationError(
            "Research work transition rows may only be created by the lifecycle service."
        )


class ResearchWorkTransition(models.Model):
    """Append-only evidence of one legal ResearchWork lifecycle transition."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    research_work = models.ForeignKey(
        ResearchWork,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    from_status = models.CharField(max_length=24, choices=ResearchWorkStatus.choices)
    to_status = models.CharField(max_length=24, choices=ResearchWorkStatus.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="research_work_transitions",
    )
    reason = models.TextField()
    request_id = models.CharField(max_length=128, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ResearchWorkTransitionQuerySet.as_manager()

    class Meta:
        verbose_name = "研究成果狀態轉換"
        verbose_name_plural = "研究成果狀態轉換"
        ordering = ("created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(from_status=F("to_status")),
                name="research_work_transition_changes_status",
            ),
            models.CheckConstraint(
                condition=~Q(reason=""),
                name="research_work_transition_reason_not_empty",
            ),
            models.CheckConstraint(
                condition=~Q(request_id=""),
                name="research_work_transition_request_id_not_empty",
            ),
        ]

    def save(self, *args, _allow_transition_record=False, **kwargs):
        if not self._state.adding:
            raise ValidationError("Research work transition records are immutable.")
        if not _allow_transition_record:
            raise ValidationError(
                "Research work transition rows may only be created by the lifecycle service."
            )
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        del args, kwargs
        raise ValidationError("Research work transition records are immutable.")

    def __str__(self) -> str:
        return f"{self.research_work_id}: {self.from_status} → {self.to_status}"


class StudentStatus(models.TextChoices):
    ACTIVE = "active", "啟用"
    ARCHIVED = "archived", "封存"


class StudentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=StudentStatus.ACTIVE)

    def visible_to(self, user):
        return self.filter(visibility_scope__in=visible_scopes_for(user))

    def discoverable_to(self, user):
        return self.active().visible_to(user)


class Student(models.Model):
    """Minimal author identity; no student number or personal email is stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160, editable=False, db_index=True)
    public_display_name = models.CharField(max_length=160, blank=True)
    is_name_public = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=StudentStatus.choices,
        default=StudentStatus.ACTIVE,
        db_index=True,
    )
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        default=VisibilityScope.ADMIN,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StudentQuerySet.as_manager()

    class Meta:
        verbose_name = "研究學生"
        verbose_name_plural = "研究學生"
        ordering = ("display_name", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(display_name=""),
                name="research_student_name_not_empty",
            ),
            models.CheckConstraint(
                condition=~Q(normalized_name=""),
                name="research_student_normalized_name_not_empty",
            ),
        ]

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_research_text(self.display_name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"normalized_name"}
        super().save(*args, **kwargs)

    def display_name_for(self, user=None) -> str:
        if self.is_name_public:
            return self.public_display_name or self.display_name
        if (
            getattr(user, "is_authenticated", False)
            and self.visibility_scope in visible_scopes_for(user)
        ):
            return self.display_name
        return "Student author"

    def __str__(self) -> str:
        return self.display_name


class AdvisorRole(models.TextChoices):
    PRIMARY = "primary", "指導教授"
    CO_ADVISOR = "co_advisor", "共同指導"
    OTHER = "other", "其他"


class WorkAdvisor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    research_work = models.ForeignKey(
        ResearchWork,
        on_delete=models.CASCADE,
        related_name="advisor_links",
    )
    professor = models.ForeignKey(
        "professors.Professor",
        on_delete=models.PROTECT,
        related_name="advisor_links",
    )
    role = models.CharField(max_length=20, choices=AdvisorRole.choices, default=AdvisorRole.PRIMARY)
    position = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "研究成果指導教師"
        verbose_name_plural = "研究成果指導教師"
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("research_work", "professor"),
                name="research_work_advisor_unique",
            ),
            models.UniqueConstraint(
                fields=("research_work", "position"),
                name="research_work_advisor_position_unique",
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1),
                name="research_work_advisor_position_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.research_work} → {self.professor}"


class WorkAuthor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    research_work = models.ForeignKey(
        ResearchWork,
        on_delete=models.CASCADE,
        related_name="author_links",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="author_links",
    )
    position = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "研究成果作者"
        verbose_name_plural = "研究成果作者"
        ordering = ("position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("research_work", "student"),
                name="research_work_author_unique",
            ),
            models.UniqueConstraint(
                fields=("research_work", "position"),
                name="research_work_author_position_unique",
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1),
                name="research_work_author_position_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.research_work} → {self.student}"


class RelationshipSource(models.TextChoices):
    MANUAL = "manual", "人工"
    IMPORTED = "imported", "匯入"
    AI_APPROVED = "ai_approved", "AI 核准候選"


class RelationshipStatus(models.TextChoices):
    APPROVED = "approved", "已核准"
    ARCHIVED = "archived", "已封存"


class FieldRelevance(models.TextChoices):
    CORE = "core", "核心"
    RELATED = "related", "相關"
    WEAK = "weak", "弱相關"
    INCIDENTAL = "incidental", "偶然提及"


class WorkField(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    research_work = models.ForeignKey(
        ResearchWork,
        on_delete=models.CASCADE,
        related_name="field_links",
    )
    research_field = models.ForeignKey(
        "taxonomy.ResearchField",
        on_delete=models.PROTECT,
        related_name="work_links",
    )
    relevance = models.CharField(
        max_length=16,
        choices=FieldRelevance.choices,
        default=FieldRelevance.RELATED,
    )
    is_primary = models.BooleanField(default=False)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    source_type = models.CharField(
        max_length=16,
        choices=RelationshipSource.choices,
        default=RelationshipSource.MANUAL,
    )
    status = models.CharField(
        max_length=16,
        choices=RelationshipStatus.choices,
        default=RelationshipStatus.APPROVED,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "研究成果領域"
        verbose_name_plural = "研究成果領域"
        ordering = ("-is_primary", "research_field__display_name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("research_work", "research_field"),
                name="research_work_field_unique",
            ),
            models.CheckConstraint(
                condition=Q(confidence__isnull=True)
                | (Q(confidence__gte=0) & Q(confidence__lte=1)),
                name="research_work_field_confidence_valid",
            ),
            models.CheckConstraint(
                condition=Q(is_primary=False) | Q(relevance=FieldRelevance.CORE),
                name="research_primary_field_is_core",
            ),
        ]

    @property
    def field(self):
        return self.research_field

    @field.setter
    def field(self, value):
        self.research_field = value

    def __str__(self) -> str:
        return f"{self.research_work} → {self.research_field}"


class MethodUsage(models.TextChoices):
    PRIMARY = "primary", "主要"
    SUPPORTING = "supporting", "輔助"


class WorkMethod(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    research_work = models.ForeignKey(
        ResearchWork,
        on_delete=models.CASCADE,
        related_name="method_links",
    )
    research_method = models.ForeignKey(
        "taxonomy.ResearchMethod",
        on_delete=models.PROTECT,
        related_name="work_links",
    )
    usage = models.CharField(
        max_length=16,
        choices=MethodUsage.choices,
        default=MethodUsage.PRIMARY,
    )
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    source_type = models.CharField(
        max_length=16,
        choices=RelationshipSource.choices,
        default=RelationshipSource.MANUAL,
    )
    status = models.CharField(
        max_length=16,
        choices=RelationshipStatus.choices,
        default=RelationshipStatus.APPROVED,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "研究成果方法"
        verbose_name_plural = "研究成果方法"
        ordering = ("usage", "research_method__display_name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("research_work", "research_method"),
                name="research_work_method_unique",
            ),
            models.CheckConstraint(
                condition=Q(confidence__isnull=True)
                | (Q(confidence__gte=0) & Q(confidence__lte=1)),
                name="research_work_method_confidence_valid",
            ),
        ]

    @property
    def method(self):
        return self.research_method

    @method.setter
    def method(self, value):
        self.research_method = value

    def __str__(self) -> str:
        return f"{self.research_work} → {self.research_method}"


class FeaturedWorkQuerySet(models.QuerySet):
    def current(self, at=None):
        at = at or timezone.now()
        return self.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=at),
            Q(ends_at__isnull=True) | Q(ends_at__gt=at),
        )

    def visible_to(self, user):
        return self.filter(
            research_work__visibility_scope__in=visible_scopes_for(user),
        )

    def discoverable_to(self, user, at=None):
        return self.current(at).visible_to(user).filter(
            research_work__status=ResearchWorkStatus.PUBLISHED,
        )


class FeaturedWork(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    research_work = models.OneToOneField(
        ResearchWork,
        on_delete=models.CASCADE,
        related_name="feature",
    )
    headline = models.CharField(max_length=240, blank=True)
    summary = models.TextField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="featured_works_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FeaturedWorkQuerySet.as_manager()

    class Meta:
        verbose_name = "首頁精選研究"
        verbose_name_plural = "首頁精選研究"
        ordering = ("display_order", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True)
                | Q(starts_at__isnull=True)
                | Q(ends_at__gt=models.F("starts_at")),
                name="research_featured_work_window_valid",
            )
        ]

    def __str__(self) -> str:
        return self.headline or self.research_work.title


class AwardType(models.TextChoices):
    BEST_PROJECT = "best_project", "最佳專題"
    EXCELLENCE = "excellence", "優良作品"
    OTHER = "other", "其他"


class AwardStatus(models.TextChoices):
    ACTIVE = "active", "啟用"
    ARCHIVED = "archived", "封存"


class AwardQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=AwardStatus.ACTIVE)

    def visible_to(self, user):
        scopes = visible_scopes_for(user)
        return self.filter(
            visibility_scope__in=scopes,
            research_work__visibility_scope__in=scopes,
        )

    def discoverable_to(self, user):
        scopes = visible_scopes_for(user)
        return self.active().visible_to(user).filter(
            research_work__status=ResearchWorkStatus.PUBLISHED,
        ).filter(
            Q(advisor__isnull=True)
            | Q(
                advisor__status="active",
                advisor__visibility_scope__in=scopes,
            )
        )


class Award(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    research_work = models.ForeignKey(
        ResearchWork,
        on_delete=models.PROTECT,
        related_name="awards",
    )
    advisor = models.ForeignKey(
        "professors.Professor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="awards",
    )
    name = models.CharField(max_length=240)
    award_type = models.CharField(max_length=24, choices=AwardType.choices)
    award_year = models.PositiveSmallIntegerField(db_index=True)
    category = models.CharField(max_length=160, blank=True)
    organization = models.CharField(max_length=240, blank=True)
    description = models.TextField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=AwardStatus.choices,
        default=AwardStatus.ACTIVE,
        db_index=True,
    )
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        default=VisibilityScope.PUBLIC,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AwardQuerySet.as_manager()

    class Meta:
        verbose_name = "獎項"
        verbose_name_plural = "獎項"
        ordering = ("-award_year", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("research_work", "name", "award_year", "category"),
                name="research_award_work_name_year_category_unique",
            ),
            models.CheckConstraint(
                condition=~Q(name=""),
                name="research_award_name_not_empty",
            ),
            models.CheckConstraint(
                condition=Q(award_year__gte=1900) & Q(award_year__lte=2100),
                name="research_award_year_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.award_year})"
