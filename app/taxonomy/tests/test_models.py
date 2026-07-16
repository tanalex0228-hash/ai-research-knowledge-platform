from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.permissions import VisibilityScope
from taxonomy.models import ResearchField, ResearchMethod
from taxonomy.services import resolve_taxonomy_terms


class TaxonomyModelTests(TestCase):
    def test_slug_and_aliases_are_normalized(self):
        method = ResearchMethod.objects.create(
            slug="Time Series",
            display_name="Time Series",
            aliases=["  VAR  ", "var", "ＶＡＲ"],
        )
        self.assertEqual(method.slug, "time-series")
        self.assertEqual(method.aliases, ["VAR"])
        self.assertEqual(
            resolve_taxonomy_terms(ResearchMethod, "var", user=AnonymousUser()),
            [method],
        )

    def test_item_cannot_parent_itself(self):
        field = ResearchField.objects.create(slug="finance", display_name="Finance")
        field.parent = field
        with self.assertRaises(ValidationError):
            field.full_clean()

    def test_archived_and_non_public_terms_are_not_discoverable(self):
        public = ResearchField.objects.create(slug="public", display_name="Public")
        ResearchField.objects.create(
            slug="private",
            display_name="Private",
            visibility_scope=VisibilityScope.ADMIN,
        )
        archived = ResearchField.objects.create(slug="old", display_name="Old")
        archived.status = "archived"
        archived.save(update_fields={"status"})

        self.assertQuerySetEqual(
            ResearchField.objects.discoverable_to(AnonymousUser()),
            [public],
            ordered=False,
        )

