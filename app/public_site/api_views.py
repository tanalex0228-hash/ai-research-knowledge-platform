from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from accounts.services import record_audit_event
from documents.choices import VisibilityScope
from documents.services import create_source_document
from professors.models import Professor
from research.models import ResearchWork, WorkAdvisor, WorkField, WorkMethod
from taxonomy.models import ResearchField, ResearchMethod

from .permissions import is_admin, visible_professors, visible_research_works


def _request_id(request) -> str:
    return request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]


def _error(request, code: str, message: str, status: int, details=None):
    return JsonResponse(
        {
            "error_code": code,
            "message": message,
            "details": details or {},
            "request_id": _request_id(request),
        },
        status=status,
    )


def _work_payload(work: ResearchWork) -> dict:
    return {
        "id": str(work.id),
        "work_type": work.work_type,
        "title": work.title,
        "abstract": work.abstract,
        "year": work.year,
        "language": work.language,
        "status": work.status,
        "visibility_scope": work.visibility_scope,
    }


def _professor_payload(professor: Professor, user, works=None) -> dict:
    payload = {
        "id": str(professor.id),
        "display_name": professor.display_name,
        "title": professor.title,
        "office": professor.office,
        "profile_summary": professor.profile_summary,
    }
    contact_email = professor.contact_email_for(user)
    if contact_email:
        payload["email"] = contact_email
    if works is not None:
        payload["research_works"] = [_work_payload(work) for work in works]
    return payload


@require_GET
def research_work_list(request):
    queryset = visible_research_works(request.user)
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(Q(title__icontains=query) | Q(abstract__icontains=query))
    for field_name in ("work_type", "language"):
        value = request.GET.get(field_name, "").strip()
        if value:
            queryset = queryset.filter(**{field_name: value})
    year = request.GET.get("year", "").strip()
    if year.isdigit():
        queryset = queryset.filter(year=int(year))
    results = [_work_payload(work) for work in queryset.order_by("-year", "title")[:100]]
    return JsonResponse({"count": len(results), "results": results, "request_id": _request_id(request)})


@require_GET
def research_work_detail(request, work_id):
    work = get_object_or_404(visible_research_works(request.user), pk=work_id)
    visible_professor_ids = visible_professors(request.user).values("id")
    visible_field_ids = ResearchField.objects.discoverable_to(request.user).values("id")
    visible_method_ids = ResearchMethod.objects.discoverable_to(request.user).values("id")
    payload = _work_payload(work)
    payload["advisors"] = [
        {"id": str(link.professor_id), "display_name": link.professor.display_name}
        for link in WorkAdvisor.objects.filter(
            research_work=work, professor_id__in=visible_professor_ids
        ).select_related("professor")
    ]
    payload["fields"] = [
        {"slug": link.research_field.slug, "display_name": link.research_field.display_name}
        for link in WorkField.objects.filter(
            research_work=work,
            status="approved",
            research_field_id__in=visible_field_ids,
        ).select_related("research_field")
    ]
    payload["methods"] = [
        {"slug": link.research_method.slug, "display_name": link.research_method.display_name}
        for link in WorkMethod.objects.filter(
            research_work=work,
            status="approved",
            research_method_id__in=visible_method_ids,
        ).select_related("research_method")
    ]
    return JsonResponse({"data": payload, "request_id": _request_id(request)})


@require_GET
def professor_list(request):
    queryset = visible_professors(request.user)
    query = request.GET.get("q", "").strip()
    if query:
        queryset = queryset.filter(
            Q(display_name__icontains=query)
            | Q(normalized_name__icontains=query)
            | Q(profile_summary__icontains=query)
        )
    results = [_professor_payload(item, request.user) for item in queryset.order_by("display_name")[:100]]
    return JsonResponse({"count": len(results), "results": results, "request_id": _request_id(request)})


@require_GET
def professor_detail(request, professor_id):
    professor = get_object_or_404(visible_professors(request.user), pk=professor_id)
    works = visible_research_works(request.user).filter(
        id__in=WorkAdvisor.objects.filter(professor=professor).values("research_work_id")
    )
    return JsonResponse(
        {"data": _professor_payload(professor, request.user, works.order_by("-year", "title")), "request_id": _request_id(request)}
    )


@require_POST
def research_work_document_upload(request, work_id):
    if not is_admin(request.user):
        return _error(request, "NOT_FOUND", "Resource not found.", 404)
    work = get_object_or_404(ResearchWork, pk=work_id)
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return _error(request, "FILE_REQUIRED", "A PDF file is required.", 400)
    try:
        document = create_source_document(
            research_work=work,
            uploaded_file=uploaded_file,
            uploaded_by=request.user,
            visibility_scope=request.POST.get("visibility_scope", VisibilityScope.ADMIN),
        )
    except ValidationError as exc:
        details = getattr(exc, "message_dict", {"file": exc.messages})
        return _error(request, "INVALID_DOCUMENT", "The document was rejected.", 400, details)
    record_audit_event(
        event_type="source_document.uploaded",
        actor=request.user,
        target_type="documents.SourceDocument",
        target_id=document.id,
        request_id=_request_id(request),
        metadata={
            "research_work_id": str(work.id),
            "visibility_scope": document.visibility_scope,
        },
    )
    return JsonResponse(
        {
            "data": {
                "id": str(document.id),
                "research_work_id": str(work.id),
                "extraction_status": document.extraction_status,
                "visibility_scope": document.visibility_scope,
            },
            "request_id": _request_id(request),
        },
        status=201,
    )


@require_POST
def semantic_search(request):
    return _error(
        request,
        "SEMANTIC_SEARCH_NOT_IMPLEMENTED",
        "Semantic search is scheduled for Phase 2. Use the permission-filtered keyword search for now.",
        501,
    )


@require_POST
def teacher_matching(request):
    return _error(
        request,
        "TEACHER_MATCHING_NOT_IMPLEMENTED",
        "AI teacher matching is intentionally unavailable in the Phase 0/1 MVP.",
        501,
    )
