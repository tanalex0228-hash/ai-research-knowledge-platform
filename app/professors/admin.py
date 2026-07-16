from django.contrib import admin

from .models import Professor, ProfessorAlias


class ProfessorAliasInline(admin.TabularInline):
    model = ProfessorAlias
    extra = 0
    readonly_fields = ("normalized_alias", "created_at")


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


@admin.register(ProfessorAlias)
class ProfessorAliasAdmin(admin.ModelAdmin):
    list_display = ("alias", "professor", "created_at")
    search_fields = ("alias", "normalized_alias", "professor__display_name")
    autocomplete_fields = ("professor",)
    readonly_fields = ("id", "normalized_alias", "created_at")

