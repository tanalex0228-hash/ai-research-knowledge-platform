from django.db import models


class VisibilityScope(models.TextChoices):
    """Ordered from least to most restrictive."""

    PUBLIC = "public", "公開"
    STUDENT = "student", "學生與教職員"
    TEACHER = "teacher", "教師與管理員"
    ADMIN = "admin", "管理員"

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
    PENDING = "pending", "等待中"
    QUEUED = "queued", "已排程"
    PROCESSING = "processing", "處理中"
    SUCCEEDED = "succeeded", "成功"
    FAILED = "failed", "失敗"
