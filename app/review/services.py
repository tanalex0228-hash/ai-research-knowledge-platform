"""Narrow, auditable review state transitions.

Approval records the decision but intentionally does not publish or copy the
candidate into canonical domain tables.  That governance mapping is a later,
explicit service boundary.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import is_platform_admin, visible_scopes_for
from professors.models import Professor, ProfessorStatus
from research.models import WorkAdvisor
from review.models import (
    REVIEW_TRANSITIONS,
    TERMINAL_REVIEW_STATES,
    ReviewAction,
    ReviewDecision,
    ReviewItem,
    ReviewState,
    ReviewTargetType,
    _allow_review_transition,
)


def _is_admin_reviewer(user: object | None) -> bool:
    return is_platform_admin(user)


def _is_active_teacher(user: object | None) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    has_role = getattr(user, "has_platform_role", None) or getattr(user, "has_role", None)
    return bool(has_role and has_role("teacher"))


def _validate_teacher_assignment(item: ReviewItem, teacher: object | None) -> None:
    """Validate role and ownership where the target has a defined owner."""

    if not _is_active_teacher(teacher):
        raise ValidationError(
            {"assign_to": "Teacher review requires an active user with the teacher role."}
        )
    if item.visibility_scope not in visible_scopes_for(teacher):
        raise ValidationError(
            {"assign_to": "The review item is outside the assignee's visibility scope."}
        )

    teacher_id = getattr(teacher, "pk", None)
    if item.target_type == ReviewTargetType.PROFESSOR:
        if item.target_id is None or not Professor.objects.filter(
            pk=item.target_id,
            user_id=teacher_id,
            status=ProfessorStatus.ACTIVE,
            visibility_scope__in=visible_scopes_for(teacher),
        ).exists():
            raise ValidationError(
                {
                    "assign_to": (
                        "The assignee must own the active professor profile targeted "
                        "by this review item."
                    )
                }
            )
    elif item.target_type == ReviewTargetType.RESEARCH_WORK:
        if item.target_id is None or not WorkAdvisor.objects.filter(
            research_work_id=item.target_id,
            professor__user_id=teacher_id,
            professor__status=ProfessorStatus.ACTIVE,
            professor__visibility_scope__in=visible_scopes_for(teacher),
            research_work__visibility_scope__in=visible_scopes_for(teacher),
        ).exclude(
            research_work__status__in=("archived", "rejected")
        ).exists():
            raise ValidationError(
                {
                    "assign_to": (
                        "The assignee must be an active professor advising the "
                        "targeted research work."
                    )
                }
            )
    else:
        raise ValidationError(
            {
                "assign_to": (
                    "Teacher review is supported only for a teacher-owned Professor "
                    "or advised ResearchWork target."
                )
            }
        )


@transaction.atomic
def decide_review_item(
    *,
    item: ReviewItem,
    reviewer: object,
    action: str,
    decided_value=None,
    reason: str = "",
    assign_to: object | None = None,
) -> ReviewDecision:
    """Apply one administrator decision and append an immutable audit record."""

    if not _is_admin_reviewer(reviewer):
        raise PermissionDenied("Only platform administrators may decide review items.")
    if action not in ReviewAction.values:
        raise ValidationError({"action": "Unsupported review action."})

    locked = ReviewItem.objects.select_for_update().get(pk=item.pk)
    resulting_state = REVIEW_TRANSITIONS.get(locked.state, {}).get(action)
    if resulting_state is None:
        if locked.state in TERMINAL_REVIEW_STATES:
            raise ValidationError("This review item is terminal and cannot transition.")
        raise ValidationError(
            {
                "action": (
                    f"Action '{action}' is not allowed from state '{locked.state}'."
                )
            }
        )
    if action == ReviewAction.EDIT and decided_value is None:
        raise ValidationError({"decided_value": "Edit decisions require a value."})
    if action == ReviewAction.REJECT and not reason.strip():
        raise ValidationError({"reason": "Reject decisions require a reason."})
    if action == ReviewAction.REQUEST_TEACHER_REVIEW:
        _validate_teacher_assignment(locked, assign_to)
    elif assign_to is not None:
        raise ValidationError(
            {"assign_to": "An assignee is only valid when requesting teacher review."}
        )

    previous_state = locked.state
    with _allow_review_transition():
        decision = ReviewDecision(
            review_item=locked,
            reviewer=reviewer,
            assigned_to_snapshot=(
                assign_to
                if action == ReviewAction.REQUEST_TEACHER_REVIEW
                else None
            ),
            action=action,
            previous_state=previous_state,
            resulting_state=resulting_state,
            decided_value=decided_value,
            reason=reason.strip(),
        )
        decision.save()

        locked.state = resulting_state
        locked.assigned_to = (
            assign_to if resulting_state == ReviewState.TEACHER_REVIEW else None
        )
        locked.resolved_at = (
            timezone.now() if resulting_state in TERMINAL_REVIEW_STATES else None
        )
        locked.save(
            update_fields={"state", "assigned_to", "resolved_at", "updated_at"}
        )
    return decision
