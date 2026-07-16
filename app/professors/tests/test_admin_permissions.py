from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from accounts.models import Role, User, UserRole
from professors.admin import ProfessorAdmin
from professors.models import Professor


class ProfessorAdminObjectPermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = ProfessorAdmin(Professor, AdminSite())
        teacher_role = Role.objects.get(slug="teacher")
        admin_role = Role.objects.get(slug="admin")

        self.teacher = User.objects.create_user(
            username="teacher-one",
            email="teacher-one@example.test",
            is_staff=True,
        )
        UserRole.objects.create(user=self.teacher, role=teacher_role)
        self.own_profile = Professor.objects.create(
            user=self.teacher,
            display_name="Teacher One",
        )

        self.other_teacher = User.objects.create_user(
            username="teacher-two",
            email="teacher-two@example.test",
            is_staff=True,
        )
        UserRole.objects.create(user=self.other_teacher, role=teacher_role)
        self.other_profile = Professor.objects.create(
            user=self.other_teacher,
            display_name="Teacher Two",
        )

        self.admin_user = User.objects.create_user(
            username="platform-admin",
            email="platform-admin@example.test",
            is_staff=True,
        )
        UserRole.objects.create(user=self.admin_user, role=admin_role)
        self.regular_staff = User.objects.create_user(
            username="regular-staff",
            email="regular-staff@example.test",
            is_staff=True,
        )

    def request_for(self, user):
        request = self.factory.get("/admin/professors/professor/")
        request.user = user
        return request

    def test_teacher_only_lists_and_changes_linked_profile(self):
        request = self.request_for(self.teacher)
        self.assertQuerySetEqual(
            self.model_admin.get_queryset(request),
            [self.own_profile],
            ordered=False,
        )
        self.assertTrue(self.model_admin.has_view_permission(request, self.own_profile))
        self.assertTrue(self.model_admin.has_change_permission(request, self.own_profile))
        self.assertFalse(self.model_admin.has_view_permission(request, self.other_profile))
        self.assertFalse(self.model_admin.has_change_permission(request, self.other_profile))
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_delete_permission(request, self.own_profile))

        readonly = self.model_admin.get_readonly_fields(request, self.own_profile)
        self.assertIn("user", readonly)
        self.assertIn("status", readonly)
        self.assertIn("visibility_scope", readonly)

    def test_admin_role_retains_full_professor_access(self):
        request = self.request_for(self.admin_user)
        self.assertEqual(self.model_admin.get_queryset(request).count(), 2)
        self.assertTrue(self.model_admin.has_change_permission(request, self.other_profile))
        self.assertTrue(self.model_admin.has_add_permission(request))
        self.assertTrue(self.model_admin.has_delete_permission(request, self.other_profile))

    def test_staff_without_platform_role_has_no_professor_access(self):
        request = self.request_for(self.regular_staff)
        self.assertFalse(self.model_admin.get_queryset(request).exists())
        self.assertFalse(self.model_admin.has_module_permission(request))
        self.assertFalse(self.model_admin.has_view_permission(request, self.own_profile))

    def test_inactive_linked_professor_revokes_teacher_admin_access(self):
        self.own_profile.status = "inactive"
        self.own_profile.save(update_fields={"status"})
        request = self.request_for(self.teacher)

        self.assertFalse(self.model_admin.get_queryset(request).exists())
        self.assertFalse(self.model_admin.has_module_permission(request))
        self.assertFalse(
            self.model_admin.has_change_permission(request, self.own_profile)
        )
