from django.contrib import admin
from django.contrib import messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.contrib.admin import helpers
from django import forms
from django.db import transaction
from django.template.response import TemplateResponse

from accounts.permissions import is_platform_admin
from accounts.services import record_audit_event
from public_site.request_ids import request_id_for
from research.hard_delete import hard_delete_user

from .models import AuditLog, Role, User, UserRole


class UserRoleInline(admin.TabularInline):
    model = UserRole
    fk_name = "user"
    extra = 0
    autocomplete_fields = ("role", "assigned_by")


class GrantGroupsForm(forms.Form):
    groups = forms.ModelMultipleChoiceField(
        label="要授予的權限群組",
        queryset=Group.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text="列出所有目前建立的 Django 權限群組；未來新增的群組會自動出現在此清單。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = Group.objects.order_by("name")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("平台身份", {"fields": ("id",)}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("聯絡資訊", {"fields": ("email",)}),
    )
    inlines = (UserRoleInline,)
    list_display = ("username", "email", "is_active", "is_staff", "last_login")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email", "first_name", "last_name")
    readonly_fields = ("id", "last_login", "date_joined")
    actions = ("grant_selected_groups", "hard_delete_selected_users")

    def _can_hard_delete(self, request) -> bool:
        return is_platform_admin(request.user) and request.user.has_perm(
            "accounts.hard_delete_user"
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not is_platform_admin(request.user):
            actions.pop("grant_selected_groups", None)
        if not self._can_hard_delete(request):
            actions.pop("hard_delete_selected_users", None)
        return actions

    @admin.action(description="授予權限群組")
    def grant_selected_groups(self, request, queryset):
        """Grant any current Django auth Group through a confirmation form.

        This intentionally reads groups at request time: administrators do not
        need a code change when a new permission group is created.
        """

        if not is_platform_admin(request.user):
            self.message_user(request, "您沒有授予權限群組的權限。", level=messages.ERROR)
            return None

        if request.POST.get("apply"):
            form = GrantGroupsForm(request.POST)
            if form.is_valid():
                groups = list(form.cleaned_data["groups"])
                created_assignments = 0
                with transaction.atomic():
                    users = list(queryset.select_for_update())
                    existing_assignments = set(
                        User.groups.through.objects.filter(
                            user_id__in=[user.pk for user in users],
                            group_id__in=[group.pk for group in groups],
                        ).values_list("user_id", "group_id")
                    )
                    for user in users:
                        user.groups.add(*groups)
                    created_assignments = len(users) * len(groups) - len(existing_assignments)
                    record_audit_event(
                        event_type="accounts.user_groups.granted_bulk",
                        actor=request.user,
                        request_id=request_id_for(request),
                        metadata={
                            "user_count": len(users),
                            "group_names": [group.name for group in groups],
                            "created_assignments": created_assignments,
                        },
                    )
                self.message_user(
                    request,
                    f"已處理 {len(users)} 位使用者，新增 {created_assignments} 筆群組指派。",
                    level=messages.SUCCESS,
                )
                return None
        else:
            form = GrantGroupsForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "授予權限群組",
            "opts": self.model._meta,
            "users": queryset,
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "form": form,
        }
        return TemplateResponse(request, "admin/accounts/user/grant_groups.html", context)

    def delete_model(self, request, obj):
        if self._can_hard_delete(request):
            hard_delete_user(obj)
            return
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        if not self._can_hard_delete(request):
            return super().delete_queryset(request, queryset)
        deleted = 0
        for user in queryset:
            hard_delete_user(user)
            deleted += 1
        self.message_user(
            request,
            f"已直接刪除 {deleted} 個使用者帳號。",
            level=messages.WARNING,
        )

    @admin.action(description="直接刪除選取的使用者與受保護關聯")
    def hard_delete_selected_users(self, request, queryset):
        self.delete_queryset(request, queryset)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("display_name", "slug", "is_active", "is_system")
    list_filter = ("is_active", "is_system")
    search_fields = ("display_name", "slug")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "assigned_by", "assigned_at")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email", "role__slug")
    autocomplete_fields = ("user", "role", "assigned_by")
    readonly_fields = ("id", "assigned_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "target_type", "target_id", "actor")
    list_filter = ("event_type", "target_type", "created_at")
    search_fields = ("event_type", "target_type", "target_id", "request_id")
    readonly_fields = (
        "id",
        "actor",
        "event_type",
        "target_type",
        "target_id",
        "request_id",
        "metadata",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
