from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError

from accounts.permissions import is_platform_admin, visible_scopes_for
from professors.services import linked_professor_for_teacher
from public_site.request_ids import request_id_for

from .models import (
    Award,
    FeaturedWork,
    ResearchWork,
    ResearchWorkStatus,
    ResearchWorkTransition,
    Student,
    WorkAdvisor,
    WorkAuthor,
    WorkField,
    WorkMethod,
)
from .services import transition_research_work


def _teacher_manageable_works(user):
    linked = linked_professor_for_teacher(user)
    if linked is None:
        return ResearchWork.objects.none()
    return (
        ResearchWork.objects.filter(
            advisor_links__professor=linked,
            visibility_scope__in=visible_scopes_for(user),
        )
        .exclude(status__in=(ResearchWorkStatus.ARCHIVED, ResearchWorkStatus.REJECTED))
        .distinct()
    )


def _can_access_work(user, research_work=None) -> bool:
    if is_platform_admin(user):
        return True
    if research_work is None:
        return _teacher_manageable_works(user).exists()
    return _teacher_manageable_works(user).filter(pk=research_work.pk).exists()


class WorkAdvisorInline(admin.TabularInline):
    model = WorkAdvisor
    extra = 0
    autocomplete_fields = ("professor",)

    def has_view_permission(self, request, obj=None):
        return _can_access_work(request.user, obj)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)


class WorkAuthorInline(admin.TabularInline):
    model = WorkAuthor
    extra = 0
    autocomplete_fields = ("student",)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)


class WorkFieldInline(admin.TabularInline):
    model = WorkField
    extra = 0
    autocomplete_fields = ("research_field",)


class WorkMethodInline(admin.TabularInline):
    model = WorkMethod
    extra = 0
    autocomplete_fields = ("research_method",)


class ResearchWorkTransitionInline(admin.TabularInline):
    model = ResearchWorkTransition
    extra = 0
    can_delete = False
    fields = (
        "from_status",
        "to_status",
        "actor",
        "reason",
        "request_id",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _can_access_work(request.user, obj)


@admin.register(ResearchWork)
class ResearchWorkAdmin(admin.ModelAdmin):
    inlines = (
        WorkAdvisorInline,
        WorkAuthorInline,
        WorkFieldInline,
        WorkMethodInline,
        ResearchWorkTransitionInline,
    )
    list_display = ("title", "year", "work_type", "status", "visibility_scope", "source_quality")
    list_filter = ("status", "visibility_scope", "work_type", "source_quality", "year")
    search_fields = ("title", "normalized_title", "abstract")
    autocomplete_fields = ("created_by", "updated_by")
    readonly_fields = ("id", "normalized_title", "created_at", "updated_at")
    date_hierarchy = "created_at"
    actions = (
        "transition_to_uploaded",
        "transition_to_parsed",
        "transition_to_ai_extracted",
        "transition_to_under_review",
        "transition_to_approved",
        "transition_to_published",
        "transition_to_archived",
        "transition_to_rejected",
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_platform_admin(request.user):
            return queryset
        return queryset.filter(pk__in=_teacher_manageable_works(request.user))

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or linked_professor_for_teacher(
            request.user
        ) is not None

    def has_view_permission(self, request, obj=None):
        return _can_access_work(request.user, obj)

    def has_change_permission(self, request, obj=None):
        return _can_access_work(request.user, obj)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        # Admin-created works always begin at the model default (draft). Trusted
        # fixture/import code may still create historical records at a later state.
        fields.append("status")
        if not is_platform_admin(request.user):
            fields.extend(("visibility_scope", "created_by", "updated_by"))
        return tuple(dict.fromkeys(fields))

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not is_platform_admin(request.user):
            for action_name in self.actions:
                actions.pop(action_name, None)
        return actions

    def save_model(self, request, obj, form, change):
        if change:
            obj.updated_by = request.user
        elif obj.created_by_id is None:
            obj.created_by = request.user
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def _transition_selected(self, request, queryset, to_status):
        request_id = request_id_for(request)
        succeeded = 0
        failures: list[str] = []
        for work in queryset:
            try:
                transition_research_work(
                    research_work=work,
                    to_status=to_status,
                    actor=request.user,
                    reason=f"Django admin action: transition to {to_status}",
                    request_id=request_id,
                )
            except ValidationError as exc:
                failures.append(f"{work}: {'; '.join(exc.messages)}")
            else:
                succeeded += 1
        if succeeded:
            self.message_user(
                request,
                f"Transitioned {succeeded} research work(s) to {to_status}.",
                level=messages.SUCCESS,
            )
        if failures:
            self.message_user(
                request,
                "Skipped illegal transition(s): " + " | ".join(failures[:5]),
                level=messages.WARNING,
            )

    @admin.action(description="Lifecycle: mark uploaded")
    def transition_to_uploaded(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.UPLOADED)

    @admin.action(description="Lifecycle: mark parsed")
    def transition_to_parsed(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.PARSED)

    @admin.action(description="Lifecycle: mark AI extracted")
    def transition_to_ai_extracted(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.AI_EXTRACTED)

    @admin.action(description="Lifecycle: send under review")
    def transition_to_under_review(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.UNDER_REVIEW)

    @admin.action(description="Lifecycle: approve")
    def transition_to_approved(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.APPROVED)

    @admin.action(description="Lifecycle: publish")
    def transition_to_published(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.PUBLISHED)

    @admin.action(description="Lifecycle: archive")
    def transition_to_archived(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.ARCHIVED)

    @admin.action(description="Lifecycle: reject")
    def transition_to_rejected(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.REJECTED)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("display_name", "is_name_public", "status", "visibility_scope")
    list_filter = ("is_name_public", "status", "visibility_scope")
    search_fields = ("display_name", "normalized_name", "public_display_name")
    readonly_fields = ("id", "normalized_name", "created_at", "updated_at")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if is_platform_admin(request.user) else queryset.none()

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)


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


@admin.register(ResearchWorkTransition)
class ResearchWorkTransitionAdmin(admin.ModelAdmin):
    list_display = (
        "research_work",
        "from_status",
        "to_status",
        "actor",
        "request_id",
        "created_at",
    )
    list_filter = ("from_status", "to_status", "created_at")
    search_fields = ("research_work__title", "request_id", "reason")
    raw_id_fields = ("research_work", "actor")
    readonly_fields = tuple(field.name for field in ResearchWorkTransition._meta.fields)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_platform_admin(request.user):
            return queryset
        return queryset.filter(
            research_work__in=_teacher_manageable_works(request.user)
        ).distinct()

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or linked_professor_for_teacher(
            request.user
        ) is not None

    def has_view_permission(self, request, obj=None):
        return _can_access_work(
            request.user, obj.research_work if obj is not None else None
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkAdvisor)
class WorkAdvisorAdmin(admin.ModelAdmin):
    list_display = ("research_work", "professor", "role", "position", "created_at")
    list_filter = ("role",)
    search_fields = ("research_work__title", "professor__display_name")
    autocomplete_fields = ("research_work", "professor")
    readonly_fields = ("id", "created_at")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_platform_admin(request.user):
            return queryset
        linked = linked_professor_for_teacher(request.user)
        if linked is None:
            return queryset.none()
        return queryset.filter(
            professor=linked,
            research_work__in=_teacher_manageable_works(request.user),
        )

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or linked_professor_for_teacher(
            request.user
        ) is not None

    def has_view_permission(self, request, obj=None):
        if is_platform_admin(request.user):
            return True
        linked = linked_professor_for_teacher(request.user)
        return linked is not None and (
            obj is None
            or (
                obj.professor_id == linked.pk
                and _teacher_manageable_works(request.user)
                .filter(pk=obj.research_work_id)
                .exists()
            )
        )

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)


