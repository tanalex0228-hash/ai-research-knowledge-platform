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
        "candidate_type",
        "work_title",
        "candidate_preview",
        "confidence_display",
        "model",
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

    @admin.display(description="候選項目")
    def candidate_type(self, obj):
        names = {
            "work_authors": "研究學生",
            "work_advisors": "指導教師",
            "work_fields": "研究領域",
            "work_methods": "研究方法",
        }
        parts = obj.field_path.split(".")
        if len(parts) >= 3 and parts[0] in {"relations", "suggestions"}:
            suffix = (
                "可關聯" if parts[0] == "relations" else
                "核准後建立並關聯" if parts[1] in {"work_fields", "work_methods"} else
                "未找到既有資料"
            )
            return f"{names.get(parts[1], parts[1])} · {suffix}"
        if obj.field_path == "authors":
            return "作者辨識總覽 · 不建立關聯"
        return obj.field_path

    @admin.display(description="研究成果")
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

    @admin.display(description="候選值")
    def candidate_preview(self, obj):
        from django.utils.html import format_html
        val = obj.candidate_value
        if isinstance(val, dict):
            display = (
                val.get("name")
                or val.get("student_name")
                or val.get("professor_name")
                or val.get("research_field_name")
                or val.get("research_method_name")
                or val.get("value")
                or val.get("slug")
                or str(val)
            )
            if val.get("canonical_name") and val.get("canonical_name") != display:
                display = f"{display} → {val['canonical_name']}"
            if val.get("provider_fallback_reason"):
                display = f"規則備援：{val['provider_fallback_reason']}"
        else:
            display = str(val)
        preview = str(display)[:60]
        return format_html('<code style="font-size:0.8rem;">{}</code>', preview)

    @admin.display(description="信心分數")
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
        relation_reviews = 0
        new_taxonomy_reviews = 0
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
                if action == ReviewAction.APPROVE and item.field_path.startswith("relations."):
                    relation_reviews += 1
                elif action == ReviewAction.APPROVE and item.field_path.startswith(
                    ("suggestions.work_fields.", "suggestions.work_methods.")
                ):
                    new_taxonomy_reviews += 1

        if succeeded:
            self.message_user(
                request,
                (
                    f"已核准 {succeeded} 筆；既有資料關聯 {relation_reviews} 筆、"
                    f"新建並關聯分類 {new_taxonomy_reviews} 筆；其餘僅記錄審核結果。"
                    if action == ReviewAction.APPROVE
                    else f"已記錄 {succeeded} 筆不可變審核決策。"
                ),
                level=messages.SUCCESS,
            )
        if failures:
            self.message_user(
                request,
                "已略過審核項目：" + "; ".join(failures[:5]),
                level=messages.WARNING,
            )

    @admin.action(description="核准選取項目")
    def approve_selected(self, request, queryset):
        self._apply_decision(
            request,
            queryset,
            action=ReviewAction.APPROVE,
            reason="透過 Django 後台治理動作核准。",
        )

    @admin.action(description="拒絕選取項目")
    def reject_selected(self, request, queryset):
        self._apply_decision(
            request,
            queryset,
            action=ReviewAction.REJECT,
            reason="透過 Django 後台治理動作拒絕。",
        )

    @admin.action(description="封存選取項目")
    def archive_selected(self, request, queryset):
        self._apply_decision(
            request,
            queryset,
            action=ReviewAction.ARCHIVE,
            reason="透過 Django 後台治理動作封存。",
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
