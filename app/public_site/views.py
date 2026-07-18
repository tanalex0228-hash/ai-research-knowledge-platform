from __future__ import annotations

from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET

from accounts.permissions import visible_scopes_for
from documents.models import SourceDocument
from professors.models import Professor
from research.models import Award, FeaturedWork, ResearchWork, WorkAdvisor, WorkField, WorkMethod
from research.services import professor_field_distribution
from taxonomy.models import ResearchField, ResearchMethod, normalize_taxonomy_label

from .permissions import can_download_document, visible_professors, visible_research_works


@require_GET
def healthz(request):
    database_status = "ok"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        database_status = "unavailable"
    status_code = 200 if database_status == "ok" else 503
    return JsonResponse(
        {
            "status": "ok" if status_code == 200 else "degraded",
            "service": "ai-research-knowledge-platform",
            "checks": {"database": database_status},
        },
        status=status_code,
    )


@require_GET
def home(request):
    works = visible_research_works(request.user)
    professors = visible_professors(request.user)
    fields = (
        ResearchField.objects.discoverable_to(request.user)
        .annotate(
            public_work_count=Count(
                "work_links",
                filter=Q(
                    work_links__research_work__in=works,
                    work_links__status="approved",
                ),
                distinct=True,
            )
        )
        .order_by("-public_work_count", "display_name")[:8]
    )
    featured = (
        FeaturedWork.objects.discoverable_to(request.user)
        .filter(research_work__in=works)
        .select_related("research_work")
        .order_by("display_order", "-created_at")[:6]
    )
    awards = (
        Award.objects.discoverable_to(request.user)
        .filter(research_work__in=works)
        .select_related("research_work")
        .order_by("-award_year", "display_order")[:6]
    )
    context = {
        "professor_count": professors.count(),
        "work_count": works.count(),
        "professors": professors.order_by("display_name")[:6],
        "fields": fields,
        "featured": featured,
        "awards": awards,
    }
    return render(request, "public_site/home.html", context)


@require_GET
def professor_list(request):
    query = request.GET.get("q", "").strip()
    queryset = visible_professors(request.user).order_by("display_name")
    if query:
        queryset = queryset.filter(
            Q(display_name__icontains=query)
            | Q(normalized_name__icontains=query)
            | Q(profile_summary__icontains=query)
            | Q(aliases__alias__icontains=query)
        ).distinct()
    page = Paginator(queryset, 18).get_page(request.GET.get("page"))
    return render(
        request,
        "public_site/professor_list.html",
        {"page_obj": page, "query": query},
    )


@require_GET
def professor_detail(request, professor_id):
    professor = get_object_or_404(visible_professors(request.user), pk=professor_id)
    works = visible_research_works(request.user).filter(
        id__in=WorkAdvisor.objects.filter(professor=professor).values("research_work_id")
    )
    field_filter = request.GET.get("field", "").strip()
    if field_filter:
        works = works.filter(
            field_links__research_field__slug=field_filter,
            field_links__status="approved",
        )
    field_rows = list(professor_field_distribution(professor, user=request.user))
    distribution = {
        "labels": [row["field_links__research_field__display_name"] for row in field_rows],
        "values": [row["work_count"] for row in field_rows],
        "slugs": [row["field_links__research_field__slug"] for row in field_rows],
    }
    return render(
        request,
        "public_site/professor_detail.html",
        {
            "professor": professor,
            "contact_email": professor.contact_email_for(request.user),
            "works": works.order_by("-year", "title"),
            "distribution": distribution,
            "selected_field": field_filter,
        },
    )


