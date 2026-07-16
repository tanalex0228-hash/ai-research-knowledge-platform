from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from accounts.permissions import visible_scopes_for
from documents.choices import VisibilityScope as GraphVisibility

from .registry import EdgeType, NodeType, edge_policy, validate_edge_types


class NodeStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class EdgeStatus(models.TextChoices):
    CANDIDATE = "candidate", "Candidate"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    ARCHIVED = "archived", "Archived"


class EvidenceSourceType(models.TextChoices):
    MANUAL = "manual", "Manual"
    SOURCE_DOCUMENT = "source_document", "Source document"
    DOCUMENT_CHUNK = "document_chunk", "Document chunk"
    AI_EXTRACTION = "ai_extraction", "AI extraction"


class KnowledgeNodeQuerySet(models.QuerySet):
    def public(self):
        return self.filter(
            visibility_scope=GraphVisibility.PUBLIC,
            status=NodeStatus.ACTIVE,
        )

    def for_scopes(self, scopes):
        return self.filter(
            visibility_scope__in={
                str(getattr(scope, "value", scope)) for scope in scopes
            }
        )

    def visible_to(self, user):
        return self.for_scopes(visible_scopes_for(user))


class KnowledgeNode(models.Model):
    """Stable graph identity for a governed relational-domain object."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node_type = models.CharField(max_length=40, choices=NodeType.choices, db_index=True)
    object_id = models.UUIDField(db_index=True)
    label = models.CharField(max_length=255)
    visibility_scope = models.CharField(
        max_length=20,
        choices=GraphVisibility.choices,
        default=GraphVisibility.ADMIN,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=NodeStatus.choices,
        default=NodeStatus.ACTIVE,
        db_index=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = KnowledgeNodeQuerySet.as_manager()

    class Meta:
        ordering = ("node_type", "label", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("node_type", "object_id"),
                name="knowledge_graph_node_type_object_unique",
            ),
            models.CheckConstraint(
                condition=~Q(label=""),
                name="knowledge_graph_node_label_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_node_type_display()}: {self.label}"

    def clean(self) -> None:
        super().clean()
        self.label = (self.label or "").strip()
        if not self.label:
            raise ValidationError({"label": "A graph node label is required."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class KnowledgeEdge(models.Model):
    """A directed, typed, evidence-backed relationship between graph nodes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        KnowledgeNode,
        on_delete=models.CASCADE,
        related_name="outgoing_edges",
    )
    target = models.ForeignKey(
        KnowledgeNode,
        on_delete=models.CASCADE,
        related_name="incoming_edges",
    )
    edge_type = models.CharField(max_length=40, choices=EdgeType.choices, db_index=True)
    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=1,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    evidence_source_type = models.CharField(
        max_length=30,
        choices=EvidenceSourceType.choices,
        default=EvidenceSourceType.MANUAL,
    )
    evidence_source_id = models.UUIDField(null=True, blank=True)
    evidence_note = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=EdgeStatus.choices,
        default=EdgeStatus.CANDIDATE,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="knowledge_edges_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("source_id", "edge_type", "target_id")
        constraints = [
            models.UniqueConstraint(
                fields=("source", "target", "edge_type"),
                name="knowledge_graph_edge_unique",
            ),
            models.CheckConstraint(
                condition=Q(confidence__gte=0) & Q(confidence__lte=1),
                name="knowledge_graph_edge_confidence_range",
            ),
            models.CheckConstraint(
                condition=~Q(source=models.F("target")),
                name="knowledge_graph_edge_not_self",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_id} -[{self.edge_type}]→ {self.target_id}"

    def clean(self) -> None:
        super().clean()
        if not self.source_id or not self.target_id:
            return

        validate_edge_types(
            self.edge_type,
            self.source.node_type,
            self.target.node_type,
        )
        policy = edge_policy(self.edge_type)
        if self.source_id == self.target_id and not policy.allow_self_reference:
            raise ValidationError({"target": "A knowledge edge cannot target itself."})

        if (
            self.status == EdgeStatus.APPROVED
            and policy.evidence_required_for_approval
            and not self.evidence_source_id
            and not (self.evidence_note or "").strip()
        ):
            raise ValidationError(
                {
                    "evidence_source_id": (
                        "Approved relationships require an evidence source or a "
                        "manual evidence note."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
