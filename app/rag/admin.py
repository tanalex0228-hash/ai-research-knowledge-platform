from django.contrib import admin

from .models import (
    CitationSource,
    EmbeddingRecord,
    RetrievalLog,
    VectorChunk,
    VectorDocument,
)


class VectorChunkInline(admin.TabularInline):
    model = VectorChunk
    extra = 0
    raw_id_fields = ("source_chunk",)
    fields = ("source_chunk", "status", "visibility_scope", "content_checksum")


@admin.register(VectorDocument)
class VectorDocumentAdmin(admin.ModelAdmin):
    inlines = (VectorChunkInline,)
    list_display = ("source_document", "status", "visibility_scope", "indexed_at")
    list_filter = ("status", "visibility_scope")
    search_fields = ("id", "source_document__id")
    raw_id_fields = ("source_document",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(VectorChunk)
class VectorChunkAdmin(admin.ModelAdmin):
    list_display = ("source_chunk", "vector_document", "status", "visibility_scope")
    list_filter = ("status", "visibility_scope")
    search_fields = ("id", "source_chunk__id", "content_checksum")
    raw_id_fields = ("vector_document", "source_chunk")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(EmbeddingRecord)
class EmbeddingRecordAdmin(admin.ModelAdmin):
    list_display = (
        "vector_chunk",
        "provider",
        "model_name",
        "dimensions",
        "is_active",
        "created_at",
    )
    list_filter = ("provider", "model_name", "is_active")
    search_fields = ("id", "vector_chunk__id", "input_checksum")
    raw_id_fields = ("vector_chunk",)
    readonly_fields = (
        "id",
        "vector_chunk",
        "provider",
        "model_name",
        "dimensions",
        "embedding",
        "input_checksum",
        "is_active",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class RetrievalAuditAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RetrievalLog)
class RetrievalLogAdmin(RetrievalAuditAdmin):
    list_display = (
        "request_id",
        "retrieval_kind",
        "requested_by",
        "status",
        "result_count",
        "created_at",
    )
    list_filter = ("retrieval_kind", "status", "created_at")
    search_fields = ("request_id", "query_text", "failure_code")
    raw_id_fields = ("requested_by",)
    readonly_fields = tuple(field.name for field in RetrievalLog._meta.fields)


@admin.register(CitationSource)
class CitationSourceAdmin(RetrievalAuditAdmin):
    list_display = (
        "retrieval_log",
        "rank",
        "document_chunk",
        "score",
        "visibility_scope",
    )
    list_filter = ("visibility_scope", "created_at")
    search_fields = ("retrieval_log__request_id", "document_chunk__id", "excerpt")
    raw_id_fields = ("retrieval_log", "document_chunk")
    readonly_fields = tuple(field.name for field in CitationSource._meta.fields)

