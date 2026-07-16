import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import User


class DemoSeedSafetyTests(TestCase):
    @override_settings(DEBUG=False, ENVIRONMENT="local")
    def test_seed_refuses_debug_false_without_explicit_override(self):
        with self.assertRaisesMessage(CommandError, "Refusing to seed"):
            call_command("seed_demo_data")

    @override_settings(DEBUG=True, ENVIRONMENT="test")
    def test_seed_refuses_username_collision_with_non_demo_account(self):
        User.objects.create_user(
            username="demo_teacher_1",
            email="faculty@example.edu",
        )
        with tempfile.TemporaryDirectory() as private_root, override_settings(
            PRIVATE_DOCUMENT_ROOT=private_root
        ):
            with self.assertRaisesMessage(CommandError, "Refusing to reuse username"):
                call_command("seed_demo_data")
