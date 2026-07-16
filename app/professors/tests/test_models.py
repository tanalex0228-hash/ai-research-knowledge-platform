from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Role, User, UserRole
from accounts.permissions import VisibilityScope
from professors.models import Professor, ProfessorAlias
from professors.services import find_professors_by_name


class ProfessorModelTests(TestCase):
    def test_name_and_aliases_are_unicode_normalized(self):
        professor = Professor.objects.create(display_name="  Ａlex   Wang ")
        ProfessorAlias.objects.create(professor=professor, alias="  王   教授 ")

        self.assertEqual(professor.normalized_name, "alex wang")
        self.assertEqual(
            list(find_professors_by_name("王 教授", user=AnonymousUser())),
            [professor],
        )

    def test_duplicate_normalized_alias_for_same_professor_is_rejected(self):
        professor = Professor.objects.create(display_name="Professor Wang")
        ProfessorAlias.objects.create(professor=professor, alias="Prof. Wang")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProfessorAlias.objects.create(professor=professor, alias="  PROF. WANG  ")

    def test_visibility_and_email_privacy(self):
        public = Professor.objects.create(
            display_name="Public",
            email="private@example.test",
            email_is_public=False,
        )
        teacher_only = Professor.objects.create(
            display_name="Private",
            visibility_scope=VisibilityScope.TEACHER,
        )
        teacher_user = User.objects.create_user(
            username="teacher",
            email="teacher@example.test",
        )
        teacher_role = Role.objects.get(slug="teacher")
        UserRole.objects.create(user=teacher_user, role=teacher_role)

        self.assertQuerySetEqual(
            Professor.objects.discoverable_to(AnonymousUser()),
            [public],
            ordered=False,
        )
        self.assertIn(teacher_only, Professor.objects.discoverable_to(teacher_user))
        self.assertEqual(public.contact_email_for(AnonymousUser()), "")
        self.assertEqual(public.contact_email_for(teacher_user), "private@example.test")
