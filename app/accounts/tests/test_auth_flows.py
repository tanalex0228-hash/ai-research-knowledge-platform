from datetime import date, timedelta
from unittest.mock import patch
from django.conf import settings
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import (
    EnrollmentStatus,
    Program,
    Role,
    StudentRegistrationOTP,
    StudentRoster,
    User,
    UserProfile,
)
from accounts.services import (
    enforce_rate_limit,
    find_active_roster_match,
    send_student_registration_otp,
    student_access_level,
    verify_captcha,
    verify_student_registration_otp,
)


class AuthFlowsTests(TestCase):
    def setUp(self):
        # Setup system roles
        self.student_role = Role.objects.get(slug="student")
        self.teacher_role = Role.objects.get(slug="teacher")
        self.admin_role = Role.objects.get(slug="admin")

        # Setup student roster entry
        self.roster_entry = StudentRoster.objects.create(
            student_id="410410123",
            fju_cloud_email="410410123@cloud.fju.edu.tw",
            department_code="FIN",
            program=Program.UNDERGRADUATE,
            academic_year=3,
            enrollment_status=EnrollmentStatus.ACTIVE,
            project_eligibility=True,
            effective_start=date.today() - timedelta(days=10),
            source="test_source",
            source_version="v1.0",
        )

    def test_find_active_roster_match_success(self):
        match = find_active_roster_match(
            student_id="410410123",
            fju_cloud_email="410410123@cloud.fju.edu.tw",
        )
        self.assertEqual(match, self.roster_entry)

    def test_find_active_roster_match_mismatch_id(self):
        with self.assertRaises(ValidationError):
            find_active_roster_match(
                student_id="wrong_id",
                fju_cloud_email="410410123@cloud.fju.edu.tw",
            )

    def test_find_active_roster_match_mismatch_email(self):
        with self.assertRaises(ValidationError):
            find_active_roster_match(
                student_id="410410123",
                fju_cloud_email="wrong_email@cloud.fju.edu.tw",
            )

    def test_find_active_roster_match_disallowed_domain(self):
        with self.assertRaises(ValidationError):
            find_active_roster_match(
                student_id="410410123",
                fju_cloud_email="410410123@gmail.com",
            )

    def test_send_student_registration_otp_success(self):
        registration = send_student_registration_otp(
            student_id="410410123",
            fju_cloud_email="410410123@cloud.fju.edu.tw",
            backup_email="backup@example.com",
            phone_number="0912345678",
        )
        self.assertIsNotNone(registration)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("verification code", mail.outbox[0].body)

    def test_send_student_registration_otp_cooldown(self):
        send_student_registration_otp(
            student_id="410410123",
            fju_cloud_email="410410123@cloud.fju.edu.tw",
            backup_email="backup@example.com",
            phone_number="0912345678",
        )
        with self.assertRaises(PermissionDenied):
            send_student_registration_otp(
                student_id="410410123",
                fju_cloud_email="410410123@cloud.fju.edu.tw",
                backup_email="backup@example.com",
                phone_number="0912345678",
            )

    def test_verify_student_registration_otp_success(self):
        registration = send_student_registration_otp(
            student_id="410410123",
            fju_cloud_email="410410123@cloud.fju.edu.tw",
            backup_email="backup@example.com",
            phone_number="0912345678",
        )
        # Extract OTP from email
        body = mail.outbox[0].body
        otp = body.split("is ")[1].split(".")[0]

        user = verify_student_registration_otp(
            registration_id=registration.id,
            otp=otp,
            password="securepassword123",
        )
        self.assertIsNotNone(user)
        self.assertEqual(user.username, "410410123")
        self.assertTrue(user.has_role("student"))

        profile = user.profile
        self.assertEqual(profile.roster_entry, self.roster_entry)
        self.assertEqual(profile.backup_email, "backup@example.com")
        self.assertEqual(profile.phone_number, "0912345678")

    def test_verify_student_registration_otp_expired(self):
        registration = send_student_registration_otp(
            student_id="410410123",
            fju_cloud_email="410410123@cloud.fju.edu.tw",
            backup_email="backup@example.com",
            phone_number="0912345678",
        )
        registration.expires_at = timezone.now() - timedelta(seconds=1)
        registration.save()

        with self.assertRaises(ValidationError):
            verify_student_registration_otp(
                registration_id=registration.id,
                otp="123456",
                password="securepassword123",
            )

    def test_verify_student_registration_otp_max_attempts(self):
        registration = send_student_registration_otp(
            student_id="410410123",
            fju_cloud_email="410410123@cloud.fju.edu.tw",
            backup_email="backup@example.com",
            phone_number="0912345678",
        )
        for _ in range(registration.max_attempts):
            with self.assertRaises(ValidationError):
                verify_student_registration_otp(
                    registration_id=registration.id,
                    otp="wrong",
                    password="securepassword123",
                )
        with self.assertRaises(PermissionDenied):
            verify_student_registration_otp(
                registration_id=registration.id,
                otp="wrong",
                password="securepassword123",
            )

    def test_student_access_level_general_vs_project(self):
        # Project eligible
        user_project = User.objects.create_user(username="student1", email="student1@cloud.fju.edu.tw")
        UserProfile.objects.create(user=user_project, roster_entry=self.roster_entry)
        self.assertEqual(student_access_level(user_project), "project_eligible_student")

        # General student (project_eligibility=False)
        roster_general = StudentRoster.objects.create(
            student_id="410410999",
            fju_cloud_email="410410999@cloud.fju.edu.tw",
            department_code="FIN",
            program=Program.UNDERGRADUATE,
            academic_year=3,
            enrollment_status=EnrollmentStatus.ACTIVE,
            project_eligibility=False,
            effective_start=date.today() - timedelta(days=10),
            source="test_source",
            source_version="v1.0",
        )
        user_general = User.objects.create_user(username="student2", email="student2@cloud.fju.edu.tw")
        UserProfile.objects.create(user=user_general, roster_entry=roster_general)
        self.assertEqual(student_access_level(user_general), "general_student")

    @override_settings(DEBUG=True)
    def test_captcha_verification(self):
        # Debug CAPTCHA verify success
        verify_captcha("test-pass", action="login")

        # Debug CAPTCHA verify fail
        with self.assertRaises(PermissionDenied):
            verify_captcha("wrong-token", action="login")

    def test_login_rate_limit(self):
        # Fake request
        class FakeRequest:
            META = {"REMOTE_ADDR": "127.0.0.1"}
            request_id = "test-req"

        req = FakeRequest()
        for _ in range(settings.AUTH_RATE_LIMIT_ATTEMPTS):
            enforce_rate_limit(req, action="login", identifier="test-user")
        
        with self.assertRaises(PermissionDenied):
            enforce_rate_limit(req, action="login", identifier="test-user")
