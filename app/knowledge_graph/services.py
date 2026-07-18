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


def sync_research_work_to_kg(work):
    from knowledge_graph.models import KnowledgeNode, NodeStatus, KnowledgeEdge, EdgeStatus, EvidenceSourceType
    from knowledge_graph.registry import NodeType, EdgeType
    
    node_status = NodeStatus.ACTIVE
    edge_status = EdgeStatus.APPROVED
    
    if work.status == 'archived':
        node_status = NodeStatus.ARCHIVED
        edge_status = EdgeStatus.ARCHIVED
    elif work.status in ('rejected', 'draft'):
        node_status = NodeStatus.ARCHIVED
        edge_status = EdgeStatus.REJECTED
    elif work.status in ('uploaded', 'parsed', 'ai_extracted', 'under_review'):
        node_status = NodeStatus.ACTIVE
        edge_status = EdgeStatus.CANDIDATE
    
    work_node, _ = KnowledgeNode.objects.update_or_create(
        node_type=NodeType.RESEARCH_WORK,
        object_id=work.id,
        defaults={
            'label': work.title,
            'visibility_scope': work.visibility_scope,
            'status': node_status,
        }
    )
    
    if node_status == NodeStatus.ARCHIVED or edge_status in (EdgeStatus.ARCHIVED, EdgeStatus.REJECTED):
        KnowledgeEdge.objects.filter(source=work_node).update(status=edge_status)
        return
        
    current_advisors = work.advisor_links.all()
    for link in current_advisors:
        prof = link.professor
        prof_node, _ = KnowledgeNode.objects.update_or_create(
            node_type=NodeType.PROFESSOR,
            object_id=prof.id,
            defaults={
                'label': prof.display_name,
                'visibility_scope': prof.visibility_scope,
                'status': NodeStatus.ACTIVE if prof.status == 'active' else NodeStatus.ARCHIVED,
            }
        )
        KnowledgeEdge.objects.update_or_create(
            source=work_node,
            target=prof_node,
            edge_type=EdgeType.ADVISED_BY,
            defaults={
                'confidence': 1.0,
                'evidence_source_type': EvidenceSourceType.MANUAL,
                'evidence_note': "Synchronized automatically from canonical database.",
                'status': edge_status,
            }
        )
    prof_ids = [link.professor_id for link in current_advisors]
    KnowledgeEdge.objects.filter(
        source=work_node, 
        edge_type=EdgeType.ADVISED_BY
    ).exclude(target__object_id__in=prof_ids).delete()

    current_authors = work.author_links.all()
    for link in current_authors:
        student = link.student
        student_node, _ = KnowledgeNode.objects.update_or_create(
            node_type=NodeType.STUDENT,
            object_id=student.id,
            defaults={
                'label': student.display_name,
                'visibility_scope': student.visibility_scope,
                'status': NodeStatus.ACTIVE if student.status == 'active' else NodeStatus.ARCHIVED,
            }
        )
        KnowledgeEdge.objects.update_or_create(
            source=work_node,
            target=student_node,
            edge_type=EdgeType.AUTHORED_BY,
            defaults={
                'confidence': 1.0,
                'evidence_source_type': EvidenceSourceType.MANUAL,
                'evidence_note': "Synchronized automatically from canonical database.",
                'status': edge_status,
            }
        )
    student_ids = [link.student_id for link in current_authors]
    KnowledgeEdge.objects.filter(
        source=work_node, 
        edge_type=EdgeType.AUTHORED_BY
    ).exclude(target__object_id__in=student_ids).delete()

    current_fields = work.field_links.filter(status='approved')
    for link in current_fields:
        field = link.research_field
        field_node, _ = KnowledgeNode.objects.update_or_create(
            node_type=NodeType.RESEARCH_FIELD,
            object_id=field.id,
            defaults={
                'label': field.display_name,
                'visibility_scope': field.visibility_scope,
                'status': NodeStatus.ACTIVE if field.status == 'active' else NodeStatus.ARCHIVED,
            }
        )
        KnowledgeEdge.objects.update_or_create(
            source=work_node,
            target=field_node,
            edge_type=EdgeType.BELONGS_TO_FIELD,
            defaults={
                'confidence': link.confidence or 1.0,
                'evidence_source_type': EvidenceSourceType.MANUAL,
                'evidence_note': "Synchronized automatically from canonical database.",
                'status': edge_status,
            }
        )
    field_ids = [link.research_field_id for link in current_fields]
    KnowledgeEdge.objects.filter(
        source=work_node, 
        edge_type=EdgeType.BELONGS_TO_FIELD
    ).exclude(target__object_id__in=field_ids).delete()

    current_methods = work.method_links.filter(status='approved')
    for link in current_methods:
        method = link.research_method
        method_node, _ = KnowledgeNode.objects.update_or_create(
            node_type=NodeType.RESEARCH_METHOD,
            object_id=method.id,
            defaults={
                'label': method.display_name,
                'visibility_scope': method.visibility_scope,
                'status': NodeStatus.ACTIVE if method.status == 'active' else NodeStatus.ARCHIVED,
            }
        )
        KnowledgeEdge.objects.update_or_create(
            source=work_node,
            target=method_node,
            edge_type=EdgeType.USES_METHOD,
            defaults={
                'confidence': link.confidence or 1.0,
                'evidence_source_type': EvidenceSourceType.MANUAL,
                'evidence_note': "Synchronized automatically from canonical database.",
                'status': edge_status,
            }
        )
    method_ids = [link.research_method_id for link in current_methods]
    KnowledgeEdge.objects.filter(
        source=work_node, 
        edge_type=EdgeType.USES_METHOD
    ).exclude(target__object_id__in=method_ids).delete()


def sync_professor_to_kg(professor):
    from knowledge_graph.models import KnowledgeNode, NodeStatus
    from knowledge_graph.registry import NodeType
    KnowledgeNode.objects.update_or_create(
        node_type=NodeType.PROFESSOR,
        object_id=professor.id,
        defaults={
            'label': professor.display_name,
            'visibility_scope': professor.visibility_scope,
            'status': NodeStatus.ACTIVE if professor.status == 'active' else NodeStatus.ARCHIVED,
        }
    )


def sync_student_to_kg(student):
    from knowledge_graph.models import KnowledgeNode, NodeStatus
    from knowledge_graph.registry import NodeType
    KnowledgeNode.objects.update_or_create(
        node_type=NodeType.STUDENT,
        object_id=student.id,
        defaults={
            'label': student.display_name,
            'visibility_scope': student.visibility_scope,
            'status': NodeStatus.ACTIVE if student.status == 'active' else NodeStatus.ARCHIVED,
        }
    )


def delete_research_work_from_kg(work):
    from knowledge_graph.models import KnowledgeNode
    from knowledge_graph.registry import NodeType
    KnowledgeNode.objects.filter(
        node_type=NodeType.RESEARCH_WORK,
        object_id=work.id
    ).delete()