class OwnedWorkRelationshipAdmin(admin.ModelAdmin):
    """Fail-closed standalone relationship admin for teacher accounts."""

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_platform_admin(request.user):
            return queryset
        return queryset.filter(
            research_work__in=_teacher_manageable_works(request.user)
        ).distinct()

    def has_module_permission(self, request):
        return is_platform_admin(request.user) or linked_professor_for_teacher(
            request.user
        ) is not None

    def has_view_permission(self, request, obj=None):
        return _can_access_work(
            request.user, obj.research_work if obj is not None else None
        )

    def has_add_permission(self, request):
        return is_platform_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return is_platform_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_platform_admin(request.user)


@admin.register(WorkAuthor)
class WorkAuthorAdmin(OwnedWorkRelationshipAdmin):
    list_display = ("research_work", "student", "position", "created_at")
    search_fields = ("research_work__title", "student__display_name")
    autocomplete_fields = ("research_work", "student")
    readonly_fields = ("id", "created_at")

    def get_queryset(self, request):
        queryset = admin.ModelAdmin.get_queryset(self, request)
        return queryset if is_platform_admin(request.user) else queryset.none()

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)


@admin.register(WorkField)
class WorkFieldAdmin(OwnedWorkRelationshipAdmin):
    list_display = (
        "research_work",
        "research_field",
        "relevance",
        "is_primary",
        "status",
    )
    list_filter = ("relevance", "is_primary", "status", "source_type")
    search_fields = ("research_work__title", "research_field__display_name")
    autocomplete_fields = ("research_work", "research_field")
    readonly_fields = ("id", "created_at")

    def get_queryset(self, request):
        queryset = admin.ModelAdmin.get_queryset(self, request)
        return queryset if is_platform_admin(request.user) else queryset.none()

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)


@admin.register(WorkMethod)
class WorkMethodAdmin(OwnedWorkRelationshipAdmin):
    list_display = ("research_work", "research_method", "usage", "status")
    list_filter = ("usage", "status", "source_type")
    search_fields = ("research_work__title", "research_method__display_name")
    autocomplete_fields = ("research_work", "research_method")
    readonly_fields = ("id", "created_at")

    def get_queryset(self, request):
        queryset = admin.ModelAdmin.get_queryset(self, request)
        return queryset if is_platform_admin(request.user) else queryset.none()

    def has_module_permission(self, request):
        return is_platform_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return is_platform_admin(request.user)
