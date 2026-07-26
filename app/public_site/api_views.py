from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from accounts.permissions import VisibilityScope, visible_scopes_for
from accounts.services import record_audit_event
from ai.services.teacher_matching import match_teachers
from documents.choices import ExtractionStatus
from ai.models import (
    AssistantMessage,
    AssistantMessageRole,
    AssistantSession,
    AssistantSessionStatus,
)
from ai.services.research_navigator import ResearchNavigatorService
from documents.services import create_source_document, queue_document_extraction
from documents.models import SourceDocument
from knowledge_graph.models import EdgeStatus, KnowledgeEdge, KnowledgeNode, NodeStatus
from professors.models import Professor
from research.models import ResearchWork, WorkAdvisor, WorkField, WorkMethod
from research.services import transition_research_work
from taxonomy.models import ResearchField, ResearchMethod

from .api_errors import api_error_response
from .permissions import (
    is_admin,
    manageable_research_works,
    visible_professors,
    visible_research_works,
)
from .request_ids import request_id_for


def _request_id(request) -> str:
    return request_id_for(request)


def _error(request, code: str, message: str, status: int, details=None):
    return api_error_response(
        request,
        error_code=code,
        message=message,
        status=status,
        details=details,
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


def _json_body(request) -> dict:
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValidationError({"body": "Invalid JSON body."})
    if not isinstance(body, dict):
        raise ValidationError({"body": "JSON body must be an object."})
    return body


def _assistant_owner_kwargs(request, *, create_session_key: bool = False) -> dict:
    if getattr(request.user, "is_authenticated", False):
        return {"user": request.user}
    if create_session_key and not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key
    if not session_key:
        return {"user__isnull": True, "django_session_key": "__no_browser_session__"}
    return {"user__isnull": True, "django_session_key": session_key}


def _active_assistant_session(request, *, create: bool = False) -> AssistantSession | None:
    owner_kwargs = _assistant_owner_kwargs(request, create_session_key=create)
    session = (
        AssistantSession.objects.filter(
            **owner_kwargs,
            status=AssistantSessionStatus.ACTIVE,
        )
        .order_by("-updated_at", "-created_at")
        .first()
    )
    if session or not create:
        return session
    return AssistantSession.objects.create(
        user=request.user if getattr(request.user, "is_authenticated", False) else None,
        django_session_key=request.session.session_key or "",
    )


def _assistant_message_payload(message: AssistantMessage) -> dict:
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "page_context": message.page_context,
        "citations": message.citations,
        "created_at": message.created_at.isoformat(),
    }


def _assistant_session_payload(session: AssistantSession | None) -> dict:
    if session is None:
        return {"session": None, "messages": []}
    return {
        "session": {
            "id": str(session.id),
            "status": session.status,
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "last_context": session.last_context,
        },
        "messages": [
            _assistant_message_payload(message)
            for message in session.messages.order_by("created_at", "id")[:80]
        ],
    }


@require_GET
def assistant_session(request):
    session = _active_assistant_session(request)
    return JsonResponse(
        {"data": _assistant_session_payload(session), "request_id": _request_id(request)}
    )


@require_POST
def assistant_message(request):
    try:
        body = _json_body(request)
    except ValidationError as exc:
        return _error(
            request,
            "BAD_REQUEST",
            "Invalid JSON body.",
            400,
            exc.message_dict,
        )

    question = str(body.get("message") or body.get("question") or "").strip()
    if not question:
        return _error(request, "BAD_REQUEST", "Message is required.", 400)
    if len(question) > 4000:
        return _error(request, "BAD_REQUEST", "Message is too long.", 400)

    page_context = body.get("page_context") or {}
    if not isinstance(page_context, dict):
        return _error(request, "BAD_REQUEST", "page_context must be an object.", 400)

    session = _active_assistant_session(request, create=True)
    assert session is not None

    with transaction.atomic():
        user_message = AssistantMessage.objects.create(
            session=session,
            role=AssistantMessageRole.USER,
            content=question,
            page_context=page_context,
        )
        history = [
            {"role": message.role, "content": message.content}
            for message in session.messages.order_by("created_at", "id")
        ]
        result = ResearchNavigatorService().answer(
            user=request.user,
            question=question,
            page_context=page_context,
            conversation_history=history,
        )
        assistant_reply = AssistantMessage.objects.create(
            session=session,
            role=AssistantMessageRole.ASSISTANT,
            content=result["answer"],
            page_context=result["page_context"],
            citations=result["citations"],
        )
        session.last_context = result["page_context"]
        session.save(update_fields={"last_context", "updated_at"})

    return JsonResponse(
        {
            "data": {
                "session": {
                    "id": str(session.id),
                    "status": session.status,
                    "started_at": session.started_at.isoformat(),
                    "ended_at": None,
                    "last_context": session.last_context,
                },
                "user_message": _assistant_message_payload(user_message),
                "assistant_message": _assistant_message_payload(assistant_reply),
                "citations": result["citations"],
                "page_context": result["page_context"],
            },
            "request_id": _request_id(request),
        },
        status=201,
    )


