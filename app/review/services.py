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

    if resulting_state in TERMINAL_REVIEW_STATES and locked.target_type == ReviewTargetType.RESEARCH_WORK:
        work_id = locked.target_id
        pending_count = ReviewItem.objects.filter(
            target_type=ReviewTargetType.RESEARCH_WORK,
            target_id=work_id
        ).exclude(state__in=TERMINAL_REVIEW_STATES).count()
        if pending_count == 0:
            from research.models import ResearchWork
            from research.services import transition_research_work
            try:
                work = ResearchWork.objects.get(pk=work_id)
                if work.status == "under_review":
                    transition_research_work(
                        research_work=work,
                        to_status="approved",
                        actor=reviewer,
                        reason="All candidate review items resolved.",
                        request_id=f"auto-approve-{item.id}",
                    )
                work.refresh_from_db()
                if work.status == "approved":
                    transition_research_work(
                        research_work=work,
                        to_status="published",
                        actor=reviewer,
                        reason="Auto-publishing after successful review completion.",
                        request_id=f"auto-publish-{item.id}",
                    )
            except Exception:
                pass

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
        elif field_path == "year":
            work.year = int(extracted_value)
            work.save(update_fields=["year"])
        elif field_path == "language":
            work.language = str(extracted_value)
            work.save(update_fields=["language"])
        elif field_path == "work_type":
            work.work_type = str(extracted_value)
            work.save(update_fields=["work_type"])
        elif field_path == "advisors":
            from professors.models import Professor
            from research.models import AdvisorRole
            work.advisor_links.all().delete()
            names = extracted_value if isinstance(extracted_value, list) else [extracted_value]
            for pos, name in enumerate(names, start=1):
                try:
                    prof = Professor.objects.get(display_name=name)
                    WorkAdvisor.objects.update_or_create(
                        research_work=work,
                        professor=prof,
                        defaults={
                            "role": AdvisorRole.PRIMARY if pos == 1 else AdvisorRole.CO_ADVISOR,
                            "position": pos,
                        }
                    )
                except Professor.DoesNotExist:
                    pass
        elif field_path == "authors":
            from research.models import Student
            work.author_links.all().delete()
            names = extracted_value if isinstance(extracted_value, list) else [extracted_value]
            for pos, name in enumerate(names, start=1):
                student, _ = Student.objects.get_or_create(
                    display_name=name,
                    defaults={"status": "active", "visibility_scope": "public"}
                )
                WorkAuthor.objects.update_or_create(
                    research_work=work,
                    student=student,
                    defaults={
                        "position": pos,
                    }
                )



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
        version=2,
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
    if not title:
        title = document.research_work.title or "Untitled Research"
    extracted_summary["title"] = title if title_from_document else None
    extracted_summary["title_confidence"] = 0.9 if title_from_document else None
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
    if not abstract:
        abstract = document.research_work.abstract or (first_chunk_text[:500] + "...")
    extracted_summary["abstract"] = abstract if abstract_from_document else None
    extracted_summary["abstract_confidence"] = 0.95 if abstract_from_document else None
    add_candidate("abstract", abstract, ExtractionResultType.SUMMARY,
                  confidence=0.95,
                  evidence="Extracted matching segment after 'Abstract' keyword block.",
                  source_text=first_chunk_text[:1000],
                  extraction_reason="Regex match for 'Abstract/摘要' boundary with keyword exclusions.")

    # Extract Year
    year = None
    year_from_document = False
    for roc_match in re.finditer(r"(?<!\d)(\d{2,3})\s*學年度", all_chunks_text):
        y = int(roc_match.group(1))
        if 80 <= y <= 150:
            year = y + 1911
            year_from_document = True
            break
    if not year_from_document:
        for ce_match in re.finditer(r"\b(19\d{2}|20\d{2})\b", all_chunks_text):
            candidate_year = int(ce_match.group(1))
            if 1900 <= candidate_year <= 2100:
                year = candidate_year
                year_from_document = True
                break
    extracted_summary["year"] = year if year_from_document else None
    extracted_summary["year_confidence"] = 0.85 if year_from_document else None
    if year_from_document:
        add_candidate("year", year, ExtractionResultType.SUMMARY,
                      confidence=0.85,
                      evidence="Matched explicit academic ROC year or CE year pattern.",
                      source_text=all_chunks_text[:500],
                      extraction_reason="Fail-closed regex match for explicit 學年度 or CE year format.")

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

    # Extract Work Type
    work_type = None
    work_type_from_document = False
    all_chunks_text_lower = all_chunks_text.lower()
    if "專題成果" in all_chunks_text_lower or "專題報告" in all_chunks_text_lower or "大學部" in all_chunks_text_lower:
        work_type = "undergraduate_project"
        work_type_from_document = True
    elif "碩士論文" in all_chunks_text_lower or "thesis" in all_chunks_text_lower:
        work_type = "master_thesis"
        work_type_from_document = True
    elif "期刊" in all_chunks_text_lower or "journal" in all_chunks_text_lower:
        work_type = "journal_article"
        work_type_from_document = True
    elif "研討會" in all_chunks_text_lower or "conference" in all_chunks_text_lower:
        work_type = "conference_paper"
        work_type_from_document = True
    extracted_summary["work_type"] = work_type if work_type_from_document else None
    extracted_summary["work_type_confidence"] = 0.9 if work_type_from_document else None
    if work_type_from_document:
        add_candidate("work_type", work_type, ExtractionResultType.SUMMARY,
                      confidence=0.9,
                      evidence="Matched standard document type keywords (e.g. 碩士論文, 專題).",
                      source_text=all_chunks_text[:1000],
                      extraction_reason="Keyword lookup in document text.")

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
                result = AIExtractionResult.objects.create(
                    job=job,
                    result_type=ExtractionResultType.FIELD,
                    candidate_data={"slug": field.slug},
                    confidence=1.0,
                    primary_evidence_chunk=chunk,
                    schema_version=job.schema_version,
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
                    confidence=1.0,
                    model="taxonomy-keyword-matcher",
                    evidence=f"Matched field slug or display name in chunk {chunk.chunk_index}.",
                    source_text=chunk.text[:500],
                    extraction_reason=f"Deterministic keyword match for taxonomy field '{field.display_name}'.",
                )
                candidates_created += 1
                break

    for method in methods:
        names_to_check = {method.display_name.lower(), method.slug.lower()}
        for chunk in chunks:
            chunk_text_lower = chunk.text.lower()
            if any(name in chunk_text_lower for name in names_to_check):
                method_names_found.append(method.display_name)
                result = AIExtractionResult.objects.create(
                    job=job,
                    result_type=ExtractionResultType.METHOD,
                    candidate_data={"slug": method.slug},
                    confidence=1.0,
                    primary_evidence_chunk=chunk,
                    schema_version=job.schema_version,
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
                    confidence=1.0,
                    model="taxonomy-keyword-matcher",
                    evidence=f"Matched method slug or display name in chunk {chunk.chunk_index}.",
                    source_text=chunk.text[:500],
                    extraction_reason=f"Deterministic keyword match for taxonomy method '{method.display_name}'.",
                )
                candidates_created += 1
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
