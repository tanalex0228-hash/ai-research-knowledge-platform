from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.text import slugify


role_slug_validator = RegexValidator(
    regex=r"^[a-z][a-z0-9_-]*$",
    message="Role slugs must start with a letter and contain lowercase letters, numbers, _ or -.",
)


class UserManager(DjangoUserManager):
    @classmethod
    def normalize_email(cls, email):
        normalized = super().normalize_email(email)
        return normalized.casefold() if normalized else normalized


class User(AbstractUser):
    """Platform user with a stable, non-sequential public identifier."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    roles = models.ManyToManyField(
        "Role",
        through="UserRole",
        through_fields=("user", "role"),
        related_name="users",
        blank=True,
    )

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_case_insensitive_unique",
            )
        ]

    def save(self, *args, **kwargs):
        self.email = self.__class__.objects.normalize_email(self.email)
        super().save(*args, **kwargs)

    def has_platform_role(self, role_slug: str) -> bool:
        if not self.is_authenticated or not self.is_active:
            return False
        normalized = slugify(role_slug).lower()
        return self.roles.filter(slug=normalized, is_active=True).exists()

    def has_role(self, role_slug: str) -> bool:
        """Compatibility-friendly spelling for permission services."""

        return self.has_platform_role(role_slug)

    @property
    def user_roles(self):
        """Expose the normalized assignments under a conventional name."""

        return self.role_assignments


class RoleQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


class Role(models.Model):
    """Normalized role record; permissions remain enforced by backend services."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=50, unique=True, validators=[role_slug_validator])
    display_name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = RoleQuerySet.as_manager()

    class Meta:
        ordering = ("display_name", "slug")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(slug=""),
                name="accounts_role_slug_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(display_name=""),
                name="accounts_role_display_name_not_empty",
            )
        ]

    def save(self, *args, **kwargs):
        self.slug = slugify(self.slug).lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.display_name


class UserRole(models.Model):
    """Auditable normalized assignment instead of a role string on User."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="role_assignments")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="assignments")
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="role_assignments_made",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("user_id", "role_id")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "role"),
                name="accounts_user_role_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.role.slug}"


class AuditLog(models.Model):
    """Append-only security and governance event metadata.

    Payloads must contain identifiers and state transitions only; secrets, file
    contents, prompts, and student PII do not belong in this table.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    event_type = models.CharField(max_length=120, db_index=True)
    target_type = models.CharField(max_length=120, blank=True)
    target_id = models.UUIDField(null=True, blank=True, db_index=True)
    request_id = models.CharField(max_length=128, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(event_type=""),
                name="accounts_audit_event_type_not_empty",
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Audit log records are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit log records are append-only.")

    def __str__(self) -> str:
        return f"{self.event_type} at {self.created_at or 'pending'}"
