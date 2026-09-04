from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import (
    AuditLog,
    EnrollmentStatus,
    Program,
    Role,
    StudentRoster,
    User,
    UserProfile,
    UserRole,
)
from accounts.permissions import VisibilityScope, visible_scopes_for


class RoleModelTests(TestCase):
    def test_roster_linked_profile_gets_student_role_automatically(self):
        roster = StudentRoster.objects.create(
            student_id="414411001",
            fju_cloud_email="414411001@cloud.fju.edu.tw",
            department_code="FIN",
            program=Program.UNDERGRADUATE,
            academic_year=2,
            enrollment_status=EnrollmentStatus.ACTIVE,
            effective_start="2026-09-01",
            source="test",
            source_version="profile-role-test",
        )
        user = User.objects.create_user(
            username="roster-user",
            email="414411001@cloud.fju.edu.tw",
        )

        UserProfile.objects.create(user=user, roster_entry=roster, display_name="Roster User")

        self.assertTrue(user.has_platform_role("student"))

    def test_role_slug_is_normalized(self):
        role = Role.objects.create(slug="Teacher Role", display_name="Teacher")
        self.assertEqual(role.slug, "teacher-role")

    def test_duplicate_assignment_is_rejected(self):
        user = User.objects.create_user(username="student", email="student@example.test")
        role = Role.objects.get(slug="student")
        UserRole.objects.create(user=user, role=role)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserRole.objects.create(user=user, role=role)

    def test_email_is_normalized_and_case_insensitively_unique(self):
        user = User.objects.create_user(username="first", email="User@Example.TEST")
        self.assertEqual(user.email, "user@example.test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="second", email="USER@example.test")

    def test_visibility_tiers_follow_normalized_roles(self):
        user = User.objects.create_user(username="teacher", email="teacher@example.test")
        teacher = Role.objects.get(slug="teacher")
        UserRole.objects.create(user=user, role=teacher)

        self.assertEqual(
            visible_scopes_for(user),
            (
                VisibilityScope.PUBLIC,
                VisibilityScope.STUDENT,
                VisibilityScope.TEACHER,
            ),
        )

    def test_staff_flag_alone_does_not_widen_data_access(self):
        staff = User.objects.create_user(
            username="staff",
            email="staff@example.test",
            is_staff=True,
        )
        self.assertEqual(visible_scopes_for(staff), (VisibilityScope.PUBLIC,))

    def test_inactive_user_cannot_retain_restricted_role_access(self):
        user = User.objects.create_user(
            username="inactive",
            email="inactive@example.test",
            is_active=False,
        )
        role = Role.objects.get(slug="admin")
        UserRole.objects.create(user=user, role=role)
        self.assertEqual(visible_scopes_for(user), (VisibilityScope.PUBLIC,))
        self.assertFalse(user.has_role("admin"))

    def test_audit_logs_cannot_be_mutated_or_deleted(self):
        event = AuditLog.objects.create(event_type="test.event")

        with self.assertRaises(ValueError):
            AuditLog.objects.filter(pk=event.pk).update(event_type="rewritten")
        with self.assertRaises(ValueError):
            AuditLog.objects.bulk_update([event], ["event_type"])
        with self.assertRaises(ValueError):
            AuditLog.objects.filter(pk=event.pk).delete()

        self.assertEqual(AuditLog.objects.get(pk=event.pk).event_type, "test.event")
