from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from accounts.models import Role, User, UserRole
from professors.models import Professor
from research.admin import (
    ResearchWorkAdmin,
    WorkAdvisorAdmin,
    WorkAdvisorInline,
    WorkAuthorAdmin,
    WorkFieldAdmin,
    WorkMethodAdmin,
)
from research.models import (
    ResearchWork,
    ResearchWorkStatus,
    ResearchWorkType,
    Student,
    WorkAdvisor,
    WorkAuthor,
    WorkField,
    WorkMethod,
)
from taxonomy.models import ResearchField, ResearchMethod


class ResearchWorkAdminObjectPermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = AdminSite()
        self.model_admin = ResearchWorkAdmin(ResearchWork, self.admin_site)
        self.advisor_inline = WorkAdvisorInline(ResearchWork, self.admin_site)
        self.advisor_admin = WorkAdvisorAdmin(WorkAdvisor, self.admin_site)
        self.author_admin = WorkAuthorAdmin(WorkAuthor, self.admin_site)
        self.field_admin = WorkFieldAdmin(WorkField, self.admin_site)
        self.method_admin = WorkMethodAdmin(WorkMethod, self.admin_site)
        teacher_role = Role.objects.get(slug="teacher")
        admin_role = Role.objects.get(slug="admin")

        self.teacher = User.objects.create_user(
            username="research-teacher",
            email="research-teacher@example.test",
            is_staff=True,
        )
        UserRole.objects.create(user=self.teacher, role=teacher_role)
        self.professor = Professor.objects.create(
            user=self.teacher,
            display_name="Research Teacher",
        )

        self.other_teacher = User.objects.create_user(
            username="other-research-teacher",
            email="other-research-teacher@example.test",
            is_staff=True,
        )
        UserRole.objects.create(user=self.other_teacher, role=teacher_role)
        self.other_professor = Professor.objects.create(
            user=self.other_teacher,
            display_name="Other Research Teacher",
        )

        self.own_work = self.make_work("Own advised work")
        self.other_work = self.make_work("Other advised work")
        self.shared_work = self.make_work("Shared advised work")
        WorkAdvisor.objects.create(research_work=self.own_work, professor=self.professor)
        WorkAdvisor.objects.create(
            research_work=self.other_work,
            professor=self.other_professor,
        )
        WorkAdvisor.objects.create(research_work=self.shared_work, professor=self.professor)
        WorkAdvisor.objects.create(
            research_work=self.shared_work,
            professor=self.other_professor,
            position=2,
        )

        self.admin_user = User.objects.create_user(
            username="research-admin",
            email="research-admin@example.test",
            is_staff=True,
        )
        UserRole.objects.create(user=self.admin_user, role=admin_role)
        self.unlinked_teacher = User.objects.create_user(
            username="unlinked-teacher",
            email="unlinked-teacher@example.test",
            is_staff=True,
        )
        UserRole.objects.create(user=self.unlinked_teacher, role=teacher_role)

    @staticmethod
    def make_work(title):
        return ResearchWork.objects.create(
            work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
            title=title,
            year=2026,
        )

    def request_for(self, user):
        request = self.factory.get("/admin/research/researchwork/")
        request.user = user
        return request

    def test_teacher_only_lists_views_and_changes_advised_works(self):
        request = self.request_for(self.teacher)
        self.assertQuerySetEqual(
            self.model_admin.get_queryset(request),
            [self.own_work, self.shared_work],
            ordered=False,
        )
        self.assertTrue(self.model_admin.has_view_permission(request, self.own_work))
        self.assertTrue(self.model_admin.has_change_permission(request, self.shared_work))
        self.assertFalse(self.model_admin.has_view_permission(request, self.other_work))
        self.assertFalse(self.model_admin.has_change_permission(request, self.other_work))
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.own_work))

        readonly = self.model_admin.get_readonly_fields(request, self.own_work)
        self.assertIn("status", readonly)
        self.assertIn("visibility_scope", readonly)
        self.assertIn("created_by", readonly)
        self.assertIn("updated_by", readonly)
        self.assertFalse(self.model_admin.get_actions(request))

    def test_admin_role_retains_all_works_and_lifecycle_actions(self):
        request = self.request_for(self.admin_user)
        self.assertEqual(self.model_admin.get_queryset(request).count(), 3)
        self.assertTrue(self.model_admin.has_change_permission(request, self.other_work))
        self.assertTrue(self.model_admin.has_add_permission(request))
        self.assertTrue(self.model_admin.has_delete_permission(request, self.other_work))
        self.assertIn("transition_to_uploaded", self.model_admin.get_actions(request))
        self.assertIn(
            "status", self.model_admin.get_readonly_fields(request, self.own_work)
        )
        self.assertIn("status", self.model_admin.get_readonly_fields(request, None))

    def test_admin_lifecycle_action_uses_service_and_writes_audit(self):
        request = self.factory.post(
            "/admin/research/researchwork/",
            HTTP_X_REQUEST_ID="admin-transition-request-1",
        )
        request.user = self.admin_user
        request.request_id = "middleware-transition-request-1"
        request.session = {}
        request._messages = FallbackStorage(request)

        self.model_admin.transition_to_uploaded(
            request,
            ResearchWork.objects.filter(pk=self.own_work.pk),
        )

        self.own_work.refresh_from_db()
        self.assertEqual(self.own_work.status, ResearchWorkStatus.UPLOADED)
        transition = self.own_work.transitions.get()
        self.assertEqual(transition.actor, self.admin_user)
        self.assertEqual(transition.request_id, "middleware-transition-request-1")
        self.assertIn("Django admin action", transition.reason)

    def test_unlinked_teacher_has_no_research_work_admin_access(self):
        request = self.request_for(self.unlinked_teacher)
        self.assertFalse(self.model_admin.get_queryset(request).exists())
        self.assertFalse(self.model_admin.has_module_permission(request))
        self.assertFalse(self.model_admin.has_view_permission(request, self.own_work))

    def test_teacher_cannot_change_or_remove_advisor_ownership(self):
        request = self.request_for(self.teacher)
        own_link = WorkAdvisor.objects.get(
            research_work=self.own_work,
            professor=self.professor,
        )
        other_link = WorkAdvisor.objects.get(
            research_work=self.other_work,
            professor=self.other_professor,
        )

        self.assertTrue(self.advisor_inline.has_view_permission(request, self.own_work))
        self.assertFalse(
            self.advisor_inline.has_view_permission(request, self.other_work)
        )
        self.assertFalse(
            self.advisor_inline.has_change_permission(request, self.own_work)
        )
        self.assertFalse(self.advisor_inline.has_add_permission(request, self.own_work))
        self.assertFalse(
            self.advisor_inline.has_delete_permission(request, self.own_work)
        )

        self.assertQuerySetEqual(
            self.advisor_admin.get_queryset(request),
            list(WorkAdvisor.objects.filter(professor=self.professor)),
            ordered=False,
        )
        self.assertTrue(self.advisor_admin.has_view_permission(request, own_link))
        self.assertFalse(self.advisor_admin.has_view_permission(request, other_link))
        self.assertFalse(self.advisor_admin.has_change_permission(request, own_link))
        self.assertFalse(self.advisor_admin.has_delete_permission(request, own_link))

        admin_request = self.request_for(self.admin_user)
        self.assertTrue(
            self.advisor_inline.has_change_permission(admin_request, self.own_work)
        )
        self.assertTrue(self.advisor_admin.has_change_permission(admin_request, own_link))

    def test_standalone_relationship_admins_fail_closed_for_teachers(self):
        student = Student.objects.create(display_name="Relationship Student")
        field = ResearchField.objects.create(
            slug="relationship-field",
            display_name="Relationship Field",
        )
        method = ResearchMethod.objects.create(
            slug="relationship-method",
            display_name="Relationship Method",
        )
        own_objects = (
            WorkAuthor.objects.create(research_work=self.own_work, student=student),
            WorkField.objects.create(
                research_work=self.own_work,
                research_field=field,
            ),
            WorkMethod.objects.create(
                research_work=self.own_work,
                research_method=method,
            ),
        )
        other_student = Student.objects.create(display_name="Other Relationship Student")
        other_field = ResearchField.objects.create(
            slug="other-relationship-field",
            display_name="Other Relationship Field",
        )
        other_method = ResearchMethod.objects.create(
            slug="other-relationship-method",
            display_name="Other Relationship Method",
        )
        other_objects = (
            WorkAuthor.objects.create(
                research_work=self.other_work,
                student=other_student,
            ),
            WorkField.objects.create(
                research_work=self.other_work,
                research_field=other_field,
            ),
            WorkMethod.objects.create(
                research_work=self.other_work,
                research_method=other_method,
            ),
        )

        teacher_request = self.request_for(self.teacher)
        admin_request = self.request_for(self.admin_user)
        self.assertFalse(self.author_admin.has_module_permission(teacher_request))
        self.assertFalse(self.author_admin.get_queryset(teacher_request).exists())
        self.assertFalse(
            self.author_admin.has_view_permission(teacher_request, own_objects[0])
        )
        self.assertTrue(
            self.author_admin.has_change_permission(admin_request, other_objects[0])
        )

        for model_admin, own_object, other_object in zip(
            (self.field_admin, self.method_admin),
            own_objects[1:],
            other_objects[1:],
        ):
            with self.subTest(model_admin=model_admin.__class__.__name__):
                self.assertFalse(model_admin.has_module_permission(teacher_request))
                self.assertNotIn(own_object, model_admin.get_queryset(teacher_request))
                self.assertNotIn(other_object, model_admin.get_queryset(teacher_request))
                self.assertFalse(
                    model_admin.has_view_permission(teacher_request, own_object)
                )
                self.assertFalse(
                    model_admin.has_view_permission(teacher_request, other_object)
                )
                self.assertFalse(
                    model_admin.has_change_permission(teacher_request, own_object)
                )
                self.assertFalse(model_admin.has_add_permission(teacher_request))
                self.assertFalse(
                    model_admin.has_delete_permission(teacher_request, own_object)
                )
                self.assertTrue(
                    model_admin.has_change_permission(admin_request, other_object)
                )

    def test_teacher_cannot_access_admin_scope_or_terminal_advised_work(self):
        admin_scope = self.make_work("Admin-only advised work")
        admin_scope.visibility_scope = "admin"
        admin_scope.save(update_fields={"visibility_scope"})
        archived = self.make_work("Archived advised work")
        archived.status = ResearchWorkStatus.ARCHIVED
        archived.save(update_fields={"status"}, _allow_status_transition=True)
        WorkAdvisor.objects.create(research_work=admin_scope, professor=self.professor)
        WorkAdvisor.objects.create(research_work=archived, professor=self.professor)
        request = self.request_for(self.teacher)

        queryset = self.model_admin.get_queryset(request)
        self.assertNotIn(admin_scope, queryset)
        self.assertNotIn(archived, queryset)
        self.assertFalse(self.model_admin.has_view_permission(request, admin_scope))
        self.assertFalse(self.model_admin.has_change_permission(request, archived))