@require_POST
def assistant_end(request):
    session = _active_assistant_session(request)
    if session is not None:
        session.end()
    return JsonResponse(
        {
            "data": {
                "ended": session is not None,
                "session_id": str(session.id) if session else None,
            },
            "request_id": _request_id(request),
        }
    )


@require_POST
def research_navigation_session_create(request):
    """Phase-4 API alias for the floating navigator session model."""

    session = _active_assistant_session(request, create=True)
    assert session is not None
    return JsonResponse(
        {"data": _assistant_session_payload(session), "request_id": _request_id(request)},
        status=201,
    )


@require_POST
def research_navigation_message(request, session_id):
    session = get_object_or_404(
        AssistantSession.objects.filter(status=AssistantSessionStatus.ACTIVE),
        pk=session_id,
    )
    owner_kwargs = _assistant_owner_kwargs(request)
    if not AssistantSession.objects.filter(pk=session.pk, **owner_kwargs).exists():
        return _error(request, "NOT_FOUND", "Resource not found.", 404)

    try:
        body = _json_body(request)
    except ValidationError as exc:
        return _error(
            request,
            "BAD_REQUEST",
            "Invalid JSON body.",
            400,
            exc.message_dict,
        )
    question = str(body.get("message") or body.get("question") or "").strip()
    if not question:
        return _error(request, "BAD_REQUEST", "Message is required.", 400)
    page_context = body.get("page_context") or {}
    if not isinstance(page_context, dict):
        return _error(request, "BAD_REQUEST", "page_context must be an object.", 400)

    with transaction.atomic():
        user_message = AssistantMessage.objects.create(
            session=session,
            role=AssistantMessageRole.USER,
            content=question,
            page_context=page_context,
        )
        history = [
            {"role": message.role, "content": message.content}
            for message in session.messages.order_by("created_at", "id")
        ]
        result = ResearchNavigatorService().answer(
            user=request.user,
            question=question,
            page_context=page_context,
            conversation_history=history,
        )
        assistant_reply = AssistantMessage.objects.create(
            session=session,
            role=AssistantMessageRole.ASSISTANT,
            content=result["answer"],
            page_context=result["page_context"],
            citations=result["citations"],
        )
        session.last_context = result["page_context"]
        session.save(update_fields={"last_context", "updated_at"})

    return JsonResponse(
        {
            "data": {
                "session": _assistant_session_payload(session)["session"],
                "user_message": _assistant_message_payload(user_message),
                "assistant_message": _assistant_message_payload(assistant_reply),
                "citations": result["citations"],
                "page_context": result["page_context"],
            },
            "request_id": _request_id(request),
        },
        status=201,
    )


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


@require_GET
def field_detail(request, field_id):
    field = get_object_or_404(ResearchField.objects.discoverable_to(request.user), pk=field_id)
    works = visible_research_works(request.user).filter(
        field_links__research_field=field,
        field_links__status="approved",
    )
    professors = visible_professors(request.user).filter(
        advisor_links__research_work__in=works,
    ).distinct()
    return JsonResponse(
        {
            "data": {
                "id": str(field.id),
                "slug": field.slug,
                "display_name": field.display_name,
                "description": field.description,
                "aliases": field.aliases,
                "research_works": [_work_payload(work) for work in works.order_by("-year", "title")[:50]],
                "professors": [_professor_payload(professor, request.user) for professor in professors.order_by("display_name")[:50]],
            },
            "request_id": _request_id(request),
        }
    )


