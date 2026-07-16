from __future__ import annotations

from django.db import models

from accounts.permissions import is_platform_teacher, visible_scopes_for

from .models import Professor, ProfessorStatus, normalize_person_name


def find_professors_by_name(value: str, *, user=None):
    """Resolve canonical names and aliases while preserving ambiguous matches."""

    normalized = normalize_person_name(value)
    return (
        Professor.objects.discoverable_to(user)
        .filter(
            models.Q(normalized_name=normalized)
            | models.Q(aliases__normalized_alias=normalized)
        )
        .distinct()
    )


def linked_professor_for_teacher(user) -> Professor | None:
    """Resolve the sole Professor profile that grants a teacher object access."""

    if not is_platform_teacher(user):
        return None
    return Professor.objects.filter(
        user=user,
        status=ProfessorStatus.ACTIVE,
        visibility_scope__in=visible_scopes_for(user),
    ).first()
