"""Narrow, auditable review state transitions.

Approval records the decision but intentionally does not publish or copy the
candidate into canonical domain tables.  That governance mapping is a later,
explicit service boundary.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from review.models import ReviewAction, ReviewDecision, ReviewItem, ReviewState


ACTION_STATE = {
    ReviewAction.APPROVE: ReviewState.APPROVED,
    ReviewAction.EDIT: ReviewState.APPROVED,
    ReviewAction.REJECT: ReviewState.REJECTED,
    ReviewAction.REQUEST_TEACHER_REVIEW: ReviewState.TEACHER_REVIEW,
    ReviewAction.ARCHIVE: ReviewState.ARCHIVED,
}


def _is_admin_reviewer(user: object | None) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    has_role = getattr(user, "has_platform_role", None) or getattr(user, "has_role", None)
    return bool(has_role and has_role("admin"))


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
    if action not in ACTION_STATE:
        raise ValidationError({"action": "Unsupported review action."})

    locked = ReviewItem.objects.select_for_update().get(pk=item.pk)
    if locked.state in {ReviewState.APPROVED, ReviewState.REJECTED, ReviewState.ARCHIVED}:
        raise ValidationError("This review item is already resolved.")
    if action == ReviewAction.EDIT and decided_value is None:
        raise ValidationError({"decided_value": "Edit decisions require a value."})
    if action == ReviewAction.REJECT and not reason.strip():
        raise ValidationError({"reason": "Reject decisions require a reason."})
    if action == ReviewAction.REQUEST_TEACHER_REVIEW and assign_to is None:
        raise ValidationError({"assign_to": "Teacher review requires an assignee."})

    previous_state = locked.state
    resulting_state = ACTION_STATE[action]
    decision = ReviewDecision.objects.create(
        review_item=locked,
        reviewer=reviewer,
        action=action,
        previous_state=previous_state,
        resulting_state=resulting_state,
        decided_value=decided_value,
        reason=reason.strip(),
    )
    locked.state = resulting_state
    if action == ReviewAction.REQUEST_TEACHER_REVIEW:
        locked.assigned_to = assign_to
    if resulting_state in {
        ReviewState.APPROVED,
        ReviewState.REJECTED,
        ReviewState.ARCHIVED,
    }:
        locked.resolved_at = timezone.now()
    locked.save(update_fields={"state", "assigned_to", "resolved_at", "updated_at"})
    return decision