@require_GET
def method_detail(request, method_id):
    method = get_object_or_404(ResearchMethod.objects.discoverable_to(request.user), pk=method_id)
    works = visible_research_works(request.user).filter(
        method_links__research_method=method,
        method_links__status="approved",
    )
    professors = visible_professors(request.user).filter(
        advisor_links__research_work__in=works,
    ).distinct()
    return JsonResponse(
        {
            "data": {
                "id": str(method.id),
                "slug": method.slug,
                "display_name": method.display_name,
                "description": method.description,
                "aliases": method.aliases,
                "research_works": [_work_payload(work) for work in works.order_by("-year", "title")[:50]],
                "professors": [_professor_payload(professor, request.user) for professor in professors.order_by("display_name")[:50]],
            },
            "request_id": _request_id(request),
        }
    )


@require_POST
def research_work_document_upload(request, work_id):
    work = get_object_or_404(manageable_research_works(request.user), pk=work_id)
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return _error(request, "FILE_REQUIRED", "A PDF file is required.", 400)
    requested_scope = request.POST.get("visibility_scope", "").strip()
    if is_admin(request.user):
        visibility_scope = requested_scope or VisibilityScope.ADMIN
    else:
        if requested_scope and requested_scope != VisibilityScope.TEACHER:
            return _error(
                request,
                "INVALID_VISIBILITY_SCOPE",
                "Teacher uploads must remain teacher-only until administrator review.",
                400,
                {"visibility_scope": "Only the teacher scope is allowed."},
            )
        visibility_scope = VisibilityScope.TEACHER
    request_id = _request_id(request)
    document = None
    try:
        with transaction.atomic():
            document = create_source_document(
                research_work=work,
                uploaded_file=uploaded_file,
                uploaded_by=request.user,
                visibility_scope=visibility_scope,
            )
            if work.status == "draft":
                transition_research_work(
                    research_work=work,
                    to_status="uploaded",
                    actor=request.user,
                    reason="Document PDF uploaded successfully.",
                    request_id=request_id,
                )
            record_audit_event(
                event_type="source_document.uploaded",
                actor=request.user,
                target_type="documents.SourceDocument",
                target_id=document.id,
                request_id=request_id,
                metadata={
                    "research_work_id": str(work.id),
                    "visibility_scope": document.visibility_scope,
                },
            )
            queue_document_extraction(document)
    except ValidationError as exc:
        details = getattr(exc, "message_dict", {"file": exc.messages})
        return _error(request, "INVALID_DOCUMENT", "The document was rejected.", 400, details)
    except Exception:
        # Database rollback cannot remove a blob already written by storage.
        # Avoid leaving an ungoverned orphan if audit persistence fails.
        if document is not None and document.file:
            document.file.delete(save=False)
        raise
    return JsonResponse(
        {
            "data": {
                "id": str(document.id),
                "research_work_id": str(work.id),
                "extraction_status": document.extraction_status,
                "visibility_scope": document.visibility_scope,
            },
            "request_id": request_id,
        },
        status=201,
    )


def _ingestion_document_for_user(user):
    queryset = SourceDocument.objects.select_related("research_work")
    if is_admin(user):
        return queryset
    return queryset.none()


def _ingestion_payload(document: SourceDocument) -> dict:
    latest_job = (
        document.ai_extraction_jobs.order_by("-created_at")
        .values("id", "status", "error_code", "created_at", "completed_at")
        .first()
    )
    return {
        "id": str(document.id),
        "source_document_id": str(document.id),
        "research_work_id": str(document.research_work_id),
        "status": document.extraction_status,
        "error": document.extraction_error,
        "latest_ai_extraction_job": {
            **latest_job,
            "id": str(latest_job["id"]),
            "created_at": latest_job["created_at"].isoformat(),
            "completed_at": latest_job["completed_at"].isoformat()
            if latest_job["completed_at"]
            else None,
        }
        if latest_job
        else None,
    }


@require_POST
def ingestion_job_create(request):
    if not is_admin(request.user):
        return _error(request, "NOT_FOUND", "Resource not found.", 404)
    try:
        body = _json_body(request)
    except ValidationError as exc:
        return _error(request, "BAD_REQUEST", "Invalid JSON body.", 400, exc.message_dict)
    document_id = body.get("source_document_id") or body.get("document_id")
    if not document_id:
        return _error(request, "BAD_REQUEST", "source_document_id is required.", 400)
    document = get_object_or_404(_ingestion_document_for_user(request.user), pk=document_id)
    document.extraction_status = ExtractionStatus.QUEUED
    document.extraction_error = ""
    document.save(update_fields={"extraction_status", "extraction_error"})
    record_audit_event(
        event_type="source_document.ingestion_queued",
        actor=request.user,
        target_type="documents.SourceDocument",
        target_id=document.id,
        request_id=_request_id(request),
        metadata={"research_work_id": str(document.research_work_id)},
    )
    queue_document_extraction(document)
    return JsonResponse(
        {"data": _ingestion_payload(document), "request_id": _request_id(request)},
        status=201,
    )


