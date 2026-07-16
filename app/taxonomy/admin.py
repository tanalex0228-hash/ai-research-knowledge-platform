from django.contrib import admin

from .models import AITag, ResearchField, ResearchMethod


class TaxonomyAdmin(admin.ModelAdmin):
    list_display = ("display_name", "slug", "parent", "status", "visibility_scope")
    list_filter = ("status", "visibility_scope")
    search_fields = ("display_name", "slug")
    autocomplete_fields = ("parent",)
    readonly_fields = ("id", "created_at", "updated_at")


admin.site.register(ResearchField, TaxonomyAdmin)
admin.site.register(ResearchMethod, TaxonomyAdmin)
admin.site.register(AITag, TaxonomyAdmin)

