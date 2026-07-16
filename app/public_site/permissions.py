from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet

from accounts.permissions import (
    is_platform_admin,
    is_platform_teacher,
    visible_scopes_for,
)

if TYPE_CHECKING:
    from accounts.models import User
    from documents.models import SourceDocument


PUBLISHED_STATUS = "published"


def user_has_role(user: "User", role_slug: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return role_slug == "admin"
    has_role = getattr(user, "has_role", None)
    if callable(has_role):
        return bool(has_role(role_slug))
    return user.user_roles.filter(role__slug=role_slug, role__is_active=True).exists()


def is_admin(user: "User") -> bool:
    return is_platform_admin(user)


def manageable_research_works(user: "User") -> QuerySet:
    """Works whose documents the caller may manage without existence leakage."""

    from research.models import ResearchWork

    if is_platform_admin(user):
        return ResearchWork.objects.all()
    if is_platform_teacher(user):
        return (
            ResearchWork.objects.filter(
                advisor_links__professor__user=user,
                advisor_links__professor__status="active",
                advisor_links__professor__visibility_scope__in=visible_scopes_for(user),
                visibility_scope__in=visible_scopes_for(user),
            )
            .exclude(status__in=("archived", "rejected"))
            .distinct()
        )
    return ResearchWork.objects.none()


def visible_research_works(user: "User") -> QuerySet:
    from research.models import ResearchWork

    return ResearchWork.objects.discoverable_to(user)


def visible_professors(user: "User") -> QuerySet:
    from professors.models import Professor

    return Professor.objects.discoverable_to(user)


def can_download_document(user: "User", document: "SourceDocument") -> bool:
    work = document.research_work
    if work.status != PUBLISHED_STATUS:
        return False
    scopes = set(visible_scopes_for(user))
    return document.visibility_scope in scopes and work.visibility_scope in scopes
