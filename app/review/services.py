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

        if resulting_state == ReviewState.APPROVED:
            promote_value = decided_value if action == ReviewAction.EDIT else locked.candidate_value
            promote_review_item(locked, promote_value)

    return decision


def promote_review_item(item: ReviewItem, value: dict | str) -> None:
    """Promote an approved review item candidate value to canonical catalog tables."""

    from research.models import ResearchWork, WorkField, WorkMethod, RelationshipSource, RelationshipStatus
    from taxonomy.models import ResearchField, ResearchMethod

    if item.target_type == ReviewTargetType.RESEARCH_WORK:
        try:
            work = ResearchWork.objects.get(pk=item.target_id)
        except ResearchWork.DoesNotExist as exc:
            raise ValidationError("Target ResearchWork does not exist.") from exc

        field_path = item.field_path
        extracted_value = value
        if isinstance(value, dict):
            extracted_value = value.get("slug") or value.get("value") or value.get("text") or value

        if field_path.startswith("fields"):
            slug = extracted_value if isinstance(extracted_value, str) else str(extracted_value)
            field, _ = ResearchField.objects.get_or_create(
                slug=slug,
                defaults={
                    "display_name": slug.replace("-", " ").title(),
                    "status": "active",
                }
            )
            confidence = item.extraction_result.confidence if item.extraction_result else 1.0
            WorkField.objects.update_or_create(
                research_work=work,
                research_field=field,
                defaults={
                    "confidence": confidence,
                    "source_type": RelationshipSource.AI_APPROVED,
                    "status": RelationshipStatus.APPROVED,
                }
            )
        elif field_path.startswith("methods"):
            slug = extracted_value if isinstance(extracted_value, str) else str(extracted_value)
            method, _ = ResearchMethod.objects.get_or_create(
                slug=slug,
                defaults={
                    "display_name": slug.replace("-", " ").title(),
                    "status": "active",
                }
            )
            confidence = item.extraction_result.confidence if item.extraction_result else 1.0
            WorkMethod.objects.update_or_create(
                research_work=work,
                research_method=method,
                defaults={
                    "confidence": confidence,
                    "source_type": RelationshipSource.AI_APPROVED,
                    "status": RelationshipStatus.APPROVED,
                }
            )
        elif field_path == "title":
            title = extracted_value if isinstance(extracted_value, str) else str(extracted_value)
            work.title = title
            work.save(update_fields=["title"])
        elif field_path == "abstract":
            abstract = extracted_value if isinstance(extracted_value, str) else str(extracted_value)
            work.abstract = abstract
            work.save(update_fields=["abstract"])


def create_extraction_job_and_candidates(document: SourceDocument) -> AIExtractionJob:
    """Create an AI extraction job and scan document chunks for taxonomy matches."""

    from ai.models import PromptVersion, AIRequestLog, AIRequestStatus, AIRequestPurpose
    from review.models import AIExtractionJob, ExtractionJobStatus, AIExtractionResult, ExtractionResultType, ExtractionResultStatus, ReviewItem, ReviewState
    from taxonomy.models import ResearchField, ResearchMethod
    from django.utils import timezone

    # 1. Get or create a PromptVersion for metadata extraction
    prompt_version, _ = PromptVersion.objects.get_or_create(
        key="metadata-extraction",
        version=1,
        defaults={
            "template": "Extract research fields and methods.",
            "status": "active",
            "output_schema": {"type": "object"},
        }
    )

    # 2. Create an AIRequestLog for auditable extraction
    ai_request = AIRequestLog.objects.create(
        purpose=AIRequestPurpose.EXTRACTION,
        prompt_version=prompt_version,
        requested_by=document.uploaded_by,
        provider="deterministic",
        model_name="regex-matcher",
        input_payload={"document_id": str(document.pk)},
        status=AIRequestStatus.PENDING,
        evidence_required=False,
    )

    # 3. Create the Ingestion Job
    job = AIExtractionJob.objects.create(
        source_document=document,
        prompt_version=prompt_version,
        ai_request=ai_request,
        requested_by=document.uploaded_by,
        visibility_scope=document.visibility_scope,
        status=ExtractionJobStatus.RUNNING,
        started_at=timezone.now(),
    )

    # 4. Scan Chunks for ResearchFields and ResearchMethods
    chunks = list(document.chunks.all().order_by("chunk_index"))

    from taxonomy.models import TaxonomyStatus
    fields = list(ResearchField.objects.filter(status=TaxonomyStatus.ACTIVE))
    methods = list(ResearchMethod.objects.filter(status=TaxonomyStatus.ACTIVE))

    candidates_created = 0

    for field in fields:
        names_to_check = {field.display_name.lower(), field.slug.lower()}
        for chunk in chunks:
            chunk_text_lower = chunk.text.lower()
            if any(name in chunk_text_lower for name in names_to_check):
                result = AIExtractionResult.objects.create(
                    job=job,
                    result_type=ExtractionResultType.FIELD,
                    candidate_data={"slug": field.slug},
                    confidence=1.0,
                    primary_evidence_chunk=chunk,
                    schema_version="1",
                    visibility_scope=document.visibility_scope,
                    status=ExtractionResultStatus.CANDIDATE,
                )
                ReviewItem.objects.create(
                    extraction_result=result,
                    target_type=ReviewTargetType.RESEARCH_WORK,
                    target_id=document.research_work_id,
                    field_path=f"fields.{field.slug}",
                    candidate_value={"slug": field.slug},
                    state=ReviewState.PENDING,
                    visibility_scope=document.visibility_scope,
                )
                candidates_created += 1
                break

    for method in methods:
        names_to_check = {method.display_name.lower(), method.slug.lower()}
        for chunk in chunks:
            chunk_text_lower = chunk.text.lower()
            if any(name in chunk_text_lower for name in names_to_check):
                result = AIExtractionResult.objects.create(
                    job=job,
                    result_type=ExtractionResultType.METHOD,
                    candidate_data={"slug": method.slug},
                    confidence=1.0,
                    primary_evidence_chunk=chunk,
                    schema_version="1",
                    visibility_scope=document.visibility_scope,
                    status=ExtractionResultStatus.CANDIDATE,
                )
                ReviewItem.objects.create(
                    extraction_result=result,
                    target_type=ReviewTargetType.RESEARCH_WORK,
                    target_id=document.research_work_id,
                    field_path=f"methods.{method.slug}",
                    candidate_value={"slug": method.slug},
                    state=ReviewState.PENDING,
                    visibility_scope=document.visibility_scope,
                )
                candidates_created += 1
                break

    # 5. Complete Job & Request Log
    job.status = ExtractionJobStatus.SUCCEEDED
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "completed_at"])

    ai_request.output_payload = {"candidates_created": candidates_created}
    ai_request.status = AIRequestStatus.SUCCEEDED
    ai_request.completed_at = timezone.now()
    ai_request.save(update_fields=["output_payload", "status", "completed_at"])

    return job
