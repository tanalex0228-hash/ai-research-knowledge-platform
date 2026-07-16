from django.db import models


class VisibilityScope(models.TextChoices):
    """Ordered from least to most restrictive."""

    PUBLIC = "public", "Public"
    STUDENT = "student", "Students and staff"
    TEACHER = "teacher", "Teachers and administrators"
    ADMIN = "admin", "Administrators"

    @classmethod
    def rank(cls, value: str) -> int:
        order = {
            cls.PUBLIC.value: 0,
            cls.STUDENT.value: 1,
            cls.TEACHER.value: 2,
            cls.ADMIN.value: 3,
        }
        return order[str(getattr(value, "value", value))]


class ExtractionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
