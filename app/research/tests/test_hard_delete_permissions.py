from django.contrib import admin
from django.contrib.auth.models import Permission
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase

from accounts.admin import UserAdmin
from accounts.models import Role, User, UserRole
from professors.admin import ProfessorAdmin
from professors.models import Professor
from research.admin import ResearchWorkAdmin
from research.models import ResearchWork, ResearchWorkTransition, WorkAdvisor
from research.services import transition_research_work


class HardDeletePermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_user(
            username="hard-delete-admin",
            email="hard-delete-admin@example.test",
            is_staff=True,
        )
        UserRole.objects.create(
            user=self.admin_user,
            role=Role.objects.get(slug="admin"),
        )

    def request_for(self, path="/admin/"):
        request = self.factory.post(path)
        request.user = self.admin_user
        request.session = {}
        return request

    def grant(self, codename):
        permission = Permission.objects.get(codename=codename)
        self.admin_user.user_permissions.add(permission)

    def test_hard_delete_permissions_exist_for_admin_permission_menu(self):
        self.assertTrue(Permission.objects.filter(codename="hard_delete_user").exists())
        self.assertTrue(
            Permission.objects.filter(codename="hard_delete_professor").exists()
        )
        self.assertTrue(
            Permission.objects.filter(codename="hard_delete_researchwork").exists()
        )
        self.assertTrue(
            Permission.objects.filter(codename="hard_delete_sourcedocument").exists()
        )

    def test_research_work_hard_delete_bypasses_transition_protection(self):
        self.grant("hard_delete_researchwork")
        work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="Hard delete protected work",
            year=2026,
        )
        transition_research_work(
            research_work=work,
            to_status=ResearchWork.Status.UPLOADED,
            actor=self.admin_user,
            reason="Create protected lifecycle audit.",
            request_id="hard-delete-work-test",
        )

        with self.assertRaises(ProtectedError):
            work.delete()

        model_admin = ResearchWorkAdmin(ResearchWork, admin.site)
        model_admin.delete_model(self.request_for(), work)

        self.assertFalse(ResearchWork.objects.filter(pk=work.pk).exists())
        self.assertFalse(
            ResearchWorkTransition.objects.filter(research_work_id=work.pk).exists()
        )

    def test_user_hard_delete_detaches_transition_actor_then_deletes_user(self):
        self.grant("hard_delete_user")
        target = User.objects.create_user(
            username="protected-demo-teacher",
            email="protected-demo-teacher@example.test",
            is_staff=True,
        )
        work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="User actor protected work",
            year=2026,
        )
        transition = transition_research_work(
            research_work=work,
            to_status=ResearchWork.Status.UPLOADED,
            actor=target,
            reason="Create protected actor reference.",
            request_id="hard-delete-user-test",
        )

        with self.assertRaises(ProtectedError):
            target.delete()

        model_admin = UserAdmin(User, admin.site)
        model_admin.delete_model(self.request_for(), target)

        self.assertFalse(User.objects.filter(pk=target.pk).exists())
        transition.refresh_from_db()
        self.assertIsNone(transition.actor_id)

    def test_professor_hard_delete_removes_advisor_reference_and_keeps_work(self):
        self.grant("hard_delete_professor")
        professor = Professor.objects.create(display_name="Protected Professor")
        work = ResearchWork.objects.create(
            work_type=ResearchWork.WorkType.UNDERGRADUATE_PROJECT,
            title="Professor protected work",
            year=2026,
        )
        WorkAdvisor.objects.create(research_work=work, professor=professor)

        with self.assertRaises(ProtectedError):
            professor.delete()

        model_admin = ProfessorAdmin(Professor, admin.site)
        model_admin.delete_model(self.request_for(), professor)

        self.assertFalse(Professor.objects.filter(pk=professor.pk).exists())
        self.assertTrue(ResearchWork.objects.filter(pk=work.pk).exists())
        self.assertFalse(WorkAdvisor.objects.filter(professor_id=professor.pk).exists())
