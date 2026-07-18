from __future__ import annotations

import uuid

from django.conf import settings
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
        verbose_name = "使用者"
        verbose_name_plural = "使用者"
        permissions = (
            (
                "hard_delete_user",
                "可直接刪除受治理紀錄保護的使用者",
            ),
        )
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
        verbose_name = "角色"
        verbose_name_plural = "角色"
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
        verbose_name = "使用者角色"
        verbose_name_plural = "使用者角色"
        ordering = ("user_id", "role_id")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "role"),
                name="accounts_user_role_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.role.slug}"


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        del kwargs
        raise ValueError("Audit log records are append-only.")

    def bulk_update(self, objs, fields, batch_size=None):
        del objs, fields, batch_size
        raise ValueError("Audit log records are append-only.")

    def delete(self):
        raise ValueError("Audit log records are append-only.")


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

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        verbose_name = "稽核紀錄"
        verbose_name_plural = "稽核紀錄"
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


class EnrollmentStatus(models.TextChoices):
    ACTIVE = "active", "在學"
    LEAVE = "leave", "休學"
    GRADUATED = "graduated", "畢業"
    WITHDRAWN = "withdrawn", "退學"
    SUSPENDED = "suspended", "停學"


class Program(models.TextChoices):
    UNDERGRADUATE = "undergraduate", "大學部"
    MASTER = "master", "碩士班"
    PHD = "phd", "博士班"
    OTHER = "other", "其他"


class StudentRoster(models.Model):
    """Canonical department membership imported by administrators only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student_id = models.CharField(max_length=32, db_index=True)
    fju_cloud_email = models.EmailField()
    department_code = models.CharField(max_length=32, db_index=True)
    program = models.CharField(max_length=32, choices=Program.choices)
    academic_year = models.PositiveSmallIntegerField()
    enrollment_status = models.CharField(
        max_length=32,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ACTIVE,
        db_index=True,
    )
    project_eligibility = models.BooleanField(default=False)
    effective_start = models.DateField()
    effective_end = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=160)
    source_version = models.CharField(max_length=80)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_roster_imports",
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "學生名冊"
        verbose_name_plural = "學生名冊"
        ordering = ("student_id", "-effective_start")
        constraints = [
            models.UniqueConstraint(
                Lower("student_id"),
                Lower("fju_cloud_email"),
                "source_version",
                name="accounts_roster_student_email_version_unique",
            ),
            models.CheckConstraint(
                condition=~models.Q(student_id=""),
                name="accounts_roster_student_id_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(department_code=""),
                name="accounts_roster_department_code_not_empty",
            ),
            models.CheckConstraint(
                condition=~models.Q(source_version=""),
                name="accounts_roster_source_version_not_empty",
            ),
        ]

    def save(self, *args, **kwargs):
        self.student_id = self.student_id.strip().casefold()
        self.fju_cloud_email = self.__class__.objects.model._meta.get_field(
            "fju_cloud_email"
        ).to_python(self.fju_cloud_email)
        self.fju_cloud_email = User.objects.normalize_email(self.fju_cloud_email)
        self.department_code = self.department_code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.student_id} ({self.department_code})"


class UserProfile(models.Model):
    """Editable account profile fields kept separate from canonical identity."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    roster_entry = models.ForeignKey(
        StudentRoster,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="linked_profiles",
    )
    display_name = models.CharField(max_length=120, blank=True)
    backup_email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=40, blank=True)
    avatar = models.ImageField(upload_to="avatars/%Y/%m/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "使用者個人資料"
        verbose_name_plural = "使用者個人資料"

    def __str__(self) -> str:
        return self.display_name or self.user.get_username()


class StudentRegistrationOTP(models.Model):
    """Hashed, single-use OTP for roster-verified student registration."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student_id = models.CharField(max_length=32, db_index=True)
    fju_cloud_email = models.EmailField(db_index=True)
    backup_email = models.EmailField()
    phone_number = models.CharField(max_length=40)
    roster_entry = models.ForeignKey(
        StudentRoster,
        on_delete=models.PROTECT,
        related_name="registration_otps",
    )
    otp_hash = models.CharField(max_length=256)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    request_id = models.CharField(max_length=128, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "學生註冊 OTP"
        verbose_name_plural = "學生註冊 OTP"
        ordering = ("-created_at", "id")

    def __str__(self) -> str:
        return f"Registration OTP for {self.student_id}"

    def save(self, *args, **kwargs):
        self.student_id = self.student_id.strip().casefold()
        self.fju_cloud_email = User.objects.normalize_email(self.fju_cloud_email)
        super().save(*args, **kwargs)
