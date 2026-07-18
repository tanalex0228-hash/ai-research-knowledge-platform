from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch

from accounts.permissions import visible_scopes_for
from professors.models import Professor, ProfessorStatus
from taxonomy.models import TaxonomyStatus

from .models import (
    RelationshipStatus,
    ResearchWork,
    ResearchWorkStatus,
    ResearchWorkTransition,
    WorkAdvisor,
    WorkAuthor,
    WorkField,
    WorkMethod,
)


LEGAL_RESEARCH_WORK_TRANSITIONS = {
    ResearchWorkStatus.DRAFT: frozenset({
        ResearchWorkStatus.UPLOADED,
        ResearchWorkStatus.UNDER_REVIEW,
        ResearchWorkStatus.APPROVED,
        ResearchWorkStatus.PUBLISHED,
    }),
    ResearchWorkStatus.UPLOADED: frozenset({ResearchWorkStatus.PARSED}),
    ResearchWorkStatus.PARSED: frozenset({ResearchWorkStatus.AI_EXTRACTED}),
    ResearchWorkStatus.AI_EXTRACTED: frozenset({ResearchWorkStatus.UNDER_REVIEW}),
    ResearchWorkStatus.UNDER_REVIEW: frozenset({
        ResearchWorkStatus.APPROVED,
        ResearchWorkStatus.REJECTED,
        ResearchWorkStatus.PUBLISHED,
    }),
    ResearchWorkStatus.APPROVED: frozenset({ResearchWorkStatus.PUBLISHED}),
    ResearchWorkStatus.PUBLISHED: frozenset({
        ResearchWorkStatus.UNDER_REVIEW,
        ResearchWorkStatus.ARCHIVED,
    }),
    ResearchWorkStatus.ARCHIVED: frozenset({ResearchWorkStatus.PUBLISHED}),
    ResearchWorkStatus.REJECTED: frozenset({
        ResearchWorkStatus.UNDER_REVIEW,
        ResearchWorkStatus.DRAFT,
    }),
}



@transaction.atomic
def transition_research_work(
    *,
    research_work: ResearchWork,
    to_status: str,
    actor,
    reason: str,
    request_id: str,
) -> ResearchWorkTransition:
    """Lock, validate, transition, and append one immutable lifecycle audit row."""

    if not research_work.pk:
        raise ValidationError({"research_work": "Research work must be saved first."})

    normalized_status = str(getattr(to_status, "value", to_status))
    if normalized_status not in ResearchWorkStatus.values:
        raise ValidationError({"to_status": "Unknown research work lifecycle state."})
    normalized_reason = str(reason or "").strip()
    normalized_request_id = str(request_id or "").strip()
    if not normalized_reason:
        raise ValidationError({"reason": "A lifecycle transition reason is required."})
    if not normalized_request_id:
        raise ValidationError({"request_id": "A lifecycle request ID is required."})
    if not (
        getattr(actor, "is_authenticated", False)
        and getattr(actor, "is_active", False)
        and getattr(actor, "pk", None)
    ):
        raise ValidationError({"actor": "An active authenticated actor is required."})

    locked = ResearchWork.objects.select_for_update().get(pk=research_work.pk)
    allowed = LEGAL_RESEARCH_WORK_TRANSITIONS.get(locked.status, frozenset())
    if normalized_status not in allowed:
        raise ValidationError(
            {
                "to_status": (
                    f"Illegal research work transition: "
                    f"{locked.status} → {normalized_status}."
                )
            }
        )

    previous_status = locked.status
    locked.status = normalized_status
    update_fields = {"status", "updated_at"}
    locked.updated_by = actor
    update_fields.add("updated_by")
    locked.save(update_fields=update_fields, _allow_status_transition=True)

    transition = ResearchWorkTransition(
        research_work=locked,
        from_status=previous_status,
        to_status=normalized_status,
        actor=actor,
        reason=normalized_reason,
        request_id=normalized_request_id,
    )
    transition.save(_allow_transition_record=True)
    return transition


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
            "field_links__research_field__slug",
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
