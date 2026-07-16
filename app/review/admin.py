from django.contrib import admin

from .models import (
    AIExtractionEvidence,
    AIExtractionJob,
    AIExtractionResult,
    ReviewDecision,
    ReviewItem,
)


class ExtractionEvidenceInline(admin.TabularInline):
    model = AIExtractionEvidence
    extra = 0
    raw_id_fields = ("document_chunk",)
    readonly_fields = ("id", "created_at")


@admin.register(AIExtractionJob)
class AIExtractionJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source_document",
        "schema_version",
        "visibility_scope",
        "status",
        "created_at",
    )
    list_filter = ("status", "visibility_scope", "schema_version", "created_at")
    search_fields = ("id", "source_document__id", "error_code")
    raw_id_fields = ("source_document", "prompt_version", "ai_request", "requested_by")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(AIExtractionResult)
class AIExtractionResultAdmin(admin.ModelAdmin):
    inlines = (ExtractionEvidenceInline,)
    list_display = (
        "id",
        "job",
        "result_type",
        "confidence",
        "status",
        "visibility_scope",
    )
    list_filter = ("result_type", "status", "visibility_scope")
    search_fields = ("id", "job__id", "candidate_data")
    raw_id_fields = ("job", "primary_evidence_chunk")
    readonly_fields = ("id", "created_at")


@admin.register(ReviewItem)
class ReviewItemAdmin(admin.ModelAdmin):
    list_display = (
        "field_path",
        "target_type",
        "state",
        "assigned_to",
        "visibility_scope",
        "created_at",
    )
    list_filter = ("state", "target_type", "visibility_scope", "created_at")
    search_fields = ("id", "field_path", "target_id")
    raw_id_fields = ("extraction_result", "assigned_to")
    readonly_fields = ("id", "created_at", "updated_at", "resolved_at")


@admin.register(ReviewDecision)
class ReviewDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "review_item",
        "action",
        "reviewer",
        "previous_state",
        "resulting_state",
        "created_at",
    )
    list_filter = ("action", "resulting_state", "created_at")
    search_fields = ("review_item__id", "reviewer__username", "reason")
    raw_id_fields = ("review_item", "reviewer")
    readonly_fields = tuple(field.name for field in ReviewDecision._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

