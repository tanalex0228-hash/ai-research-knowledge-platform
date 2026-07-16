from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import AuditLog, Role, User, UserRole


class UserRoleInline(admin.TabularInline):
    model = UserRole
    fk_name = "user"
    extra = 0
    autocomplete_fields = ("role", "assigned_by")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Platform identity", {"fields": ("id",)}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Contact", {"fields": ("email",)}),
    )
    inlines = (UserRoleInline,)
    list_display = ("username", "email", "is_active", "is_staff", "last_login")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email", "first_name", "last_name")
    readonly_fields = ("id", "last_login", "date_joined")


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
