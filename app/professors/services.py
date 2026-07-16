from __future__ import annotations

from django.db import models

from .models import Professor, normalize_person_name


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
