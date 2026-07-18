from __future__ import annotations

import unicodedata
import uuid

from django.conf import settings
from django.db import models

from accounts.permissions import VisibilityScope, visible_scopes_for


def normalize_person_name(value: str) -> str:
    """Normalize compatibility characters and whitespace without romanizing names."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class ProfessorStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    ARCHIVED = "archived", "Archived"


class ProfessorQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=ProfessorStatus.ACTIVE)

    def visible_to(self, user):
        return self.filter(visibility_scope__in=visible_scopes_for(user))

    def discoverable_to(self, user):
        return self.active().visible_to(user)


class Professor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="professor_profile",
    )
    display_name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160, db_index=True, editable=False)
    title = models.CharField(max_length=160, blank=True)
    office = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    email_is_public = models.BooleanField(default=False)
    profile_summary = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=ProfessorStatus.choices,
        default=ProfessorStatus.ACTIVE,
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

    objects = ProfessorQuerySet.as_manager()

    class Meta:
        ordering = ("display_name", "id")
        permissions = (
            (
                "hard_delete_professor",
                "Can hard delete professors referenced by governed research records",
            ),
        )
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(display_name=""),
                name="professors_display_name_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(normalized_name=""),
                name="professors_normalized_name_not_empty",
            ),
        ]

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_person_name(self.display_name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"normalized_name"}
        super().save(*args, **kwargs)

    def contact_email_for(self, user=None) -> str:
        """Avoid exposing a private staff email from a serialized profile."""

        scopes = visible_scopes_for(user)
        if self.visibility_scope not in scopes:
            return ""
        if self.email_is_public or VisibilityScope.TEACHER in scopes:
            return self.email
        return ""

    def __str__(self) -> str:
        return self.display_name


class ProfessorAlias(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    professor = models.ForeignKey(
        Professor,
        on_delete=models.CASCADE,
        related_name="aliases",
    )
    alias = models.CharField(max_length=160)
    normalized_alias = models.CharField(max_length=160, db_index=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("alias", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("professor", "normalized_alias"),
                name="professors_alias_per_professor_unique",
            ),
            models.CheckConstraint(
                condition=~models.Q(alias=""),
                name="professors_alias_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(normalized_alias=""),
                name="professors_normalized_alias_not_empty",
            ),
        ]

    def save(self, *args, **kwargs):
        self.normalized_alias = normalize_person_name(self.alias)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"normalized_alias"}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.alias
