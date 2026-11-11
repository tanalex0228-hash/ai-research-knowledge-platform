from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase
from django.urls import reverse
from unittest.mock import Mock

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

    def test_inline_author_reordering_swaps_positions_without_unique_conflict(self):
        first = Student.objects.create(display_name="First author")
        second = Student.objects.create(display_name="Second author")
        first_link = WorkAuthor.objects.create(
            research_work=self.work, student=first, position=1
        )
        second_link = WorkAuthor.objects.create(
            research_work=self.work, student=second, position=2
        )
        formset = Mock()
        formset.model = WorkAuthor
        formset.instance = self.work
        formset.deleted_objects = []
        formset.forms = []
        for link, position in ((first_link, 2), (second_link, 1)):
            inline_form = Mock()
            inline_form.cleaned_data = {"position": position}
            inline_form.save.return_value = link
            formset.forms.append(inline_form)

        ResearchWorkAdmin(ResearchWork, self.site).save_formset(
            self.request(), Mock(), formset, change=True
        )

        first_link.refresh_from_db()
        second_link.refresh_from_db()
        self.assertEqual((first_link.position, second_link.position), (2, 1))

    def test_standalone_author_move_buttons_reorder_and_audit(self):
        from accounts.models import AuditLog

        self.grant(WorkAuthor, "change")
        first = Student.objects.create(display_name="First author")
        second = Student.objects.create(display_name="Second author")
        first_link = WorkAuthor.objects.create(
            research_work=self.work, student=first, position=1
        )
        second_link = WorkAuthor.objects.create(
            research_work=self.work, student=second, position=2
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("admin:research_workauthor_move", args=[second_link.pk, "up"])
        )

        self.assertEqual(response.status_code, 204)
        first_link.refresh_from_db()
        second_link.refresh_from_db()
        self.assertEqual((first_link.position, second_link.position), (2, 1))
        self.assertTrue(
            AuditLog.objects.filter(
                event_type="research_work.author_reordered",
                target_id=self.work.pk,
            ).exists()
        )
