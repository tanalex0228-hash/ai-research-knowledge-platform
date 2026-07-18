from django.contrib import admin
from django.contrib import messages

from accounts.permissions import is_platform_admin
from research.hard_delete import hard_delete_professor

from .models import Professor, ProfessorAlias
from .services import linked_professor_for_teacher


def _can_access_professor(user, professor=None) -> bool:
    if is_platform_admin(user):
        return True
    linked = linked_professor_for_teacher(user)
    if linked is None:
        return False
    return professor is None or professor.pk == linked.pk


class ProfessorAliasInline(admin.TabularInline):
    model = ProfessorAlias
    extra = 0
    readonly_fields = ("normalized_alias", "created_at")

    def has_view_permission(self, request, obj=None):
        return _can_access_professor(request.user, obj)

    def has_change_permission(self, request, obj=None):
        return _can_access_professor(request.user, obj)

    def has_add_permission(self, request, obj=None):
        return _can_access_professor(request.user, obj)

    def has_delete_permission(self, request, obj=None):
        return _can_access_professor(request.user, obj)


@admin.register(Professor)
class ProfessorAdmin(admin.ModelAdmin):
    inlines = (ProfessorAliasInline,)
    list_display = (
        "display_name",
        "title",
        "status",
        "visibility_scope",
        "email_is_public",
    )
    list_filter = ("status", "visibility_scope", "email_is_public")
    search_fields = ("display_name", "normalized_name", "email", "aliases__alias")
    autocomplete_fields = ("user",)
    readonly_fields = ("id", "normalized_name", "created_at", "updated_at")
    actions = ("hard_delete_selected_professors",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_platform_admin(request.user):
            return queryset
        linked = linked_professor_for_teacher(request.user)
        return queryset.filter(pk=linked.pk) if linked is not None else queryset.none()

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or linked_professor_for_teacher(
            request.user
        ) is not None

    def has_view_permission(self, request, obj=None):
        return _can_access_professor(request.user, obj)

    def has_change_permission(self, request, obj=None):
        return _can_access_professor(request.user, obj)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def _can_hard_delete(self, request) -> bool:
        return is_platform_admin(request.user) and request.user.has_perm(
            "professors.hard_delete_professor"
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self._can_hard_delete(request):
            actions.pop("hard_delete_selected_professors", None)
        return actions

    def delete_model(self, request, obj):
        if self._can_hard_delete(request):
            hard_delete_professor(obj)
            return
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        if not self._can_hard_delete(request):
            return super().delete_queryset(request, queryset)
        deleted = 0
        for professor in queryset:
            hard_delete_professor(professor)
            deleted += 1
        self.message_user(
            request,
            f"已直接刪除 {deleted} 筆教師資料。",
            level=messages.WARNING,
        )

    @admin.action(description="直接刪除選取的教師與受保護關聯")
    def hard_delete_selected_professors(self, request, queryset):
        self.delete_queryset(request, queryset)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not is_platform_admin(request.user):
            fields.extend(("user", "status", "visibility_scope"))
        return tuple(dict.fromkeys(fields))


@admin.register(ProfessorAlias)
class ProfessorAliasAdmin(admin.ModelAdmin):
    list_display = ("alias", "professor", "created_at")
    search_fields = ("alias", "normalized_alias", "professor__display_name")
    autocomplete_fields = ("professor",)
    readonly_fields = ("id", "normalized_alias", "created_at")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_platform_admin(request.user):
            return queryset
        linked = linked_professor_for_teacher(request.user)
        return (
            queryset.filter(professor=linked)
            if linked is not None
            else queryset.none()
        )

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or linked_professor_for_teacher(
            request.user
        ) is not None

    def has_view_permission(self, request, obj=None):
        return _can_access_professor(
            request.user, obj.professor if obj is not None else None
        )

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not is_platform_admin(request.user):
            fields.append("professor")
        return tuple(dict.fromkeys(fields))
