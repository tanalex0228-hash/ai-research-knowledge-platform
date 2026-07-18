from __future__ import annotations

import unicodedata
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.text import slugify

from accounts.permissions import VisibilityScope, visible_scopes_for


def normalize_taxonomy_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def clean_aliases(value) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError("Aliases must be a list of strings.")

    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = " ".join(unicodedata.normalize("NFKC", item).split())
        normalized = normalize_taxonomy_label(cleaned)
        if cleaned and normalized not in seen:
            seen.add(normalized)
            result.append(cleaned)
    return result


class TaxonomyStatus(models.TextChoices):
    ACTIVE = "active", "啟用"
    ARCHIVED = "archived", "封存"


class TaxonomyQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=TaxonomyStatus.ACTIVE)

    def visible_to(self, user):
        return self.filter(visibility_scope__in=visible_scopes_for(user))

    def discoverable_to(self, user):
        return self.active().visible_to(user)


class TaxonomyBase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=120, unique=True, allow_unicode=True)
    display_name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    status = models.CharField(
        max_length=20,
        choices=TaxonomyStatus.choices,
        default=TaxonomyStatus.ACTIVE,
        db_index=True,
    )
    visibility_scope = models.CharField(
        max_length=20,
        choices=VisibilityScope.choices,
        default=VisibilityScope.PUBLIC,
        db_index=True,
    )
    aliases = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TaxonomyQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ("display_name", "id")
        constraints = [
            models.CheckConstraint(
                condition=~Q(slug=""),
                name="%(app_label)s_%(class)s_slug_not_empty",
            ),
            models.CheckConstraint(
                condition=~Q(display_name=""),
                name="%(app_label)s_%(class)s_name_not_empty",
            ),
            models.CheckConstraint(
                condition=~Q(parent=F("id")),
                name="%(app_label)s_%(class)s_parent_not_self",
            ),
        ]

    def clean(self):
        super().clean()
        self.aliases = clean_aliases(self.aliases)
        if self.parent_id == self.id:
            raise ValidationError({"parent": "A taxonomy item cannot be its own parent."})

    def save(self, *args, **kwargs):
        source_slug = self.slug or self.display_name
        self.slug = slugify(source_slug, allow_unicode=True).lower()
        self.aliases = clean_aliases(self.aliases)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"slug", "aliases"}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.display_name


class ResearchField(TaxonomyBase):
    """A governed subject area such as AI finance or macroeconomics."""

    class Meta(TaxonomyBase.Meta):
        verbose_name = "研究領域"
        verbose_name_plural = "研究領域"


class ResearchMethod(TaxonomyBase):
    """A governed research method such as VAR, survey, or LSTM."""

    class Meta(TaxonomyBase.Meta):
        verbose_name = "研究方法"
        verbose_name_plural = "研究方法"


class AITag(TaxonomyBase):
    """A lower-governance approved tag; raw AI candidates live elsewhere."""

    class Meta(TaxonomyBase.Meta):
        verbose_name = "AI 標籤"
        verbose_name_plural = "AI 標籤"
