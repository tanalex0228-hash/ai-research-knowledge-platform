import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def normalize_legacy_review_items(apps, schema_editor):
    """Repair states produced by the Phase 0/1 decision service before checks."""

    ReviewItem = apps.get_model("review", "ReviewItem")
    ReviewDecision = apps.get_model("review", "ReviewDecision")
    queryset = ReviewItem.objects.using(schema_editor.connection.alias)
    for item in queryset.exclude(assigned_to__isnull=True).iterator():
        latest_assignment = (
            ReviewDecision.objects.using(schema_editor.connection.alias)
            .filter(
                review_item_id=item.pk,
                action="request_teacher_review",
                assigned_to_snapshot__isnull=True,
            )
            .order_by("-created_at", "-pk")
            .first()
        )
        if latest_assignment is not None:
            ReviewDecision.objects.using(schema_editor.connection.alias).filter(
                pk=latest_assignment.pk
            ).update(assigned_to_snapshot_id=item.assigned_to_id)
    queryset.filter(state="pending").update(assigned_to=None, resolved_at=None)
    queryset.filter(state="teacher_review").update(resolved_at=None)
    queryset.filter(state="teacher_review", assigned_to__isnull=True).update(
        state="pending"
    )
    terminal = queryset.filter(state__in=("approved", "rejected", "archived"))
    terminal.update(assigned_to=None)
    terminal.filter(resolved_at__isnull=True).update(resolved_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ("review", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="reviewdecision",
            name="assigned_to_snapshot",
            field=models.ForeignKey(
                blank=True,
                help_text="Immutable snapshot of the teacher selected by a review request.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="review_assignment_decisions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            normalize_legacy_review_items,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="reviewitem",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        state="pending",
                        assigned_to__isnull=True,
                        resolved_at__isnull=True,
                    )
                    | models.Q(
                        state="teacher_review",
                        assigned_to__isnull=False,
                        resolved_at__isnull=True,
                    )
                    | models.Q(
                        state__in=("approved", "rejected", "archived"),
                        assigned_to__isnull=True,
                        resolved_at__isnull=False,
                    )
                ),
                name="review_item_state_fields_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="reviewdecision",
            constraint=models.CheckConstraint(
                condition=(
                    (
                        models.Q(previous_state="pending")
                        & (
                            models.Q(
                                action__in=("approve", "edit"),
                                resulting_state="approved",
                            )
                            | models.Q(
                                action="reject",
                                resulting_state="rejected",
                            )
                            | models.Q(
                                action="request_teacher_review",
                                resulting_state="teacher_review",
                            )
                            | models.Q(
                                action="archive",
                                resulting_state="archived",
                            )
                        )
                    )
                    | (
                        models.Q(previous_state="teacher_review")
                        & (
                            models.Q(
                                action__in=("approve", "edit"),
                                resulting_state="approved",
                            )
                            | models.Q(
                                action="reject",
                                resulting_state="rejected",
                            )
                            | models.Q(
                                action="archive",
                                resulting_state="archived",
                            )
                            | models.Q(
                                action="request_teacher_review",
                                resulting_state="teacher_review",
                            )
                        )
                    )
                ),
                name="review_decision_transition_legal",
            ),
        ),
    ]
