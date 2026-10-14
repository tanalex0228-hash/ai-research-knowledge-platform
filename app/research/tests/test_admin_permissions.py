from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase

from accounts.models import User
from research.admin import (
    ResearchWorkAdmin,
    ResearchWorkTransitionAdmin,
    StudentAdmin,
    WorkAdvisorAdmin,
    WorkAuthorAdmin,
    WorkFieldAdmin,
    WorkMethodAdmin,
)
from research.models import (
    ResearchWork,
    ResearchWorkTransition,
    ResearchWorkType,
    Student,
    WorkAdvisor,
    WorkAuthor,
    WorkField,
    WorkMethod,
)


class ResearchAdminGroupPermissionTests(TestCase):
    """Research administration follows normal Django group permissions."""

    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.group = Group.objects.create(name="Lab_Member")
        self.user = User.objects.create_user(
            username="lab-member", email="lab-member@example.test", is_staff=True
        )
        self.user.groups.add(self.group)
        self.work = ResearchWork.objects.create(
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            title="A work not linked to this user",
            year=2026,
        )

    def grant(self, model, *actions):
        content_type = ContentType.objects.get_for_model(model)
        self.group.permissions.add(
            *Permission.objects.filter(
                content_type=content_type,
                codename__in=[f"{action}_{model._meta.model_name}" for action in actions],
            )
        )
        self.user = User.objects.get(pk=self.user.pk)

    def request(self):
        request = self.factory.get("/admin/research/")
        request.user = self.user
        return request

    def test_research_work_crud_is_granted_by_group_without_professor_link(self):
        self.grant(ResearchWork, "view", "add", "change", "delete")
        model_admin = ResearchWorkAdmin(ResearchWork, self.site)
        request = self.request()

        self.assertIn(self.work, model_admin.get_queryset(request))
        self.assertTrue(model_admin.has_module_permission(request))
        self.assertTrue(model_admin.has_view_permission(request, self.work))
        self.assertTrue(model_admin.has_add_permission(request))
        self.assertTrue(model_admin.has_change_permission(request, self.work))
        self.assertTrue(model_admin.has_delete_permission(request, self.work))

    def test_relationship_and_student_crud_are_granted_by_model_permissions(self):
        for model in (Student, WorkAuthor, WorkAdvisor, WorkField, WorkMethod):
            self.grant(model, "view", "add", "change", "delete")

        request = self.request()
        for model, admin_class in (
            (Student, StudentAdmin),
            (WorkAuthor, WorkAuthorAdmin),
            (WorkAdvisor, WorkAdvisorAdmin),
            (WorkField, WorkFieldAdmin),
            (WorkMethod, WorkMethodAdmin),
        ):
            with self.subTest(model=model.__name__):
                model_admin = admin_class(model, self.site)
                self.assertTrue(model_admin.has_module_permission(request))
                self.assertTrue(model_admin.has_add_permission(request))
                self.assertTrue(model_admin.has_change_permission(request))
                self.assertTrue(model_admin.has_delete_permission(request))

    def test_lab_member_cannot_use_publication_or_review_governance_actions(self):
        self.grant(ResearchWork, "view", "change")
        model_admin = ResearchWorkAdmin(ResearchWork, self.site)
        actions = model_admin.get_actions(self.request())

        self.assertNotIn("transition_to_approved", actions)
        self.assertNotIn("transition_to_published", actions)
        self.assertNotIn("rerun_extraction_pipeline", actions)
        self.assertNotIn("hard_delete_selected_research_works", actions)

    def test_transition_audit_is_view_only_even_with_model_permissions(self):
        self.grant(ResearchWorkTransition, "view", "change", "delete", "add")
        model_admin = ResearchWorkTransitionAdmin(ResearchWorkTransition, self.site)
        request = self.request()

        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
