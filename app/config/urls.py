from django.contrib import admin
from django.urls import include, path

from public_site import api_views, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", views.healthz, name="healthz"),
    path("api/healthz", views.healthz, name="api-healthz"),
    path("", include("accounts.urls")),
    path("api/v1/", include("public_site.api_urls")),
    path("", include("public_site.urls")),
]

handler404 = "public_site.views.not_found"
handler500 = "public_site.views.server_error"
