from django.contrib import admin

from .forms import DocumentChunkAdminForm, SourceDocumentUploadForm
from .models import DocumentChunk, SourceDocument


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    form = SourceDocumentUploadForm
    list_display = (
        "id",
        "research_work",
        "original_filename",
        "visibility_scope",
        "extraction_status",
        "uploaded_at",
    )
    list_filter = ("visibility_scope", "extraction_status", "uploaded_at")
    search_fields = (
        "id",
        "research_work__title",
        "original_filename",
        "checksum",
    )
    raw_id_fields = ("research_work", "uploaded_by")
    readonly_fields = (
        "id",
        "original_filename",
        "mime_type",
        "file_size",
        "checksum",
        "extraction_status",
        "extraction_error",
        "uploaded_at",
        "updated_at",
    )
    fieldsets = (
        (
            None,
            {"fields": ("id", "research_work", "file", "visibility_scope")},
        ),
        (
            "Private upload metadata (administrators only)",
            {
                "fields": (
                    "original_filename",
                    "mime_type",
                    "file_size",
                    "checksum",
                    "uploaded_by",
                    "uploaded_at",
                    "updated_at",
                )
            },
        ),
        (
            "Extraction",
            {"fields": ("extraction_status", "extraction_error")},
        ),
    )

    def save_model(self, request, obj, form, change):
        if not change and not obj.uploaded_by_id:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    form = DocumentChunkAdminForm
    list_display = (
        "id",
        "source_document",
        "chunk_index",
        "page_start",
        "page_end",
        "visibility_scope",
        "token_count",
    )
    list_filter = ("visibility_scope", "chunk_strategy_version")
    search_fields = ("id", "source_document__id", "text")
    raw_id_fields = ("source_document",)
    readonly_fields = ("id", "created_at", "updated_at")
