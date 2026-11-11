"""Auditable review decisions and narrowly scoped relationship promotion."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
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
        if action == ReviewAction.APPROVE and locked.field_path.startswith("relations."):
            _link_approved_existing_entity(locked)
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


def promote_review_item(item: ReviewItem, value: dict | str) -> None:
    """Reject canonical promotion of AI analysis.

    AI extraction is an isolated, read-only analysis aid.  A human may record
    a review decision, but the candidate must never write ResearchWork data,
    taxonomy, people, graph edges, or lifecycle state.
    """

    raise PermissionDenied("AI analysis candidates cannot be promoted to canonical data.")


def _link_approved_existing_entity(item: ReviewItem) -> None:
    """Link one existing entity after approval; never create or edit source metadata."""

    from documents.models import DocumentChunk
    from accounts.models import User
    from research.models import (
        AdvisorRole,
        ResearchWork,
        RelationshipSource,
        Student,
        WorkAuthor,
        WorkField,
        WorkMethod,
    )
    from taxonomy.models import ResearchField, ResearchMethod

    if item.target_type != ReviewTargetType.RESEARCH_WORK:
        raise ValidationError("Only research-work relationship candidates can be linked.")
    source = item.extraction_result.job.source_document
    if (
        source.research_work_id != item.target_id
        or item.extraction_result.job.schema_version != "document_intelligence.v1"
    ):
        raise ValidationError("The candidate source does not match this research work.")
    candidate = item.candidate_value
    if not isinstance(candidate, dict) or candidate.get("matched") is not True:
        raise ValidationError("Only matched existing entities can be linked.")
    evidence = candidate.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValidationError("A cited source chunk is required before linking.")
    first = evidence[0]
    source_chunk = (
        DocumentChunk.objects.filter(
            pk=first.get("chunk_id"), source_document=source
        ).first() if isinstance(first, dict) else None
    )
    if source_chunk is None:
        raise ValidationError("The candidate evidence does not belong to this document.")
    if first.get("page_number") != source_chunk.page_start:
        raise ValidationError("The cited page does not match the source chunk.")
    from review.document_intelligence import normalize_text

    excerpt = normalize_text(first.get("source_excerpt") or "")
    if not excerpt or excerpt not in normalize_text(source_chunk.text):
        raise ValidationError("The cited excerpt cannot be verified in the source chunk.")

    work = ResearchWork.objects.select_for_update().get(pk=item.target_id)
    kind = item.field_path.split(".")[1]
    if kind == "work_authors":
        student_id = candidate.get("student_id")
        user_id = candidate.get("user_id")
        student = Student.objects.active().filter(pk=student_id).first() if student_id else None
        user = User.objects.filter(pk=user_id, is_active=True).first() if user_id else None
        if student is None and (user is None or not user.has_platform_role("student")):
            raise ValidationError("The matched student account is no longer active or available.")
        existing = WorkAuthor.objects.filter(research_work=work)
        already_linked = (
            existing.filter(student=student).exists()
            if student else existing.filter(user=user).exists()
        )
        if not already_linked:
            position = (WorkAuthor.objects.filter(research_work=work).aggregate(
                maximum=Max("position")
            )["maximum"] or 0) + 1
            if student:
                WorkAuthor.objects.create(research_work=work, student=student, position=position)
            else:
                WorkAuthor.objects.create(research_work=work, user=user, position=position)
    elif kind == "work_advisors":
        professor = Professor.objects.active().filter(pk=candidate.get("professor_id")).first()
        if professor is None:
            raise ValidationError("The matched professor is no longer active or available.")
        if not WorkAdvisor.objects.filter(research_work=work, professor=professor).exists():
            position = (WorkAdvisor.objects.filter(research_work=work).aggregate(
                maximum=Max("position")
            )["maximum"] or 0) + 1
            role = candidate.get("role")
            if role not in AdvisorRole.values:
                raise ValidationError("The advisor role is not supported.")
            WorkAdvisor.objects.create(
                research_work=work, professor=professor, role=role, position=position
            )
    elif kind == "work_fields":
        field = ResearchField.objects.active().filter(pk=candidate.get("research_field_id")).first()
        if field is None:
            raise ValidationError("The matched research field is no longer active.")
        WorkField.objects.get_or_create(
            research_work=work,
            research_field=field,
            defaults={"source_type": RelationshipSource.AI_APPROVED},
        )
    elif kind == "work_methods":
        method = ResearchMethod.objects.active().filter(pk=candidate.get("research_method_id")).first()
        if method is None:
            raise ValidationError("The matched research method is no longer active.")
        WorkMethod.objects.get_or_create(
            research_work=work,
            research_method=method,
            defaults={"source_type": RelationshipSource.AI_APPROVED},
        )
    else:
        raise ValidationError("Unsupported relationship candidate.")



def create_extraction_job_and_candidates(
    document: SourceDocument,
    *,
    provider=None,
) -> AIExtractionJob:
    """Create an extraction job and full-document metadata review candidates."""

    from ai.models import PromptVersion, AIRequestLog, AIRequestStatus, AIRequestPurpose
    from review.models import AIExtractionJob, ExtractionJobStatus, AIExtractionResult, ExtractionResultType, ExtractionResultStatus, ReviewItem, ReviewState
    from taxonomy.models import ResearchField, ResearchMethod
    from django.utils import timezone
    import re
    from review.document_intelligence import (
        DOCUMENT_INTELLIGENCE_OUTPUT_SCHEMA,
        DOCUMENT_INTELLIGENCE_PROMPT,
        DOCUMENT_INTELLIGENCE_SCHEMA_VERSION,
        normalize_provider_payload,
        whole_document_text,
    )
    from review.document_intelligence_providers import (
        DeterministicDocumentIntelligenceProvider,
        DocumentIntelligenceProviderError,
        audit_safe_input_payload,
        get_document_intelligence_provider,
    )

    # 1. Get or create a PromptVersion for metadata extraction
    prompt_version, _ = PromptVersion.objects.get_or_create(
        key="metadata-extraction",
        version=4,
        defaults={
            "template": DOCUMENT_INTELLIGENCE_PROMPT,
            "status": "active",
            "output_schema": DOCUMENT_INTELLIGENCE_OUTPUT_SCHEMA,
            "schema_version": DOCUMENT_INTELLIGENCE_SCHEMA_VERSION,
        }
    )

    chunks = list(document.chunks.all().order_by("chunk_index"))
    selected_provider = provider or get_document_intelligence_provider()

    # 2. Create an AIRequestLog for auditable extraction
    ai_request = AIRequestLog.objects.create(
        purpose=AIRequestPurpose.EXTRACTION,
        prompt_version=prompt_version,
        requested_by=document.uploaded_by,
        provider=selected_provider.provider_name,
        model_name=selected_provider.model_name,
        input_payload=audit_safe_input_payload(
            document=document,
            chunks=chunks,
            provider_name=selected_provider.provider_name,
            model_name=selected_provider.model_name,
        ),
        status=AIRequestStatus.RUNNING,
        started_at=timezone.now(),
        evidence_required=False,
    )

    # 3. Create the Ingestion Job
    job = AIExtractionJob.objects.create(
        source_document=document,
        prompt_version=prompt_version,
        ai_request=ai_request,
        requested_by=document.uploaded_by,
        schema_version=DOCUMENT_INTELLIGENCE_SCHEMA_VERSION,
        visibility_scope=document.visibility_scope,
        status=ExtractionJobStatus.RUNNING,
        started_at=timezone.now(),
    )

    # 4. Scan Chunks for Metadata and Taxonomy matches
    first_chunk_text = chunks[0].text if chunks else ""
    all_chunks_text = whole_document_text(chunks)
    candidates_created = 0

    def add_candidate(field_path, value, result_type, confidence=1.0, model="deterministic-heuristic", evidence="", source_text="", extraction_reason="", candidate_data=None):
        nonlocal candidates_created
        if not chunks:
            return
        payload = candidate_data if candidate_data is not None else {"value": value}
        result = AIExtractionResult.objects.create(
            job=job,
            result_type=result_type,
            candidate_data=payload,
            confidence=confidence,
            primary_evidence_chunk=chunks[0],
            schema_version=job.schema_version,
            visibility_scope=document.visibility_scope,
            status=ExtractionResultStatus.CANDIDATE,
        )
        ReviewItem.objects.create(
            extraction_result=result,
            target_type=ReviewTargetType.RESEARCH_WORK,
            target_id=document.research_work_id,
            field_path=field_path,
            candidate_value=payload,
            state=ReviewState.PENDING,
            visibility_scope=document.visibility_scope,
            confidence=confidence,
            model=model,
            evidence=evidence,
            source_text=source_text,
            extraction_reason=extraction_reason,
        )
        candidates_created += 1

    extracted_summary: dict[str, object] = {}
    advisors_found: list[str] = []
    authors_found: list[str] = []
    field_names_found: list[str] = []
    method_names_found: list[str] = []

    def split_person_names(raw_value: str) -> list[str]:
        names: list[str] = []
        for item in re.split(r"[、，,；;\s]+", raw_value.strip()):
            cleaned = item.strip("：:()（）[]【】")
            if 2 <= len(cleaned) <= 40 and cleaned not in names:
                names.append(cleaned)
        return names

    def labeled_people(label_pattern: str) -> list[str]:
        people: list[str] = []
        pattern = rf"(?:^|\n)\s*{label_pattern}\s*[:：\s]+([^\n\r]{{2,120}})"
        for match in re.finditer(pattern, all_chunks_text, flags=re.IGNORECASE):
            raw = re.split(
                r"(?:摘要|關鍵字|研究|目錄|Abstract|Keywords|學生|作者|指導|共同指導)",
                match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            for name in split_person_names(raw):
                if name not in people:
                    people.append(name)
        return people

    # Extract Title
    title = ""
    title_from_document = False
    if first_chunk_text:
        lines = [line.strip() for line in first_chunk_text.split('\n') if line.strip()]
        stop_keywords = ["輔仁", "大學", "學系", "專題", "學年度", "指導老師", "學生", "研究計劃", "報告", "目錄", "abstract", "摘要"]
        candidate_lines = []
        for line in lines[:8]:
            if not any(k in line for k in stop_keywords) and len(line) > 3:
                candidate_lines.append(line)
        if candidate_lines:
            title = max(candidate_lines[:3], key=len)
            title_from_document = True
        else:
            title = lines[0] if lines else "Untitled Research"
            title_from_document = bool(lines)
    extracted_summary["title"] = title if title_from_document else None
    extracted_summary["title_confidence"] = 0.9 if title_from_document else None
    if title_from_document:
        add_candidate("title", title, ExtractionResultType.SUMMARY,
                      confidence=0.9,
                      evidence="First chunk of PDF contains title text in prominent layout.",
                      source_text=first_chunk_text[:500],
                      extraction_reason="Heuristically extracted highest length line matching Title layout rules.")

    # Extract Abstract
    abstract = ""
    abstract_from_document = False
    for chunk in chunks:
        match = re.search(r'(摘要|Abstract|ABSTRACT)[:：\s\n]+(.*)', chunk.text, re.DOTALL | re.IGNORECASE)
        if match:
            abstract_text = match.group(2).strip()
            stop_match = re.split(r'(關鍵字|Keywords|Keywords:|1\.\s+(前言|緒論|Introduction)|目錄)', abstract_text, maxsplit=1, flags=re.IGNORECASE)
            abstract = stop_match[0].strip()
            if abstract:
                abstract_from_document = True
                break
    extracted_summary["abstract"] = abstract if abstract_from_document else None
    extracted_summary["abstract_confidence"] = 0.95 if abstract_from_document else None
    if abstract_from_document:
        add_candidate("abstract", abstract, ExtractionResultType.SUMMARY,
                      confidence=0.95,
                      evidence="Extracted matching segment after 'Abstract' keyword block.",
                      source_text=first_chunk_text[:1000],
                      extraction_reason="Regex match for 'Abstract/摘要' boundary with keyword exclusions.")

    # Extract Language
    has_chinese = bool(re.search(r'[\u4e00-\u9fa5]', all_chunks_text))
    language = "zh-Hant" if has_chinese else "en"
    extracted_summary["language"] = language
    extracted_summary["language_confidence"] = 1.0
    add_candidate("language", language, ExtractionResultType.SUMMARY,
                  confidence=1.0,
                  evidence="Matched unicode ranges for Chinese characters.",
                  source_text=all_chunks_text[:1000],
                  extraction_reason="Scanned text character encoding detection.")

    # Extract Advisors
    advisors_found = labeled_people(r"(?<!共同)指導(?:教授|老師)")
    if advisors_found:
        add_candidate("advisors", advisors_found, ExtractionResultType.SUMMARY,
                      confidence=0.88,
                      evidence=f"Matched explicit advisor label names: {', '.join(advisors_found)}.",
                      source_text=all_chunks_text,
                      extraction_reason="Extracted only from explicit 指導教授/指導老師 label blocks.")

    # Extract Authors
    authors_found = labeled_people(r"(?:學生|作者|撰寫人)")
    if authors_found:
        add_candidate("authors", authors_found, ExtractionResultType.SUMMARY,
                      confidence=0.88,
                      evidence=f"Matched explicit author/student label names: {', '.join(authors_found)}.",
                      source_text=all_chunks_text,
                      extraction_reason="Extracted only from explicit 學生/作者/撰寫人 label blocks.")

    # Scan Chunks for ResearchFields and ResearchMethods (Taxonomy)
    from taxonomy.models import TaxonomyStatus
    fields = list(ResearchField.objects.filter(status=TaxonomyStatus.ACTIVE))
    methods = list(ResearchMethod.objects.filter(status=TaxonomyStatus.ACTIVE))

    for field in fields:
        names_to_check = {field.display_name.lower(), field.slug.lower()}
        for chunk in chunks:
            chunk_text_lower = chunk.text.lower()
            if any(name in chunk_text_lower for name in names_to_check):
                field_names_found.append(field.display_name)
                break

    for method in methods:
        names_to_check = {method.display_name.lower(), method.slug.lower()}
        for chunk in chunks:
            chunk_text_lower = chunk.text.lower()
            if any(name in chunk_text_lower for name in names_to_check):
                method_names_found.append(method.display_name)
                break

    active_ai_request = ai_request
    try:
        intelligence_payload = selected_provider.extract(
            document=document,
            chunks=chunks,
            extracted=extracted_summary,
            advisor_names=advisors_found,
            author_names=authors_found,
            field_names=field_names_found,
            method_names=method_names_found,
        )
        intelligence_payload = normalize_provider_payload(
            document=document,
            chunks=chunks,
            raw_payload=intelligence_payload,
        )
        intelligence_model = selected_provider.model_name
        intelligence_provider_name = selected_provider.provider_name
        fallback_reason = getattr(selected_provider, "fallback_reason", "")
        if fallback_reason:
            intelligence_payload["provider_fallback_reason"] = fallback_reason
    except DocumentIntelligenceProviderError as exc:
        ai_request.mark_failed(code=exc.__class__.__name__, detail=str(exc))
        fallback_provider = DeterministicDocumentIntelligenceProvider(
            fallback_reason=exc.__class__.__name__
        )
        active_ai_request = AIRequestLog.objects.create(
            purpose=AIRequestPurpose.EXTRACTION,
            prompt_version=prompt_version,
            requested_by=document.uploaded_by,
            provider=fallback_provider.provider_name,
            model_name=fallback_provider.model_name,
            input_payload=audit_safe_input_payload(
                document=document,
                chunks=chunks,
                provider_name=fallback_provider.provider_name,
                model_name=fallback_provider.model_name,
            )
            | {"fallback_from": selected_provider.provider_name, "fallback_reason": str(exc)},
            status=AIRequestStatus.RUNNING,
            started_at=timezone.now(),
            evidence_required=False,
        )
        job.ai_request = active_ai_request
        job.save(update_fields=["ai_request", "updated_at"])
        intelligence_payload = fallback_provider.extract(
            document=document,
            chunks=chunks,
            extracted=extracted_summary,
            advisor_names=advisors_found,
            author_names=authors_found,
            field_names=field_names_found,
            method_names=method_names_found,
        )
        intelligence_payload = normalize_provider_payload(
            document=document,
            chunks=chunks,
            raw_payload=intelligence_payload,
        )
        intelligence_payload["provider_fallback_reason"] = exc.__class__.__name__
        intelligence_model = fallback_provider.model_name
        intelligence_provider_name = fallback_provider.provider_name
        fallback_reason = str(exc)
    add_candidate(
        "document_intelligence",
        intelligence_payload,
        ExtractionResultType.SUMMARY,
        confidence=0.82 if intelligence_provider_name != "deterministic" else 0.72,
        model=intelligence_model,
        evidence=(
            "Structured whole-document candidate assembled from all page-aware chunks, "
            "provider output, and normalized database matches."
        ),
        source_text=all_chunks_text[:1500],
        extraction_reason=(
            "Uses all page-aware chunks to build a canonical metadata, knowledge graph, "
            "academic summary, and database action candidate for review."
        ),
        candidate_data=intelligence_payload,
    )

    # One review row per proposed relation lets the reviewer approve only the
    # existing people/taxonomy terms they verified. Unmatched names remain
    # suggestions and never create canonical entities or links.
    for kind in ("work_authors", "work_advisors", "work_fields", "work_methods"):
        for index, candidate in enumerate(
            intelligence_payload.get("database_actions", {}).get(kind, [])
        ):
            evidence = candidate.get("evidence") if isinstance(candidate, dict) else None
            if not isinstance(evidence, list) or not evidence:
                continue
            first_evidence = evidence[0]
            if not isinstance(first_evidence, dict):
                continue
            source_chunk = next(
                (chunk for chunk in chunks if str(chunk.pk) == first_evidence.get("chunk_id")),
                None,
            )
            if source_chunk is None:
                continue
            matched = candidate.get("matched") is True
            result = AIExtractionResult.objects.create(
                job=job,
                result_type=ExtractionResultType.RELATIONSHIP,
                candidate_data=candidate,
                confidence=candidate.get("confidence") or 0,
                primary_evidence_chunk=source_chunk,
                schema_version=job.schema_version,
                visibility_scope=document.visibility_scope,
                status=ExtractionResultStatus.CANDIDATE,
            )
            ReviewItem.objects.create(
                extraction_result=result,
                target_type=ReviewTargetType.RESEARCH_WORK,
                target_id=document.research_work_id,
                field_path=("relations" if matched else "suggestions") + f".{kind}.{index + 1}",
                candidate_value=candidate,
                state=ReviewState.PENDING,
                visibility_scope=document.visibility_scope,
                confidence=candidate.get("confidence") or 0,
                model=intelligence_model,
                evidence=f"第 {first_evidence.get('page_number')} 頁：{first_evidence.get('source_excerpt', '')}",
                source_text=first_evidence.get("source_excerpt", "")[:1000],
                extraction_reason=(
                    "Existing database match awaiting human approval."
                    if matched else "No unique existing database match; informational only."
                ),
            )
            candidates_created += 1

    # 5. Complete Job & Request Log
    job.status = ExtractionJobStatus.SUCCEEDED
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "completed_at"])

    active_ai_request.output_payload = {
        "schema_version": job.schema_version,
        "candidates_created": candidates_created,
        "document_intelligence_candidate": True,
        "provider": intelligence_provider_name,
        "model": intelligence_model,
        "fallback_reason": fallback_reason,
        "document_intelligence": intelligence_payload,
    }
    active_ai_request.status = AIRequestStatus.SUCCEEDED
    active_ai_request.completed_at = timezone.now()
    active_ai_request.save(update_fields={"output_payload", "status", "completed_at"})

    return job
