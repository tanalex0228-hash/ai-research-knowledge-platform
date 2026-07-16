"""Small, dependency-free helpers used by role-aware querysets.

Visibility is deliberately enforced while building a queryset.  Callers such as
search, RAG, and the public site should not fetch a private row and decide later
whether to hide it.
"""

from __future__ import annotations

from django.db import models


class VisibilityScope(models.TextChoices):
    PUBLIC = "public", "Public"
    STUDENT = "student", "Students and staff"
    TEACHER = "teacher", "Teachers and administrators"
    ADMIN = "admin", "Administrators only"


ROLE_STUDENT = "student"
ROLE_TEACHER = "teacher"
ROLE_ADMIN = "admin"


def role_slugs_for(user: object | None) -> set[str]:
    """Return normalized role slugs without assuming a concrete user class."""

    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    if not getattr(user, "is_active", False) or not getattr(user, "pk", None):
        return set()

    prefetched = getattr(user, "_prefetched_objects_cache", {}).get("roles")
    if prefetched is not None:
        return {role.slug for role in prefetched if role.is_active}

    roles = getattr(user, "roles", None)
    if roles is None:
        return set()
    return set(roles.filter(is_active=True).values_list("slug", flat=True))


def visible_scopes_for(user: object | None) -> tuple[str, ...]:
    """Map a user to the visibility tiers the user may retrieve.

    Tiers are cumulative. Only Django superusers or users with the normalized
    ``admin`` role receive every tier; ``is_staff`` alone must not widen data
    access because teachers may legitimately use the admin interface.
    """

    if user is None or not getattr(user, "is_authenticated", False):
        return (VisibilityScope.PUBLIC,)
    if not getattr(user, "is_active", False):
        return (VisibilityScope.PUBLIC,)
    if getattr(user, "is_superuser", False):
        return tuple(VisibilityScope.values)

    roles = role_slugs_for(user)
    if ROLE_ADMIN in roles:
        return tuple(VisibilityScope.values)
    if ROLE_TEACHER in roles:
        return (
            VisibilityScope.PUBLIC,
            VisibilityScope.STUDENT,
            VisibilityScope.TEACHER,
        )
    if ROLE_STUDENT in roles:
        return (VisibilityScope.PUBLIC, VisibilityScope.STUDENT)
    return (VisibilityScope.PUBLIC,)
