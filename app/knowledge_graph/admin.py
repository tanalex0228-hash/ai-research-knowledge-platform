from django.contrib import admin

from .forms import KnowledgeEdgeAdminForm, KnowledgeNodeAdminForm
from .models import KnowledgeEdge, KnowledgeNode


@admin.register(KnowledgeNode)
class KnowledgeNodeAdmin(admin.ModelAdmin):
    form = KnowledgeNodeAdminForm
    list_display = (
        "label",
        "node_type",
        "object_id",
        "visibility_scope",
        "status",
    )
    list_filter = ("node_type", "visibility_scope", "status")
    search_fields = ("label", "object_id")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(KnowledgeEdge)
class KnowledgeEdgeAdmin(admin.ModelAdmin):
    form = KnowledgeEdgeAdminForm
    list_display = (
        "source",
        "edge_type",
        "target",
        "confidence",
        "status",
        "evidence_source_type",
    )
    list_filter = ("edge_type", "status", "evidence_source_type")
    search_fields = ("source__label", "target__label", "evidence_note")
    raw_id_fields = ("source", "target", "created_by")
    readonly_fields = ("id", "created_at", "updated_at")