@require_GET
def ingestion_job_detail(request, job_id):
    document = get_object_or_404(_ingestion_document_for_user(request.user), pk=job_id)
    return JsonResponse(
        {"data": _ingestion_payload(document), "request_id": _request_id(request)}
    )


@csrf_exempt
@require_POST
def semantic_search(request):
    import json
    request_id = _request_id(request)

    query = ""
    limit = 10
    filters = {}
    if request.content_type == "application/json":
        try:
            body = json.loads(request.body)
            query = body.get("q") or body.get("query") or ""
            limit = int(body.get("limit", 10))
            filters = body.get("filters") or {}
        except Exception:
            return _error(request, "BAD_REQUEST", "Invalid JSON body.", 400)
    else:
        query = request.POST.get("q") or request.POST.get("query") or ""
        try:
            limit = int(request.POST.get("limit", 10))
        except ValueError:
            pass

    query = str(query).strip()
    if not query:
        return _error(request, "BAD_REQUEST", "Query parameter 'q' or 'query' is required.", 400)

    from rag.services.retrieval import PermissionAwareRetrievalService
    service = PermissionAwareRetrievalService()
    try:
        result = service.retrieve_semantic(
            query=query,
            user=request.user,
            limit=limit,
            filters=filters,
        )
    except Exception as exc:
        return _error(request, "BAD_REQUEST", str(exc), 400)

    citations_data = []
    for c in result.citations:
        work = c.document_chunk.source_document.research_work
        citations_data.append({
            "id": str(c.id),
            "rank": c.rank,
            "score": c.score,
            "excerpt": c.excerpt,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "research_work": {
                "id": str(work.id),
                "title": work.title,
                "year": work.year,
            }
        })

    return JsonResponse({
        "query": query,
        "results": citations_data,
        "request_id": request_id,
    })


@csrf_exempt
@require_POST
def teacher_matching(request):
    try:
        body = _json_body(request)
        intent = str(body.get("intent") or "").strip()
        constraints = body.get("constraints") or {}
        max_results = int(body.get("max_results", 5))
        if not intent:
            return _error(request, "BAD_REQUEST", "intent is required.", 400)
        result = match_teachers(
            intent=intent,
            user=request.user,
            constraints=constraints,
            max_results=max_results,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        return _error(request, "BAD_REQUEST", str(exc), 400)

    return JsonResponse({"data": result, "request_id": _request_id(request)})


@require_GET
def graph_node_neighbors(request, node_id):
    scopes = [str(scope) for scope in visible_scopes_for(request.user)]
    node = get_object_or_404(
        KnowledgeNode.objects.filter(
            pk=node_id,
            status=NodeStatus.ACTIVE,
            visibility_scope__in=scopes,
        )
    )
    edges = (
        KnowledgeEdge.objects.filter(
            Q(source=node) | Q(target=node),
            status=EdgeStatus.APPROVED,
            source__status=NodeStatus.ACTIVE,
            target__status=NodeStatus.ACTIVE,
            source__visibility_scope__in=scopes,
            target__visibility_scope__in=scopes,
        )
        .select_related("source", "target")
        .order_by("edge_type", "target__label", "source__label")[:100]
    )
    return JsonResponse(
        {
            "data": {
                "node": {
                    "id": str(node.id),
                    "node_type": node.node_type,
                    "object_id": str(node.object_id),
                    "label": node.label,
                },
                "neighbors": [
                    {
                        "edge_id": str(edge.id),
                        "edge_type": edge.edge_type,
                        "direction": "outgoing" if edge.source_id == node.id else "incoming",
                        "confidence": float(edge.confidence),
                        "node": {
                            "id": str(edge.target_id if edge.source_id == node.id else edge.source_id),
                            "node_type": edge.target.node_type if edge.source_id == node.id else edge.source.node_type,
                            "object_id": str(edge.target.object_id if edge.source_id == node.id else edge.source.object_id),
                            "label": edge.target.label if edge.source_id == node.id else edge.source.label,
                        },
                    }
                    for edge in edges
                ],
            },
            "request_id": _request_id(request),
        }
    )