@require_GET
def research_work_detail(request, work_id):
    work = get_object_or_404(visible_research_works(request.user), pk=work_id)
    scopes = visible_scopes_for(request.user)
    advisors = WorkAdvisor.objects.filter(
        research_work=work,
        professor__status="active",
        professor__visibility_scope__in=scopes,
    ).select_related("professor")
    fields = WorkField.objects.filter(
        research_work=work,
        status="approved",
        research_field__status="active",
        research_field__visibility_scope__in=scopes,
    ).select_related("research_field")
    methods = WorkMethod.objects.filter(
        research_work=work,
        status="approved",
        research_method__status="active",
        research_method__visibility_scope__in=scopes,
    ).select_related("research_method")
    documents = SourceDocument.objects.visible_to(request.user).filter(research_work=work).only(
        "id", "research_work_id", "visibility_scope", "extraction_status", "uploaded_at"
    )
    return render(
        request,
        "public_site/research_work_detail.html",
        {
            "work": work,
            "advisors": advisors,
            "fields": fields,
            "methods": methods,
            "documents": documents,
        },
    )


@require_GET
def search(request):
    query = request.GET.get("q", "").strip()
    year = request.GET.get("year", "").strip()
    work_type = request.GET.get("work_type", "").strip()
    field = request.GET.get("field", "").strip()
    method = request.GET.get("method", "").strip()

    queryset = visible_research_works(request.user)

    if year.isdigit():
        queryset = queryset.filter(year=int(year))
    if work_type:
        queryset = queryset.filter(work_type=work_type)
    if field:
        visible_field_ids = ResearchField.objects.discoverable_to(request.user).filter(
            slug=field
        ).values("id")
        queryset = queryset.filter(
            field_links__research_field_id__in=visible_field_ids,
            field_links__status="approved",
        )
    if method:
        visible_method_ids = ResearchMethod.objects.discoverable_to(request.user).filter(
            slug=method
        ).values("id")
        queryset = queryset.filter(
            method_links__research_method_id__in=visible_method_ids,
            method_links__status="approved",
        )

    results = []

    if query:
        work_scores = {}
        scopes = visible_scopes_for(request.user)
        base_queryset_ids = set(queryset.values_list("id", flat=True))

        def get_work_entry(work):
            if work.id not in work_scores:
                work_scores[work.id] = {
                    "work": work,
                    "score": 0.0,
                    "excerpt": "",
                    "page_start": 1,
                    "match_reasons": []
                }
            return work_scores[work.id]

        # 1. Semantic vector search
        try:
            from rag.services.retrieval import PermissionAwareRetrievalService
            service = PermissionAwareRetrievalService()
            filters = {}
            if year.isdigit():
                filters["year"] = int(year)
            
            retrieval_res = service.retrieve_semantic(
                query=query,
                user=request.user,
                limit=100,
                filters=filters,
            )
            for citation in retrieval_res.citations:
                work = citation.document_chunk.source_document.research_work
                if work.id in base_queryset_ids:
                    entry = get_work_entry(work)
                    entry["score"] += float(citation.score) * 0.5
                    if not entry["excerpt"]:
                        entry["excerpt"] = citation.excerpt
                        entry["page_start"] = citation.page_start
                    entry["match_reasons"].append("語意相近")
        except Exception:
            pass

        # 2. Database Keyword & Taxonomy search
        needle = normalize_taxonomy_label(query)
        visible_professor_ids = visible_professors(request.user).values("id")
        matching_field_ids = [
            item.id
            for item in ResearchField.objects.discoverable_to(request.user)
            if any(
                needle in normalize_taxonomy_label(candidate)
                for candidate in [item.display_name, item.slug, *item.aliases]
            )
        ]
        matching_method_ids = [
            item.id
            for item in ResearchMethod.objects.discoverable_to(request.user)
            if any(
                needle in normalize_taxonomy_label(candidate)
                for candidate in [item.display_name, item.slug, *item.aliases]
            )
        ]

        keyword_matches = queryset.filter(
            Q(title__icontains=query)
            | Q(abstract__icontains=query)
            | Q(
                advisor_links__professor_id__in=visible_professor_ids,
                advisor_links__professor__display_name__icontains=query,
            )
            | Q(
                field_links__research_field_id__in=matching_field_ids,
                field_links__status="approved",
            )
            | Q(
                method_links__research_method_id__in=matching_method_ids,
                method_links__status="approved",
            )
        ).distinct()

        for work in keyword_matches:
            if work.id in base_queryset_ids:
                entry = get_work_entry(work)
                kw_score = 0.0
                if query.lower() in work.title.lower():
                    kw_score += 0.2
                if work.abstract and query.lower() in work.abstract.lower():
                    kw_score += 0.1
                entry["score"] += min(0.3, kw_score or 0.1)
                entry["match_reasons"].append("關鍵字匹配")

        # 3. Knowledge Graph matching
        try:
            from knowledge_graph.models import KnowledgeNode, KnowledgeEdge
            from knowledge_graph.registry import NodeType
            
            kg_nodes = KnowledgeNode.objects.filter(
                label__icontains=query,
                status="active",
                visibility_scope__in=scopes
            )
            for node in kg_nodes:
                if node.node_type == NodeType.RESEARCH_WORK:
                    work_id = node.object_id
                    if work_id in base_queryset_ids:
                        work = next((w for w in keyword_matches if w.id == work_id), None)
                        if not work:
                            try:
                                work = ResearchWork.objects.get(pk=work_id)
                            except ResearchWork.DoesNotExist:
                                continue
                        entry = get_work_entry(work)
                        entry["score"] += 0.2
                        entry["match_reasons"].append("知識圖譜直接關聯")
                else:
                    edges = KnowledgeEdge.objects.filter(
                        Q(source=node) | Q(target=node),
                        status="approved"
                    ).select_related("source", "target")
                    for edge in edges:
                        other_node = edge.target if edge.source_id == node.id else edge.source
                        if other_node.node_type == NodeType.RESEARCH_WORK:
                            work_id = other_node.object_id
                            if work_id in base_queryset_ids:
                                work = next((w for w in keyword_matches if w.id == work_id), None)
                                if not work:
                                    try:
                                        work = ResearchWork.objects.get(pk=work_id)
                                    except ResearchWork.DoesNotExist:
                                        continue
                                entry = get_work_entry(work)
                                entry["score"] += 0.1
                                entry["match_reasons"].append(f"圖譜關聯 ({edge.edge_type})")
        except Exception:
            pass

        scored_list = list(work_scores.values())
        scored_list.sort(key=lambda x: (-x["score"], -x["work"].year if x["work"].year else 0, x["work"].title))
        results = scored_list
    else:
        ordered_queryset = queryset.distinct().order_by("-year", "title")
        results = [{"work": w, "score": 0.0, "excerpt": "", "page_start": 1, "match_reasons": []} for w in ordered_queryset]

    paginator = Paginator(results, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "public_site/search.html",
        {
            "page_obj": page_obj,
            "query": query,
            "selected_year": year,
            "selected_work_type": work_type,
            "selected_field": field,
            "selected_method": method,
            "work_types": ResearchWork.WorkType.choices,
            "fields": ResearchField.objects.discoverable_to(request.user).order_by("display_name"),
            "methods": ResearchMethod.objects.discoverable_to(request.user).order_by("display_name"),
        },
    )


@require_GET
def download_document(request, document_id):
    document = get_object_or_404(
        SourceDocument.objects.select_related("research_work"), pk=document_id
    )
    if not can_download_document(request.user, document):
        # Do not disclose whether a restricted resource exists.
        raise Http404
    if not document.file or not document.file.storage.exists(document.file.name):
        raise Http404
    response = FileResponse(document.file.open("rb"), content_type="application/pdf")
    safe_name = f"research-{document.research_work_id}.pdf"
    response["Content-Disposition"] = content_disposition_header(False, safe_name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


def not_found(request, exception):
    return render(request, "public_site/404.html", status=404)


def server_error(request):
    return render(request, "public_site/500.html", status=500)
