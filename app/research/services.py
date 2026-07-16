from __future__ import annotations

from django.db.models import Count, Prefetch

from accounts.permissions import visible_scopes_for
from professors.models import Professor, ProfessorStatus
from taxonomy.models import TaxonomyStatus

from .models import (
    RelationshipStatus,
    ResearchWork,
    WorkAdvisor,
    WorkAuthor,
    WorkField,
    WorkMethod,
)


def research_catalog_for(user):
    """Permission-safe catalog with only visible, governed taxonomy links."""

    scopes = visible_scopes_for(user)
    field_links = WorkField.objects.select_related("research_field").filter(
        status=RelationshipStatus.APPROVED,
        research_field__status=TaxonomyStatus.ACTIVE,
        research_field__visibility_scope__in=scopes,
    )
    method_links = WorkMethod.objects.select_related("research_method").filter(
        status=RelationshipStatus.APPROVED,
        research_method__status=TaxonomyStatus.ACTIVE,
        research_method__visibility_scope__in=scopes,
    )
    advisor_links = WorkAdvisor.objects.select_related("professor").filter(
        professor__status=ProfessorStatus.ACTIVE,
        professor__visibility_scope__in=scopes,
    )
    return (
        ResearchWork.objects.discoverable_to(user)
        .prefetch_related(
            Prefetch("advisor_links", queryset=advisor_links, to_attr="visible_advisor_links"),
            Prefetch("field_links", queryset=field_links, to_attr="visible_field_links"),
            Prefetch("method_links", queryset=method_links, to_attr="visible_method_links"),
        )
    )


def professor_works(professor, *, user=None, field=None, method=None):
    if not Professor.objects.discoverable_to(user).filter(pk=professor.pk).exists():
        return ResearchWork.objects.none()
    queryset = ResearchWork.objects.discoverable_to(user).filter(
        advisor_links__professor=professor,
    )
    if field is not None:
        queryset = queryset.filter(
            field_links__research_field=field,
            field_links__status=RelationshipStatus.APPROVED,
        )
    if method is not None:
        queryset = queryset.filter(
            method_links__research_method=method,
            method_links__status=RelationshipStatus.APPROVED,
        )
    return queryset.distinct()


def work_author_names(research_work, *, user=None) -> list[str]:
    """Return author labels safe for the caller; never expose Student objects."""

    return [
        link.student.display_name_for(user)
        for link in WorkAuthor.objects.filter(research_work=research_work)
        .select_related("student")
        .order_by("position")
    ]


def professor_field_distribution(professor, *, user=None):
    """Drill-down counts derived only from works the user may discover."""

    scopes = visible_scopes_for(user)
    return (
        professor_works(professor, user=user)
        .filter(
            field_links__status=RelationshipStatus.APPROVED,
            field_links__research_field__status=TaxonomyStatus.ACTIVE,
            field_links__research_field__visibility_scope__in=scopes,
        )
        .values(
            "field_links__research_field_id",
            "field_links__research_field__display_name",
        )
        .annotate(work_count=Count("id", distinct=True))
        .order_by("-work_count", "field_links__research_field__display_name")
    )


def professor_method_distribution(professor, *, user=None):
    scopes = visible_scopes_for(user)
    return (
        professor_works(professor, user=user)
        .filter(
            method_links__status=RelationshipStatus.APPROVED,
            method_links__research_method__status=TaxonomyStatus.ACTIVE,
            method_links__research_method__visibility_scope__in=scopes,
        )
        .values(
            "method_links__research_method_id",
            "method_links__research_method__display_name",
        )
        .annotate(work_count=Count("id", distinct=True))
        .order_by("-work_count", "method_links__research_method__display_name")
    )
