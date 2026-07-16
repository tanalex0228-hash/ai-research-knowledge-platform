import uuid

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from knowledge_graph.models import EdgeStatus, KnowledgeEdge, KnowledgeNode
from knowledge_graph.registry import EdgeType, NodeType


class KnowledgeEdgeValidationTests(SimpleTestCase):
    def node(self, node_type: str, label: str) -> KnowledgeNode:
        return KnowledgeNode(
            node_type=node_type,
            object_id=uuid.uuid4(),
            label=label,
        )

    def test_model_accepts_research_work_uses_method(self):
        edge = KnowledgeEdge(
            source=self.node(NodeType.RESEARCH_WORK, "A research work"),
            target=self.node(NodeType.RESEARCH_METHOD, "VAR"),
            edge_type=EdgeType.USES_METHOD,
        )

        edge.clean()

    def test_model_rejects_professor_directly_uses_method(self):
        edge = KnowledgeEdge(
            source=self.node(NodeType.PROFESSOR, "A professor"),
            target=self.node(NodeType.RESEARCH_METHOD, "VAR"),
            edge_type=EdgeType.USES_METHOD,
        )

        with self.assertRaisesMessage(ValidationError, "does not allow"):
            edge.clean()

    def test_approved_edge_requires_evidence(self):
        edge = KnowledgeEdge(
            source=self.node(NodeType.RESEARCH_WORK, "A research work"),
            target=self.node(NodeType.RESEARCH_METHOD, "VAR"),
            edge_type=EdgeType.USES_METHOD,
            status=EdgeStatus.APPROVED,
        )

        with self.assertRaisesMessage(ValidationError, "require an evidence"):
            edge.clean()

    def test_approved_manual_edge_accepts_evidence_note(self):
        edge = KnowledgeEdge(
            source=self.node(NodeType.RESEARCH_WORK, "A research work"),
            target=self.node(NodeType.RESEARCH_METHOD, "VAR"),
            edge_type=EdgeType.USES_METHOD,
            status=EdgeStatus.APPROVED,
            evidence_note="Confirmed by an administrator against the source PDF.",
        )

        edge.clean()

    def test_edge_cannot_target_itself(self):
        node = self.node(NodeType.RESEARCH_WORK, "A research work")
        edge = KnowledgeEdge(
            source=node,
            target=node,
            edge_type=EdgeType.SIMILAR_TO_WORK,
        )

        with self.assertRaisesMessage(ValidationError, "cannot target itself"):
            edge.clean()

