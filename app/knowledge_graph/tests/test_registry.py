from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from knowledge_graph.registry import (
    EDGE_TYPE_REGISTRY,
    EdgeType,
    NodeType,
    is_edge_allowed,
    validate_edge_types,
)


class EdgeTypeRegistryTests(SimpleTestCase):
    def test_research_work_may_use_research_method(self):
        self.assertTrue(
            is_edge_allowed(
                EdgeType.USES_METHOD,
                NodeType.RESEARCH_WORK,
                NodeType.RESEARCH_METHOD,
            )
        )

    def test_professor_may_not_directly_use_research_method(self):
        self.assertFalse(
            is_edge_allowed(
                EdgeType.USES_METHOD,
                NodeType.PROFESSOR,
                NodeType.RESEARCH_METHOD,
            )
        )
        with self.assertRaisesMessage(ValidationError, "does not allow"):
            validate_edge_types(
                EdgeType.USES_METHOD,
                NodeType.PROFESSOR,
                NodeType.RESEARCH_METHOD,
            )

    def test_registry_is_immutable(self):
        with self.assertRaises(TypeError):
            EDGE_TYPE_REGISTRY["unsafe"] = object()

    def test_unknown_edge_type_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Unsupported"):
            validate_edge_types(
                "invented_edge",
                NodeType.RESEARCH_WORK,
                NodeType.RESEARCH_METHOD,
            )

