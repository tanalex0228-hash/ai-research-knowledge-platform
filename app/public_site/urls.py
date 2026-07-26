from django.urls import path

from . import views

app_name = "public_site"

urlpatterns = [
    path("", views.home, name="home"),
    path("professors/", views.professor_list, name="professor-list"),
    path("professors/<uuid:professor_id>/", views.professor_detail, name="professor-detail"),
    path("fields/<slug:slug>/", views.field_detail, name="field-detail"),
    path("methods/<slug:slug>/", views.method_detail, name="method-detail"),
    path("research-works/<uuid:work_id>/", views.research_work_detail, name="work-detail"),
    path("search/", views.search, name="search"),
    path("documents/<uuid:document_id>/download/", views.download_document, name="document-download"),
]
