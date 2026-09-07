from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Role, User, UserRole
from accounts.permissions import VisibilityScope
from professors.models import Professor
from research.models import (
    FeaturedWork,
    FieldRelevance,
    ResearchWork,
    ResearchWorkStatus,
    ResearchWorkType,
    Student,
    WorkAdvisor,
    WorkAuthor,
    WorkField,
)
from research.services import professor_field_distribution, professor_works, work_author_names
from taxonomy.models import ResearchField


def make_work(title, *, status=ResearchWorkStatus.PUBLISHED, visibility=VisibilityScope.PUBLIC):
    return ResearchWork.objects.create(
        title=title,
        year=2026,
        work_type=ResearchWorkType.UNDERGRADUATE_PROJECT,
        status=status,
        visibility_scope=visibility,
    )


class ResearchVisibilityTests(TestCase):
    def setUp(self):
        self.student_user = User.objects.create_user(
            username="student",
            email="student@example.test",
        )
        student_role = Role.objects.get(slug="student")
        UserRole.objects.create(user=self.student_user, role=student_role)

    def test_discoverable_requires_both_published_state_and_role_visibility(self):
        public = make_work("Public")
        student = make_work("Student", visibility=VisibilityScope.STUDENT)
        make_work("Draft", status=ResearchWorkStatus.DRAFT)

        self.assertQuerySetEqual(
            ResearchWork.objects.discoverable_to(AnonymousUser()),
            [public],
            ordered=False,
        )
        self.assertQuerySetEqual(
            ResearchWork.objects.discoverable_to(self.student_user),
            [public, student],
            ordered=False,
        )

    def test_normalized_duplicate_work_is_rejected(self):
        make_work("  AI  Finance ")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_work("ＡＩ finance")

    def test_student_name_is_private_by_default(self):
        student = Student.objects.create(display_name="Lin Student")
        self.assertEqual(student.display_name_for(AnonymousUser()), "Student author")
        self.assertEqual(student.display_name_for(self.student_user), "Student author")
        admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.test",
            password="test-password",
        )
        self.assertEqual(student.display_name_for(admin), "Lin Student")

        work = make_work("Private authorship")
        WorkAuthor.objects.create(research_work=work, student=student)
        self.assertEqual(work_author_names(work, user=AnonymousUser()), ["Student author"])
        self.assertEqual(work_author_names(work, user=admin), ["Lin Student"])

    def test_nonpublic_name_stays_anonymous_even_when_row_scope_is_public(self):
        student = Student.objects.create(
            display_name="Private Public-Scope Student",
            visibility_scope=VisibilityScope.PUBLIC,
            is_name_public=False,
        )

        self.assertEqual(student.display_name_for(AnonymousUser()), "Student author")


class ResearchRelationshipTests(TestCase):
    def test_student_user_author_link_defaults_to_student_visible_author_identity(self):
        user = User.objects.create_user(
            username="Roster Student",
            email="roster-student@example.test",
        )
        UserRole.objects.create(user=user, role=Role.objects.get(slug="student"))
        work = make_work("Linked authorship")

        author = WorkAuthor.objects.create(research_work=work, user=user)

        self.assertEqual(author.user, user)
        self.assertEqual(author.student.user, user)
        self.assertEqual(author.student.display_name, "Roster Student")
        self.assertEqual(author.student.visibility_scope, VisibilityScope.STUDENT)

    def test_non_student_user_cannot_become_research_author(self):
        user = User.objects.create_user(
            username="not-a-student",
            email="not-a-student@example.test",
        )
        work = make_work("Rejected author")

        with self.assertRaises(ValidationError):
            WorkAuthor.objects.create(research_work=work, user=user)

    def test_duplicate_author_position_is_rejected(self):
        work = make_work("Authorship")
        first = Student.objects.create(display_name="First")
        second = Student.objects.create(display_name="Second")
        WorkAuthor.objects.create(research_work=work, student=first, position=1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkAuthor.objects.create(research_work=work, student=second, position=1)

    def test_professor_drilldown_and_distribution_exclude_unpublished_works(self):
        professor = Professor.objects.create(display_name="Professor Wang")
        field = ResearchField.objects.create(slug="ai-finance", display_name="AI Finance")
        published = make_work("Published research")
        draft = make_work("Draft research", status=ResearchWorkStatus.DRAFT)
        WorkAdvisor.objects.create(research_work=published, professor=professor)
        WorkAdvisor.objects.create(research_work=draft, professor=professor)
        WorkField.objects.create(
            research_work=published,
            research_field=field,
            is_primary=True,
            relevance=FieldRelevance.CORE,
        )
        WorkField.objects.create(
            research_work=draft,
            research_field=field,
            is_primary=True,
            relevance=FieldRelevance.CORE,
        )

        self.assertQuerySetEqual(
            professor_works(professor, user=AnonymousUser(), field=field),
            [published],
            ordered=False,
        )
        distribution = list(professor_field_distribution(professor, user=AnonymousUser()))
        self.assertEqual(distribution[0]["work_count"], 1)
        hydrated = ResearchWork.objects.filter(pk=published.pk).with_catalog_relations().get()
        self.assertEqual(hydrated.field_links.get().field, field)

    def test_featured_work_must_reference_published_visible_work_to_be_discoverable(self):
        public = make_work("Featured")
        draft = make_work("Hidden feature", status=ResearchWorkStatus.DRAFT)
        public_feature = FeaturedWork.objects.create(research_work=public)
        FeaturedWork.objects.create(research_work=draft)

        self.assertQuerySetEqual(
            FeaturedWork.objects.discoverable_to(AnonymousUser()),
            [public_feature],
            ordered=False,
        )
