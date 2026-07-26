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
    path("ingestion-jobs", api_views.ingestion_job_create, name="ingestion-job-create"),
    path("ingestion-jobs/<uuid:job_id>", api_views.ingestion_job_detail, name="ingestion-job-detail"),
    path("professors", api_views.professor_list, name="professor-list"),
    path("professors/<uuid:professor_id>", api_views.professor_detail, name="professor-detail"),
    path("fields/<uuid:field_id>", api_views.field_detail, name="field-detail"),
    path("methods/<uuid:method_id>", api_views.method_detail, name="method-detail"),
    path("search/semantic", api_views.semantic_search, name="semantic-search"),
    path("ai/teacher-matching", api_views.teacher_matching, name="teacher-matching"),
    path(
        "ai/research-navigation/sessions",
        api_views.research_navigation_session_create,
        name="research-navigation-session-create",
    ),
    path(
        "ai/research-navigation/sessions/<uuid:session_id>/messages",
        api_views.research_navigation_message,
        name="research-navigation-message",
    ),
    path("ai/assistant/session", api_views.assistant_session, name="assistant-session"),
    path("ai/assistant/messages", api_views.assistant_message, name="assistant-message"),
    path("ai/assistant/end", api_views.assistant_end, name="assistant-end"),
    path("graph/nodes/<uuid:node_id>/neighbors", api_views.graph_node_neighbors, name="graph-node-neighbors"),
]
