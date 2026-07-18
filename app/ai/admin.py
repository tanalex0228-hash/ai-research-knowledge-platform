from django.contrib import admin

from .models import AIRequestLog, AssistantMessage, AssistantSession, PromptVersion


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    list_display = ("key", "version", "schema_version", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("key", "template", "schema_version")
    raw_id_fields = ("created_by",)
    readonly_fields = ("id", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        if obj and obj.request_logs.exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(AIRequestLog)
class AIRequestLogAdmin(admin.ModelAdmin):
    list_display = (
        "request_id",
        "purpose",
        "requested_by",
        "provider",
        "model_name",
        "status",
        "created_at",
    )
    list_filter = ("purpose", "status", "provider", "created_at")
    search_fields = ("request_id", "error_code", "model_name")
    raw_id_fields = ("prompt_version", "retrieval_log", "requested_by")
    readonly_fields = tuple(field.name for field in AIRequestLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssistantSession)
class AssistantSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "django_session_key", "status", "started_at", "ended_at")
    list_filter = ("status", "started_at")
    search_fields = ("id", "user__username", "django_session_key")
    raw_id_fields = ("user",)
    readonly_fields = tuple(field.name for field in AssistantSession._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_staff)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssistantMessage)
class AssistantMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("id", "session__id", "content")
    raw_id_fields = ("session",)
    readonly_fields = tuple(field.name for field in AssistantMessage._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
