from django.contrib import admin

from .models import (
    Award,
    FeaturedWork,
    ResearchWork,
    Student,
    WorkAdvisor,
    WorkAuthor,
    WorkField,
    WorkMethod,
)


class WorkAdvisorInline(admin.TabularInline):
    model = WorkAdvisor
    extra = 0
    autocomplete_fields = ("professor",)


class WorkAuthorInline(admin.TabularInline):
    model = WorkAuthor
    extra = 0
    autocomplete_fields = ("student",)


class WorkFieldInline(admin.TabularInline):
    model = WorkField
    extra = 0
    autocomplete_fields = ("research_field",)


class WorkMethodInline(admin.TabularInline):
    model = WorkMethod
    extra = 0
    autocomplete_fields = ("research_method",)


@admin.register(ResearchWork)
class ResearchWorkAdmin(admin.ModelAdmin):
    inlines = (WorkAdvisorInline, WorkAuthorInline, WorkFieldInline, WorkMethodInline)
    list_display = ("title", "year", "work_type", "status", "visibility_scope", "source_quality")
    list_filter = ("status", "visibility_scope", "work_type", "source_quality", "year")
    search_fields = ("title", "normalized_title", "abstract")
    autocomplete_fields = ("created_by", "updated_by")
    readonly_fields = ("id", "normalized_title", "created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("display_name", "is_name_public", "status", "visibility_scope")
    list_filter = ("is_name_public", "status", "visibility_scope")
    search_fields = ("display_name", "normalized_name", "public_display_name")
    readonly_fields = ("id", "normalized_name", "created_at", "updated_at")


@admin.register(FeaturedWork)
class FeaturedWorkAdmin(admin.ModelAdmin):
    list_display = ("research_work", "headline", "display_order", "is_active", "starts_at", "ends_at")
    list_filter = ("is_active",)
    search_fields = ("headline", "research_work__title")
    autocomplete_fields = ("research_work", "created_by")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Award)
class AwardAdmin(admin.ModelAdmin):
    list_display = ("name", "research_work", "award_year", "award_type", "status", "visibility_scope")
    list_filter = ("award_type", "status", "visibility_scope", "award_year")
    search_fields = ("name", "category", "organization", "research_work__title")
    autocomplete_fields = ("research_work", "advisor")
    readonly_fields = ("id", "created_at", "updated_at")


admin.site.register(WorkAdvisor)
admin.site.register(WorkAuthor)
admin.site.register(WorkField)
admin.site.register(WorkMethod)
