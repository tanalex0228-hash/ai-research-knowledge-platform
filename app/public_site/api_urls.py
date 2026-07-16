from django.urls import path

from . import api_views

app_name = "api-v1"

urlpatterns = [
    path("research-works", api_views.research_work_list, name="research-work-list"),
    path("research-works/<uuid:work_id>", api_views.research_work_detail, name="research-work-detail"),
    path(
        "research-works/<uuid:work_id>/documents",
        api_views.research_work_document_upload,
        name="research-work-document-upload",
    ),
    path("professors", api_views.professor_list, name="professor-list"),
    path("professors/<uuid:professor_id>", api_views.professor_detail, name="professor-detail"),
    path("search/semantic", api_views.semantic_search, name="semantic-search"),
    path("ai/teacher-matching", api_views.teacher_matching, name="teacher-matching"),
]

