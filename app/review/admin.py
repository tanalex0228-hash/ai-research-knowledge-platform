from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError

from .models import (
    AIExtractionEvidence,
    AIExtractionJob,
    AIExtractionResult,
    ReviewDecision,
    ReviewItem,
)
from .services import decide_review_item
from .models import ReviewAction


class ExtractionEvidenceInline(admin.TabularInline):
    model = AIExtractionEvidence
    extra = 0
    raw_id_fields = ("document_chunk",)
    readonly_fields = tuple(field.name for field in AIExtractionEvidence._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIExtractionJob)
class AIExtractionJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source_document",
        "schema_version",
        "visibility_scope",
        "status",
        "created_at",
    )
    list_filter = ("status", "visibility_scope", "schema_version", "created_at")
    search_fields = ("id", "source_document__id", "error_code")
    raw_id_fields = ("source_document", "prompt_version", "ai_request", "requested_by")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(AIExtractionResult)
class AIExtractionResultAdmin(admin.ModelAdmin):
    inlines = (ExtractionEvidenceInline,)
    list_display = (
        "id",
        "job",
        "result_type",
        "confidence",
        "status",
        "visibility_scope",
    )
    list_filter = ("result_type", "status", "visibility_scope")
    search_fields = ("id", "job__id", "candidate_data")
    raw_id_fields = ("job", "primary_evidence_chunk")
    readonly_fields = tuple(field.name for field in AIExtractionResult._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Preserve view access while preventing candidate mutation in admin.
        return bool(request.user.has_perm("review.view_aiextractionresult"))

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReviewItem)
class ReviewItemAdmin(admin.ModelAdmin):
    list_display = (
        "field_path",
        "work_title",
        "candidate_preview",
        "confidence_display",
        "state",
        "visibility_scope",
        "created_at",
    )
    list_filter = ("state", "target_type", "visibility_scope", "created_at", "field_path")
    search_fields = ("id", "field_path", "target_id")
    raw_id_fields = ("extraction_result", "assigned_to")
    readonly_fields = tuple(field.name for field in ReviewItem._meta.fields)
    actions = ("approve_selected", "reject_selected", "archive_selected")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Research Work")
    def work_title(self, obj):
        from research.models import ResearchWork
        from django.utils.html import format_html
        try:
            work = ResearchWork.objects.get(pk=obj.target_id)
            return format_html(
                '<span title="{}" style="font-size:0.85rem;">{}</span>',
                work.title, work.title[:40] + ("…" if len(work.title) > 40 else "")
            )
        except Exception:
            return str(obj.target_id)[:20]

    @admin.display(description="Candidate Value")
    def candidate_preview(self, obj):
        from django.utils.html import format_html
        val = obj.candidate_value
        if isinstance(val, dict):
            display = val.get("value") or val.get("slug") or str(val)
        else:
            display = str(val)
        preview = str(display)[:60]
        return format_html('<code style="font-size:0.8rem;">{}</code>', preview)

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        from django.utils.html import format_html
        pct = int((obj.confidence or 0) * 100)
        color = "#27ae60" if pct >= 90 else "#e67e22" if pct >= 70 else "#e74c3c"
        return format_html(
            '<span style="color:{};font-weight:bold;">{} %</span>',
            color, pct
        )

    def _apply_decision(self, request, queryset, *, action, reason):
        succeeded = 0
        failures: list[str] = []
        for item in queryset.only("pk"):
            try:
                decide_review_item(
                    item=item,
                    reviewer=request.user,
                    action=action,
                    reason=reason,
                )
            except (PermissionDenied, ValidationError) as exc:
                failures.append(f"{item.pk}: {exc}")
            else:
                succeeded += 1

        if succeeded:
            self.message_user(
                request,
                f"Recorded {succeeded} immutable review decision(s).",
                level=messages.SUCCESS,
            )
        if failures:
            self.message_user(
                request,
                "Skipped review items: " + "; ".join(failures[:5]),
                level=messages.WARNING,
            )

    @admin.action(description="✅ Approve selected")
    def approve_selected(self, request, queryset):
        self._apply_decision(
            request,
            queryset,
            action=ReviewAction.APPROVE,
            reason="Approved through the Django admin governed action.",
        )

    @admin.action(description="❌ Reject selected")
    def reject_selected(self, request, queryset):
        self._apply_decision(
            request,
            queryset,
            action=ReviewAction.REJECT,
            reason="Rejected through the Django admin governed action.",
        )

    @admin.action(description="🗄 Archive selected")
    def archive_selected(self, request, queryset):
        self._apply_decision(
            request,
            queryset,
            action=ReviewAction.ARCHIVE,
            reason="Archived through the Django admin governed action.",
        )


@admin.register(ReviewDecision)
class ReviewDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "review_item",
        "action",
        "reviewer",
        "previous_state",
        "resulting_state",
        "created_at",
    )
    list_filter = ("action", "resulting_state", "created_at")
    search_fields = ("review_item__id", "reviewer__username", "reason")
    raw_id_fields = ("review_item", "reviewer")
    readonly_fields = tuple(field.name for field in ReviewDecision._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
