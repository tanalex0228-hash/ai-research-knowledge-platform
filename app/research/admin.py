from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html

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
from documents.admin import SourceDocumentInline
from .hard_delete import hard_delete_research_work
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
        SourceDocumentInline,
        ResearchWorkTransitionInline,
    )
    list_display = ("title", "year", "work_type", "status_badge", "visibility_scope", "source_quality", "review_item_count")
    list_filter = ("status", "visibility_scope", "work_type", "source_quality", "year")
    search_fields = ("title", "normalized_title", "abstract")
    autocomplete_fields = ("created_by", "updated_by")
    readonly_fields = ("id", "normalized_title", "created_at", "updated_at")
    date_hierarchy = "created_at"
    actions = (
        "transition_to_approved",
        "transition_to_published",
        "transition_to_archived",
        "transition_to_rejected",
        "restore_archived",
        "approve_all_ai_candidates_and_publish",
        "rerun_extraction_pipeline",
        "hard_delete_selected_research_works",
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

    def _can_hard_delete(self, request) -> bool:
        return is_platform_admin(request.user) and request.user.has_perm(
            "research.hard_delete_researchwork"
        )

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
        if not self._can_hard_delete(request):
            actions.pop("hard_delete_selected_research_works", None)
        return actions

    def delete_model(self, request, obj):
        if self._can_hard_delete(request):
            hard_delete_research_work(obj)
            return
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        if not self._can_hard_delete(request):
            return super().delete_queryset(request, queryset)
        deleted = 0
        for work in queryset:
            hard_delete_research_work(work)
            deleted += 1
        self.message_user(
            request,
            f"Hard-deleted {deleted} research work(s).",
            level=messages.WARNING,
        )

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

    @admin.action(description="Lifecycle: Approve")
    def transition_to_approved(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.APPROVED)

    @admin.action(description="Lifecycle: Publish")
    def transition_to_published(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.PUBLISHED)

    @admin.action(description="Lifecycle: Archive")
    def transition_to_archived(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.ARCHIVED)

    @admin.action(description="Lifecycle: Reject")
    def transition_to_rejected(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.REJECTED)

    @admin.action(description="Lifecycle: Restore")
    def restore_archived(self, request, queryset):
        self._transition_selected(request, queryset, ResearchWorkStatus.PUBLISHED)

    @admin.action(description="✨ AI: Approve All Candidates & Publish")
    def approve_all_ai_candidates_and_publish(self, request, queryset):
        """Batch-approve all pending AI-extracted ReviewItems and auto-publish."""
        from review.models import ReviewItem, ReviewState, ReviewAction
        from review.services import decide_review_item
        request_id = request_id_for(request)
        total_approved = 0
        total_skipped = 0
        total_works = 0
        for work in queryset:
            pending_items = ReviewItem.objects.filter(
                target_id=work.pk,
                state=ReviewState.PENDING,
            )
            work_approved = 0
            for item in pending_items:
                try:
                    decide_review_item(
                        item=item,
                        reviewer=request.user,
                        action=ReviewAction.APPROVE,
                        reason="Batch approved via Django admin AI action.",
                    )
                    work_approved += 1
                except Exception:
                    total_skipped += 1
            total_approved += work_approved
            if work_approved > 0:
                total_works += 1
            # Auto-publish if still in reviewable state
            work.refresh_from_db()
            if work.status in ("under_review", "approved", "ai_extracted"):
                try:
                    if work.status in ("under_review", "ai_extracted"):
                        transition_research_work(
                            research_work=work,
                            to_status=ResearchWorkStatus.APPROVED,
                            actor=request.user,
                            reason="Admin batch-approved all AI candidates.",
                            request_id=request_id,
                        )
                        work.refresh_from_db()
                    transition_research_work(
                        research_work=work,
                        to_status=ResearchWorkStatus.PUBLISHED,
                        actor=request.user,
                        reason="Auto-publish after batch AI candidate approval.",
                        request_id=request_id,
                    )
                except ValidationError:
                    pass
        self.message_user(
            request,
            f"Approved {total_approved} AI candidates across {total_works} work(s). "
            f"{total_skipped} item(s) skipped.",
            level=messages.SUCCESS if total_approved else messages.WARNING,
        )

    @admin.action(description="🔄 AI: Re-run Extraction Pipeline")
    def rerun_extraction_pipeline(self, request, queryset):
        """Re-trigger PDF extraction for all source documents of selected works."""
        from documents.models import SourceDocument, ExtractionStatus
        from documents.services import queue_document_extraction
        queued = 0
        for work in queryset:
            for doc in SourceDocument.objects.filter(research_work=work):
                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE documents_sourcedocument SET extraction_status=%s, extraction_error=%s WHERE id=%s",
                        [ExtractionStatus.PENDING, "", str(doc.id)]
                    )
                doc.refresh_from_db()
                queue_document_extraction(doc)
                queued += 1
        self.message_user(
            request,
            f"Queued re-extraction for {queued} document(s).",
            level=messages.SUCCESS if queued else messages.WARNING,
        )

    @admin.action(description="Hard delete selected research works and protected records")
    def hard_delete_selected_research_works(self, request, queryset):
        self.delete_queryset(request, queryset)

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "draft": "#aaa",
            "uploaded": "#17a2b8",
            "parsed": "#6f42c1",
            "ai_extracted": "#fd7e14",
            "under_review": "#ffc107",
            "approved": "#28a745",
            "published": "#007bff",
            "archived": "#6c757d",
            "rejected": "#dc3545",
        }
        color = colors.get(obj.status, "#aaa")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:bold">{}</span>',
            color, obj.get_status_display() if hasattr(obj, 'get_status_display') else obj.status
        )

    @admin.display(description="📝 AI Candidates")
    def review_item_count(self, obj):
        from review.models import ReviewItem, ReviewState
        pending = ReviewItem.objects.filter(target_id=obj.pk, state=ReviewState.PENDING).count()
        total = ReviewItem.objects.filter(target_id=obj.pk).count()
        if pending > 0:
            return format_html('<span style="color:#e67e22;font-weight:bold">{} pending / {} total</span>', pending, total)
        return format_html('<span style="color:#27ae60">{} total</span>', total)


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
