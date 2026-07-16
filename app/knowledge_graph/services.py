from __future__ import annotations

from django.db import transaction

from .models import EdgeStatus, EvidenceSourceType, KnowledgeEdge, KnowledgeNode


@transaction.atomic
def create_candidate_edge(
    *,
    source: KnowledgeNode,
    target: KnowledgeNode,
    edge_type: str,
    confidence,
    evidence_source_type: str = EvidenceSourceType.MANUAL,
    evidence_source_id=None,
    evidence_note: str = "",
    created_by=None,
) -> KnowledgeEdge:
    """Create a validated candidate; registry validation is enforced by save()."""

    return KnowledgeEdge.objects.create(
        source=source,
        target=target,
        edge_type=edge_type,
        confidence=confidence,
        evidence_source_type=evidence_source_type,
        evidence_source_id=evidence_source_id,
        evidence_note=evidence_note,
        status=EdgeStatus.CANDIDATE,
        created_by=created_by,
    )


def traverse_graph(*args, **kwargs):
    del args, kwargs
    raise NotImplementedError("Graph traversal and scoring are deferred beyond Phase 1.")

