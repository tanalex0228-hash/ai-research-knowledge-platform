from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import router, transaction

from accounts.permissions import (
    is_platform_admin,
    is_platform_teacher,
    visible_scopes_for,
)
from accounts.services import record_audit_event
from public_site.request_ids import request_id_for
from research.models import ResearchWork

from .choices import VisibilityScope
from .forms import DocumentChunkAdminForm, SourceDocumentUploadForm
from .models import DocumentChunk, SourceDocument


def _advised_works_for(user):
    if not is_platform_teacher(user):
        return ResearchWork.objects.none()
    return (
        ResearchWork.objects.filter(
            advisor_links__professor__user=user,
            advisor_links__professor__status="active",
            advisor_links__professor__visibility_scope__in=visible_scopes_for(user),
            visibility_scope__in=visible_scopes_for(user),
        )
        .exclude(status__in=("archived", "rejected"))
        .distinct()
    )


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
        "stored_file_reference",
        "original_filename",
        "mime_type",
        "file_size",
        "checksum",
        "extraction_status",
        "extraction_error",
        "uploaded_at",
        "updated_at",
    )

    @admin.display(description="Stored source file")
    def stored_file_reference(self, obj):
        if obj and obj.file:
            from django.urls import reverse
            from django.utils.html import format_html
            url = reverse("public_site:document-download", args=[obj.id])
            return format_html('<a href="{}" target="_blank">📥 下載/檢視 PDF 檔案</a><br><span class="help">Stored privately and immutable; create a new source document to replace it.</span>', url)
        return "Stored privately and immutable; create a new source document to replace it."

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None:
            return fieldsets
        return tuple(
            (
                title,
                {
                    **options,
                    "fields": tuple(
                        "stored_file_reference" if field == "file" else field
                        for field in options["fields"]
                    ),
                },
            )
            for title, options in fieldsets
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

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or is_platform_teacher(request.user)

    def has_view_permission(self, request, obj=None):
        if is_platform_admin(request.user):
            return True
        if not is_platform_teacher(request.user):
            return False
        return obj is None or self._teacher_advises_document(request.user, obj)

    def has_add_permission(self, request):
        if is_platform_admin(request.user):
            return True
        return _advised_works_for(request.user).exists()

    def has_change_permission(self, request, obj=None):
        if is_platform_admin(request.user):
            return True
        return False

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    @staticmethod
    def _teacher_advises_document(user, obj) -> bool:
        return (
            obj.visibility_scope in visible_scopes_for(user)
            and _advised_works_for(user).filter(pk=obj.research_work_id).exists()
        )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_platform_admin(request.user):
            return queryset
        if is_platform_teacher(request.user):
            return queryset.filter(
                research_work__advisor_links__professor__user=request.user,
                research_work__advisor_links__professor__status="active",
                research_work__advisor_links__professor__visibility_scope__in=visible_scopes_for(
                    request.user
                ),
                research_work__visibility_scope__in=visible_scopes_for(request.user),
                visibility_scope__in=visible_scopes_for(request.user),
            ).distinct()
        return queryset.none()



    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "research_work" and not is_platform_admin(request.user):
            kwargs["queryset"] = _advised_works_for(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if (
            db_field.name == "visibility_scope"
            and is_platform_teacher(request.user)
            and not is_platform_admin(request.user)
        ):
            kwargs["choices"] = (
                (VisibilityScope.TEACHER, VisibilityScope.TEACHER.label),
            )
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj)) + ("uploaded_by",)
        if obj is not None:
            fields += ("research_work", "stored_file_reference", "visibility_scope")
        return tuple(dict.fromkeys(fields))

    def save_model(self, request, obj, form, change):
        if is_platform_teacher(request.user) and not is_platform_admin(request.user):
            if not self._teacher_advises_document(request.user, obj):
                raise PermissionDenied(
                    "Teachers may manage documents only for research works they advise."
                )
            if not change:
                obj.uploaded_by = request.user
            obj.visibility_scope = VisibilityScope.TEACHER
        elif not change:
            obj.uploaded_by = request.user

        file_changed = not change or bool(
            form is not None and "file" in getattr(form, "changed_data", ())
        )
        old_file_name = ""
        if change and obj.pk:
            old_file_name = (
                SourceDocument.objects.filter(pk=obj.pk)
                .values_list("file", flat=True)
                .first()
                or ""
            )

        database = router.db_for_write(SourceDocument, instance=obj)
        try:
            with transaction.atomic(using=database):
                super().save_model(request, obj, form, change)
                if file_changed:
                    record_audit_event(
                        event_type="source_document.uploaded",
                        actor=request.user,
                        target_type="documents.SourceDocument",
                        target_id=obj.id,
                        request_id=request_id_for(request),
                        metadata={
                            "research_work_id": str(obj.research_work_id),
                            "visibility_scope": obj.visibility_scope,
                            "source": "django_admin",
                        },
                        using=database,
                    )
            if file_changed:
                from .services import queue_document_extraction
                queue_document_extraction(obj)
        except Exception:
            # Storage is not transactional. Remove only a newly committed blob;
            # never delete the previous canonical file when an edit rolls back.
            new_file_name = getattr(obj.file, "name", "")
            if (
                file_changed
                and new_file_name
                and new_file_name != old_file_name
                and getattr(obj.file, "_committed", False)
            ):
                obj.file.storage.delete(new_file_name)
            raise


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

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or is_platform_teacher(request.user)

    def has_view_permission(self, request, obj=None):
        if is_platform_admin(request.user):
            return True
        if not is_platform_teacher(request.user):
            return False
        return obj is None or (
            obj.visibility_scope in visible_scopes_for(request.user)
            and obj.source_document.visibility_scope in visible_scopes_for(request.user)
            and _advised_works_for(request.user)
            .filter(pk=obj.source_document.research_work_id)
            .exists()
        )

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_platform_admin(request.user):
            return queryset
        if is_platform_teacher(request.user):
            return queryset.filter(
                source_document__research_work__advisor_links__professor__user=request.user,
                source_document__research_work__advisor_links__professor__status="active",
                source_document__research_work__advisor_links__professor__visibility_scope__in=visible_scopes_for(
                    request.user
                ),
                source_document__research_work__visibility_scope__in=visible_scopes_for(
                    request.user
                ),
                source_document__visibility_scope__in=visible_scopes_for(request.user),
                visibility_scope__in=visible_scopes_for(request.user),
            ).distinct()
        return queryset.none()


class SourceDocumentInline(admin.TabularInline):
    model = SourceDocument
    extra = 0
    fields = ("id", "stored_file_link", "visibility_scope", "extraction_status", "uploaded_at")
    readonly_fields = ("id", "stored_file_link", "extraction_status", "uploaded_at")

    def stored_file_link(self, obj):
        if obj and obj.file:
            from django.urls import reverse
            from django.utils.html import format_html
            url = reverse("public_site:document-download", args=[obj.id])
            return format_html('<a href="{}" target="_blank">📥 下載 PDF</a>', url)
        return "No file"
    stored_file_link.short_description = "檔案連結"

    def has_add_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)

