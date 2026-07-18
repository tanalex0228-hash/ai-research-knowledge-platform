from django.test import TestCase
from research.models import ResearchWork, ResearchWorkStatus, ResearchWorkType, WorkAdvisor, WorkAuthor, WorkField, WorkMethod, Student
from professors.models import Professor
from taxonomy.models import ResearchField, ResearchMethod
from knowledge_graph.models import KnowledgeNode, KnowledgeEdge, NodeStatus, EdgeStatus
from knowledge_graph.registry import NodeType, EdgeType
from accounts.models import User

class KnowledgeGraphSignalsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test_teacher", email="teacher@test.com")
        self.professor = Professor.objects.create(
            user=self.user,
            display_name="Professor Wang",
            status="active"
        )
        self.student = Student.objects.create(
            display_name="Student Lin",
            status="active"
        )
        self.field = ResearchField.objects.create(
            slug="ai-finance",
            display_name="AI Finance",
            status="active"
        )
        self.method = ResearchMethod.objects.create(
            slug="var-method",
            display_name="VAR Method",
            status="active"
        )

    def test_research_work_signals_sync(self):
        # 1. Create ResearchWork
        work = ResearchWork.objects.create(
            title="AI and ESG Analysis",
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            status=ResearchWorkStatus.DRAFT,
            year=2026
        )
        # Check node created and marked archived/inactive for draft
        node = KnowledgeNode.objects.get(node_type=NodeType.RESEARCH_WORK, object_id=work.id)
        self.assertEqual(node.label, "AI and ESG Analysis")
        self.assertEqual(node.status, NodeStatus.ARCHIVED)

        # 2. Transition work to published
        from research.services import transition_research_work
        transition_research_work(
            research_work=work,
            to_status=ResearchWorkStatus.PUBLISHED,
            actor=self.user,
            reason="Publishing for test",
            request_id="test-trace"
        )
        work.refresh_from_db()
        node.refresh_from_db()
        self.assertEqual(node.status, NodeStatus.ACTIVE)

        # 3. Add advisor link
        advisor_link = WorkAdvisor.objects.create(
            research_work=work,
            professor=self.professor,
            position=1
        )
        # Check advisor node and advised_by edge created
        prof_node = KnowledgeNode.objects.get(node_type=NodeType.PROFESSOR, object_id=self.professor.id)
        self.assertEqual(prof_node.label, "Professor Wang")
        edge = KnowledgeEdge.objects.get(source=node, target=prof_node, edge_type=EdgeType.ADVISED_BY)
        self.assertEqual(edge.status, EdgeStatus.APPROVED)

        # 4. Add author link
        author_link = WorkAuthor.objects.create(
            research_work=work,
            student=self.student,
            position=1
        )
        student_node = KnowledgeNode.objects.get(node_type=NodeType.STUDENT, object_id=self.student.id)
        edge_author = KnowledgeEdge.objects.get(source=node, target=student_node, edge_type=EdgeType.AUTHORED_BY)
        self.assertEqual(edge_author.status, EdgeStatus.APPROVED)

        # 5. Add field and method links
        field_link = WorkField.objects.create(
            research_work=work,
            research_field=self.field,
            status="approved"
        )
        field_node = KnowledgeNode.objects.get(node_type=NodeType.RESEARCH_FIELD, object_id=self.field.id)
        edge_field = KnowledgeEdge.objects.get(source=node, target=field_node, edge_type=EdgeType.BELONGS_TO_FIELD)
        self.assertEqual(edge_field.status, EdgeStatus.APPROVED)

        method_link = WorkMethod.objects.create(
            research_work=work,
            research_method=self.method,
            status="approved"
        )
        method_node = KnowledgeNode.objects.get(node_type=NodeType.RESEARCH_METHOD, object_id=self.method.id)
        edge_method = KnowledgeEdge.objects.get(source=node, target=method_node, edge_type=EdgeType.USES_METHOD)
        self.assertEqual(edge_method.status, EdgeStatus.APPROVED)

        # 6. Delete draft work (transitioned work is protected from deletion)
        draft_work = ResearchWork.objects.create(
            title="Draft to delete",
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            status=ResearchWorkStatus.DRAFT,
            year=2026
        )
        draft_node = KnowledgeNode.objects.get(node_type=NodeType.RESEARCH_WORK, object_id=draft_work.id)
        draft_work.delete()
        with self.assertRaises(KnowledgeNode.DoesNotExist):
            KnowledgeNode.objects.get(node_type=NodeType.RESEARCH_WORK, object_id=draft_work.id)
